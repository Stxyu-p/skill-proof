# Skill Proof

Deterministic, sub-millisecond skill routing engine for autonomous AI coding agents and fleets.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Stdlib Default](https://img.shields.io/badge/Dependencies-0_(Stdlib_Default)-success.svg)](https://docs.python.org/3/library/)

---

## What is Skill Proof?

**Skill Proof** is an in-process, deterministic routing engine designed for AI coding agents such as Hermes Agent, Claude Code, Codex, Antigravity, and Cursor.

Instead of burning thousands of prompt tokens every turn by injecting massive skill catalogs into system prompts—or making slow, non-deterministic LLM calls just to choose a skill—Skill Proof analyzes the turn in-process and selects the exact matching skill in **under 2 milliseconds**.

```bash
$ python cli.py select --query "ช่วยทำ dashboard หน้าเว็บให้สวยๆ หน่อย"
decision: selected (lexical_match)
selected: ui-ux-pro-max (reasons: tag_terms, alias_terms, description_terms)
```

---

## Why Use Skill Proof? (What It Solves)

Modern agent systems often maintain 50 to 200+ specialized skills (`SKILL.md`). Managing this catalog directly inside the model prompt creates significant bottlenecks:

- **Eliminates Prompt Bloat & Token Waste:** Injecting a catalog of 100+ skills consumes ~4,000 tokens on every turn. Skill Proof routes in-process, enabling agents to replace massive prompt dumps with a clean, single-line load instruction.
- **Eliminates Routing Latency:** LLM-based skill selection takes 5–15 seconds per turn. Skill Proof resolves queries in **~1.3 ms** (pure standard library) or **~19 ms** (local ONNX semantic cache)—over 500x faster.
- **Prevents Hallucinations & Disabled Tool Leaks:** Automatically syncs with host configuration to strictly exclude disabled tools (such as inactive converters or scripts), ensuring the model never hallucinates or calls forbidden skills.
- **Strict Negative Constraints:** Correctly identifies explicit negative requests (e.g. *"don't use TDD"*, *"ไม่เอา TDD"*) across canonical names and aliases, preventing unwanted workflows from triggering.
- **Truthful Audit Receipts:** Emits SHA256-hashed JSON audit receipts recording why a skill was selected, whether it was actually loaded, and whether declared tool invariants were respected—without ever logging private user prompts.

---

## Key Capabilities (What You Get)

### 1. Three Neutral Provider Modes

Skill Proof adapts to your environment without requiring configuration changes:

| Provider | Description | Requirements |
| :--- | :--- | :--- |
| **`off` (Default)** | Pure Python standard library lexical matching, character n-grams, and colloquial synonym graph. | **Zero dependencies**, 100% offline. |
| **`local`** | Local CPU ONNX embedding engine using verified HuggingFace models for deep multilingual semantic matching. | `onnxruntime` (optional). Zero API keys. |
| **`gateway`** | OpenAI-compatible embeddings REST endpoint with automatic fail-open fallback to lexical search on network issues. | API key + Gateway URL. |

### 2. Multi-Agent Ecosystem Support (MCP)

Skill Proof includes a standard Model Context Protocol (MCP) server. External agents like Claude Code, Cursor, and Codex can search and inspect your skill catalog over stdio via `skill_search` and `skill_read`.

### 3. Model Registry with Verified Checksums

When using local semantic embeddings, Skill Proof downloads official, verified models directly from HuggingFace with real-time SHA256 checksum verification:

- **`Xenova/multilingual-e5-small`** (Quantized ONNX, 384 dims, MIT license)
- **`Xenova/multilingual-e5-small-fp32`** (FP32 ONNX fallback, 384 dims, MIT license)
- **`Xenova/paraphrase-multilingual-MiniLM-L12-v2`** (Quantized ONNX backup, 384 dims, Apache-2.0)

---

## Installation

### As a Hermes Plugin

Clone into your Hermes plugins directory and enable:

```bash
git clone https://github.com/Stxyu-p/skill-proof.git ~/.hermes/plugins/skill-proof
hermes plugins enable skill-proof
```

### Standalone CLI / Any Project

Clone the repository and run directly:

```bash
git clone https://github.com/Stxyu-p/skill-proof.git
cd skill-proof
python cli.py status
```

*Requirements: Python 3.11+. The default mode runs entirely on the standard library with zero external dependencies.*

---

## Usage

### 1. Command-Line Interface (CLI)

```bash
# Check status of catalog and providers
python cli.py status

# Route a user instruction to the best matching skill
python cli.py select --query "ช่วยดูโค้ด Python ให้หน่อย แต่อย่าเพิ่งรันเทสต์นะ ไม่เอา TDD"

# List available embedding models and local download status
python cli.py models

# Prefetch a verified local model (optional)
python cli.py prefetch --model Xenova/multilingual-e5-small

# Scan active roots and inspect catalog diagnostics
python cli.py scan
```

### 2. Hermes Slash Commands

When running inside Hermes Agent, use the built-in commands:

- `/skill-proof status` — View the current turn's routing decision.
- `/skill-proof explain` — Inspect scores, candidate margins, and selection rationale.
- `/skill-proof why <skill>` — Diagnostic view of why a skill won, lost, or was vetoed.
- `/skill-proof health` — Hook activity, performance timings, and catalog health.

### 3. Stdio MCP Server

To expose your skill catalog to Claude Code, Codex, or Cursor, add Skill Proof to your MCP configuration:

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

Available MCP tools:
- `skill_search`: Hybrid top-k search over active skills.
- `skill_read`: Read full markdown content of a skill.
- `skill_select`: Deterministic single-skill selection.
- `skill_scan`: Catalog discovery and status.

---

## Configuration

Configure Skill Proof in your host's `config.yaml` under `plugins.entries.skill-proof.settings`:

```yaml
plugins:
  enabled:
    - skill-proof
  entries:
    skill-proof:
      settings:
        mode: nudge                       # nudge (default), observe, or enforce-tools
        embedding_provider: off           # off (default zero-dep), local, or gateway
        embedding_model: Xenova/multilingual-e5-small
        min_score: 0.28
        min_margin: 0.05
        skill_roots:
          - ~/.agents/skills
          - ~/.hermes/skills
```

| Setting | Default | Description |
| :--- | :--- | :--- |
| `mode` | `nudge` | `nudge` (recommends load), `observe` (silent), or `enforce-tools` (blocks tools until skill loaded). |
| `embedding_provider` | `off` | Vector backend: `off` (pure stdlib lexical), `local` (CPU ONNX), or `gateway` (REST). |
| `embedding_model` | `Xenova/multilingual-e5-small` | Model repository identifier from the verified registry. |
| `min_score` | `0.28` | Minimum score threshold required for auto-selection. |
| `min_margin` | `0.05` | Minimum score margin over the runner-up candidate to avoid ambiguity. |
| `skill_roots` | `[]` | Explicit skill search directories (merges project, user, and agent roots). |

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

Please ensure all changes pass the test suite:

```bash
python -m unittest discover -s tests -q
```

---

## License

[MIT](LICENSE) © Stxyu-p
