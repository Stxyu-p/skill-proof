"""Skill Proof Hermes plugin adapter.

The adapter uses only the public ``PluginContext`` surface.  Selection and
evidence logic live in :mod:`core`, which can be tested without importing
Hermes Agent internals.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import time
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Mapping, Optional, Sequence

try:  # Hermes loads directory plugins as packages; direct unit tests do not.
    from .core import (
        SkillProofEngine,
        __version__ as CORE_VERSION,
        normalize_identifier,
        default_hermes_home,
        detect_agent_roots,
    )
except ImportError:  # pragma: no cover - exercised by direct adapter tests
    from core import (
        SkillProofEngine,
        __version__ as CORE_VERSION,
        normalize_identifier,
        default_hermes_home,
        detect_agent_roots,
    )


logger = logging.getLogger(__name__)

_HELP = """\
/skill-proof — inspect auditable skill-selection evidence

Usage:
  /skill-proof status      Compact state for the latest turn
  /skill-proof explain     Selection decision and ranked candidates
  /skill-proof why <name>  Why this skill won, lost, or was vetoed
  /skill-proof stats      Hit rate, misses, and overrides from the audit log
  /skill-proof overlap    Near-duplicate skills worth pruning
  /skill-proof trace       Full bounded JSON receipt
  /skill-proof refresh     Reread local skill content on the next turn
  /skill-proof health      Hook activity, catalog diagnostics, and timing

