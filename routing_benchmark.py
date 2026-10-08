"""Evaluate labeled routing cases against the Skill Proof router.

The fixture (``routing_cases.json``) is materialized into a throwaway skill
tree, so the benchmark never reads or touches real skills.  Cases are labeled
with a tier: ``core`` cases are gated (the router must pass them) and
``stretch`` cases are measured and reported as documented gaps of the
lexical-only design.

    python routing_benchmark.py                  # report on the shipped fixture
    python routing_benchmark.py --gate           # exit 1 when the gate fails
    python routing_benchmark.py --sweep          # threshold sensitivity table
    python routing_benchmark.py --json out.json  # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core import extract_explicit_skill_names, scan_catalog, select_skill  # noqa: E402

DEFAULT_FIXTURE = pathlib.Path(__file__).with_name("routing_cases.json")
GATED_TIERS = ("core",)
DEFAULT_MIN_SCORE = 0.28
DEFAULT_MIN_MARGIN = 0.05
DEFAULT_LIMIT = 3
SWEEP_SCORES = (0.20, 0.24, 0.28, 0.32, 0.36, 0.40)
SWEEP_MARGINS = (0.0, 0.05, 0.10, 0.15)


def load_fixture(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("skills"), list) or not isinstance(data.get("cases"), list):
        raise ValueError("fixture must contain 'skills' and 'cases' lists")
    for skill in data["skills"]:
        if not isinstance(skill, Mapping) or "name" not in skill or "description" not in skill:
            raise ValueError("every fixture skill needs 'name' and 'description'")
    for case in data["cases"]:
        if not isinstance(case, Mapping) or "query" not in case or "expected" not in case:
            raise ValueError("every fixture case needs 'query' and 'expected'")
    return data


def _frontmatter_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(str(value), ensure_ascii=False) for value in values) + "]"


def materialize(data: Mapping[str, Any], root: pathlib.Path) -> None:
    """Write fixture skills as a real skill tree (frontmatter only, no body tricks)."""
    for index, skill in enumerate(data["skills"]):
        folder = root / f"skill-{index:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"name: {json.dumps(str(skill['name']))}",
            f"description: {json.dumps(str(skill['description']), ensure_ascii=False)}",
        ]
        aliases = tuple(skill.get("aliases") or ())
        tags = tuple(skill.get("tags") or ())
        if aliases:
            lines.append(f"aliases: {_frontmatter_list(aliases)}")
        if tags:
            lines.append(f"tags: {_frontmatter_list(tags)}")
        lines += ["---", "", "Workflow instructions.", ""]
        (folder / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


@contextmanager
def fixture_catalog(data: Mapping[str, Any]) -> Iterator[Any]:
    with tempfile.TemporaryDirectory(prefix="skill-proof-routing-") as tmp:
        root = pathlib.Path(tmp)
        materialize(data, root)
        yield scan_catalog({"fixture": root})


def case_tier(case: Mapping[str, Any]) -> str:
    return str(case.get("tier") or "core")


def _selected_cases(cases: Sequence[Mapping[str, Any]], tiers: Sequence[str]) -> list[Mapping[str, Any]]:
    return [case for case in cases if case_tier(case) in tiers]


def _case_record(
    index: int,
    case: Mapping[str, Any],
    result: Any,
    expected: Optional[str],
) -> dict[str, Any]:
    actual = result.selected.skill.name if result.selected else None
    if actual == expected:
        outcome = "correct"
    elif actual is not None:
        outcome = "false_selection"
    else:
        outcome = "miss"
    return {
        "index": index,
        "tier": case_tier(case),
        "category": str(case.get("category") or "uncategorized"),
        "query": str(case["query"]),
        "expected": expected,
        "actual": actual,
        "reason": result.reason,
        "status": result.status,
        "outcome": outcome,
        "note": str(case.get("note") or ""),
        "candidates": [
            {"name": candidate.skill.name, "score": candidate.score, "reasons": list(candidate.reasons)}
            for candidate in result.candidates
        ],
    }


def _metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    expected_selections = [r for r in records if r["expected"] is not None]
    expected_abstains = [r for r in records if r["expected"] is None]
    # A correct abstain is not a correct selection: recall only counts the
    # cases that asked for a skill.
    correct = sum(1 for r in expected_selections if r["actual"] == r["expected"])
    false_selections = sum(1 for r in records if r["outcome"] == "false_selection")
    misses = sum(1 for r in records if r["outcome"] == "miss")
    abstain_correct = sum(1 for r in expected_abstains if r["actual"] is None)
    selections = correct + false_selections
    return {
        "cases": len(records),
        "expected_selections": len(expected_selections),
        "expected_abstains": len(expected_abstains),
        "correct": correct,
        "false_selections": false_selections,
        "misses": misses,
        "abstain_correct": abstain_correct,
        "selections": selections,
        "recall": round(correct / len(expected_selections), 4) if expected_selections else 1.0,
        "precision": round(correct / selections, 4) if selections else 1.0,
        "abstain_accuracy": round(abstain_correct / len(expected_abstains), 4) if expected_abstains else 1.0,
        "decision_accuracy": round((correct + abstain_correct) / len(records), 4) if records else 1.0,
        "failures": [r for r in records if r["outcome"] != "correct"],
    }


def compare(
    data: Mapping[str, Any],
    *,
    catalog: Optional[Any] = None,
    tiers: Sequence[str] = GATED_TIERS,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    limit: int = DEFAULT_LIMIT,
    synonyms: Optional[Mapping[str, Sequence[str]]] = None,
) -> dict[str, Any]:
    """Score the fixture and return per-case records plus aggregate metrics."""
    if catalog is None:
        with fixture_catalog(data) as built:
            return compare(
                data,
                catalog=built,
                tiers=tiers,
                min_score=min_score,
                min_margin=min_margin,
                limit=limit,
                synonyms=synonyms,
            )
    cases = _selected_cases(data["cases"], tiers)
    records = []
    for index, case in enumerate(cases):
        query = str(case["query"])
        result = select_skill(
            catalog,
            query,
            explicit_names=extract_explicit_skill_names(query),
            min_score=min_score,
            min_margin=min_margin,
            limit=limit,
            synonyms=synonyms,
        )
        records.append(_case_record(index, case, result, case.get("expected")))
    return {
        "skills": len(catalog.skills),
        "tiers": list(tiers),
        "min_score": float(min_score),
        "min_margin": float(min_margin),
        "metrics": _metrics(records),
        "records": records,
    }


def evaluate(
    data: Mapping[str, Any],
    *,
    tiers: Sequence[str] = GATED_TIERS,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    limit: int = DEFAULT_LIMIT,
    synonyms: Optional[Mapping[str, Sequence[str]]] = None,
) -> dict[str, Any]:
    """Materialize the fixture, score it, and add per-category breakdowns."""
    with fixture_catalog(data) as catalog:
        report = compare(
            data,
            catalog=catalog,
            tiers=tiers,
            min_score=min_score,
            min_margin=min_margin,
            limit=limit,
            synonyms=synonyms,
        )
    by_category: dict[str, Any] = {}
    by_tier: dict[str, Any] = {}
    for record in report["records"]:
        by_category.setdefault(record["category"], []).append(record)
        by_tier.setdefault(record["tier"], []).append(record)
    report["metrics"]["by_category"] = {
        name: {k: v for k, v in _metrics(records).items() if k != "failures"}
        for name, records in sorted(by_category.items())
    }
    report["metrics"]["by_tier"] = {
        name: {k: v for k, v in _metrics(records).items() if k != "failures"}
        for name, records in sorted(by_tier.items())
    }
    return report


def gate(
    report: Mapping[str, Any],
    *,
    min_recall: float = 0.95,
    max_false_selections: int = 0,
    min_abstain_accuracy: float = 0.9,
) -> dict[str, Any]:
    """Turn a report into explicit pass/fail checks (fail-closed default)."""
    metrics = report["metrics"]
    checks = [
        {
            "name": "recall",
            "actual": metrics["recall"],
            "requirement": f">= {min_recall}",
            "passed": metrics["recall"] >= min_recall,
        },
        {
            "name": "false_selections",
            "actual": metrics["false_selections"],
            "requirement": f"<= {max_false_selections}",
            "passed": metrics["false_selections"] <= max_false_selections,
        },
        {
            "name": "abstain_accuracy",
            "actual": metrics["abstain_accuracy"],
            "requirement": f">= {min_abstain_accuracy}",
            "passed": metrics["abstain_accuracy"] >= min_abstain_accuracy,
        },
    ]
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def sweep(
    data: Mapping[str, Any],
    *,
    tiers: Sequence[str] = GATED_TIERS,
    scores: Sequence[float] = SWEEP_SCORES,
    margins: Sequence[float] = SWEEP_MARGINS,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Recall/precision sensitivity across min_score x min_margin."""
    rows = []
    with fixture_catalog(data) as catalog:
        for min_score in scores:
            for min_margin in margins:
                report = compare(
                    data,
                    catalog=catalog,
                    tiers=tiers,
                    min_score=min_score,
                    min_margin=min_margin,
                    limit=limit,
                )
                metrics = report["metrics"]
                rows.append(
                    {
                        "min_score": float(min_score),
                        "min_margin": float(min_margin),
                        "recall": metrics["recall"],
                        "precision": metrics["precision"],
                        "abstain_accuracy": metrics["abstain_accuracy"],
                        "decision_accuracy": metrics["decision_accuracy"],
                        "false_selections": metrics["false_selections"],
                        "misses": metrics["misses"],
                    }
                )
    return rows


