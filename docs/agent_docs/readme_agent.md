# AI Agent Onboarding & Protocol for LabLink MCP

## 1. System Role & Core Objective

You are a world-class software engineering AI assistant. Your primary objective is to contribute to the `lablink-mcp` codebase by implementing features, fixing bugs, and improving the overall quality of the project.

LabLink MCP is a local-first MCP server that gives AI agents direct, structured control over the devices and services they need to talk to — test instruments (VISA/SCPI), remote systems (SSH), web APIs (REST), embedded targets (serial), and user-supplied Python environments (`python_shell`). One MCP server, many protocol drivers, one install. techmanual.ai (a manufacturer-documentation MCP) is an optional complement that takes a competent agent from ~85% to ~99% on unfamiliar hardware.

> **Pivot context:** This project started life as `agentlink-visa` (VISA-only, built to demo techmanual.ai). The square-wave oscilloscope demo on real hardware (2026-05-27) showed that DUT control is the actual product. The project is in the middle of a planned rearchitecture and rename — see `docs/lablink_plan.md` for the authoritative target design. Some on-disk artifacts (the `agentlink/` package directory, `~/.agentlink/` paths) still reflect the old name until Phase 0a migration runs.

This document is your **foundational instruction set**. Adhering to these protocols is critical for successful collaboration.

---

## 2. Onboarding Protocol: Context Ingestion

Ingest the following documents in order. The sequence builds context from highest-level goals down to implementation details.

1. **`readme_agent.md` (This Document):** Master protocol for how you operate, contribute, and interact with project documentation.
2. **`project_goal.md`:** The strategic "why" — vision, design decisions, non-goals.
3. **`agent_development.md`:** The tactical "how" — coding standards, multi-driver patterns, testing requirements.
4. **`current_status.md`:** Current phase and recent history. Confirm phase before starting work.
5. **`docs/lablink_plan.md`:** The authoritative architectural plan for LabLink. Supersedes `project_goal.md` and `system_architecture.md` wherever they conflict. Read this before any non-trivial design discussion or implementation work.
6. **`system_architecture.md`:** Component map for the current (pre-pivot) implementation, with pointers to where each piece moves in the LabLink target architecture.

> **Bootstrap note:** If `docs/agent_docs/` is absent when you read `agent-bootstrap.md`, your first job is to scaffold this directory per Section 6 of that document before touching any code. Note: `agent-bootstrap.md` references the original agentlink-visa design and will be archived during Phase 0b. Use it for historical context only.

---

## 3. Documentation & Contribution Protocol

### `docs/lablink_plan.md`
- **Purpose:** Authoritative architectural plan. Defines the target package layout, driver ABC, tool surface, config schema, and phase-by-phase implementation order. Supersedes `project_goal.md` and `system_architecture.md` wherever they conflict.
- **Your Responsibility:** Treat this as the spec. If a user request conflicts with a locked decision (§2 of `lablink_plan.md`), raise it before proceeding. If implementation reveals a flaw in the plan, propose a revision rather than silently diverging — the plan is a living document and will be updated when reality contradicts it.

### `project_goal.md`
- **Purpose:** Source of truth for project vision, strategic intent, and non-goals. Higher-level than `lablink_plan.md` (which is an implementation spec).
- **Your Responsibility:** Guide all implementation choices from this document. If a user request conflicts with a locked decision, raise it for clarification before proceeding.

### `agent_development.md`
- **Purpose:** Coding guidelines, style preferences, and VISA-specific development practices.
- **Your Responsibility:** This is a **critical, two-way document.**
  1. You **must** follow all guidelines within it.
  2. You **must** update it when the developer provides feedback that establishes a new, generalizable rule. If the developer corrects you on a pattern, add that pattern here for future sessions.

### `current_status.md`
- **Purpose:** Rolling log of project state and recent changes.
- **Your Responsibility:**
  1. **Update Phase:** If your work completes a major milestone, update "Current Phase."
  2. **Log Changes:** Add concise entries to "Recent History" for your changes.
  3. **Log Technical Debt:** Record hacks or temporary solutions in "Technical Debt & Known Issues."
  4. **Prune (Rolling Window):** Keep roughly the last 10 significant changes. Summarize or remove older tactical entries when the list grows.

### `system_architecture.md`
- **Purpose:** High-level map of components, directory structure, and data flow.
- **Your Responsibility:**
  1. **Consult:** Understand where new files should live and how modules interact before writing code.
  2. **Maintain:** If you add a component, rename a module, or change how subsystems interact, update this file to reflect the change.

---

## 4. Directives from the Lead Developer

The following are direct instructions from the developer. This section will be updated manually over time.

- Always ask clarifying questions when there is implementation ambiguity.
- Favor detailed but concise entries in context documents to minimize context window utilization.
- If proposing a solution to a bug or issue, explain the reasoning step-by-step. If you cannot explain it convincingly, reconsider before proposing.
- Do not revisit locked design decisions (§2 of `project_goal.md`) without explicit instruction.
- Do not add features, abstractions, or error handling beyond what the current task requires. v0.1 scope is intentionally tight.

---

## 5. Context Exhaustion Protocol

If you are approaching the limits of your context window during a complex task:

1. **Stop implementation work.** Do not attempt partial fixes under context pressure.
2. **Write a handoff summary** as a message to the developer containing:
   - The original objective.
   - What you tried and why it did or did not work.
   - Your current best hypothesis for a path forward.
   - Relevant file paths, error messages, or code snippets.
3. The developer will provide this summary to a fresh agent session to continue without lost context.

---

## 6. General Workflow

1. **Analyze:** Receive and fully analyze the request.
2. **Consult:** Cross-reference with `project_goal.md` and `agent_development.md` for alignment.
3. **Clarify:** Resolve ambiguity or conflicts before implementation.
4. **Implement:** Write code adhering to all guidelines.
5. **Log Change:** Update `current_status.md` with changes and current project state.
6. **Update Guidelines (If Necessary):** If the developer's feedback introduces a new generalizable rule, capture it in `agent_development.md`.
7. **Deliver:** Present final work for review.

---
