# Changelog

All notable changes to Skill Proof are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.14.0] - 2026-10-09

### Fixed
- **Strict Disabled Skills Isolation (P0 Security Guard)**:
  - Added `load_disabled_skills()` reading host `config.yaml` (`skills.disabled`) with PyYAML fast-path and robust stdlib regex fallback.
  - `scan_catalog()` automatically excludes all 64 disabled skills at the catalog level.
  - Verified with live test probes: disabled utilities (`pdf`, `xlsx`, `docx`, `powerpoint`) can never win selection or leak into prompt nudges.
- **Alias-Aware Negation Veto (P1.1 Accuracy Hardening)**:
  - Updated `_skill_is_negated()` and `_rank()` to evaluate all skill aliases and synonyms.
  - Queries containing vetoes against aliases (e.g. *"ไม่เอา TDD"*, *"don't use TDD"*) now cleanly veto `test-driven-development` and gracefully fall back to bug-checking or syntax tools.

### Added
- **Built-in Synonym Graph & Colloquial Vocabulary**:
  - Added `DEFAULT_SYNONYMS` connecting conversational Thai and English phrasing directly to canonical skills:
    - Colloquial debugging (`"โค้ดไพธอน"`, `"traceback"`, `"ส่องที"`) -> `python-debugpy`, `systematic-debugging`
    - Extreme boundary stress testing (`"เคสพิสดาร"`, `"boundary โหดๆ"`, `"หาจุดพัง"`) -> `edge-case-sadist`, `adversarial-boundary-testing`
    - Parallel subagent delegation (`"กระจายงาน"`, `"subagent ขนาน"`, `"ขนานกัน"`) -> `superpowers-dispatching-parallel-agents`
    - GitHub PR review (`"รีวิว pull request"`, `"ตรวจ pr"`) -> `github-code-review`, `github-pr-workflow`
- **Multi-Root Catalog Configuration & Hermes Runtime Wiring**:
  - Configured `skill_roots` in Hermes `config.yaml` to index workspace (`.agents/skills`), user agent store (`~/.agents/skills`), and host skills (`~/.hermes/skills`).
  - Live catalog indexes 136 active skills with 0 token prompt overhead.
- **New Regression Tests**:
  - Added `test_disabled_skills_excluded_from_scan` and `test_negation_vetoes_skill_via_alias` to `tests/test_core.py`.

### Performance & Measured Benchmark
- **2,661x Faster Selection Latency vs Prompt-Only LLM Routing**:
  - In-process warm select runs at **0.58 ms median (1,724 QPS)**; live end-to-end hook runs at **3.47 ms median** (vs 10,692.8 ms model-based selection).
  - Eliminates ~4,050 prompt tokens per turn from the system prompt.
  - 10/10 hard simulation scenarios pass 100.0% (anti-slop UI, negative veto, multi-domain slides, colloquial Thai debugging, extreme boundary stress testing).

### Validation
- 299 tests passing (100% pass, 0 failures, 2 skipped on Windows symlinks).
- 58/58 benchmark gate cases passing (100% precision, 100% recall, 0 false selections).

## [0.13.0] - 2026-10-09

### Added
- **Global Multilingual Vocabulary & SOV Negation Veto**:
  - Stopwords, Negation Vetoes, and Dialogue Phrases expanded across German, French, Spanish, Japanese, Korean, Vietnamese, Chinese, Hindi, and Thai.
  - Added Subject-Object-Verb (SOV) post-negation pattern recognition for Japanese (`〜使わないで`, `〜不要`) and Korean (`〜하지마`, `〜쓰지마`).
  - Thai fleet specialist routing in `suggest_fleet_agent()` (`"ตรวจโค้ด"` -> Altima, `"เขียนโค้ด/แก้บั๊ก"` -> Sora, `"ค้นคว้า/วิจัย"` -> Nua, `"ออกแบบ/หน้าเว็บ"` -> Milim).

