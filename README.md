<div align="center">

  <h1>🛡️ Skill Proof</h1>
  <p><strong>Deterministic, sub-millisecond skill routing engine for autonomous AI agent fleets</strong></p>
  <p><em>Route in sub-milliseconds · Zero model tokens · Multi-agent via Stdio MCP</em></p>

  <p>
    <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-CLI-0284c7?style=for-the-badge" alt="Quick Start" /></a>
    <a href="#-key-capabilities-what-you-get"><img src="https://img.shields.io/badge/Routing_Modes-Off_|_Local_|_Gateway-7B61FF?style=for-the-badge" alt="Routing Modes" /></a>
    <a href="#-multi-agent-mcp-integration"><img src="https://img.shields.io/badge/Multi--Agent-MCP_Server-10b981?style=for-the-badge" alt="MCP Server" /></a>
    <a href="#-installation"><img src="https://img.shields.io/badge/Installation-Hermes_&_CLI-blueviolet?style=for-the-badge" alt="Installation" /></a>
  </p>

  <p>
    <img src="https://img.shields.io/badge/Architecture-Deterministic_Lexical_+_ONNX-07405E?style=flat-square" alt="Architecture" />
    <img src="https://img.shields.io/badge/Latency-1.3_ms_(In--Process)-brightgreen?style=flat-square" alt="Latency" />
    <img src="https://img.shields.io/badge/Routing_Cost-0_Tokens-blueviolet?style=flat-square" alt="Tokens" />
    <img src="https://img.shields.io/badge/Dependencies-0_(Stdlib_Default)-success?style=flat-square" alt="Dependencies" />
    <img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" alt="License" />
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square" alt="Python" />
  </p>

</div>

---

## 🌟 What is Skill Proof?

**Skill Proof** is an in-process, deterministic routing engine designed for autonomous AI coding agents—including **Hermes Agent, Claude Code, OpenAI Codex, Antigravity, and Cursor**.

In modern agent environments with 50 to 200+ specialized skills (`SKILL.md`), injecting full skill catalogs into system prompts bloats context windows by ~4,000 tokens every turn, while delegating routing to an LLM adds 5–15 seconds of latency per instruction.

Skill Proof solves this by evaluating instructions **in-process** via lexical token overlap, synonym graphs, and negative veto probes, selecting the exact winning skill in **under 2 milliseconds** with **zero model calls**.

```bash
$ python cli.py select --query "ช่วยทำ dashboard หน้าเว็บให้สวยๆ หน่อย"
decision: selected (lexical_match)
selected: ui-ux-pro-max (reasons: tag_terms, alias_terms, description_terms)
```

---

## 📐 Architecture

![Skill Proof Two-Tier In-Process Routing Architecture](docs/architecture.svg)

<sub>Two-tier architecture: Tier 1 resolves high-confidence skills in-process (&lt; 2 ms, 0 tokens) and emits a single-line prompt instruction. Ambiguous or multi-domain instructions fall back safely to a top-3 candidate shortlist evaluated by the host model. Plain SVG, zero external assets.</sub>

---

## 🎯 Why Use Skill Proof? (What It Solves)

| Challenge in LLM-Based Routing | How Skill Proof Solves It | Practical Benefit |
| :--- | :--- | :--- |
| **Prompt Bloat (~4,000 tok/turn)** | Replaces massive catalog prompt dumps with a clean 1-line nudge (`Use skill_view for "X" first`). | **Saves ~4,000 tokens every single turn** |
| **Routing Delay (5–15 seconds)** | In-process lexical engine scores the catalog in **~1.3 ms** (or ~19 ms with local ONNX cache). | **~500x faster execution response** |
| **Disabled Tool Leaks** | Reads host configuration (`config.yaml`) to strictly filter disabled utilities (e.g. inactive converters). | **Zero tool hallucinations or accidental leaks** |
| **Ignoring Negative Vetoes** | Analyzes negation patterns against canonical names and aliases (*"don't use TDD"*, *"ไม่เอา TDD"*). | **Strictly vetoes unwanted workflows** |
| **Opaque Hallucinations** | Bounded decision thresholds (`min_score=0.28`, `min_margin=0.05`); marks close calls as `ambiguous`. | **Never guesses wildly on vague instructions** |
| **Unverifiable Actions** | Generates cryptographic SHA256 audit receipts recording selection rationale and tool invariants. | **100% reproducible compliance audit trail** |

---

## 🚀 Key Capabilities (What You Get)

### 1. Three Neutral Provider Modes

Skill Proof provides three interchangeable provider modes to suit any deployment scenario:

