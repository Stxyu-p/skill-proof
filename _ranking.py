from __future__ import annotations

"""Ranking layer: tokenizer, morphology, n-grams, IDF scoring, candidate index."""

import json
import pathlib
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from math import log
from typing import Any, Iterable, Mapping, Optional, Sequence


from _catalog import (
    Catalog,
    SkillRecord,
    _identifier_in_query,
    _is_sea_spaceless_character,
    _is_spaceless_character,
    _phrase_present,
    normalize_identifier,
    normalize_identifiers,
)
from _vector import (
    VectorIndex,
)
from _dialogue import (
    _query_might_contain_negation,
    _skill_is_negated,
)


# ponytail: default synonym bridges for high-frequency natural-language routing
DEFAULT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "ui-ux-pro-max": ("dashboard", "หน้าเว็บ", "ออกแบบเว็บ", "frontend", "ui", "ux", "interface", "web design"),
    "github-pr-workflow": ("pull request", "pr", "review pr", "pull", "merge", "เปิด pr"),
    "github-code-review": ("รีวิว pull request", "ตรวจ pr", "github review", "code review github", "ตรวจ pull request"),
    "hermes-context-budget": ("compress", "context", "token", "budget", "บีบอัด", "ย่อบทสนทนา", "context เต็ม"),
    "slides": ("presentation", "pitch", "สไลด์", "deck", "pitch deck"),
    "test-driven-development": ("tdd", "เขียนเทสต์", "red-green-refactor", "unit test"),
    "python-debugpy": ("แก้บั๊ก python", "debug python", "pdb", "debugpy", "โค้ดไพธอน", "traceback", "ส่องที"),
    "systematic-debugging": ("แก้บั๊ก", "debugging", "root cause", "โค้ดพัง", "หาสาเหตุบั๊ก"),
    "impeccable": ("ใส่สี", "spacing", "polish ui", "design polish"),
    "banner-design": ("แบนเนอร์", "promotion banner", "social media banner"),
    "imagegen": ("สร้าง logo", "โลโก้", "generate logo"),
    "edge-case-sadist": ("เคสพิสดาร", "boundary โหดๆ", "หาจุดพัง", "stress test", "break the api"),
    "adversarial-boundary-testing": ("boundary testing", "test boundary", "ทดสอบ boundary"),
    "superpowers-dispatching-parallel-agents": ("กระจายงาน", "subagent ขนาน", "dispatch parallel", "กระจาย subagent", "ขนานกัน"),
}


def overlap_report(
    catalog: Catalog,
    *,
    min_similarity: float = 0.4,
    limit: int = 20,
) -> dict[str, Any]:
    """Near-duplicate skills, so a library can be pruned with evidence.

    Similarity is the Jaccard index over the union of name, description, tag,
    and alias tokens: pure vocabulary overlap, no embeddings, no network. Exact
    duplicates never appear here because the catalog already collapsed them
    (``alias_skipped``) or shadowed them (``shadowed_by_root``).
    """
    if not 0.0 <= float(min_similarity) <= 1.0:
        raise ValueError("min_similarity must be between 0 and 1")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    threshold = float(min_similarity)
    bags: list[set[str]] = []
    for skill in catalog.skills:
        bags.append(
            set(
                _tokens(
                    " ".join(
                        (
                            skill.name.replace("-", " ").replace("_", " "),
                            skill.description,
                            " ".join(skill.tags),
                            " ".join(skill.aliases),
                        )
                    )
                )
            )
        )
    diagnostics = Counter(item.code for item in catalog.diagnostics)
    rows: list[dict[str, Any]] = []
    checked = 0
    for left in range(len(catalog.skills)):
        a = bags[left]
        if not a:
            continue
        for right in range(left + 1, len(catalog.skills)):
            b = bags[right]
            if not b:
                continue
            checked += 1
            shared = sorted(a & b)
            if not shared:
                continue
            union_len = len(a) + len(b) - len(shared)
            similarity = len(shared) / union_len if union_len else 0.0
            if similarity < threshold:
                continue
            first, second = catalog.skills[left], catalog.skills[right]
            rows.append(
                {
                    "a": first.name,
                    "b": second.name,
                    "root_a": first.root_id,
                    "root_b": second.root_id,
                    "similarity": round(similarity, 4),
                    "dice": round((2.0 * len(shared)) / (len(a) + len(b)), 4),
                    "shared": shared[:12],
                    "shared_count": len(shared),
                    "recommendation": _overlap_recommendation(first, second, similarity),
                }
            )
    rows.sort(key=lambda row: (-row["similarity"], row["a"], row["b"]))
    total = len(rows)
    return {
        "skills": len(catalog.skills),
        "pairs_checked": checked,
        "min_similarity": threshold,
        "limit": int(limit),
        "total_found": total,
        "truncated": total > int(limit),
        "exact_copies": int(diagnostics.get("alias_skipped", 0)),
        "shadowed_copies": int(diagnostics.get("shadowed_by_root", 0)),
        "pairs": rows[: int(limit)],
    }


