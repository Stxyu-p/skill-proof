from __future__ import annotations

"""Dialogue layer: negation veto, session memory, and dialogue references."""

import re
import unicodedata
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional, Sequence


from _catalog import (
    _identifier_in_query,
    _phrase_present,
    extract_explicit_skill_names,
    normalize_identifier,
    normalize_identifiers,
)


_NEGATION_WORDS = (
    # Thai vetoes keep the loose window: spaceless text needs back-tracking
    # inside a non-space run ("ไม่ต้องใช้python-tdd").
    "ไม่ต้อง",
    "ไม่ใช้",
    "ไม่เอา",
    "ไม่เลือก",
    "อย่าใช้",
    "ยกเว้น",
    "ห้าม",
    "อย่า",
    "ไม่",
    # Chinese
    "不要用",
    "不要",
    "别用",
    "别",
    "不用",
    "禁止使用",
    "禁止",
    "除了",
    # Hindi
    "मत",
    "नहीं",
    "बिना",
    # Japanese
    "使わないでください",
    "使わないで",
    "使わない",
    "使わず",
    "不要",
    "除外",
    # Korean
    "하지마",
    "사용하지마",
    "쓰지마",
    "금지",
    # Vietnamese
    "đừng",
    "không",
    "tránh",
    "chớ",
    # German
    "nicht",
    "ohne",
    "kein",
    "keine",
    # French
    "sans",
    "sauf",
    "jamais",
    # Spanish
    "sin",
    "excepto",
    "nunca",
    "jamás",
)


_SOV_NEGATION_WORDS = (
    "使わないでください",
    "使わないで",
    "使わない",
    "使わず",
    "不要",
    "除外",
    "하지마",
    "사용하지마",
    "쓰지마",
    "금지",
)


_ENGLISH_NEGATION_WORDS = (
    "do\\s+not",
    "don't",
    "doesn't",
    "never",
    "without",
    "except",
    "not",
    "no",
    # German
    "nicht",
    "ohne",
    "kein",
    "keine",
    # French
    "ne\\s+pas",
    "sans",
    "sauf",
    "jamais",
    # Spanish
    "sin",
    "excepto",
    "nunca",
    "jamás",
    # Vietnamese
    "đừng",
    "không",
)


# Closed vocabulary for veto clauses ("don't use the skill X").
_NEGATION_FILLERS = (
    "ever", "really", "actually", "just", "again", "simply", "even",
    "want", "wants", "wanted", "need", "needs", "needed", "to",
    "use", "uses", "using", "load", "loads", "loading", "select",
    "selecting", "choose", "choosing", "pick", "picking", "invoke",
    "call", "run", "running",
    "the", "a", "an", "any", "that", "this", "those", "these", "my", "its",
    "skill", "skills", "สกิล",
    # German
    "bitte", "verwenden", "nutzen", "laden", "den", "dem", "die", "das", "ein", "eine",
    # French
    "utiliser", "charger", "le", "la", "les", "ce", "cette", "de", "d'",
    # Spanish
    "usar", "utilizar", "cargar", "el", "la", "los", "las", "este", "esta", "de",
    # Vietnamese
    "dùng", "sử dụng", "kỹ", "năng", "cho",
    # Japanese / Korean particles & fillers
    "は", "を", "が", "で", "の", "ください",
)


@lru_cache(maxsize=1024)
def _negation_patterns(normalized_name: str) -> "tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]":
    """Compiled veto patterns per skill name (compiling per call cost ~1 ms x N skills
    and overflowed the re module cache on large catalogs)."""
    tail = r"[`\"']?\$?" + re.escape(normalized_name) + r"(?![a-z0-9_-])"
    loose = re.compile(
        r"(?:(?<!\w)|(?<=[\s\u0e00-\u0e7f\u4e00-\u9fff]))(?:"
        + "|".join(_NEGATION_WORDS)
        + r")(?:\s*\S+){0,2}\s*"
        + tail
    )
    negations = "|".join(_NEGATION_WORDS + _ENGLISH_NEGATION_WORDS)
    structured = re.compile(
        r"(?<!\w)(?:" + negations + r")"
        r"(?:\s+(?:" + "|".join(_NEGATION_FILLERS) + r"))*"
        r"\s*" + tail
    )
    # SOV post-negation (Japanese & Korean): skill followed by optional particle/fillers and negation
    post_negation = re.compile(
        tail
        + r"(?:[はをがでの는을를에]?\s*(?:"
        + "|".join(_NEGATION_FILLERS)
        + r"\s*)*)"
        + r"(?:"
        + "|".join(_SOV_NEGATION_WORDS)
        + r")"
    )
    return loose, structured, post_negation


