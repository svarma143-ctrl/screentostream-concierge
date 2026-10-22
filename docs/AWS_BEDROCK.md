# Amazon Bedrock integration

ScreenToStream Concierge uses **Amazon Bedrock Runtime** as its preferred AI
provider. The MCP server still exposes the same flat `/mcp` Streamable HTTP
contract; Bedrock sits behind the provider adapter layer.

This document is the operator's guide for the AWS Builder Mini Challenge entry.  
For a from-zero AWS account, IAM, and first-time-use walkthrough, see the **[AWS Account Setup Guide](AWS_ACCOUNT_SETUP.md)**.  
For the judging document, see the **[AWS Architectural Blueprint](AWS_ARCHITECTURE_BLUEPRINT.md)**; for the build retrospective, see the **[Open Source Friction Log](FRICTION_LOG.md)**.

---

## Why the adapter layer, not a new tool endpoint

Do **not** replace the MCP server with a REST route such as
`/mcp/tools/execute`. Alexa+ expects JSON-RPC `initialize`, `tools/list`,
`tools/call`, protocol-version negotiation, an MCP session header, OAuth
bearer tokens, and the normal Streamable HTTP responses.

The integration point is therefore:

```text
Alexa+ / simulator
        │  MCP JSON-RPC over Streamable HTTP
        ▼
POST /mcp  (FastMCP, OAuth 2.1)
        │
        ▼
planner / simulator / synthesis
        │
        ▼
app.providers.get_reasoner() / get_vision()
        │
        ├── Amazon Bedrock Runtime (preferred)
        ├── DeepSeek / MiniMax (optional fallback)
        └── deterministic catalog/rules (always available)
```

`app/providers.py` keeps the existing method signatures. When Bedrock is
enabled, those factories return `app.aws_bedrock.Bedrock` instead of the
external HTTP adapter. No MCP tool schema or route changes are required.

---

## Configuration

Add the following to `.env`:

```dotenv
BEDROCK_MODE=live
AWS_REGION=us-east-1
# Optional alternative to IAM access keys:
# AWS_BEARER_TOKEN_BEDROCK=<short-term key from the Bedrock console>
BEDROCK_TEXT_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_VISION_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_IMAGE_MODEL_ID=amazon.titan-image-generator-v1
BEDROCK_IMAGE_QUALITY=standard
BEDROCK_IMAGE_WIDTH=0
BEDROCK_IMAGE_HEIGHT=0
```

`BEDROCK_MODE` has three values:

| Value | Behavior |
|---|---|
| `auto` | Uses Bedrock when standard AWS credential markers are present (`AWS_ACCESS_KEY_ID`, `AWS_PROFILE`, a web-identity token, container credentials, or `AWS_BEARER_TOKEN_BEDROCK`). |
| `live` | Always uses Bedrock. Use this for a shared profile or an instance/task role that `auto` cannot see without querying metadata. |
| `off` | Uses DeepSeek/MiniMax if configured, otherwise the deterministic path. |

The standard AWS credential chain is used by `boto3`, so you can also rely on
`~/.aws/credentials`, `AWS_PROFILE`, ECS task roles, or EC2 instance profiles.
Do not put long-lived AWS keys in the repository.

Set `LLM_MODE=mock` to force deterministic mode in tests or a stage demo.

### Minimum access checklist

To verify live Bedrock, provide or configure the following on the machine that
runs the server:

1. **AWS account and region** — use a region where Bedrock and the selected
   Claude model are available. The repo default is `us-east-1`.
2. **Anthropic first-time-use form** — in the Bedrock console, open an
   Anthropic model from **Model catalog** and complete the first-time-use form.
   In commercial regions, most models are enabled on first invocation rather
   than through a separate **Model access** page. Some models require extra
   account-level access; if `AccessDeniedException` persists after the form and
   region check, contact AWS Sales.