def _overlap_recommendation(first: SkillRecord, second: SkillRecord, similarity: float) -> str:
    if first.description.strip().casefold() == second.description.strip().casefold():
        return "identical description: pick one name, point aliases at it, delete the other"
    if similarity >= 0.95:
        return "same content in practice: keep one name and delete the other"
    if first.root_id == second.root_id:
        return "same root: merge the descriptions or delete the weaker one"
    return "near-duplicate: merge, or differentiate the descriptions before both stay indexed"


_STOPWORDS = {
    # English
    "a", "an", "and", "for", "in", "of", "on", "please", "the", "this", "to", "use", "using", "with",
    "skill", "skills",
    # Thai
    "ช่วย", "ด้วย", "ทำ", "นี้", "สกิล", "ใช้", "ให้", "ใน", "ของ", "หน่อย",
    # Chinese (Standard conversational fillers & common particles)
    "请", "帮", "我", "进行", "和", "与", "或", "在", "的", "了", "是", "把", "用", "使用", "一个",
    "请帮我", "帮我", "请使用", "帮我做", "技能",
    # Hindi
    "का", "के", "की", "है", "में", "से", "को", "पर", "और", "तो", "भी", "कर", "करें", "हो", "लिए", "कृपया",
    # Korean
    "의", "가", "이", "은", "들", "는", "좀", "잘", "과", "도", "를", "으로", "에", "와", "하다", "해주세요",
    "스킬", "사용",
    # Japanese
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し", "ある", "いる", "も", "する", "から",
    "スキル", "使って", "使いたい", "お願いします",
    # German
    "der", "die", "das", "und", "für", "in", "mit", "von", "zu", "eine", "einen", "ein", "bitte", "verwenden", "nutzen",
    # French
    "le", "la", "les", "de", "pour", "dans", "avec", "un", "une", "des", "sur", "utiliser", "compétence",
    # Spanish
    "el", "la", "de", "para", "por", "en", "con", "un", "una", "los", "las", "usar", "usando", "habilidad",
    # Vietnamese
    "của", "cho", "trong", "với", "một", "dùng", "sử dụng", "kỹ năng", "giúp",
}


# ponytail: lru_cache on _tokens cuts repeated tokenization across catalog scans
@lru_cache(maxsize=8192)
def _tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    raw_tokens: list[str] = []
    current: list[str] = []
    for ch in normalized:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "M", "N"):
            current.append(ch)
        else:
            if current:
                raw_tokens.append("".join(current))
                current = []
    if current:
        raw_tokens.append("".join(current))

    return tuple(
        token for token in raw_tokens
        if len(token) > 1 and token not in _STOPWORDS and not any(_is_sea_spaceless_character(c) for c in token)
    )


