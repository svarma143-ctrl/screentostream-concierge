# Amazon Bedrock Converse agentic tool loop

ScreenToStream Concierge does not use Bedrock as a single text-generation call.
The simulator is a real MCP client, and Bedrock Converse is the decision layer
inside a multi-round tool-use loop over the same 14 MCP tools that Alexa+ sees.

This is the AWS Builder centerpiece: the model chooses a tool, the MCP server
executes it, the result goes back to the model, and the loop continues until the
model can answer or the round budget is exhausted.

---

## The loop

```text
User turn
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ app/simulator.py · _SessionRunner.run_agent()                │
│                                                              │
│  1. MCP client lists the 14 tools                            │
│  2. Bedrock Converse receives the message + toolSpecs        │
│  3. Response may contain toolUse blocks                      │
│  4. Adapter returns generic tool_calls                       │
│  5. Simulator calls the real /mcp endpoint with a bearer     │
│  6. Tool result is appended as a Converse toolResult block   │
│  7. Repeat, up to SIMULATOR_MAX_TOOL_ROUNDS (default 6)      │
│  8. Final text is spoken; display cards are sent over SSE    │
└──────────────────────────────────────────────────────────────┘
```

The loop is implemented in:

- `app/simulator.py` — `_SessionRunner.run_agent()` owns the round budget and
  executes each model-selected tool through the MCP client.
- `app/aws_bedrock.py` — `Bedrock.chat()` makes one Converse turn and converts
  `toolUse` blocks into generic `tool_calls`.
- `app/aws_bedrock.py` — `_convert_tools()` maps MCP/OpenAI-style schemas to
  Converse `toolConfig.tools[].toolSpec`.
- `app/aws_bedrock.py` — `_convert_chat_messages()` maps assistant `tool_calls`
  to `toolUse` blocks and role-`tool` results to `toolResult` blocks.

The tool calls are not simulated inside the adapter. They go through the real
FastMCP client to `POST /mcp`, with the OAuth bearer token and the normal MCP
session handling.

---

## Why this is agentic, not a single call

A single-call integration asks the model for text and returns it. This loop:

1. **Delegates tool choice to the model.** The model decides whether to read the
   viewing context, plan a trip, refine it, quote it, book it, or send a card.
2. **Uses external state.** Each tool result changes the next model turn.
3. **Supports multiple rounds and multiple tool calls per round.**
4. **Keeps the transaction state deterministic.** Catalog selection, pricing,
   quotes, and bookings remain local Python/SQLite operations; the model
   orchestrates the tools but does not invent money or availability.
5. **Degrades safely.** If Bedrock is unavailable, the same tools run through
   the deterministic router.

That is an agent orchestration pattern, not a prompt wrapper.

---

## Evidence

The adapter test suite includes a loop-level test that does not need AWS:

```bash
uv run pytest -q tests/test_bedrock.py
```

`test_bedrock_agent_loop_executes_mcp_tool_and_feeds_result_back` asserts that:

- the fake model's first turn returns a `get_viewing_context` tool call;
- the simulator executes that call through the MCP client;
- the second model turn receives the tool result as a role-`tool` message;
- the final assistant text is returned to the user.

`test_bedrock_chat_maps_tools_and_tool_results` separately proves the
OpenAI-style ↔ Converse `toolSpec` / `toolUse` / `toolResult` translation.

The live gate is intentionally separate:

```bash
make bedrock-check
```

It makes one real `Converse` call and fails with an actionable error if
credentials, region, model access, or IAM are wrong. It never substitutes a
canned response.

---

## Current AWS scope

- **AWS service:** Amazon Bedrock Runtime through `boto3`.
- **APIs:** Converse for text, vision, and tool use; InvokeModel for hero art.
- **Default text/vision model:** `global.anthropic.claude-sonnet-4-6`
  (override with the exact model or inference profile in your account).
- **Default image model:** `amazon.nova-canvas-v1:0`; Titan v1 is also
  supported.
- **Not used:** AgentCore, Strands, SageMaker, or a deployed AWS runtime. This
  is a single-service Bedrock integration with an agentic loop, not a
  multi-service pipeline. The submission does not claim otherwise.

The demo render uses the deterministic fallback because the build environment
had no AWS credentials. The video says so explicitly. The adapter tests and the
live readiness gate are the evidence for the Bedrock path.
