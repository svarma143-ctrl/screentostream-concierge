name: Pull request
about: Describe the change and the checks you ran.
title: ""
labels: ""
assignees: ""
---

## What changed

## Why

## Checks

- [ ] `make check`
- [ ] `make ui-test` (if the simulator UI changed)
- [ ] Docker build/health (if the image or compose file changed)
- [ ] Docs updated
- [ ] No secrets, tokens, or runtime data added

## Protocol impact

- [ ] MCP `2025-11-25+` and flat `/mcp` unchanged
- [ ] OAuth 2.1 + PKCE S256 and `resource` unchanged
- [ ] All 14 tools still discoverable
