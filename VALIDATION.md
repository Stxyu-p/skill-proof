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

Command: `python routing_benchmark.py` (v0.6.0 harness)

The corpus is 53 labeled cases over 14 fixture skills, split into a gated `core`
tier (48 cases: explicit names, English paraphrases, Thai, aliases, negation,
abstain, near-duplicate ambiguity) and a `stretch` tier (5 cases). Cases with
`expected: null` require a refusal, so selecting the wrong skill and selecting
anything at all are both graded.

Observed: `core 48/48 recall=1.0 precision=1.0 abstain_accuracy=1.0
false_selections=0 misses=0`, `stretch 0/5 recall=0.0`. Stretch failures are
reported with their top candidates and are documented gaps of a lexical-only
router (thin lexical overlap, English synonyms such as "pull request", Thai
synonyms such as "สรุป").

Threshold sweep (`--sweep`): recall stays 1.0 for `min_score` 0.20-0.32 when
`min_margin` >= 0.05; `min_margin: 0` tie-breaks the two near-duplicate cases
into 2 false selections, and `min_score` >= 0.36 loses 4-5 correct selections.
The shipped defaults (0.28 / 0.05) sit inside the stable plateau.

These fixtures are synthetic and hand-labeled by the author. They measure router
behavior on that corpus only: no production accuracy, domain coverage, or
comparison to other retrieval systems is claimed.

Spaceless-script path measured on the real 145-skill catalog: 12-14 ms per
selection (about 1 ms added over an equivalent Latin query, since the n-gram
path is skipped entirely for spaced scripts).

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
