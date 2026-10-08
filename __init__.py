"""Skill Proof Hermes plugin adapter.

The adapter uses only the public ``PluginContext`` surface.  Selection and
evidence logic live in :mod:`core`, which can be tested without importing
Hermes Agent internals.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import time
import threading
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Mapping, Optional

try:  # Hermes loads directory plugins as packages; direct unit tests do not.
    from .core import SkillProofEngine, __version__ as CORE_VERSION, normalize_identifier
except ImportError:  # pragma: no cover - exercised by direct adapter tests
    from core import SkillProofEngine, __version__ as CORE_VERSION, normalize_identifier


logger = logging.getLogger(__name__)

_HELP = """\
/skill-proof — inspect auditable skill-selection evidence

Usage:
  /skill-proof status    Compact state for the latest turn
  /skill-proof explain   Selection decision and ranked candidates
  /skill-proof trace     Full bounded JSON receipt
  /skill-proof refresh   Reread local skill content on the next turn
  /skill-proof health    Hook activity, catalog diagnostics, and timing

Loaded means Hermes emitted a successful skill lifecycle event. It does not
mean the model followed the skill, and it does not verify the task result.
"""


def _positive_int(value: Any, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if 1 <= parsed <= maximum else default


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
    configured = os.environ.get("HERMES_HOME", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if os.name == "nt" and local_app_data:
        return pathlib.Path(local_app_data) / "hermes"
    return pathlib.Path.home() / ".hermes"


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
        )
        self.visible_receipt = _bool_setting(ctx.get_config("visible_receipt", True), True)
        self.receipt_style = ctx.get_config("receipt_style", "compact")
        self.receipt_history_limit = _positive_int(
            ctx.get_config("receipt_history_limit", 20), 20, 100
        )
        self._policy_errors: set[tuple[str, str, str]] = set()
        self._health_lock = threading.RLock()
        self._hook_activity: dict = {}

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
        if problems:
            detail += " | " + ", ".join(dict.fromkeys(problems)) + "; see /skill-proof explain"
        return f"{response_text}{marker} {detail}]"

    @staticmethod
    def _receipt_summary(receipt: Mapping[str, Any]) -> str:
        selected = receipt.get("selected")
        selected_name = selected.get("name") if isinstance(selected, Mapping) else "none"
        evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), Mapping) else {}
        runtime = receipt.get("runtime") if isinstance(receipt.get("runtime"), Mapping) else {}
        return (
            f"Skill Proof: selected={selected_name} "
            f"loaded={'yes' if evidence.get('hermes_loaded_event') else 'no'} "
            f"active={'yes' if runtime.get('active') else 'no'} "
            f"compliance={receipt.get('compliance', 'unassessed')} "
            f"verification={receipt.get('verification', 'unverified')}"
        )

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
            self._persist(self.engine.finish_turn(turn_id=effective_turn_id))

    def on_session_end(self, session_id: str = "", **_: Any) -> None:
        current = self.engine.receipt(session_id=str(session_id or ""))
        turn_id = str(current.get("turn_id") or "")
        if turn_id:
            self._persist(self.engine.finish_turn(turn_id=turn_id))

    def handle_command(self, raw_args: str = "") -> str:
        command = str(raw_args or "").strip().casefold() or "status"
        if command in {"help", "-h", "--help"}:
            return _HELP
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
            args_hint="status|explain|trace|refresh|health",
            argument_mode="options",
        )


def register(ctx: Any) -> SkillProofPlugin:
    """Hermes plugin entrypoint."""
    plugin = SkillProofPlugin(ctx)
    plugin.register()
    return plugin
