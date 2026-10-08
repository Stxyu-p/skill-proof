# Changelog

All notable changes to Skill Proof are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
