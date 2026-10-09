<div align="center">

  <h1>🛡️ Skill Proof</h1>
  <p><strong>Deterministic, zero-overhead skill routing engine for autonomous AI agent fleets</strong></p>
  <p><em>Route in sub-milliseconds. Save 4,000+ prompt tokens. Never trust prompt-only guessing.</em></p>

  <p>
    <a href="https://github.com/Stxyu-p/skill-proof/releases"><img src="https://img.shields.io/badge/Release-v0.14.2-315C4B?style=for-the-badge" alt="Release" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=for-the-badge" alt="License" /></a>
    <a href="https://docs.python.org/3/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge" alt="Python" /></a>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Architecture-Deterministic_Lexical_+_Synonyms-0284c7?style=flat-square" alt="Architecture" />
    <img src="https://img.shields.io/badge/Dependencies-0_(Stdlib_Only)-success?style=flat-square" alt="Stdlib only" />
    <img src="https://img.shields.io/badge/Latency-0.58_ms_|_1,724_QPS-brightgreen?style=flat-square" alt="Latency" />
    <img src="https://img.shields.io/badge/Token_Cost-0_Tokens_In--Memory-blueviolet?style=flat-square" alt="Tokens" />
    <img src="https://img.shields.io/badge/Tests-303_Passing-brightgreen?style=flat-square" alt="Tests" />
    <img src="https://img.shields.io/badge/Routing_Gate-58%2F58-brightgreen?style=flat-square" alt="Routing Gate" />
    <img src="https://img.shields.io/badge/Hard_Scenarios-10%2F10_PASS-brightgreen?style=flat-square" alt="Scenarios" />
  </p>

</div>

---

## 🌟 Overview

Skill Proof answers one critical question with verifiable evidence: **which agent skill best matches this turn, and why?**

Instead of forcing the host LLM to read through dozens of skill descriptions on every conversational turn (wasting ~4,050 prompt tokens per turn), Skill Proof indexes local `SKILL.md` documents, builds an in-memory lexical and synonym graph, and resolves the target skill in **0.58 milliseconds** with **zero model calls**.

When a match is clear, it injects a one-line deterministic nudge for the model to load the skill. When a request is weak or ambiguous, it abstains with an explicit reason. As a Hermes plugin, it logs truthful audit receipts and enforces tool-execution invariants.

```text
$ python cli.py select --query "ช่วยทำ dashboard หน้าเว็บให้สวยๆ หน่อย"
decision: selected (lexical_match)
suggested fleet agent: milim
selected: ui-ux-pro-max score=0.96 reasons=tag_terms,alias_terms,description_terms
candidate: ui-ux-pro-max score=0.96 reasons=tag_terms,alias_terms,description_terms
```

```text
$ python routing_benchmark.py --gate
cases:         58 (35 expect a skill, 23 expect abstain)
results:       recall=1.0  precision=1.0  abstain_accuracy=1.0  decision_accuracy=1.0
errors:        false_selections=0  misses=0
gate: PASS
```

---

## 🎯 Core Guarantees

- **Zero-Token Ranking Overhead:** The routing path runs entirely in-process using Python standard library. No model tokens are consumed to choose which skill to load.
- **Sub-Millisecond Latency:** In-process lexical scoring runs at **0.58 ms median (1,724 QPS)** across 136 live skills, operating 2,600x faster than model-based prompt selection.
- **Strict Disabled Skill Isolation:** Automatically syncs with host configuration (`config.yaml`) to exclude disabled utilities (e.g. `pdf`, `xlsx`, `docx`), ensuring disabled tools never win selection.
- **Alias-Aware Negation Veto:** Vetoes phrases targeting either canonical names or known aliases (e.g. *"ไม่เอา TDD"*, *"don't use TDD"* cleanly vetoes `test-driven-development`).
- **Cryptographic Audit Trail:** Generates verifiable, SHA256-backed JSON receipts per turn without logging private user prompt text or raw skill code.
- **Zero Third-Party Dependencies:** 100% Python Standard Library. Runs completely offline without external APIs, vector databases, or background daemons.