### Performance (Ponytail Ultra)
- **Sub-millisecond Routing Latency & 1,500+ QPS**:
  - Latency reduced from 95.1 ms to **0.663 ms/query** (**143.6x speedup** vs baseline).
  - Throughput raised to **1,508.1 queries/sec** across the full local catalog of 137 skills.
  - Eliminated 3.6 million redundant function calls via substring fast-paths in `_skill_is_negated` and pre-cached token sets in `_skill_tokens_cache`.
  - Zero-allocation set union optimization in `overlap_report` using inclusion-exclusion principle.
  - Test suite runtime cut in half from 14.8s to 6.6s.

### Validation
- 295 tests passing (100%).
- 58/58 benchmark gate cases passing (100% precision, 100% recall, 0 false selections).

## [0.12.0] - 2026-10-09

### Added
- **Global Use Wave 1 — Universal Multilingual Tokenization & Script Support**:
  - `_tokens()` now groups Unicode Letter (`\p{L}`), Mark (`\p{M}`), and Number (`\p{N}`) characters into coherent tokens rather than shredding words on combining marks. Matras, virama, and diacritics in Indic scripts (Devanagari/Hindi, Bengali, Tamil, etc.), Thai, and accented text now retain their full word integrity.
  - Conversational stopword vocabulary expanded to cover Chinese, Hindi, Korean, Japanese, Thai, and European common fillers.
  - Multilingual negation recognition added for Chinese (`不要用`, `别用`, `不用`, `禁止`), Hindi (`मत`, `नहीं`, `बिना`), Japanese (`使わない`), Korean (`하지마`), and Thai (`ไม่ต้อง`, `ห้าม`, `อย่า`).
- **Expanded Host Bridges & Smart Auto-Detection (`bridge.py` & `cli.py bridge`)**:
  - Added support for Google Gemini / Antigravity (`GEMINI.md`), Cline (`.clinerules`), Windsurf (`.windsurfrules`), and GitHub Copilot (`.github/copilot-instructions.md`).
  - Added `detect_host_environments()` for smart automatic detection of the host workspace.
  - Added one-command auto-installation (`python bridge.py install` / `python cli.py bridge install`) defaulting to `--host auto`.
  - Added `python bridge.py ai-setup` / `python cli.py bridge ai-setup` for deterministic AI agent bootstrapping.
- **Folder State & Unreadable File Resilience**:
  - Target project directories for bridges are automatically created with parent directories.
  - Invalid UTF-8 or corrupt binary skill files in the catalog emit clean diagnostics (`invalid_utf8`) without crashing the scan or selection engine.

### Validation
- 288 tests passing (2 skipped on Windows symlinks).
- 58/58 gated routing benchmark cases (100% recall, 100% precision, 0 false selections).

## [0.11.0] - 2026-10-09

### Added
- **Fleet Profile Awareness & Subagent Routing**: `detect_agent_roots` and `cli.py` accept `--profile <name>` (e.g. `altima`, `sora`, `nua`, `milim`), prioritizing that Hermes profile's dedicated skills directory (`~/.hermes/profiles/<name>/skills`). `suggest_fleet_agent()` infers the appropriate specialist agent (`altima` for review/audit, `sora` for code/debugging/build, `nua` for research/evidence, `milim` for UI/design, `mika` for orchestration) and attaches `suggested_agent` to `SelectedSkill` and JSON payloads.
- **Companion Skill Chaining**: `SkillRecord` parses `related_skills` / `related` from frontmatter; `select_skill` attaches validated companion skills (`companions`) present in the catalog to the selection without violating the single-primary-skill invariant.
- **Interactive Query Diagnostics (`cli.py eval`)**: Added `python cli.py eval "<query>"` command showing query token breakdown, CJK/Thai character n-grams count, decision reason, suggested fleet agent, companion skills, and top-N candidate scoring breakdown.