def format_report(report: Mapping[str, Any], *, show_failures: bool = True) -> str:
    metrics = report["metrics"]
    lines = [
        f"fixture skills: {report['skills']}  tiers: {', '.join(report['tiers'])}",
        f"thresholds:    min_score={report['min_score']}  min_margin={report['min_margin']}",
        (
            "cases:         {cases} ({expected_selections} expect a skill, {expected_abstains} expect abstain)"
        ).format(**metrics),
        (
            "results:       recall={recall}  precision={precision}  abstain_accuracy={abstain_accuracy}  "
            "decision_accuracy={decision_accuracy}"
        ).format(**metrics),
        (
            "errors:        false_selections={false_selections}  misses={misses}"
        ).format(**metrics),
        "",
        "by category:",
    ]
    for name, bucket in metrics.get("by_category", {}).items():
        lines.append(
            "  {name:<24} cases={cases:<3} recall={recall:<7} abstain={abstain_accuracy:<7} false={false_selections}".format(
                name=name, **bucket
            )
        )
    for name, bucket in metrics.get("by_tier", {}).items():
        lines.append(
            "  tier {name:<19} cases={cases:<3} recall={recall:<7} abstain={abstain_accuracy:<7} false={false_selections}".format(
                name=name, **bucket
            )
        )
    if show_failures and metrics["failures"]:
        lines += ["", "failures:"]
        for failure in metrics["failures"]:
            lines.append(
                "  [{tier}/{category}] expected={expected} actual={actual} ({status}/{reason})\n"
                "    query: {query}".format(**failure)
            )
            if failure["note"]:
                lines.append(f"    note:  {failure['note']}")
            for candidate in failure["candidates"][:3]:
                lines.append(
                    "    top:   {name} score={score} reasons={reasons}".format(**candidate)
                )
    return "\n".join(lines)


