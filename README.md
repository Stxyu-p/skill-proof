<p align="center">
  <img src="assets/logo.png" width="160" alt="Skill Proof Logo" />
</p>

# Skill Proof

**Global Skill Vault & Deterministic In-Process Skill Router for Autonomous AI Fleets**

[![PyPI](https://img.shields.io/pypi/v/skill-proof-router?style=flat-square&color=blue)](https://pypi.org/project/skill-proof-router/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Storage](https://img.shields.io/badge/vault-Global_NTFS_/_Symlink-07405E?style=flat-square)](#the-global-skill-vault-skill-proofskills)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_warm_route-10b981?style=flat-square)](#two-tier-deterministic-routing-engine)
[![Safety](https://img.shields.io/badge/audit-SHA--256_Hash_Chain-8b5cf6?style=flat-square)](#cryptographic-audit-trail--compliance)
[![Governance](https://img.shields.io/badge/governance-Immutable_SOP_Lock-10B981?style=flat-square)](#immutable-sop-protection--write-guard)
[![Synergy](https://img.shields.io/badge/synergy-MemCore_Veto_Active-3B82F6?style=flat-square)](#memcore-persistent-constraint-veto)
[![License: MIT](https://img.shields.io/badge/license-MIT-slate?style=flat-square)](LICENSE)

---

## 🚀 Overview

As engineering teams adopt autonomous coding agents, playbooks and `SKILL.md` documents proliferate into chaos:
1. **Dispersed Folder Sprawl:** Skills are duplicated across `.agents/skills`, `.claude/skills`, `.cursor/skills`, `.gemini/skills`, and `~/.hermes/skills`. Editing a skill in one directory leaves all other agents with obsolete instructions.
2. **System Prompt Bloat:** Dumping 50–100 full skill descriptions into every single turn's prompt burns **10,000–25,000 tokens** per request before the agent even reads the user's message.
3. **Nondeterministic Guesswork:** Calling a secondary routing LLM adds 1.5–3.5 seconds of latency per turn, burns API credits, and frequently ignores negative user constraints (*"do not use TDD"*).
4. **SOP Drift & Accidental Overwrites:** Autonomous agents frequently hallucinate edits or overwrite production rules in `SKILL.md` files while executing coding tasks.

### The Solution: One Global Vault + Sub-2ms Deterministic Router
**Skill Proof** consolidates all your playbooks into a single source of truth (`~/.skill-proof/skills`), routes incoming tasks in **< 2 ms** without LLM routing calls, enforces immutable SOP read-only locks, and verifies cryptographic compliance.

---

## 🥊 Market Comparison: Skill Proof vs Industry Alternatives

| Feature / Metric | **Skill Proof v1.5.0** 🌟 | System Prompt Dump (Default) | Secondary LLM Tool Router | LangChain ToolRetriever |
| :--- | :---: | :---: | :---: | :---: |
| **Routing Mechanism** | **In-Process Two-Tier (Lexical + ONNX)** | Full catalog prompt injection | Secondary API call | Vector similarity retrieval |
| **Routing Latency** | **< 2 ms (0.4 ms measured)** | 0 ms (pre-injected) | 1,500 – 3,500 ms | 200 – 800 ms |
| **Token Overhead per Turn** | **0 – 200 tokens (Winner only)** | **10,000 – 25,000 tokens** | Schema tokens | Variable query tokens |
| **Multi-Lingual Negation Veto** | ✅ **6 Languages (Thai, EN, JA, KO, VI, ZH)** | ❌ Model ignores negative prompt | ⚠️ Frequent model misinterpretations | ❌ Negative vectors still score high |
| **MemCore Constraint Synergy** | ✅ **Auto-vetoes disproven skills** | ❌ None | ❌ None | ❌ None |
| **Immutable SOP Protection** | ✅ **OS Read-Only + Tool Guard** | ❌ None (Agent overwrites SOP) | ❌ None | ❌ None |
| **Auditability** | ✅ **SHA-256 Hash Chained Receipts** | ❌ Opaque | ❌ Black box | ❌ None |
| **Network & Privacy** | **100% Offline / Local CPU** | Local | Cloud LLM dependency | Usually requires Cloud DB |

---

## ⚡ Core Engineering Capabilities

### 1. Two-Tier Deterministic Routing Engine (< 2 ms)
- **Tier 1 (Fast Lexical & Fuzzy Path):** Inverted index, RapidFuzz C++ token alignment, character n-grams for spaceless scripts (Thai, CJK), and pre-filter negation vetoes. Resolves queries in **~0.4 ms**.
- **Tier 2 (Local ONNX Vector Path):** When lexical margin is borderline, fuses dense vector cosine similarity (`Xenova/multilingual-e5-small`) via Reciprocal Rank Fusion ($k=60$) running directly on local CPU via `onnxruntime` — zero external daemons or open network ports.
- **Selective Prompt Reduction:** When enabled, removes the 50-skill catalog dump from the system prompt, injecting only the single winning skill description, saving thousands of tokens per turn.

### 2. Multi-Lingual Negation Veto
Standard vector search fails on negative prompts: searching *"do not use TDD"* yields a high cosine similarity match for `test-driven-development`! Skill Proof employs regularized negation parsers supporting 6 languages (Thai, English, Japanese, Korean, Vietnamese, Chinese), pruning vetoed playbooks before ranking runs.

### 3. MemCore Persistent Constraint Synergy
Skill Proof integrates with local `~/.memcore/memory.db`. Any standing user constraint or disproven rule recorded in MemCore (e.g. *"ห้ามใช้ test-driven-development ในโปรเจกต์นี้"* or *"never use X"*) is automatically queried with a 30-second in-process cache (< 0.05 ms) and applied as an automated veto.

### 4. Immutable SOP Protection & Write-Guard
Critical team playbooks must never be rewritten by autonomous coding agents.
- **Tool Interceptor Guard:** In `on_pre_tool_call`, checks `skill_manage`, `patch`, and `write_file` invocations. If the targeted skill specifies `immutable: true`, `governance: locked`, or `protected: true` in its YAML frontmatter, the modification is immediately refused before disk I/O.
- **OS-Level Read-Only Attribute:** `skill-proof vault lock <skill>` applies DOS/POSIX read-only file attributes across the directory, making tampering impossible even if tools bypass application guards.

### 5. Cryptographic Audit Trail & Compliance Verification
Every routing decision appends an immutable, SHA-256 hash-chained receipt to `audit.jsonl`. Verify audit trail integrity anytime with `/skill-proof audit-verify`.

---

## 🛠️ First-Time Setup (5 Minutes)

### Step 1: Install Skill Proof Router
```bash
pip install skill-proof-router
```

### Step 2: Run Unified Client Setup Wizard
Automatically creates the Global Skill Vault (`~/.skill-proof/skills`), migrates existing agent skills, verifies local ONNX vector embeddings, and registers the stdio MCP server across all detected AI coding clients:
```bash
skill-proof install
skill-proof setup local
```
Auto-detects and links:
- **Claude Code** (`~/.claude.json`) & **Claude Desktop**
- **Cursor** (`~/.cursor/mcp.json`), **Windsurf**, and **VS Code (Copilot)**
- **Google Antigravity & Antigravity IDE** (`mcp_config.json`)
- **Gemini CLI** (`settings.json`) & **Codex CLI** (`config.toml`)
- **OpenCode** & **Cline**

### Step 3: Verify Catalog & Router Status
```bash
skill-proof status
skill-proof roots
```

---

## ⚙️ Native Hermes Agent Plugin Configuration

To install Skill Proof as a native plugin in Hermes Agent:
```bash
# Windows
git clone https://github.com/Stxyu-p/skill-proof.git "$env:LOCALAPPDATA\hermes\plugins\skill-proof"

# Linux / macOS
git clone https://github.com/Stxyu-p/skill-proof.git ~/.hermes/plugins/skill-proof

# Enable plugin in Hermes
hermes plugins enable skill-proof
```

### Configuration Options (`~/.hermes/config.yaml` or `plugin.yaml`)
```yaml
plugins:
  entries:
    skill-proof:
      settings:
        mode: nudge                 # observe (log only), nudge (prompt hints), or enforce-tools
        reduce_skill_prompt: false  # true = strip unselected skills from system prompt (saves 10K+ tokens)
        embedding_provider: local   # local (onnxruntime CPU), gateway, or off
        min_score: 0.28             # Minimum confidence score for auto-selection
        min_margin: 0.05            # Minimum score gap required between #1 and #2 candidate
        max_candidates: 3           # Top N candidates to inspect
        audit_log: true             # Enable SHA-256 hash-chained receipts in plugin-data
```

---

## 💻 Slash Commands & Standalone CLI

### Hermes In-Chat Slash Commands
- **`/skill-proof status`**: Displays catalog size, detected roots, embedding status, and routing timing.
- **`/skill-proof explain`**: Explains why the current turn selected its active skill.
- **`/skill-proof why <skill-name>`**: Detailed diagnostic report showing candidate score breakdown, veto status, or ranking gap.
- **`/skill-proof audit-verify`**: Cryptographically verifies the SHA-256 hash chain of all historical routing receipts.
- **`/skill-proof refresh`**: Invalidates in-memory catalog and re-scans skill roots immediately.

### Global Vault Management CLI
```bash
# Inspect all consolidated skills in the global vault
skill-proof vault list

# Lock a critical SOP playbook with OS read-only attributes
skill-proof vault lock critical-production-sop

# Unlock an SOP for editing
skill-proof vault unlock critical-production-sop

# Link global vault skills into a new repository workspace
skill-proof vault link ./my-project/.agents/skills
```

---

## 🌐 Part of the Global Agent Trinity

**Skill Proof** forms the **Global Agent Trinity** alongside **trimtok** and **MemCore** — unifying context compression, governed shared memory, and skill execution across all your AI coding clients:

| Pillar | Hub | Role |
| :--- | :--- | :--- |
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, web, and JSON bloat by 38–48% (real telemetry) with instant `#c1` CCR recovery. |
| **[MemCore](https://github.com/Stxyu-p/memcore)** | `~/.memcore` | **Governed Memory:** Shared bitemporal memory with hard SQL scope boundaries, 1-bit vector quantization (32x smaller RAM), and Tainted Context Guard. |
| **[Skill Proof](https://github.com/Stxyu-p/skill-proof)** | `~/.skill-proof` | **Skill Vault & Router:** Sub-2ms deterministic router, multi-lingual negation veto, and OS NTFS read-only Immutable SOP protection. |

```bash
# Install and link all three pillars in one pass
pip install trimtok memcore-omnix skill-proof-router
trimtok install && memcore install && skill-proof install
```

---

## 📄 License

Distributed under the [MIT License](LICENSE). Designed and crafted for robust, high-leverage autonomous software engineering.
