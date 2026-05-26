# AI Agent Onboarding & Protocol for AgentLink-Visa

## 1. System Role & Core Objective

You are a world-class software engineering AI assistant. Your primary objective is to contribute to the `agentlink-visa` codebase by implementing features, fixing bugs, and improving the overall quality of the project.

AgentLink-Visa is an MCP server that gives AI agents direct, structured control over test and measurement equipment via PyVISA. It is the execution backbone that complements techmanual.ai's knowledge backbone.

This document is your **foundational instruction set**. Adhering to these protocols is critical for successful collaboration.

---

## 2. Onboarding Protocol: Context Ingestion

Ingest the following documents in order. The sequence builds context from highest-level goals down to implementation details.

1. **`readme_agent.md` (This Document):** Master protocol for how you operate, contribute, and interact with project documentation.
2. **`project_goal.md`:** The strategic "why" — vision, design decisions, non-goals.
3. **`agent_development.md`:** The tactical "how" — coding standards, VISA-specific dev guidelines, testing requirements.
4. **`current_status.md`:** Current phase and recent history. Confirm phase before starting work.
5. **`system_architecture.md`:** Component map, directory structure, and data flow.

> **Bootstrap note:** If `docs/agent_docs/` is absent when you read `agent-bootstrap.md`, your first job is to scaffold this directory per Section 6 of that document before touching any code.

---

## 3. Documentation & Contribution Protocol

### `project_goal.md`
- **Purpose:** Source of truth for project direction, design decisions, and non-goals.
- **Your Responsibility:** Guide all implementation choices from this document. If a user request conflicts with a locked decision in §2, raise it for clarification before proceeding.

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
