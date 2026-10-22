# Submission checklist

Last verified: **2026-10-21**

Legend: **done** = reproduced locally, **pending** = needs an external account or
console, **optional** = useful but not required.

---

## Alexa+ MCP requirements

| Requirement | Status | Evidence |
|---|---|---|
| MCP protocol **2025-11-25** or newer | done | `tests/test_mcp_protocol.py`; `scripts/smoke.py` |
| Streamable HTTP transport | done | `POST /mcp` with JSON-RPC and SSE responses |
| Flat `/mcp` endpoint, no custom execute route | done | `app/mcp_server.py` |
| Self-hosted server | done | FastAPI + FastMCP in `app/main.py` |
| OAuth 2.1 authorization-code flow | done | `app/auth.py`; `tests/test_metadata_and_auth.py` |
| PKCE `S256` | done | Advertised in AS metadata; smoke test verifies it |
| Protected-resource metadata | done | `/.well-known/oauth-protected-resource` |
| Authorization-server metadata | done | `/.well-known/oauth-authorization-server` |
| `resource` parameter | done | Advertised and enforced |
| Bearer access tokens | done | `Authorization: Bearer ...` |
| `401` without `WWW-Authenticate` | done | Smoke test asserts the header is absent |
| MCP session id | done | `mcp-session-id` returned by `initialize` |
| All 14 tools listed and callable | done | `tools/list` plus smoke plan/quote/book path |
| Refresh-token rotation and revocation | done | `tests/test_metadata_and_auth.py` |
| Alexa+ toolkit onboarding | pending | Requires Alexa+ toolkit credentials/console access |

---

## AWS Builder Mini Challenge

| Requirement | Status | Evidence |
|---|---|---|
| Amazon Bedrock Runtime through `boto3` | done | `app/aws_bedrock.py` |
| Converse API for structured text | done | Intent, narrative, refinement, card copy |
| Converse tool use for the simulator loop | done | Tool-use block translation in `app/simulator.py` |
| Multimodal frame understanding | done | Claude image blocks from Fire TV frame data |
| Image generation through `InvokeModel` | done | Titan Image Generator v1 or Nova Canvas |
| Provider abstraction, not a new MCP endpoint | done | `app/providers.py`; flat `/mcp` unchanged |
| Deterministic fallback without credentials | done | Rules provider selected when no keys exist |
| Mocked Bedrock adapter tests | done | `tests/test_bedrock.py` |
| Explicit live readiness gate | done | `scripts/bedrock_check.py`; `make bedrock-check` |
| IAM policy and setup guide | done | `docs/AWS_ACCOUNT_SETUP.md`; `docs/AWS_BEDROCK.md` |
| Real AWS account and model access | pending | Blocked on AWS sign-up |
| Live Bedrock `Converse` call | pending | Run `make bedrock-check` after credentials exist |
| Live MCP simulator turn on Bedrock | pending | Verify `/api/simulator/status` reports `bedrock` |

---

## Verification evidence

| Check | Status | Command or document |
|---|---|---|
| Unit/integration suite | done | `make test` — **43 passed** |
| Ruff lint | done | `make lint` |
| Ruff formatting | done | `make format-check` |
| Local OAuth/MCP smoke | done | `make smoke` |
| Public HTTPS tunnel smoke | done | Cloudflare quick tunnel; OAuth/MCP contract passed |
| Docker image build | done | `docs/DOCKER_VERIFICATION.md` |
| Container health check | done | `docs/DOCKER_VERIFICATION.md` |
| In-container MCP smoke | done | `docs/DOCKER_VERIFICATION.md` |
| Dockerfile lint | done | `hadolint Dockerfile` |
| GitHub Actions | done | Python checks and Docker build/health check passed on the first push |
| Demo recording | optional | See `README.md` simulator section |

---

## Repository and submission hygiene

- [x] MIT license
- [x] No hardcoded AWS, DeepSeek, or MiniMax secrets
- [x] `.env` ignored by Git and Docker
- [x] Credential-free test path
- [x] Architecture blueprint
- [x] Open-source friction log
- [x] Product feedback
- [x] From-zero AWS account guide
- [x] CI workflows for tests and Docker
- [x] Push repository to GitHub
- [x] Confirm Actions pass after the first push
- [ ] Add the public repository URL to the challenge submission
- [ ] Record the 2–3 minute demo
- [ ] Confirm the final public tunnel URL before Alexa+ onboarding

---

## Commands

```bash
make check          # lint + format check + tests
make smoke          # local OAuth/MCP contract test (server must be running)
make bedrock-check  # live Bedrock gate; fails if AWS is not ready
```

Before submitting, also run:

```bash
git status --short
git grep -nE 'AKIA[0-9A-Z]{16}|aws_secret_access_key|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY'
```

If that returns real secret material, stop and rotate it before pushing.