def _normalize_morph(token: str) -> str:
    """Collapse common English plural/past/gerund suffixes without an external stemmer.

    Only ASCII alphabetic tokens longer than four characters are touched; the
    callers keep both the full token and this base form, so an imperfect base
    can never remove the original exact match.
    """
    if len(token) <= 4 or not token.isascii() or not token.isalpha():
        return token
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"  # categories -> category
    if token.endswith("ing") and len(token) > 5:
        base = token[:-3]
        return base if len(base) >= 4 else token  # crashing -> crash
    if token.endswith("eed"):
        return token  # speed/feed are not past-tense -ed forms
    if token.endswith("ed") and len(token) > 4:
        base = token[:-2]
        return base if len(base) >= 4 else token  # failed -> fail
    if token.endswith("es"):
        stem = token[:-2]
        if stem.endswith(("sh", "ch", "ss", "x")):
            return stem if len(stem) >= 4 else token  # crashes -> crash
        base = token[:-1]
        return base if len(base) >= 4 else token  # tables -> table
    if token.endswith("ss"):
        return token
    if token.endswith("s"):
        base = token[:-1]
        return base if len(base) >= 4 else token  # keeps -> keep
    return token


def _add_morph_bases(tokens: set[str]) -> None:
    for token in tuple(tokens):
        base = _normalize_morph(token)
        if base != token:
            tokens.add(base)


_NGRAM_SIZE = 2


_NGRAM_MINIMUM_DICE = 0.25


_NGRAM_WEIGHT = 0.55


# ponytail: lru_cache on _char_ngrams cuts repeated n-gram generation on catalogs and queries
@lru_cache(maxsize=4096)
def _char_ngrams(value: str, size: int = _NGRAM_SIZE) -> "frozenset[str]":
    """Character n-grams of the spaceless-script part of *value*.

    Spaceless scripts have no word boundaries to tokenize on, so token overlap
    cannot see that "ช่วยเขียน เอกสาร ภาษาไทย" and "สร้างและตรวจ เอกสาร
    ภาษาไทย" are about the same thing.  Character n-grams are a dictionary-free
    way to measure that overlap, and they never fire for spaced scripts.
    """
    text = "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if _is_spaceless_character(character)
    )
    if len(text) < size:
        return frozenset()
    return frozenset(text[index : index + size] for index in range(len(text) - size + 1))


def _ngram_dice(left: "frozenset[str]", right: "frozenset[str]") -> float:
    """Symmetric overlap of two n-gram sets (0.0 when either side is empty)."""
    if not left or not right:
        return 0.0
    shared = len(left & right)
    if not shared:
        return 0.0
    return (2.0 * shared) / (len(left) + len(right))


@dataclass(frozen=True)
class RankedCandidate:
    skill: SkillRecord
    score: float
    reasons: tuple[str, ...]
    companions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankRow:
    """One row of the full lexical ranking, kept for ``/skill-proof why``."""

    name: str
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Selection:
    status: str
    reason: str
    selected: Optional[RankedCandidate]
    candidates: tuple[RankedCandidate, ...]
    explicit: bool
    required: bool
    # Every skill that scored above zero this turn (bounded), so an "why not X"
    # question can be answered exactly without ever storing the prompt.
    rank_table: tuple[RankRow, ...] = ()
    suggested_agent: str = "mika"


_FLEET_AGENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("altima", ("review", "audit", "rubric", "verdict", "pr-review", "code-review", "altima", "รีวิว", "ตรวจโค้ด", "ตรวจงาน")),
    ("sora", ("code", "coding", "software", "bug", "tdd", "debug", "test", "fix", "endpoint", "backend", "deploy", "build", "sora", "refactor", "โค้ด", "แก้บั๊ก", "เขียนโค้ด", "เทสต์", "บิลด์")),
    ("nua", ("evidence", "research", "verify", "benchmark", "fact", "source", "nua", "investigate", "cite", "ค้นคว้า", "หาข้อมูล", "วิจัย", "หลักฐาน")),
    ("milim", ("design", "ui", "ux", "landing", "frontend", "tailwind", "token", "component", "css", "styling", "milim", "mockup", "ออกแบบ", "ดีไซน์", "หน้าเว็บ", "ยูไอ")),
)


