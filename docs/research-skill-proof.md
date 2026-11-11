# Punch-for-Punch Research: skill-proof vs. Graphify DAG & L0 Lazy Manifest

**Target Architecture Analyzed:**  
1. `Graphify-Labs/graphify` (Deterministic AST parsing, `EXTRACTED` vs `INFERRED` edge taxonomy, Prerequisite DAG)  
2. Industry AI Agent Skill Architectures (Claude Code, OpenAI Codex, Hermes Skill Protocol, L0 Lazy Manifest)  
**Internal Baseline:** `skill-proof` (v2.x, Capability-Tool mapping, Workflow Crystallization, Frontmatter Compliance, Unused Pruning)  
**Primary Source Evidence:** Direct inspection of `Graphify ARCHITECTURE.md`, `skill_proof/core.py`, `_catalog.py`, `crystallize.py`, `prune.py`  
**Hardware & Environment Constraints:** Windows 11 / ThinkPad L570 (Intel Core i5-7200U 2C/4T, 8GB RAM, Local Stdlib Python)  
**Guiding Principle:** Standalone 100%, Userfacing clarity, Minimalist logic, Zero hallucinated claims.

---

## 1. Executive Summary

In 2025–2026, the AI agent ecosystem hit a hard ceiling with naive skill architectures: **System Prompt Bloat.**  
Dumping dozens of full skill Markdown documents (10,000+ tokens) into the model's active context at session startup wastes tokens ("at-rest token tax"), degrades instruction adherence, and confuses tool selection.

The modern consensus has converged on three architectural pillars:
1. **L0 Lazy Manifest (Progressive Disclosure):** Exposing only ultra-compact trigger manifests (`<name>: Use when <trigger>. <behavior>`) at rest, loading full bodies only upon explicit invocation.
2. **Deterministic Prerequisite DAG:** Checking tool availability, environment prerequisites, and skill dependencies *before* dispatching a skill, avoiding mid-run execution failures.
3. **Workflow Crystallization:** Mining repeated, successful command sequences from production telemetry into verified, reusable skills.

This research examines how `skill-proof` compares against `Graphify`'s deterministic DAG and industry standards, and outlines concrete, zero-dependency improvements.

---

## 2. Punch-for-Punch Comparison Matrix

| Architectural Dimension | `skill-proof` (Current) | `Graphify DAG / Industry` | The Punch-for-Punch Winner |
| :--- | :--- | :--- | :--- |
| **1. At-Rest Context Tax** | **L0 Trigger Manifest:** Keeps skills dormant; exposes only compact triggers until `skill_view` loads full body. | **Full Injection or Tool Schema:** Many frameworks inject all tool schemas upfront, wasting 5K–10K tokens. | 🏆 **skill-proof wins:** Near-zero token footprint at session startup. |
| **2. Prerequisite & Dependency DAG** | **Flat Capability Map:** Maps abstract caps (`fs:read`, `shell:exec`) to tools, but lacks cross-skill dependency checks. | **Deterministic DAG:** Tracks directed call/dependency chains; detects missing prerequisites and cyclic imports. | 🏆 **Graphify wins:** Graphify's DAG prevents invoking a skill whose required companion tools/skills are absent. |
| **3. Provenance of Mined Skills** | **Evidence Annotation:** Records timestamp and raw session origin in `crystallize.py`, but treats content as a single block. | **Edge Epistemics (`EXTRACTED` vs `INFERRED`):** Explicitly tags AST/literal command links vs model-inferred descriptions. | 🏆 **Graphify wins:** Distinguishes what was physically executed from what the LLM summarized. |
| **4. Telemetry-Driven Pruning** | **Activity Pruner (`prune.py`):** Can identify dormant skills, but lacks real-time invocation telemetry (`usage.jsonl`). | **Audit Reports:** Generates static `GRAPH_REPORT.md` of unused/orphaned nodes. | 🤝 **Draw / High Potential:** skill-proof has the pruning engine, but needs trimtok-style telemetry. |
| **5. Runtime Footprint** | **Pure Python Stdlib:** Zero external graph libraries; optional ONNX embedder fails open cleanly. | **Heavy Graph Stacks:** Requires `networkx`, `scipy`, `leidenalg` for clustering and graph traversal. | 🏆 **skill-proof wins:** Instant execution on ThinkPad L570 without pulling hundreds of megabytes of wheels. |

