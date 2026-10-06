# Skill Proof v0.2 validation — 2026-09-05

## v0.3 checks — 2026-09-05

`host_smoke.py` now exercises the real `skill_view` result, multiline description,
and source-path comparison as well as disabled-skill filtering. Observed:
`real_host_skills_list: PASS`, `local_skill_selected: PASS`,
`disabled_skill_excluded: PASS`, `multiline_and_source_path: PASS`.
On that small cold fixture: listing 378.499 ms, scan 5.925 ms, selection 1.874 ms,
core 7.819 ms, total plugin pre-LLM work 386.359 ms. These are one-run diagnostics,
not a production benchmark or comparable to the 300-skill core-only runs below.
No model conversation was executed.

## Implemented

Host skills_list name filtering, metadata cache, refresh command, completed-turn
retention, compact/error footer, synthetic benchmarks, isolated host smoke.
No live installation or MemCore modification was performed.

## Performance measurement

Command: `python benchmark.py`, same machine, 300 generated skills, 21 turns.

| Measurement | Before v0.2 changes | After cache and duplicate-count changes |
|---|---:|---:|
| Warm median (20 turns) | 721.390 ms | 536.638 ms |
| Warm p95 | 768.218 ms | 561.563 ms |
| Cold first turn | 3791.131 ms | 1574.884 ms |

Warm median decreased by 25.6% in these runs. Cold numbers are strongly affected
by filesystem warming and must not be treated as isolated optimization gains.
This measures core scanning and selection only: it excludes the new host
skills_list call, model latency, and actual user skill workloads. It does not
establish an end-to-end latency improvement or superiority over Eagle Eye.

## Routing measurement

Command: `python routing_benchmark.py`

Observed output: `correct: 12, total: 12, false_selections: 0, misses: 0, failures: []`.
Fixtures are synthetic English/Thai examples with explicit names, description
matches, missing names, multiple requests, and unrelated requests. No production
accuracy claim is supported. Add real labeled cases to measure that separately.

## Real host integration

Set `HERMES_HOME` to a scratch profile and `PYTHONPATH` to the installed Hermes
source; run `python host_smoke.py` from this directory.

Observed output:

```json
{"real_host_skills_list":"PASS","local_skill_selected":"PASS","disabled_skill_excluded":"PASS","network":"blocked by Doctor","live_profile_modified":false}
```

Command: `hermes plugins doctor . --ci` with a scratch HERMES_HOME.

Observed output: `skill-proof 0.2.0 (standalone)`;
`OK: runtime discovery, manifest parsing, import, and registration passed`;
`registrations: 0 tool(s), 7 hook(s)`.

These checks exercise real host listing/registration, not a live model conversation.
