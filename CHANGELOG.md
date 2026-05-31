# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Home Assistant support** via the existing `rest` driver — drive a whole
  smart home (lights, locks, thermostats, sensors, media) through one alias with
  a Home Assistant Long-Lived Access Token. Ships an example config
  (`examples/configs/rest_home_assistant.toml`) and a README walkthrough; no new
  driver required.

### Security

- **Credential redaction in the event log.** Configured credentials (the values
  behind a config's `auth_*_env` variables) are now scrubbed to `***` from
  free-form event-log fields — an SSH `command`, a REST query `path`, or an
  `error` that echoes either — so a secret the agent inlines by mistake never
  lands in the durable `~/.lablink/logs` JSONL. Scrubbing is applied at the
  logging boundary, and the SSH tools attach a `metadata.security_warning` when
  a known credential is detected inline. The result returned to the agent is
  unchanged. Catches only secrets LabLink knows about, matched as their literal
  value (very short values are excluded to avoid mangling logs); the tool
  docstrings remain the front-line rule against inlining secrets at all.

## [0.1.0] - 2026-05-30

First release of LabLink: five protocol drivers on a shared multi-driver
dispatch core, exposed to AI agents over MCP and to developers over a CLI.

### Added

**Drivers**

| Driver | Transport | Operation tools |
|--------|-----------|----------------|
| `visa` | PyVISA (USB-TMC, TCPIP, GPIB, Serial-VISA) | `visa_query`, `visa_write` |
| `ssh` | Paramiko | `ssh_exec`, `ssh_shell_session`, `ssh_start_stream`, `ssh_read_stream`, `ssh_stop_stream` |
| `rest` | httpx | `rest_get`, `rest_post`, `rest_put`, `rest_patch`, `rest_delete` |
| `serial` | pyserial (RS232/RS422/RS485) | `serial_query`, `serial_write`, `serial_read`, `serial_flush` |
| `python_shell` | subprocess | `python_shell_exec`, `python_shell_eval` |

An `external` routing stub lets a device be handled by a vendor-supplied MCP
server, surfacing routing hints to the agent on `connect()`.

**SSH streaming.** `ssh_start_stream` / `ssh_read_stream` / `ssh_stop_stream`
buffer the output of long-running commands (log tails, continuous monitors) in a
bounded background queue, with clean teardown on stop and disconnect.

**python_shell.** A persistent subprocess REPL bound to a user-supplied
interpreter (`python_path`), bridging to vendor SDKs such as `nidaqmx` and
`picosdk` that have no VISA or network interface. State persists across calls
within a session. Communicates over a newline-delimited JSON wire protocol with
timeout, crash, and busy-state recovery.

**Architecture**

- Shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`)
  dispatch via the config `type` field across all drivers.
- Per-driver operation tools register only when that driver's Python
  dependencies are installed — missing deps are surfaced by `diagnose()`, never
  a server crash.
- Driver ABC (`LabLinkDriver[ConfigT]`) with a `Generic[ConfigT]` session model.
- `AuthConfig` mixin for drivers that need credentials (SSH, REST), referenced by
  environment variable name only — secrets never live in config files.
- `DocumentedConfig` mixin carrying `techmanual_document_ids` for
  [techmanual.ai](https://techmanual.ai) integration on T&M instruments.
- JSONL event log with canonical `ts` / `op` / `alias` / `success` fields.
- Three-state session lookup (missing / wrong type / found) for precise error
  hints.
- Optional dependency extras per driver (`[visa]`, `[ssh]`, `[rest]`, `[serial]`,
  `[python_shell]`, `[all]`); the server runs with zero drivers installed.
- CLI mirroring the MCP tool surface for development and debugging, with the same
  dependency gating.

### Validated

- **VISA** — end-to-end on a Siglent SDS1104X-E (connect / diagnose / query /
  write / device memory / event log).
- **SSH** — unit tests plus a hardware smoke test on a Raspberry Pi 4 (exec,
  shell session, and live streaming of an `rtl_433` capture).
- **REST** — live against a public API across all five HTTP verbs.
- **Serial** — unit tests with a mocked `serial.Serial`.
- **python_shell** — unit tests plus real-subprocess integration tests exercising
  the JSON wire protocol (exec, eval, exception/traceback, namespace persistence,
  stdout capture, shutdown).