---

## 3. Deep Dive into Key Mechanisms to Borrow

### A. Deterministic Prerequisite DAG (Zero External Dependencies)
**Inspiration:** `Graphify-Labs/graphify/graphify/build.py` & `paths.py`

When an agent invokes a complex skill (e.g. `github:pr-workflow`), it often assumes prerequisites exist:
- Command-line binaries: `gh`, `git`
- Environment variables: `GITHUB_TOKEN`
- Companion skills: `code-review`, `git-guardrails`

If any prerequisite is missing, the agent fails several turns into the task, wasting time and tokens.

**Solution for `skill-proof`:**
Add a lightweight, dependency-free DAG checker in `skill_proof/core.py`:
- In `SKILL.md` frontmatter, allow optional:
  ```yaml
  requires:
    tools: [terminal, write_file]
    binaries: [gh, git]
    skills: [git-guardrails]
  ```
- `skill-proof` runs a simple 10-line topological check using Python `dict[str, list[str]]`.
- If a binary or tool is missing on the host OS (e.g. Windows without `gh`), `skill-proof` immediately returns an advisory preflight error *before* the skill runs.

---

### B. Provenance Tagging in `crystallize.py` (`EXTRACTED` vs `INFERRED`)
**Inspiration:** `Graphify-Labs/graphify/ARCHITECTURE.md` (Confidence Labels)

When `skill-proof` crystallizes a new skill by mining telemetry from `trimtok`:
- **`EXTRACTED`:** The exact shell commands, exit codes, and arguments executed during the session. (Ground Truth).
- **`INFERRED`:** The LLM's explanation of *why* the command was run, and the generated trigger phrase. (Subjective Hypothesis).

By tagging sections in the generated `SKILL.md`:
```markdown
## Workflow [EXTRACTED from telemetry session #929688]
1. `uv run pytest tests/test_core.py`
2. `git diff --stat`

## Rationale [INFERRED by assistant]
Use when verifying core test regressions before staging.
```
Human developers and reviewing agents can immediately tell what physically ran versus what was inferred.

---

### C. Unified Telemetry: `~/.skill-proof/usage.jsonl`
Mirroring `trimtok`'s production telemetry baseline:
- Every time a skill is evaluated, matched, or invoked, append a single JSON line:
  ```json
  {"timestamp": 1794307500, "skill": "research", "status": "invoked", "duration_ms": 12.4}
  ```
- `skill-proof prune --dormant-days 30` is a proposed future feature, not an implemented CLI option. Current audit pruning uses only the retained audit window and cannot establish 30-day dormancy.

---

## 4. Minimalist Action Plan for skill-proof

1. **Lightweight Prerequisite Checker in `skill_proof/core.py`:**
   - Add frontmatter parser support for `requires: { tools, binaries, skills }`.
   - Implement `check_prerequisites(skill_name) -> tuple[bool, list[str]]` using standard `shutil.which` and tool registry.
   - *Cost:* ~30 lines of pure Python stdlib. Zero dependencies.

2. **Add Provenance Badges to `crystallize.py`:**
   - Mark mined workflows as `[EXTRACTED]` and generated summaries as `[INFERRED]`.

3. **Implement `usage.jsonl` Telemetry Logging:**
   - Record skill lookup and execution latency.

4. **Keep Architecture 100% Standalone:**
   - Maintain zero hard external dependencies. Keep it lightning-fast on Windows 11 / ThinkPad L570.