### Validation
- 279 tests passing (2 skipped on Windows), 58/58 gated routing cases (100% recall, 100% precision, 0 false selections).

## [0.10.1] - 2026-10-09

### Fixed
- **Mixed Spaceless-Spaced Query Routing**: `_tokens` excludes tokens containing spaceless characters (Thai, Lao, Myanmar, Khmer, CJK, Hangul) so that fragmented splits caused by combining characters/tone marks do not inflate the IDF `query_weight` denominator for Latin/spaced keywords. Mixed queries such as `"ช่วยทำ tdd ในโปรเจกต์นี้ให้หน่อย"` now route cleanly to the intended skill (`python-tdd` / `test-driven-development`).
- **Multi-Agent Root Detection**: `detect_agent_roots` now detects `~/.codex/skills` (OpenAI Codex format) and `~/.gemini/config/skills`, and excludes symlinked directory candidates (`not path.is_symlink()`) so roots like `antigravity` are no longer rejected with `unsafe_root`.
- **Host Bridge (`bridge.py`)**: Fixed syntax error in Cursor rule generation; added `tests/test_bridge.py` test suite covering Claude Code (`PreToolUse` hook), Codex (`AGENTS.md`), and Cursor (`.cursor/rules`).
- **Audit Isolation & Budget Fallback**: Hardened audit file isolation across test fixtures, context budget fallback for oversize turns, and spaced skill name parsing (`Skill Factory`).

### Validation
- 273 tests passing (2 skipped), 58/58 gated routing cases (100% recall, 100% precision, 0 false selections), doctor OK as standalone with 7 hooks. 0 invalid frontmatter across 146 skills from 5 roots.

## [0.10.0] - 2026-10-08

### Added
- **Overlap and Pruning Report**: `overlap_report()` compares every skill pair by Jaccard similarity over name, description, tag, and alias tokens (pure vocabulary overlap, no embeddings, no network) and returns the pairs above a threshold with the shared terms and a recommendation. Exact copies never surface here because the catalog already collapsed them (`alias_skipped`) or shadowed them (`shadowed_by_root`); both counts are included in the report.
- **`cli.py overlap`**: `--min-similarity` (default 0.4), `--limit`, `--json`, `--root`; plain text lists both sides, their roots, the shared vocabulary, and the recommendation. Invalid thresholds exit 2.
- **`/skill-proof overlap`**: the same report inside the session, annotated with usage from the audit log — how often the host loaded each side and which one to consider dropping. Without an audit log it says so instead of guessing.

### Validation
- 261 tests passing (2 skipped), 48/48 gated routing cases, Hermes plugin doctor OK with 7 hooks registered. `tests/test_v0100.py` (19 cases) covers identical descriptions, threshold filtering, pair counts, sorting and limits, the truncated flag, determinism, argument validation, exact-copy collapse across roots, the CLI surface, and the plugin command including drop-candidate selection from usage.
- Live measurement on the real 145-skill catalog: 10,440 pairs checked in 59 ms, 3 pairs at `>= 0.4` (`claude-code`/`grok`/`opencode`), 6 at `>= 0.31`, 0 identical descriptions, 17 exact copies already collapsed, 12 divergent copies shadowed.

## [0.9.0] - 2026-10-08

### Added
- **Override Capture**: a skill the host loads while Skill Proof selected something else (or had nothing selected) is recorded as `evidence.override_loaded` instead of being dropped. It never sets `hermes_loaded_event` and never satisfies compliance: a load we did not select is an override, not proof of our decision.
- **`/skill-proof stats [--json]`**: aggregates the append-only audit log into outcomes — turns, routed, loaded, miss, focus fallback, abstained, overridden, vetoed, a per-skill hit-rate table, and the override list. `hit_rate = loaded / (routed - fallback)`, so focus carries (score 0) are never counted as routing wins. Unreadable audit lines are counted and reported rather than silently ignored.

