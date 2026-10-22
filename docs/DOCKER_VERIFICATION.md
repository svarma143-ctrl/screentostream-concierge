# Docker verification log

**Date:** 2026-10-21  
**Host:** macOS 15.3, Apple Silicon (arm64)  
**Runtime:** Lima 2.2.1 with its `docker` template (Docker 29.9.2, rootless)

Docker Desktop was not installed. Lima was used as a lightweight, home-directory-only Linux VM so the image could be built and exercised for real. Lima lives under `~/.local/lima`; add `~/.local/lima/bin` to `PATH` to run the commands below verbatim.

---

## 1. Dockerfile lint

```bash
hadolint Dockerfile
```

Result: clean. The only initial warnings were `DL3013` for `pip install .` and `DL3025` for shell-form `HEALTHCHECK`; the health check was converted to JSON form, and the pip warning is suppressed because `pyproject.toml` is the dependency source for the image.

---

## 2. Build

```bash
limactl start --name=concierge-docker --tty=false \
  --cpus=4 --memory=4 --disk=12 \
  --mount "$PWD:w" template:docker

limactl shell concierge-docker -- \
  bash -lc 'docker build --progress=plain -t screentostream-concierge:ci .'
```

Result:

```text
#13 naming to docker.io/library/screentostream-concierge:ci done
#13 DONE 5.2s
```

The build installed `boto3`, FastMCP, FastAPI, and the rest of the runtime dependencies from `pyproject.toml`.

---

## 3. Container health

```bash
limactl shell concierge-docker -- bash -lc '
  docker run -d --name screentostream-ci \
    -e PUBLIC_BASE_URL=http://127.0.0.1:8000 \
    -e LLM_MODE=mock \
    screentostream-concierge:ci
'
```

Result:

```text
screentostream-ci Up (healthy)
```

The image `HEALTHCHECK` calls `/api/health`; the same endpoint returned:

```json
{"status":"ok","database":"ok","mcp":{"path":"/mcp","transport":"streamable-http","minimum_spec":"2025-11-25"}}
```

---

## 4. Full MCP smoke test inside the container

```bash
docker cp scripts/smoke.py screentostream-ci:/tmp/smoke.py
docker exec screentostream-ci \
  python /tmp/smoke.py --base-url http://127.0.0.1:8000
```

Result:

```text
ok  authenticated initialize returns 200
ok  MCP negotiated 2025-11-25 (>= 2025-11-25)
ok  server issued an MCP session id
..  initialize: 3.3 ms
ok  tools/list advertises 14 tools
ok  core booking tools are present
ok  get_viewing_context round trip is 1.9 ms (< 500 ms)
ok  viewing context includes has_live_context

PASS — OAuth discovery, PKCE exchange, MCP handshake, tools, and latency all check out.
```

---

## 5. Public tunnel check

A Cloudflare quick tunnel was also verified end-to-end against the host server:

```text
PRM resource: https://<quick-tunnel>.trycloudflare.com/mcp
AS issuer:    https://<quick-tunnel>.trycloudflare.com
MCP initialize: 114.5 ms
get_viewing_context: 79.2 ms
PASS
```

Quick-tunnel hostnames are ephemeral. The stable verification is the local Docker build plus the GitHub Actions workflow.

---

## 6. CI workflow

`.github/workflows/docker-check.yml` repeats the important parts in GitHub Actions:

1. `docker build -t screentostream-concierge:ci .`
2. `docker run` on port `8000`
3. poll `/api/health`
4. print container logs on failure
5. always remove the container

Both this Docker workflow and the Python lint/test workflow passed on the first push to the public repository. The equivalent local build, health check, and MCP smoke test also passed.

See also: [AWS architecture blueprint](AWS_ARCHITECTURE_BLUEPRINT.md) · [Alexa+ integration guide](ALEXA_PLUS.md).

---

## Cleanup

The verification container and image were removed after testing. The Lima instance was stopped. To repeat:

```bash
limactl start concierge-docker
limactl shell concierge-docker -- bash -lc 'docker build -t screentostream-concierge:ci .'
```
