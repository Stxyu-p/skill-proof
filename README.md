<div align="center">

  <h1>Skill Proof</h1>
  <p><strong>Deterministic, in-process skill routing for AI coding agents</strong></p>
  <p><em>Zero routing model calls. Optional local embeddings. Stdio MCP for any agent.</em></p>

  <p>
    <a href=https://github.com/Stxyu-p/skill-proof><img src="docs/badges/release.svg" alt="Release" /></a>
    <a href="#license"><img src="docs/badges/license.svg" alt="License" /></a>
    <img src="docs/badges/python.svg" alt="Python" />
  </p>
  <p>
    <a href="#quick-start"><img src="docs/badges/quickstart.svg" alt="Quick Start" /></a>
    <a href="#installation"><img src="docs/badges/install.svg" alt="Install" /></a>
    <a href="#mcp-server"><img src="docs/badges/mcp.svg" alt="MCP" /></a>
  </p>

</div>

## Overview

**Skill Proof** is an in-process, deterministic skill routing engine for autonomous AI coding agents: Hermes Agent, Claude Code, OpenAI Codex, Antigravity, and Cursor.

A large skill catalog has two costs. The full index makes the system prompt heavy, and delegating routing to a separate model call adds latency and uncertainty on top. Skill Proof resolves both in-process. It scores every installed skill deterministically against the current request (token overlap, alias phrases, a synonym graph, character n-grams for spaceless languages, and optional embeddings) and returns one of three decisions: **selected**, **ambiguous**, or **no_match**. No model call is made to choose a skill, and routing never leaves the machine.

When `reduce_skill_prompt` is enabled, a confident selection replaces the full available-skills index for that request. Ambiguous and no-match turns leave the host index untouched.

Real output, captured from the current build:

```bash
$ python cli.py select --query "Review this pull request for syntax bugs, but do not use TDD"
decision: selected (lexical_match)
suggested fleet agent: altima
selected: github-code-review score=0.940715 reasons=alias_phrase,name_terms,tag_terms,alias_terms,description_terms companions=github-pr-workflow
candidate: github-code-review score=0.940715 reasons=alias_phrase,name_terms,tag_terms,alias_terms,description_terms
candidate: code-review score=0.876008 reasons=alias_phrase,name_terms,alias_terms,description_terms
candidate: github-pr-workflow score=0.852364 reasons=alias_phrase,tag_terms,alias_terms
```

The same command on a vague report-style prompt:

```bash
$ python cli.py select --query "my page keeps crashing after login"
decision: selected (lexical_match)
suggested fleet agent: sora
selected: systematic-debugging score=0.796145 reasons=alias_phrase,alias_terms companions=test-driven-development
candidate: systematic-debugging score=0.796145 reasons=alias_phrase,alias_terms
candidate: planning-with-files score=0.076061 reasons=description_terms
candidate: caveman score=0.035353 reasons=description_terms
```

## Why In-Process Routing

| Cost of a large skill catalog | Without in-process routing | With Skill Proof |
| :--- | :--- | :--- |
| **Routing itself** | An extra model call, or the host model reasons over the full index | Zero model calls. Scoring runs in-process before the provider request |
| **Prompt size** | Every skill name and description stays in the system prompt | A confident selection can replace the index for that request (opt-in, per turn) |
| **Negative constraints** | "do not use X" is just more text | Negation probes veto named skills before scoring (English, Thai, Chinese, Hindi, Japanese, Korean patterns) |
| **Disabled or shadowed skills** | Host-dependent filtering, if any | Reads host configuration. Disabled skills never enter the candidate set |
| **Weak or vague prompts** | The model guesses, or activates the wrong tool | Conservative thresholds mark close candidates ambiguous and return a shortlist instead of a guess |
| **Verification** | No record of why a skill was chosen | SHA-256 audit receipts with selection rationale and observed tool evidence, hash-chained across restarts |
| **Privacy and offline** | Routing data leaves the process | `off` mode is 100% standard library and offline. `local` embeddings stay on disk |

## Architecture

![Skill Proof two-tier in-process routing architecture](docs/architecture.svg)

<sub>Two-tier routing: Tier 1 resolves high-confidence skills in-process and injects a one-line instruction into the current user message. Low-confidence turns fall back to the host with a shortlist of at most three candidates. Plain SVG, zero external assets.</sub>

Decision rules:

