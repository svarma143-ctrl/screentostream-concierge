# Demo recording plan

Current submission render: **1:28**. Hard limit: **3:00**. Public on YouTube or
Vimeo, English audio or captions.

The demo must show the product working. The deterministic path is the primary
demo because it needs no credentials. Show the Bedrock integration as code and
tests unless live AWS is working on recording day.

## Current render

- Primary cut: `demo/screentostream-concierge-demo-andrew-agentic.mp4` — Andrew
  voice, updated agentic AWS segment, 1:28
- Preserved alternates: `demo/screentostream-concierge-demo.mp4` (Ava) and
  `demo/screentostream-concierge-demo-andrew.mp4` (previous Andrew cut)
- Captions: `demo/screentostream-concierge-demo-agentic.srt`
- Video: 1920×1080, 25 fps, H.264/AAC, 1:27.8, −16 LUFS
- Voice: neural TTS, −4% rate, −1 Hz pitch (`en-US-AndrewNeural`)
- Capture: Playwright page recording (no screen-recording permission needed)
- Flow: real OAuth 2.1 + PKCE → plan → refine → quote → book → TV overlay → MCP contract page
- AWS: Bedrock Converse tool-use loop slide with the tool-loop code path, a
  passing adapter/loop test, the live gate, and an explicit deterministic
  fallback; no fake live AWS call

### Timeline

| Time | Scene | Narration |
|---|---|---|
| 0:00 | Title card | “This is ScreenToStream Concierge. It turns the show you're watching into a bookable weekend.” |
| 0:06 | OAuth consent + linked state | “The simulator is a real MCP client. It completes OAuth 2.1 with PKCE before calling Alexa Plus.” |
| 0:15 | Play the scene → Kyoto plan card | “Book a weekend stay here next spring. The server resolves Kyoto, builds a two-night stay, and sends a card to the TV.” |
| 0:24 | Refine to cheaper / food-focused | “The guest can refine in natural language. Pricing stays deterministic, so the quote is instant.” |
| 0:32 | Quote card | “The quote is held, then confirmed. The server creates a simulated booking and exports a calendar file.” |
| 0:39 | Booking + send to TV | “After confirmation, it creates a simulated booking, sends the itinerary back to the TV, and exports a calendar file.” |
| 0:51 | TV overlay | “The card lands back on the TV.” |
| 0:54 | `/docs/alexa-plus` contract page | “Under the hood: MCP, Streamable HTTP, fourteen tools, OAuth discovery, and the bare 401 Alexa Plus expects.” |
| 1:04 | AWS Builder slide | “Bedrock's Converse tool loop is the agent: it picks one of fourteen MCP tools, calls the MCP endpoint, and feeds the result back until the trip is planned. The adapter test passes; `make bedrock-check` is the live gate. This render uses the deterministic fallback.” |
| 1:22 | Close card | “The show ends. The trip begins. ScreenToStream Concierge.” |

The older shot list below is retained as a longer-form option if the video is
re-recorded with live Bedrock or Alexa+ console footage.

---

## Before recording

Fastest path:

```bash
make demo
```

Manual path:

```bash
cp .env.example .env
uv sync --all-groups
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

- Use a **1440×900** or **1920×1080** window at 100% zoom.
- Click **Reset demo** to start clean.
- Close unrelated tabs, notifications, and personal bookmarks.
- Do not show `.env`, bearer tokens, AWS keys, or the consent URL's query string.
- Use the original scene art. Do not play third-party music or show unrelated
  streaming brands.
- Record at 1080p/30 fps if possible. Speak slowly; leave a beat for cards.
- Run one dry run without recording and note how long the first plan takes.

Optional preparation: link the simulator once before recording, then reset only
the demo data if that keeps `Linked · Ava` visible. If the OAuth flow is quick,
record it; it is strong evidence for the Alexa+ track.

---

## Shot list

### 0:00–0:15 — Hook

**On screen:** Fire TV pane with the Kyoto scene, then the title.

**Voiceover:** “What if the show you are already watching could become the trip
you take next spring? This is ScreenToStream Concierge, an Alexa+ MCP server
that turns a viewing moment into a bookable weekend.”

**Action:** show the TV scene and the prompt ribbon: “Alexa, book a weekend
stay here next spring.”

### 0:15–0:35 — Real OAuth, real MCP

**On screen:** click **Link account**, consent page, approve, back to
**Linked · Ava**.

**Voiceover:** “The simulator is not a mock chat UI. It runs the same OAuth 2.1
PKCE flow Alexa+ uses, then speaks Streamable HTTP to the real `/mcp` endpoint.”

**Action:** after linking, flash the tool trace or the endpoint badge. Keep this
short. Do not expose the token.

### 0:35–1:05 — The first trip

**On screen:** click **Play the scene**. The first user turn appears, then the
assistant reply and TV card.

**Voiceover:** “Alexa+ sends the viewing context. The server resolves Kyoto,
builds a two-night machiya stay, adds two experiences and dinners, and returns
a spoken summary plus a structured card. The TV gets the card over SSE.”

**Action:** let the plan card land. Point at the TV overlay, the tool trace, and
the provider badge.

### 1:05–1:35 — Refine and quote

**On screen:** type or click:

1. “Make it cheaper and more food-focused.”
2. “Quote it.”

**Voiceover:** “The guest can change the plan in natural language. Pricing and
availability stay deterministic, so the quote is instant and reproducible. The
model handles language; the rules handle money.”

**Action:** show the refined card and the quote card with the total.

### 1:35–1:55 — Book and send it back

**On screen:** type “Book it.” Show the confirmation.

**Voiceover:** “After confirmation, the server creates a simulated booking and
pushes the itinerary back to the TV. The calendar export is real; payment and
inventory are simulated.”

**Action:** show the booking card and the TV overlay. Open the calendar export
only if it is instant.

### 1:55–2:20 — Under the hood

**On screen:** switch to the README architecture section or a terminal running:

```bash
make smoke
```

**Voiceover:** “Under the hood: MCP 2025-11-25 over Streamable HTTP, 14 tools, a
flat `/mcp` endpoint, OAuth discovery, PKCE S256, bare 401 behavior, SQLite
persistence, and a credential-free fallback. The smoke test exercises the whole
contract.”

**Action:** show the smoke test passing and the tool count.

### 2:20–2:40 — AWS Bedrock agentic loop

**On screen:** `app/simulator.py`, `app/aws_bedrock.py`, and a passing adapter
and loop test:

```bash
uv run pytest tests/test_bedrock.py -q
```

**Voiceover:** “Bedrock's Converse tool loop is the agent: it picks one of
fourteen MCP tools, calls the MCP endpoint, and feeds the result back until the
trip is planned. The adapter test passes; `make bedrock-check` is the live gate.
This render uses the deterministic fallback.”

**Honesty note:** if live AWS is not available on recording day, say: “This
recording uses the deterministic fallback; the Bedrock adapter and agent loop
are implemented and tested, and the live gate is documented.” Do not fake a
live AWS call.

### 2:40–2:45 — Close

**On screen:** repo URL and tagline.

**Voiceover:** “ScreenToStream Concierge. The show ends. The trip begins. Try it
without credentials at github.com/svarma143-ctrl/screentostream-concierge.”

---

## If live Bedrock is working

Replace the 2:20–2:40 segment with a live check. Keep the total under 3:00 by
trimming the “Under the hood” segment.

1. Run `make bedrock-check`.
2. Restart with `BEDROCK_MODE=live`.
3. Show `/api/simulator/status` reporting `bedrock`.
4. Run one short turn and point at the provider badge.
5. Say: “This is a live Bedrock Converse call, with the same MCP contract and
   the deterministic path still available as fallback.”

Do not show account IDs, credentials, or the AWS console.

---

## Upload checklist

- [ ] Runtime is under 3:00
- [ ] Product is visibly working
- [ ] The MCP endpoint and OAuth path are visible
- [ ] No secrets, tokens, or personal data
- [ ] No third-party music
- [ ] English audio or English captions
- [ ] Public visibility on YouTube or Vimeo
- [ ] Title: **ScreenToStream Concierge — Alexa+ MCP demo**
- [ ] Description contains the repo URL and the track/mini-challenge names
- [ ] URL pasted into `docs/DEVPOST_SUBMISSION.md` and the Devpost form

Suggested description:

> ScreenToStream Concierge turns a Fire TV viewing moment into a planned,
> quoted, and booked weekend. Built for the Alexa+ track and the AWS Builder
> mini challenge. Self-hosted MCP 2025-11-25 over Streamable HTTP, OAuth 2.1 +
> PKCE, 14 tools, and a browser simulator that is a real MCP client.
>
> Under AWS, a Bedrock Converse tool-use loop chooses and executes those MCP
> tools, with a deterministic fallback for credential-free evaluation.
>
> Code: https://github.com/svarma143-ctrl/screentostream-concierge
> Run locally: `make demo` → http://127.0.0.1:8000. No account, API key, or
> email required.
