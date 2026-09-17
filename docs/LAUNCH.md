# LabLink Launch Checklist

First real distribution test. The repo has existed since 2026-05-26 with ~5 stars and
essentially zero external referrers — that is not a demand signal, because no demand test
has ever been run. This checklist runs one.

Target: ~1 day of work, then a 30-day measurement window.

---

## Phase 0 — Pre-register the kill criteria (do this FIRST)

Fill these in *before* launching. Not after seeing results.

- [ ] Baseline recorded (date, stars, PyPI 30d, clones/uniques) → see Phase 5 table
- [ ] **Continue threshold:** ≥ ____ stars AND ≥ ____ inbound issues/questions from strangers at day 30
- [ ] **Kill threshold:** < ____ stars AND zero stranger engagement at day 30 → shelve, no further pivots
- [ ] Decision date on calendar: ________

Default suggestion: continue at ≥25 stars + ≥1 stranger issue; kill below that with zero engagement.

---

## Phase 1 — Ship blockers

- [ ] **Ship the simulator.** `demo/` is currently untracked (`git ls-files demo` returns nothing);
      only stale `.pyc` files survive locally. Anyone evaluating an instrument-control server
      without a bench cannot try it. This is the single largest adoption blocker.
  - [ ] Recover/rewrite `demo/fake_instruments` source
  - [ ] Remove the bare `venv/` from `demo/`, keep it out of git
  - [ ] Add `examples/configs/simulated_*.toml` so `connect()` works with zero hardware
  - [ ] README: a "Try it with no hardware" section directly under Install
- [ ] **Cut v0.2.0.** Last push was 2026-06-01; a 3.5-month-dormant repo reads as abandoned.
  - [ ] Bump `pyproject.toml` version → `0.2.0`
  - [ ] Bump `server.json` `version` and `packages[0].version` → `0.2.0`
  - [ ] Update `CHANGELOG.md`
  - [ ] `git tag v0.2.0 && git push --tags`, publish GitHub release
  - [ ] Build + upload to PyPI
- [ ] CI green on `ci.yml`
- [ ] README top-line states the two differentiators explicitly: **multi-protocol in one workflow**
      and **`system_topology`** (no competitor has either)
- [ ] Safety note in README: what guardrails exist for an agent driving real hardware

---

## Phase 2 — Registries (highest leverage, lowest effort)

`server.json` is already written and the README already carries the
`<!-- mcp-name: io.github.techmanual-ai/lablink-mcp -->` marker. The prep is done; publishing never happened.

- [ ] **Official MCP registry** — currently returns 0 results for "lablink"
  - [ ] `mcp-publisher login github` (use the `techmanual-ai` account)
  - [ ] `mcp-publisher publish`
  - [ ] Verify: `curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=lablink"`
- [ ] **Glama** — currently 404. Competitors with fewer features are already indexed here.
- [ ] **Smithery**
- [ ] **PulseMCP / mcp.so / Awesome-MCP-Servers** (PR to the awesome list)
- [ ] GitHub repo: set homepage URL, confirm topics (already good)

---

## Phase 3 — Content

- [ ] **Short vertical video** (30–45s) — see script outline in the launch notes
  - [ ] 10s of real bench footage (phone is fine) — the physical hook nobody can fake
  - [ ] Screen capture of an agent driving an instrument end to end
  - [ ] Captions burned in (most views are muted)
- [ ] **One written post**: "I gave Claude hands on my test bench"
  - [ ] Lead with the topology graph + multi-protocol workflow, not SCPI passthrough
  - [ ] Include the LabOSBench framing: measured agent failures are state interpretation and
        long-horizon execution, not command lookup — that is what topology addresses
  - [ ] Embed the video, link the simulator quickstart

---

## Phase 4 — Seed where the users actually are

Space these over ~a week; do not blast in one day.

- [ ] EEVblog forum (Test Equipment section)
- [ ] r/AskElectronics
- [ ] r/labrats
- [ ] r/embedded
- [ ] Hackaday tips line
- [ ] Hacker News — Show HN, weekday morning ET
- [ ] MCP Discord / community channels
- [ ] Direct note to 3–5 small instrument vendors from `manuals_library`
      (Aaronia, Batronix, LabNation, Intona) — separate experiment, cheap to run alongside

---

## Phase 5 — Measurement

Record on day 0, 7, 14, 30. Only day-30 triggers the Phase 0 decision.

| Metric | Day 0 | Day 7 | Day 14 | Day 30 |
|---|---|---|---|---|
| Stars | 5 | | | |
| Forks | 0 | | | |
| Unique views (14d) | 12 | | | |
| Unique clones (14d) | 18 | | | |
| Top referrers | github.com, Google | | | |
| PyPI downloads (30d) | 51 | | | |
| Issues/questions from strangers | 0 | | | |

```sh
R=techmanual-ai/lablink-mcp
gh api repos/$R --jq '{stars:.stargazers_count,forks:.forks_count}'
gh api repos/$R/traffic/views  --jq '{views:.count,uniques:.uniques}'
gh api repos/$R/traffic/clones --jq '{clones:.count,uniques:.uniques}'
gh api repos/$R/traffic/popular/referrers
curl -s https://pypistats.org/api/packages/lablink-mcp/recent
```

**Caveat on these numbers:** unique cloners (18) currently exceed unique viewers (12), which is
backwards for human behavior and indicates most clone/download volume is CI, mirrors, and
indexing bots. Treat stars and *stranger-authored issues* as the real signals. Ignore raw
download counts.
