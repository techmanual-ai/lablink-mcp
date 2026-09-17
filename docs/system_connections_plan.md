# Feature Plan: `system_topology`

**Status:** Design revised post-review (round 3), pre-implementation
**Date:** 2026-05-31
**Scope:** New shared-layer subsystem (not a driver)

> **Naming note.** This feature was scoped as `system_connections`. It is
> renamed to **topology** across every surface to avoid colliding with LabLink's
> existing "connect / connection / session" vocabulary for a *live protocol
> link* — this subsystem describes physical wiring, not open sessions. The names
> are deliberately consistent (agent_development.md §5 — these are load-bearing):
> the MCP tool is **`system_topology`** (the `system_` prefix marks it a
> shared/system-level tool alongside the lifecycle four, not a per-device one),
> the CLI subgroup is **`lablink topology`**, the on-disk file is
> **`~/.lablink/topology.toml`**, and the override env var is
> **`LABLINK_TOPOLOGY_FILE`**.

---

## 1. Intent

Give an agent a machine-readable map of *how the lab is wired together* — not
just what devices exist, but the physical and logical connections between them,
the parameters of each connection, and the safety constraints that govern them.

Today LabLink answers "what can I talk to, and how?" per device. It cannot
answer "the signal generator's OUTPUT1 feeds the DUT's `WFM_IN_B`," or "these
three instruments share a 10 MHz reference with the siggen as master," or "do
not put more than 13.5 V into the DUT's 12 V input." That relational knowledge
lives only in a human's head or a lab notebook. `system_topology` makes it a
first-class, queryable artifact.

The target outcome: an agent new to a bench can call one tool, understand the
devices at play, how they are connected, what flows across each connection, and
what limits it should respect — then perform setup and measurements with the
same situational awareness a returning human engineer would have.

> **What this is not.** The topology — including its `constraint` blocks — is
> *advisory context surfaced to the agent*, exactly like device memory. LabLink
> does **not** enforce constraints and architecturally cannot: it sends commands
> and returns responses without parsing protocol syntax (a stated non-goal in
> the README). When the agent connects to the power supply it *sees* the 13.5 V
> cap and is responsible for honoring it; LabLink will not block an over-voltage
> `visa_write`. See §4 (Decision D) and §7.

---

## 2. How it fills out the ecosystem

LabLink's value to an AI lab assistant comes from layered context. This feature
adds the missing fourth layer:

| Layer | Artifact | Answers |
|-------|----------|---------|
| 1. Drivers / device configs | `~/.lablink/devices/<alias>.toml` | What can I talk to, and over what protocol? |
| 2. Device memory | `~/.lablink/devices/<alias>.md` | What's special about *this* device? (notes, quirks) |
| 3. techmanual.ai | indexed manuals | How do I drive this model? (SCPI, procedures) |
| **4. System topology** | **`~/.lablink/topology.toml`** | **How is the whole system wired, and what are the rules?** |

Layers 1–3 are all *per-device*. `system_topology` is the **relational layer**
that ties them together. Combined, these four give an agent substantially
everything it needs to perform measurements and validation autonomously: the
devices, their individual notes, their reference documentation, and the topology
that binds them — including the safety limits it is expected to honor.

---

## 3. Architectural placement

`system_topology` is **system-level, not device-level**, so it is *not a
driver*: it has no config `type`, no `connect()`, no session. Structurally it is
most similar to **device memory** — parsed by the shared layer, injected into
results, never owned by a driver.

- Definition lives in a single `~/.lablink/topology.toml`, a sibling of the
  `devices/` directory.
- The read tool `system_topology` is a **shared lifecycle tool** (alongside
  `connect` / `disconnect` / `list_devices` / `diagnose`), so it lives in
  `lablink/mcp_server.py`, not in any `interfaces/<type>/` package.
- Per-device context is **injected into `connect()`** the same way device memory
  is (via `dataclasses.replace()`; see ARCHITECTURE §8.3).

### 3.1 Placement of the new code (honoring existing conventions)

The subsystem is split across three homes so it matches LabLink's established
module conventions rather than concentrating models, loading, and logic in one
new file:

