from __future__ import annotations

"""Vector layer: SQLite vector index and embedding-provider client."""

import hashlib
import json
import math
import os
import pathlib
import sqlite3
import struct
import threading
import time
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterable, Mapping, Optional, Sequence
try:
    from . import models
except ImportError:
    try:
        import models
    except ImportError:
        models = None


from _catalog import (
    SkillRecord,
    default_hermes_home,
)


class VectorIndex:
    """SQLite-backed vector cache and ranker for Skill Proof.

    Supports neutral providers: 'off' (pure lexical), 'local' (ONNX), 'gateway' (API).
    # ponytail: stdlib sqlite3 and urllib with local in-memory dict cache.
    """

    def __init__(
        self,
        db_path: Optional[os.PathLike[str] | str] = None,
        provider: str = "off",
        model: str = "Xenova/multilingual-e5-small",
        gateway_url: str = "",
        api_key: Optional[str] = None,
        gateway_key_env: str = "HERMES_CUSTOM_LOCALHOST_20128_API_KEY",
        models_dir: Optional[os.PathLike[str] | str] = None,
        timeout: float = 3.0,
        embedder: Any = None,
    ) -> None:
        self.db_path = pathlib.Path(db_path).resolve() if db_path else None
        self.model = str(model)
        self.gateway_url = str(gateway_url) if gateway_url else "http://localhost:20128/v1/embeddings"
        self.gateway_key_env = str(gateway_key_env)
        self.models_dir = pathlib.Path(models_dir).resolve() if models_dir else None
        self.timeout = float(timeout)
        self._embedder = embedder

        # Provider determination with backwards compatibility
        p = str(provider or "").strip().lower()
        if not p or p == "off":
            if api_key:
                p = "gateway"
            else:
                p = "off"
        self.provider = p

        self.api_key = api_key if api_key is not None else os.environ.get(self.gateway_key_env, "")
        self._memory_vectors: dict[str, tuple[float, ...]] = {}
        self._local_embedder = None
        self._trace_local = threading.local()
        self.reset_trace()

        if self.provider == "local" and models is not None:
            self._local_embedder = models.LocalEmbedder(model_key=self.model, root_dir=self.models_dir)

        if self.db_path:
            self._init_db()
            self._load_memory()

    def provider_status(self) -> dict[str, Any]:
        active = False
        if self.provider == "local":
            active = self._local_embedder is not None and self._local_embedder.is_available()
        elif self.provider == "gateway":
            active = bool(self.api_key and self.gateway_url)
        return {
            "provider": self.provider,
            "active": active,
            "model": self.model,
            "cached_skills": len(self._memory_vectors),
        }

    def reset_trace(self) -> None:
        self._trace_local.value = {
            "provider_configured": self.provider,
            "provider_used": "off",
            "fallback_reason": "not_used",
            "embedding_latency_ms": 0.0,
            "query_cache_hit": False,
            "vector_used": False,
        }

    def _trace(self, **updates: Any) -> None:
        current = getattr(self._trace_local, "value", None)
        if current is None:
            self.reset_trace()
            current = self._trace_local.value
        current.update(updates)

    def trace_snapshot(self) -> dict[str, Any]:
        current = getattr(self._trace_local, "value", None)
        return dict(current) if current is not None else {
            "provider_configured": self.provider,
            "provider_used": "off",
            "fallback_reason": "not_used",
            "embedding_latency_ms": 0.0,
            "query_cache_hit": False,
            "vector_used": False,
        }

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        if not self.db_path:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_embeddings (
                    skill_name TEXT PRIMARY KEY,
                    source_sha256 TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS query_embeddings (
                    query_sha256 TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _load_memory(self) -> None:
        if not self.db_path or not self.db_path.exists():
            return
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT skill_name, dim, vector FROM skill_embeddings WHERE model = ?",
                (self.model,),
            ).fetchall()
            for name, dim, blob in rows:
                self._memory_vectors[name] = struct.unpack(f"{dim}f", blob)

    def _call_embeddings(self, texts: list[str], is_query: bool = False) -> list[tuple[float, ...]]:
        if not texts:
            return []
        if self._embedder is not None:
            # v0.16 seam: an injected embedder (EmbedderProtocol) replaces the
            # provider entirely; used by tests and embedders of the index.
            started = time.perf_counter()
            try:
                vectors = [tuple(float(x) for x in vec) for vec in self._embedder.embed(texts, is_query=is_query)]
            except Exception:
                vectors = []
            self._trace(
                provider_used="embedder" if vectors else "off",
                fallback_reason=None if vectors else "embedder_failed",
                embedding_latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            return vectors
        if self.provider == "off":
            self._trace(provider_used="off", fallback_reason="provider_off")
            return []

        if self.provider == "local":
            if not self._local_embedder or not self._local_embedder.is_available():
                self._trace(provider_used="off", fallback_reason="local_model_unavailable")
                return []
            started = time.perf_counter()
            try:
                vectors = self._local_embedder.embed(texts, is_query=is_query)
            except Exception:
                vectors = []
            self._trace(
                provider_used="local" if vectors else "off",
                fallback_reason=None if vectors else "local_inference_failed",
                embedding_latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            return vectors

        if self.provider == "gateway":
            if not self.api_key:
                self._trace(provider_used="off", fallback_reason="gateway_key_missing")
                return []
            out: list[tuple[float, ...]] = []
            started = time.perf_counter()
            for i in range(0, len(texts), 32):
                batch = texts[i : i + 32]
                req = urllib.request.Request(
                    self.gateway_url,
                    data=json.dumps({"model": self.model, "input": batch}).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        data = json.load(resp)
                    items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
                    out.extend([tuple(float(v) for v in item["embedding"]) for item in items])
                except Exception:
                    self._trace(
                        provider_used="off",
                        fallback_reason="gateway_request_failed",
                        embedding_latency_ms=round((time.perf_counter() - started) * 1000, 3),
                    )
                    return []
            self._trace(
                provider_used="gateway" if len(out) == len(texts) else "off",
                fallback_reason=None if len(out) == len(texts) else "gateway_incomplete_response",
                embedding_latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            return out

        self._trace(provider_used="off", fallback_reason="unknown_provider")
        return []

    def sync_catalog(self, catalog_skills: Sequence[SkillRecord]) -> int:
        if not self.db_path or self.provider == "off":
            return 0
        with self._connect() as conn:
            known = dict(
                conn.execute(
                    "SELECT skill_name, source_sha256 FROM skill_embeddings WHERE model = ?",
                    (self.model,),
                ).fetchall()
            )
        missing = [skill for skill in catalog_skills if known.get(skill.name) != skill.source_sha256]

        if not missing:
            return 0

        texts = [f"{s.name}: {s.description}" for s in missing]
        vectors = self._call_embeddings(texts, is_query=False)
        if not vectors or len(vectors) != len(missing):
            return 0

        now = time.time()
        with self._connect() as conn:
            for s, vec in zip(missing, vectors):
                dim = len(vec)
                blob = struct.pack(f"{dim}f", *vec)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO skill_embeddings (skill_name, source_sha256, model, dim, vector, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (s.name, s.source_sha256, self.model, dim, blob, now),
                )
                self._memory_vectors[s.name] = vec
            conn.commit()
        return len(missing)

    def get_query_vector(self, query: str) -> Optional[tuple[float, ...]]:
        if self.provider == "off":
            self._trace(provider_used="off", fallback_reason="provider_off")
            return None
        q_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()
        if self.db_path and self.db_path.exists():
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT dim, vector FROM query_embeddings WHERE query_sha256 = ? AND model = ?",
                    (q_sha, self.model),
                ).fetchone()
                if row:
                    self._trace(provider_used="cache", fallback_reason=None, query_cache_hit=True)
                    return struct.unpack(f"{row[0]}f", row[1])

        vectors = self._call_embeddings([query], is_query=True)
        if not vectors:
            return None
        vec = vectors[0]
        dim = len(vec)
        if self.db_path:
            blob = struct.pack(f"{dim}f", *vec)
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO query_embeddings (query_sha256, model, dim, vector, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (q_sha, self.model, dim, blob, time.time()),
                    )
                    conn.commit()
            except Exception:
                pass
        return vec

    def rank(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        if self.provider == "off":
            self._trace(provider_used="off", fallback_reason="provider_off")
            return []
        if not self._memory_vectors:
            self._trace(provider_used="off", fallback_reason="skill_vectors_missing")
            return []
        q_vec = self.get_query_vector(query)
        if not q_vec:
            return []

        def cos(a: tuple[float, ...], b: tuple[float, ...]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            norm1 = math.sqrt(sum(x * x for x in a))
            norm2 = math.sqrt(sum(y * y for y in b))
            return dot / (norm1 * norm2) if (norm1 and norm2) else 0.0

        scores = [(name, cos(q_vec, s_vec)) for name, s_vec in self._memory_vectors.items()]
        scores.sort(key=lambda x: x[1], reverse=True)
        self._trace(vector_used=bool(scores), fallback_reason=None if scores else "skill_vectors_missing")
        return scores[:top_k]

    def record_lexical_fastpath(self) -> None:
        """Trace a turn where a decisive lexical winner skipped the vector path."""
        self._trace(
            provider_used="lexical_fastpath",
            fallback_reason=None,
            embedding_latency_ms=0.0,
            query_cache_hit=False,
            vector_used=False,
        )


def vector_index_from_env(
    db_path: Optional[os.PathLike[str] | str] = None,
) -> Optional[VectorIndex]:
    """Build a configured vector index for standalone CLI/MCP processes.

    Embeddings remain disabled unless ``SKILL_PROOF_EMBEDDING_PROVIDER`` is set.
    An absent database in ``off`` mode does not create a file as a side effect.
    """
    env = os.environ.get
    provider = (env("SKILL_PROOF_EMBEDDING_PROVIDER") or "off").strip().lower()
    if provider not in {"off", "local", "gateway"}:
        provider = "off"
    configured_db = db_path or env("SKILL_PROOF_VECTOR_DB") or (
        default_hermes_home() / "plugin-data" / "skill-proof" / "embeddings.db"
    )
    path = pathlib.Path(configured_db).expanduser()
    if provider == "off" and not path.exists():
        return None
    return VectorIndex(
        db_path=path,
        provider=provider,
        model=env("SKILL_PROOF_EMBEDDING_MODEL") or "Xenova/multilingual-e5-small",
        gateway_url=env("SKILL_PROOF_GATEWAY_URL") or "http://localhost:20128/v1/embeddings",
        gateway_key_env=env("SKILL_PROOF_GATEWAY_KEY_ENV") or "HERMES_CUSTOM_LOCALHOST_20128_API_KEY",
        models_dir=env("SKILL_PROOF_MODELS_DIR") or None,
    )