_NEGATION_SUBSTRINGS: tuple[str, ...] = (
    "not", "don't", "dont", "never", "without", "except", "no", "doesn't", "doesnt",
    "ไม่", "อย่า", "ห้าม", "ยกเว้น",
    "不", "别", "禁止", "除了",
    "मत", "नहीं", "बिना",
    "하지마", "사용하지마", "금지", "쓰지마",
    "使わない", "不要", "除外", "使わず",
    # German
    "nicht", "ohne", "kein", "keine",
    # French
    "sans", "sauf", "jamais", "pas",
    # Spanish
    "sin", "excepto", "nunca", "jamás",
    # Vietnamese
    "đừng", "không", "tránh", "chớ",
)


@lru_cache(maxsize=2048)
def _query_might_contain_negation(query: str) -> bool:
    # ponytail: fast substring probe skips expensive regexes on non-negated queries
    if not query:
        return False
    folded = query.casefold()
    return any(stem in folded for stem in _NEGATION_SUBSTRINGS)


def _skill_is_negated(
    name: str,
    query: str,
    aliases: Sequence[str] = (),
    norm_query: Optional[str] = None,
) -> bool:
    """True when the query vetoes *name* or any of its *aliases* ("don't use X", "ไม่เอา X").

    Three shapes are accepted: a loose window for Thai vetoes, a structured
    filler-vocabulary clause for prefix vetoes, and an SOV post-negation pattern
    for Japanese/Korean syntax ("skillは使わないで").
    """
    if not _query_might_contain_negation(query):
        return False
    normalized_query = norm_query if norm_query is not None else normalize_identifier(query)
    candidates = (name,) + tuple(aliases)
    for cand in candidates:
        norm = normalize_identifier(cand)
        if not norm or norm not in normalized_query:
            continue
        loose, structured, post_negation = _negation_patterns(norm)
        if bool(loose.search(normalized_query)) or bool(structured.search(normalized_query)) or bool(post_negation.search(normalized_query)):
            return True
    return False


# Closed vocabularies for requests that point at the *ongoing* skill instead of
# describing a task.  Only these phrasings are accepted: an ordinary sentence
# that happens to contain "again" or "เดิม" must keep routing lexically.
_DIALOGUE_RELEASE_PHRASES = (
    "stop using",
    "stop focusing",
    "stop with",
    "unfocus",
    "no longer use",
    "forget the skill",
    "เลิกใช้",
    "หยุดใช้",
    "เลิกโฟกัส",
    "เลิกทำ",
    # German
    "aufhören mit",
    "nicht mehr verwenden",
    # French
    "arrêter d'utiliser",
    # Spanish
    "dejar de usar",
    # Japanese
    "使うのをやめて",
    "使用中止",
    # Korean
    "그만 써",
    # Vietnamese
    "ngừng dùng",
)


_DIALOGUE_FOCUS_PHRASES = (
    "keep using",
    "keep working with",
    "keep going with",
    "stick with",
    "stay on",
    "from now on use",
    "continue with",
    "carry on with",
    "ใช้ต่อ",
    "ใช้ตัวนี้ต่อ",
    "ทำต่อด้วย",
    "ใช้ต่อไป",
    # German
    "weiterhin verwenden",
    "weitermachen mit",
    # French
    "continuer avec",
    # Spanish
    "seguir usando",
    # Japanese
    "使い続けて",
    # Korean
    "계속 써",
    # Vietnamese
    "tiếp tục dùng",
)


_DIALOGUE_REPEAT_PHRASES = (
    "same skill",
    "the same one",
    "same one",
    "same as before",
    "like before",
    "that skill again",
    "that one again",
    "do the same",
    "same as last time",
    "ใช้ตัวเดิม",
    "ตัวเดิม",
    "อันเดิม",
    "เหมือนเดิม",
    "แบบเดิม",
    "อย่างเมื่อกี้",
    "เมื่อกี้",
    # German
    "gleicher skill",
    "wie vorher",
    # French
    "même compétence",
    "comme avant",
    # Spanish
    "misma habilidad",
    "igual que antes",
    # Japanese
    "同じスキル",
    "さっきの",
    "さっきと同じ",
    # Korean
    "같은 스킬",
    "방금 전",
    # Vietnamese
    "kỹ năng cũ",
    "như trước",
)


_DIALOGUE_PREVIOUS_PHRASES = (
    "previous skill",
    "the one before",
    "skill before",
    "ครั้งก่อน",
    "ก่อนหน้า",
    "อันที่แล้ว",
    "ตัวที่แล้ว",
    "อันก่อน",
    # German
    "vorheriger skill",
    "vorherige",
    # French
    "compétence précédente",
    "précédent",
    # Spanish
    "habilidad anterior",
    "anterior",
    # Japanese
    "前のスキル",
    "前回の",
    # Korean
    "이전 스킬",
    # Vietnamese
    "kỹ năng trước",
)


