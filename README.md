<p align="center">
  <img src="assets/logo.png" width="160" alt="Skill Proof Logo" />
</p>

# Skill Proof

**Global Skill Vault & Deterministic In-Process Skill Router for AI Coding Agents**

Consolidates all your dispersed `SKILL.md` playbooks into one global vault (`~/.skill-proof/skills`), protects strict SOPs with OS read-only locks, and routes agent requests in **< 2 ms** with **zero LLM routing calls** and zero open network ports.

[![PyPI](https://img.shields.io/pypi/v/skill-proof-router?style=flat-square&color=blue)](https://pypi.org/project/skill-proof-router/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)](https://www.python.org/)
[![Storage](https://img.shields.io/badge/vault-Global_NTFS_/_Symlink-07405E?style=flat-square)](#the-global-skill-vault-skill-proofskills)
[![Latency](https://img.shields.io/badge/latency-%3C_2ms_warm_route-10b981?style=flat-square)](#two-tier-deterministic-routing-engine)
[![Safety](https://img.shields.io/badge/audit-SHA--256_Hash_Chain-8b5cf6?style=flat-square)](#cryptographic-audit-trail--compliance)
[![Governance](https://img.shields.io/badge/governance-Immutable_SOP_Lock-10B981?style=flat-square)](#immutable-sop-protection--write-guard)
[![License: MIT](https://img.shields.io/badge/license-MIT-slate?style=flat-square)](LICENSE)

---

## The Problem: Skill Fragmentation & Prompt Bloat

As teams and developers adopt autonomous coding agents, skills multiply into chaos:
1. **Dispersed Folder Sprawl:** Skills are duplicated and scattered across `.agents/skills`, `.claude/skills`, `.cursor/skills`, `.gemini/skills`, and `~/.hermes/skills`. Editing a skill in one directory leaves all other agents with outdated playbooks.
2. **System Prompt Bloat:** Injecting 50–100 full skill descriptions into every turn's base prompt burns thousands of context tokens on every request.
3. **Nondeterministic Guesswork:** Calling a secondary routing LLM adds 1–3 seconds of latency, incurs extra token costs, and frequently misroutes tasks or ignores negative user constraints (e.g., *"do not use TDD"*).

---

## The Solution: One Global Vault + Sub-2ms Deterministic Router

**Skill Proof** replaces manual duplication and prompt bloat with an in-process, dual-stage routing engine:

1. **Global Skill Vault (`~/.skill-proof/skills`):** Consolidates all skills into a single source of truth. Links skills into client directories via native NTFS junctions (Windows) or POSIX symlinks (macOS/Linux) without duplicating disk space.
2. **Dual-Stage In-Process Router (< 2 ms):**
   - **Fast Lexical & Fuzzy Path:** Pre-computed inverted index, RapidFuzz C++ token matching, Thai/CJK character n-grams, and 6-language negation vetoes (`"do not use X"`) that prune unwanted skills before scoring.
   - **Local ONNX Vector Path:** When lexical scores are borderline, fuses dense vector cosine similarity (`Xenova/multilingual-e5-small`) via Reciprocal Rank Fusion ($k=60$) in pure CPU RAM — zero external daemons.
3. **Cryptographic Audit Trail & Auto-Compliance:** Every routing decision appends an immutable, SHA-256 hash-chained receipt to `audit.jsonl` and automatically verifies whether the selected skill was loaded during the turn.

---

## Core Comparison: With vs Without Skill Proof

| Dimension | Without Skill Proof | With Skill Proof |
| :--- | :--- | :--- |
| **Skill Storage** | Duplicated across `.agents`, `.claude`, `.cursor`, `.gemini`, `.hermes` | **One canonical vault (`~/.skill-proof/skills`)** shared by all agents |
| **Routing Latency** | 1–3 seconds (secondary LLM call) or full-catalog prompt bloat | **< 2 ms** deterministic in-process scoring |
| **Negative Constraints** | *"do not use X"* is frequently ignored by prompt keywords | **6-language Negation Veto** prunes vetoed skills before ranking |
| **Token Cost** | Every skill description is sent on every turn | Confident selection injects only the winner (`reduce_skill_prompt`) |
| **Ambiguity Handling** | Agent guesses blindly between overlapping skills | Returns an explicit `ambiguous` shortlist or clean `no_match` |
| **Auditability** | Opaque black box with no record of why a tool won | **SHA-256 hash-chained receipts** + `/skill-proof why <skill>` |

---

## One-Command Setup Wizard

Install Skill Proof and run the Setup Wizard to initialize the Global Skill Vault, automatically migrate existing skills, pre-warm local ONNX embeddings, and register the stdio MCP server across all detected AI coding clients:

```bash
pip install skill-proof-router
skill-proof install
```

`skill-proof install` detects and automatically registers:
- **Claude Code** (`~/.claude.json`) & **Claude Desktop**
- **Google Antigravity & Antigravity IDE** (`mcp_config.json`)
- **Gemini CLI** (`settings.json`) & **Codex CLI** (`config.toml`)
- **Cursor** (`~/.cursor/mcp.json`), **Windsurf**, **VS Code (Copilot)**, **OpenCode**, and **Cline**

Verify client registrations and catalog health anytime:

```bash
skill-proof clients
skill-proof status
skill-proof scan
```

---

## The Global Skill Vault (`~/.skill-proof/skills`)

Manage all your coding skills in one centralized location:

```bash
# Migrate physical skills from all detected agent folders into the canonical vault
skill-proof vault migrate

# Check vault inventory and client link status
skill-proof vault status

# Link all vault skills into an agent directory (NTFS junction or symlink)
skill-proof vault link-all --to ~/.cursor/skills
skill-proof vault pin --skill systematic-debugging --to ~/.claude/skills
```

---

## Immutable SOP Protection & Write-Guard

Protect your strict SOP playbooks against autonomous modification, upstream self-improvement rewrites, and unintended agent edits:

1. **Frontmatter Governance Flag:** Add `immutable: true`, `governance: locked`, or `protected: true` to your `SKILL.md` frontmatter:
   ```yaml
   ---
   name: production-deployment-sop
   description: Mandatory pre-flight checklist for production releases
   immutable: true
   ---
   ```
2. **Dual-Layer Defense:**
   - **Filesystem Level:** `set_skill_readonly()` enforces OS read-only permissions (`chmod 444` / `stat.S_IREAD`) on all vault-linked files.
   - **Hermes Tool Interceptor:** `on_pre_tool_call` executes a fast-path (< 0.1 ms) guard that rejects `skill_manage`, `patch`, and `write_file` tool calls before they touch disk.

---

## Two-Tier Deterministic Routing Engine

### Decision States
- **`selected` (Clear Winner):** High confidence score with a decisive margin over the runner-up. The embedding layer is bypassed via `lexical_fastpath` (< 1 ms).
- **`ambiguous` (Close Competitors):** Multiple skills score within `min_margin`. Returns a ranked candidate shortlist instead of guessing.
- **`no_match` (Clean Abstention):** No skill clears threshold. Skill Proof abstains so unrelated playbooks are never forced onto the agent.

### Routing CLI & Diagnostics

```bash
# Deterministically select a skill for a task
skill-proof select --query "Review this PR for syntax bugs, but do not use TDD"

# Interactive query evaluation: token breakdown, n-grams, and full ranking matrix
skill-proof eval "my page keeps crashing on login"

# Detect near-duplicate or overlapping skills across roots
skill-proof overlap

# Suggest threshold calibration based on audit history
skill-proof calibrate
```

### MCP Tools Exposed

When registered via MCP, hosts gain five deterministic tools:
- **`skill_select`**: Evaluates a user task and returns a deterministic routing decision (`selected`, `ambiguous`, or `no_match`), companions, and fleet hints.
- **`skill_search`**: Hybrid lexical + vector RRF search across skill names, tags, aliases, and descriptions.
- **`skill_read`**: Reads the full body of a skill playbook on demand without bloating the base system prompt.
- **`skill_scan`**: Scans all skill roots and reports catalog health and diagnostic counts.
- **`skill_roots`**: Lists active skill root directories and their precedence order.

---

## Native Hermes Agent Plugin

Skill Proof integrates natively into Hermes Agent without an external MCP layer:

```bash
# Linux / macOS
git clone https://github.com/Stxyu-p/skill-proof.git ~/.hermes/plugins/skill-proof

# Windows
git clone https://github.com/Stxyu-p/skill-proof.git "$env:LOCALAPPDATA\hermes\plugins\skill-proof"

# Enable plugin in Hermes
hermes plugins enable skill-proof
```

In-session slash commands available in Hermes:
- `/skill-proof status` — Compact turn state and catalog summary.
- `/skill-proof why <skill>` — Full scoring breakdown for why a skill won, lost, or was vetoed.
- `/skill-proof stats` — Hit rates, misses, and overrides from `audit.jsonl`.
- `/skill-proof trace` — Cryptographic JSON receipt for the turn.

---

## Part of the Global Agent Trinity

Pairing **Skill Proof** with **MemCore** and **trimtok** completes the **Global Agent Trinity** — connecting all your AI coding clients to three shared local hubs without open ports or daemons:

| Pillar | Hub | Role |
| :--- | :--- | :--- |
| **[Skill Proof](https://github.com/Stxyu-p/skill-proof)** | `~/.skill-proof` | **Skill Vault & Router:** Consolidates all `SKILL.md` files into one global vault and routes tasks in `< 2 ms` without LLM routing calls. |
| **[MemCore](https://github.com/Stxyu-p/memcore)** | `~/.memcore` | **Governed Memory:** Shared bitemporal memory with hard SQL scope boundaries, source-file SHA-256 tracking, and zero hallucinated drift. |
| **[trimtok](https://github.com/Stxyu-p/trimtok)** | `~/.trimtok` | **Context Shield:** Cuts terminal, traceback, diff, and JSON bloat by 38–48% (real telemetry) with instant `#c1` CCR recovery. |

```bash
# Install and link all three tools in one pass
pip install skill-proof-router memcore-omnix trimtok
skill-proof install && memcore install && trimtok install
```

---

## License

Distributed under the [MIT License](LICENSE). Designed and built for robust, real-world autonomous engineering.
