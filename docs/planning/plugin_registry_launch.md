# Plugin & Registry Launch

**Status: PENDING** — waiting on hardware validation and demo before publishing.

---

## Path 1 — MCP Registry (modelcontextprotocol.io)

`server.json` is complete and ready at the repo root.

```bash
npm install -g @modelcontextprotocol/mcp-publisher
mcp-publisher login   # GitHub OAuth via techmanual-ai org
mcp-publisher publish # run from repo root where server.json lives
```

Verify listing after publish:
```bash
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.techmanual-ai"
```

---

## Path 2 — Claude Code Plugin (self-hosted marketplace)

Requires a public GitHub repo (e.g. `techmanual-ai/agentlink-visa`) with the following structure:

```
.claude-plugin/
  plugin.json        # name, description, author
  marketplace.json   # makes the repo a marketplace
.mcp.json            # MCP connection config (stdio, calls agentlink-mcp)
skills/
  agentlink-visa/
    SKILL.md         # tells Claude when to reach for the tools
README.md
```

Unlike techmanual, agentlink's `.mcp.json` must use stdio transport (no HTTP server), so users still need `pip install agentlink-visa` before the plugin works. The plugin adds discoverability but cannot skip local install.

User install flow once live:
```
/plugin marketplace add techmanual-ai/agentlink-visa
/plugin install agentlink-visa@techmanual-ai
```

**Lower priority than Path 1** given the mandatory local install requirement.

---

## Path 3 — Anthropic Official Marketplace (future)

Submit via web form at `claude.ai/settings/plugins/submit`. Goes through Anthropic's internal review. No published SLA.

Prerequisite: Path 1 or 2 live and verified first.

---

## Prerequisites Before Publishing Either Path

- [ ] Hardware in hand and instrument connected
- [ ] End-to-end demo complete (connect → query → measure → report)
- [ ] `agentlink list`, `agentlink connect`, CLI verified on real hardware
- [ ] MCP tools verified via Claude Code with real instrument
- [ ] `pip install agentlink-visa` install path smoke-tested on a clean machine
