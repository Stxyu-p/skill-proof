"""Phase 3 gate measurements for the v0.16 audit plan.

Gate 1 (bypass): the confidence-gated cascade should skip embedding work on
confident lexical winners. Reported against two denominators, because the
fixture deliberately mixes abstain cases (23/63) whose vector path is a
designed rescue channel: all vector-eligible cases, and cases that expect a
selection. Recall with and without the vector index must be identical.

Gate 2 (hook latency): bridge.py PreToolUse handler, in-process, < 15 ms.

Run: python phase3_gates.py
"""
from __future__ import annotations

import json
import pathlib
import statistics
import tempfile
import time

import bridge
import routing_benchmark
from core import InMemoryPolicyStore, select_skill


class CountingVectorIndex:
    """Duck-typed index: counts bypass vs vector calls; ranks nothing."""

    _memory_vectors = {"_stub": (1.0,)}

    def __init__(self) -> None:
        self.fastpath = 0
        self.vector_calls = 0

    def record_lexical_fastpath(self) -> None:
        self.fastpath += 1

    def rank(self, query: str, top_k: int = 20):
        self.vector_calls += 1
        return []


def measure_bypass() -> dict:
    data = routing_benchmark.load_fixture(pathlib.Path("routing_cases.json"))
    index = CountingVectorIndex()
    expected_total = 0
    expected_eligible = 0
    expected_fastpath = 0
    selections_changed = 0
    with routing_benchmark.fixture_catalog(data) as catalog:
        for case in data["cases"]:
            expects_selection = bool(case.get("expect") or case.get("expected"))
            baseline = select_skill(catalog, case["query"])
            baseline_name = baseline.selected.skill.name if baseline.selected else None
            before = (index.fastpath, index.vector_calls)
            result = select_skill(catalog, case["query"], vector_index=index)
            got = result.selected.skill.name if result.selected else None
            if got != baseline_name:
                selections_changed += 1
            used_fastpath = index.fastpath > before[0]
            used_vector = index.vector_calls > before[1]
            if expects_selection:
                expected_total += 1
                if used_fastpath:
                    expected_fastpath += 1
                if used_fastpath or used_vector:
                    expected_eligible += 1
    eligible = index.fastpath + index.vector_calls
    return {
        "cases": len(data["cases"]),
        "vector_eligible": eligible,
        "fastpath": index.fastpath,
        "vector_calls": index.vector_calls,
        "bypass_rate_all_eligible": round(index.fastpath / eligible, 4) if eligible else None,
        "selection_expected_cases": expected_total,
        "selection_expected_eligible": expected_eligible,
        "selection_expected_fastpath": expected_fastpath,
        "bypass_rate_selection_expected": (
            round(expected_fastpath / expected_eligible, 4) if expected_eligible else None
        ),
        "recall_preserved": selections_changed == 0,
    }


def measure_hook_latency(iterations: int = 60) -> dict:
    payload = {"tool_name": "read_file"}
    state = {
        "mode": "enforce-tools",
        "selected_skill": "alpha",
        "forbidden_tools": ["rm"],
        "ordered_tools": ["first", "second"],
        "completed": [],
    }
    memory_store = InMemoryPolicyStore(dict(state))
    times = []
    for _ in range(iterations):
        started = time.perf_counter()
        bridge.run_hook(payload, store=memory_store)
        times.append((time.perf_counter() - started) * 1000)
    with tempfile.TemporaryDirectory() as tmp:
        from _compliance import FilePolicyStore

        file_store = FilePolicyStore(pathlib.Path(tmp) / "active_turn.json")
        file_store.write(state)
        file_times = []
        for _ in range(iterations):
            started = time.perf_counter()
            bridge.run_hook(payload, store=file_store)
            file_times.append((time.perf_counter() - started) * 1000)
    return {
        "memory_median_ms": round(statistics.median(times), 4),
        "memory_p95_ms": round(sorted(times)[int(len(times) * 0.95)], 4),
        "file_median_ms": round(statistics.median(file_times), 4),
        "file_p95_ms": round(sorted(file_times)[int(len(file_times) * 0.95)], 4),
    }


def main() -> int:
    bypass = measure_bypass()
    hook = measure_hook_latency()
    report = {
        "bypass_gate": {
            **bypass,
            "target": (
                ">= 0.70. Abstain cases (no expected selection) keep the vector path as a "
                "designed rescue channel, so both denominators are reported; recall with "
                "and without the vector index must be identical."
            ),
            "pass_selection_expected": bool(
                bypass["recall_preserved"]
                and (bypass["bypass_rate_selection_expected"] or 0) >= 0.70
            ),
            "pass_all_eligible": bool((bypass["bypass_rate_all_eligible"] or 0) >= 0.70),
        },
        "hook_gate": {
            **hook,
            "target": "< 15 ms in-process (python process boot excluded)",
            "pass": hook["file_median_ms"] < 15.0,
        },
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