Loaded means Hermes emitted a successful skill lifecycle event. It does not
mean the model followed the skill, and it does not verify the task result.
"""


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    """Integer setting whose default includes the low bound (focus_turns may be 0)."""
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if low <= parsed <= high else default


def _positive_int(value: Any, default: int, maximum: int) -> int:
    return _bounded_int(value, default, 1, maximum)


def _float_setting(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0 <= parsed <= 1 else default


def _bool_setting(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _default_profile_home() -> pathlib.Path:
    return default_hermes_home()


def _resolve_roots(configured: Any) -> dict[str, pathlib.Path]:
    if isinstance(configured, list):
        roots: dict[str, pathlib.Path] = {}
        for index, item in enumerate(configured, start=1):
            if isinstance(item, str) and item.strip():
                roots[f"configured-{index}"] = pathlib.Path(item).expanduser()
        if roots:
            return roots
    return {"profile": _default_profile_home() / "skills"}


class SkillProofPlugin:
    """Thin translation layer between Hermes hooks and ``SkillProofEngine``."""

    _SKILL_TOOLS = frozenset({"skill_view", "skills_list", "skill_search", "skill_manage"})

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.roots = _resolve_roots(ctx.get_config("skill_roots", []))
        mode = str(ctx.get_config("mode", "nudge") or "nudge")
        invariants = ctx.get_config("invariants", {})
        synonyms = ctx.get_config("synonyms", {})
        hub_lock_path: Optional[pathlib.Path] = None
        if _bool_setting(ctx.get_config("hub_provenance", True), True):
            configured_hub = str(ctx.get_config("hub_lock_path", "") or "").strip()
            hub_lock_path = (
                pathlib.Path(configured_hub).expanduser()
                if configured_hub
                else _default_profile_home() / "skills" / ".hub" / "lock.json"
            )
        self.engine = SkillProofEngine(
            self.roots,
            mode=mode,
            min_score=_float_setting(ctx.get_config("min_score", 0.28), 0.28),
            min_margin=_float_setting(ctx.get_config("min_margin", 0.05), 0.05),
            max_candidates=_positive_int(ctx.get_config("max_candidates", 3), 3, 20),
            max_skill_bytes=_positive_int(
                ctx.get_config("max_skill_bytes", 256 * 1024), 256 * 1024, 4 * 1024 * 1024
            ),
            context_budget_bytes=_positive_int(
                ctx.get_config("context_budget_bytes", 1200), 1200, 16 * 1024
            ),
            observed_tool_limit=_positive_int(ctx.get_config("observed_tool_limit", 16), 16, 100),
            invariants=invariants if isinstance(invariants, Mapping) else None,
            hub_lock_path=hub_lock_path,
            synonyms=synonyms if isinstance(synonyms, Mapping) else None,
            session_memory=_bool_setting(ctx.get_config("session_memory", True), True),
            focus_turns=_bounded_int(ctx.get_config("focus_turns", 5), 5, 0, 50),
            disabled=ctx.get_config("disabled", None),
        )
        self.visible_receipt = _bool_setting(ctx.get_config("visible_receipt", True), True)
        self.receipt_style = ctx.get_config("receipt_style", "compact")
        self.receipt_history_limit = _positive_int(
            ctx.get_config("receipt_history_limit", 20), 20, 100
        )
        self._policy_errors: set[tuple[str, str, str]] = set()
        self._health_lock = threading.RLock()
        self._hook_activity: dict = {}
        self.audit_enabled = _bool_setting(ctx.get_config("audit_log", True), True)
        self.audit_limit = _bounded_int(ctx.get_config("audit_limit", 500), 500, 10, 10000)
        configured_audit = str(ctx.get_config("audit_path", "") or "").strip()
        self.audit_path = (
            pathlib.Path(configured_audit).expanduser()
            if configured_audit
            else _default_profile_home() / "plugin-data" / "skill-proof" / "audit.jsonl"
        )
        self._audit_writes = 0
        self._audit_broken = False

    @staticmethod
    def _key(session_id: Any, task_id: Any, turn_id: Any) -> tuple[str, str, str]:
        return (str(session_id or ""), str(task_id or ""), str(turn_id or ""))

    def on_pre_llm_call(
        self,
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        user_message: Any = "",
        **_: Any,
    ) -> Optional[dict[str, str]]:
        effective_turn_id = str(turn_id or f"{session_id}:{task_id}:current")
        key = self._key(session_id, task_id, effective_turn_id)
        started = time.perf_counter()
        try:
            availability_error = False
            names = set()
            try:
                listing = json.loads(self.ctx.dispatch_tool("skills_list", {}))
                if not isinstance(listing, dict) or listing.get("success") is not True or not isinstance(listing.get("skills"), list):
                    raise ValueError("Invalid Hermes skill listing")
                for item in listing["skills"]:
                    if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                        raise ValueError("Invalid Hermes skill name")
                    names.add(item["name"])
            except Exception:
                availability_error = True
            listing_ms = (time.perf_counter() - started) * 1000
            turn = self.engine.begin_turn(
                turn_id=effective_turn_id,
                session_id=str(session_id or ""),
                task_id=str(task_id or ""),
                query=str(user_message or ""),
                available_names=names,
                availability_error=availability_error,
            )
            self.engine.record_timing(effective_turn_id, listing_ms=listing_ms,
                                      total_ms=(time.perf_counter() - started) * 1000)
        except Exception:
            logger.exception("Skill Proof could not begin turn")
            if self.engine.mode == "enforce-tools":
                self._policy_errors.add(key)
            return None
        self._policy_errors.discard(key)
        return {"context": turn.context} if turn.context else None

    def on_pre_tool_call(
        self,
        tool_name: str = "",
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        **_: Any,
    ) -> Optional[dict[str, str]]:
        effective_turn_id = str(turn_id or f"{session_id}:{task_id}:current")
        normalized_tool = normalize_identifier(tool_name)
        if (
            self.engine.mode == "enforce-tools"
            and self._key(session_id, task_id, effective_turn_id) in self._policy_errors
            and normalized_tool not in self._SKILL_TOOLS
        ):
            return {
                "action": "block",
                "message": "Skill Proof blocked this tool because skill policy initialization failed.",
            }
        try:
            decision = self.engine.guard_tool(
                turn_id=effective_turn_id,
                session_id=str(session_id or ""),
                task_id=str(task_id or ""),
                tool_name=str(tool_name or ""),
            )
        except Exception:
            logger.exception("Skill Proof pre-tool policy failed")
            if self.engine.mode == "enforce-tools" and normalized_tool not in self._SKILL_TOOLS:
                return {
                    "action": "block",
                    "message": "Skill Proof blocked this tool because of an internal policy error.",
                }
            return None
        if decision.allowed:
            return None
        return {"action": "block", "message": decision.message or decision.reason}

    def on_skill_lifecycle(
        self,
        action: str = "",
        skill_name: str = "",
        provenance: Optional[str] = None,
        task_id: str = "",
        session_id: str = "",
        use_count: Optional[int] = None,
        reused: Optional[bool] = None,
        reuse_after_patch: Optional[bool] = None,
        **_: Any,
    ) -> None:
        if normalize_identifier(action) != "loaded":
            return
        try:
            self.engine.observe_skill_loaded(
                session_id=str(session_id or ""),
                task_id=str(task_id or ""),
                skill_name=str(skill_name or ""),
                provenance=provenance,
                use_count=use_count,
                reused=reused,
                reuse_after_patch=reuse_after_patch,
            )
        except Exception:
            logger.exception("Skill Proof could not record skill lifecycle event")

    def on_post_tool_call(
        self,
        tool_name: str = "",
        args: Optional[Mapping[str, Any]] = None,
        result: Any = None,
        status: Optional[str] = None,
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        **_: Any,
    ) -> None:
        try:
            self.engine.observe_tool_result(
                turn_id=str(turn_id or f"{session_id}:{task_id}:current"),
                session_id=str(session_id or ""),
                task_id=str(task_id or ""),
                tool_name=str(tool_name or ""),
                args=args,
                result=result,
                status=status,
            )
        except Exception:
            logger.exception("Skill Proof could not record tool result")

    def on_transform_llm_output(
        self,
        response_text: Any = None,
        session_id: str = "",
        **_: Any,
    ) -> Optional[str]:
        if not self.visible_receipt or not isinstance(response_text, str):
            return None
        receipt = self.engine.receipt(session_id=str(session_id or ""))
        if not receipt:
            return None
        decision = receipt.get("decision") or {}
        if receipt.get("selected") is None and not decision.get("explicit") and not receipt.get("errors"):
            return None
        marker = "\n\n[Skill Proof:"
        if marker in response_text:
            return None
        summary = self.engine.summary(session_id=str(session_id or ""))
        detail = summary.removeprefix("Skill Proof: ")
        if self.receipt_style == "compact":
            selected = receipt.get("selected") or {}
            evidence = receipt.get("evidence") or {}
            compliance_val = receipt.get("compliance", "unassessed")
            detail = f"{selected.get('name', 'none')} | {'loaded' if evidence.get('hermes_loaded_event') else 'not loaded'} | compliance {compliance_val}"
        problems = list(receipt.get("errors") or [])
        if decision.get("status") == "blocked":
            problems.append(decision.get("reason", "blocked"))
        if receipt.get("compliance") == "failed" and receipt.get("compliance_reasons"):
            problems.extend(receipt.get("compliance_reasons"))
        hub_evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), Mapping) else {}
        hub_record = hub_evidence.get("hub") if isinstance(hub_evidence.get("hub"), Mapping) else {}
        if hub_record.get("bundle") == "modified":
            problems.append("hub_bundle_modified")
        session_record = (
            hub_evidence.get("session") if isinstance(hub_evidence.get("session"), Mapping) else {}
        )
        if session_record.get("focus"):
            detail += f" | focus {session_record.get('focus')} ({session_record.get('focus_remaining')} turns)"
        if problems:
            detail += " | " + ", ".join(dict.fromkeys(problems)) + "; see /skill-proof explain"
        return f"{response_text}{marker} {detail}]"

    @staticmethod
    def _format_why(report: Mapping[str, Any], asked: str = "") -> str:
        skill = report.get("skill") if isinstance(report.get("skill"), Mapping) else {}
        decision = report.get("decision") if isinstance(report.get("decision"), Mapping) else {}
        thresholds = report.get("thresholds") if isinstance(report.get("thresholds"), Mapping) else {}
        selected = report.get("selected") if isinstance(report.get("selected"), Mapping) else None
        lines = [
            f"Turn: {report.get('turn_id')}  "
            f"decision={decision.get('status')}/{decision.get('reason')}  "
            f"thresholds min_score={thresholds.get('min_score')} "
            f"min_margin={thresholds.get('min_margin')}"
        ]
        if selected:
            lines.append(
                f"Selected: {selected.get('name')} score={selected.get('score')} "
                f"reasons={','.join(selected.get('reasons') or [])}"
            )
        else:
            lines.append("Selected: none")
        vetoed = report.get("vetoed") or []
        if vetoed:
            lines.append(f"Vetoed this turn: {', '.join(vetoed)}")
        if skill:
            verdict = skill.get("verdict")
            lines.append(f"Asked about: {asked}")
            if verdict == "selected":
                lines.append(
                    f"  -> selected: score={skill.get('score')} "
                    f"reasons={','.join(skill.get('reasons') or [])}"
                )
            elif verdict == "vetoed":
                lines.append("  -> vetoed by this turn's wording; it never entered the ranking")
            elif verdict in ("ranked", "below_threshold"):
                lines.append(
                    f"  -> ranked #{skill.get('rank')} at {skill.get('score')} "
                    f"(threshold {skill.get('threshold')}) "
                    f"threshold_met={skill.get('threshold_met')} "
                    f"gap_to_top={skill.get('gap_to_top')}"
                )
                if skill.get("reasons"):
                    lines.append(f"  -> matched on {','.join(skill.get('reasons'))}")
            elif verdict == "unknown_skill":
                lines.append("  -> no indexed SKILL.md under the configured roots has this name")
            elif verdict == "not_ranked":
                lines.append(f"  -> {skill.get('note')}")
            else:
                lines.append(f"  -> {skill.get('note')}")
        if isinstance(report.get("rank_table"), list) and report["rank_table"]:
            lines.append("Top ranked this turn:")
            for row in report["rank_table"][:5]:
                lines.append(
                    f"  - {row.get('name')} {row.get('score')} "
                    f"({','.join(row.get('reasons') or [])})"
                )
        lines.append("Evidence is derived numbers only; Skill Proof never stores prompts.")
        return "\n".join(lines)

    @staticmethod
    def _receipt_summary(receipt: Mapping[str, Any]) -> str:
        selected = receipt.get("selected")
        selected_name = selected.get("name") if isinstance(selected, Mapping) else "none"
        evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), Mapping) else {}
        runtime = receipt.get("runtime") if isinstance(receipt.get("runtime"), Mapping) else {}
        session = evidence.get("session") if isinstance(evidence.get("session"), Mapping) else {}
        summary = (
            f"Skill Proof: selected={selected_name} "
            f"loaded={'yes' if evidence.get('hermes_loaded_event') else 'no'} "
            f"active={'yes' if runtime.get('active') else 'no'} "
            f"compliance={receipt.get('compliance', 'unassessed')} "
            f"verification={receipt.get('verification', 'unverified')}"
        )
        if session.get("focus"):
            summary += f" focus={session.get('focus')}({session.get('focus_remaining')} turns)"
        reference = session.get("reference") if isinstance(session.get("reference"), Mapping) else {}
        if reference.get("kind"):
            summary += f" reference={reference.get('kind')}:{reference.get('phrase', '')}"
        return summary

    def _latest_receipt(self) -> dict[str, Any]:
        live = self.engine.receipt(turn_id=self.engine.latest_turn_id)
        if live:
            return live
        try:
            persisted = self.ctx.state.get("receipts", [])
        except Exception:
            logger.exception("Skill Proof could not read persisted receipts")
            return {}
        if isinstance(persisted, list) and persisted and isinstance(persisted[-1], dict):
            return persisted[-1]
        return {}

    def _persist(self, receipt: Mapping[str, Any]) -> None:
        if not receipt:
            return
        try:
            current = self.ctx.state.get("receipts", [])
            history = [item for item in current if isinstance(item, dict)] if isinstance(current, list) else []
            turn_id = receipt.get("turn_id")
            history = [item for item in history if item.get("turn_id") != turn_id]
            history.append(dict(receipt))
            self.ctx.state.set("receipts", history[-self.receipt_history_limit :])
        except Exception:
            logger.exception("Skill Proof could not persist receipt")

    def on_post_llm_call(
        self,
        session_id: str = "",
        task_id: str = "",
        turn_id: str = "",
        **_: Any,
    ) -> None:
        effective_turn_id = str(turn_id or "")
        if not effective_turn_id:
            current = self.engine.receipt(session_id=str(session_id or ""), task_id=str(task_id or ""))
            effective_turn_id = str(current.get("turn_id") or "")
        if effective_turn_id:
            payload = self.engine.finish_turn(turn_id=effective_turn_id)
            self._persist(payload)
            self._write_audit(payload, session_id)

    # --- Append-only decision log ----------------------------------------
    #
    # Derived numbers only: no prompt, no skill body. One JSON object per line,
    # so the file can be diffed, tailed, or shipped to a log store as-is.

    @staticmethod
    def _audit_record(receipt: Mapping[str, Any], session_id: str) -> dict[str, Any]:
        decision = receipt.get("decision") if isinstance(receipt.get("decision"), Mapping) else {}
        selected = receipt.get("selected") if isinstance(receipt.get("selected"), Mapping) else {}
        evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), Mapping) else {}
        session = evidence.get("session") if isinstance(evidence.get("session"), Mapping) else {}
        runtime = receipt.get("runtime") if isinstance(receipt.get("runtime"), Mapping) else {}
        performance = receipt.get("performance") if isinstance(receipt.get("performance"), Mapping) else {}
        return {
            "schema": "skill-proof.audit.v1",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "turn_id": receipt.get("turn_id"),
            "session_sha256": hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:16],
            "status": decision.get("status"),
            "reason": decision.get("reason"),
            "selected": selected.get("name"),
            "score": selected.get("score"),
            "reasons": list(selected.get("reasons") or []),
            "explicit": bool(decision.get("explicit")),
            "override_loaded": evidence.get("override_loaded") or None,
            "focus": session.get("focus"),
            "loaded": bool(evidence.get("hermes_loaded_event")),
            "compliance": receipt.get("compliance"),
            "active": bool(runtime.get("active")),
            "errors": list(receipt.get("errors") or []),
            "query_sha256": receipt.get("query_sha256"),
            "catalog_size": (receipt.get("catalog") or {}).get("skill_count")
            if isinstance(receipt.get("catalog"), Mapping) else None,
            "total_ms": performance.get("total_ms"),
        }

    def _write_audit(self, receipt: Mapping[str, Any], session_id: str) -> None:
        if not receipt or not self.audit_enabled or self._audit_broken:
            return
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(self._audit_record(receipt, session_id), ensure_ascii=False)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self._audit_writes += 1
            if self._audit_writes % 50 == 0:
                self._rotate_audit()
        except OSError:
            # A broken audit file must never break a turn; stop trying quietly.
            self._audit_broken = True
            logger.exception("Skill Proof could not append to %s", self.audit_path)

    def _rotate_audit(self) -> None:
        if not self.audit_path.is_file():
            return
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= self.audit_limit:
            return
        kept = [line for line in lines[-self.audit_limit:] if line.strip()]
        tmp_path = self.audit_path.with_name(self.audit_path.name + ".tmp")
        tmp_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp_path.replace(self.audit_path)

    def _overlap_report(self, min_similarity: float, limit: int):
        """Near-duplicates plus which side the host actually loads (audit hits)."""
        try:
            report = self.engine.overlap(min_similarity=min_similarity, limit=limit)
        except ValueError as error:
            return (
                "Usage: /skill-proof overlap [--min-similarity 0.4] [--limit 10] [--json]\n"
                f"{error}"
            )
        stats = self.stats()
        hits: dict[str, int] = {}
        if isinstance(stats, dict) and isinstance(stats.get("skills"), Mapping):
            for name, row in stats["skills"].items():
                if isinstance(row, Mapping):
                    hits[str(name)] = int(row.get("loaded") or 0)
        for row in report["pairs"]:
            left = hits.get(str(row["a"]), 0)
            right = hits.get(str(row["b"]), 0)
            row["hits_a"] = left
            row["hits_b"] = right
            row["drop_candidate"] = (
                None if left == right else (str(row["a"]) if left < right else str(row["b"]))
            )
        report["version"] = CORE_VERSION
        report["audit_hits_available"] = bool(hits)
        return report

    @staticmethod
    def _format_overlap(report: Mapping[str, Any]) -> str:
        lines = [
            (
                f"Skill Proof overlap — {report.get('skills')} skills, "
                f"{report.get('pairs_checked')} pairs checked, "
                f"similarity >= {report.get('min_similarity')}"
            ),
            (
                f"exact copies collapsed: {report.get('exact_copies')}  "
                f"divergent copies shadowed: {report.get('shadowed_copies')}  "
                f"near-duplicates found: {report.get('total_found')}"
            ),
        ]
        pairs = report.get("pairs") or []
        if not pairs:
            lines.append("No near-duplicates above the threshold.")
        for row in pairs:
            lines.append(
                f"{row['similarity']:.3f}  {row['a']} ({row['root_a']})  <->  "
                f"{row['b']} ({row['root_b']})"
            )
            lines.append(f"         shares: {', '.join(row['shared'])}")
            lines.append(f"         -> {row['recommendation']}")
            if report.get("audit_hits_available"):
                loaded = (
                    f"         usage: {row['a']} loaded {row['hits_a']}x, "
                    f"{row['b']} loaded {row['hits_b']}x"
                )
                if row.get("drop_candidate"):
                    loaded += f" -> consider dropping {row['drop_candidate']}"
                lines.append(loaded)
        if report.get("truncated"):
            lines.append(
                f"... {int(report.get('total_found', 0)) - len(pairs)} more pair(s) hidden by --limit"
            )
        if not report.get("audit_hits_available"):
            lines.append("No audit hits available yet, so usage cannot break the tie.")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        """Aggregate the append-only audit log into per-skill outcomes.

        Read-only and derived: hit rate counts a selected skill the host
        actually loaded, misses are selections nothing loaded, fallback is a
        focus carry (score 0) and is excluded from the hit rate, and an
        override is a skill the host loaded that we had not selected.
        """
        if not self.audit_enabled:
            return {"error": "audit_log is disabled, so there are no outcomes to aggregate."}
        if not self.audit_path.is_file():
            return {"error": f"No audit log at {self.audit_path} yet."}
        try:
            raw = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"error": f"Could not read {self.audit_path}."}
        records: list[dict[str, Any]] = []
        corrupt = 0
        for line in raw:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                corrupt += 1
                continue
            if isinstance(item, dict) and item.get("schema") == "skill-proof.audit.v1":
                records.append(item)
        return self._aggregate_audit(records, corrupt=corrupt, path=str(self.audit_path))

    @staticmethod
    def _aggregate_audit(
        records: Sequence[Mapping[str, Any]], *, corrupt: int = 0, path: str = ""
    ) -> dict[str, Any]:
        totals: dict[str, Any] = {
            "turns": len(records), "routed": 0, "loaded": 0, "miss": 0,
            "fallback": 0, "abstained": 0, "overridden": 0, "vetoed": 0,
        }
        skills: dict[str, dict[str, int]] = {}
        overrides: dict[str, int] = {}
        for record in records:
            if record.get("reason") == "negated_skill":
                totals["vetoed"] += 1
            override = record.get("override_loaded")
            if override:
                totals["overridden"] += 1
                name = str(override)
                overrides[name] = overrides.get(name, 0) + 1
            selected = record.get("selected")
            if not selected:
                totals["abstained"] += 1
                continue
            totals["routed"] += 1
            name = str(selected)
            row = skills.setdefault(name, {"routed": 0, "loaded": 0, "miss": 0, "fallback": 0})
            row["routed"] += 1
            if record.get("reason") == "focus_fallback":
                row["fallback"] += 1
                totals["fallback"] += 1
            elif record.get("loaded"):
                row["loaded"] += 1
                totals["loaded"] += 1
            else:
                row["miss"] += 1
                totals["miss"] += 1
        effective = totals["routed"] - totals["fallback"]
        totals["hit_rate"] = round(totals["loaded"] / effective, 4) if effective else None
        totals["routed_rate"] = (
            round(totals["routed"] / totals["turns"], 4) if totals["turns"] else None
        )
        skill_rows: dict[str, dict[str, Any]] = {}
        for name in sorted(skills):
            row = dict(skills[name])
            rows_effective = row["routed"] - row["fallback"]
            row["hit_rate"] = round(row["loaded"] / rows_effective, 4) if rows_effective else None
            skill_rows[name] = row
        return {
            "schema": "skill-proof.stats.v1",
            "path": path,
            "records": len(records),
            "corrupt_lines": corrupt,
            "totals": totals,
            "skills": skill_rows,
            "overrides": dict(sorted(overrides.items(), key=lambda item: (-item[1], item[0]))),
        }

    @staticmethod
    def _format_stats(payload: Mapping[str, Any]) -> str:
        totals = payload.get("totals") if isinstance(payload.get("totals"), Mapping) else {}

        def percent(value: Any) -> str:
            return "n/a" if value is None else f"{float(value) * 100:.1f}%"

        lines = [
            f"Skill Proof stats — {payload.get('records')} records from {payload.get('path')}",
            f"turns      {totals.get('turns', 0)}",
            (
                f"routed     {totals.get('routed', 0)} ({percent(totals.get('routed_rate'))})  "
                f"loaded {totals.get('loaded', 0)}  miss {totals.get('miss', 0)}  "
                f"fallback {totals.get('fallback', 0)}"
            ),
            f"abstained  {totals.get('abstained', 0)}",
            f"overridden {totals.get('overridden', 0)}  (host loaded something we did not select)",
            f"vetoed     {totals.get('vetoed', 0)}",
            f"hit_rate   {percent(totals.get('hit_rate'))}  = loaded / (routed - fallback)",
        ]
        skills = payload.get("skills") if isinstance(payload.get("skills"), Mapping) else {}
        if skills:
            lines += ["", f"{'skill':<34} {'routed':>6} {'loaded':>6} {'miss':>5} {'fall':>5} {'hit':>7}"]
            for name, row in skills.items():
                lines.append(
                    f"{name[:34]:<34} {row.get('routed', 0):>6} {row.get('loaded', 0):>6} "
                    f"{row.get('miss', 0):>5} {row.get('fallback', 0):>5} "
                    f"{percent(row.get('hit_rate')):>7}"
                )
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), Mapping) else {}
        if overrides:
            lines += ["", "overrides (loaded instead of our selection):"]
            for name, count in overrides.items():
                lines.append(f"  {name} x{count}")
        if payload.get("corrupt_lines"):
            lines.append(f"\nskipped {payload['corrupt_lines']} unreadable audit line(s)")
        return "\n".join(lines)

    def _audit_tail(self, limit: int = 5) -> list[dict[str, Any]]:
        """Most recent audit records (read-only, for health/reporting)."""
        if not self.audit_enabled or not self.audit_path.is_file():
            return []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def on_session_end(self, session_id: str = "", **_: Any) -> None:
        current = self.engine.receipt(session_id=str(session_id or ""))
        turn_id = str(current.get("turn_id") or "")
        if turn_id:
            self._persist(self.engine.finish_turn(turn_id=turn_id))
        # Skill history and focus belong to this session only; drop them at once
        # instead of carrying them across sessions.
        self.engine.forget_session(str(session_id or ""))

    def handle_command(self, raw_args: str = "") -> str:
        parts = str(raw_args or "").strip().split(None, 1)
        command = parts[0].casefold() if parts else "status"
        argument = parts[1].strip() if len(parts) > 1 else ""
        if command in {"help", "-h", "--help"}:
            return _HELP
        if command == "why":
            if not argument:
                return "Usage: /skill-proof why <skill name>\n\n" + _HELP
            report = self.engine.explain(
                argument, turn_id=self.engine.latest_turn_id
            )
            if not report.get("available"):
                return (
                    "No live Skill Proof turn is available in this process, so the ranking "
                    "cannot be explained. Use /skill-proof trace for the persisted receipt."
                )
            return self._format_why(report, argument)
        if command == "overlap":
            tokens = str(argument).split()
            min_similarity = 0.4
            limit = 10
            try:
                for index, token in enumerate(tokens):
                    if token == "--min-similarity" and index + 1 < len(tokens):
                        min_similarity = float(tokens[index + 1])
                    elif token == "--limit" and index + 1 < len(tokens):
                        limit = int(tokens[index + 1])
                    elif token == "--json":
                        pass
            except ValueError:
                return "Usage: /skill-proof overlap [--min-similarity 0.4] [--limit 10] [--json]"
            report = self._overlap_report(min_similarity, limit)
            if isinstance(report, str):
                return report
            if "--json" in tokens:
                return json.dumps(report, ensure_ascii=False, indent=2)
            return self._format_overlap(report)
        if command == "stats":
            payload = self.stats()
            if payload.get("error"):
                return str(payload["error"])
            if "--json" in str(argument):
                return json.dumps(payload, ensure_ascii=False, indent=2)
            return self._format_stats(payload)
        if command == "health":
            receipt = self._latest_receipt()
            with self._health_lock:
                activity = {name: dict(info) for name, info in self._hook_activity.items()}
            report = {
                'version': CORE_VERSION, 'mode': self.engine.mode,
                'turn_status': 'observed' if receipt else 'No turn observed',
                'receipt_origin': 'current_process' if self.engine.latest_turn_id else 'persisted' if receipt else 'none',
                'hooks': activity,
                'catalog': receipt.get('catalog', {}),
                'performance': receipt.get('performance', {}),
                'errors': receipt.get('errors', []),
                'hub': self.engine.hub_status(),
                'root_suggestions': self.engine.root_suggestions(),
                'session': (receipt.get('evidence') or {}).get('session', {}),
                'audit': {
                    'enabled': self.audit_enabled,
                    'path': str(self.audit_path),
                    'limit': self.audit_limit,
                    'broken': self._audit_broken,
                    'recent': self._audit_tail(3),
                },
                'note': 'Hook counts mean invocation, not success. Timing is plugin pre-LLM work, not model latency.',
            }
            return json.dumps(report, ensure_ascii=False, indent=2)
        if command == "refresh":
            self.engine.refresh_catalog()
            return "Skill cache cleared; content will be reread on the next turn. Existing receipts are unchanged."
        if command not in {"status", "explain", "trace"}:
            return f"Unknown subcommand: {command}\n\n{_HELP}"
        receipt = self._latest_receipt()
        if not receipt:
            return "No Skill Proof receipt is available yet."
        if command == "status":
            return self._receipt_summary(receipt)
        if command == "trace":
            return json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)

        decision = receipt.get("decision") if isinstance(receipt.get("decision"), Mapping) else {}
        lines = [
            f"Decision: {decision.get('status', 'unknown')} ({decision.get('reason', 'unknown')})"
        ]
        selected = receipt.get("selected")
        if isinstance(selected, Mapping):
            lines.append(
                f"Selected: {selected.get('name')} score={selected.get('score')} "
                f"reasons={','.join(selected.get('reasons') or [])}"
            )
        candidates = receipt.get("candidates")
        if isinstance(candidates, list) and candidates:
            lines.append("Candidates:")
            for candidate in candidates:
                if isinstance(candidate, Mapping):
                    lines.append(
                        f"- {candidate.get('name')} score={candidate.get('score')} "
                        f"reasons={','.join(candidate.get('reasons') or [])}"
                    )
        if receipt.get("compliance") != "unassessed" or receipt.get("compliance_reasons"):
            reasons_str = f" ({','.join(receipt.get('compliance_reasons') or [])})" if receipt.get('compliance_reasons') else ""
            lines.append(f"Compliance: {receipt.get('compliance', 'unassessed')}{reasons_str}")
        evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), Mapping) else {}
        session = evidence.get("session") if isinstance(evidence.get("session"), Mapping) else {}
        if session.get("reference"):
            reference = session.get("reference")
            if isinstance(reference, Mapping):
                lines.append(
                    f"Dialogue reference: {reference.get('kind')} matched {reference.get('phrase')!r}"
                )
        if session.get("focus"):
            lines.append(
                f"Focus: {session.get('focus')} ({session.get('focus_remaining')} turns left)"
            )
        if session.get("history"):
            lines.append(f"Session history: {', '.join(session.get('history'))}")
        lines.append(self._receipt_summary(receipt))
        return "\n".join(lines)

    def _observed_hook(self, name, callback):
        @wraps(callback)
        def observed(*args, **kwargs):
            with self._health_lock:
                previous = self._hook_activity.get(name, {})
                self._hook_activity[name] = {
                    'calls': previous.get('calls', 0) + 1,
                    'last_seen_utc': datetime.now(timezone.utc).isoformat(),
                }
            return callback(*args, **kwargs)
        return observed

    def register(self) -> None:
        for name, callback in (
            ('pre_llm_call', self.on_pre_llm_call), ('pre_tool_call', self.on_pre_tool_call),
            ('post_tool_call', self.on_post_tool_call), ('transform_llm_output', self.on_transform_llm_output),
            ('post_llm_call', self.on_post_llm_call), ('on_skill_lifecycle', self.on_skill_lifecycle),
            ('on_session_end', self.on_session_end),
        ):
            self.ctx.register_hook(name, self._observed_hook(name, callback))
        self.ctx.register_command(
            "skill-proof",
            handler=self.handle_command,
            description="Inspect skill selection, load evidence, and proof limits.",
            args_hint="status|explain|why <name>|stats [--json]|overlap [--json]|trace|refresh|health",
            argument_mode="options",
        )


def register(ctx: Any) -> SkillProofPlugin:
    """Hermes plugin entrypoint."""
    plugin = SkillProofPlugin(ctx)
    plugin.register()
    return plugin
