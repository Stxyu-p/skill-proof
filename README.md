<p align="center">
  <img src="assets/logo.png" width="160" alt="Skill Proof Logo" />
</p>

# Skill Proof v2.0.0

**Universal Skill Platform, Zero-Duplication Hub & Deterministic Governance Gatekeeper for Autonomous AI Fleets**

[![PyPI](https://img.shields.io/pypi/v/skill-proof-router?style=flat-square&color=blue)](https://pypi.org/project/skill-proof-router/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Hub](https://img.shields.io/badge/hub-Canonical_NTFS_Junctions-07405E?style=flat-square)](#1-physical-hub--auto-healing-reconciler)
[![Token Shield](https://img.shields.io/badge/tokens--99%25_prompt_overhead-10b981?style=flat-square)](#2-just-in-time-jit-prompt-token-shield)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_warm_route-10b981?style=flat-square)](#two-tier-deterministic-routing-engine)
[![Protocol](https://img.shields.io/badge/mcp-Universal_Cross--CLI_stdio-8b5cf6?style=flat-square)](#3-universal-mcp-stdio-server)
[![Safety](https://img.shields.io/badge/audit-SHA--256_Hash_Chain-8b5cf6?style=flat-square)](#cryptographic-audit-trail--compliance)
[![Governance](https://img.shields.io/badge/governance-Immutable_SOP_%26_Tool_Gating-10B981?style=flat-square)](#6-immutable-sop-protection--write-guard)
[![Synergy](https://img.shields.io/badge/synergy-MemCore_Veto_Active-3B82F6?style=flat-square)](#4-memcore-persistent-constraint-veto)
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

## ⚡ 10 Core Engineering Capabilities

### 1. Physical Hub & Auto-Healing Reconciler
All playbooks are stored in a single canonical vault: `~/.agents/skills` (Global) and `./.agents/skills` (Project).
Every agent root (`~/.gemini/antigravity-cli/skills`, `AppData/Local/hermes/skills`, `.hermes/skills`) is wired directly via **NTFS Directory Junctions** (`_winapi.CreateJunction` on Windows, symlinks on POSIX).
- **Zero byte duplication** across the entire machine.
- Editing a skill file updates every agent immediately.
- Run `skill-proof vault reconcile` anytime to verify, repair, and link all client ecosystems idempotently.

### 2. Just-In-Time (JIT) Prompt Token Shield
Hermes Agent traditionally dumps all available skills into `<available_skills>` on every turn, burning **~3,451 tokens (13,114 characters)** per request.
Enabling `reduce_skill_prompt: true`:
- Intercepts outgoing LLM requests via middleware.
- Strips the catalog down to **only the winning skill and declared companions** (~80 characters).
- Achieves **99.0% prompt overhead reduction**, saving >3,000 tokens on every single turn.
- Fails open safely to a compact index if user intent is ambiguous or unselected.

### 3. Universal MCP stdio Server
Exposes Skill Proof tools to any MCP-capable host (Claude Code, Antigravity CLI/IDE, Cursor, OpenCode, Codex):
- `skill_select`: Fast deterministic routing returning winning skill and match evidence.
- `skill_search`: Keyword and semantic hybrid search across the canonical vault.
- `skill_read`: Inlines full `SKILL.md` content and linked reference files in one turn.
- Register across all detected clients in one command: `skill-proof install`.

### 4. MemCore Persistent Constraint Veto
Vector search natively fails on negative queries: searching *"do not use TDD"* produces a high cosine similarity match for `test-driven-development`.
Skill Proof queries `~/.memcore/memory.db` (< 0.05ms cached):
- Automatically extracts standing negative rules (e.g. *"ห้ามใช้ test-driven-development"*, *"never use synthetic tests"*).
- Vetoes conflicting playbooks before ranking runs, ensuring persistent user preferences are respected across every session.

### 5. Single-Source Rule Compiler (`skill-proof compile`)
Maintains unified instruction files across all agents:
- Reads canonical playbooks and Trinity protocols.
- Injects/updates bounded sections (`<!-- trinity:habit:start -->` ... `<!-- trinity:habit:end -->`) inside:
  - `AGENTS.md` (Hermes Agent & Antigravity)
  - `CLAUDE.md` (Claude Code)
  - `GEMINI.md` (Antigravity CLI)
  - `.cursorrules` (Cursor)
- Eliminates manual documentation duplication.

### 6. Immutable SOP Protection & Write-Guard
Critical operational playbooks must never be rewritten or poisoned by autonomous coding agents.
- **Tool Interceptor:** In `on_pre_tool_call`, inspects `skill_manage`, `patch`, and `write_file` calls. If the targeted skill specifies `immutable: true` or `governance: locked`, execution is immediately refused before disk I/O.
- **OS-Level Read-Only Attribute:** `skill-proof vault lock <skill>` applies POSIX / Windows `S_IREAD` read-only flags across all files in the skill folder.

### 7. Cross-Agent Session Handoff (`skill-proof handoff`)
Seamlessly switch between AI agents (e.g., from high-level planning in one agent to implementation in another CLI):
- `skill-proof handoff --target antigravity`:
  - Collects active task context, open work items, and pending issues.
  - Captures current `git status -s` and diff statistics.
  - Injects standing constraints from MemCore.
  - Outputs an executable, bounded Markdown payload (< 2,000 tokens) ready for immediate agent consumption.

### 8. Real-Usage Telemetry & Dead Skill Pruning
Ground truth lies in real production telemetry, not synthetic benchmarks.
- `skill-proof audit stats`: Analyzes `audit.jsonl` to calculate exact hit rates, top invoked playbooks, and identifies unused skills.
- `skill-proof audit prune [--dry-run]`: Safely archives dead playbooks (0 invocations over 30 days) to `~/.agents/archive/`, keeping active catalogs clean and fast.

### 9. Dynamic Tool Gating per Skill (Blast Radius Restriction)
Skills declaring `mode: readonly` or `forbidden_tools: [patch, write_file, terminal]` restrict the agent's tool execution environment:
- Review and research playbooks (`code-review`, `diagnosing-bugs`) are barred from invoking file-write or destructive shell tools.
- Violations are intercepted and logged as `compliance: failed` audit receipts.

### 10. Local Patch Layer for Upstream Skills
When skills are updated via `npx skills update` from upstream repositories (e.g. `mattpocock/skills`), local customizations and language-specific prompt enhancements are preserved:
- Stores unified diff patches under `~/.agents/patches/<skill>.patch`.
- `skill-proof vault patch --patch-op rebase` automatically reapplies local rules cleanly on top of updated upstream playbooks.

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