- **Data models → `lablink/base.py`** — every tool return type and data model
  already lives here; the topology dataclasses and the new `ConnectResult`
  field join them.
- **TOML loading → `lablink/config.py`** — `config.py` is the single reader of
  all config and the closest analog (`load_device_memory`) already lives there.
  agent_development.md §1 is explicit: *"Config loading lives in
  `lablink/config.py`. Never scatter config reads across other modules."*
- **Graph logic → `lablink/system.py`** (new) — `device_slice()` and
  `validate_system()` are genuine graph operations, not models or loading, so
  they earn a dedicated module.

---

## 4. Decisions made

Five forks were resolved with the lead developer; all took the recommended
option.

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| A | Connection model | **Separate `link` + `net`** | `link` keeps the common 2-point directional case terse; `net` expresses n-ary shared buses (e.g. a 10 MHz reference shared by three instruments with a designated master) that a 2-endpoint edge cannot. |
| B | File scope | **Single `topology.toml`** | Matches the local-first single-bench model; simplest surface. Can grow to multiple named systems later without migration. |
| C | Agent access | **Tool + `connect()` injection** | A standalone `system_topology(alias?)` tool *and* injection of each device's slice into `connect()`, so wiring and safety limits surface proactively — the agent sees the 13.5 V cap the moment it connects to the power supply. |
| D | Safety / notes | **First-class, advisory `constraint` block** | A structured `severity` field (the machine-actionable signal the agent gates on) plus a readable `limit` and `note`. Advisory only — LabLink surfaces constraints to the agent but does not and cannot enforce them (it does not parse protocol syntax). |
| E | Topology-load error isolation | **Topology load never breaks the hot path** | A malformed `topology.toml` must not break a device `connect()` or the driver-readiness audit. `load_system()` may raise `ConfigError`, but every caller in `connect` / `diagnose` / the no-alias audit catches it, injects nothing, and (for the audit) surfaces it as a soft `topology_warning`. See §4.2. |

### 4.1 Constraints are advisory, and `severity` carries the structure

