# Skill Proof 0.4.1

![Version](https://img.shields.io/badge/Version-0.4.1-10b981?style=flat-square)
![Tests](https://img.shields.io/badge/Tests-73_OK-brightgreen?style=flat-square)
![Routing](https://img.shields.io/badge/Routing-12_of_12-brightgreen?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-blue?style=flat-square)

Local skill routing for Hermes agents: deterministic lexical selection,
tool gating, truthful load receipts, and invariant compliance verification.
Offline, stdlib only, no external calls.

## New in v0.4.1: Negation Guard + Token-Boundary Phrase Matching

- **Negation guard**: vetoes such as `don't use X`, `without X`, `ไม่เอา X`,
or `ไม่ใช้ X` exclude that skill from ranking instead of selecting it.
A vetoed `$name` falls back to lexical ranking; when every explicit name
is vetoed the turn reports `no_match/negated_skill`.
- **Token-boundary phrase matching**: short queries such as `ui` no longer
match inside longer words like `build`. Thai text without word spaces
keeps substring matching.
- The disabled-skill intersection is now locked by a regression test, and
the `health` command reports the correct plugin version.

## New in v0.4.0: Invariant Compliance Verification

Skills can now define and verify execution invariants instead of leaving compliance permanently `unassessed`:
- **`required_tools`**: Tools that must be invoked during the turn (e.g. `terminal` for TDD). Missing required tools at turn end set `compliance: failed` with `missing_required_tool:<tool>`.
- **`forbidden_tools`**: Tools that must not be invoked (e.g. `write_file` or `patch` for read-only audit skills). Calling a forbidden tool sets `compliance: failed` with `forbidden_tool:<tool>`; in `enforce-tools` mode the tool call is blocked outright.
- **`ordered_tools`**: Tool execution order sequences (e.g. `[terminal, write_file]` for TDD: test before implementation). Out-of-order calls set `compliance: failed` with `order_violation:<tool>_before_<prior>`; in `enforce-tools` mode the tool call is blocked outright.

Invariants can be declared directly in `SKILL.md` frontmatter:
```yaml
---
name: my-skill
description: ...
invariants:
  required_tools: [terminal]
  forbidden_tools: [git_push]
  ordered_tools: [terminal, write_file]
---
```
Or configured centrally in `config.yaml`:
```yaml
plugins:
  entries:
    skill-proof:
      settings:
        invariants:
          test-driven-development:
            required_tools: [terminal]
            ordered_tools: [terminal, write_file]
          codebase-inspection:
            forbidden_tools: [write_file, patch]
```

When all invariants are satisfied, receipts and summaries truthfully report `compliance: verified`.
Skills without invariants remain `compliance: unassessed` for full backward compatibility.

## TRSS fixes in v0.3.1

Natural-language references such as `ใช้ skill proof plugin ดูหน่อย` no longer
force a missing `proof` skill. Conjoined catalog skills (`ใช้สกิล pdf กับ xlsx`,
`use skills pdf and xlsx`) are both detected; multiple selections still request
disambiguation instead of silently dropping a skill. Action continuations such
as `and summarize it` are not interpreted as catalog skills. `$missing` and
`skill:missing` remain explicit missing-skill requests.

This release does not claim improved multilingual semantic ranking or proven
model compliance. The installed package now includes tests and benchmark files.

## New in v0.3

`/skill-proof health` shows hook invocation counts and last-seen UTC timestamps,
the latest receipt's origin (current process or persisted), local/host/eligible
skill counts, up to 20 file diagnostics with relative paths, omitted count,
cache hits/misses, and `listing_ms`, `scan_ms`, `selection_ms`, `core_ms`, `total_ms`.
Counters record invocation, not successful completion. Health does not cause a
new scan. Host names and local files have different scopes, so counts may differ.
Diagnostics cover visited candidate files; deliberately excluded directories are
not enumerated. `not_in_hermes_list` means the host did not list that name; it does
not infer whether the reason was disabled status, platform rules, or another filter.

Timing measures plugin pre-LLM work including host listing, not the whole model
turn. Cache counts refer to valid local files reused or read; failed path checks
are diagnostics. Supporting-file views do not overwrite main-skill result evidence.

Description block scalars `>`, `|`, and optional `+`/`-` are now supported with
whitespace normalized for searching. This remains a subset, not a general YAML parser.
When a successful main `skill_view` reports `_source_path`, the receipt records
`source_path_match: match/mismatch/unknown`. Missing paths remain unknown. A mismatch
is reported; a path match is not proof of identical bytes or instruction compliance.
Absolute returned paths are not stored in receipts. Duplicate local names remain ambiguous.

Hermes standalone plugin for local skill selection and auditable load evidence.
Python 3.11; no additional dependencies. MemCore remains the memory provider;
this plugin neither reads nor writes MemCore data.

## Behavior

- Scans configured `SKILL.md` roots each user turn, reuses parsed content when file metadata is unchanged, and selects one skill using deterministic lexical ranking.
- Intersects local names with the real Hermes `skills_list` result each turn. Failure is visible as `hermes_catalog_unavailable`; it blocks operational tools in `enforce-tools` mode and permits them in other modes.
- Explicit requests such as `$python-tdd` or `ใช้สกิล python-tdd` take precedence.
- Nudges the agent to call Hermes `skill_view`.
- Records Hermes `on_skill_lifecycle: loaded`, local source hash, and pre-transform tool-result hash.
- Adds a compact final receipt and exposes `/skill-proof status`, `explain`, `trace`, and `refresh`. Set `receipt_style: verbose` for the original detailed footer.
- Keeps at most 100 completed turns in memory; in-flight turns are retained so active gates are not evicted.
- Keeps the latest 20 completed receipts in Hermes plugin state by default. Receipts exclude raw prompts and skill bodies.

## Modes

| Mode | Behavior |
|---|---|
| `observe` | Suggests a candidate and records events; tools remain allowed |
| `nudge` (default) | Requests loading before operational tools; tools remain allowed |
| `enforce-tools` | Blocks operational tools until a selected skill has a Hermes load event |

`enforce` is an alias. Skill discovery/management tools remain allowed. No match
allows ordinary work. An explicitly unknown, duplicate, or multiple skill request
blocks operational tools only in `enforce-tools` mode. Correct the request in a
new turn to resolve that condition.

## Install after reviewing the source

Copy this directory to the active Hermes profile's `plugins/skill-proof` directory,
then run `hermes plugins enable skill-proof` and start a new Hermes session.
The source build does not perform installation or modify the live configuration.

Merge these settings into the existing configuration; retain other enabled plugins:

```yaml
plugins:
  enabled:
    - skill-proof
  entries:
    skill-proof:
      settings:
        mode: nudge
        skill_roots:
          - C:/Users/BlankScreen/AppData/Local/hermes/skills
        visible_receipt: true
        receipt_history_limit: 20
```

Use explicit roots for custom profiles. Empty roots use `HERMES_HOME/skills`, or
the platform default. Roots must already be resolvable by Hermes `skill_view`;
listing a root here does not register it with Hermes. Start with `nudge` and
inspect `/skill-proof explain` before enabling tool gating.

## Evidence and limitations

`loaded=yes` means Hermes emitted a matching successful lifecycle event.
`active=yes` means an operational tool was requested after that event; another
gate can still block execution. Compliance remains `unassessed` and verification
remains `unverified`. A local source hash is a filesystem recheck, not proof that
Hermes served those exact bytes. Tool hashes observe the result before other
plugins transform it.

Hermes can answer without tools; this plugin cannot require another model turn.
Another output-transform plugin can take precedence over the visible footer;
the receipt remains available through the command. Lifecycle events lack turn IDs,
so they correlate to the current session/task; delayed events within the same task
cannot be distinguished perfectly. Commands show the latest receipt in this plugin
instance, not a caller-specific receipt.

The MVP selects one skill. It supports a deliberately restricted frontmatter
subset: scalar name, scalar or block description, and inline tags. Other YAML constructs,
symlinks, hidden directories, and plugin-only skills are not indexed. It does not
independently implement Hermes platform filters, project trust rules, or disabled-skill settings;
it filters against names returned by Hermes. This is a name-level intersection, not
proof of identical path resolution or identical model-visible catalog. External and
plugin skills outside configured roots are still excluded. Thai matching is lexical/phrase-based, without Thai
word segmentation or semantic embeddings. No ranking-quality or speed improvement
over Hermes/Eagle Eye has been demonstrated yet. Synthetic core performance results
are in `VALIDATION.md`; they exclude the host listing overhead.

The cache compares mtime, ctime, size and file identity on every scan. Normal edits,
adds and deletes are detected next turn. Metadata-preserving changes can evade that
check: `/skill-proof refresh` forces a reread next turn. Successful load events always
perform a fresh source read. Completed receipts are not rewritten by refresh.

## Checks

Run from this directory:

```powershell
python -m unittest discover -s tests -v
python -m py_compile __init__.py core.py
hermes plugins doctor . --ci
python benchmark.py
python routing_benchmark.py
```

Tests cover discovery, ranking, tool gating, evidence hashes, cross-task isolation,
and simulated hook flows. Doctor checks import and registration in the real host;
it does not exercise a live model conversation.

`routing_cases.json` contains representative synthetic cases, not user conversation
history. Add labeled cases or run `python routing_benchmark.py your-cases.json`.
`host_smoke.py` optionally tests the real listing and disabled-skill filtering with
Hermes source on `PYTHONPATH`; its Doctor sandbox blocks networking and uses a temporary profile.
