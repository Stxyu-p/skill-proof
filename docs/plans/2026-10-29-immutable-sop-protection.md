# Immutable SOP Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Protect user-defined SOP skills with `immutable: true` or `governance: locked` from automatic rewrites, patches, and overwrites by Hermes background self-improvement or agent tools (`skill_manage`, `patch`, `write_file`).

**Architecture:** Dual-layer defense:
1. Metadata & Filesystem Layer (`vault.py`): Parses YAML frontmatter for immutability markers (`immutable: true`, `governance: locked`, `protected: true`) and enforces OS-level read-only permissions on linked vault files.
2. Interceptor Hook Layer (`__init__.py`): In `SkillProofPlugin.on_pre_tool_call`, inspects tool invocations targeting `skill_manage`, `patch`, and `write_file`. If the target is an immutable skill, rejects the tool call immediately before disk modification.

**Tech Stack:** Python 3.10+ stdlib (`pathlib`, `stat`, `os`, `re`), Hermes Plugin Hooks (`pre_tool_call`).

**Spec:** Community pain point from Nous Research Discord (`Don't fxxking change my skills!` after upstream PR #134289 removed skill write guard).

## Global Constraints

- Pure Python stdlib — zero external dependencies.
- Sub-millisecond latency overhead in `on_pre_tool_call` (< 0.2 ms).
- Fail-open on unmanaged non-skill files; fail-closed on confirmed immutable skill targets.
- Cross-platform compatible (Windows NTFS & POSIX).

## Review Focus

1. Windows Read-Only file permissions (`stat.S_IREAD`) preventing normal file deletion/overwrite without breaking symlink/junction resolution.
2. `skill_manage` payload variations: single operation dict, batch `operations` list, or legacy `action`/`name` arguments.
3. Path variations for `patch`/`write_file`: relative path, absolute path, forward slash, backward slash.
4. Non-immutable skills must remain completely editable with zero friction.
5. Malformed or non-YAML frontmatter must gracefully default to non-immutable without throwing exceptions.

---

### Task 1: Immutability Detection & Read-Only Helpers in `vault.py`

**Files:**
- Modify: `skill_proof/vault.py`
- Test: `tests/test_immutable_vault.py`

**Interfaces:**
- Produces:
  - `is_skill_immutable(skill_path: pathlib.Path | str) -> bool`
  - `set_skill_readonly(skill_dir: pathlib.Path | str, readonly: bool = True) -> bool`

- [ ] **Step 1: Write the failing test in `tests/test_immutable_vault.py`**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `is_skill_immutable` and `set_skill_readonly` in `skill_proof/vault.py`**
- [ ] **Step 4: Run test to verify it passes**

---

### Task 2: Tool Guard Interceptor in `SkillProofPlugin.on_pre_tool_call`

**Files:**
- Modify: `__init__.py:388-425`
- Test: `tests/test_immutable_guard.py`

**Interfaces:**
- Consumes:
  - `is_skill_immutable` from `skill_proof.vault`
- Produces:
  - Pre-tool call blocking response `{"action": "block", "message": "..."}` for unauthorized modifications to immutable skills.

- [ ] **Step 1: Write the failing test in `tests/test_immutable_guard.py`**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement immutable skill guard inside `on_pre_tool_call` in `__init__.py`**
- [ ] **Step 4: Run test to verify it passes**

---

### Task 3: Integration Verification & Regression Suite

**Files:**
- Test: `tests/test_immutable_guard.py`, `tests/test_immutable_vault.py`, `tests/test_plugin.py`

- [ ] **Step 1: Run all test suites together**
- [ ] **Step 2: Verify `test_plugin.py` regression suite still passes 100%**
