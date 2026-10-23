# Changelog

All notable changes to Skill Proof are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.1] - 2026-10-24

### Changed
- **OS-Standard Global Models Directory (`skill_proof/models.py`)**: `default_models_dir` now resolves models using platform-standard locations:
  - Windows: `%LOCALAPPDATA%\hermes\models`
  - Linux / macOS: `$XDG_DATA_HOME/hermes/models` (falling back to `~/.local/share/hermes/models`)
  - Universal fallback: `~/.hermes/models` (overridable via `HERMES_MODELS_DIR`)
  - Allows sharing downloaded ONNX models (`Xenova/multilingual-e5-small`) across Hermes plugins (`skill-proof`, `memcore`) with zero duplication.
- **Decoupled Gateway Defaults (`plugin.yaml`, `skill_proof/cli.py`, `skill_proof/_vector.py`, `__init__.py`)**:
  - Replaced hardcoded private localhost proxy settings with clean, neutral defaults (`https://api.openai.com/v1/embeddings`, `OPENAI_API_KEY`).
  - Out-of-the-box zero-coupling experience for new users and clean installs.

## [1.3.0] - 2026-10-20

### Added
- **PyPI distribution renamed to `skill-proof-router`** (`pyproject.toml`): the `skill-proof` name was rejected by PyPI typosquatting protection due to phonetic and lexical similarity to the pre-existing `skillproof` project (by Akhil Garg). `skill-proof-router` is accepted and configured with PyPI Trusted Publishing.
  - **The import package (`import skill_proof`), console scripts (`skill-proof`, `skill-proof-mcp`), and MCP server name (`skill-proof`) are completely unchanged.** Every agent config and local tool integration continues to work without modification. Only the package name on PyPI changed: install via `pip install skill-proof-router`.

## [1.2.0] - 2026-10-20

### Added
- **Release workflow for PyPI** (`.github/workflows/release.yml`): publishes the wheel and sdist via Trusted Publishing when a GitHub release is published, with `skip-existing` so a re-run cannot overwrite an existing version. All actions are pinned to commit SHAs, matching the verified workflow in the sibling MemCore repo.

## [1.1.0] - 2026-10-19

