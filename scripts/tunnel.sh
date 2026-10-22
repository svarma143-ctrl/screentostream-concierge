#!/usr/bin/env bash
# Start a Cloudflare quick tunnel for the local concierge and print the URL
# that must be used as PUBLIC_BASE_URL.
#
# OAuth discovery advertises the issuer/resource from PUBLIC_BASE_URL, so the
# server must be restarted with the tunnel URL before Alexa+ onboarding:
#
#   ./scripts/tunnel.sh
#   # copy the printed URL, then:
#   PUBLIC_BASE_URL="https://....trycloudflare.com" make run
#
# A quick tunnel is temporary. For a demo, start the tunnel first and leave it
# running for the whole recording so the OAuth client and token audience stay
# stable.

set -euo pipefail

PORT="${PORT:-8000}"
LOG_DIR="${LOG_DIR:-runtime}"
LOG_FILE="${LOG_FILE:-${LOG_DIR}/cloudflared.log}"

mkdir -p "${LOG_DIR}"

CLOUDFLARED="${CLOUDFLARED:-}"
if [[ -z "${CLOUDFLARED}" ]]; then
  if command -v cloudflared >/dev/null 2>&1; then
    CLOUDFLARED="$(command -v cloudflared)"
  elif [[ -x "${HOME}/.local/bin/cloudflared" ]]; then
    CLOUDFLARED="${HOME}/.local/bin/cloudflared"
  else
    cat >&2 <<'EOF'
cloudflared is not installed.

macOS:   brew install cloudflared
Linux:   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
EOF
    exit 1
  fi
fi

if ! curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "The concierge is not answering on 127.0.0.1:${PORT}." >&2
  echo "Start it first with: make run" >&2
  exit 1
fi

: >"${LOG_FILE}"
echo "Opening a quick tunnel to http://127.0.0.1:${PORT} ..."
"${CLOUDFLARED}" tunnel --no-autoupdate --url "http://127.0.0.1:${PORT}" >"${LOG_FILE}" 2>&1 &
TUNNEL_PID=$!

cleanup() {
  kill "${TUNNEL_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

URL=""
for _ in $(seq 1 60); do
  URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "${LOG_FILE}" | head -n 1 || true)"
  if [[ -n "${URL}" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "${URL}" ]]; then
  echo "Could not read a tunnel URL from ${LOG_FILE}." >&2
  exit 1
fi

cat <<EOF

Tunnel is live:

  ${URL}

Restart the server with this public URL so OAuth metadata, the resource
audience, and the registered redirect URI all match:

  PUBLIC_BASE_URL="${URL}" make run

Then check it with:

  uv run python scripts/smoke.py --base-url "${URL}"

Use ${URL}/mcp as the MCP server URL in Alexa+. Keep this tunnel running for
the whole demo. Press Ctrl-C here to close it.
EOF

wait "${TUNNEL_PID}"