---

## 📊 Comparison Against Prompt-Only Routing

Measured head-to-head on a live 136-skill agent fleet catalog across 10 hard real-world scenarios:

| Metric / Dimension | Prompt-Only LLM Routing | Skill Proof (Hybrid Tier 1) | Advantage |
| :--- | :--- | :--- | :--- |
| **Selection Latency** | 10,692.8 ms (~10.7 s) | **3.47 ms** (0.58 ms warm select) | **2,661x faster** response time |
| **Prompt Token Cost** | ~4,050 tokens / turn | **0 tokens** (in-memory) | **Saves ~4,050 tokens every turn** |
| **Cost over 25 Turns** | ~101,250 tokens consumed | **0 tokens consumed** | Massive context window savings |
| **Disabled Skill Guard** | Probabilistic (relies on model attention) | **Deterministic hard filter** | Zero leak of disabled tools |
| **Veto Handling ("ไม่เอา TDD")** | Model context dependent | **Strict alias-aware negation filter** | Immediate veto without hesitation |
| **Ambiguity Handling** | May guess or pick unpredictably | **Explicit abstention (`ambiguous`)** | Prevents unvetted execution |
| **Decision Auditability** | Unstructured free-form text | **Bounded JSON receipt with hashes** | 100% reproducible and verifiable |
| **Network & Daemon Requirement** | Remote API / Gateway connection | **None (Pure Python stdlib)** | Complete offline resilience |

---

## 📐 Architecture & Decision Flow

```
[User Turn / Prompt]
          │
          ▼
┌────────────────────────────────────────────────────────┐
│  Tier 1: Skill Proof Engine (0.58 ms | 0 tokens)       │
│  - Multi-root catalog scan (.agents, hermes, workspace)│
│  - Host disabled-skills filter (config.yaml)           │
│  - Alias-aware negation probe & Unicode normalizer     │
│  - Lexical token match + char n-grams + synonym graph  │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
   [status == 'selected']       [status == 'ambiguous' / 'no_match']
  (Score >= 0.28, Margin >= 0.05)         │
              │                           ▼
              │             ┌───────────────────────────────┐
              │             │  Tier 2: Host LLM Fallback    │
              │             │  - Evaluates complex context  │
              │             │  - Runs only when ambiguous   │
              │             └─────────────┬─────────────────┘
              │                           │
              └─────────────┬─────────────┘
                            ▼
           [Emit Verifiable Receipt + Load Skill]
```

### Decision Pipeline

1. **Explicit Skill Resolution:** Resolves dollar-sign identifiers (e.g. `$python-tdd`) or explicit requests (`"use skill frontend-design"`).
2. **Session Dialogue Reference:** Resolves conversational references (e.g. `"use the same skill"`, `"keep using X"`, `"อันที่แล้ว"`) against session history.
3. **Lexical Scoring & Synonym Matching:** Computes weighted overlap across normalized name (+0.92), description phrases (+0.76), aliases/synonyms (+0.74), tags (+0.22), and character n-grams (+0.55).
4. **Alias-Aware Negation Veto:** Vetoes candidate skills if the query negates the skill name or any of its known aliases/synonyms.
5. **Score and Margin Thresholds:** Requires minimum score (`min_score=0.28`) and minimum winning margin (`min_margin=0.05`). Otherwise abstains cleanly (`no_match` or `ambiguous`).

---

## 🏛️ Subsystems