### Validation
- 242 tests passing (2 skipped), 48/48 gated routing cases, Hermes plugin doctor OK with 7 hooks registered. `tests/test_v090.py` (16 cases) covers override capture in every position (matching load, mismatched load, load while abstained, unknown turn), the aggregation math including fallback exclusion and null hit rates, corrupt-line handling, and the command surface.

## [0.8.0] - 2026-10-08

### Added
- **`/skill-proof why <skill>`**: answers "why (not) this skill?" against the current turn. Verdicts are `selected`, `ranked`/`below_threshold` (exact score, rank, threshold, gap to the top, and the terms that matched), `vetoed`, `no_signal`, `ranked_below_cap`, `unknown_skill`, and `not_ranked` for turns decided by explicit syntax. It is built from a bounded rank table and the veto list recorded at turn time, so no prompt is ever read or returned.
- **Append-Only Decision Audit**: `audit_log` (default `true`) appends one JSON line per turn to `plugin-data/skill-proof/audit.jsonl` (override with `audit_path`). The record carries derived numbers only: decision, selected skill and score, focus, load and compliance state, errors, catalog size, timing, and `query_sha256`; session ids are hashed. `audit_limit` (default 500) rotates by keeping the tail. A broken audit path stops logging instead of failing a turn. `health` reports the configuration and the last three records.
- **Turn Rank Table**: lexical turns keep the top 20 non-zero scores (`rank_table`) in turn state, which is what makes an exact answer possible without storing the prompt.

### Validation
- 226 tests passing (2 skipped), 48/48 gated routing cases, Hermes plugin doctor OK with 7 hooks registered. `tests/test_v080.py` covers every explain verdict (including `not_listed_by_host`, which separates "not indexed" from "excluded by the host listing"), the prompt-free guarantee, argument parsing for the six commands, audit line shape, rotation, the broken-path fallback, and health reporting.

## [0.7.0] - 2026-10-08

### Added
- **Session Skill History**: each session keeps the skills it selected, so a follow-up that only says *use the same skill*, *the same one*, *same as before*, *เหมือนเดิม*, or *ตัวเดิม* resolves to the last skill with reason `dialogue_reference`. Asking for *the previous one* / *อันที่แล้ว* walks one step back (`dialogue_reference_previous`). With no history the request fails closed as `no_previous_selection` instead of guessing.
- **Bounded Focus**: *keep using X* / *stick with X* / *ใช้ต่อไป* holds `X` as the session focus for `focus_turns` turns (default 5, `0` means until released). Focus is a decaying, bounded score bonus that only applies to a skill that already has lexical signal: it decides close calls, never beats a competing match, a veto, or an ambiguity, and expires by itself. When nothing else matches it still carries `X` with `focus_fallback` (score 0) and a context line that says it is a fallback. *stop using X* / *เลิกใช้ X* releases it and routes the rest of the sentence ("stop using python-tdd and design a landing page" still selects `frontend-design`).
- **Evidence and Surfaces**: receipts carry `evidence.session` (`history`, `focus`, `focus_remaining`, `reference`), `status`/`explain` show focus and reference lines, `health` reports `session`, and the compact footer shows `focus <name> (<n> turns)`.
- **Config**: `session_memory` (default `true`) and `focus_turns` (default 5, range 0-50). `on_session_end` drops the memory immediately, so history never crosses sessions.

### Fixed
- **NFKC and Thai SARA AM**: dialogue phrases are now normalized identically to queries. NFKC rewrites `ำ` (U+0E33) into `ํา`, so a raw phrase such as `ทำต่อด้วย` never matched a normalized query.
- **Unknown Tokens as Skill Names**: a token after "skill" ("stop using the skill now") is no longer treated as a skill name, so it can neither trigger nor block a release.
- **Hyphen Identifier Boundaries**: `frontend-design` no longer matches inside `frontend-design-pro`; identifiers now stop at `-` as well as at word boundaries, matching what the negation patterns already did.