3. **A valid payment method** — required for AWS Marketplace subscriptions.
4. **Credentials** — one of:
   - `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (and `AWS_SESSION_TOKEN`
     for temporary credentials);
   - `AWS_PROFILE` pointing at `~/.aws/credentials` / `~/.aws/config`;
   - an ECS task role, EC2 instance profile, or web-identity role;
   - `AWS_BEARER_TOKEN_BEDROCK` with a short-term Bedrock API key.
5. **IAM permission** — `bedrock:Converse` and `bedrock:InvokeModel` on the
   selected foundation model or inference profile. First use of a Marketplace
   model also needs `aws-marketplace:Subscribe`,
   `aws-marketplace:Unsubscribe`, and `aws-marketplace:ViewSubscriptions`.
   Image generation also needs `InvokeModel` on Titan/Nova.
6. **Exact model ID** — copy the model or inference-profile ID from the
   console. Claude may require a global or regional profile such as
   `global.anthropic...` or `us.anthropic...` rather than the direct ID.
7. **Optional image model access** — Titan Image Generator v1 or Nova Canvas.

Do not paste secret access keys into chat, source files, issues, or the
repository. Configure them locally, then run:

```bash
make bedrock-check
```

The check makes one real `Converse` call. It does not require the web server to
be running and it never substitutes a mock response.

---

## Model access and IDs

In commercial AWS Regions, most Bedrock models are enabled automatically on
first invocation once the principal has the required permissions. Anthropic
models additionally require the one-time first-time-use form. If an invocation
returns `AccessDeniedException`, check the region, the first-time-use form,
the payment method, the AWS Marketplace permissions, and the model or
inference-profile ID before assuming the code is wrong.

The defaults are:

| Purpose | Default model |
|---|---|
| Intent, narrative, refinement, tool loop | `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| Frame understanding | same Claude model |
| Hero art | `amazon.titan-image-generator-v1` |

Some AWS accounts and regions expose Claude through a cross-region inference
profile. If the direct model ID is rejected, use the exact inference-profile
ID shown in your Bedrock console, for example:

```dotenv
BEDROCK_TEXT_MODEL_ID=us.anthropic.claude-3-5-sonnet-20241022-v2:0
BEDROCK_VISION_MODEL_ID=us.anthropic.claude-3-5-sonnet-20241022-v2:0
```

Newer models may use a global profile such as
`global.anthropic.claude-opus-5`. Use the exact ID shown on the model card in
your account; the adapter accepts any Converse-capable model ID.

For a native 16:9 hero, use:

```dotenv
BEDROCK_IMAGE_MODEL_ID=amazon.nova-canvas-v1:0
```

Titan v1 is rendered at 1024×1024. Nova Canvas uses 1280×720 for the card
hero. The card CSS uses `background-size: cover`, so both crop cleanly.

---

## IAM policy

Attach a least-privilege policy similar to this to the user, role, or task
identity running the app. Replace the account ID and model IDs with the ones
your account actually exposes.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockConciergeRuntime",
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:Converse"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/anthropic.*",
        "arn:aws:bedrock:*::foundation-model/amazon.titan-image-generator-v1",
        "arn:aws:bedrock:*::foundation-model/amazon.nova-canvas-v1:0",
        "arn:aws:bedrock:*:123456789012:inference-profile/*"
      ]
    },
    {
      "Sid": "BedrockModelSetup",
      "Effect": "Allow",
      "Action": [
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel",
        "bedrock:GetFoundationModelAvailability",
        "bedrock:ListFoundationModelAgreementOffers",
        "bedrock:PutUseCaseForModelAccess"
      ],
      "Resource": "*"
    },
    {
      "Sid": "MarketplaceFirstUse",
      "Effect": "Allow",
      "Action": [
        "aws-marketplace:Subscribe",
        "aws-marketplace:Unsubscribe",
        "aws-marketplace:ViewSubscriptions"
      ],
      "Resource": "*"
    }
  ]
}
```

If your deployment streams responses in future, add
`bedrock:InvokeModelWithResponseStream` to the same statement. The current
adapter uses non-streaming Converse and InvokeModel calls.

---

## What the adapter does

`app/aws_bedrock.py` implements the same provider surface used by the planner
and simulator:

| Method | Bedrock path |
|---|---|
| `parse_intent` | Converse + structured `TravelIntent` prompt |
| `narrate_plan` | Converse + narrative prompt |
| `refine_intent` | Converse + refinement prompt |
| `synthesize_itinerary_card` | Converse + card copy schema |
| `dish_story` | Converse + culinary schema |
| `analyze_frame` | Converse with a Claude multimodal image block |
| `chat` | Converse tool-use loop |
| `generate_image` | InvokeModel with Titan/Nova |

All synchronous `boto3` calls are wrapped in `asyncio.to_thread`, so a slow
Bedrock response does not block the event loop or the MCP transport.

### Why there is no mock inside the adapter

Do not add an `is_mock` branch that returns canned travel payloads when
credentials are missing.

- `boto3.client()` can succeed without credentials because the standard chain
  resolves lazily. The real failure appears on the first `converse` call.
- A mock branch would hide that failure and make a “live Bedrock” claim
  impossible to verify.
- Provider fallback already lives one layer up in `app/providers.py`. When
  Bedrock is disabled, the planner and simulator use DeepSeek/MiniMax or the
  deterministic rules path. The adapter itself should only speak to Bedrock.
- For an explicit live check, run `make bedrock-check`. It makes a real
  `Converse` call and returns an actionable error if credentials, region,
  model access, or IAM are wrong.

### Tool-loop translation

The simulator keeps using its existing OpenAI-style message list and MCP tool
schemas. The Bedrock adapter translates:

- OpenAI `tools[].function` → Converse `toolConfig.tools[].toolSpec`
- assistant `tool_calls` → Converse `toolUse` content blocks
- role `tool` results → Converse `toolResult` content blocks
- Converse `toolUse` blocks → the simulator's generic call shape

The tool calls still execute through the real MCP client against `/mcp`.

### Image handling

Frame uploads arrive as data URLs. The adapter converts them to Converse image
bytes with the correct `png`, `jpeg`, `gif`, or `webp` format. The existing
`app/media.py` metadata (dimensions, orientation, aspect ratio) is included in
the prompt.

Generated art is cached under `runtime/generated/` and served from
`/media/generated/...`. The plan is served immediately with a deterministic
card; Bedrock copy and hero art are optional background polish.

---

## Verify the integration

Start the server:

```bash
uv sync --all-groups
BEDROCK_MODE=live uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Before or while the server is running, make one real Bedrock call:

```bash
make bedrock-check
# or:
uv run python scripts/bedrock_check.py --model us.anthropic.claude-3-5-sonnet-20241022-v2:0
```

This is deliberately not mocked. A missing credential, disabled model,
wrong region, or IAM denial fails with a specific error and a suggested fix.

Check the effective provider:

```bash
curl -sS http://127.0.0.1:8000/api/simulator/status | jq '.providers'
```

Expected with Bedrock live:

```json
{
  "bedrock": {
    "configured": true,
    "mode": "live",
    "region": "us-east-1",
    "text_model": "anthropic.claude-3-5-sonnet-20241022-v2:0"
  },
  "reasoner": "bedrock",
  "vision": "bedrock",
  "simulator_agent": "bedrock"
}
```

Run one real MCP turn:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/simulator/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Plan a weekend in Kyoto next spring."}' | jq
```

The trace should show real MCP tool calls; `/api/usage` should show `bedrock`
rows with latency and status.

No-credential behavior is still a first-class path:

```bash
BEDROCK_MODE=off LLM_MODE=auto uv run uvicorn app.main:app --port 8000
```

That selects DeepSeek/MiniMax when keys exist, otherwise the deterministic
rules provider. The simulator badge changes from `bedrock` to `deepseek`,
`minimax`, or `rules` accordingly.

---

## Tests

The Bedrock adapter has isolated tests that do not call AWS:

```bash
uv run pytest -q tests/test_bedrock.py
```

Coverage includes:

- Converse structured intent parsing and provider metadata
- OpenAI tool schema → Converse `toolSpec` translation
- assistant `tool_calls` → `toolUse` and `toolResult` round trip
- frame data URL → Converse image block
- Titan/Nova image response caching
- provider factory and status selection

The rest of the suite continues to run with `LLM_MODE=mock`, so CI never needs
AWS credentials.

---

## AWS Builder Mini-Challenge profile

**AWS Services Utilized:** Amazon Bedrock Runtime (`boto3`).

**Active Models Configured:** `anthropic.claude-3-5-sonnet-20241022-v2:0` for
the intent/reasoning core and multimodal frame understanding;
`amazon.titan-image-generator-v1` (or `amazon.nova-canvas-v1:0`) for itinerary
card imagery.

**Integration Strategy:** Inbound MCP requests hit the flat Streamable HTTP
`/mcp` endpoint. The existing FastMCP tools call the shared planner and
provider interfaces. When Bedrock is enabled, the provider factory routes
reasoning, refinement, frame analysis, tool-loop turns, and image generation
through Bedrock Runtime. The deterministic catalog/rules path remains the
fallback, and the MCP protocol/OAuth contract is unchanged.