| Subsystem | File | Primary Responsibility | Technical Advantage |
| :--- | :--- | :--- | :--- |
| **Catalog Scanner** | `core.py` | Multi-root discovery and validation | Reparse-point aware, max-byte guarded, 64-skill disabled filter |
| **Lexical Ranker** | `core.py` | Scoring, char n-grams, and synonym graph | Sub-millisecond token weights, spaceless Thai/CJK handling |
| **Engine State** | `core.py` | Thread-safe turn state and focus memory | Bounded session history, focus decay, zero-allocation cache |
| **Evidence Ledger** | `core.py` | Receipt generation and tool compliance | Cryptographic SHA256 fingerprints, privacy-safe hashes |
| **Hermes Adapter** | `__init__.py` | 7 lifecycle hooks and slash commands | Seamless integration with Hermes TUI and CLI runtime |
| **CLI & Diagnostics** | `cli.py` | Command-line evaluation and catalog tools | Fast terminal diagnostics, JSON export, interactive eval |
| **Stdio MCP Server** | `mcp_server.py` | Model Context Protocol tools | Standard JSON-RPC interface for Claude Code, Codex, and Cursor |
| **Host Bridges** | `bridge.py` | Cross-tool workspace adapters | Auto-configures Cursor, Codex, Gemini, Claude, and Copilot |

---

## ⚙️ Modes

| Mode | Behaviour |
| :--- | :--- |
| `nudge` | **Default.** Requests the selected skill be loaded via `skill_view` before operational tools; does not block execution |
| `observe` | Reports candidate skill without asking the host model to load it; silent observer mode |
| `enforce-tools` | Hard enforcement. Blocks operational tools until a selected skill load is observed; blocks forbidden-tool violations |

---

## 💻 Interfaces

### Hermes Slash Commands

| Command | Purpose |
| :--- | :--- |
| `/skill-proof status` | Compact routing decision for the latest turn |
| `/skill-proof explain` | Decision reason, candidate scores, and margin breakdown |
| `/skill-proof why <name>` | Diagnostic breakdown of why a skill won, lost, or was vetoed |
| `/skill-proof stats` | Hit rate, misses, and overrides from the audit log |
| `/skill-proof overlap` | Near-duplicate skill report across all active roots |
| `/skill-proof trace` | Full bounded JSON receipt for compliance audits |
| `/skill-proof refresh` | Clears cache and forces catalog re-scan on next turn |
| `/skill-proof health` | Hook activity, catalog diagnostics, and latency timings |

### Command-Line Interface (CLI)

```bash
# List all detected skill roots
python cli.py roots

# Scan catalog, filter disabled skills, and report diagnostics
python cli.py scan

# Rank a query and display winning candidate
python cli.py select --query "ช่วยแก้บั๊ก python หน่อย"

# Inspect detailed scoring tokens, CJK/Thai n-grams, and reasons
python cli.py eval "ช่วยทำ dashboard หน้าเว็บให้สวยๆ หน่อย"

# Find overlapping or redundant skills across roots
python cli.py overlap --min-similarity 0.4
```

### Stdio MCP Server

Start `python mcp_server.py` as a stdio MCP process for Claude Code, Codex, or Cursor:

| Tool | Purpose |
| :--- | :--- |
| `skill_roots` | Lists active skill directories and discovery paths |
| `skill_scan` | Scans catalog and returns diagnostics |
| `skill_select` | Ranks query and returns deterministic decision and candidates |

---

## 🔧 Configuration

Configure settings in `config.yaml` under `plugins.entries.skill-proof.settings`:

```yaml
plugins:
  enabled:
    - skill-proof
  entries:
    skill-proof:
      allow_tool_override: false
      settings:
        mode: nudge
        min_score: 0.28
        min_margin: 0.05
        max_candidates: 3
        visible_receipt: true
        audit_log: true
        skill_roots:
          - C:\Users\BlankScreen\Workspace\.agents\skills
          - C:\Users\BlankScreen\.agents\skills
          - C:\Users\BlankScreen\AppData\Local\hermes\skills
```

| Setting | Default | Description |
| :--- | :--- | :--- |
| `mode` | `nudge` | Operating mode: `observe`, `nudge`, or `enforce-tools` |
| `skill_roots` | `[]` | Explicit skill search directories (merges project, user, and host roots) |
| `min_score` | `0.28` | Minimum lexical score required for selection |
| `min_margin` | `0.05` | Minimum score lead over the second candidate |
| `max_candidates` | `3` | Maximum candidates retained in decision table |
| `max_skill_bytes` | `262144` | Maximum bytes read from a single `SKILL.md` (256 KB) |
| `context_budget_bytes` | `1200` | Maximum bytes for ephemeral prompt nudge |
| `observed_tool_limit` | `16` | Maximum tool calls tracked per turn for invariant checks |
| `visible_receipt` | `true` | Display compact receipt in response |
| `audit_log` | `true` | Append derived decision records to `audit.jsonl` |

