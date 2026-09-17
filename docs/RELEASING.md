# Releasing

Releases are tag-triggered. `.github/workflows/release.yml` builds, tests,
publishes to PyPI, and cuts the GitHub release.

---

## One-time setup: PyPI Trusted Publishing

Trusted Publishing lets PyPI verify a GitHub Actions OIDC identity instead of
an API token, so no long-lived credential is stored in repo secrets or on
anyone's machine. This must be configured once, by a PyPI owner of the project.

1. Sign in to PyPI → **lablink-mcp** → *Manage* → *Publishing*
2. Under *Trusted publishers*, add a **GitHub** publisher:

   | Field | Value |
   |-------|-------|
   | Owner | `techmanual-ai` |
   | Repository name | `lablink-mcp` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

3. In GitHub → *Settings* → *Environments*, create an environment named
   `pypi`. Adding yourself as a required reviewer there gives you a manual
   approval gate before any upload — recommended, since a version number
   burned on PyPI can never be reused.

All four fields must match exactly or PyPI rejects the OIDC exchange.

---

## Cutting a release

1. Bump the version in **both** places — the workflow refuses to publish if
   they disagree with each other or with the tag:
   - `pyproject.toml` → `[project] version`
   - `server.json` → top-level `version` **and** `packages[0].version`
2. Move `[Unreleased]` in `CHANGELOG.md` to `[X.Y.Z] - YYYY-MM-DD`. The release
   notes are extracted from this section, so write it before tagging.
3. Merge to `main`.
4. Tag and push:

   ```sh
   git tag -a v0.3.0 -m "v0.3.0"
   git push origin v0.3.0
   ```

The workflow then runs `check-version` → `test` (3.10–3.13) → `build` →
`publish-pypi` → `github-release`. Any failure stops the chain before upload.

### Dry run

Run the workflow manually from the *Actions* tab with **publish** left
unchecked. Everything builds and is verified; nothing is uploaded.

---

## What the guards catch

| Guard | Prevents |
|-------|----------|
| Tag vs `pyproject.toml` vs `server.json` | Shipping a wheel whose version disagrees with the registry manifest |
| PyPI existence check | Wasting a version number on a duplicate upload that would fail anyway |
| Full test matrix | Publishing code that fails on a supported Python |
| `twine check` | Malformed metadata that renders badly on the project page |
| Wheel contents check | Shipping without `lablink/demo/` or the `lablink-sim` entry point — both are packaging-config dependent and fail silently |

---

## After publishing

Update the MCP registry, which is a separate system with its own auth:

```sh
mcp-publisher login github     # browser flow
mcp-publisher publish          # reads server.json
```

Verify:

```sh
curl -s "https://registry.modelcontextprotocol.io/v0/servers?search=lablink"
```