@lru_cache(maxsize=4)
def _load_fleet_manifest() -> Optional[tuple[tuple[str, tuple[str, ...]], ...]]:
    for name in ("fleet.json", "agents.json"):
        p = pathlib.Path(name)
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, Mapping):
                    return tuple((str(k), tuple(str(x) for x in v)) for k, v in data.items() if isinstance(v, (list, tuple)))
            except Exception:
                pass
    return None


def suggest_fleet_agent(
    query: str,
    selected_skill: Optional[SkillRecord | str] = None,
    manifest: Optional[Mapping[str, Sequence[str]]] = None,
    default_agent: Optional[str] = "mika",
) -> Optional[str]:
    """Recommend an agent based on profile root ownership, custom manifest, or query intent."""
    # 1. Profile root ownership takes precedence: the agent holding the skill owns execution
    if isinstance(selected_skill, SkillRecord) and selected_skill.root_id.startswith("profile-"):
        return selected_skill.root_id.removeprefix("profile-")

    # 2. Configured manifest, local fleet.json/agents.json, or fallback defaults
    if manifest is not None:
        patterns: Sequence[tuple[str, Sequence[str]]] = tuple(manifest.items())
    else:
        loaded = _load_fleet_manifest()
        patterns = loaded if loaded is not None else _FLEET_AGENT_PATTERNS

    tokens = set(_tokens(query))
    query_lower = str(query).lower()
    skill_name = selected_skill.name if isinstance(selected_skill, SkillRecord) else str(selected_skill or "")
    skill_lower = skill_name.lower()
    if skill_lower:
        tokens.update(_tokens(skill_lower.replace("-", " ").replace("_", " ")))
    for agent, keywords in patterns:
        for kw in keywords:
            if kw in tokens or kw in query_lower or (skill_lower and kw in skill_lower):
                return agent
    return default_agent


_RANK_TABLE_LIMIT = 20


def _rank_table(ranked: Sequence[RankedCandidate]) -> tuple[RankRow, ...]:
    return tuple(
        RankRow(item.skill.name, item.score, item.reasons)
        for item in ranked[:_RANK_TABLE_LIMIT]
    )


# ponytail: cache parsed tokens per skill avoiding string joins and re-tokenization per query
@lru_cache(maxsize=2048)
def _skill_tokens_cache(
    name: str, tags: tuple[str, ...], description: str, aliases: tuple[str, ...] = ()
) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str]]:
    def _field(value: str, morph: bool = True) -> frozenset[str]:
        tokens = set(_tokens(value))
        if morph:
            _add_morph_bases(tokens)
        return frozenset(tokens)

    return (
        # Names stay exact: a base form in a short structured name widens the
        # 0.58-weight field too aggressively (measured false-selection drift).
        _field(name.replace("-", " ").replace("_", " "), morph=False),
        _field(" ".join(tags)),
        _field(description),
        _field(" ".join(aliases)),
    )


@lru_cache(maxsize=32)
def _catalog_duplicate_names(skills: tuple[SkillRecord, ...]) -> frozenset[str]:
    counts = Counter(skill.normalized_name for skill in skills)
    return frozenset(name for name, count in counts.items() if count > 1)


@lru_cache(maxsize=2048)
def _skill_spaceless_ngrams(
    description: str, tags: tuple[str, ...], aliases: tuple[str, ...]
) -> frozenset[str]:
    return _char_ngrams(" ".join((description, " ".join(tags), " ".join(aliases))))


@lru_cache(maxsize=32)
def _catalog_document_frequency(skills: tuple[SkillRecord, ...]) -> tuple[dict[str, int], int]:
    document_frequency: dict[str, int] = {}
    for skill in skills:
        nt, tt, dt, _ = _skill_tokens_cache(skill.name, skill.tags, skill.description, skill.aliases)
        for token in (nt | tt | dt):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    return document_frequency, max(1, len(skills))


@dataclass(frozen=True)
class CatalogIndex:
    """Immutable lookup tables derived from a catalog's skills (built once)."""

    token_postings: Mapping[str, tuple[int, ...]]
    ngram_postings: Mapping[str, tuple[int, ...]]
    alias_phrases: tuple[tuple[str, ...], ...]
    name_norms: tuple[str, ...]
    desc_norms: tuple[str, ...]
    name_to_index: Mapping[str, int]
    synonym_terms: Mapping[str, tuple[str, ...]]


