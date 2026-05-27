# Session Playbook: SDS1104X-E Instrument Control
**Date:** 2026-05-26  
**Scope:** Siglent SDS1104X-E (alias: `siglent_sds1104xe`, TCPIP::10.10.10.2::INSTR)  
**Tools:** agentlink-visa MCP + techmanual.ai MCP

---

## 1. Device Discovery and Configuration

**Action:** Ran `agentlink list` via Bash to check for existing configs before touching any MCP tools.

**Result:** One instrument found — `siglent_sds1104xe`, Siglent SDS1104X-E, TCPIP::10.10.10.2::INSTR.

**Action:** Called `connect_instrument("siglent_sds1104xe")`.

**Result:** Connected successfully. IDN response: `Siglent Technologies,SDS1104X-E,SDSMMEBQ3R5552,8.1.6.1.33`. The response also showed `techmanual_document_id: null` — a flag to go find the manual.

**Action:** Called `list_manuals(manufacturer="Siglent", model_number="SDS1104X-E")` to locate documentation.

**Result:** Four documents returned:
| Doc ID | Title | Type |
|---|---|---|
| 102 | SDS1000X-E Series Digital Oscilloscope User Manual | Users Guide |
| 1291 | SDS1000X-E & SDS1000X-U User Manual | Users Guide |
| 1287 | SDS Series Programming Guide (E11F) | Programmers Reference |
| 1323 | SDS Series Programming Guide (latest) | Programmers Reference |

**Decision:** Used doc 1291 as the primary `techmanual_document_id` (user manual, explicitly lists SDS1104X-E as an applicable model, more recent than 102). Noted doc 1323 (programming guide) in a config comment for SCPI reference. Updated `~/.agentlink/instruments/siglent_sds1104xe.toml`.

> **Note:** Doc 1323 (programming guide) lists newer SDS models (SDS5000X, SDS2000X Plus, etc.) but not SDS1104X-E. It was used as a best-available SCPI reference. This later caused cursor query failures — see Section 6.

---

## 2. Getting Bearings: Initial State Snapshot

**Action:** Queried the current instrument state across all channels and timebase settings before touching anything.

**Queries fired in parallel:**
- `C1:TRA?`, `C2:TRA?`, `C3:TRA?`, `C4:TRA?` — channel visibility
- `C1:VDIV?`, `C2:VDIV?` — vertical scales
- `C1:OFST?`, `C2:OFST?` — vertical offsets
- `C1:CPL?`, `C2:CPL?` — input coupling
- `TDIV?` — timebase
- `TRIG_MODE?`, `TRIG_SELECT?` — trigger configuration

**Findings:**
- CH1: ON, 1V/div, offset 0, DC 1MΩ
- CH2: ON, 100mV/div, offset 0, DC 1MΩ
- CH3, CH4: OFF
- Timebase: 5µs/div
- Trigger: AUTO mode, EDGE on **C1**

**Understanding challenge:** CH1 was the trigger source, but CH1 was turned OFF (`TRA OFF`). This meant the scope had no valid trigger reference — it was likely running untriggered in AUTO mode. This was noted and corrected in the next step.

---

## 3. Demo Walkthrough (No Signal Connected)

With no probes connected, ran a sequence of visible changes to demonstrate end-to-end control. All executed as writes with no readback required:

1. `C1:TRA ON` — turned on CH1 so the trigger source was at least visible
2. `TDIV 1E-3` — swept timebase out to 1ms/div
3. `PACU 1,FREQ,C1`, `PACU 2,PKPK,C1`, `PACU 3,RMS,C2`, `PACU 4,PKPK,C2` — enabled on-screen measurement parameters
4. `TDIV 1E-6` — swept timebase back in to 1µs/div
5. `C1:VDIV 500E-3` — changed CH1 scale to 500mV/div
6. `TRMD NORM` — switched trigger mode to NORMAL
7. `TRSE EDGE,SR,C2,HT,OFF` — moved trigger source to CH2
8. `ASET` — issued auto-setup

**Post-ASET readback:** ASET left both CH1 and CH2 on, timebase at 1µs/div, trigger on C2 EDGE, NORMAL mode.

> **Note:** `success: true` on a write confirms the VISA transaction completed without error, not that the instrument acted on the command. All eight writes above returned success. The ASET confirmation came from the subsequent state queries, not from the write itself.

---

## 4. First Signal Characterization (CH2 Square Wave)

**Setup:** User connected a signal. Called `ASET` to let the scope find it.

**ASET result:** Timebase settled at **50ns/div** — immediately indicated a high-frequency signal.

**Parallel measurement queries (CH2):**
```
C2:PAVA? FREQ    → 1.61E+06 Hz
C2:PAVA? PKPK    → 1.44V
C2:PAVA? AMPL    → 1.06V
C2:PAVA? MEAN    → -9.69mV ≈ 0V
C2:PAVA? DUTY    → 49.52%
C2:PAVA? RISE    → 34ns
C2:PAVA? FALL    → 40ns
C2:PAVA? RMS     → 532mV
```

**CH1 (also queried for completeness):**
```
C1:PAVA? FREQ    → **** (no periodic signal)
C1:PAVA? PKPK    → 40mV
C1:PAVA? MEAN    → -1.71V
```

CH1 was DC at −1.71V — a biased reference or negative supply rail, not an intentional signal.

**Post-processing (no manual lookup needed):**
- Overshoot: (PKPK − AMPL) / AMPL × 100 = (1.44 − 1.06) / 1.06 × 100 ≈ **18%**
- Edge bandwidth: 0.35 / 34ns ≈ **10.3 MHz**
- RMS check: For 50% square wave, RMS ≈ AMPL/2 = 1.06/2 = 530mV → measured 532mV ✓