### Validation
- 199 tests passing (2 skipped), 48/48 gated routing cases, Hermes plugin doctor OK with 7 hooks registered. Focus, expiry, release, reference, and fallback behavior are covered by `tests/test_v070.py` (48 cases).

## [0.6.0] - 2026-10-08

### Added
- **Gated Routing Quality Harness**: `routing_benchmark.py` now evaluates a labeled corpus of 53 cases (14 skills) split into tiers. `core` cases are gated, `stretch` cases are measured and reported as documented gaps of the lexical-only design. The report carries recall (expected selections only), precision, `abstain_accuracy`, false selections, misses, per-category and per-tier breakdowns, and the top candidates behind every failure. `--gate` exits non-zero, `--sweep` prints the `min_score` x `min_margin` sensitivity table, `--json` writes a machine-readable report.
- **Abstain Expectations in the Corpus**: a case may declare `expected: null` to require a refusal, so "select the wrong skill" and "select anything at all" are both graded. The gate defaults are recall >= 0.95, false selections <= 0, abstain accuracy >= 0.9.
- **Dictionary-Free N-gram Matching for Spaceless Scripts**: queries in Thai, Lao, Myanmar, Khmer, CJK, or Hangul are compared against descriptions, tags, and aliases with character bigram overlap (Dice). This is the only workable signal when a script has no word boundaries to tokenize, and it is skipped entirely for spaced-script turns, so Latin routing pays nothing.

### Changed
- The shipped corpus replaced the 4-skill, 12-case fixture with 53 labeled cases across explicit names, English paraphrases, Thai, aliases, negation, abstain, and near-duplicate ambiguity. The old shape still loads unchanged.

### Validation
- 151 tests passing (2 skipped), 48/48 gated routing cases passing (0 false selections, abstain accuracy 1.0), stretch tier 0/5 passing and reported as documented gaps. Hermes plugin doctor OK with 7 hooks registered.
- Threshold sweep on the shipped corpus: recall 1.0 holds for `min_score` 0.20-0.32 with `min_margin` >= 0.05, and `min_margin: 0` silently tie-breaks the two near-duplicate cases (2 false selections). Shipped defaults (0.28 / 0.05) sit inside that plateau.
- N-gram path measured on the real 145-skill catalog: 12-14 ms per selection (about 1 ms added over a Latin query), scan unchanged at about 546 ms.

## [0.5.0] - 2026-10-08

### Added
- **Hub Provenance and Drift Evidence**: When the local Hermes hub lock exists, a loaded skill's receipt now carries `trust_level`, `scan_verdict`, pinned `source_revision`, and a `bundle` verdict (`match`, `modified`, or `unknown`). The bundle hash reproduces the hub's `skills-guard` algorithm (sha256 over sorted `name\\0content` pairs, verified against live lock entries), so drift is detected without trusting the lock blindly.
- **Aliases and Synonyms**: Skills can declare `aliases:` in frontmatter (inline or block list), and `synonyms` can map skill names to extra terms in config. Matching is language-agnostic: spaceless scripts (Thai, Lao, Myanmar, Khmer, CJK, Hangul) match by substring, spaced scripts keep token boundaries. Aliases score below exact names and above tag terms.
- **Candidate Hints on Ambiguity**: An ambiguous turn now injects the bounded candidate names and asks which skill to load instead of staying silent.
- **Canonical Root Suggestions**: `suggest_roots()` resolves junction/symlink facades and reports which target directories should be added to `skill_roots`; `/skill-proof health` includes `hub` status and `root_suggestions`.
- **Portable CLI (`cli.py`)**: `roots`, `scan`, and `select --query` over any SKILL.md directory, with agent-root auto-detection (project and user `.agents/skills`, Claude Code, Gemini, Antigravity, Cursor, OpenCode, Copilot, Windsurf, Kilo, Hermes).
- **stdio MCP Server (`mcp_server.py`)**: `skill_roots`, `skill_scan`, and `skill_select` tools over newline-delimited JSON-RPC for MCP-capable hosts (Claude Code, Codex, Antigravity, Cursor, ...). Stdlib only, offline.
- **Single version source**: `core.__version__` is the authority; the plugin health command and manifest are checked against it by tests.

