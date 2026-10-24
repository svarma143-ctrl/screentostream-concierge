# YouTube / Vimeo upload copy

## Title

ScreenToStream Concierge — Alexa+ MCP demo

## Visibility

**Public.** The hackathon requires a public YouTube or Vimeo URL.

## Renders

The updated submission cut is:

- `demo/screentostream-concierge-demo-andrew-agentic.mp4` — Andrew (male neural
  voice), 1:28, with the new Bedrock Converse agentic-loop AWS segment

The earlier renders are preserved as alternates:

- `demo/screentostream-concierge-demo.mp4` — Ava (female neural voice), 1:28
- `demo/screentostream-concierge-demo-andrew.mp4` — Andrew (male neural voice),
  1:28, previous AWS segment

Upload the updated Andrew render as the public submission video.

## Description

ScreenToStream Concierge turns a Fire TV viewing moment into a planned, quoted,
and booked weekend. Built for the Alexa+ track and the AWS Builder mini
challenge.

Alexa+ already plans travel; this is a reference MCP add-on for the
content-to-transaction seam, not a replacement for Alexa+'s native travel
planning or its Expedia/eDreams booking partners.

The browser simulator is a real MCP client: it completes OAuth 2.1 with PKCE,
gets a bearer token, and calls the same self-hosted Streamable HTTP `/mcp`
endpoint that Alexa+ uses. The server exposes 14 tools for context, planning,
refinement, quotes, bookings, TV cards, preferences, and calendar export.

The demo uses the deterministic, credential-free path for the live product flow.
The AWS Builder segment shows the implemented Amazon Bedrock Runtime adapter
(`Converse` + `InvokeModel`) and the multi-round Converse tool-use loop that
orchestrates the 14 MCP tools. It shows the adapter/loop test and the live gate,
and it is explicit about the fallback; it does not fake a live AWS call.

Code: https://github.com/svarma143-ctrl/screentostream-concierge

Run locally in one command: `make demo` → http://127.0.0.1:8000. No account,
API key, or email required. The OAuth link is optional and demonstrates the
real PKCE flow with a seeded demo user.

Chapters:
0:00 Title
0:06 OAuth 2.1 + PKCE
0:15 Plan a Kyoto weekend
0:24 Refine and quote
0:39 Book and send to the TV
0:51 TV overlay
0:54 MCP contract
1:04 Bedrock Converse agentic loop
1:22 Close

## Captions

Upload `demo/screentostream-concierge-demo-agentic.srt` as English captions. The
MP4 also contains a soft English subtitle track.

## Thumbnail

`demo/screentostream-concierge-thumbnail.png`

## Tags

Alexa+, Amazon Alexa, MCP, Model Context Protocol, Streamable HTTP, OAuth 2.1,
PKCE, AWS, Amazon Bedrock, Bedrock Runtime, Fire TV, travel concierge, AI agent,
hackathon