def _build_catalog_index(skills: tuple[SkillRecord, ...]) -> CatalogIndex:
    token_postings: dict[str, set[int]] = {}
    ngram_postings: dict[str, set[int]] = {}
    alias_phrases: list[tuple[str, ...]] = []
    name_norms: list[str] = []
    desc_norms: list[str] = []
    name_to_index: dict[str, int] = {}
    synonym_terms: dict[str, tuple[str, ...]] = {}
    for position, skill in enumerate(skills):
        norm_name = normalize_identifier(skill.name)
        name_norms.append(norm_name)
        name_to_index.setdefault(norm_name, position)
        desc_norms.append(normalize_identifier(skill.description))
        name_tokens, tag_tokens, desc_tokens, cache_alias_tokens = _skill_tokens_cache(
            skill.name, skill.tags, skill.description, skill.aliases
        )
        extra = tuple(DEFAULT_SYNONYMS.get(skill.normalized_name, ()))
        if extra:
            synonym_terms[skill.normalized_name] = extra
        merged_terms = tuple(dict.fromkeys(tuple(skill.aliases) + extra))
        alias_phrases.append(tuple(dict.fromkeys(
            norm for alias in merged_terms if (norm := normalize_identifier(alias))
        )))
        alias_tokens = cache_alias_tokens
        if extra:
            merged_tokens = set(_tokens(" ".join(merged_terms)))
            _add_morph_bases(merged_tokens)
            alias_tokens = cache_alias_tokens | frozenset(merged_tokens)
        for token in name_tokens | tag_tokens | desc_tokens | alias_tokens:
            token_postings.setdefault(token, set()).add(position)
        for gram in _skill_spaceless_ngrams(skill.description, skill.tags, merged_terms):
            ngram_postings.setdefault(gram, set()).add(position)
    return CatalogIndex(
        token_postings={token: tuple(sorted(ids)) for token, ids in token_postings.items()},
        ngram_postings={gram: tuple(sorted(ids)) for gram, ids in ngram_postings.items()},
        alias_phrases=tuple(alias_phrases),
        name_norms=tuple(name_norms),
        desc_norms=tuple(desc_norms),
        name_to_index=name_to_index,
        synonym_terms=synonym_terms,
    )


def _candidate_indices(
    index: CatalogIndex,
    query_tokens: set[str],
    query_ngrams: "frozenset[str]",
    normalized_query: str,
    synonyms: Mapping[str, Sequence[str]],
) -> set[int]:
    """Skill positions that could score above zero for this query.

    Keep this in lockstep with the scoring body in ``_rank``: every signal that
    can add score needs a candidate source here, or a matching skill is pruned.
    """
    candidates: set[int] = set()
    postings = index.token_postings
    for token in query_tokens:
        hit = postings.get(token)
        if hit:
            candidates.update(hit)
    if query_ngrams:
        ngram_postings = index.ngram_postings
        for gram in query_ngrams:
            hit = ngram_postings.get(gram)
            if hit:
                candidates.update(hit)
    if synonyms:
        for name, terms in synonyms.items():
            norm = normalize_identifier(name)
            position = index.name_to_index.get(norm)
            if position is None:
                continue
            if tuple(terms) != index.synonym_terms.get(norm, ()):
                candidates.add(position)
    if not normalized_query:
        return candidates
    if len(candidates) == len(index.name_norms):
        # Everything already matched on tokens: no substring pass can add value.
        return candidates
    for position, norm_name in enumerate(index.name_norms):
        if position in candidates:
            continue
        if norm_name and norm_name in normalized_query:
            candidates.add(position)
            continue
        norm_desc = index.desc_norms[position]
        if norm_desc and (normalized_query in norm_desc or norm_desc in normalized_query):
            candidates.add(position)
            continue
        for phrase in index.alias_phrases[position]:
            if phrase in normalized_query or normalized_query in phrase:
                candidates.add(position)
                break
    return candidates