**Signal identified as:** 1.61 MHz square wave, ~50% duty cycle, 1.06Vpp, zero DC offset, 18% overshoot.

---

## 5. Detecting What Changed (First Change Set)

**User action:** Changed several scope and signal generator settings without narrating them.

**Approach:** Re-queried all state in parallel and diffed against the previous snapshot.

**Queries fired:** All channel TRA, VDIV, OFST, CPL, TDIV, TRIG_SELECT, TRMD, plus PAVA measurements on CH1 and CH2.

**Detected changes:**
| Parameter | Before | After |
|---|---|---|
| Timebase | 50ns/div | 200ns/div |
| Trigger mode | NORMAL | AUTO |
| CH2 signal | 1.61MHz, 1.44Vpp | ~24mV noise floor |

**Understanding challenge — missing CH3:** Initial report identified three changes and described the CH2 signal as "gone." The user confirmed correctness but indicated the story was incomplete. The oversight: `C3:TRA` and `C4:TRA` were OFF, and they were queried only for visibility state — not for measurements underneath. A channel being hidden does not mean it has no signal.

**Resolution:** Queried `C3:PAVA? PKPK` and `C4:PAVA? PKPK` on the non-displayed channels. CH3 returned **2.52Vpp** — a live signal that was simply not being shown. CH4 returned `****` (floating/open).

**Lesson encoded:** Always query PAVA measurements on all four channels regardless of TRA state.

---

## 6. CH3 Signal Characterization and Cursor Attempt

**Action:** `C3:TRA ON`, then `ASET`.

**ASET result:** Timebase moved to **1µs/div**, trigger source automatically moved to **C3** — confirming that's where the active signal was.

**Initial measurements were noisy** (scope still settling after ASET + channel enable). Re-queried after stabilization for the final characterization used in Section 7.

---

## 7. Second Signal Characterization (CH3 Triangle Wave)

**User action:** Changed signal generator settings again.

**ASET result:** Timebase settled at **20µs/div** — much slower than before, indicating a lower-frequency signal.

**Parallel measurement queries (CH3):**
```
C3:PAVA? FREQ    → 1.64E+04 Hz (16.4 kHz)
C3:PAVA? PKPK    → 5.44V
C3:PAVA? AMPL    → 5.44V
C3:PAVA? MEAN    → -68mV ≈ 0V
C3:PAVA? DUTY    → 49.27%
C3:PAVA? RISE    → 24.3µs
C3:PAVA? FALL    → 24.4µs
C3:PAVA? RMS     → 1.57V
C3:PAVA? MAX     → +2.68V
C3:PAVA? MIN     → -2.76V
```

**Cursor attempt:**

Searched techmanual doc 1323 for cursor SCPI syntax. Found `:CURSor:X1 <value>` / `:CURSor:X2 <value>` commands. Issued:
```
CURSOR_TYPE TIME         → write succeeded (CRTY X confirmed)
C3:CRVA HREL,X1,-3.05E-05   → write succeeded (cursors visible on screen)
C3:CRVA HREL,X2,3.05E-05    → write succeeded
C3:CRVA? HREL,X1         → TIMEOUT
CURSOR_X1?               → TIMEOUT
```

**Root cause:** Doc 1323 covers the newer SDS multi-cursor architecture. The SDS1104X-E uses older firmware with different cursor read-back command syntax. The write commands happened to be compatible; the query syntax was not. No SDS1104X-E-specific programming guide exists in the techmanual catalog.

**Workaround:** Cursor markers were confirmed visible on screen. Period measurement was derived from PAVA FREQ rather than cursor delta readback.

**Post-processing (waveform type identification):**

Three independent tests:

| Test | Triangle prediction | Measured | Match |
|---|---|---|---|
| RMS | Vp/√3 = 2.72/1.732 = **1.571V** | **1.57V** | ✓ exact |
| Rise time | 0.8 × T/2 = 0.8 × 30.5µs = **24.4µs** | **24.3µs** | ✓ |
| AMPL vs PKPK | Equal (no overshoot) | Both **5.44V** | ✓ |

Sine wave predicted RMS = Vp/√2 = 1.924V — does not match. **Signal conclusively identified as triangle wave.**

---

## 8. Full Change Delta (CH2 Square → CH3 Triangle)

| Parameter | Before | After |
|---|---|---|
| Waveform type | Square | Triangle |
| Frequency | 1.61 MHz | 16.4 kHz (÷98×) |
| Amplitude | 1.06V | 5.44V (+5.1×) |
| Pk-Pk | 1.44V | 5.44V |
| DC offset | −9.7mV | −68mV (both ≈ 0) |
| Duty cycle | 49.5% | 49.3% |
| Overshoot | 18% | 0% |
| Rise time | 34ns | 24.3µs |
| Channel | CH2 | CH3 |
| Timebase | 50ns/div | 20µs/div |

---

## Key Patterns From This Session

1. **Always snapshot all four channels** — TRA OFF does not mean no signal.
2. **ASET is a useful discovery tool** — the channel the trigger moves to after ASET tells you where the active signal is.
3. **Write success ≠ command effect** — follow writes that matter with a confirming query.
4. **AMPL vs PKPK gap = overshoot indicator** — AMPL (histogram-based) equals PKPK when there's no overshoot; the gap is the overshoot amplitude.
5. **RMS is the strongest waveform-type discriminant** — it uniquely distinguishes triangle from sine from square given the same amplitude. Always query it.
6. **Programming guide model mismatch** — confirm that the programming guide in the catalog actually covers your firmware generation before trusting its SCPI syntax for advanced features. Core measurement commands (PAVA, TDIV, VDIV, TRSE) were consistent; cursor architecture was not.
