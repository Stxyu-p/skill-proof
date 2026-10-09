<div align="center">

# Skill Proof

**Deterministic, auditable skill routing with no model call in the ranking path.**

Python 3.11+ | Standard library only | Offline

[![Release](https://img.shields.io/badge/release-v0.13.0-315C4B?style=for-the-badge)](https://github.com/Stxyu-p/skill-proof/releases)
[![License](https://img.shields.io/github/license/Stxyu-p/skill-proof?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge)](https://docs.python.org/3/)

[![Tests](https://img.shields.io/badge/tests-296%20run%2C%202%20skipped-2E7D32?style=flat-square)](CHANGELOG.md)
[![Routing gate](https://img.shields.io/badge/routing-58%2F58-2E7D32?style=flat-square)](routing_cases.json)
[![Dependencies](https://img.shields.io/badge/dependencies-0-2E7D32?style=flat-square)](plugin.yaml)
[![Hooks](https://img.shields.io/badge/Hermes%20hooks-7-00739C?style=flat-square)](plugin.yaml)

</div>

---

## Overview

Skill Proof answers one question with evidence: **which skill best matches this turn, and why?**

It indexes existing `SKILL.md` files, ranks them against the request with a deterministic lexical scorer, and abstains when the match is weak or ambiguous. As a Hermes plugin, it also observes skill-load and tool events so the selection can be compared with what the host actually did.

The ranking path makes no model call and needs no network service. Decisions are reproducible from the same catalog, query, and settings.

```text
$ python cli.py select --query "use tdd skill"
decision: selected (lexical_match)
suggested fleet agent: sora
selected: test-driven-development score=0.56 reasons=tag_terms,description_terms companions=systematic-debugging
candidate: test-driven-development score=0.56 reasons=tag_terms,description_terms
```

```text
$ python routing_benchmark.py --gate
cases:         58 (35 expect a skill, 23 expect abstain)
results:       recall=1.0  precision=1.0  abstain_accuracy=1.0  decision_accuracy=1.0
errors:        false_selections=0  misses=0
gate: PASS
```

The routing results are from a synthetic test corpus, not a production accuracy claim.

---

## Capabilities

| Capability | What it does |
| :--- | :--- |
| Skill discovery | Scans configured roots for `SKILL.md`; can auto-detect common Hermes, Claude, Codex, Gemini/Antigravity, OpenCode, Copilot, Cursor, Windsurf, and Kilo locations |
| Deterministic ranking | Weights matching terms across skill name, tags, description, and aliases; uses character n-grams for scripts without spaces |
| Explicit skill requests | Resolves explicit skill names against the catalog; unknown, duplicate, or multiple names are not silently guessed |
| Abstention | Applies minimum-score and score-margin thresholds to lexical matches; returns a reason when no selection is safe |
| Negation | Vetoes supported English, Thai, Chinese, Hindi, Japanese, Korean, Vietnamese, German, French, and Spanish forms; includes Japanese and Korean post-negation forms |
| Session references | Resolves phrases such as “use the same skill”, “keep using X”, and `อันที่แล้ว` against this session's bounded skill history |
| Companion skills | Reports `related_skills` that exist in the active catalog; companions do not become the primary selection |
| Live availability | When Hermes exposes its skill listing, filters out locally indexed skills that are not available to the host |
| Evidence receipts | Records decision reasons, hashes, lifecycle events, and bounded tool evidence without storing prompt text or skill bodies |
| Invariant checks | Observes required tools and checks forbidden or ordered tools; `enforce-tools` can block forbidden and out-of-order calls |
| Overlap report | Measures vocabulary overlap to help find possible near-duplicates |
| Fleet suggestion | Returns an advisory agent name; it does not dispatch work |
| CLI and MCP | Provides the same local selection engine through a command-line interface and a stdio MCP server |
| Host bridges | Optional setup adapters for Claude Code, Codex, Gemini, Cursor, Cline, Windsurf, and Copilot |

### Decision rules

1. Resolve an explicit skill name if it exists in the catalog.
2. Resolve a dialogue reference only when this session has matching skill history.
3. Otherwise rank the catalog lexically using weighted term overlap and character n-grams.
4. Remove vetoed skills before selection.
5. A lexical match must clear the configured score and margin thresholds. Otherwise Skill Proof abstains with a named reason. An explicit known skill request is handled as an explicit selection rather than a lexical match.

### Fleet suggestions

A selected skill from a `profile-<name>` root maps to that profile. Otherwise, Skill Proof checks an explicit manifest or a local `fleet.json` / `agents.json`, then uses built-in intent patterns. If none match, the default suggestion is `mika`. These names are suggestions only: Skill Proof does not call, launch, or dispatch agents.

When a role changes, a manifest can describe the new intent mapping. Profile-root ownership applies only to skills indexed from that profile; shared-root skills use the manifest or fallback patterns.

---

## Modes

| Mode | Behaviour |
| :--- | :--- |
| `observe` | Reports a candidate without asking the model to load it |
| `nudge` | Default. Requests the selected skill be loaded before operational tools; does not block tools |
| `enforce-tools` | Blocks operational tools until a selected skill load is observed; blocks configured forbidden-tool and tool-order violations. `enforce` is an alias |

`required_tools` are checked when compliance is evaluated; they are not a pre-call blocking rule.

---

## Interfaces

### Hermes commands

| Command | Purpose |
| :--- | :--- |
| `/skill-proof status` | Compact state for the latest turn |
| `/skill-proof explain` | Decision and ranked candidates |
| `/skill-proof why <name>` | Why a skill won, lost, or was vetoed |
| `/skill-proof stats` | Hit rate, misses, and overrides from the audit log |
| `/skill-proof overlap` | Near-duplicate report |
| `/skill-proof trace` | Full bounded JSON receipt |
| `/skill-proof refresh` | Reread local skill content on the next turn |
| `/skill-proof health` | Hook activity, catalog diagnostics, and timing |

### CLI

Run from the Skill Proof directory:

| Command | Purpose |
| :--- | :--- |
| `python cli.py roots` | List detected skill roots |
| `python cli.py scan` | Scan the catalog and report diagnostics |
| `python cli.py select --query "..."` | Rank one request; use `--json` for machine-readable output |
| `python cli.py eval "..."` | Inspect the tokens and ranking evidence behind a decision |
| `python cli.py overlap` | Find possible near-duplicate skills |
| `python cli.py bridge status` | Check optional bridge files in the target project |
| `python cli.py bridge init --host codex --dry-run` | Preview bridge setup without writing files |

`select`, `eval`, `roots`, and `scan` accept repeatable `--root DIR` arguments and an optional `--profile NAME`.

### MCP server

Start `python mcp_server.py` as a stdio MCP process. It exposes three tools:

| Tool | Purpose |
| :--- | :--- |
| `skill_roots` | List detected skill roots |
| `skill_scan` | Scan roots and return catalog diagnostics |
| `skill_select` | Rank a request and return the decision and candidates |

The server uses newline-delimited JSON-RPC over stdin/stdout. It does not require an external service.

### Optional host bridges

`bridge.py` can initialize, inspect, or remove project-level host bridge files. Supported host values are `claude-code`, `codex`, `gemini`, `cursor`, `cline`, `windsurf`, `copilot`, and `auto`. Preview changes with `--dry-run` before writing.

---

## Configuration

The plugin accepts the following settings. Defaults are defined in `plugin.yaml`.

| Setting | Default | Purpose |
| :--- | :--- | :--- |
| `mode` | `nudge` | `observe`, `nudge`, or `enforce-tools` |
| `skill_roots` | `[]` | Absolute skill roots. Empty uses the active profile's skills directory |
| `min_score` | `0.28` | Minimum score required for a lexical selection |
| `min_margin` | `0.05` | Minimum score lead over the next candidate |
| `max_candidates` | `3` | Maximum candidates retained in a decision |
| `max_skill_bytes` | `262144` | Maximum bytes read from one skill file |
| `context_budget_bytes` | `1200` | Maximum injected context size |
| `observed_tool_limit` | `16` | Maximum tools observed per turn for compliance |
| `visible_receipt` | `true` | Show a receipt in the response |
| `receipt_style` | `compact` | `compact` or `verbose`; trace always contains full evidence |
| `receipt_history_limit` | `20` | Maximum in-session receipts retained for commands |
| `invariants` | `{}` | Per-skill `required_tools`, `forbidden_tools`, and `ordered_tools` |
| `hub_provenance` | `true` | Read-only cross-check against the local hub lock |
| `hub_lock_path` | `""` | Override lock path; empty auto-detects the profile home |
| `synonyms` | `{}` | Extra matching terms per skill, in any language |
| `session_memory` | `true` | Retain selected-skill history for dialogue references |
| `focus_turns` | `5` | Focus duration; `0` keeps focus until released |
| `audit_log` | `true` | Append derived decision data per turn |
| `audit_path` | `""` | Override audit path; empty uses `plugin-data/skill-proof/audit.jsonl` |
| `audit_limit` | `500` | Retained audit lines, from 10 to 10000 |

Example Hermes configuration. Merge these entries into the existing config and keep any other enabled plugins:

```yaml
plugins:
  enabled:
    - skill-proof
  entries:
    skill-proof:
      settings:
        mode: nudge
        skill_roots:
          - /absolute/path/to/skills
        visible_receipt: true
        audit_log: true
```

Skills can declare aliases and invariants in `SKILL.md` frontmatter:

```yaml
---
name: example-skill
description: Example skill
aliases: [short-name, another-term]
invariants:
  required_tools: [terminal]
  forbidden_tools: [git_push]
  ordered_tools: [terminal, write_file]
---
```

Or define invariants centrally:

```yaml
plugins:
  entries:
    skill-proof:
      settings:
        invariants:
          example-skill:
            required_tools: [terminal]
            ordered_tools: [terminal, write_file]
```

### Fleet manifest format

Place `fleet.json` or `agents.json` in the current working directory to map agent names to intent terms. An explicit manifest passed to the API takes precedence.

```json
{
  "reviewer": ["review", "audit", "rubric", "รีวิว"],
  "builder": ["code", "debug", "tdd", "แก้บั๊ก"],
  "researcher": ["evidence", "research", "verify", "ค้นคว้า"]
}
```

---

## Installation

| Use | Steps |
| :--- | :--- |
| Hermes plugin | Copy this directory to the active profile's `plugins/skill-proof`, run `hermes plugins enable skill-proof`, then start a new session |
| CLI | Run `python cli.py` from this directory |
| MCP | Register `python /absolute/path/to/skill-proof/mcp_server.py` as a stdio MCP server in the host |
| Host bridge | Run `python cli.py bridge init --host <host> --dry-run`, review the changes, then rerun without `--dry-run` |

Source: [GitHub repository](https://github.com/Stxyu-p/skill-proof) | [Releases](https://github.com/Stxyu-p/skill-proof/releases)

Quick start for the standalone CLI:

```bash
git clone https://github.com/Stxyu-p/skill-proof.git
cd skill-proof
python -m unittest discover -s tests
python cli.py select --query "design a landing page"
```

---

## Project Structure

| Path | Purpose |
| :--- | :--- |
| `core.py` | Skill parsing, ranking, negation, receipts, invariants, and fleet suggestions |
| `__init__.py` | Hermes plugin adapter, hook registration, and commands |
| `cli.py` | Command-line interface |
| `mcp_server.py` | Stdio MCP server |
| `bridge.py` | Optional host bridge setup and removal |
| `plugin.yaml` | Plugin manifest and full configuration schema |
| `routing_cases.json` | Labeled routing corpus with gated core and reported stretch tiers |
| `routing_benchmark.py` | Routing gate and threshold sweep |
| `benchmark.py` | Synthetic lifecycle performance probe |
| `tests/` | 18 test modules covering core, plugin, bridge, portability, compliance, and multilingual behavior |
| `CHANGELOG.md` | Release history |
| `VALIDATION.md` | Benchmark notes and scope limits |

---

## Verification

Run from the repository root:

```bash
python -m unittest discover -s tests -v
python -m py_compile __init__.py core.py bridge.py cli.py mcp_server.py
hermes plugins doctor . --ci
python benchmark.py
python routing_benchmark.py --gate
python routing_benchmark.py --sweep
```

Current local verification: 296 tests run, 2 skipped, no failures; routing gate 58/58; plugin doctor reports version 0.13.0 with 7 hooks registered and 0 tools.

### Measured benchmark

`benchmark.py` creates 300 synthetic skills and measures `begin_turn`, including the plugin's selection lifecycle. Latest local run:

| Measurement | Result |
| :--- | :--- |
| Cold first turn | 1,210.8 ms |
| Warm median | 209.6 ms |
| Warm p95 | 269.8 ms |
| Synthetic selections | 5 of 5 |

These results depend on the machine and are not a user-facing latency guarantee. The routing gate tests selection behavior separately from catalog scan overhead.

---

## Evidence and Limitations

- `loaded=yes` means a matching successful lifecycle event was observed. It does not prove the exact bytes served, that the model followed the skill, or that the task succeeded.
- `active=yes` means an operational tool was requested after that event; another gate can still block execution.
- Compliance is `unassessed` when no invariants are declared, `verified` when declared invariants are satisfied, and `failed` with named reasons otherwise. Task outcome verification is always `unverified`.
- Lifecycle events have no turn IDs, so evidence correlation is session and task scoped.
- Session history records the skills Skill Proof selected. It is cleared at session end and is not a log of every skill the host loaded independently.
- Focus is a bounded score bonus, never a tie-breaker. A stronger match, veto, or ambiguity wins over focus.
- Multilingual matching is lexical and phrase based. There is no word segmentation or embedding model, including for Thai.
- Routing fixtures are synthetic. A passing core gate is not a production accuracy claim.
- Overlap similarity measures vocabulary overlap. It can flag shared wording for different tasks and miss paraphrased duplicates.
- `hit_rate` counts a selected skill the host actually loaded over `routed - fallback`. Focus carries are excluded. Repeated user requests are not measured because detecting them would require storing prompt text.
- Audit entries contain `query_sha256`, never query text. Session identifiers are hashed. `audit_log: false` disables audit writes.
- Stored per turn: hashes, event flags, bounded receipts, and derived numbers. Stored never: prompts and skill bodies.
- `/skill-proof why` uses live in-process state. After a restart, use `/skill-proof trace`, which also contains no prompt.

---

## License

MIT. See [LICENSE](LICENSE).

<div align="center">

**Skill Proof** <sub>v0.13.0</sub> | Local-first | Auditable | Offline

</div>
