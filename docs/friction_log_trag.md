# ScreenToStream Concierge v0.1.0 developer and judge-path friction log

**Product under test:** The ScreenToStream Concierge repository as a runnable product: `Makefile`, local server, demo simulator, tests, Docker, and CI.

**Log type:** Guided friction log, following the [Trag.dev Friction Log Guide](https://trag.dev/context/friction-log/)
**Different scope from:** [FRICTION_LOG_V2.md](FRICTION_LOG_V2.md), which covers the Alexa+ MCP Toolkit and Amazon Bedrock APIs. This log covers what a judge or contributor experiences when they clone and run the repository itself.

| Reviewer | Status |
| :--- | :--- |
| **Maintainer replaying the judge path** | Complete |
| **ScreenToStream maintainers** | Under review |

## BLUF (bottom line up front)

The judge path is genuinely one command, and the product proves itself without keys: `make demo` boots the real MCP server, the simulator calls the real `/mcp` endpoint, and the tool trace makes the architecture visible. The friction starts when you leave the happy path: port `8000` is a hidden contract, the container path needs a file the Make target does not create, and the default test run skips the browser test that matters most. A judge who follows the README on port `8000` sees a polished demo; a developer who changes one variable hits avoidable dead ends.

**Grade: B** (strong first-run path, sharp edges on the second run)

**Size:** Medium — one repository onboarding and judge-path replay, roughly two hours including the hardening checks.

## Persona

- **Role:** Full-stack engineer and the repository maintainer, deliberately replaying the documented judge path as if seeing the project for the first time.
- **Experience with this product:** Power user of the codebase, first-time user of several operational paths: non-default port, LAN access, production configuration, and the local Docker equivalent of CI.
- **Goal:** Clone the repository, run the documented `make demo` path, click through Play → refine → quote → book, verify the MCP trace, then run tests, the browser test, Docker, and CI without relying on undocumented knowledge.
- **Technical context:** macOS 15.3 on Apple silicon, Python 3.12.15, uv 0.13.4, Chrome via Playwright, SQLite. No local Docker daemon; Docker correctness was verified in GitHub Actions. No live AWS credentials, DeepSeek/MiniMax keys, Alexa+ Preview console, or Fire TV device.

## Scope

- [x] Clone the repository and run the documented `make demo` path on the default port.
- [x] Complete Play → refine → quote → book and confirm the real MCP tool trace.
- [x] Run the documented lint, format, unit-test, and smoke commands.
- [x] Run the Playwright browser test of the judge path.
- [x] Replay the server on a non-default port.
- [x] Try to reach the demo from another device on the LAN.
- [x] Flip the configuration to production values and observe the failure mode.
- [x] Audit the public media mount and the demo/production boundary.
- [x] Verify the Docker build path through CI rather than a local daemon.
- [ ] Live AWS/Bedrock inference, Alexa+ Preview console onboarding, and Fire TV device testing (out of scope for this log).

**Evidence key**

- **Observed:** reproduced in this repository, its tests, its CI, or the live replay.
- **Inferred:** strongly implied by the configuration or documentation, but not reproduced in this environment.

## Pros

- `make demo` really is the judge path. It creates `.env` from the example, installs dependencies, and starts the server without an account, API key, or email.
- The deterministic fallback is not a mock screen. It routes the same 14 MCP tools over the real Streamable HTTP endpoint, so the demo proves the protocol even without credentials.
- The simulator makes the architecture legible: the tool trace shows arguments and latency, and the Fire TV pane consumes a real SSE event.
- The test suite is integration-heavy where it matters: OAuth, PRM/AS metadata, the bare `401`, MCP negotiation, booking lifecycle, signed frame URLs, and production secret checks.
- The documentation is unusually complete for a hackathon: production boundaries, storage limits, security policy, AWS setup, and a separate onboarding path.
- The Docker image is built with the lockfile, runs as UID `10001`, uses a read-only root filesystem, and has a real health check.
- CI covers Python, the browser test, Hadolint, the non-root check, and the container health endpoint.
- The companion `screentostream-video-timeline` repository is installable from git, ships its schema in the wheel, and tests Python 3.10 and 3.12.

## Cons

- The default port is a contract spread across the Makefile, `.env`, CORS defaults, and the simulator's internal MCP client.
- The Makefile's local path and its Docker path disagree about who creates `.env`.
- “Tests pass” locally excludes the browser path by default.
- The first UI test needs a second, large install step that is easy to miss.
- The pre-hardening media mount exposed runtime secrets and the SQLite database through the public `/media` URL.
- A missing FastMCP lifespan produced a 500 on the first MCP request with no startup warning.
- Production fail-closed behavior is correct but arrives as a traceback, one gate at a time.
- Opening the demo from a phone or TV on the LAN returns `400 Invalid host header` until `ALLOWED_HOSTS` is set.
- Exact test counts have drifted across logs and submission notes.
- The local Docker target only builds; the meaningful runtime checks live in CI.
- A 45 MB session export sits untracked at the repository root, so `git status` is never clean.

## Friction

No continuous screen recording was captured. Timestamps are marked `N/A` rather than invented; the evidence is the terminal output, tests, CI runs, and the live replay.

### D-01 — `PORT` changes the listener but not the client

**Severity:** ⚡ Medium · **Timestamp:** N/A (replay from terminal notes; no continuous recording)
**Evidence:** Observed

**Expected:** `PORT` is the only knob. The UI, OAuth metadata, CORS origins, and the simulator's internal MCP client all follow it.

**Actual:** `make demo` listens on `$(PORT)`, but `app/config.py` defaults both `PUBLIC_BASE_URL` and `INTERNAL_MCP_URL` to `http://127.0.0.1:8000`, and `app/main.py` also hard-codes `http://127.0.0.1:8000` and `http://localhost:8000` in the allowed origins. The simulator calls `settings.internal_mcp_url`. During the final live smoke on port `8010`, the page loaded but tool calls still targeted `8000` until both `PUBLIC_BASE_URL` and `INTERNAL_MCP_URL` were overridden. A port conflict on `8000` — common on a development laptop — turns the one-command path into a two-variable debugging session.

**Recommendation:** Derive the internal URL from `PUBLIC_BASE_URL` when it is local, or have `make demo` export `PUBLIC_BASE_URL` and `INTERNAL_MCP_URL` from `PORT`. Add a startup preflight that compares the listener port, `PUBLIC_BASE_URL`, and `INTERNAL_MCP_URL` and prints one line when they disagree. Keep `INTERNAL_MCP_URL` overridable for containers and tunnels.

### D-02 — `docker compose` needs `.env`, but `make docker-up` does not create it

**Severity:** ⚡ Medium · **Timestamp:** N/A
**Evidence:** Observed (configuration and Compose behavior)

**Expected:** `make docker-up` is as self-contained as `make demo`.

**Actual:** `docker-compose.yml` declares `env_file: .env`, but `docker-up` runs `docker compose up --build` without depending on the `env` target. A fresh clone fails with `env file .../.env not found` before the image starts. The README tells the reader to `cp .env.example .env`, so the documented path works, but the Make target does not. We hit this when switching between the local and container paths.

**Recommendation:** Make `docker-up: env`, or give Compose sensible defaults and make `env_file` optional. If the file is required, print “run `make env` first” instead of letting Compose fail. Add a smoke test that runs `docker compose config` without a `.env` and checks the message.

### D-03 — The default test run skips the judge-path browser test

**Severity:** ⚡ Medium · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** The default test command covers the judge path, or clearly says it does not.

**Actual:** `pyproject.toml` sets `addopts = "-m 'not ui'"`; `make test` reports `68 passed, 1 deselected`. The browser test that clicks Play → Quote → Book lives behind `-m ui` and needs Chromium. CI runs it in a separate step, but a local contributor can see a fully green suite while the judge path is broken. We only caught the distinction because pytest prints the deselection count.

**Recommendation:** Add `make test-all` (unit plus UI) and make it the documented pre-push command. In `make check`, print a visible “UI test skipped; run `make ui-test`” line. In CI, keep the separate step but fail if the UI marker collects zero tests.

### D-04 — The browser test has a hidden second install

**Severity:** ℹ️ Low · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** `make ui-test` either works after `make install` or tells you exactly what to run.

**Actual:** `make install` installs Python dependencies only. The first `make ui-test` fails with Playwright's “Executable doesn't exist” until `make ui-install` downloads Chromium plus the headless shell. On this machine the Playwright cache is 572 MB including ffmpeg. The README lists `make ui-install`, but the failure is a second setup step hidden behind a test command.

**Recommendation:** Make `ui-test` depend on `ui-install`, or run a fast check and print the install command with the expected download size. Keep the browser cache in CI and document the one-time cost. Consider a `make ui-test` that skips with a clear message rather than failing when the browser is absent.

### D-05 — The public media mount exposed runtime secrets and the database

**Severity:** ⚠️ High (fixed) · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** Only generated art is public. Uploaded frames are private capabilities, and the signing key and SQLite database are never reachable over HTTP.

**Actual:** The pre-hardening mount was `app.mount("/media", StaticFiles(directory=RUNTIME_DIR))`. That made `runtime/dev-secret.key` and `runtime/concierge.db` reachable at `/media/dev-secret.key` and `/media/concierge.db`, in addition to uploaded frames at guessable names. The demo worked, so nothing failed visibly. The pre-submission audit found it; commit `d5b7bb9` mounts `runtime/media` only and serves frames through expiring HMAC-signed URLs. `tests/test_security_hardening.py` now asserts the static-media boundary.

**Recommendation:** Mount the narrowest directory, never the runtime root. Add a startup assertion that the media root contains only generated assets. Keep regression tests for `/media/dev-secret.key`, `/media/concierge.db`, and expired or tampered frame URLs. State the public/private split in the deployment guide, as `docs/PRODUCTION.md` now does.

### D-06 — The first MCP request returned 500 because the FastMCP lifespan was missing

**Severity:** ⚡ Medium (fixed) · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** Mounting a FastMCP `http_app()` under FastAPI is enough.

**Actual:** The first `/mcp` request returned 500 with no startup error because the FastMCP lifespan was never entered. FastAPI runs the parent lifespan, but the mounted app's task group has to be entered explicitly. The fix is `async with mcp_app.lifespan(app): yield` in `app/main.py`; the module docstring now calls out the failure. This is exactly the kind of bug that makes a judge think OAuth or MCP is broken.

**Recommendation:** FastMCP's FastAPI mounting documentation should lead with the lifespan pattern. Add a health check that performs an `initialize` against `/mcp`, not just `/api/health`. Keep the comment next to the mount so a refactor does not remove it.

### D-07 — Production configuration fails closed as a traceback, one gate at a time

**Severity:** ℹ️ Low · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** Switching to production prints a short list of missing settings and the exact lines to add.

**Actual:** `ENVIRONMENT=production` with the demo defaults raises `ValueError` during settings construction: `DEMO_MODE must be false when ENVIRONMENT=production.` A missing or short `SECRET_KEY` raises a second, separate error. The behavior is correct and fail-closed, but the deployer sees a traceback and fixes one gate at a time.

**Recommendation:** Aggregate production configuration failures into one startup report with variable names and example values. Add `make prod-check` that validates the environment without opening a port. Keep the fail-closed behavior; only improve the presentation. Link the error to `docs/PRODUCTION.md`, which already lists the required values.

### D-08 — LAN access returns `400 Invalid host header`

**Severity:** ⚡ Medium · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** Opening the demo from a phone or TV on the same network works with a clear instruction.

**Actual:** `TrustedHostMiddleware` allows the public host, `localhost`, `127.0.0.1`, `testserver`, `0.0.0.0`, and explicit `ALLOWED_HOSTS`. A LAN address such as `http://192.168.1.50:8000` returns `400 Invalid host header`, which looks like a server failure rather than a host-allowlist decision. We added `ALLOWED_HOSTS` for this reason; the default remains closed.

**Recommendation:** In development, print the detected LAN addresses and the exact `ALLOWED_HOSTS=...` line at startup. Consider allowing RFC1918 hosts only when `ENVIRONMENT=development` and the bind host is `0.0.0.0`. Keep production closed. Add the phone/TV access recipe to the README.

### D-09 — Test counts drift across logs and submission notes

**Severity:** ℹ️ Low · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** Documentation quotes the current suite or avoids exact counts.

**Actual:** Older logs and submission notes say 46 tests; the current suite collects 68 unit tests plus one UI test, and the count changed several times during hardening. Exact numbers are useful evidence, but stale ones are a credibility risk in a judged submission.

**Recommendation:** Generate counts from `pytest --collect-only -q` in CI or omit exact numbers from prose and link the latest green run. If counts stay, add a docs check that fails when README and the submission draft disagree. Keep historical logs dated rather than silently rewritten.

### D-10 — The local Docker target does not mirror the CI gate

**Severity:** ℹ️ Low · **Timestamp:** N/A
**Evidence:** Observed (configuration; no local Docker daemon)

**Expected:** A local Docker command reproduces the CI check.

**Actual:** `make docker-build` only runs `docker build`. CI additionally runs Hadolint, verifies UID `10001`, starts the container, waits for `/api/health`, and prints logs on failure. A contributor can get a green local build that fails the Docker workflow. There is no local Docker daemon in this environment, so the runtime check was CI-only.

**Recommendation:** Add `make docker-check` that runs the same Hadolint/build/UID/health sequence, or extract the CI steps into `scripts/docker_check.sh` and call it from both. Document that Docker runtime verification requires a daemon; keep CI as the authoritative gate when one is unavailable.

### D-11 — The 45 MB session export keeps `git status` dirty

**Severity:** ℹ️ Low · **Timestamp:** N/A
**Evidence:** Observed

**Expected:** A checkout plus intentional local artifacts has a clean or clearly explained status.

**Actual:** `alexa-mcp-server-hackathon-with-deepseek-and-minimax.json` is a 45 MB session export at the repository root. It is intentionally untracked, but it is not in `.gitignore`, so every `git status`, `git add -A`, and review sees it. One careless `git add .` would commit it.

**Recommendation:** Move it to release assets or a local `artifacts/` directory, or add it to `.gitignore` with a note. If it must remain for evidence, document the exclusion in `CONTRIBUTING.md` so contributors do not “fix” the dirty tree by committing it.

## Questions

- Should `PORT` be the single source of truth, with `PUBLIC_BASE_URL` and `INTERNAL_MCP_URL` derived automatically?
- Should `make docker-up` own `.env` creation, or should Compose provide defaults that work without it?
- Should `make test` run the UI test when a browser is present, or should `make check` clearly report the skipped coverage?
- What is the supported phone/TV access story? Should private LAN hosts be allowed in development, or should every address be explicit?
- Should production configuration errors be aggregated into a preflight command instead of raised one at a time?
- Should exact test counts be generated, or omitted in favor of a link to the latest green CI run?
- Should the 45 MB session export be gitignored, moved to release assets, or documented as an intentional local artifact?
- Is Python 3.12 the hard floor, and should `make demo` bootstrap a managed interpreter through uv when the host has an older Python?
- Should the Docker health and non-root checks be reproducible locally, or is CI the only supported runtime gate?

## Recommendations

Ranked by priority:

1. **Make one port setting authoritative.** Derive `INTERNAL_MCP_URL` and the local CORS origins from `PUBLIC_BASE_URL`/`PORT`, or have `make demo` export both. Add a preflight that fails when they disagree. This is the most likely judge-path failure after a port conflict.
2. **Add `make verify`.** Run lint, unit tests, the UI test, and a smoke test against a temporary server. Make skipped UI coverage visible instead of silently deselected.
3. **Make the container path self-contained.** `docker-up: env`, a clear missing-file message, and a `make docker-check` that mirrors Hadolint, the UID check, and the health loop.
4. **Keep the narrow media mount and its regression tests.** Never mount the runtime root; assert that the signing key and database are not reachable.
5. **Replace the production traceback with a startup report.** List every missing or invalid setting once, with example values and a link to `docs/PRODUCTION.md`.
6. **Print the LAN recipe at startup.** Show the detected addresses and the exact `ALLOWED_HOSTS` line in development; keep production closed.
7. **Make `ui-test` self-installing or self-explaining.** Depend on `ui-install`, or check for the browser and print the download size and command.
8. **Generate or de-duplicate test counts.** Avoid exact numbers in prose, or add a docs check that compares README, the submission draft, and `pytest --collect-only`.
9. **Resolve the untracked 45 MB export.** Gitignore it, move it out of the repository root, or document it in `CONTRIBUTING.md`.
10. **Keep the FastMCP lifespan comment and add an MCP health probe.** A real `initialize` check catches the 500 before a judge does.

## Video timestamps

There is no continuous friction recording. The only video is the polished demo, which shows the success path rather than the failures above.

| Time | Event |
| :--- | :--- |
| — | No continuous friction recording was captured; this log is a written replay from the terminal, tests, and CI. |
| 00:00:00 | Not recorded: `make demo` starts the server and opens the simulator. |
| 00:00:00 | Not recorded: Play → refinement → quote → book completes, with the MCP trace visible. |

## Screen recording

- **Full session:** Not captured. The replay was logged through terminal output, test results, CI runs, and notes rather than a screen recording.
- **Demo video (success path):** `demo/screentostream-concierge-demo-andrew-agentic.mp4`. This is the polished judge path, not a friction recording.

## Test environment

- **OS:** macOS 15.3 (24D2059), Apple silicon.
- **Device/Emulator:** MacBook and Chrome via Playwright. No Fire TV device or Alexa+ Preview console.
- **Repository state:** `f7cd81c` on `main`; tag `v0.1.0` points at `dc60ea4`.
- **SDK/API version tested:** MCP `2025-11-25`; OAuth 2.1 + PKCE `S256`; FastAPI/FastMCP; Python 3.12.15; uv 0.13.4; SQLite.
- **Browser:** Playwright Chromium 1248; cache 572 MB including the headless shell and ffmpeg.
- **Docker:** GitHub Actions on `ubuntu-latest` (Hadolint, build, UID `10001`, health). No local Docker daemon.
- **Network:** localhost, including a final smoke run on port `8010`. An ephemeral Cloudflare quick tunnel was used earlier in the build; this replay is local.
- **Cloud/providers:** no live AWS account or model access, no DeepSeek/MiniMax keys, and no Alexa+ Preview console. The deterministic fallback was the default path.

## What succeeded

- ✅ `make demo` created `.env`, installed dependencies, and started the server with no account or API key.
- ✅ Play → refine → quote → book completed in the simulator.
- ✅ The MCP tool trace showed real Streamable HTTP calls to `/mcp`, not a mocked client.
- ✅ The Fire TV pane is wired to the `send_to_tv` SSE event; the browser test asserted the booking path and trace without a mocked MCP client.
- ✅ The optional OAuth link uses the real PKCE `S256` flow and the seeded `demo-user`; the live smoke exercised discovery and token exchange.
- ✅ `pytest` reported 68 passed, 1 deselected; `pytest -m ui` passed after Chromium was installed.
- ✅ Ruff lint and format checks passed; `node --check app/web/app.js` passed.
- ✅ Docker CI passed Hadolint, built the image, verified UID `10001`, and reached `/api/health`.
- ✅ Python and Docker workflows were green on `f7cd81c`.
- ✅ Security-hardening tests covered production secret fail-closed behavior, key rotation, OAuth revocation, confidential clients, demo gating, Basic auth, rate limiting, request IDs, security headers, and signed frame URLs.
- ✅ The companion `screentostream-video-timeline` wheel installed from git in a clean temporary environment, and its 16-test matrix was green on Python 3.10 and 3.12.

## What failed

- ❌ A non-default port broke the simulator's internal MCP client until two environment values were overridden.
- ❌ `docker compose` without `.env` failed before the application started; no local Docker daemon was available to run the full container path.
- ❌ The default `make test` excluded the browser judge path.
- ❌ The first `make ui-test` required a separate, several-hundred-megabyte Chromium download.
- ❌ The pre-hardening `/media` mount exposed the development signing key and SQLite database (fixed in `d5b7bb9`).
- ❌ The first `/mcp` request returned 500 without the explicit FastMCP lifespan (fixed).
- ❌ Production configuration surfaced as a traceback, one failing gate at a time.
- ❌ A LAN address returned `400 Invalid host header` until `ALLOWED_HOSTS` was set.
- ❌ Exact test counts drifted between the older logs and the current suite.
- ❌ Live Bedrock inference, Alexa+ Preview onboarding, and Fire TV device testing were not possible in this environment.

## Appendix

### Scratch pad

Raw notes from the replay:

- `PORT=8010 make demo` served the UI on `8010`, but the simulator still called `http://127.0.0.1:8000/mcp`. Re-running with `PUBLIC_BASE_URL=http://127.0.0.1:8010` and `INTERNAL_MCP_URL=http://127.0.0.1:8010/mcp` fixed it.
- `docker compose up` failed with `env file .../.env not found`. `make env` first, or `cp .env.example .env`, is the undocumented dependency of `make docker-up`.
- `pytest -q` printed `68 passed, 1 deselected`. `pytest -q -m ui` printed `1 passed` only after `uv run playwright install chromium`.
- `du -sh ~/Library/Caches/ms-playwright` reported `572M` (Chromium, headless shell, ffmpeg).
- Pre-hardening, `/media/dev-secret.key` and `/media/concierge.db` were reachable. Post-hardening, `/media` serves `runtime/media` only, and uploaded frames require a signed URL with a TTL.
- The first `/mcp` request returned 500 until `async with mcp_app.lifespan(app):` was added. The parent FastAPI lifespan alone is not enough.
- `ENVIRONMENT=production` with the demo defaults raised `ValueError: DEMO_MODE must be false when ENVIRONMENT=production.` A missing `SECRET_KEY` raised a separate error.
- `http://192.168.1.50:8000` returned `400 Invalid host header` until the address was added to `ALLOWED_HOSTS`.
- `git status` always shows the 45 MB `alexa-mcp-server-hackathon-with-deepseek-and-minimax.json` export. It is intentionally not committed, but it is not ignored either.
- `make docker-build` only builds. The CI workflow also runs Hadolint, the UID check, and the health loop.

### Evidence

- `Makefile` — `demo`, `env`, `test`, `ui-install`, `ui-test`, `smoke`, and `docker-build` targets.
- `docker-compose.yml` — `env_file: .env` and the container-local `INTERNAL_MCP_URL`.
- `pyproject.toml` — Python `>=3.12`, `-m 'not ui'` default, and the `ui` marker.
- `app/config.py` — `PUBLIC_BASE_URL`, `INTERNAL_MCP_URL`, production fail-closed checks, and `ALLOWED_HOSTS`.
- `app/main.py` — FastMCP lifespan, trusted hosts, allowed origins, and the narrow `/media` mount.
- `app/simulator.py` — the internal MCP client that consumes `settings.internal_mcp_url`.
- `app/security.py` — demo gating, Basic auth, rate limiting, request IDs, and security headers.
- `tests/test_security_hardening.py` — production secrets, signed frames, demo gating, and media privacy.
- `tests/ui/test_simulator_ui.py` — Play → Quote → Book browser smoke test.
- `.github/workflows/ci.yml` — lint, format, unit tests, Playwright install, and UI test.
- `.github/workflows/docker-check.yml` — Hadolint, build, UID `10001`, and health check.
- `Dockerfile` — locked install, non-root user, read-only root, and health check.
- `docs/PRODUCTION.md` — production configuration, demo boundary, proxy, and multi-instance caveats.
- `README.md` — quick start, test commands, Docker path, tunnel guidance, and honest-status section.
