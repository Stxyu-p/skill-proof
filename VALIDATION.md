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

## Session memory, dialogue references, and focus — 2026-10-08 (v0.7.0)

`tests/test_v070.py` (48 cases) covers repeat and previous references, focus
request, decay, expiry, release, veto precedence, ambiguity precedence,
fallback, unknown skill names, session scoping, and bounds on the session map
and history stack.

Observed on the live 145-skill catalog, one session, seven turns:
`keep using ui-ux-pro-max` → `focus_requested` with `focus_remaining: 5`;
`stop using ui-ux-pro-max` → `focus_released` and the focus cleared;
`keep using skills-that-do-not-exist` → `dialogue_reference_unknown` (not
`no_previous_selection`); a Thai turn with no lexical signal abstained while
focused; an unrelated turn with no competitor selected the focused skill with
`focus_fallback` (score 0) and a context line stating it is a fallback.

Scope limits: session history records what Skill Proof selected, is bounded to
64 sessions and 10 entries per session, and is dropped at session end. It is
not a record of skills a host loaded on its own. `focus_turns` bounds focus to
5 turns by default. No live model conversation was executed for these checks.

## Explain and audit — 2026-10-08 (v0.8.0)

`tests/test_v080.py` (27 cases) covers every `why` verdict (`selected`,
`ranked`/`below_threshold`, `vetoed`, `no_signal`, `ranked_below_cap`,
`unknown_skill`, `not_listed_by_host`, `not_ranked`), asserts the report never
contains prompt text,
checks the rank table bound (20 rows), and covers audit line shape, the
prompt-free guarantee, `audit_log: false`, tail rotation, the broken-path
fallback, and health reporting.

Measured on the live 145-skill catalog: recording the veto list costs about
0.51 ms per turn (the same scan already runs inside `_rank`), and a full
lexical rank returns only 10 non-zero rows for a typical query, so the 20-row
table bound holds without extra ranking work.

Observed on a three-skill fixture through the real hooks:
`why frontend-design` on a below-threshold turn reported
`ranked #1 at 0.219985 (threshold 0.28) threshold_met=False` with
`name_terms,description_terms`; `why python-tdd` on a vetoed turn reported
`vetoed by this turn's wording`; the audit line for the same turn contained
`status no_match / reason negated_skill / query_sha256` and no prompt text.

Scope limits: `why` reads only live in-process turn state (restart loses the
table; `/skill-proof trace` remains available and holds no prompt). The audit
file stores derived numbers only, is append-only with tail rotation, and is
disabled by `audit_log: false`.

## Outcome telemetry — 2026-10-08 (v0.9.0)

`tests/test_v090.py` (16 cases) covers override capture in every position
(matching load, mismatched load, load while we abstained, unknown turn), the
aggregation math (fallback excluded from hit rate, null hit rate when only
fallbacks exist, per-skill rows summing to totals), corrupt and foreign audit
lines, and the `stats` command surface.

Observed on a six-turn three-skill fixture driven through the real hooks:
`routed 4 (66.7%) loaded 2 miss 2 fallback 0`, `abstained 2`, `overridden 3`,
`vetoed 1`, `hit_rate 50.0%`; the per-skill table and `overrides: slides x3`
matched hand-counted expectations, and no audit line contained prompt text.

## Overlap and pruning — 2026-10-08 (v0.10.0)

`tests/test_v0100.py` (19 cases) covers identical descriptions, threshold
filtering, pair counts, ordering and limits, the truncated flag, determinism,
argument validation, exact-copy collapse across roots, the CLI text and JSON
surfaces, and the plugin command including drop-candidate selection from audit
usage.

Measured on the live 145-skill catalog: 10,440 pairs checked in 59 ms;
3 pairs at `similarity >= 0.4` (`claude-code`/`grok` 0.625,
`claude-code`/`opencode` 0.500, `grok`/`opencode` 0.500), 6 pairs at
`>= 0.31`, 0 identical descriptions, 17 exact copies already collapsed by
cross-root precedence, and 12 divergent copies shadowed. Diagnostics also show
72 `unsafe_reparse` and 2 `invalid_frontmatter` entries.

Scope limits: similarity is lexical overlap, so it flags shared vocabulary
describing different tasks (the `9router-*` family sits just below the default
threshold for that reason) and misses duplicates that are paraphrased. It says
nothing about which skill is better — only which pair is redundant and, when
the audit log exists, which side the host actually loads.

Scope limits: `hit_rate = loaded / (routed - fallback)` and says nothing about
whether the skill was *useful* — only that the host loaded it. Overrides count
skills the host loaded that we had not selected; they are never treated as
load evidence for our decision. A user re-asking the same question is not
measured, because that needs prompt text and prompts are never stored. The
metric requires `audit_log: true` (the default) and a readable audit file.

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
