"""Reproducible synthetic benchmark; never reads personal skills or prompts."""
import json
import pathlib
import statistics
import tempfile
import time

from core import SkillProofEngine


def run():
    with tempfile.TemporaryDirectory(prefix="skill-proof-bench-") as tmp:
        root = pathlib.Path(tmp)
        for i in range(300):
            folder = root / f"workflow-{i}"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                f"---\nname: workflow-{i}\ndescription: Handle domain {i} maintenance\n---\n"
                + "Instruction content.\n" * 200, encoding="utf-8")
        engine = SkillProofEngine({"benchmark": root})
        times = []
        for i in range(21):
            started = time.perf_counter()
            engine.begin_turn(turn_id=str(i), session_id="bench", task_id="bench",
                              query="Use workflow-42 for maintenance")
            times.append((time.perf_counter() - started) * 1000)
        cases = [
            ("Use $workflow-42", "workflow-42"),
            ("ใช้สกิล workflow-42", "workflow-42"),
            ("Please use workflow-99", "workflow-99"),
            ("What is the capital of France?", None),
            ("Use $unknown", None),
        ]
        correct = 0
        for i, (query, expected) in enumerate(cases):
            turn = engine.begin_turn(turn_id=f"case-{i}", session_id="bench", task_id="bench", query=query)
            actual = turn.selection.selected.skill.name if turn.selection.selected else None
            correct += actual == expected
        print(json.dumps({"skills": 300, "cold_ms": round(times[0], 3),
                          "warm_median_ms": round(statistics.median(times[1:]), 3),
                          "warm_p95_ms": round(sorted(times[1:])[18], 3),
                          "synthetic_routing_correct": correct, "synthetic_routing_total": len(cases)}, indent=2))


if __name__ == "__main__":
    run()
