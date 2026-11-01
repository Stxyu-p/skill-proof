<p align="center">
  <img src="assets/logo.png" width="160" alt="Skill Proof Logo" />
</p>

# Skill Proof v2.0.0

**Universal Skill Platform, Zero-Duplication Hub & Deterministic Governance Gatekeeper for Autonomous AI Fleets**

[![PyPI](https://img.shields.io/pypi/v/skill-proof-router?style=flat-square&color=blue)](https://pypi.org/project/skill-proof-router/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Hub](https://img.shields.io/badge/hub-Canonical_NTFS_Junctions-07405E?style=flat-square)](#system-architecture--core-capabilities)
[![Token Shield](https://img.shields.io/badge/tokens--99%25_prompt_overhead-10b981?style=flat-square)](#system-architecture--core-capabilities)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_warm_route-10b981?style=flat-square)](#system-architecture--core-capabilities)
[![Protocol](https://img.shields.io/badge/mcp-Universal_Cross--CLI_stdio-8b5cf6?style=flat-square)](#system-architecture--core-capabilities)
[![Safety](https://img.shields.io/badge/audit-SHA--256_Hash_Chain-8b5cf6?style=flat-square)](#cryptographic-audit-trail--compliance)
[![Governance](https://img.shields.io/badge/governance-Immutable_SOP_%26_Tool_Gating-10B981?style=flat-square)](#system-architecture--core-capabilities)
[![Synergy](https://img.shields.io/badge/synergy-MemCore_Veto_Active-3B82F6?style=flat-square)](#system-architecture--core-capabilities)
[![License: MIT](https://img.shields.io/badge/license-MIT-slate?style=flat-square)](LICENSE)

---

## 🚀 Overview: Unified Playbook Management for AI Fleets

As engineering teams adopt multi-agent autonomous coding fleets (Hermes Agent, Google Antigravity, Claude Code, Cursor, OpenCode, Codex), team playbooks (`SKILL.md`) rapidly degrade into chaos:
1. **Physical Folder Sprawl:** The same 38–100 skills are duplicated across `.agents/skills`, `.claude/skills`, `.cursor/skills`, `.gemini/skills`, and `~/.hermes/skills`. Editing a playbook in one directory leaves all other agents executing obsolete, conflicting instructions.
2. **System Prompt Bloat:** Dumping 100+ skill descriptions into every turn burns **3,000–5,000 tokens per request** before the model reads the user prompt.
3. **Cross-Process Isolation:** AI CLI tools run in isolated language sandboxes (Go, TypeScript, C++, Python) and will not route requests through an external runtime middleman.
4. **SOP Drift & Accidental Overwrites:** Autonomous agents frequently hallucinate edits or overwrite production rules in `SKILL.md` files while executing coding tasks.

### The Solution: One Physical Hub + Universal MCP + Lean Governance Gatekeeper
Skill Proof consolidates playbooks into a single source of truth, eliminates prompt token bloat, and enforces strict governance rails across all AI coding hosts:

```text
                           [ skills.sh / GitHub / Community ]
                                           │
                                    (Install / Update)
                                           ▼
                           ┌───────────────────────────────┐
                           │    ~/.agents/skills (Hub)     │  <─── Single Source of Truth
                           └───────────────┬───────────────┘
                                           │
                      ┌────────────────────┼────────────────────┐
                (NTFS Junction)      (NTFS Junction)      (NTFS Junction)
                      ▼                    ▼                    ▼
          ~/.gemini/antigravity   ~/.claude/skills     AppData/Local/hermes/skills
               (Antigravity)        (Claude Code)             (Hermes Agent)
                      │                    │                        │
                      ▼                    ▼                        ▼
               [ MCP Server ]       [ MCP Server ]        [ Native Hermes Plugin ]
             (skill-proof mcp)    (skill-proof mcp)                 │
                      │                    │                        ▼
                      └────────────────────┴──────────► ┌───────────────────────┐
                                                        │  reduce_skill_prompt  │
                                                        │  (-99% Prompt Bloat)  │
                                                        └───────────┬───────────┘
                                                                    │
                                                        ┌───────────▼───────────┐
                                                        │  MemCore Veto Engine  │
                                                        │  (~/.memcore/memory)  │
                                                        └───────────────────────┘
```

---

## 🥊 Market Comparison: Skill Proof v2.0 vs Alternatives

| Feature / Metric | **Skill Proof v2.0** 🌟 | Legacy Multi-Dir Setup | Secondary LLM Tool Router | System Prompt Catalog Dump |
| :--- | :---: | :---: | :---: | :---: |
| **Storage Architecture** | **1 Canonical Hub + OS Junctions** | 4–6 duplicate copies (150+ folders) | Per-project copies | In-memory string dump |
| **System Prompt Bloat** | **~80 chars (JIT Winner Only)** | Full catalog (13KB+ / ~3,450 tokens) | Schema tokens | 10,000 – 25,000 tokens |
| **Token Reduction** | **99.0% savings per turn** | 0% | Variable | 0% |
| **Routing Mechanism** | **In-Process RapidFuzz + ONNX Vector** | Manual user invocation | Secondary API call (1.5–3.5s) | LLM context reading |
| **Routing Latency** | **< 2 ms warm (0.4–4.6 ms measured)** | Manual | 1,500 – 3,500 ms | 0 ms (pre-injected bloat) |
| **Cross-Agent Support** | **Universal stdio MCP Server** | Proprietary formats | Host-specific | Host-specific |
| **Multi-Lingual Veto** | ✅ **6 Languages (Thai, EN, JA, KO, VI, ZH)** | ❌ Negative prompt ignored | ⚠️ Frequent LLM misinterpretation | ❌ Negative vectors score high |
| **MemCore Synergy** | ✅ **Automatic Veto from Memory DB** | ❌ None | ❌ None | ❌ None |
| **Immutable SOP Protection** | ✅ **OS Read-Only + Tool Interceptor** | ❌ None (Agent rewrites SOP) | ❌ None | ❌ None |
| **Cross-Model Handoff** | ✅ **1-Click Launch Payload (`handoff`)** | ❌ Manual prompt copying | ❌ None | ❌ None |
| **Telemetry & Pruning** | ✅ **Real-Usage Audit Analyzer** | ❌ Dead skills accumulate | ❌ Opaque logs | ❌ None |
| **Network & Privacy** | **100% Offline / Local CPU** | Local | Cloud LLM dependency | Local |

---

## ⚡ System Architecture & Core Capabilities

Skill Proof delivers an integrated suite of four production subsystems engineered for zero-overhead, multi-agent fleet operations:

| Subsystem | Capability | Technical Implementation | Operational Impact & CLI Reference |
| :--- | :--- | :--- | :--- |
| **Physical Storage & Hub** | **Canonical Single-Source Vault** | Centralizes playbooks at `~/.agents/skills`; fans out to all agent environments via OS-level NTFS Directory Junctions (`_winapi.CreateJunction` on Windows, symlinks on POSIX). | **0 Duplicate Bytes.** Instant real-time synchronization across all agent directories.<br>`skill-proof vault reconcile` |
| | **Local Upstream Patch Layer** | Preserves local customizations and language-specific prompt rules across upstream `npx skills update` runs using unified diffs (`~/.agents/patches/`). | Rebase local customizations cleanly over external updates without loss.<br>`skill-proof vault patch --patch-op rebase` |
| **Token & Prompt Engine** | **JIT Prompt Token Shield** | Middleware intercepts prompt construction; strips full 100+ skill catalog down to only the active winning playbook and declared companions. | **99.0% Token Overhead Cut** (>3,000 tokens saved per turn).<br>`reduce_skill_prompt: true` in config |
| | **Two-Tier Deterministic Router** | Tier 1: In-process RapidFuzz C++ token alignment (< 0.5 ms). Tier 2: Local CPU ONNX vector fusion (`Xenova/multilingual-e5-small`) via Reciprocal Rank Fusion. | **Sub-2ms Routing Latency.** Completely offline; zero external API calls or network latency.<br>`skill-proof select "<query>"` |
| **Cross-Agent Integration** | **Universal stdio MCP Server** | Implements standard Model Context Protocol over stdio JSON-RPC, exposing `skill_select`, `skill_search`, and `skill_read` to Claude Code, Antigravity, Cursor, and OpenCode. | Standardized tool access across all language runtimes (Go, TypeScript, Python).<br>`skill-proof install` |
| | **Cross-Agent Session Handoff** | Serializes active tasks, open work items, git diff statistics, and memory constraints into a structured launch payload. | Zero-friction context transfer when switching between agent models.<br>`skill-proof handoff --target <agent>` |
| | **Single-Source Rule Compiler** | Injects bounded Global Agent Trinity guidelines into `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, and `.cursorrules` in-place. | Eliminates manual documentation divergence across heterogeneous agent tools.<br>`skill-proof compile` |
| **Governance & Safety** | **Immutable SOP Lock & Write-Guard** | Intercepts `write_file`, `patch`, and `skill_manage` tool calls for `immutable: true` skills; enforces OS `S_IREAD` read-only file attributes. | Absolute protection against autonomous agent hallucinated playbook edits.<br>`skill-proof vault lock <skill>` |
| | **Dynamic Blast-Radius Tool Gating** | Enforces `mode: readonly` and `forbidden_tools` per skill, barring audit/review playbooks from executing destructive write or terminal commands. | Restricts tool execution environment based on active playbook intent.<br>`mode: readonly` in frontmatter |
| | **MemCore Constraint Veto** | Queries `~/.memcore/memory.db` (< 0.05 ms cached) for negative constraints (`ห้ามใช้`, `do not use`) across 6 languages, pruning candidates pre-ranking. | Prevents negated queries from scoring high semantic similarity.<br>Automated in routing pipeline |
| | **Production Telemetry & Pruning** | Mines `audit.jsonl` execution receipts to track actual hit frequencies and safely archive dead skills (0 hits over 30 days) to `~/.agents/archive/`. | Evidence-based catalog maintenance; eliminates dead weight from catalogs.<br>`skill-proof audit stats` / `prune` |

---

## 🛠️ Installation & Setup

### 1. Install Skill Proof CLI & Router
```bash
# Install via pip
pip install skill-proof-router

# Install optional local CPU vector dependencies (onnxruntime + tokenizers)
pip install onnxruntime tokenizers numpy
```

### 2. Run Auto-Healing Reconciler
Automatically initializes the canonical vault (`~/.agents/skills`) and links all agent directories via NTFS Directory Junctions:
```bash
skill-proof vault reconcile
```

### 3. Register MCP Server Across AI CLIs
Registers the stdio MCP server in Antigravity, Claude Code, Cursor, and OpenCode:
```bash
skill-proof install
skill-proof clients
```

### 4. Enable Native Hermes Agent Plugin
```bash
# Windows
git clone https://github.com/Stxyu-p/skill-proof.git "$env:LOCALAPPDATA\hermes\plugins\skill-proof"

# Linux / macOS
git clone https://github.com/Stxyu-p/skill-proof.git ~/.hermes/plugins/skill-proof

# Enable and configure in Hermes
hermes plugins enable skill-proof
hermes config set plugins.entries.skill-proof.settings.reduce_skill_prompt true
hermes config set plugins.entries.skill-proof.settings.embedding_provider local
```

---

## 💻 CLI & Slash Command Reference

### Standalone CLI Commands
```bash
# Reconcile & auto-heal all client junction links
skill-proof vault reconcile

# Inspect vault status and client linkages
skill-proof vault status

# Lock / unlock an SOP playbook with OS read-only attributes
skill-proof vault lock <skill-name>
skill-proof vault unlock <skill-name>

# Manage local customization patches for upstream skills
skill-proof vault patch --patch-op export --skill <name> --patch-content "..."
skill-proof vault patch --patch-op rebase

# Export session handoff payload for another agent
skill-proof handoff --target antigravity
skill-proof handoff --target claude-code

# Inspect production telemetry and prune dead skills
skill-proof audit stats
skill-proof audit prune --dry-run
skill-proof audit prune --skill <skill-name>

# Compile Global Trinity protocol into AGENTS.md / CLAUDE.md / GEMINI.md
skill-proof compile

# Route intent deterministically (< 2ms)
skill-proof select "ช่วยรีวิว PR โค้ดหน่อยครับ"
```

### Hermes In-Chat Slash Commands
- **`/skill-proof status`**: Displays catalog size, detected roots, embedding status, and routing latency.
- **`/skill-proof explain`**: Explains why the current turn selected its active skill with ranked candidate scores.
- **`/skill-proof why <skill-name>`**: Detailed diagnostic report showing candidate score breakdown, veto status, or ranking gap.
- **`/skill-proof stats`**: Displays real telemetry hit counts, miss rate, and top invoked skills.
- **`/skill-proof audit-verify`**: Cryptographically verifies the SHA-256 hash chain of all historical routing receipts.
- **`/skill-proof refresh`**: Invalidates in-memory catalog cache and re-scans skill roots immediately.

---

## 🌐 Part of the Global Agent Trinity

**Skill Proof** forms the **Global Agent Trinity** alongside **trimtok** and **MemCore** — unifying context compression, governed shared memory, and skill execution across all your AI coding clients:

| Pillar | Hub | Role |
| :--- | :--- | :--- |
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, web, and JSON bloat by 38–48% (real telemetry) with instant `#c1` CCR recovery. |
| **[MemCore](https://github.com/Stxyu-p/memcore)** | `~/.memcore` | **Governed Memory:** Shared bitemporal memory with hard SQL scope boundaries, 1-bit vector quantization (32x smaller RAM), and Tainted Context Guard. |
| **[Skill Proof](https://github.com/Stxyu-p/skill-proof)** | `~/.agents/skills` | **Universal Skill Hub & Gatekeeper:** Zero-duplication NTFS junctions, JIT prompt token shield (-99% bloat), and OS-level Immutable SOP protection. |

```bash
# Install and link all three pillars in one pass
pip install trimtok memcore-omnix skill-proof-router
trimtok install && memcore install && skill-proof install
```

---

## 🧪 Verification & Automated Testing

Skill Proof includes a comprehensive 153-test test suite verifying every component:

```bash
# Run full unit & integration test suite
python -m unittest discover -s tests -t .

# Run specific capability tests
python tests/test_memcore_synergy.py       # MemCore negative constraint vetoes
python tests/test_dynamic_tool_gating.py   # Readonly mode & tool gating
python tests/test_immutable_guard.py       # Pre-tool SOP write protection
```

---

## 📄 License

Distributed under the [MIT License](LICENSE). Designed and crafted for robust, high-leverage autonomous software engineering.
