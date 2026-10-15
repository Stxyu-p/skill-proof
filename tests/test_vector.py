import hashlib, math, os, pathlib, sqlite3, struct, tempfile, unittest
from unittest import mock

from core import (
    Catalog,
    RankedCandidate,
    Selection,
    SkillRecord,
    VectorIndex,
    scan_catalog,
    select_skill,
)

class VectorIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.tmp.name) / "test_vec.db"

    def tearDown(self):
        try:
            self.tmp.cleanup()
        except Exception:
            pass

    def test_sqlite_vector_persistence_and_in_memory_cache(self):
        idx = VectorIndex(self.db_path, api_key="dummy")
        # Manually insert vectors into db
        dim = 4
        vec1 = (0.1, 0.2, 0.3, 0.4)
        blob = struct.pack(f"{dim}f", *vec1)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO skill_embeddings VALUES (?, ?, ?, ?, ?, ?)",
                ("skill-a", "sha1", idx.model, dim, blob, 123.0)
            )
            conn.commit()

        # Reload
        idx2 = VectorIndex(self.db_path, api_key="dummy")
        self.assertIn("skill-a", idx2._memory_vectors)
        self.assertEqual(len(idx2._memory_vectors["skill-a"]), 4)
        for expected, actual in zip(vec1, idx2._memory_vectors["skill-a"]):
            self.assertAlmostEqual(expected, actual, places=4)

    def test_rank_returns_cosine_similarity(self):
        idx = VectorIndex(self.db_path, api_key="dummy")
        idx._memory_vectors = {
            "skill-a": (1.0, 0.0),
            "skill-b": (0.0, 1.0),
        }
        with mock.patch.object(idx, "get_query_vector", return_value=(1.0, 0.1)):
            ranked = idx.rank("query", top_k=2)
            self.assertEqual(ranked[0][0], "skill-a")
            self.assertGreater(ranked[0][1], 0.9)
            self.assertEqual(ranked[1][0], "skill-b")

    def test_graceful_fallback_when_offline(self):
        idx = VectorIndex(self.db_path, api_key="")
        # When api_key is empty or gateway fails, ranking returns []
        self.assertEqual(idx.rank("hello"), [])

    def test_rrf_hybrid_reranks_and_selects(self):
        rec_a = SkillRecord("a", "skill-a", "skill-a", "database ops", (), "r1", "p1", "sha1", 100, pathlib.Path("."), pathlib.Path("."))
        rec_b = SkillRecord("b", "skill-b", "skill-b", "user design", (), "r1", "p2", "sha2", 100, pathlib.Path("."), pathlib.Path("."))
        catalog = Catalog((rec_a, rec_b), (), "hash1")

        idx = VectorIndex(self.db_path, api_key="dummy")
        idx._memory_vectors = {
            "skill-a": (0.0, 1.0),
            "skill-b": (1.0, 0.0),
        }
        with mock.patch.object(idx, "get_query_vector", return_value=(0.95, 0.05)):
            sel = select_skill(catalog, "help me make a landing page", vector_index=idx)
            self.assertEqual(sel.candidates[0].skill.name, "skill-b")
            self.assertIn("rrf_hybrid", sel.candidates[0].reasons)

    def test_negation_vetoes_vector_candidate(self):
        rec_a = SkillRecord("a", "skill-a", "skill-a", "database ops", (), "r1", "p1", "sha1", 100, pathlib.Path("."), pathlib.Path("."))
        catalog = Catalog((rec_a,), (), "hash1")

        idx = VectorIndex(self.db_path, api_key="dummy")
        idx._memory_vectors = {"skill-a": (1.0, 0.0)}
        with mock.patch.object(idx, "get_query_vector", return_value=(1.0, 0.0)):
            sel = select_skill(catalog, "do not use skill-a", vector_index=idx)
            self.assertEqual(sel.status, "no_match")
            self.assertIsNone(sel.selected)


class DeterministicMockEmbedder:
    """EmbedderProtocol implementation: stable hashed vectors, no model, no network."""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    def embed(self, texts, is_query: bool = False):
        out = []
        for text in texts:
            seed = f"{'q' if is_query else 'd'}:{text}"
            out.append(
                tuple(
                    (int(hashlib.sha256(f"{seed}:{i}".encode("utf-8")).hexdigest()[:8], 16) % 1000) / 1000.0
                    for i in range(self.dim)
                )
            )
        return out


class EmbedderSeamTests(unittest.TestCase):
    """v0.16 plan item 5.1: the EmbedderProtocol seam on VectorIndex."""

    def test_injected_embedder_replaces_provider_and_is_traced(self):
        with tempfile.TemporaryDirectory() as tmp:
            idx = VectorIndex(
                pathlib.Path(tmp) / "v.db",
                provider="gateway",
                api_key="unused",
                embedder=DeterministicMockEmbedder(),
            )
            vectors = idx._call_embeddings(["alpha skill"], is_query=False)
            self.assertEqual(len(vectors), 1)
            self.assertEqual(len(vectors[0]), 8)
            trace = idx.trace_snapshot()
            self.assertEqual(trace["provider_used"], "embedder")
            self.assertIsNone(trace["fallback_reason"])

    def test_injected_embedder_failure_is_fail_open(self):
        class BoomEmbedder:
            def embed(self, texts, is_query=False):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            idx = VectorIndex(
                pathlib.Path(tmp) / "v.db",
                provider="gateway",
                api_key="unused",
                embedder=BoomEmbedder(),
            )
            self.assertEqual(idx._call_embeddings(["x"]), [])
            self.assertEqual(idx.trace_snapshot()["fallback_reason"], "embedder_failed")


if __name__ == "__main__":
    unittest.main()