| Provider | Mechanism | Best Used For | External Requirements |
| :--- | :--- | :--- | :--- |
| **`off` (Default)** | Python Standard Library lexical matching, character n-grams, and colloquial synonym graph. | Production fleets, local development, lightweight containers. | **None (0 dependencies)**, 100% offline. |
| **`local`** | Local CPU ONNX embedding engine with verified HuggingFace models (`multilingual-e5-small`). | Deep multilingual semantic matching (Thai, English, CJK) without API keys. | `onnxruntime` (optional). Zero network calls. |
| **`gateway`** | OpenAI-compatible embeddings REST endpoint with automatic fail-open fallback. | Centralized enterprise gateways and existing embedding proxies. | API key + Endpoint URL. |

### 2. Multi-Agent MCP Integration

Skill Proof embeds a standard **Model Context Protocol (MCP)** server. External agents connect via Stdio to search and read skills without modifying their harness:

- `skill_search`: Hybrid RRF top-k search returning matching skill names, descriptions, and file paths.
- `skill_read`: Direct retrieval of full `SKILL.md` markdown content.
- `skill_select`: Single-skill deterministic routing decision.
- `skill_scan`: Multi-root catalog diagnostics and status.

### 3. Official HuggingFace Model Registry

When running with `embedding_provider: local`, Skill Proof downloads official, verified models directly from HuggingFace with streaming SHA256 checksum verification:

- **`Xenova/multilingual-e5-small`** (Quantized ONNX, 384 dims, MIT license) — *Primary recommended*
- **`Xenova/multilingual-e5-small-fp32`** (FP32 ONNX fallback, 384 dims, MIT license)
- **`Xenova/paraphrase-multilingual-MiniLM-L12-v2`** (Quantized ONNX backup, 384 dims, Apache-2.0)

---

## 💻 Quick Start

### Option A: Install as Hermes Agent Plugin

Clone directly into your Hermes plugins directory and activate:

```bash
git clone https://github.com/Stxyu-p/skill-proof.git ~/.hermes/plugins/skill-proof
hermes plugins enable skill-proof
```

Verify in your next session:
```text
/skill-proof status
/skill-proof health
```

### Option B: Standalone CLI / Local Python

```bash
# 1. Clone repository
git clone https://github.com/Stxyu-p/skill-proof.git
cd skill-proof

# 2. Inspect active skills and system status
python cli.py status

# 3. Route an instruction
python cli.py select --query "ช่วยดูโค้ด Python ให้หน่อย แต่อย่าเพิ่งรันเทสต์นะ ไม่เอา TDD"
```

### Option C: Connect External Agents via Stdio MCP

Add Skill Proof to your agent configuration (`~/.codex/config.toml`, Claude Code, or Cursor settings):

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

---

## 🛠️ Command-Line Reference

```bash
# Display catalog skills, installed models, and provider status
python cli.py status

# List available HuggingFace models, download sizes, and installation state
python cli.py models

# Prefetch and verify an embedding model (optional)
python cli.py prefetch --model Xenova/multilingual-e5-small

# Scan catalog roots and view diagnostics
python cli.py scan

# Evaluate scoring breakdown and char n-gram weights for a query
python cli.py eval "ช่วยทำ dashboard หน้าเว็บให้สวยๆ หน่อย"

# Detect near-duplicate or overlapping skills across roots
python cli.py overlap --min-similarity 0.4
```

---

## ⚙️ Configuration

Configure Skill Proof in your host configuration file (`config.yaml`):

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

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `mode` | `nudge` | `nudge` (injects 1-line prompt instruction), `observe` (silent observation), or `enforce-tools` (blocks tool use until skill loads). |
| `embedding_provider` | `off` | Vector backend: `off` (stdlib lexical), `local` (CPU ONNX), or `gateway` (REST). |
| `embedding_model` | `Xenova/multilingual-e5-small` | Model repository identifier from the verified registry. |
| `min_score` | `0.28` | Minimum score threshold required for auto-selection. |
| `min_margin` | `0.05` | Minimum score margin over the runner-up candidate to avoid ambiguity. |
| `skill_roots` | `[]` | Explicit skill search directories (merges project, user, and agent roots). |

---

## 🤝 Contributing

Contributions and feedback are welcome! Please open an issue first to discuss architectural changes or proposed feature additions.

To run the offline test suite:
```bash
python -m unittest discover -s tests -q
```

---

## 📄 License

[MIT](LICENSE) © Stxyu-p

<div align="center">

**Skill Proof** <sub>v0.15.0</sub> · Deterministic · Zero-Overhead · Offline

</div>