---

## 🚀 Installation & Quick Start

| Environment | Setup Method |
| :--- | :--- |
| **Hermes Plugin** | Clone into `plugins/skill-proof`, run `hermes plugins enable skill-proof`, restart session |
| **Standalone CLI** | Run directly via `python cli.py` from repository directory |
| **MCP Server** | Configure `python /path/to/skill-proof/mcp_server.py` as stdio server |
| **Host Bridges** | Run `python cli.py bridge init --host auto --dry-run` to inspect and install rules |

### 4-Step Quick Start

```bash
# 1. Clone repository
git clone https://github.com/Stxyu-p/skill-proof.git
cd skill-proof

# 2. Run unit tests (stdlib only, no dependencies required)
python -m unittest discover -s tests -q

# 3. Verify routing benchmark gate
python routing_benchmark.py --gate

# 4. Route your first query
python cli.py select --query "review my pull request please"
```

---

## 📂 Project Structure

| File | Purpose |
| :--- | :--- |
| `core.py` | Catalog scanner, lexical ranker, synonym graph, negation engine, and receipt ledger |
| `__init__.py` | Hermes plugin adapter, hook dispatchers, and slash commands |
| `cli.py` | Standalone command-line interface and query evaluation tools |
| `mcp_server.py` | Stdio Model Context Protocol (MCP) server |
| `bridge.py` | Workspace bridges for Claude Code, Codex, Gemini, Cursor, and Copilot |
| `plugin.yaml` | Hermes plugin manifest and configuration schema |
| `routing_cases.json` | 58-case labeled routing test fixture |
| `routing_benchmark.py` | Automated routing gate (58/58) and threshold sweep harness |
| `benchmark.py` | Synthetic performance and latency benchmark suite |
| `tests/` | 19 test modules covering core, plugin, bridge, compliance, and multilingual routing |
| `CHANGELOG.md` | Version history and performance milestones |

---

## 🧪 Verification & Measured Performance

Run complete test suite from repository root:

```bash
python -m unittest discover -s tests -q
python routing_benchmark.py --gate
python routing_benchmark.py --sweep
```

### Measured System Numbers

- **Unit Tests:** **299 tests run, 0 failures, 2 skipped** (Windows symlink constraints).
- **Routing Gate:** **58/58 cases pass** (100% recall, 100% precision, 0 false selections).
- **Hard Simulation Scenarios:** **10/10 PASS (100.0%)** on live multi-domain Thai/English queries.
- **Warm Routing Latency:** **0.58 ms** median, **1.44 ms** p95, **1,724 QPS** throughput.
- **Catalog Scaling:** 136 live skills scanned in 354 ms cold, cached in-process thereafter.
- **Disabled Skill Rejection:** 64 disabled skills strictly excluded from winning selection.

---

## 🔒 Evidence and Limitations

- `loaded=yes` confirms a matching skill lifecycle event occurred. It does not prove the model followed instructions or that the code compiled.
- `active=yes` confirms an operational tool was invoked following skill selection.
- Invariant compliance is `verified` when declared tool rules are satisfied, `failed` when violated, and `unassessed` when no invariants are declared.
- Stored per turn: cryptographic SHA256 hashes, latency metrics, and derived candidate records. Stored never: raw user prompts, private keys, or code bodies.
- Multilingual matching uses token overlap, char n-grams, and synonym graphs. It is deterministic and does not use heavy neural embedding models.

---

## 📄 License

MIT. See [LICENSE](LICENSE).

<div align="center">

**Skill Proof** <sub>v0.14.0</sub> · Deterministic · Zero-Overhead · Offline

</div>