def format_sweep(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = ["min_score min_margin  recall  precision  abstain  decision  false  misses"]
    for row in rows:
        lines.append(
            "{min_score:<9.2f} {min_margin:<10.2f} {recall:<7} {precision:<10} {abstain_accuracy:<8} "
            "{decision_accuracy:<9} {false_selections:<6} {misses}".format(**row)
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate labeled routing cases.")
    parser.add_argument("fixture", nargs="?", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--gate", action="store_true", help="exit non-zero when the gate fails")
    parser.add_argument("--all-tiers", action="store_true", help="gate stretch cases too")
    parser.add_argument("--min-recall", type=float, default=0.95)
    parser.add_argument("--max-false-selections", type=int, default=0)
    parser.add_argument("--min-abstain-accuracy", type=float, default=0.9)
    parser.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--min-margin", type=float, default=DEFAULT_MIN_MARGIN)
    parser.add_argument("--sweep", action="store_true", help="print threshold sensitivity")
    parser.add_argument("--json", dest="json_path", help="write the report to this path")
    parser.add_argument("--quiet", action="store_true", help="only print the verdict lines")
    args = parser.parse_args(argv)

    data = load_fixture(pathlib.Path(args.fixture))
    tiers = ("core", "stretch") if args.all_tiers else GATED_TIERS
    report = evaluate(
        data,
        tiers=tiers,
        min_score=args.min_score,
        min_margin=args.min_margin,
    )
    verdict = gate(
        report,
        min_recall=args.min_recall,
        max_false_selections=args.max_false_selections,
        min_abstain_accuracy=args.min_abstain_accuracy,
    )
    report["gate"] = verdict
    if args.sweep:
        report["sweep"] = sweep(data, tiers=tiers)
    if args.json_path:
        pathlib.Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.quiet:
        for check in verdict["checks"]:
            mark = "pass" if check["passed"] else "FAIL"
            print(f"{mark} {check['name']}: {check['actual']} (needs {check['requirement']})")
    else:
        print(format_report(report))
        if args.sweep:
            print()
            print(format_sweep(report["sweep"]))
        print()
        for check in verdict["checks"]:
            mark = "pass" if check["passed"] else "FAIL"
            print(f"{mark} {check['name']}: {check['actual']} (needs {check['requirement']})")
        print(f"gate: {'PASS' if verdict['passed'] else 'FAIL'}")
    if args.gate and not verdict["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