### Validation
- 119 tests passing (2 skipped), 12/12 routing benchmark cases passing, Hermes plugin doctor OK with 7 hooks registered.
- Real-machine checks: 145 skills indexed across detected roots; hub bundle `match` for `caveman`/`blackbox` and `modified` for `adversarial-ux-test`; root suggestion pointed at the canonical `~/.agents/skills` store.

## [0.4.2] - 2026-10-08

### Added
- **Reparse-Point Classification**: Directory symlinks, Windows junctions, and other reparse points are pruned with a dedicated `unsafe_reparse` diagnostic instead of being walked into and rejected later as the generic `unsafe_path`.
- **Cross-Root Precedence**: When several `skill_roots` index the same skill name, the first configured root wins. Byte-identical copies collapse as `alias_skipped` (a junction facade plus its physical store); divergent copies are reported as `shadowed_by_root`. Same-root name collisions remain ambiguous `duplicate_name`.
- **`listed_but_unindexed` Selection Reason**: An explicit `$name` that Hermes lists but no configured root indexes is no longer reported as `unknown_explicit_skill`; the receipt also carries `host_unindexed_count`, `host_unindexed_sample`, and `host_unindexed_omitted`.

### Fixed
- **Negation Window**: English phrase vetoes such as `don't use the skill X`, `do not use the skill X`, and `never use the skill X` are vetoed instead of being selected as explicit requests. Non-veto phrasings such as `do not forget to use X` are no longer misread as vetoes.
- **Block-List Tags**: `tags:` written as a YAML block list now parses instead of being silently discarded.

### Performance
- Veto and identifier regexes are compiled once per skill name (`lru_cache`) instead of per call; measured selection time on a 138-skill catalog dropped from about 372 ms to about 12 ms per turn.

### Validation
- 89 tests passing (2 skipped), 12/12 routing benchmark cases passing, Hermes plugin doctor OK with 7 hooks registered.

## [0.4.1] - 2026-10-07

### Added
- **Negation Guard**: Veto phrases such as `don't use X`, `without X`, `ไม่เอา X`, or `ไม่ใช้ X` exclude the referenced skill from candidate ranking. Vetoed explicit `$name` falls back to lexical ranking; fully vetoed turns report `no_match/negated_skill`.
- **Token-Boundary Phrase Matching**: Short queries (e.g. `ui`) no longer falsely match inside longer words like `build`. Thai spaceless text preserves substring matching.
- **Live Listing Check Gate**: Lexical matches outside the live listing resolve deterministically to `not_in_hermes_list` with no selection.

### Fixed
- **Health Diagnostics Version**: Corrected plugin version reporting in `/skill-proof health` output to match manifest (0.4.1).

### Validation
- 73 tests passing (2 skipped), 12/12 routing benchmark cases passing, Hermes plugin doctor OK with 7 hooks registered.

## [0.4.0] - 2026-10-06

### Added
- **Invariant Compliance Verification**: Support for `required_tools`, `forbidden_tools`, and `ordered_tools` declarations in `SKILL.md` frontmatter and `config.yaml`.
- Violations fail compliance with a typed reason; `enforce-tools` mode blocks operational tools outright upon violation.

## [0.3.1] - 2026-10-05

### Fixed
- Fixed Thai conjoined-skill detection and action-continuation false matches.

## [0.3.0] - 2026-10-04

### Added
- `/skill-proof health` diagnostics command with per-hook timing and catalog status.
- Support for YAML block-scalar frontmatter in `SKILL.md`.
