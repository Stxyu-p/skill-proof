<div align="center">

# 🛡️ Skill Proof <sub>v0.4.1</sub>

**Local skill routing for Hermes agents: deterministic selection, tool gating, truthful receipts**

*Python 3.11, stdlib only, offline, zero external calls, 7 hooks*

[![Release: v0.4.1](https://img.shields.io/badge/Release-v0.4.1-10b981?style=for-the-badge)](https://github.com/Stxyu-p/skill-proof/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-f59e0b?style=for-the-badge)](LICENSE)
[![Hermes: Native Plugin](https://img.shields.io/badge/Hermes-Native_Plugin-7B61FF?style=for-the-badge)](https://github.com/NousResearch/hermes-agent)

![Tests: 73 OK](https://img.shields.io/badge/Tests-73_OK-brightgreen?style=flat-square)
![Routing: 12 of 12](https://img.shields.io/badge/Routing-12_of_12-brightgreen?style=flat-square)
![Hooks: 7](https://img.shields.io/badge/Hooks-7-blue?style=flat-square)
![Dependencies: Zero](https://img.shields.io/badge/Dependencies-Zero-success?style=flat-square)
![Python: 3.11](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square)

</div>

---

## ⚡ Overview

**Skill Proof** is a Hermes standalone plugin for local skill selection and auditable load evidence. It scans configured `SKILL.md` roots each user turn, selects one skill with deterministic lexical ranking, gates operational tools by mode, and records a compact receipt with real evidence.

No model call. No network. No vector database. Python 3.11 with stdlib only. MemCore remains the memory provider, this plugin neither reads nor writes MemCore data.

---

## 🌟 Why Skill Proof?

Most agent setups pick skills by prompt guesswork. The wrong skill loads, disabled skills get suggested anyway, a veto like `don't use X` still selects X, short queries match inside longer words, and load claims carry no evidence.

**Skill Proof routes differently:**

- 🎯 **Deterministic lexical ranking:** explicit `$name`, `skill:name`, and English or Thai phrase requests take precedence. Ties request disambiguation instead of silently dropping a skill.
- 🚫 **Negation guard in English and Thai:** vetoed skills leave the ranking with a `negated_skill` receipt instead of becoming the selection.
- 🔍 **Token-boundary phrase matching:** short queries stop matching inside longer words. Thai text without word spaces keeps substring matching.
- 🧾 **Truthful receipts:** lifecycle event, source hash, and tool-result hash behind every claim. No raw prompts or skill bodies are stored.
- 🛡️ **Progressive tool gating plus invariant checks:** `observe`, `nudge`, and `enforce-tools` modes with `required`, `forbidden`, and `ordered` tool rules.

## 📊 Feature Comparison

| Capability | Prompt-only routing | 🛡️ **Skill Proof** |
| :--- | :---: | :--- |
| **Skill selection** | Model guesses, no audit trail | ✅ **Deterministic lexical rank with a per-turn receipt** |
| **Disabled skills** | Suggested even when disabled | ✅ **Intersected with the live catalog, receipt `not_in_hermes_list`** |
| **Veto requests** | `don't use X` still selects X | ✅ **Negation guard excludes X, receipt `negated_skill`** |
| **Short queries** | `ui` matches inside `build` | ✅ **Token-boundary matching, Thai spaceless keeps substring** |
| **Load evidence** | Claimed in prose | ✅ **Lifecycle event plus source and result hashes** |
| **Tool gating** | None | ✅ **`observe`, `nudge`, `enforce-tools`** |
| **Execution compliance** | Never checked | ✅ **Required, forbidden, and ordered invariants, `verified` or `failed`** |
| **External calls** | Varies | ✅ **Zero, stdlib only, fully offline** |

---

## 🧭 Architectural Dataflow

```mermaid
flowchart LR
    T[User turn] --> S[Scan SKILL.md roots]
    S --> R[Lexical rank with veto]
    R --> C[Intersect live catalog]
    C --> G[Gate tools by mode]
    G --> V[Verify invariants]
    V --> E[Receipt and trace]
```

---

## 🚀 Key Modules and Engineering Advantages

| Component | What It Does | Technical Advantage |
| :--- | :--- | :--- |
| **Lexical Ranker** | Scores name, description phrase, and tags against score and margin thresholds | Deterministic, no model call, no network |
| **Negation Guard** | Detects English and Thai veto phrasing around a skill name | A veto never becomes a selection |
| **Phrase Matcher** | Token-boundary match for spaced text, substring for Thai spaceless text | Short queries stop matching inside longer words |
| **Catalog Intersection** | Matches local names against the live `skills_list` every turn | Disabled or unlisted skills stay visible as filtered, never loaded silently |
| **Tool Gate** | `observe`, `nudge`, `enforce-tools` (`enforce` is an alias) | Progressive strictness, discovery tools always allowed |
| **Receipt Engine** | Compact or verbose footer plus `status`, `explain`, `trace`, `refresh`, `health` | Every claim carries evidence, last 20 receipts retained |
| **Invariant Verifier** | `required_tools`, `forbidden_tools`, `ordered_tools` from frontmatter or config | Violations fail compliance with a named reason, `enforce-tools` blocks outright |
| **Cache** | Metadata-keyed reuse of parsed skills, at most 100 completed turns | Normal edits detected next turn, `refresh` forces a reread |

---

## 📊 System Footprint

| Metric | Measured Value | Note |
| :--- | :--- | :--- |
| **Python files** | 12 | Core, hooks, benchmarks, 7 test files |
| **Python lines** | 3,069 | Includes tests and benchmarks |
| **Tests** | 73 passing, 2 skipped | `python -m unittest discover -s tests` |
| **Routing cases** | 12 of 12 | `routing_benchmark.py`, zero false selections |
| **Hooks** | 7 | Pre and post LLM, pre and post tool, transform, lifecycle, session end |
| **External dependencies** | 0 | Stdlib only |
| **Network calls** | 0 | Fully offline at runtime |

---

## ⚙️ Modes and Configuration

| Mode | Behavior |
| :--- | :--- |
| `observe` | Suggests a candidate and records events, tools remain allowed |
| `nudge` (default) | Requests loading before operational tools, tools remain allowed |
| `enforce-tools` | Blocks operational tools until a selected skill has a Hermes load event |

No match allows ordinary work. An explicitly unknown, duplicate, or multiple skill request blocks operational tools only in `enforce-tools` mode.

Skills declare invariants in `SKILL.md` frontmatter:

```yaml
---
name: my-skill
description: ...
invariants:
  required_tools: [terminal]
  forbidden_tools: [git_push]
  ordered_tools: [terminal, write_file]
---
```

Or centrally in `config.yaml`:

```yaml
plugins:
  entries:
    skill-proof:
      settings:
        invariants:
          test-driven-development:
            required_tools: [terminal]
            ordered_tools: [terminal, write_file]
          codebase-inspection:
            forbidden_tools: [write_file, patch]
```

---

## 📦 Installation and Setup

| Method | Source | Notes |
| :--- | :--- | :--- |
| **Git clone** | [Repo](https://github.com/Stxyu-p/skill-proof) | `git clone https://github.com/Stxyu-p/skill-proof.git`, copy into place |
| **Manual** | This directory | Copy to the active profile `plugins/skill-proof`, run `hermes plugins enable skill-proof`, start a new session |

Merge these settings into the existing configuration and retain other enabled plugins:

```yaml
plugins:
  enabled:
    - skill-proof
  entries:
    skill-proof:
      settings:
        mode: nudge
        skill_roots:
          - C:/Users/BlankScreen/AppData/Local/hermes/skills
        visible_receipt: true
        receipt_history_limit: 20
```

Use explicit roots for custom profiles. Roots must already be resolvable by Hermes `skill_view`. Start with `nudge` and inspect `/skill-proof explain` before enabling tool gating.

---

## 🆕 What Is New in v0.4.1

- **Negation guard**: vetoes such as `don't use X`, `without X`, `ไม่เอา X`, or `ไม่ใช้ X` exclude that skill from ranking. A vetoed `$name` falls back to lexical ranking, fully vetoed turns report `no_match/negated_skill`.
- **Token-boundary phrase matching**: short queries such as `ui` no longer match inside longer words like `build`. Thai text without word spaces keeps substring matching.
- **Disabled-skill intersection locked by a regression test**: lexical matches outside the live catalog resolve to `not_in_hermes_list` with no selection.
- **Health reports the correct plugin version** (0.4.1).

Prior releases: v0.4.0 added invariant compliance verification (`required`, `forbidden`, `ordered` tools). v0.3.1 fixed Thai conjoined-skill detection and action-continuation false matches. v0.3 added `health` timing, diagnostics, and block-scalar frontmatter support.

---

## 🔬 Evidence and Limitations

- `loaded=yes` means Hermes emitted a matching successful lifecycle event, not proof of the exact bytes served.
- `active=yes` means an operational tool was requested after that event, another gate can still block execution.
- Compliance without invariants stays `unassessed`, satisfied invariants report `verified`.
- The local source hash is a filesystem recheck, tool hashes observe results before other plugins transform them.
- Lifecycle events lack turn IDs, so correlation is session and task scoped.
- Thai matching is lexical and phrase based, with no word segmentation or embeddings.
- No ranking-quality or speed improvement over Hermes or Eagle Eye has been demonstrated.
- Synthetic core results live in `VALIDATION.md` and exclude host listing overhead.

---

## 📁 Project Structure

| Path | Purpose |
| :--- | :--- |
| `core.py` | Ranking, negation guard, phrase matching, gating, receipts, invariants |
| `__init__.py` | Hook wiring, commands, 7 hook registrations |
| `plugin.yaml` | Manifest, version 0.4.1, config schema |
| `tests/` | Discovery, ranking, gating, evidence, and isolation suites, 7 files |
| `routing_benchmark.py`, `routing_cases.json` | Representative synthetic routing cases, 12 of 12 passing |
| `benchmark.py` | Synthetic core performance probe |
| `host_smoke.py` | Optional live listing and disabled-filter check against Hermes source |
| `VALIDATION.md` | Synthetic benchmark notes and scope limits |

---

## 🧪 Verification and Automated Testing

Run from this directory:

```powershell
python -m unittest discover -s tests -v
python -m py_compile __init__.py core.py
hermes plugins doctor . --ci
python benchmark.py
python routing_benchmark.py
```

**Verification Status:** **73 tests passing (2 skipped), 12 of 12 routing cases, doctor OK as standalone with 7 hooks.**

---

## 📄 License

Distributed under the [MIT License](LICENSE).
Copyright (c) 2026 P Choke & SORA.

<div align="center">

**Skill Proof** <sub>v0.4.1</sub> · Built for deterministic routing

*Local-first, Auditable, Gated, Governed*

</div>
