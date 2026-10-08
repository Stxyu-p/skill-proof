# Changelog

All notable changes to Skill Proof are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