def _rank(
    catalog: Catalog,
    query: str,
    synonyms: Optional[Mapping[str, Sequence[str]]] = None,
    *,
    exclude: Sequence[str] = (),
    preferred: Optional[str] = None,
    preferred_bonus: float = 0.0,
) -> list[RankedCandidate]:
    synonyms = synonyms or {}
    excluded = normalize_identifiers(exclude)
    preferred_normalized = normalize_identifier(preferred) if preferred else ""
    bonus = max(0.0, float(preferred_bonus))
    query_tokens = set(_tokens(query))
    _add_morph_bases(query_tokens)
    query_ngrams = _char_ngrams(query)
    normalized_query = normalize_identifier(query)
    duplicate_names = _catalog_duplicate_names(tuple(catalog.skills))
    document_frequency, total_documents = _catalog_document_frequency(tuple(catalog.skills))
    token_weights = {
        token: log((total_documents + 1) / (document_frequency.get(token, 0) + 1)) + 1
        for token in query_tokens
    }
    query_weight = sum(token_weights.values()) if query_tokens else 0.0

    candidates = _candidate_indices(
        catalog.index, query_tokens, query_ngrams, normalized_query, synonyms
    )

    has_negation = _query_might_contain_negation(query)
    ranked: list[RankedCandidate] = []

    def _calc_coverage(field_tokens: frozenset[str] | set[str]) -> float:
        matched_weight = sum(token_weights[token] for token in query_tokens if token in field_tokens)
        return (matched_weight / query_weight) if matched_weight else 0.0

    for position in sorted(candidates):
        skill = catalog.skills[position]
        if skill.normalized_name in duplicate_names or skill.normalized_name in excluded:
            continue
        name_tokens, tag_tokens, description_tokens, cached_alias_tokens = _skill_tokens_cache(
            skill.name, skill.tags, skill.description, skill.aliases
        )
        if not synonyms:
            alias_terms = skill.aliases
            alias_tokens = cached_alias_tokens
        else:
            extra = tuple(synonyms.get(skill.normalized_name, ()))
            if not extra:
                alias_terms = skill.aliases
                alias_tokens = cached_alias_tokens
            else:
                alias_terms = tuple(dict.fromkeys(tuple(skill.aliases) + extra))
                alias_tokens = frozenset(_tokens(" ".join(alias_terms)))
        if has_negation and _skill_is_negated(skill.name, query, alias_terms, normalized_query):
            continue
        reasons: list[str] = []
        score = 0.0

        # Only computed when the query itself contains a spaceless script, so
        # spaced-language turns pay nothing for this path.
        if query_ngrams:
            field_ngrams = _skill_spaceless_ngrams(skill.description, skill.tags, alias_terms)
            dice = _ngram_dice(query_ngrams, field_ngrams)
            if dice >= _NGRAM_MINIMUM_DICE:
                score += _NGRAM_WEIGHT * dice
                reasons.append("spaceless_script_overlap")

        if _identifier_in_query(skill.name, query, normalized_query):
            score += 0.92
            reasons.append("exact_name_in_query")

        normalized_description = normalize_identifier(skill.description)
        if _phrase_present(normalized_description, normalized_query) or _phrase_present(
            normalized_query, normalized_description
        ):
            score += 0.76
            reasons.append("description_phrase")

        for alias in alias_terms:
            normalized_alias = normalize_identifier(alias)
            if normalized_alias and (
                _phrase_present(normalized_alias, normalized_query)
                or _phrase_present(normalized_query, normalized_alias)
            ):
                score += 0.74
                reasons.append("alias_phrase")
                break

        if query_tokens and query_weight:
            name_coverage = _calc_coverage(name_tokens)
            tag_coverage = _calc_coverage(tag_tokens)
            description_coverage = _calc_coverage(description_tokens)
            alias_coverage = _calc_coverage(alias_tokens) if alias_terms else 0.0
            if name_coverage:
                score += 0.58 * name_coverage
                reasons.append("name_terms")
            if tag_coverage:
                score += 0.22 * tag_coverage
                reasons.append("tag_terms")
            if alias_coverage:
                score += 0.22 * alias_coverage
                reasons.append("alias_terms")
            if description_coverage:
                score += 0.34 * description_coverage
                reasons.append("description_terms")

        # A focused skill only wins when it is already a plausible candidate:
        # focus nudges the ranking, it never hijacks an unrelated turn.
        if bonus and score > 0 and skill.normalized_name == preferred_normalized:
            score += bonus
            reasons.append("dialogue_focus")

        if score > 0:
            ranked.append(RankedCandidate(skill, round(min(1.0, score), 6), tuple(reasons)))
    ranked.sort(key=lambda item: (-item.score, item.skill.skill_id, item.skill.relative_path))
    return ranked