- **Clear winner** (score >= `min_score`, margin >= `min_margin`): selected. One-line nudge, optional index replacement, and the embedding layer is skipped entirely on the lexical fast path.
- **Close candidates** (margin below `min_margin`) or weak top score: ambiguous. Up to `max_candidates` names are offered to the host model, which keeps its full skill index.
- **Nothing above threshold**: no_match. The host is unchanged, so Skill Proof cannot force an unrelated skill onto a turn.

## Key Modules

| Module | What It Does | Technical Advantage |
| :--- | :--- | :--- |
| `core.py` | Engine facade: turn lifecycle, session memory, receipts, audit | Stable imports while internals stay split into focused modules |
| `_catalog.py` | Multi-root discovery, host guard, catalog cache, bundle hashing | TTL-cached scans, symlink-safe walking, host-disabled skills filtered |
| `_ranking.py` | Tokenizer, morphology, IDF lexical ranker, candidate pruning | Pre-computed inverted index. Non-matching queries rank in about 0.2 to 0.3 ms on a 300-skill catalog |
| `_dialogue.py` | Session focus, reference resolution ("same skill as before"), negation patterns | Conversational routing that survives multi-turn work |
| `_vector.py` | Optional embedding providers (`off` / `local` / `gateway`), RRF fusion | Confidence-gated: embeddings run only when lexical ranking is borderline |
| `_compliance.py` | Policy store, tool gates, audit records | Enforce mode blocks tools until the skill loads. Stores derived numbers only |
| `bridge.py` | Host hook bridge (PreToolUse / Stop) | One call turns any harness into a guarded agent |
| `cli.py`, `mcp_server.py` | Standalone CLI and MCP server | Same engine pipeline as the plugin, one construction seam (`from_config`) |
| `benchmark.py`, `routing_benchmark.py`, `phase3_gates.py` | Benchmarks and gates | Reproducible numbers. The routing gate exits non-zero on recall regression |

## System Footprint

| Property | Value |
| :--- | :--- |
| Primary dependency (`off` mode) | Python standard library only |
| Routing cost | Zero model calls, pure in-process scoring |
| Network use (`off` / `local`) | None. Routing and embeddings stay offline |
| Embedding work | Skipped on confident lexical winners (`lexical_fastpath` trace) |
| Warm catalog scan (300 skills) | About 0.02 ms per turn |
| Full routing median (300-skill synthetic catalog) | About 6 ms, single Python process |

## Quick Start

### Installation

| Option | Best For | How |
| :--- | :--- | :--- |
| **Hermes Agent plugin** | In-session routing, nudges, tool gates | Clone into your Hermes plugins directory, then `hermes plugins enable skill-proof` |
| **Standalone CLI** | Inspection, scripting, CI | Clone the repository and run `python cli.py` commands |
| **Stdio MCP server** | Claude Code, Codex, Cursor, any MCP client | Point your client at `mcp_server.py` (config below) |

```bash
# 1. Clone into your Hermes plugins directory:
#    macOS / Linux:  ~/.hermes/plugins/skill-proof
#    Windows:        %LOCALAPPDATA%\hermes\plugins\skill-proof
git clone https://github.com/Stxyu-p/skill-proof.git ~/.hermes/plugins/skill-proof

# 2. Enable the plugin
hermes plugins enable skill-proof
```

3. Verify in your next session:

```text
/skill-proof status
/skill-proof why <skill name>
```

4. Or use it standalone:

```bash
python cli.py status
python cli.py scan
```

### MCP server

Skill Proof embeds a standard Model Context Protocol (MCP) server. External agents connect over stdio and share the same catalog:

```json
{
  "mcpServers": {
    "skill-proof": {
      "command": "python",
      "args": ["/path/to/skill-proof/mcp_server.py"]
    }
  }
}
```

The MCP server exposes four tools:

| Tool | Purpose |
| :--- | :--- |
| `skill_select` | Single-skill deterministic routing decision for a query |
| `skill_search` | Hybrid RRF top-k search: matching names, descriptions, and paths |
| `skill_read` | Full `SKILL.md` markdown content for one skill |
| `skill_scan` | Multi-root catalog diagnostics and status |

