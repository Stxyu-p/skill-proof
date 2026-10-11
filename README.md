<div align="center">

  <h1>🛡️ Skill Proof</h1>
  <p><strong>Deterministic, sub-millisecond skill routing engine for autonomous AI agent fleets</strong></p>
  <p><em>Sub-millisecond routing · Zero model tokens · Multi-agent via Stdio MCP</em></p>

  <p>
    <a href="https://github.com/Stxyu-p/skill-proof"><img src="https://img.shields.io/badge/Repository-Stxyu--p%2Fskill--proof-0284c7?style=flat-square&logo=github" alt="Repository" /></a>
    <a href="#-quick-start"><img src="https://img.shields.io/badge/Architecture-Deterministic_Lexical_+_ONNX-0284c7?style=flat-square" alt="Architecture" /></a>
    <a href="#-key-capabilities-what-you-get"><img src="https://img.shields.io/badge/Latency-Sub--Millisecond-brightgreen?style=flat-square" alt="Latency" /></a>
    <a href="#-why-use-skill-proof-what-it-solves"><img src="https://img.shields.io/badge/Routing_Cost-0_Tokens-blueviolet?style=flat-square" alt="Routing Cost" /></a>
    <a href="#-multi-agent-mcp-integration"><img src="https://img.shields.io/badge/Protocol-Stdio_MCP-10b981?style=flat-square" alt="Stdio MCP" /></a>
    <img src="https://img.shields.io/badge/Dependencies-Stdlib_Default-success?style=flat-square" alt="Dependencies" />
    <img src="https://img.shields.io/badge/License-MIT-slate?style=flat-square" alt="License" />
    <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square" alt="Python" />
  </p>

</div>

---

## 🌟 What is Skill Proof?

**Skill Proof** is an in-process, deterministic skill routing engine designed for autonomous AI coding agents—including **Hermes Agent, Claude Code, OpenAI Codex, Antigravity, and Cursor**.

In modern agent environments with large catalogs of specialized skills (`SKILL.md`), injecting full skill indexes into system prompts can add thousands of tokens each turn. Delegating routing decisions to an external LLM call can add substantial latency.

Skill Proof replaces prompt-based discovery with **in-process deterministic evaluation**. Utilizing token overlap, alias-aware negation analysis, and optional local ONNX vector caching, Skill Proof resolves user requests to the exact winning skill in **under 1 millisecond** with **zero model calls**.

```bash
$ python cli.py select --query "Review this pull request for syntax bugs, but do not use TDD"
decision: selected (lexical_match)
selected: code-review (reasons: phrase_match, tag_terms)
vetoed: test-driven-development (matched negation: 'do not use TDD')
```

---

## 📐 Architecture

![Skill Proof Two-Tier In-Process Routing Architecture](docs/architecture.svg)

<sub>Two-tier architecture: Tier 1 resolves high-confidence skills in-process and injects a single-line instruction into the current user message. Ambiguous turns receive up to three candidates; no-match turns rely on the host's normal skill index. Plain SVG, zero external assets.</sub>

---

## 🎯 Why Use Skill Proof? (What It Solves)

| Challenge in LLM-Based Routing | How Skill Proof Solves It | Practical Benefit |
| :--- | :--- | :--- |
| **Prompt Bloat** | Injects a selected skill directive into the current user message; the standard Hermes plugin hook does not remove the system prompt's `<available_skills>` index. | **No fixed-prompt token saving is currently implemented or measured** |
| **Routing Delay (5–15 seconds)** | In-process lexical scoring executes in **&lt; 1 ms** (or ~19 ms with local ONNX cache). | **Thousands of times faster execution response** |
| **Disabled Tool Leaks** | Reads host configuration (`config.yaml`) to strictly filter disabled utilities (e.g. inactive converters). | **Zero tool hallucinations or accidental leaks** |
| **Ignoring Negative Constraints** | Probes negation patterns across canonical names and aliases (*"do not use TDD"*). | **Strictly vetoes unwanted workflows** |
| **Wild Guesses on Weak Prompts** | Enforces conservative decision thresholds (`min_score=0.28`, `min_margin=0.05`); marks ties as `ambiguous`. | **Eliminates false-positive tool activation** |
| **Unverifiable Actions** | Emits SHA256 audit receipts recording selection rationale and tool invariants. | **100% reproducible execution audit trail** |

