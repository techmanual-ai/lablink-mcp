# Agent Onboarding & Protocol for LabLink

This is the working guide for an AI agent contributing to the `lablink-mcp`
codebase. It explains how to come up to speed, where the source-of-truth docs
live, and how to keep them current.

LabLink is a local-first MCP server that gives AI agents direct, structured
control over the devices and services they talk to — test instruments
(VISA/SCPI), remote systems (SSH), web APIs (REST), serial devices, and
user-supplied Python environments. One server, many protocol drivers, one
install. [techmanual.ai](https://techmanual.ai) is an optional, highly
complementary documentation companion.

---

## 1. Onboarding Protocol: Context Ingestion

Read these in order, highest-level first:

1. **`README.md`** (repo root) — what LabLink is, its scope and non-goals,
   the tool surface, and config schema. The product source of truth.
2. **`docs/ARCHITECTURE.md`** — data models, the driver contract, dispatch,
   session and event-logging contracts, and how to add a driver. The
   architectural source of truth.
3. **`docs/agent_docs/agent_development.md`** — coding standards, multi-driver
   patterns, and testing requirements. The tactical "how."
4. **`CHANGELOG.md`** — what has shipped and recent changes. Skim before starting
   work so you know the current state.

---

## 2. Documentation & Contribution Protocol

Keep the docs honest as the code changes.

### `README.md`
- **Purpose:** product vision, scope, non-goals, install/usage, tool and config
  reference.
- **Your responsibility:** update it when scope, the tool surface, or the config
  schema changes. If a request conflicts with a stated non-goal, raise it before
  proceeding.

### `docs/ARCHITECTURE.md`
- **Purpose:** the architectural spec — data models, driver ABC, registries,
  config schema, session/streaming/event-logger contracts, dependency model.
- **Your responsibility:** treat it as the spec. When you add a component, rename
  a module, or change how subsystems interact, update it to match. If
  implementation reveals a flaw in the design, fix the doc rather than silently
  diverging.

### `docs/agent_docs/agent_development.md`
- **Purpose:** coding standards and conventions.
- **Your responsibility:** follow every guideline. When the developer corrects
  you on a pattern that should hold generally, capture it here so future sessions
  inherit it. This is a living, two-way document.

### `CHANGELOG.md`
- **Purpose:** the public record of what changed.
- **Your responsibility:** add a concise entry under `[Unreleased]` for any
  user-facing change (new driver, new tool, behavior change), in
  release-note tone — not a session diary.

---

## 3. Directives from the Lead Developer

- Always ask clarifying questions when there is implementation ambiguity. Do not
  guess.
- Favor detailed but concise documentation to minimize context-window usage.
- When proposing a fix, explain the reasoning step by step. If you cannot explain
  it convincingly, reconsider before proposing.
- Do not add features, abstractions, or error handling beyond what the current
  task requires. Scope is intentionally tight.

---

## 4. Context Exhaustion Protocol

If you are approaching the limits of your context window during a complex task:

1. **Stop implementation work.** Do not attempt partial fixes under pressure.
2. **Write a handoff summary** containing the objective, what you tried and why
   it did or did not work, your current best hypothesis, and relevant file paths,
   error messages, or snippets.
3. The developer hands that summary to a fresh session to continue.

---

## 5. General Workflow

1. **Analyze** the request fully.
2. **Consult** `README.md`, `docs/ARCHITECTURE.md`, and `agent_development.md`
   for alignment.
3. **Clarify** any ambiguity or conflict before implementing.
4. **Implement** to the coding standards.
5. **Update docs** — `CHANGELOG.md` for user-facing changes, `ARCHITECTURE.md`
   for structural ones, `agent_development.md` for any new generalizable rule.
6. **Deliver** for review.