def _resolve_companions(skill: SkillRecord, catalog: Catalog) -> tuple[str, ...]:
    if not skill.related_skills:
        return ()
    known_names = {s.normalized_name for s in catalog.skills}
    valid = []
    for rel in skill.related_skills:
        norm = normalize_identifier(rel)
        if norm in known_names and norm != skill.normalized_name:
            valid.append(rel)
    return tuple(valid)


_VECTOR_BYPASS_SCORE = 0.55


_VECTOR_BYPASS_MARGIN = 0.18


def select_skill(
    catalog: Catalog,
    query: str,
    *,
    explicit_names: Sequence[str] = (),
    min_score: float = 0.28,
    min_margin: float = 0.05,
    limit: int = 3,
    synonyms: Optional[Mapping[str, Sequence[str]]] = None,
    exclude: Sequence[str] = (),
    preferred: Optional[str] = None,
    preferred_bonus: float = 0.0,
    vector_index: Optional[VectorIndex] = None,
) -> Selection:
    """Choose one skill conservatively or return a reason for not choosing."""
    if not 0 <= float(min_score) <= 1:
        raise ValueError("min_score must be between 0 and 1")
    if not 0 <= float(min_margin) <= 1:
        raise ValueError("min_margin must be between 0 and 1")
    if isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")

    query = str(query or "")
    unique_explicit: list[str] = []
    seen_explicit: set[str] = set()
    for name in explicit_names:
        normalized = normalize_identifier(name)
        if normalized and normalized not in seen_explicit:
            seen_explicit.add(normalized)
            unique_explicit.append(str(name))
    effective_synonyms = DEFAULT_SYNONYMS if synonyms is None else synonyms

    # A vetoed explicit name ("don't use $X") is not a request: drop it and
    # fall through to lexical ranking instead of selecting it.
    requested = [name for name in unique_explicit if not _skill_is_negated(name, query)]
    if len(requested) > 1:
        return Selection("blocked", "multiple_explicit_skills", None, (), True, True, suggested_agent=suggest_fleet_agent(query, None))
    if requested:
        matches = catalog.by_name(requested[0])
        if not matches:
            return Selection("blocked", "unknown_explicit_skill", None, (), True, True, suggested_agent=suggest_fleet_agent(query, None))
        if len(matches) > 1:
            return Selection("blocked", "ambiguous_explicit_skill", None, (), True, True, suggested_agent=suggest_fleet_agent(query, None))
        companions = _resolve_companions(matches[0], catalog)
        candidate = RankedCandidate(matches[0], 1.0, ("explicit_name",), companions=companions)
        return Selection("selected", "explicit_skill", candidate, (candidate,), True, True, suggested_agent=suggest_fleet_agent(query, matches[0]))
    if unique_explicit and not requested:
        # Every explicit name was vetoed: lexical ranking (which also skips
        # vetoed skills) decides; an explicit-only veto must not select.
        ranked_vetoed = _rank(
            catalog, query, effective_synonyms, exclude=exclude, preferred=preferred, preferred_bonus=preferred_bonus
        )
        return Selection(
            "no_match",
            "negated_skill",
            None,
            tuple(ranked_vetoed[:limit]),
            False,
            False,
            _rank_table(ranked_vetoed),
            suggested_agent=suggest_fleet_agent(query, None),
        )

    ranked = _rank(
        catalog, query, effective_synonyms, exclude=exclude, preferred=preferred, preferred_bonus=preferred_bonus
    )

    if vector_index and vector_index._memory_vectors:
        if (
            ranked
            and ranked[0].score >= _VECTOR_BYPASS_SCORE
            and (len(ranked) == 1 or (ranked[0].score - ranked[1].score) >= _VECTOR_BYPASS_MARGIN)
        ):
            # Lexical scoring is already decisive: skip the embedding work entirely.
            recorder = getattr(vector_index, "record_lexical_fastpath", None)
            if callable(recorder):
                recorder()
            vec_ranked = []
        else:
            vec_ranked = vector_index.rank(query, top_k=20)
        if vec_ranked:
            k_rrf = 60.0
            has_negation = _query_might_contain_negation(query)
            normalized_query = normalize_identifier(query)
            lex_map = {c.skill.name: idx for idx, c in enumerate(ranked, 1)}
            vec_map = {name: idx for idx, (name, _) in enumerate(vec_ranked, 1)}
            max_vec_sim = vec_ranked[0][1] if vec_ranked else 0.0

            if max_vec_sim < 0.145 and (not ranked or ranked[0].score < float(min_score)):
                table = _rank_table(ranked)
                return Selection("no_match", "below_threshold", None, tuple(ranked[:limit]), False, False, table, suggested_agent=suggest_fleet_agent(query, None))

            rrf_scores: dict[str, float] = {}
            skill_by_name = {s.name: s for s in catalog.skills}
            cand_names = set(lex_map.keys()) | set(vec_map.keys())
            excluded_names = normalize_identifiers(exclude)
            for name in cand_names:
                sk = skill_by_name.get(name)
                if not sk or normalize_identifier(name) in excluded_names:
                    continue
                if has_negation and _skill_is_negated(
                    sk.name,
                    query,
                    tuple(sk.aliases) + tuple(effective_synonyms.get(sk.normalized_name, ())),
                    normalized_query,
                ):
                    continue
                sc = 0.0
                if name in lex_map:
                    sc += 1.0 / (k_rrf + lex_map[name])
                if name in vec_map:
                    sc += 1.0 / (k_rrf + vec_map[name])
                rrf_scores[name] = sc

            sorted_rrf = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
            if sorted_rrf:
                new_ranked: list[RankedCandidate] = []
                for name, score in sorted_rrf:
                    sk = skill_by_name[name]
                    reasons: list[str] = ["rrf_hybrid"]
                    if name in lex_map:
                        reasons.append("lexical")
                    if name in vec_map:
                        reasons.append("vector")
                    companions = _resolve_companions(sk, catalog)
                    new_ranked.append(RankedCandidate(sk, round(score * 30.0, 4), tuple(reasons), companions=companions))
                ranked = new_ranked

    table = _rank_table(ranked)
    if not ranked or ranked[0].score < float(min_score):
        if _query_might_contain_negation(query) and any(
            _skill_is_negated(skill.name, query, tuple(skill.aliases) + tuple(effective_synonyms.get(skill.normalized_name, ())))
            for skill in catalog.skills
        ):
            return Selection("no_match", "negated_skill", None, tuple(ranked[:limit]), False, False, table, suggested_agent=suggest_fleet_agent(query, None))
        return Selection("no_match", "below_threshold", None, tuple(ranked[:limit]), False, False, table, suggested_agent=suggest_fleet_agent(query, None))
    if len(ranked) > 1 and ranked[0].score - ranked[1].score < float(min_margin):
        return Selection("ambiguous", "insufficient_margin", None, tuple(ranked[:limit]), False, False, table, suggested_agent=suggest_fleet_agent(query, None))
    top = ranked[0]
    companions = _resolve_companions(top.skill, catalog)
    top_with_companions = replace(top, companions=companions)
    candidates = (top_with_companions,) + tuple(ranked[1:limit])
    return Selection("selected", "lexical_match", top_with_companions, candidates, False, False, table, suggested_agent=suggest_fleet_agent(query, top.skill))