MCP vector search defaults to lexical-only. To enable `local` or `gateway` embeddings, set these environment variables on the MCP server process: `SKILL_PROOF_EMBEDDING_PROVIDER`, `SKILL_PROOF_EMBEDDING_MODEL`, `SKILL_PROOF_VECTOR_DB`, and `SKILL_PROOF_MODELS_DIR`. For `gateway`, also set `SKILL_PROOF_GATEWAY_URL` and `SKILL_PROOF_GATEWAY_KEY_ENV`. The last one names the environment variable that holds the key. The key itself is never passed as an MCP setting or written to the audit log. The Hermes plugin uses its normal `config.yaml` settings instead.

## Commands

### CLI

```bash
python cli.py select --query "Analyze and optimize database SQL query performance"
python cli.py eval "Design a clean responsive web dashboard"    # scoring breakdown
python cli.py calibrate --json                                  # audit-driven tuning report
```

| Command | Purpose |
| :--- | :--- |
| `roots` | List detected skill roots |
| `scan` | Scan roots and report catalog state |
| `select` | Choose one skill for a query |
| `eval` | Interactive query diagnostics and ranking matrix |
| `overlap` | Detect near-duplicate or overlapping skills across roots |
| `models` | List embedding models, download sizes, and installation state |
| `setup` | Configure an embedding provider (`off` / `local` / `gateway`) |
| `prefetch` | Download and checksum-verify an embedding model |
| `remove` | Remove an installed embedding model |
| `status` | Report system, catalog, and provider status |
| `calibrate` | Threshold and weight suggestions derived from the audit history |
| `bridge` | Connect or inspect host agent workspaces |

### In-session (Hermes plugin)

| Command | Purpose |
| :--- | :--- |
| `/skill-proof status` | Catalog, models, and provider status |
| `/skill-proof why <name>` | Full scoring explanation for the last query, or for a named skill |
| `/skill-proof stats` | Turn statistics from the audit log (`--json`) |
| `/skill-proof calibrate` | Calibration suggestions from audit history (`--json`) |
| `/skill-proof audit-verify` | Verify the audit log hash chain (`--json`) |
| `/skill-proof trace` | The current turn's selection trace |
| `/skill-proof overlap` | Near-duplicate detection across roots (`--json`) |
| `/skill-proof refresh` | Rescan skill roots |
| `/skill-proof health` | Self-check across catalog, provider, and storage |

## Configuration

```yaml
plugins:
  enabled:
    - skill-proof
  entries:
    skill-proof:
      settings:
        mode: nudge                       # nudge (default), observe, or enforce-tools
        reduce_skill_prompt: false        # opt-in; confident selection replaces the full index
        embedding_provider: off           # off (default, zero-dep), local, or gateway
        embedding_model: Xenova/multilingual-e5-small
        min_score: 0.28
        min_margin: 0.05
        catalog_cache_ttl_ms: 1000        # reuse the host-filtered catalog within a session
        agent_routing: {}                 # optional: replace the built-in agent hint patterns
        skill_roots:
          - ~/.agents/skills
          - ~/.hermes/skills
```

| Setting | Default | Description |
| :--- | :--- | :--- |
| `mode` | `nudge` | `nudge` (injects a one-line instruction), `observe` (silent), or `enforce-tools` (blocks tool use until the skill loads). `enforce` is an alias |
| `reduce_skill_prompt` | `false` | When true, a confident selection replaces the host skills index for that provider request. Ambiguous and no-match turns are unchanged |
| `embedding_provider` | `off` | Vector backend: `off` (stdlib lexical), `local` (CPU ONNX), or `gateway` (REST) |
| `embedding_model` | `Xenova/multilingual-e5-small` | Registry model key or HuggingFace repository id |
| `min_score` | `0.28` | Minimum score required for auto-selection |
| `min_margin` | `0.05` | Minimum margin over the runner-up to avoid ambiguity |
| `catalog_cache_ttl_ms` | `1000` | Reuse the host-filtered catalog within a session instead of re-hashing every turn |
| `agent_routing` | `{}` | Optional mapping of agent name to keyword list, replacing the built-in fleet-agent hint patterns |
| `block_modified_hub_skills` | `false` | When true, skills whose bundle hash differs from the hub lock are excluded (hub installs only) |
| `skill_roots` | `[]` | Explicit skill search directories, merged with project, user, and agent roots |
| `session_memory` | `true` | Remember the session's skill history so "use the same skill" resolves |
| `audit_log` | `true` | Append one derived-numbers JSON line per turn. Never prompts or skill bodies |

Three interchangeable provider modes ship with the plugin:

| Provider | Mechanism | Best Used For | Requirements |
| :--- | :--- | :--- | :--- |
| **`off` (default)** | Standard library lexical matching, character n-grams, and the synonym graph | Production fleets, local development, lightweight containers | None. 100% offline |
| **`local`** | CPU ONNX embedding engine with verified registry models | Multilingual semantic matching without external API keys | `onnxruntime`, `tokenizers`, and `numpy` (optional). Falls back to lexical when missing |
| **`gateway`** | OpenAI-compatible embeddings endpoint with automatic fail-open | Centralized gateways and existing embedding proxies | Endpoint URL and API key |

For local inference, install the optional dependencies into the Hermes runtime environment. On Windows, from this checkout:

```powershell
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -m pip install -r requirements-local.txt
```

On macOS or Linux, use the Hermes virtual environment's Python:

```bash
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements-local.txt
```

If Hermes uses a custom installation path, set `HERMES_PYTHON` to its Python executable. `setup --provider local` checks that runtime before writing local mode. Then run `python cli.py setup --provider local --prefetch` to configure and download the model. The default `off` provider needs no third-party packages.

## What Is New in 0.16.0

- **Confidence-gated embeddings**: a lexical winner at 0.55 or higher with a 0.18 margin skips embedding work entirely, and the trace records `provider_used=lexical_fastpath`.
- **Faster at scale**: the pre-computed catalog index and candidate pruning cut non-matching queries to roughly 0.2 to 0.3 ms on a 300-skill catalog, and warm scans to about 0.02 ms.
- **Better spaceless-language matching** (Thai, CJK): character n-gram scoring joined with an English morphology normalizer for descriptions and tags.
- **Tamper-evident audit log**: each audit line chains the SHA-256 of the previous line, across restarts. Verify any time with `/skill-proof audit-verify`.
- **New `calibrate` command**: threshold and weight suggestions derived from real audit history, not guesses.
- **One pipeline everywhere**: the CLI and MCP server now build their engines through `SkillProofEngine.from_config`, the same path as the plugin, so decisions match across surfaces.
- **New settings**: `agent_routing`, `catalog_cache_ttl_ms`, and `block_modified_hub_skills`.
- **Hardened routing**: contrastive clauses ("use X instead of Y"), companion handoff between follow-up turns, and an optional hub integrity gate.

## Evidence and Limitations

- Benchmarks quoted above are synthetic-catalog runs. Real numbers vary with catalog size and query mix.
- Skill Proof selects or abstains. It never edits your skills, never changes prompts beyond the documented nudge or the opt-in index replacement, and never blocks tools unless `enforce-tools` mode is on.
- Receipts and the audit log store derived numbers: scores, reasons, and hashes. They never contain prompts, skill bodies, or API keys.
- Character reduction is recorded per turn. Token savings depend on the host provider's tokenizer, so Skill Proof reports characters and leaves token math to you.
- Embedding downloads happen only through `prefetch` or `setup --prefetch`. Nothing downloads silently.

## Project Structure

| Path | Role |
| :--- | :--- |
| `core.py` | Engine facade re-exporting the stable public surface |
| `_catalog.py`, `_ranking.py`, `_dialogue.py`, `_vector.py`, `_compliance.py` | Focused internals: scanning, ranking, dialogue, embeddings, policy |
| `bridge.py` | Generic host hook bridge for other harnesses |
| `cli.py` | Standalone CLI (`status`, `select`, `eval`, `calibrate`, `setup`, `bridge`, and more) |
| `mcp_server.py` | Stdio MCP server for external agents |
| `routing_cases.json` | Labeled routing fixtures used by tests and the gate |
| `benchmark.py`, `routing_benchmark.py`, `phase3_gates.py` | Performance and gate scripts |
| `tests/` | Offline unit suite |
| `docs/` | Architecture diagram and badges |

## Verification

```bash
# Offline unit suite (no network, no model downloads)
python -m unittest discover -s tests -q

# Labeled routing gate: recall, abstain accuracy, false selections
python routing_benchmark.py --gate

# Phase-3 gates: embedding bypass rate and hook latency
python phase3_gates.py
```

All checks above run with the standard library alone. The local-embedding tests skip themselves when the optional dependencies are absent, so the suite stays green on minimal machines.

## License

[MIT](LICENSE) © Stxyu-p

<div align="center">

**Skill Proof** v0.16.0. Deterministic, local-first, offline-capable.

</div>
