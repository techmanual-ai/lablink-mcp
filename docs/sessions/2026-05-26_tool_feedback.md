# MCP Tool Feedback: agentlink-visa + techmanual.ai
**Date:** 2026-05-26  
**Session context:** SDS1104X-E characterization session — connecting, exploring, and identifying signals via remote instrument control.

---

## agentlink-visa

### Reliability — 4/5

Rock solid for the core loop: connect, write, query, disconnect. Session persistence worked perfectly across dozens of calls with no drops over a full interactive session.

**One important caveat:** `success: true` on a write only confirms that the bytes were sent without a VISA-layer error. It does not confirm the instrument acted on the command. When cursor writes were issued (`CURSOR_TYPE TIME`, `C3:CRVA HREL,X1,...`), all returned success — but the subsequent queries timed out, meaning the commands may not have produced the intended state change. This is an inherent SCPI property (writes are fire-and-forget), not a bug in agentlink, but it means **write success is a necessary but not sufficient condition for "it worked."** Any write where the result matters should be followed with a confirming query.

### Ergonomics — 5/5

The four-tool surface (connect / disconnect / query / write) is exactly right. Nothing is missing, nothing is extraneous. Parallel calls worked seamlessly — 8–10 PAVA queries could be fired in a single turn, which is a major throughput advantage over manual bench operation. The structured error response (`{"success": false, "error": "...", "hint": "..."}`) was genuinely useful for triage: VISA timeouts were immediately distinguishable from other failure modes, and the hint field pointed toward the right diagnosis.

### Transparency — 3/5

The tool is a clean pass-through, which is the right design philosophy. But it means the agent carries all responsibility for SCPI correctness. Three distinct failure modes all manifest identically as a timeout:
- Command not supported by this instrument
- Wrong syntax for this firmware version
- Instrument busy / not ready

Distinguishing between these requires domain knowledge. For an agent operating without a human in the loop this could lead to unnecessary retry attempts or misdiagnosis. A light heuristic layer (e.g., flagging consecutive timeouts on the same command root) could help without compromising the thin-wrapper design.

---

## techmanual.ai

### Coverage — 4/5

Found four relevant documents immediately from a single `list_manuals` call with manufacturer and model_number filters. The catalog summary returned on every call (listing all available equipment families, manufacturers, and document types) is a useful orientation tool even when not explicitly needed. Search responses returned targeted page snippets that translated directly into usable SCPI commands with no further manual browsing required for the common measurement operations.

### Model Applicability — 3/5

This is the most important practical limitation surfaced during this session.

**The SDS Series Programming Guide (docs 1287 and 1323) does not list SDS1104X-E as an applicable model.** Both guides cover newer SDS variants (SDS5000X, SDS2000X Plus, SDS6000 Pro, etc.). There is no SDS1104X-E-specific programming guide in the catalog.

This gap was harmless for core commands — `PAVA`, `TDIV`, `VDIV`, `TRSE`, `ASET` all behaved as documented. But it caused real failures for cursor readback: the `:CURSor:X1?` syntax from doc 1323 is part of the newer multi-cursor architecture and timed out on the SDS1104X-E firmware. The tool surfaces the best available match, but there is currently no signal when "best available" and "actually correct" diverge. An applicability confidence indicator or explicit coverage gap warning would be valuable.

### Single `techmanual_document_id` Limitation — 3/5

The instrument config stores a single `techmanual_document_id`. For effective SCPI control, two documents are typically needed:
- **User manual** — conceptual operation, measurement parameter definitions, block diagram
- **Programming guide** — SCPI command syntax, parameter ranges, query formats

In this session the config was set to doc 1291 (user manual) with doc 1323 (programming guide) noted in a comment. This works but is a workaround. Supporting an array of document IDs, or a `techmanual_programming_guide_id` companion field, would more naturally represent the two-doc pattern that instrument control reliably requires.

### Search Quality — 4/5

Queries were fast and results were relevant. The `model_number` filter effectively scoped results to the right instrument family. The one limitation is that search quality is bounded by catalog coverage — when the right document doesn't exist (SDS1104X-E programming guide), the search correctly returns the closest match, but the agent has no way to know it's operating with an approximation.

---

## The Integration

### Combined Effectiveness — 5/5

The two tools together delivered exactly the intended loop: **look up → command → execute → observe → iterate.** Specific highlights:

- Searching for `PAVA` parameter names in the user manual before firing measurement queries eliminated trial-and-error and produced clean results on the first attempt across all 11 parameters queried.
- The RMS-based waveform identification (triangle vs. sine) was only possible because agentlink could retrieve all signal parameters in a single parallel round-trip. A human running SCPI manually would likely have stopped at frequency, amplitude, and duty cycle — missing the discriminating measurement.
- The techmanual search for cursor syntax surfaced the right command structure within one query, even if the specific firmware version turned out to be a mismatch.

The knowledge backbone + execution backbone framing holds up in practice. Neither tool alone would have produced the session outcome. The friction points (model applicability gap, single document_id, write-success ambiguity) are all tractable improvements that don't undermine the core value.

---

## Summary of Actionable Feedback

| Item | Tool | Priority |
|---|---|---|
| Write success ≠ command effect — document clearly | agentlink-visa | Low (known SCPI behavior; documentation clarification) |
| Timeout ambiguity (unsupported vs. wrong syntax vs. busy) | agentlink-visa | Medium |
| No signal when programming guide doesn't cover target model | techmanual.ai | High |
| Single `techmanual_document_id` — consider array or companion field | config design | Medium |
| Applicability confidence / coverage gap indicator in search results | techmanual.ai | Medium |