---

## 🚀 Key Capabilities (What You Get)

### 1. Three Neutral Provider Modes

Skill Proof provides three interchangeable provider modes to suit any deployment scenario:

| Provider | Mechanism | Best Used For | Requirements |
| :--- | :--- | :--- | :--- |
| **`off` (Default)** | Python Standard Library lexical matching, character n-grams, and synonym graph. | Production fleets, local development, lightweight containers. | **None (0 dependencies)**, 100% offline. |
| **`local`** | Local CPU ONNX embedding engine with verified registry models. | Multilingual semantic matching without external API keys. | `onnxruntime`, `tokenizers`, and `numpy` are optional runtime dependencies; if missing, routing falls back to lexical. |
| **`gateway`** | OpenAI-compatible embeddings REST endpoint with automatic fail-open fallback. | Centralized enterprise gateways and existing embedding proxies. | API key + Endpoint URL. |

### 2. Multi-Agent Ecosystem Support (MCP)

Skill Proof embeds a standard **Model Context Protocol (MCP)** server. External agents connect via Stdio to search and read skills without modifying their harness:

- `skill_search`: Hybrid RRF top-k search returning matching skill names, descriptions, and file paths.
- `skill_read`: Direct retrieval of full `SKILL.md` markdown content.
- `skill_select`: Single-skill deterministic routing decision.
- `skill_scan`: Multi-root catalog diagnostics and status.

### 4. Official Model Registry with Verified Checksums

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
python cli.py select --query "Review this pull request for syntax bugs, but do not use TDD"
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

MCP vector search defaults to lexical-only. To enable `local` or `gateway` embeddings, set these environment variables on the MCP server process: `SKILL_PROOF_EMBEDDING_PROVIDER`, `SKILL_PROOF_EMBEDDING_MODEL`, `SKILL_PROOF_VECTOR_DB`, and `SKILL_PROOF_MODELS_DIR`. For `gateway`, also set `SKILL_PROOF_GATEWAY_URL` and `SKILL_PROOF_GATEWAY_KEY_ENV`; the latter names the environment variable that holds the key. The key itself is never passed as an MCP setting or written to the audit log. The Hermes plugin continues to use its normal `config.yaml` settings.

---

## 🛠️ Command-Line Reference

```bash
# Display catalog skills, installed models, and provider status
python cli.py status

# Choose a provider and update Hermes config.yaml
python cli.py setup

# Route a user instruction to the best matching skill
python cli.py select --query "Analyze and optimize database SQL query performance"

# List available HuggingFace models, download sizes, and installation state
python cli.py models

# Prefetch and verify an embedding model (optional)
python cli.py prefetch --model Xenova/multilingual-e5-small

# Scan catalog roots and view diagnostics
python cli.py scan

# Evaluate scoring breakdown and char n-gram weights for a query
python cli.py eval "Design a clean responsive web dashboard"

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

`setup` prompts for `off`, `local`, or `gateway` and updates only `plugins.entries.skill-proof.settings` in the active Hermes profile's `config.yaml`. Use `--prefetch` to explicitly download and checksum-verify a selected local model; downloads do not start automatically.

For local inference, install its optional dependencies into the **Hermes runtime environment**. On Windows, run this from the Skill Proof checkout:

```powershell
& "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe" -m pip install -r requirements-local.txt
```

On macOS or Linux, use the Hermes virtual environment's Python instead:

```bash
~/.hermes/hermes-agent/venv/bin/python -m pip install -r requirements-local.txt
```

If Hermes uses a custom installation path, set `HERMES_PYTHON` to its Python executable; `setup --provider local` checks that runtime before writing local mode.

Then configure local mode and prefetch the model with `python cli.py setup --provider local --prefetch`. To check real inference, run `python -m unittest tests.test_v0150.ProviderAbstractionTests.test_local_embedder_real_inference_if_installed -v` with the Hermes virtual environment's Python from this checkout. The default `off` provider still has no third-party dependencies.

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
