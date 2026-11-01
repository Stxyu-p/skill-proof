import os
import pathlib
import sqlite3
import unittest
import tempfile
import time

from skill_proof._dialogue import _MEMCORE_TRIGGER_RE, _skill_is_negated
import skill_proof._dialogue as dialogue

class TestMemCoreSynergy(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp_dir.name) / "memory.db"
        
        # Create minimal MemCore SQLite schema
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE memory (
                id TEXT PRIMARY KEY,
                current_version_id TEXT,
                lifecycle TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE memory_version (
                id TEXT PRIMARY KEY,
                content TEXT
            )
        """)
        # Insert a negative preference
        conn.execute("INSERT INTO memory VALUES ('m1', 'v1', 'accepted')")
        conn.execute("INSERT INTO memory_version VALUES ('v1', 'ห้ามใช้ test-driven-development ในโปรเจกต์นี้')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()
        dialogue._MEMCORE_VETO_CACHE = (0.0, set())

    def test_memcore_regex_triggers(self):
        self.assertIn("test-driven-development", [m.group(1).lower() for m in _MEMCORE_TRIGGER_RE.finditer("ห้ามใช้ test-driven-development")])
        self.assertIn("tdd", [m.group(1).lower() for m in _MEMCORE_TRIGGER_RE.finditer("don't use tdd")])
        self.assertIn("code-review", [m.group(1).lower() for m in _MEMCORE_TRIGGER_RE.finditer("อย่าใช้ code-review")])

    def test_mock_memcore_db_veto(self):
        # Point to our temporary DB
        original_home = pathlib.Path.home
        try:
            # Inject temporary db into memcore_skill_vetoes logic
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cur = conn.execute("""
                SELECT v.content FROM memory m 
                JOIN memory_version v ON v.id = m.current_version_id 
                WHERE m.lifecycle IN ('accepted', 'candidate')
            """)
            vetoes = set()
            for (content,) in cur.fetchall():
                for match in _MEMCORE_TRIGGER_RE.finditer(content or ""):
                    vetoes.add(match.group(1).lower().replace("_", "-"))
            conn.close()

            self.assertIn("test-driven-development", vetoes)
            
            # Now set the cache directly to simulate live MemCore veto
            dialogue._MEMCORE_VETO_CACHE = (time.monotonic(), vetoes)
            
            # Verify skill is negated even if query has no negative words!
            is_negated = _skill_is_negated("test-driven-development", query="Write unit tests for the auth service")
            self.assertTrue(is_negated, "Skill should be vetoed by MemCore persistent constraint")

            # Other skills should not be negated
            is_other_negated = _skill_is_negated("pr", query="Write unit tests for the auth service")
            self.assertFalse(is_other_negated)
        finally:
            dialogue._MEMCORE_VETO_CACHE = (0.0, set())

if __name__ == "__main__":
    unittest.main()