### Added
- **Client-aware Trinity habit body** (`skill_proof/bridge.py`): `sync_global_trinity_habits()` now writes a different section per target. The global MCP client files (`~/.gemini/GEMINI.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) keep the MCP tool names; the shared workspace `AGENTS.md` gets a two-column body — A) MCP clients (Codex CLI, Claude Code, Antigravity / Gemini CLI), B) Hermes (native plugins, no MCP). The single old body named MCP tools (`skill_select`, `trimtok_compress_terminal`) that do not exist in Hermes, where all three engines run as native plugins.
- **Surface notes in the engine skill docs** (`skills/trimtok`, `skills/skill-proof`, `skills/memcore`): each doc now states which tools its own client exposes — MCP server tool names for the MCP clients, the native-plugin behaviour for Hermes. The generated skill bodies (`skill_proof/cli.py`, `memcore/__main__.py`) carry the same note so a regenerated doc keeps it.

### Fixed
- The Trinity habit section is no longer reverted by a later `skill-proof install`: the text lives in the generator, and the generator now picks the right body per client.

## [1.0.0] - 2026-10-18

### Added
- **Canonical Global Skill Vault (`skill_proof/vault.py`, `skill-proof vault`)**:
  - Unifies `SKILL.md` bundles across all agent ecosystems into `~/.skill-proof/skills` (`vault status | migrate | pin | unpin | sync | link-all`) using cross-platform directory links (NTFS junctions on Windows, symlinks on POSIX) with SHA-256 `vault-lock.json` provenance and zero local file duplication.
- **Universal Multi-Client MCP Installer (`skill_proof/mcp_install.py`, `skill-proof install`)**:
  - One-command `skill-proof install | clients | uninstall` auto-registers `skill_proof.mcp_server` across Claude Code, Claude Desktop, Gemini CLI, Antigravity, Antigravity IDE, OpenAI Codex CLI, OpenCode, and VS Code (GitHub Copilot).
- **Single-Round-Trip `include_content` & Warm Engine Cache (`skill_proof/mcp_server.py`)**:
  - `skill_select` now supports `include_content: true` to inline the winning `SKILL.md` content and `source_path` in a single MCP tool call.
  - Added module-level `_ENGINE_CACHE` in `mcp_server.py` to preserve catalog mtime fingerprints and companion warm-handoff across stdio MCP turns.
- **Zero-Willpower Global Agent Trinity Habit Protocol (`skill_proof/bridge.py`, `skill_proof/cli.py`)**:
  - `sync_global_trinity_habits()` non-destructively injects/updates `<!-- trinity:habit:start --> ... <!-- trinity:habit:end -->` in `~/.gemini/GEMINI.md`, `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, and `Workspace/AGENTS.md`, alongside `~/.gemini/antigravity-cli/mcp/skill-proof/instructions.md` and MCP `initialize` `instructions`.

### Fixed
- Added missing `Counter` import in `skill_proof/mcp_server.py` (`_tool_scan`) and resolved `routing_cases.json` relative to `__file__` in `tests/test_v0160.py` so pytest succeeds from any working directory.

## [0.16.0] - 2026-10-15

### Added
- **Tamper-evident audit hash chain (plan item 3.1)**: every `audit.jsonl` line now carries `prev_sha256` — the first 16 hex chars of the sha256 of the raw previous line — and the chain resumes across plugin restarts; `/skill-proof audit-verify [--json]` walks the log and reports broken links (rotation keeps a verifiable suffix).
- **`/skill-proof calibrate [--json]` (plan item 3.1)**: deterministic, advisory suggestions from the audit log — lower `min_score` when no_match dominates, smaller `min_margin` when ambiguity dominates, and synonym work listing the most frequent host overrides. Never mutates config or the log.
- **Clause-level contrastive routing (plan item 3.2)**: "X instead of Y", "X rather than Y" and Thai "อย่าใช้ A แต่ให้ใช้ B" / "เปลี่ยนไปใช้" queries rank only the requested clause and drop any skill named solely in the rejected clause; for "but"/"instead" the requested side is the one without the negation marker, and engagement requires a known skill in the rejected clause so ordinary prose stays unchanged.
- **Companion warm handoff (plan item 3.6)**: after a turn that used skill A, skills declared in A's `related_skills` receive a +0.04 pilot bonus when they already carry lexical signal (reason tag `companion_handoff`).
- **Hub integrity gate (plan item 3.7)**: `block_modified_hub_skills: true` refuses to select a hub skill whose local bundle no longer matches the recorded hash (`blocked` / `hub_modified`); default off.
- **Catalog TTL cache and SQLite WAL sync (plan items 5.2.1/5.2.5)**: `SkillProofEngine.catalog_cache_ttl_ms` (default 1000 ms) reuses the scanned catalog inside the window — the scandir fingerprint alone costs 31–42 ms per 300 skills — and `refresh_catalog()` plus `created`/`updated`/`deleted` lifecycle events reset it deterministically. `VectorIndex` runs SQLite in WAL mode with `synchronous=NORMAL`, and `sync_catalog` reads existing `(skill_name, source_sha256)` rows with a single SELECT instead of one query per skill.
- **Stretch tier closed in `off` mode (plan item 5.2.3)**: capability-name synonym bridges ("keeps crashing", "pull request", "pages are slow", "สรุปเอกสาร" / "เอกสาร ภาษาไทย", mirrored on the real `systematic-debugging` / `github-code-review` entries) connect natural-language problem statements to common skill names — all five shipped stretch cases now select their expected skill (router tier recall 1.0, false selections 0).
- **Test seams for time, embeddings, and turn policy (v0.16 plan item 5.1)**: `SkillProofEngine(clock=…)` and `SkillProofPlugin(ctx, clock=…)` accept an injected monotonic clock, so the 30s `skills_list` TTL and latency metrics are testable without sleeping; `VectorIndex(embedder=…)` accepts any object implementing `embed(texts, is_query)` and traces `provider_used="embedder"`; every `begin_turn` publishes `active_turn.json` (selected skill + invariants) and `bridge.py::run_hook(payload, store=…)` enforces `forbidden_tools` and `ordered_tools` from it in `enforce-tools` mode, recording completed steps back into the store.
- **Lightweight English morphology normalizer (v0.16 plan item 5.2c)**: description, tags, and alias token sets now carry common plural/gerund/past base forms (`crashing`/`crashes` → `crash`, `tables` → `table`, `migrations` → `migration`); skill names stay exact (expanding name tokens caused measured false-selection drift). The shipped stretch case "convert this PDF into a table" now selects `pdf-extraction` in `off` mode.
- **Filtered-view catalog cache (v0.16 plan item 5.2a)**: `begin_turn` reuses the host-filtered catalog for an identical `(catalog_hash, host name set)` instead of rebuilding diagnostics and re-hashing every turn; cleared by `refresh_catalog`.
- **Confidence-gated vector cascade (v0.16 plan item 5.2d)**: when the lexical winner scores at least 0.55 with a 0.18 margin, the embedding work is skipped entirely and the trace records `provider_used="lexical_fastpath"`.
- **Pre-indexed candidate pruning (v0.16 plan item 5.2b)**: every `Catalog` now carries an immutable inverted index (token/n-gram postings, alias phrases, normalized names/descriptions), and `_rank` scores only skills with at least one matching posting or substring signal. The candidate set is kept a strict superset of positive scorers (verified by a differential test over all 63 shipped cases). Measured on the 300-skill synthetic catalog: non-matching queries rank in ~0.2–0.3 ms instead of ~1.4–3.1 ms; the 14-skill shipped fixture prunes to 0–3 candidates per case (median 1); the all-match worst case is unchanged.
- **Config-driven fleet-agent routing (plan item 1.2)**: `SkillProofEngine(agent_routing={agent: [terms]})` — exposed as the plugin's `agent_routing` config — replaces the built-in `suggested_agent` patterns entirely; the built-in defaults stay when the key is absent.
- **Phase-3 gate script (`phase3_gates.py`)**: reports the confidence-cascade bypass rate on `routing_cases.json` (28/40 = 70.0% on cases that expect a selection; 28/63 = 44.4% counting abstain rescue cases) next to a recall-equality check with and without the vector index, plus `bridge.py` hook latency (median 0.8–1.4 ms, p95 ≤ 4.0 ms against the 15 ms gate).

### Changed
- `on_skill_lifecycle` keeps the host-listing cache across `loaded` events and clears it only for `created`/`updated`/`deleted`/`enabled`/`disabled` (weakness W5), removing the `skills_list` refetch after every skill load.
- `core.py` split into focused modules (v0.16 plan item 5.1) — `_catalog.py` (types, scanning, frontmatter), `_vector.py` (SQLite vector index), `_dialogue.py` (negation, session memory, references), `_ranking.py` (tokenizer, morphology, IDF ranking, candidate index), `_compliance.py` (guard results and the turn-policy store) — with `core.py` as a facade re-exporting every moved name. Pure code movement: `import core` callers are unchanged; test mock targets now patch the owning module.
- **Stat-signature proof cache (plan item 3.5)**: `_prove_selected_source` reuses a prior proof while `(mtime_ns, size, ino)` of the source file is unchanged — no disk read and no frontmatter re-parse; any change falls through to the full verification path.
- **Mtime-cached bundle hashing (plan item 3.7)**: `_bundle_sha256` reuses its digest until the newest bundle-file mtime changes, so hub provenance checks stop re-reading unchanged bundles.
- The ranking loop reads precomputed `name_norms` / `desc_norms` from the catalog index instead of re-normalizing every skill description on every turn, and `benchmark.py` now reports warm scan/routing medians separately (`warm_scan_median_ms`, `warm_routing_median_ms`, `warm_routing_p95_ms`).
- **Unified engine factory for CLI and MCP (plan item 1.2)**: `SkillProofEngine.from_config(roots, config=…, vector_index="auto")` is the single construction seam for `cli.py` and `mcp_server.py`, so both surfaces run the same `begin_turn`/`select_skill` pipeline as the plugin; `"auto"` resolves the configured provider through `vector_index_from_env()`, and explicit `$name` turns still skip the embedding sync and the vector ranking path. `SkillProofEngine.latest_catalog()` returns the last scanned catalogue.
- **Neutral host naming (plan item 1.2)**: receipt evidence now carries `host_loaded_event` (the old `hermes_loaded_event` key remains as a legacy alias) and the catalog diagnostic code is `not_in_host_list`.

### Note
- `SkillProofEngine` publishes `active_turn.json` under `<hermes home>/plugin-data/skill-proof/` on every turn (cleared when no skill is selected). The policy gates tool calls only in `enforce-tools` mode; host bridges read it through `bridge.py::run_hook`.
- All five stretch cases are closed in `off` mode (see Added). The 300-skill synthetic all-match query still costs ~5 ms of pure Python scoring per turn because every candidate legitimately matches; the plan's 0.25 ms routing target holds for realistic vocabulary mixes (~0.16–0.29 ms median on non-all-match queries), not for the pathological all-match shape — kept as a documented worst case.

### Fixed
- `_explain_skill` no longer raises `IndexError` when `/skill-proof why <name>` runs in a turn whose rank table is empty; in-catalog skills report `no_signal` instead.
- `Xenova/multilingual-e5-small-fp32` registry entries now carry real SHA-256 checksums (config.json, tokenizer_config.json, tokenizer.json previously held SHA-1 digests, which made `prefetch` fail for this model). Values verified against the HuggingFace files themselves.
- `check_model_status` and `download_model` now verify the SHA-256 of files already on disk whose size matches, so corrupted same-size files are detected and re-downloaded.
- `guard_tool` records a tool only after it is allowed, so a blocked call can no longer satisfy `ordered_tools` prerequisites, and `observe_tool_result` no longer duplicates consecutive entries.
- `bridge.py::run_hook` reads `skill-proof-bridge.json` from the workspace (`payload["cwd"]`) instead of the Skill Proof install folder; `remove_host("auto")` now cleans every detected host.
- `on_pre_llm_call` returns the engine-composed context, so `mode: observe`, `focus_fallback` warnings, and `context_budget_bytes` apply again; the selected-skill nudge remains the terse `Use skill_view for "<name>" first.` message.
- `cli.py` settings writer rewrites a `settings: {}` header to a block and replaces multi-line YAML values when updating, instead of emitting invalid YAML.
- `cli.py select` and `cli.py eval` now route through the configured vector index when one is active; the duplicated `ai-setup` block in `bridge.py::main` is removed.
- Duck-typed vector indexes only need `sync_catalog`/`rank` now: `reset_trace`/`trace_snapshot` are optional, and the emitted `vector_trace` falls back to the provider-`off` shape when the index does not expose them.

## [0.15.0] - 2026-10-11

### Added
- **Neutral Provider Abstraction (`off | local | gateway`)**:
  - `off`: zero-config stdlib lexical router (default for new installations).
  - `local`: local CPU ONNX runtime inference with explicitly prefetched models and verified SHA256 checksums.
  - `gateway`: OpenAI-compatible embeddings endpoint with graceful fallback on 429 / timeout / network failures.
- **Model Registry (HuggingFace verified)**:
  - `Xenova/multilingual-e5-small` (quantized ONNX, 384 dim, MIT).
  - `Xenova/multilingual-e5-small-fp32` (FP32 ONNX fallback, 384 dim, MIT).
  - `Xenova/paraphrase-multilingual-MiniLM-L12-v2` (quantized ONNX backup, 384 dim, Apache-2.0).
- **Selection Nudge in Adapter**:
  - `selected`: emits clean 1-line nudge `Use skill_view for "<name>" first.`
  - `ambiguous`: emits top-3 candidate names `Ambiguous match: choose between <cands>.`
  - `no_match`: returns unconstrained prompt allowing full LLM fallback.
- **CLI Subcommands**:
  - `python cli.py models`: list available registry models, size, license, and installation status.
  - `python cli.py prefetch --model <name>`: download and SHA256-verify model files.
  - `python cli.py remove --model <name>`: remove downloaded model files cleanly.
  - `python cli.py status`: report catalog skills, installed models, and vector provider status.

### Corrected on 2026-10-12
- The `pre_llm_call` hook alone only adds context to the current user message. Hermes also exposes `llm_request` middleware; Skill Proof now uses it behind the opt-in `reduce_skill_prompt` setting to replace the available-skills index only after a confident selection. Ambiguous and no-match requests keep the full index. Character reduction is recorded; prior claims of saving about 4,000 tokens per turn remain withdrawn until measured against actual provider token usage.
- Historical claims of sub-millisecond end-to-end routing and 1,500+ QPS are also unsupported and withdrawn; the routing benchmark gates decision accuracy, not throughput.
- Local model downloads are explicit through `prefetch` or `setup --prefetch`; there is no automatic lazy-download flow on a hard query in v0.15.0.
- The primary model's `tokenizer.json` is a Unigram/Metaspace tokenizer, not WordPiece. Local inference uses the optional `tokenizers` package listed in `requirements-local.txt`; a stdlib WordPiece implementation is not compatible with this model.

## [0.14.4] - 2026-10-10

### Added
- **MCP `skill_search` and `skill_read`**: hybrid RRF top-k search
  (Anthropic Tool Search style) and full SKILL.md reading for any
  MCP-capable agent; `bridge.py mcp` prints ready-to-paste JSON/TOML
  registration for Claude Code, Codex, Antigravity, and Cursor.
- **SQLite vector index + RRF fusion**: `VectorIndex` (`core.py`) persists
  embeddings in `plugin-data/skill-proof/embeddings.db` (stdlib only),
  syncs only changed skills by `source_sha256`, caches query vectors, and
  fuses with lexical ranking via RRF (k=60) with negation veto and graceful
  offline fallback.

### Performance
- **Adapter `skills_list` cache**: 30 s TTL with invalidation on skill
  lifecycle events eliminates the ~364 ms dispatch on warm turns.
- **Ambiguity nudge**: emits the top-3 candidate names instead of a static
  message (measured top-3 recall 97.6% on the 102-case gold set).

## [0.14.3] - 2026-10-09

### Optimized
- **Allocation-Free Token Coverage in `_rank`**:
  - `_calc_coverage` now sums matched token weights directly across `query_tokens` without intermediate set intersection allocations, reducing garbage collection pressure.
  - Cached `_contains_spaceless_script` with `@lru_cache(maxsize=4096)` and simplified `_phrase_present` to eliminate redundant string concatenation allocations (`shorter + longer`).
- **Normalized Query Reuse Across Candidate Loops**:
  - `_identifier_in_query` and `_skill_is_negated` now accept precomputed `norm_query`, removing repetitive `normalize_identifier(query)` calls for every skill in the catalog.

### Resilient
- **Atomic Audit Log Rotation**:
  - `_rotate_audit` now writes to a temporary file before atomically replacing `audit.jsonl` via `Path.replace()`, preventing truncated or corrupted log files during sudden terminations.

## [0.14.2] - 2026-10-09

### Optimized
- **Duplicate Names & Spaceless N-gram Caching**:
  - Added `@lru_cache` to `_catalog_duplicate_names` avoiding `Counter` allocations across queries in `_rank`.
  - Added `@lru_cache` to `_skill_spaceless_ngrams` caching joined description, tags, and aliases n-grams for spaceless script turns.
  - Optimized alias resolution in `_rank`: avoids `dict.fromkeys` and string re-tokenizing when no custom synonyms exist for a skill.

### Added
- **CLI Positional Query Support for `select`**:
  - `python cli.py select "my query"` is now accepted alongside `python cli.py select --query "my query"`, matching `eval` command ergonomics.

## [0.14.1] - 2026-10-09

### Fixed
- **Type Hint Resolution & Missing Iterable Import**:
  - Added `Iterable` to typing imports in `core.py`, fixing `NameError: name 'Iterable' is not defined` when evaluating `typing.get_type_hints` on `normalize_identifiers` and `scan_catalog`.
- **Query None-Safety & Boundary Robustness**:
  - `select_skill` now coerces `query` safely to string (`str(query or "")`), preventing `AttributeError: 'NoneType' object has no attribute 'casefold'` when callers pass `None`.
  - Added null-safety check in `_query_might_contain_negation` to gracefully return `False` on empty or `None` inputs.
  - Hardened `suggest_roots` to gracefully return `[]` when `roots` is `None` or empty.

### Optimized
- **Config Mtime-Based Cache for `load_disabled_skills`**:
  - Added `_DISABLED_SKILLS_CACHE` keyed by `cfg_path` and `st_mtime_ns`.
  - Avoids re-parsing `config.yaml` with PyYAML on repeated scans, slashing latency from 40-100 ms down to **0.001 ms** (600x-20,000x faster).
- **Document Frequency Token Reuse**:
  - `_catalog_document_frequency` now reuses pre-cached frozensets from `_skill_tokens_cache` instead of re-joining strings and re-tokenizing skills.
- **Dead Code Elimination**:
  - Removed unreferenced constants `_TOKEN_RE` and `_SEA_SPACELESS_RANGES`.

### Validation
- **301 tests passing** (100% pass, 0 failures, 2 skipped on Windows symlinks).
- **58/58 benchmark gate cases passing**.

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
