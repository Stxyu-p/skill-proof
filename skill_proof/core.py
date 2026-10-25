"""Hermes-independent core for Skill Proof.

Discovery treats skill documents as untrusted data.  This module deliberately
implements a small, documented YAML-like frontmatter subset instead of claiming
to parse arbitrary YAML without a dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import sqlite3
import stat
import struct
import threading
import time
import unicodedata
import urllib.request
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import lru_cache
from math import log
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

try:
    from . import models
except ImportError:
    try:
        import models
    except ImportError:
        models = None




from _catalog import (  # v0.16 facade re-exports
    Catalog,
    Diagnostic,
    SkillInvariants,
    SkillRecord,
    _DISABLED_SKILLS_CACHE,
    _EXCLUDED_DIRS,
    _FrontmatterError,
    _HUB_BUNDLE_FILE_MAX_BYTES,
    _HUB_BUNDLE_TOTAL_MAX_BYTES,
    _HUB_LOCK_MAX_BYTES,
    _NAME_RE,
    _SEA_RANGE_COUNT,
    _SPACELESS_RANGES,
    _apply_cross_root_precedence,
    _bundle_sha256,
    _contains_spaceless_script,
    _identifier_in_query,
    _identifier_pattern,
    _is_ignored_dirname,
    _is_reparse_point,
    _is_sea_spaceless_character,
    _is_spaceless_character,
    _is_within,
    _parse_inline_tags,
    _parse_list_value,
    _parse_scalar,
    _parse_skill,
    _phrase_boundary_pattern,
    _phrase_present,
    _tree_fingerprint,
    default_hermes_home,
    detect_agent_roots,
    extract_explicit_skill_names,
    load_disabled_skills,
    normalize_identifier,
    normalize_identifiers,
    scan_catalog,
    suggest_roots,
)


from _vector import (  # v0.16 facade re-exports
    VectorIndex,
    vector_index_from_env,
)


from _dialogue import (  # v0.16 facade re-exports
    DialogueReference,
    _DIALOGUE_FAMILIES,
    _DIALOGUE_FILLER,
    _DIALOGUE_FOCUS_PHRASES,
    _DIALOGUE_PREVIOUS_PHRASES,
    _DIALOGUE_RELEASE_PHRASES,
    _DIALOGUE_REPEAT_PHRASES,
    _ENGLISH_NEGATION_WORDS,
    _IDENTIFIER_TOKEN,
    _NEGATION_FILLERS,
    _NEGATION_SUBSTRINGS,
    _NEGATION_WORDS,
    _SOV_NEGATION_WORDS,
    _SessionMemory,
    _named_skill_in_query,
    _negation_patterns,
    _query_might_contain_negation,
    _release_remainder,
    _skill_is_negated,
    _skill_named_after,
    parse_dialogue_reference,
)


from _ranking import (  # v0.16 facade re-exports
    CatalogIndex,
    DEFAULT_SYNONYMS,
    RankRow,
    RankedCandidate,
    Selection,
    _COMPANION_BONUS,
    _FLEET_AGENT_PATTERNS,
    _NGRAM_MINIMUM_DICE,
    _NGRAM_SIZE,
    _NGRAM_WEIGHT,
    _RANK_TABLE_LIMIT,
    _STOPWORDS,
    _VECTOR_BYPASS_MARGIN,
    _VECTOR_BYPASS_SCORE,
    _add_morph_bases,
    _build_catalog_index,
    _candidate_indices,
    _catalog_document_frequency,
    _catalog_duplicate_names,
    _char_ngrams,
    _load_fleet_manifest,
    _ngram_dice,
    _normalize_morph,
    _overlap_recommendation,
    _rank,
    _rank_table,
    _resolve_companions,
    _skill_spaceless_ngrams,
    _skill_tokens_cache,
    _tokens,
    overlap_report,
    select_skill,
    suggest_fleet_agent,
)


from _compliance import (  # v0.16 facade re-exports
    FilePolicyStore,
    GuardResult,
    InMemoryPolicyStore,
    PolicyStoreProtocol,
    calibrate_audit,
    default_policy_store,
    format_calibrate,
    read_audit_records,
)


__version__ = "1.3.2"


@dataclass(frozen=True)
class TurnStart:
    selection: Selection
    context: str
    errors: tuple[str, ...]


@dataclass
class _TurnState:
    turn_id: str
    session_id: str
    task_id: str
    mode: str
    query_sha256: str
    catalog: Catalog
    selection: Selection
    context: str
    errors: list[str] = field(default_factory=list)
    host_loaded_event: bool = False
    loaded_provenance: Optional[str] = None
    loaded_use_count: Optional[int] = None
    loaded_reused: Optional[bool] = None
    loaded_reuse_after_patch: Optional[bool] = None
    source_sha256: Optional[str] = None
    source_bytes: Optional[int] = None
    source_error: Optional[str] = None
    tool_result_sha256_pre_transform: Optional[str] = None
    tool_result_bytes_pre_transform: Optional[int] = None
    active: bool = False
    active_evidence: Optional[str] = None
    observed_tools: list[str] = field(default_factory=list)
    complete: bool = False
    availability: str = "local_only"
    local_skill_count: int = 0
    host_name_count: Optional[int] = None
    host_unindexed: tuple[str, ...] = ()
    hub: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)
    vetoed: tuple[str, ...] = ()
    # A skill the host loaded that we did not select (or had nothing selected):
    # the override signal telemetry needs. It never sets host_loaded_event.
    override_loaded: str = ""
    # Normalized names of the *unfiltered* local catalog, kept so "why" can
    # tell "not indexed" apart from "excluded by the host listing".
    indexed_names: frozenset = frozenset()
    performance: dict = field(default_factory=dict)
    vector_trace: dict = field(default_factory=dict)
    source_path_match: str = "unknown"
    compliance: str = "unassessed"
    compliance_reasons: list[str] = field(default_factory=list)


def _compact_context(selection: Selection, mode: str, availability_error: bool) -> str:
    if availability_error:
        return "Skill list unavailable; availability is unknown."
    if selection.status == "selected" and selection.selected is not None:
        name = selection.selected.skill.name
        mapping = {
            "dialogue_reference": f'Previously used: "{name}"; load via skill_view.',
            "dialogue_reference_previous": f'Previously used: "{name}"; load via skill_view.',
            "focus_requested": f'Focus: "{name}"; use skill_view first.',
            "focus_fallback": f'Focus candidate: "{name}"; load via skill_view if relevant.',
        }
        if mode == "observe":
            return f'Candidate: "{name}"; use skill_view if needed.'
        return mapping.get(selection.reason, f'Use skill_view for "{name}" first.')
    if selection.status == "ambiguous" and selection.candidates:
        c_names = ", ".join(f'"{c.skill.name}"' for c in selection.candidates[:3])
        return f"Relevant skills: {c_names}; use skill_view to load."
    static_reasons = {
        "ambiguous": "Skill match ambiguous; ask which to load.",
        "no_previous_selection": "No prior skill; ask which one to load.",
        "dialogue_reference_unavailable": "Previously used skill unavailable; do not claim loaded.",
        "dialogue_reference_unknown": "Unknown skill; ask the user which one.",
    }
    if selection.status == "blocked" and selection.explicit:
        return "Requested skill unavailable; do not claim it loaded."
    return static_reasons.get(selection.status) or static_reasons.get(selection.reason, "")


class SkillProofEngine:
    """Thread-safe turn state and evidence ledger, independent of Hermes internals."""

    _MODES = frozenset({"observe", "nudge", "enforce-tools"})
    _MODE_ALIASES = {"enforce": "enforce-tools"}
    _SESSION_LIMIT = 64
    _STACK_LIMIT = 10
    _FILTERED_VIEW_LIMIT = 64
    # Focus is a bounded, decaying boost that only applies to a skill that
    # already has lexical signal: it decides close calls, never unrelated turns.
    _FOCUS_BONUS = 0.20
    _FOCUS_BONUS_STEP = 0.04
    _FOCUS_BONUS_FLOOR = 0.04
    _SKILL_TOOLS = frozenset({"skill_view", "skills_list", "skill_search", "skill_manage"})

    def __init__(
        self,
        roots: Mapping[str, os.PathLike[str] | str],
        *,
        mode: str = "nudge",
        min_score: float = 0.28,
        min_margin: float = 0.05,
        max_candidates: int = 3,
        max_skill_bytes: int = 256 * 1024,
        context_budget_bytes: int = 1200,
        observed_tool_limit: int = 16,
        retained_turn_limit: int = 100,
        invariants: Optional[Mapping[str, Mapping[str, Any]]] = None,
        hub_lock_path: Optional[str | os.PathLike[str]] = None,
        synonyms: Optional[Mapping[str, Any]] = None,
        session_memory: bool = True,
        focus_turns: int = 5,
        disabled: Optional[Iterable[str]] = None,
        vector_index: Optional[VectorIndex] = None,
        clock: Optional[Callable[[], float]] = None,
        policy_store: Optional[Any] = None,
        block_modified_hub_skills: bool = False,
        catalog_cache_ttl_ms: float = 300000.0,
        agent_routing: Optional[Mapping[str, Any]] = None,
    ) -> None:
        normalized_mode = normalize_identifier(mode)
        normalized_mode = self._MODE_ALIASES.get(normalized_mode, normalized_mode)
        if normalized_mode not in self._MODES:
            raise ValueError("mode must be observe, nudge, or enforce-tools")
        if not 0 <= float(min_score) <= 1:
            raise ValueError("min_score must be between 0 and 1")
        if not 0 <= float(min_margin) <= 1:
            raise ValueError("min_margin must be between 0 and 1")
        for value, label in (
            (max_candidates, "max_candidates"),
            (max_skill_bytes, "max_skill_bytes"),
            (context_budget_bytes, "context_budget_bytes"),
            (observed_tool_limit, "observed_tool_limit"),
            (retained_turn_limit, "retained_turn_limit"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{label} must be a positive integer")

        self._roots = {str(key): pathlib.Path(value) for key, value in roots.items()}
        self.mode = normalized_mode
        self.min_score = float(min_score)
        self.min_margin = float(min_margin)
        self.max_candidates = max_candidates
        self.max_skill_bytes = max_skill_bytes
        self.context_budget_bytes = context_budget_bytes
        self.observed_tool_limit = observed_tool_limit
        self.retained_turn_limit = retained_turn_limit
        self.invariants: dict[str, SkillInvariants] = {}
        if isinstance(invariants, Mapping):
            for sname, srules in invariants.items():
                if isinstance(srules, Mapping):
                    sname_norm = normalize_identifier(sname)
                    req = tuple(normalize_identifier(x) for x in srules.get("required_tools", ()) if normalize_identifier(x))
                    forb = tuple(normalize_identifier(x) for x in srules.get("forbidden_tools", ()) if normalize_identifier(x))
                    order = tuple(normalize_identifier(x) for x in srules.get("ordered_tools", ()) if normalize_identifier(x))
                    self.invariants[sname_norm] = SkillInvariants(
                        required_tools=req,
                        forbidden_tools=forb,
                        ordered_tools=order,
                    )
        self.hub_lock_path: Optional[pathlib.Path] = (
            pathlib.Path(hub_lock_path).expanduser() if hub_lock_path else None
        )
        self._hub_cache: Optional[dict[str, Mapping[str, Any]]] = None
        self._hub_cache_mtime: Optional[int] = None
        self.synonyms: dict[str, tuple[str, ...]] = dict(DEFAULT_SYNONYMS)
        if isinstance(synonyms, Mapping):
            for sname, terms in synonyms.items():
                if isinstance(terms, str):
                    terms = (terms,)
                if not isinstance(terms, (list, tuple)):
                    continue
                normalized_terms = tuple(
                    dict.fromkeys(str(term).strip() for term in terms if str(term).strip())
                )[:32]
                if normalized_terms:
                    self.synonyms[normalize_identifier(sname)] = normalized_terms
        self.disabled: Optional[frozenset[str]] = (
            normalize_identifiers(disabled) if disabled is not None else None
        )
        if not isinstance(session_memory, bool):
            raise ValueError("session_memory must be a boolean")
        if isinstance(focus_turns, bool) or not isinstance(focus_turns, int) or not 0 <= focus_turns <= 50:
            raise ValueError("focus_turns must be an integer between 0 and 50")
        self.session_memory = session_memory
        self.focus_turns = focus_turns
        self._sessions: dict[str, _SessionMemory] = {}
        self._catalog_cache: dict = {}
        self._proof_cache: dict[str, tuple[tuple[int, int, int], tuple[Optional[str], Optional[int], Optional[str]]]] = {}
        self._filtered_view_cache: dict[tuple[str, frozenset[str]], tuple[Catalog, tuple[str, ...]]] = {}
        self._turns: dict[str, _TurnState] = {}
        self._latest_by_task: dict[tuple[str, str], str] = {}
        self._latest_by_session: dict[str, str] = {}
        self._latest_turn_id: Optional[str] = None
        self._lock = threading.RLock()
        self.vector_index = vector_index
        self._clock: Callable[[], float] = clock or time.perf_counter
        self._policy_store = policy_store if policy_store is not None else default_policy_store()
        if not isinstance(block_modified_hub_skills, bool):
            raise ValueError("block_modified_hub_skills must be a boolean")
        self.block_modified_hub_skills = block_modified_hub_skills
        if catalog_cache_ttl_ms is None or float(catalog_cache_ttl_ms) < 0:
            raise ValueError("catalog_cache_ttl_ms must be >= 0")
        self.catalog_cache_ttl_ms = float(catalog_cache_ttl_ms)
        self._catalog_scan_at = float("-inf")
        # Plan Part 1.2 item 4: fleet-agent routing is data; a custom mapping
        # replaces the built-in patterns entirely (None keeps the defaults).
        self.agent_routing: Optional[dict[str, list[str]]] = None
        if isinstance(agent_routing, Mapping):
            self.agent_routing = {
                str(name).strip(): [str(term) for term in terms]
                for name, terms in agent_routing.items()
                if str(name or "").strip() and isinstance(terms, (list, tuple, set))
            }

    @staticmethod
    def _text_id(value: object) -> str:
        return str(value or "").strip()

    def _state(
        self,
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Optional[_TurnState]:
        if turn_id:
            state = self._turns.get(self._text_id(turn_id))
            if state is not None:
                if session_id and state.session_id != self._text_id(session_id):
                    return None
                if task_id and state.task_id != self._text_id(task_id):
                    return None
                return state
            return None
        session = self._text_id(session_id)
        task = self._text_id(task_id)
        if session or task:
            resolved_turn = self._latest_by_task.get((session, task))
            if resolved_turn:
                return self._turns.get(resolved_turn)
            if task:
                return None
        if session:
            resolved_turn = self._latest_by_session.get(session)
            if resolved_turn:
                return self._turns.get(resolved_turn)
        return None

    def _focus_fallback(
        self,
        selection: Selection,
        catalog: Catalog,
        focus: Optional[str],
        query: str,
    ) -> Selection:
        """Use the focused skill only when nothing else matched.

        "keep using X" is an explicit instruction, so an unrelated turn that has
        no competitor falls back to X instead of silently dropping it. A veto,
        an ambiguity, or a competing match always wins: focus never decides a
        close call, and it never survives its own release.
        """
        if not focus or selection.status != "no_match" or selection.reason != "below_threshold":
            return selection
        matches = catalog.by_name(focus)
        if len(matches) != 1 or _skill_is_negated(matches[0].name, query):
            return selection
        candidate = RankedCandidate(matches[0], 0.0, ("dialogue_focus_fallback",))
        return Selection(
            "selected",
            "focus_fallback",
            candidate,
            (candidate,),
            False,
            False,
            selection.rank_table,
        )

    def explain(
        self,
        skill_name: str = "",
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Answer "why (not) this skill?" from stored derived numbers.

        The prompt is never read or returned: the report is built from the rank
        table, the veto list, and the thresholds recorded for this turn.
        """
        wanted = normalize_identifier(skill_name)
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return {"available": False, "reason": "no_turn_observed"}
            selection = state.selection
            report: dict[str, Any] = {
                "available": True,
                "turn_id": state.turn_id,
                "decision": {
                    "status": selection.status,
                    "reason": selection.reason,
                    "explicit": selection.explicit,
                    "required": selection.required,
                },
                "thresholds": {
                    "min_score": self.min_score,
                    "min_margin": self.min_margin,
                    "max_candidates": self.max_candidates,
                },
                "selected": (
                    None
                    if selection.selected is None
                    else {
                        "name": selection.selected.skill.name,
                        "score": selection.selected.score,
                        "reasons": list(selection.selected.reasons),
                    }
                ),
                "candidates": [
                    {"name": item.skill.name, "score": item.score, "reasons": list(item.reasons)}
                    for item in selection.candidates
                ],
                "ranked_count": len(selection.rank_table),
                "rank_table": [
                    {"name": row.name, "score": row.score, "reasons": list(row.reasons)}
                    for row in selection.rank_table
                ],
                "vetoed": list(state.vetoed),
                "session": dict(state.session),
                "catalog_size": len(state.catalog.skills),
                "query_sha256": state.query_sha256,
            }
            if wanted:
                report["skill"] = self._explain_skill(state, wanted)
            return report

    def _explain_skill(self, state: _TurnState, wanted: str) -> dict[str, Any]:
        selection = state.selection
        base: dict[str, Any] = {
            "in_catalog": bool(state.catalog.by_name(wanted)),
            "threshold": self.min_score,
        }
        if wanted in state.indexed_names:
            base["indexed"] = True
        elif state.indexed_names:
            base["indexed"] = False
        if selection.selected is not None and selection.selected.skill.normalized_name == wanted:
            base.update(
                verdict="selected",
                name=selection.selected.skill.name,
                score=selection.selected.score,
                reasons=list(selection.selected.reasons),
                threshold_met=True,
            )
            return base
        if wanted in normalize_identifiers(state.vetoed):
            base.update(
                verdict="vetoed",
                name=None,
                score=None,
                reasons=["negated_in_query"],
                threshold_met=None,
                note=(
                    "The turn vetoed this skill, so it never entered the ranking. "
                    "Skill Proof does not store prompts, so the wording is not available here."
                ),
            )
            return base
        # Only a turn that skipped lexical ranking has no score to give; a turn
        # that ranked but scored nothing falls through to no_signal.
        if not selection.rank_table and (
            selection.explicit or selection.reason == "hermes_catalog_unavailable"
        ):
            base.update(
                verdict="not_ranked",
                name=None,
                score=None,
                reasons=[selection.reason],
                threshold_met=None,
                note=(
                    "This turn was decided without lexical ranking "
                    f"({selection.reason}), so no score exists for this skill."
                ),
            )
            return base
        for index, row in enumerate(selection.rank_table):
            if normalize_identifier(row.name) == wanted:
                base.update(
                    verdict="below_threshold" if row.score < self.min_score else "ranked",
                    name=row.name,
                    score=row.score,
                    rank=index + 1,
                    reasons=list(row.reasons),
                    threshold_met=row.score >= self.min_score,
                    gap_to_top=round(max(0.0, selection.rank_table[0].score - row.score), 6),
                )
                return base
        if not base["in_catalog"] and base.get("indexed"):
            base.update(
                verdict="not_listed_by_host",
                name=None,
                score=None,
                reasons=[],
                threshold_met=False,
                note=(
                    "A SKILL.md for this name exists under the configured roots, but the host's "
                    "skill listing for this turn did not include it, so it was excluded from "
                    "ranking. Not indexed is a different failure."
                ),
            )
            return base
        if not base["in_catalog"]:
            base.update(
                verdict="unknown_skill",
                name=None,
                score=None,
                reasons=[],
                threshold_met=False,
                note="No indexed SKILL.md under the configured roots has this name.",
            )
            return base
        capped = len(selection.rank_table) >= _RANK_TABLE_LIMIT
        last = selection.rank_table[-1].score if selection.rank_table else 0.0
        base.update(
            verdict="ranked_below_cap" if capped else "no_signal",
            name=None,
            score=round(last, 6) if capped else 0.0,
            score_is_ceiling=capped,
            reasons=[],
            threshold_met=False,
            note=(
                f"Scored at or below {last} this turn."
                if capped
                else "No term in this turn overlapped this skill's name, description, tags, "
                "aliases, or spaceless-script characters."
            ),
        )
        return base

    @staticmethod
    def _session_evidence(memory: _SessionMemory, dialogue: Mapping[str, Any]) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        if memory.focus:
            evidence["focus"] = memory.focus
            evidence["focus_remaining"] = memory.focus_remaining
        if memory.stack:
            evidence["history"] = list(memory.stack[-5:])
        if dialogue:
            evidence["reference"] = dict(dialogue)
        return evidence

    def _session(self, session_id: str) -> Optional[_SessionMemory]:
        """Session-scoped memory, bounded to the most recent sessions."""
        if not self.session_memory or not session_id:
            return None
        memory = self._sessions.get(session_id)
        if memory is None:
            memory = _SessionMemory()
            self._sessions[session_id] = memory
            while len(self._sessions) > self._SESSION_LIMIT:
                self._sessions.pop(next(iter(self._sessions)))
        return memory

    def _touch_stack(self, memory: _SessionMemory, name: str) -> None:
        normalized = normalize_identifier(name)
        if not normalized:
            return
        if memory.stack and memory.stack[-1] == normalized:
            return
        memory.stack.append(normalized)
        del memory.stack[: max(0, len(memory.stack) - self._STACK_LIMIT)]

    def _focus_bonus(self, memory: _SessionMemory) -> float:
        """Focus decays each turn so it nudges the ranking without becoming sticky forever."""
        if not memory.focus:
            return 0.0
        if self.focus_turns <= 0:
            return self._FOCUS_BONUS
        used = max(0, self.focus_turns - memory.focus_remaining)
        bonus = self._FOCUS_BONUS - self._FOCUS_BONUS_STEP * used
        return round(max(self._FOCUS_BONUS_FLOOR, bonus), 4)

    def _companion_handoff(self, memory: Optional[_SessionMemory], catalog: Catalog) -> dict[str, float]:
        """Pilot bonus for ``related_skills`` of the last loaded skill (plan 3.6).

        Deterministic warm handoff: after turns that used skill A, the skills
        A declares as related get a small head start on the next turn, and
        only while they already carry lexical signal.
        """
        if memory is None or not memory.stack:
            return {}
        matches = catalog.by_name(memory.stack[-1])
        if len(matches) != 1 or not matches[0].related_skills:
            return {}
        bonus: dict[str, float] = {}
        for name in matches[0].related_skills:
            normalized = normalize_identifier(name)
            if normalized:
                bonus[normalized] = _COMPANION_BONUS
        return bonus

    def _dialogue_selection(
        self,
        memory: _SessionMemory,
        catalog: Catalog,
        reference: DialogueReference,
    ) -> Optional[Selection]:
        """Resolve a reference to session history; ``None`` means "fall through"."""
        if reference.kind == "focus":
            if reference.name:
                matches = catalog.by_name(reference.name)
                if not matches:
                    return Selection(
                        "no_match", "dialogue_reference_unknown", None, (), False, False
                    )
            elif memory.stack:
                matches = catalog.by_name(memory.stack[-1])
                if not matches:
                    return Selection(
                        "no_match", "dialogue_reference_unavailable", None, (), False, False
                    )
            else:
                return Selection("no_match", "no_previous_selection", None, (), False, False)
            if len(matches) > 1:
                return Selection("ambiguous", "ambiguous", None, (), False, False)
            if not matches:
                return Selection(
                    "no_match", "dialogue_reference_unavailable", None, (), False, False
                )
            memory.focus = matches[0].normalized_name
            memory.focus_remaining = self.focus_turns
            candidate = RankedCandidate(matches[0], 1.0, ("dialogue_focus_request",))
            return Selection("selected", "focus_requested", candidate, (candidate,), False, False)
        if reference.kind in ("repeat", "previous"):
            depth = 1 if reference.kind == "repeat" else 2
            if len(memory.stack) < depth:
                return Selection("no_match", "no_previous_selection", None, (), False, False)
            matches = catalog.by_name(memory.stack[-depth])
            if len(matches) != 1:
                return Selection("no_match", "dialogue_reference_unavailable", None, (), False, False)
            reason = "dialogue_reference" if depth == 1 else "dialogue_reference_previous"
            candidate = RankedCandidate(matches[0], 1.0, (reason,))
            return Selection("selected", reason, candidate, (candidate,), False, False)
        return None

    def forget_session(self, session_id: str) -> bool:
        """Drop session memory (skill history and focus) without touching audit turns."""
        clean = self._text_id(session_id)
        if not clean:
            return False
        with self._lock:
            return self._sessions.pop(clean, None) is not None

    def session_state(self, session_id: str) -> dict[str, Any]:
        """Read-only view of what this session is holding on to."""
        with self._lock:
            memory = self._sessions.get(self._text_id(session_id))
            if memory is None:
                return {"enabled": self.session_memory, "history": [], "focus": "", "focus_remaining": 0}
            return {
                "enabled": self.session_memory,
                "history": list(memory.stack),
                "focus": memory.focus or "",
                "focus_remaining": memory.focus_remaining if memory.focus else 0,
            }

    def _selection_context(self, selection: Selection) -> str:
        if selection.status == "selected" and selection.selected is not None:
            name = selection.selected.skill.name
            if selection.reason in ("dialogue_reference", "dialogue_reference_previous"):
                return (
                    f'Skill Proof selected "{name}" because the request refers to a skill already '
                    f'used in this session. Call skill_view with name="{name}". The selection did not '
                    "come from the current wording, so do not present it as a fresh match."
                )
            if selection.reason == "focus_requested":
                return (
                    f'Skill Proof selected "{name}" and kept it as the session focus for the next '
                    f'turns. Call skill_view with name="{name}" before operational tools.'
                )
            if selection.reason == "focus_fallback":
                return (
                    f'Skill Proof selected "{name}" only because the session focus is active and '
                    f'nothing else matched; its score is 0. If {name} does not fit this task, '
                    f'answer directly without it. Otherwise call skill_view with name="{name}".'
                )
            if self.mode == "observe":
                return (
                    f'Skill Proof candidate: "{name}". If you use it, call skill_view with '
                    f'name="{name}". A load event proves loading only, not compliance or verification.'
                )
            return f'Use skill_view for "{name}" first.'
        if selection.status == "ambiguous":
            if not selection.candidates:
                return (
                    "Skill Proof: the named skill is ambiguous in the current catalog. "
                    "Ask which skill to load; do not claim one was selected."
                )
            names = ", ".join(item.skill.name for item in selection.candidates[:3])
            return (
                f"Skill Proof: multiple skills matched ({names}). Ask which skill to load; "
                "do not claim one was selected."
            )
        if selection.status == "blocked" and selection.explicit:
            if selection.reason == "listed_but_unindexed":
                return (
                    "Skill Proof: Hermes lists the requested skill, but no local SKILL.md under "
                    "the configured roots could be indexed for it. Do not claim that it was loaded."
                )
            return (
                f"Skill Proof could not resolve the explicitly requested skill "
                f"({selection.reason}). Do not claim that it was loaded."
            )
        if selection.status == "no_match" and selection.reason == "no_previous_selection":
            return (
                "Skill Proof: the request refers to a previously used skill, but this session has no "
                "skill history yet. Ask which skill to load; do not claim one was selected."
            )
        if selection.status == "no_match" and selection.reason == "dialogue_reference_unavailable":
            return (
                "Skill Proof: the previously used skill in this session is no longer available in the "
                "current catalog. Do not claim that it was loaded."
            )
        if selection.status == "no_match" and selection.reason == "dialogue_reference_unknown":
            return (
                "Skill Proof: the request names a skill that is not in the current catalog. Ask the "
                "user which skill they mean; do not claim one was selected."
            )
        if selection.status == "no_match" and selection.reason == "focus_released":
            return (
                "Skill Proof: session focus cleared; no skill was selected for this instruction."
            )
        return ""

    def begin_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        task_id: str,
        query: str,
        available_names: Optional[set[str]] = None,
        availability_error: bool = False,
    ) -> TurnStart:
        core_started = self._clock()
        clean_turn_id = self._text_id(turn_id)
        if not clean_turn_id:
            raise ValueError("turn_id must not be empty")
        clean_session_id = self._text_id(session_id)
        clean_task_id = self._text_id(task_id)
        query_text = str(query or "")
        metrics = {}
        if self.vector_index is not None:
            # Duck-typed indexes must not need the optional trace surface.
            try:
                self.vector_index.reset_trace()
            except AttributeError:
                pass
        with self._lock:
            now = self._clock()
            mtime_sig = tuple(
                (k, v.stat().st_mtime_ns if v.is_dir() else -1)
                for k, v in sorted(self._roots.items())
            )
            mtime_changed = getattr(self, "_last_mtime_sig", None) != mtime_sig
            self._last_mtime_sig = mtime_sig
            fresh = (
                not mtime_changed
                and self.catalog_cache_ttl_ms > 0
                and "__catalog__" in self._catalog_cache
                and (now - self._catalog_scan_at) * 1000.0 < self.catalog_cache_ttl_ms
            )
            if fresh:
                # Plan 5.2.1: an unchanged tree within the TTL window is an O(1)
                # reuse. The scandir fingerprint itself costs ~30-40 ms per 300
                # skills, so it runs at most once per window; refresh_catalog and
                # skill lifecycle events reset the window deterministically.
                catalog = self._catalog_cache["__catalog__"]
                metrics.update(
                    cache_hits=len(self._catalog_cache.get("__live_keys__", ())),
                    cache_misses=0,
                    tree_cache_hit=True,
                    catalog_ttl_reuse=True,
                )
            else:
                catalog = scan_catalog(
                    self._roots,
                    max_skill_bytes=self.max_skill_bytes,
                    cache=self._catalog_cache,
                    metrics=metrics,
                    disabled=self.disabled,
                )
                self._catalog_scan_at = self._clock()
        metrics['scan_ms'] = round((self._clock() - core_started) * 1000, 3)
        local_count = len(catalog.skills)
        indexed_names = frozenset(skill.normalized_name for skill in catalog.skills)
        host_unindexed: tuple[str, ...] = ()
        selection_started = self._clock()
        if available_names is not None:
            names = normalize_identifiers(available_names)
            view_key = (catalog.catalog_hash, frozenset(available_names))
            cached_view = self._filtered_view_cache.get(view_key)
            if cached_view is not None:
                catalog, host_unindexed = cached_view
            else:
                local_indexed = {skill.normalized_name for skill in catalog.skills}
                host_unindexed = tuple(
                    sorted(str(name) for name in available_names if normalize_identifier(name) not in local_indexed)
                )
                eligible = tuple(skill for skill in catalog.skills if skill.normalized_name in names)
                filtered_hash = hashlib.sha256(json.dumps(
                    [(s.skill_id, s.source_sha256) for s in eligible], separators=(",", ":")
                ).encode()).hexdigest()
                exclusions = tuple(Diagnostic('not_in_host_list', skill.root_id, skill.relative_path)
                                   for skill in catalog.skills if skill.normalized_name not in names)
                catalog = Catalog(eligible, catalog.diagnostics + exclusions, filtered_hash)
                # ponytail: coarse flush bound; switch to an LRU if view sets churn
                if len(self._filtered_view_cache) >= self._FILTERED_VIEW_LIMIT:
                    self._filtered_view_cache.clear()
                self._filtered_view_cache[view_key] = (catalog, host_unindexed)
        known_names = tuple(skill.name for skill in catalog.skills)
        explicit_names = extract_explicit_skill_names(query_text, known_names=known_names)
        if self.vector_index is not None and not explicit_names:
            try:
                self.vector_index.sync_catalog(catalog.skills)
            except Exception:
                pass
        memory = self._session(clean_session_id)
        dialogue: dict[str, Any] = {}
        selection: Optional[Selection] = None
        if memory is not None and not explicit_names:
            if memory.focus and self.focus_turns > 0 and memory.focus_remaining <= 0:
                memory.focus = None
            reference = parse_dialogue_reference(query_text, known_names=known_names)
            if reference is not None:
                dialogue = {"kind": reference.kind, "phrase": reference.phrase}
                if reference.name:
                    dialogue["name"] = reference.name
                selection = self._dialogue_selection(memory, catalog, reference)
                if selection is None:
                    # A release has no skill of its own: clear the focus and let
                    # lexical ranking decide what (if anything) comes next, but
                    # never re-select the skill the user just released.
                    named = normalize_identifier(dialogue.get("name") or "")
                    # A name that is not in the catalog must not redirect the
                    # release: the current focus is what gets dropped.
                    released = (
                        named if named and len(catalog.by_name(named)) == 1
                        else normalize_identifier(memory.focus or "")
                    )
                    if released and normalize_identifier(memory.focus or "") == released:
                        memory.focus = None
                    memory.focus_remaining = 0
                    # Rank what the user asked for *after* the release clause,
                    # not the whole sentence that contains it.
                    remainder = _release_remainder(
                        query_text, reference, named or released
                    )
                    selection = select_skill(
                        catalog,
                        query_text if remainder is None else remainder,
                        explicit_names=explicit_names,
                        min_score=self.min_score,
                        min_margin=self.min_margin,
                        limit=self.max_candidates,
                        synonyms=self.synonyms,
                        exclude=(released,) if released else (),
                        agent_manifest=self.agent_routing,
                        vector_index=self.vector_index,
                    )
                    if selection.status == "no_match":
                        dialogue["released"] = released or named
                        selection = Selection(
                            "no_match",
                            "focus_released",
                            None,
                            selection.candidates,
                            False,
                            False,
                            selection.rank_table,
                        )
        if selection is None:
            focus = memory.focus if memory is not None else None
            if focus and len(catalog.by_name(focus)) != 1:
                memory.focus = None
                focus = None
            selection = select_skill(
                catalog,
                query_text,
                explicit_names=explicit_names,
                min_score=self.min_score,
                min_margin=self.min_margin,
                limit=self.max_candidates,
                synonyms=self.synonyms,
                preferred=focus,
                preferred_bonus=self._focus_bonus(memory) if (memory and focus) else 0.0,
                companion_bonus=self._companion_handoff(memory, catalog),
                agent_manifest=self.agent_routing,
                vector_index=self.vector_index,
            )
            selection = self._focus_fallback(selection, catalog, focus, query_text)
        vector_trace = (
            self.vector_index.trace_snapshot()
            if self.vector_index is not None and hasattr(self.vector_index, "trace_snapshot")
            else {
                "provider_configured": "off",
                "provider_used": "off",
                "fallback_reason": "provider_off",
                "embedding_latency_ms": 0.0,
                "query_cache_hit": False,
                "vector_used": False,
            }
        )
        if not isinstance(vector_trace, Mapping):
            # Duck-typed / test indexes may not expose a trace; stay fail-open.
            vector_trace = {}
        metrics.update({
            "embedding_latency_ms": vector_trace.get("embedding_latency_ms", 0.0),
            "query_cache_hit": vector_trace.get("query_cache_hit", False),
            "vector_used": vector_trace.get("vector_used", False),
        })
        session_evidence: dict[str, Any] = {}
        if memory is not None:
            if selection.selected is not None:
                self._touch_stack(memory, selection.selected.skill.normalized_name)
            session_evidence = self._session_evidence(memory, dialogue)
        if availability_error:
            selection = Selection("blocked", "hermes_catalog_unavailable", None, (), bool(explicit_names), True)
        elif (
            selection.status == "blocked"
            and selection.reason == "unknown_explicit_skill"
            and host_unindexed
            and any(normalize_identifier(name) in names for name in explicit_names)
        ):
            # Hermes lists the requested skill, but no local SKILL.md under the
            # configured roots could be indexed for it: say that instead of
            # claiming the skill is unknown.
            selection = Selection("blocked", "listed_but_unindexed", None, (), True, True)
        if self.block_modified_hub_skills and selection.selected is not None:
            hub = self._hub_provenance(selection.selected.skill)
            if hub.get("bundle") == "modified":
                # Plan 3.7: optionally refuse hub skills whose local bytes no
                # longer match the recorded bundle when the option is enabled.
                selection = replace(selection, status="blocked", reason="hub_modified", selected=None)
        metrics['selection_ms'] = round((self._clock() - selection_started) * 1000, 3)
        metrics['core_ms'] = round((self._clock() - core_started) * 1000, 3)
        context = self._selection_context(selection)
        errors: list[str] = ["hermes_catalog_unavailable"] if availability_error else []
        if availability_error:
            context = "Skill Proof cannot read Hermes skills_list. Skill availability is unknown; report this limitation."
        if len(context.encode("utf-8")) > self.context_budget_bytes:
            compact = _compact_context(selection, self.mode, availability_error)
            context = compact if len(compact.encode("utf-8")) <= self.context_budget_bytes else ""
            errors.append("context_budget_exceeded")
        state = _TurnState(
            turn_id=clean_turn_id,
            session_id=clean_session_id,
            task_id=clean_task_id,
            mode=self.mode,
            query_sha256=hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
            catalog=catalog,
            selection=selection,
            context=context,
            errors=errors,
            availability="unavailable" if availability_error else "hermes_list" if available_names is not None else "local_only",
            local_skill_count=local_count,
            host_unindexed=host_unindexed,
            host_name_count=None if available_names is None or availability_error else len(available_names),
            session=session_evidence,
            indexed_names=indexed_names,
            vector_trace=vector_trace,
            # Recorded so "why not X" can say vetoed instead of guessing; the
            # same scan runs inside _rank for every turn (about 0.5 ms).
            vetoed=tuple(
                skill.name for skill in catalog.skills if _skill_is_negated(skill.name, query_text)
            ),
            performance=metrics,
        )
        with self._lock:
            self._turns[clean_turn_id] = state
            self._latest_by_task[(clean_session_id, clean_task_id)] = clean_turn_id
            if clean_session_id:
                self._latest_by_session[clean_session_id] = clean_turn_id
            self._latest_turn_id = clean_turn_id
        self._publish_active_turn(selection)
        return TurnStart(selection, context, tuple(errors))

    def _publish_active_turn(self, selection: Selection) -> None:
        """Publish (or clear) the bridge-visible turn policy. Never raises.

        ``active_turn.json`` carries only the selected skill and its
        invariants, so external host hooks (bridge.py) can enforce
        forbidden_tools / ordered_tools exactly like the engine does.
        """
        state: dict[str, Any] = {"mode": self.mode, "selected_skill": None}
        if selection.selected is not None:
            invariants = self._resolve_invariants(selection.selected.skill)
            state.update(
                {
                    "selected_skill": selection.selected.skill.name,
                    "required_tools": list(invariants.required_tools),
                    "forbidden_tools": list(invariants.forbidden_tools),
                    "ordered_tools": list(invariants.ordered_tools),
                    "completed": [],
                }
            )
        try:
            state["updated_at"] = self._clock()
            self._policy_store.write(state)
        except Exception:
            pass  # fail-open: policy publication must never break a turn

    @staticmethod
    def _has_symlink_component(root: pathlib.Path, relative_path: str) -> bool:
        current = root
        if current.is_symlink():
            return True
        for part in pathlib.PurePosixPath(relative_path).parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _prove_selected_source(self, selected: SkillRecord) -> tuple[Optional[str], Optional[int], Optional[str]]:
        root = selected.root_path
        source = root.joinpath(*pathlib.PurePosixPath(selected.relative_path).parts)
        cache_key = str(source)
        signature: Optional[tuple[int, int, int]] = None
        try:
            stat = source.stat()
            signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        except OSError:
            signature = None
        if signature is not None:
            # Plan 3.5: an unchanged stat signature reuses the previous proof
            # (skip disk read and YAML parse); any change falls through to the
            # full verification path.
            cached = self._proof_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        try:
            if self._has_symlink_component(root, selected.relative_path):
                return None, None, "unsafe_symlink"
            resolved_root = root.resolve(strict=True)
            resolved_source = source.resolve(strict=True)
            if not resolved_root.is_dir() or not resolved_source.is_file():
                return None, None, "source_unavailable"
            if not _is_within(resolved_source, resolved_root):
                return None, None, "unsafe_path"
            with resolved_source.open("rb") as handle:
                raw = handle.read(self.max_skill_bytes + 1)
        except (OSError, RuntimeError):
            return None, None, "source_unavailable"
        if len(raw) > self.max_skill_bytes:
            result = (None, None, "skill_too_large")
        else:
            try:
                reparsed = _parse_skill(
                    raw,
                    root_id=selected.root_id,
                    relative_path=selected.relative_path,
                    root_path=resolved_root,
                    source_path=source,
                )
            except _FrontmatterError:
                result = (None, None, "skill_changed")
            else:
                digest = hashlib.sha256(raw).hexdigest()
                if (
                    reparsed.normalized_name != selected.normalized_name
                    or digest != selected.source_sha256
                    or len(raw) != selected.source_bytes
                ):
                    result = (None, None, "skill_changed")
                else:
                    result = (digest, len(raw), None)
        if signature is not None:
            self._proof_cache[cache_key] = (signature, result)
        return result

    def observe_skill_loaded(
        self,
        *,
        session_id: str,
        task_id: str,
        skill_name: str,
        provenance: Optional[str] = None,
        use_count: Optional[int] = None,
        reused: Optional[bool] = None,
        reuse_after_patch: Optional[bool] = None,
    ) -> bool:
        with self._lock:
            state = self._state(session_id=session_id, task_id=task_id)
            if state is None:
                return False
            if state.selection.selected is None:
                # The host loaded a skill on its own while we abstained.
                if skill_name:
                    state.override_loaded = str(skill_name)
                return False
            selected = state.selection.selected.skill
            if normalize_identifier(skill_name) != selected.normalized_name:
                # The host loaded something other than the selected skill.
                state.override_loaded = str(skill_name)
                return False
            state.host_loaded_event = True
            state.loaded_provenance = None if provenance is None else str(provenance)
            state.loaded_use_count = use_count if isinstance(use_count, int) and not isinstance(use_count, bool) else None
            state.loaded_reused = reused if isinstance(reused, bool) else None
            state.loaded_reuse_after_patch = reuse_after_patch if isinstance(reuse_after_patch, bool) else None
            digest, size, error = self._prove_selected_source(selected)
            state.source_sha256 = digest
            state.source_bytes = size
            state.source_error = error
            if error and error not in state.errors:
                state.errors.append(error)
            state.hub = self._hub_provenance(selected)
            return True

    def _remember_tool(self, state: _TurnState, tool_name: str) -> None:
        clean_name = str(tool_name or "").strip()
        if not clean_name:
            return
        state.observed_tools.append(clean_name)
        if len(state.observed_tools) > self.observed_tool_limit:
            del state.observed_tools[: len(state.observed_tools) - self.observed_tool_limit]

    def _resolve_invariants(self, skill: SkillRecord) -> SkillInvariants:
        # ponytail: tuple concatenation with deduplication; add schema checks if users define custom predicates
        cfg = self.invariants.get(skill.normalized_name, SkillInvariants())
        front = skill.invariants
        req = tuple(dict.fromkeys(front.required_tools + cfg.required_tools))
        forb = tuple(dict.fromkeys(front.forbidden_tools + cfg.forbidden_tools))
        order = tuple(dict.fromkeys(front.ordered_tools + cfg.ordered_tools))
        return SkillInvariants(required_tools=req, forbidden_tools=forb, ordered_tools=order)

    def guard_tool(
        self,
        *,
        turn_id: Optional[str],
        session_id: str,
        task_id: str,
        tool_name: str,
    ) -> GuardResult:
        normalized_tool = normalize_identifier(tool_name)
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return GuardResult(True, "no_turn_state")
            if normalized_tool in self._SKILL_TOOLS:
                self._remember_tool(state, tool_name)
                return GuardResult(True, "skill_tool")
            selection = state.selection
            if self.mode == "enforce-tools" and selection.status == "blocked" and selection.required:
                return GuardResult(
                    False,
                    selection.reason,
                    f"Skill Proof blocked this tool: {selection.reason}.",
                )
            if selection.selected is None:
                self._remember_tool(state, tool_name)
                return GuardResult(True, "no_required_skill")
            if self.mode == "enforce-tools" and not state.host_loaded_event:
                name = selection.selected.skill.name
                return GuardResult(
                    False,
                    "selected_skill_not_loaded",
                    f'Call skill_view for "{name}" before operational tools.',
                )
            if state.host_loaded_event:
                state.active = True
                state.active_evidence = "post_load_tool_call"

            inv = self._resolve_invariants(selection.selected.skill)
            if not inv.is_empty:
                if normalized_tool in inv.forbidden_tools:
                    state.compliance = "failed"
                    reason = f"forbidden_tool:{normalized_tool}"
                    if reason not in state.compliance_reasons:
                        state.compliance_reasons.append(reason)
                    if self.mode == "enforce-tools":
                        return GuardResult(
                            False,
                            "forbidden_tool",
                            f'Skill Proof blocked this tool: skill "{selection.selected.skill.name}" forbids tool "{tool_name}".',
                        )
                if inv.ordered_tools and normalized_tool in inv.ordered_tools:
                    idx = inv.ordered_tools.index(normalized_tool)
                    prior = inv.ordered_tools[:idx]
                    normalized_prior = [normalize_identifier(t) for t in state.observed_tools]
                    missing = [p for p in prior if p not in normalized_prior]
                    if missing:
                        state.compliance = "failed"
                        reason = f"order_violation:{normalized_tool}_before_{missing[0]}"
                        if reason not in state.compliance_reasons:
                            state.compliance_reasons.append(reason)
                        if self.mode == "enforce-tools":
                            return GuardResult(
                                False,
                                "tool_order_violated",
                                f'Skill Proof blocked this tool: skill "{selection.selected.skill.name}" requires "{missing[0]}" before "{tool_name}".',
                            )

            self._remember_tool(state, tool_name)
            if state.host_loaded_event:
                return GuardResult(True, "selected_skill_loaded")
            return GuardResult(True, "mode_not_enforcing")

    def observe_tool_result(
        self,
        *,
        turn_id: Optional[str],
        session_id: str,
        task_id: str,
        tool_name: str,
        args: Optional[Mapping[str, Any]],
        result: Any,
        status: Optional[str] = None,
    ) -> bool:
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return False
            if not state.observed_tools or normalize_identifier(state.observed_tools[-1]) != normalize_identifier(tool_name):
                self._remember_tool(state, tool_name)
            if normalize_identifier(tool_name) != "skill_view" or state.selection.selected is None:
                return False
            supplied_name = ""
            if isinstance(args, Mapping):
                supplied_name = str(args.get("name") or args.get("skill_name") or "")
            if normalize_identifier(supplied_name) != state.selection.selected.skill.normalized_name:
                return False
            if args and args.get('file_path'):
                return False  # Supporting-file output is not the main skill's proof.
            if status is not None and normalize_identifier(status) not in {"success", "ok", "completed"}:
                return False
            if isinstance(result, bytes):
                raw_result = result
            elif isinstance(result, str):
                raw_result = result.encode("utf-8")
            else:
                raw_result = json.dumps(
                    result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")
            state.tool_result_sha256_pre_transform = hashlib.sha256(raw_result).hexdigest()
            state.tool_result_bytes_pre_transform = len(raw_result)
            try:
                parsed_result = json.loads(raw_result)
                reported_path = parsed_result.get('_source_path') if isinstance(parsed_result, dict) and parsed_result.get('success') is True else None
                if isinstance(reported_path, str) and pathlib.Path(reported_path).is_absolute():
                    actual = os.path.normcase(str(pathlib.Path(reported_path).resolve()))
                    expected = os.path.normcase(str(state.selection.selected.skill.source_path.resolve()))
                    state.source_path_match = 'match' if actual == expected else 'mismatch'
                    if actual != expected and 'source_path_mismatch' not in state.errors:
                        state.errors.append('source_path_mismatch')
            except (ValueError, OSError):
                pass
            return True

    @staticmethod
    def _candidate_payload(candidate: RankedCandidate) -> dict[str, Any]:
        skill = candidate.skill
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "root_id": skill.root_id,
            "relative_path": skill.relative_path,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
        }

    def _evaluate_compliance(self, state: _TurnState) -> None:
        if state.selection.selected is None:
            state.compliance = "not_applicable"
            return
        skill = state.selection.selected.skill
        inv = self._resolve_invariants(skill)
        if inv.is_empty:
            if state.host_loaded_event:
                state.compliance = "compliant"
                if "selected_skill_loaded" not in state.compliance_reasons:
                    state.compliance_reasons.append("selected_skill_loaded")
            else:
                state.compliance = "skipped_nudge" if state.mode == "nudge" else "failed"
                if "skill_not_loaded" not in state.compliance_reasons:
                    state.compliance_reasons.append("skill_not_loaded")
            return
        if state.compliance == "failed":
            return
        if not state.host_loaded_event:
            state.compliance = "failed"
            if "skill_not_loaded" not in state.compliance_reasons:
                state.compliance_reasons.append("skill_not_loaded")
            return
        observed = normalize_identifiers(state.observed_tools)
        missing = [t for t in inv.required_tools if t not in observed]
        if missing:
            state.compliance = "failed"
            for m in missing:
                reason = f"missing_required_tool:{m}"
                if reason not in state.compliance_reasons:
                    state.compliance_reasons.append(reason)
            return
        state.compliance = "verified"

    def _receipt_payload(self, state: _TurnState) -> dict[str, Any]:
        self._evaluate_compliance(state)
        diagnostics = Counter(item.code for item in state.catalog.diagnostics)
        selected = state.selection.selected
        return {
            "schema_version": "skill-proof.receipt.v1",
            "turn_id": state.turn_id,
            "mode": state.mode,
            "query_sha256": state.query_sha256,
            "catalog": {
                "skill_count": len(state.catalog.skills),
                "catalog_hash": state.catalog.catalog_hash,
                "diagnostic_counts": dict(sorted(diagnostics.items())),
                "availability": state.availability,
                "local_skill_count": state.local_skill_count,
                "host_name_count": state.host_name_count,
                "host_unindexed_count": len(state.host_unindexed),
                "host_unindexed_sample": list(state.host_unindexed[:10]),
                "host_unindexed_omitted": max(0, len(state.host_unindexed) - 10),
                "diagnostics": [{'code': d.code, 'root_id': d.root_id, 'relative_path': d.relative_path[:240]} for d in state.catalog.diagnostics[:20]],
                "diagnostics_omitted": max(0, len(state.catalog.diagnostics) - 20),
            },
            "decision": {
                "status": state.selection.status,
                "reason": state.selection.reason,
                "explicit": state.selection.explicit,
                "required": state.selection.required,
            },
            "selected": None if selected is None else self._candidate_payload(selected),
            "candidates": [self._candidate_payload(item) for item in state.selection.candidates],
            "evidence": {
                "host_loaded_event": state.host_loaded_event,
                "hermes_loaded_event": state.host_loaded_event,  # legacy alias (plan Part 1.2 item 4)
                "override_loaded": state.override_loaded,
                "provenance": state.loaded_provenance,
                "use_count": state.loaded_use_count,
                "reused": state.loaded_reused,
                "reuse_after_patch": state.loaded_reuse_after_patch,
                "source_sha256": state.source_sha256,
                "source_bytes": state.source_bytes,
                "source_error": state.source_error,
                "source_path_match": state.source_path_match,
                "hub": dict(state.hub),
                "session": dict(state.session),
                "tool_result_sha256_pre_transform": state.tool_result_sha256_pre_transform,
                "tool_result_bytes_pre_transform": state.tool_result_bytes_pre_transform,
            },
            "runtime": {
                "active": state.active,
                "active_evidence": state.active_evidence,
                "observed_tools": list(state.observed_tools),
            },
            "compliance": state.compliance,
            "compliance_reasons": list(state.compliance_reasons),
            "verification": "unverified",
            "errors": list(state.errors),
            "complete": state.complete,
            "performance": dict(state.performance),
            "provider": state.vector_trace.get("provider_configured", "off"),
            "provider_used": state.vector_trace.get("provider_used", "off"),
            "fallback_reason": state.vector_trace.get("fallback_reason", "not_used"),
            "embedding_latency_ms": state.vector_trace.get("embedding_latency_ms", 0.0),
            "model": getattr(self.vector_index, "model", None) if self.vector_index else None,
        }

    def record_timing(self, turn_id: str, *, listing_ms: float, total_ms: float) -> None:
        with self._lock:
            state = self._state(turn_id=turn_id)
            if state is not None:
                state.performance.update(listing_ms=round(listing_ms, 3), total_ms=round(total_ms, 3))

    def record_prompt_reduction(self, turn_id: str, chars_removed: int) -> None:
        """Record bounded, content-free system-prompt reduction telemetry."""
        if chars_removed <= 0:
            return
        with self._lock:
            state = self._state(turn_id=turn_id)
            if state is not None:
                state.performance["skill_index_chars_removed"] = (
                    int(state.performance.get("skill_index_chars_removed", 0)) + int(chars_removed)
                )

    def receipt(
        self,
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return {}
            return self._receipt_payload(state)

    def finish_turn(self, *, turn_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._state(turn_id=turn_id)
            if state is None:
                return {}
            state.complete = True
            # Focus lasts a bounded number of turns, counted including the turn
            # that requested it.
            memory = self._sessions.get(state.session_id)
            if memory is not None and memory.focus and memory.focus_remaining > 0:
                memory.focus_remaining -= 1
            payload = self._receipt_payload(state)
            completed = [key for key, value in self._turns.items() if value.complete]
            for key in completed[:-self.retained_turn_limit]:
                del self._turns[key]
            self._latest_by_task = {k: v for k, v in self._latest_by_task.items() if v in self._turns}
            self._latest_by_session = {k: v for k, v in self._latest_by_session.items() if v in self._turns}
            return payload

    def overlap(
        self,
        *,
        min_similarity: float = 0.4,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Near-duplicate skills across the configured roots (read-only)."""
        with self._lock:
            catalog = scan_catalog(
                self._roots,
                max_skill_bytes=self.max_skill_bytes,
                cache=self._catalog_cache,
                disabled=self.disabled,
            )
        return overlap_report(catalog, min_similarity=min_similarity, limit=limit)

    def refresh_catalog(self) -> None:
        """Force a content reread next turn; existing turn evidence stays unchanged."""
        with self._lock:
            self._catalog_cache.clear()
            self._filtered_view_cache.clear()
            self._catalog_scan_at = float("-inf")

    def latest_catalog(self) -> Optional[Catalog]:
        """The most recently scanned catalog for this engine, if any."""
        with self._lock:
            return self._catalog_cache.get("__catalog__")

    @classmethod
    def from_config(
        cls,
        roots: Mapping[str, os.PathLike[str] | str],
        *,
        config: Optional[Mapping[str, Any]] = None,
        vector_index: Any = "auto",
        policy_store: Optional[Any] = None,
        **overrides: Any,
    ) -> "SkillProofEngine":
        """Standard construction seam for standalone surfaces (CLI, MCP, bridges).

        ``vector_index="auto"`` resolves the env-configured index exactly like
        ``vector_index_from_env()``; pass an instance to force one or ``None``
        to stay lexical-only. ``config`` maps engine settings by their
        constructor names (unknown keys are ignored); ``overrides`` win over
        both. The default policy store is in-memory, so standalone calls never
        write turn state to disk.
        """
        settings: dict[str, Any] = {}
        if isinstance(config, Mapping):
            for key, value in config.items():
                if key in {
                    "mode", "min_score", "min_margin", "max_candidates", "max_skill_bytes",
                    "context_budget_bytes", "observed_tool_limit", "retained_turn_limit",
                    "invariants", "hub_lock_path", "synonyms", "session_memory", "focus_turns",
                    "disabled", "block_modified_hub_skills", "catalog_cache_ttl_ms", "agent_routing",
                }:
                    settings[str(key)] = value
        if isinstance(vector_index, str) and vector_index.lower() == "auto":
            settings["vector_index"] = vector_index_from_env()
        elif vector_index is None or vector_index is False:
            settings["vector_index"] = None
        else:
            settings["vector_index"] = vector_index
        settings.update(overrides)
        if "policy_store" not in settings:
            settings["policy_store"] = policy_store if policy_store is not None else InMemoryPolicyStore()
        return cls(roots, **settings)

    def _hub_entries(self) -> Mapping[str, Mapping[str, Any]]:
        """Read-only hub lock entries keyed by normalized skill name (mtime-cached)."""
        if self.hub_lock_path is None:
            return {}
        try:
            info = self.hub_lock_path.stat()
        except OSError:
            return {}
        if info.st_size > _HUB_LOCK_MAX_BYTES:
            return {}
        if self._hub_cache is not None and self._hub_cache_mtime == info.st_mtime_ns:
            return self._hub_cache
        entries: dict[str, Mapping[str, Any]] = {}
        try:
            payload = json.loads(self.hub_lock_path.read_text(encoding="utf-8"))
            installed = payload.get("installed") if isinstance(payload, Mapping) else None
            if isinstance(installed, Mapping):
                for name, entry in installed.items():
                    if isinstance(entry, Mapping):
                        entries[normalize_identifier(str(name))] = entry
        except (OSError, ValueError):
            entries = {}
        self._hub_cache = entries
        self._hub_cache_mtime = info.st_mtime_ns
        return entries

    def _hub_provenance(self, selected: SkillRecord) -> dict[str, Any]:
        """Cross-check a loaded skill against the hub lock; never trust it blindly."""
        entry = self._hub_entries().get(selected.normalized_name)
        if entry is None:
            return {}
        scan = entry.get("scan_provenance")
        scan = scan if isinstance(scan, Mapping) else {}
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        recorded = str(scan.get("bundle_hash") or entry.get("content_hash") or "")
        recorded_hex = recorded.split(":", 1)[-1] if recorded else ""
        files = entry.get("files") if isinstance(entry.get("files"), (list, tuple)) else ()
        local_hash = (
            _bundle_sha256(selected.source_path.parent, [str(name) for name in files]) if files else None
        )
        if local_hash is not None and recorded_hex:
            bundle = "match" if local_hash == recorded_hex else "modified"
        else:
            bundle = "unknown"
        return {
            "available": True,
            "trust_level": str(entry.get("trust_level") or ""),
            "scan_verdict": str(entry.get("scan_verdict") or ""),
            "source": str(entry.get("source") or ""),
            "source_revision": str(metadata.get("source_revision") or ""),
            "bundle": bundle,
        }

    def hub_status(self) -> dict[str, Any]:
        if self.hub_lock_path is None:
            return {"enabled": False}
        entries = self._hub_entries()
        return {"enabled": True, "path": str(self.hub_lock_path), "entries": len(entries)}

    def root_suggestions(self, limit: int = 3) -> list[dict[str, Any]]:
        with self._lock:
            return suggest_roots(self._roots, limit=limit)

    def summary(
        self,
        *,
        turn_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> str:
        with self._lock:
            state = self._state(turn_id=turn_id, session_id=session_id, task_id=task_id)
            if state is None:
                return "Skill Proof: no turn evidence"
            self._evaluate_compliance(state)
            selected = state.selection.selected
            name = selected.skill.name if selected is not None else "none"
            loaded = "yes" if state.host_loaded_event else "no"
            active = "yes" if state.active else "no"
            return (
                f"Skill Proof: selected={name} loaded={loaded} active={active} "
                f"compliance={state.compliance} verification=unverified"
            )

    @property
    def latest_turn_id(self) -> Optional[str]:
        with self._lock:
            return self._latest_turn_id