The earlier framing ("structured so an agent can reliably detect and respect
hard limits") overstated what the block does and is corrected here:

- **`severity` ∈ `{info, warning, critical}` is the structured, machine-actionable
  field.** An agent can reliably branch on it (e.g. refuse to proceed near a
  `critical` limit without confirmation). It is stored as a **plain `str`**, not a
  Python `enum.Enum` — an `Enum` member survives `asdict()` as the enum object
  (not JSON-serializable over MCP) and would make verbatim passthrough of an
  unrecognized value impossible. The `{info, warning, critical}` set lives only in
  `validate_system` as the comparison target for the soft warning.
- **Unknown `severity` values pass through, never hard-fail.** A typo like
  `"catastrophic"` is kept verbatim and surfaced; `validate_system` emits a soft
  warning that it is outside `{info, warning, critical}`. We deliberately do
  **not** coerce it to a lower severity — silently downgrading a safety signal is
  worse than an honest "unrecognized" — and do **not** raise `ConfigError`, since
  a notebook-grade artifact should not refuse to load over one typo. A
  conservative agent treats an unrecognized severity as at-least-`warning`.
- **`limit` is a human/agent-readable string** (e.g. `"voltage <= 13.5"`), not a
  typed expression. LabLink does not parse or evaluate it — doing so would
  require both an expression parser and live device state, and still could not
  *block* anything given the non-goal. We deliberately do **not** build a typed
  limit DSL in v1.
- **`note` is free text** for the human-meaningful "why."

The safety value is real but it is a *communication* mechanism to the agent,
identical in trust model to device memory — not a guardrail. Tool docstrings and
docs must say this plainly so no operator believes LabLink will intercept a
dangerous command. The `system_topology` tool docstring must **lead** with the
advisory-not-enforced statement: per agent_development.md §5 the docstring is the
agent's only source of truth, so the safety framing cannot live in the README
alone.

### 4.2 Topology-load error isolation (Decision E)

`load_system()` may raise `ConfigError` on malformed TOML or a structurally
broken document (§6 file 2). The topology shares device memory's *advisory trust
model* — it is context surfaced to the agent, never an enforcement gate — but it
deliberately does **not** share device memory's loader behavior. `load_device_memory()`
swallows everything and returns `None` (`config.py` `load_device_memory`, a bare
`except Exception`); `load_system()`
instead *raises* on malformed input, so `lablink topology validate` and the
`system_topology` tool (§6 file 4) can pinpoint the typo for the agent to fix —
a structured wiring document with safety constraints deserves better than silent
truncation. The device-memory-like *softness* on the hot paths is then restored
one level up: every caller in a hot path catches the raise, so a broken
`topology.toml` still **degrades gracefully** and never breaks core connectivity
or the dep audit.

- **`connect()` / `diagnose(alias)`** — on `ConfigError` from `load_system()`,
  inject no `topology_context` and return the device result unchanged. A device
  connection that is otherwise fine must not fail because of an unrelated
  topology typo. (The error is not swallowed silently from the operator's view —
  `lablink topology validate` and the no-alias audit both surface it.)
- **No-alias system audit (`_system_audit`)** — today this path *cannot* fail and
  is the agent's primary readiness oracle. It must stay that way: catch the
  `ConfigError`, record it as a soft `topology_warning`, and **leave `ready`
  untouched** (driver-dependency readiness only). A topology parse error is never
  an `action_item`.

This makes the §6 file 2 "raises `ConfigError`" contract real but contained: the
loader is honest about malformed input; the hot-path callers are responsible for
isolation.

### Additional locked schema points

- Ports are written `alias:PORT` and are **implicit by use** — no separate
  port-declaration block in v1.
- `params = { … }` is an arbitrary KWARGS bag available on every `link` and
  `net`.
- TOML `from` / `to` map to `from_port` / `to_port` in the dataclass (`from` is
  a reserved word). Reading `raw["from"]` is fine — only Python reserves the
  name.
- `constraint` carries `severity`, `limit` (string), and `note` (string).
  `severity` is validated softly: unknown values are kept and warned about, not
  rejected (see §4.1).
- Constraints attach to `link` and `net` in v1. Node-level constraints can be
  added later with no migration (empty-list-means-none).
- Nodes link to an alias when LabLink manages the device. Passive / unmanaged
  gear (splitters, combiners, pads, couplers, chambers, purely physical DUTs) has
  no alias and instead carries an `id` — a stable string handle. A port prefix in
  a `link`/`net` resolves to a node by `alias` first, then by `id`, so passive
  gear sits in the signal path as a first-class endpoint (`pad_10db:OUT`). Managed
  devices need no `id`; their alias is the handle. A node needs at least one of
  `alias` or `id`.

---

## 5. Schema, by example

The canonical `examples/topology.toml`:

```toml
# ~/.lablink/topology.toml
name = "rf_validation_bench"

[[node]]
alias = "siglent_sdg6022"
role  = "signal generator"
[[node]]
alias = "tek_mso44"
role  = "oscilloscope"
[[node]]
alias = "dut_serial"
role  = "DUT"
[[node]]
alias = "keysight_e36313"
role  = "power supply"
[[node]]
# Passive, unmanaged gear: no alias, addressed by `id`.
id    = "pad_10db"
role  = "10 dB attenuator"

# Directional signal links with a KWARGS param bag. The stimulus path runs
# through a passive pad, so it is two segments joined at the pad's ports.
[[link]]
from = "siglent_sdg6022:OUTPUT1"
to   = "pad_10db:IN"
signal = "stimulus waveform"
params = { impedance_ohm = 50, coupling = "AC" }

[[link]]
from = "pad_10db:OUT"
to   = "dut_serial:WFM_IN_B"
signal = "stimulus waveform (attenuated)"
params = { impedance_ohm = 50 }

[[link]]
from = "dut_serial:SIG_OUT_A"
to   = "tek_mso44:CH2"
signal = "DUT response"
params = { probe = "10x" }

# Power link carrying an advisory safety limit (surfaced to the agent, NOT
# enforced by LabLink — the agent is responsible for honoring it).
[[link]]
from = "keysight_e36313:CH1"
to   = "dut_serial:12V_IN"
signal = "DC power"
params = { nominal_v = 12.0 }
  [[link.constraint]]
  severity = "critical"
  limit    = "voltage <= 13.5"
  note     = "DUT is damaged above 13.5 V on 12V_IN."

# N-ary shared bus: the 10 MHz reference, siggen is master
[[net]]
name   = "ref_10mhz"
signal = "10 MHz reference clock"
params = { frequency_hz = 10_000_000 }
endpoints = [
  { port = "siglent_sdg6022:REF_OUT", role = "master" },
  { port = "tek_mso44:REF_IN",        role = "slave"  },
  { port = "dut_serial:10MHZ_REF",    role = "slave"  },
]
```

---

## 6. Implementation plan (file-by-file)

**1. `lablink/base.py`** — data models (joining the existing result/config types):
- Topology dataclasses (all `@dataclass(kw_only=True)`, per ARCHITECTURE §5.1):
  `Constraint`, `SystemNode` (optional `alias` *and* optional `id`; at least one
  required), `Link`, `NetEndpoint`, `Net`, `SystemTopology` (root),
  `DeviceConnections` (a device's slice). `Constraint.severity` is a plain
  `str` (**not** `enum.Enum`) so it serializes through `asdict()` and preserves
  unrecognized values verbatim (§4.1).
- Add `topology_context: DeviceConnections | None = None` (kw_only, defaults
  `None`) to **both** `ConnectResult` **and** `DiagnosticResult`, so the slice can
  be injected on `connect()` and `diagnose(alias)` alike. No `__post_init__`
  mirroring is needed for it; the nested dataclasses serialize cleanly through the
  existing `asdict()`.
- Add `topology_warnings: list[str] = field(default_factory=list)` to
  `DiagnosticResult`, kept **separate** from `action_items` (which stays
  install/blocking-only, "most-blocking first") so a soft wiring warning is never
  mistaken for a dependency the agent must install.

**2. `lablink/config.py`** — TOML loading (the single config reader):
- `load_system() -> SystemTopology | None` — `None` when the file is absent.
  `ConfigError` on malformed TOML or a structurally broken document — a node with
  neither `alias` nor `id`, or a `constraint` missing its required `severity` (an
  *absent* severity is a broken safety block, not a typo to pass through; §4.1) —
  mirroring the loader rules in §7.5. An unknown `severity` *value* is **not** a
  load error — it is preserved verbatim for `validate_system` to warn on (§4.1).
  Resolves the file path via the new env var (see §8). Hot-path callers catch the
  `ConfigError` per §4.2; the loader itself stays strict.
- `list_configured_aliases() -> list[str]` — a config-dir scan
  (`glob("*.toml")` → stems) extracted here as the single home for the scan.
  Today this glob is inlined in `do_list_devices`; refactor `do_list_devices` to
  call it, and have the no-alias audit reuse it to build `known_aliases`. Keeps
  config-dir reads in `config.py` per agent_development.md §1 ("Never scatter
  config reads across other modules") rather than growing a second glob in
  `system.py` / `mcp_server.py`. **Must never raise** (return `[]` on a missing
  dir — `Path.glob` already yields nothing there; do not add I/O that can throw):
  `_system_audit` calls it on the documented "cannot fail" oracle path (§4.2),
  and `load_system`'s `ConfigError` is the *only* topology-related raise that
  path is allowed to catch.

**3. `lablink/system.py`** (new — graph logic only, stdlib-only, no driver deps):
- `device_slice(topology, alias) -> DeviceConnections` — filters links/nets whose
  ports resolve to this alias, collects their constraints, and lists neighbor
  handles (alias or `id`). Callers pass a real `topology`; the `None` case (no
  `topology.toml`) is guarded one level up, so this never sees `None`.
- `validate_system(topology, known_aliases) -> list[str]` — soft warnings,
  **never raises**. Four advisory checks:
  1. **Unresolved port prefix** — a `link`/`net` endpoint whose prefix matches no
     node `alias` and no node `id`.
  2. **Declared-but-unconfigured device** — a node with an `alias` not in
     `known_aliases` (no `<alias>.toml` on disk). Informational: a bench is often
     mapped before every device is configured.
  3. **Unknown `severity`** — a constraint whose `severity` is outside
     `{info, warning, critical}` (§4.1).
  4. **alias/id namespace collision** — a passive node whose `id` equals some
     other node's `alias`. Since port resolution is "alias first, then `id`"
     (§4 schema points), a colliding `id` would be silently shadowed by the
     managed device; warn so it surfaces.
  `known_aliases` is `config.list_configured_aliases()` (the single config-dir
  scan; see file 2).

**4. `lablink/mcp_server.py`** — register `system_topology(alias?)` as a fifth
shared lifecycle tool (no alias → whole graph; with alias → that device's
slice). Its docstring states explicitly that constraints are advisory and not
enforced.
- **Error contract (this tool does NOT swallow).** `system_topology` is the
  dedicated topology reader, so unlike the §4.2 hot paths it surfaces problems
  rather than degrading to "nothing." Its contract:
  - **No `topology.toml`** (`load_system()` returns `None`) → `{"success": true,
    ...}` with an empty topology/slice and a `metadata.note` ("no topology.toml
    configured"). Absence is not an error.
  - **Malformed `topology.toml`** (`load_system()` raises `ConfigError`) → catch
    it and return a structured `{"success": false, "error": <message>, "hint":
    "Run `lablink topology validate` to locate the problem."}`. This is the one
    caller that must show the parse error to the agent directly — §4.2 isolation
    applies to `connect` / `diagnose` / the audit, **not** here.
  - **Valid, with an `alias` that has no slice** → `{"success": true, ...}` with
    an empty slice (the device is simply not wired into the topology), not an
    error.
  Every return point logs via `event_logger.log_event(op="system_topology",
  alias=<alias or None>, success=...)`.
- Extend shared `connect()` to inject `topology_context`. Load the topology once,
  **inside a `try/except ConfigError`** (§4.2): on a malformed file, inject
  nothing and return the device result unchanged — a topology typo must not break
  a healthy connection. If the load succeeds but is `None` or the device has no
  slice, also inject nothing. Otherwise fold the slice into the **single**
  existing `dataclasses.replace()` call (the one that injects `device_memory`)
  rather than constructing the result twice.
- `diagnose(alias)` injects the device's slice the same way — same
  `try/except ConfigError` guard, same single `replace()` on that path (now that
  `DiagnosticResult` carries `topology_context`).
- The no-alias `_system_audit()` builds `known_aliases` via
  `config.list_configured_aliases()`, then loads the topology **inside a
  `try/except ConfigError`** and runs `validate_system()`. It reports both the
  validation warnings and any caught load error in the new **`topology_warnings`**
  field — **not** `action_items`. **Topology warnings (and a topology parse
  error) must not flip the `ready` boolean** — `ready` reflects driver dependency
  readiness only; a dangling reference or a malformed `topology.toml` is a soft
  warning, not an unhealthy system. This keeps the audit's "cannot fail" property
  (it is the agent's primary readiness oracle).

**5. `lablink/cli.py`** — shared (non-gated) `lablink topology show [alias]` and
`lablink topology validate`. Both must honor the `load_system() -> None`
(absent-file) case explicitly: print a graceful "No topology.toml found
(expected: <path>)." to stderr and exit 0 rather than treating `None` as an error
or passing it into `device_slice` / `validate_system` (which expect a real
topology — the `None` guard lives in the CLI/tool layer, per §6 file 3). On a
`ConfigError`, `validate` prints the parse error and the offending path (its
whole job); `show` prints the same structured error as the tool (§6 file 4).

**6. `tests/test_system.py`** — parse the example, slice filtering (resolving
ports by both `alias` and a passive node's `id`), missing file → `None`,
malformed / node-with-neither-handle → `ConfigError`; tool test for
`system_topology`; plus these contract paths:
- constraints surface through the `connect()` `topology_context` injection (the
  safety-critical route). Assert on the **serialized dict** (post-`asdict()`,
  i.e. the tool's actual return), not the dataclass — that is what crosses the
  MCP boundary, and it confirms `severity`/nested dataclasses serialize cleanly;
- an unresolved-port-prefix warning appears in `diagnose()` **`topology_warnings`**
  but does **not** set `ready=False`;
- an unknown `severity` string loads without error and produces a soft warning,
  not a `ConfigError`;
- an alias/id namespace collision produces a soft warning (§6 file 3, check 4);
- **error isolation (§4.2):** a malformed `topology.toml` does **not** break
  `connect()` (device result returns success with no `topology_context`) and does
  **not** set `ready=False` in the no-alias audit — instead it appears as a
  `topology_warning`.

**7. `examples/topology.toml`** — the RF-validation bench above.

**8. Docs**
- README: a "Mapping your system" section, a tool-table row, and the new env var
  in the Environment Variables table — stating clearly that constraints are
  advisory, not enforced.
- ARCHITECTURE: a section for the topology data models and the injection
  contract (extending §5 and the §8.3 injection pattern), noting the
  base.py / config.py / system.py split from §3.1, plus a `LABLINK_TOPOLOGY_FILE`
  row in the §14 env-var table.
- CHANGELOG: an `[Unreleased] → Added` entry.

---

## 7. Deliberate v1 non-goals

Scope is intentionally tight (per the lead-developer directives):

- **Constraints are advisory, not enforced.** LabLink surfaces `severity` /
  `limit` / `note` to the agent; it does not parse protocol syntax and will not
  block a command that violates a limit. Enforcement would contradict the
  "not a protocol library" non-goal.
- **Read-only.** No tool to author or edit the topology — the human describes
  the physical wiring an agent cannot infer. Revisit if demand surfaces.
- **No port-declaration block** — ports are implicit by their use in links/nets,
  and are not validated against device capabilities (LabLink does not know a
  device's port names).
- **Soft validation** — unresolved port prefixes, declared-but-unconfigured
  devices, unknown `severity` values, and alias/id collisions warn via
  `diagnose`. Only malformed TOML or a structurally broken document — a node with
  neither `alias` nor `id`, or a `constraint` with no `severity` at all — raises
  `ConfigError` from `load_system()`; and even that is **isolated** from
  `connect` / `diagnose` / the audit (§4.2), so it degrades to a soft warning
  there rather than breaking connectivity or the dep audit.
- **No node-level constraints yet** — connection-level only in v1.
- **No typed limit DSL** — `limit` is a readable string; `severity` is the
  structured signal.

---

## 8. Resolved: override env var

**Decision: a standalone `LABLINK_TOPOLOGY_FILE`, defaulting to
`~/.lablink/topology.toml`, computed independently of `LABLINK_CONFIG_DIR`.**

The rejected alternative — anchoring the topology file to the parent of
`LABLINK_CONFIG_DIR` — has a latent bug: `LABLINK_CONFIG_DIR` points *directly
at* the devices directory and may be set to an arbitrary path. "Parent of config
dir" only yields the intended sibling when the layout happens to be
`.../something/devices/`; point it elsewhere and the "sibling `topology.toml`"
assumption silently breaks. The standalone var is uncoupled and unsurprising: if
a user overrides only `LABLINK_CONFIG_DIR`, the topology file still resolves to
`~/.lablink/topology.toml`. The default must **not** be derived from
`LABLINK_CONFIG_DIR`.

**Edge case (acceptable in v1, documented not handled).** Because the two paths
are independent, a user who points `LABLINK_CONFIG_DIR` at `~/.lablink/` itself
(the parent, not `.../devices/`) will have `topology.toml` fall inside the device
glob. `do_list_devices` / `list_configured_aliases` will then pick it up as an
alias named `topology`, and `load_config("topology")` will report it as an
`invalid` device (no `type` field). This is cosmetic — it does not affect
topology loading, which uses `LABLINK_TOPOLOGY_FILE` exclusively — and the fix is
the documented layout (`devices/` as a subdirectory). We do **not** add a
filename exclusion in v1; just note it in the env-var docs.