_DIALOGUE_FAMILIES = tuple(
    # Phrases are normalized the same way queries are: NFKC rewrites Thai SARA AM
    # (\u0e33) into NIKHANIT + SARA AA, so an unnormalized phrase silently never matches.
    (
        kind,
        tuple(unicodedata.normalize("NFKC", phrase).casefold() for phrase in phrases),
    )
    for kind, phrases in (
        ("release", _DIALOGUE_RELEASE_PHRASES),
        ("focus", _DIALOGUE_FOCUS_PHRASES),
        ("repeat", _DIALOGUE_REPEAT_PHRASES),
        ("previous", _DIALOGUE_PREVIOUS_PHRASES),
    )
)


def _named_skill_in_query(query: str, known_names: Sequence[str]) -> str:
    """Return the *known* skill the request names, or "".

    Only catalog names count: an unknown token after "skill" ("stop using the
    skill now") must not drive focus or release.
    """
    known = tuple(str(name) for name in known_names if name)
    known_ids = normalize_identifiers(known)
    for candidate in extract_explicit_skill_names(query, known_names=known_ids):
        if normalize_identifier(candidate) in known_ids:
            return str(candidate)
    for name in known:
        if _identifier_in_query(name, query):
            return name
    return ""


# Lead-in words that may sit between "keep using" and the skill name.
_DIALOGUE_FILLER = frozenset({
    "the", "a", "an", "skill", "skills", "my", "this", "that", "current",
    "สกิล", "ตัว", "อัน", "ตัวนี้", "อันนี้", "ของ",
})


_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _skill_named_after(query: str, phrase: str) -> str:
    """First identifier-shaped token after *phrase* ("keep using X").

    The token may be a skill that is not in the catalog; reporting it as
    unknown is more truthful than silently falling back to session history.
    """
    at = query.casefold().find(phrase)
    if at < 0:
        return ""
    tail = query[at + len(phrase) :].lstrip(" \t,.;:!?—–-")
    for _ in range(4):
        match = _IDENTIFIER_TOKEN.match(tail)
        if not match:
            return ""
        token = match.group(0).rstrip(".,;:!?)]}")
        if token and token.casefold() not in _DIALOGUE_FILLER:
            return token
        tail = tail[match.end() :].lstrip(" \t,.;:!?—–-")
    return ""


@dataclass(frozen=True)
class DialogueReference:
    """A request that refers to the session's skill history instead of a task."""

    kind: str
    phrase: str = ""
    name: str = ""


def parse_dialogue_reference(
    query: str,
    *,
    known_names: Sequence[str] = (),
) -> Optional[DialogueReference]:
    """Detect "use the same skill", "keep using X", "stop using X", "อันที่แล้ว"..."""
    text = unicodedata.normalize("NFKC", str(query or "")).casefold().strip()
    if not text:
        return None
    for kind, phrases in _DIALOGUE_FAMILIES:
        for phrase in phrases:
            if _phrase_present(phrase, text):
                name = _named_skill_in_query(text, known_names)
                if not name and kind in ("focus", "release"):
                    name = _skill_named_after(text, phrase)
                return DialogueReference(kind, phrase, name)
    return None


def _release_remainder(query: str, reference: DialogueReference, released: str) -> "Optional[str]":
    """Text that follows the release clause, or ``None`` when nothing was cut.

    "stop using python-tdd and design a landing page" must still route to the
    second half: the release only cancels the focus, not the rest of the turn.
    An empty string means the clause was cut and nothing else was asked, so the
    full query (which still contains the released name) must not be ranked.
    """
    text = str(query or "")
    if not text.strip():
        return ""
    # Work on a normalized copy: NFKC can change length (Thai SARA AM), so
    # positions are only safe when both the haystack and the anchor are folded.
    folded = unicodedata.normalize("NFKC", text).casefold()
    anchor = unicodedata.normalize("NFKC", str(released or "")).casefold().strip()
    index = folded.find(anchor) if anchor else -1
    if index >= 0:
        rest = folded[index + len(anchor) :]
    else:
        phrase = reference.phrase or ""
        at = folded.find(phrase)
        if at < 0:
            return None
        rest = folded[at + len(phrase) :]
    return rest.lstrip(" \t,.;:!?—–-")


@dataclass
class _SessionMemory:
    """What this session was just using, for dialogue references and focus."""

    stack: list[str] = field(default_factory=list)
    focus: Optional[str] = None
    focus_remaining: int = 0
