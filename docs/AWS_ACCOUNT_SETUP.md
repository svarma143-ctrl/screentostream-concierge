# AWS account and Bedrock setup

This is the from-zero path for getting live Amazon Bedrock access for
ScreenToStream Concierge. It is written for a developer who does not yet have
an AWS account.

I cannot create the account, accept the AWS/Anthropic terms, or enter your
payment details for you. Those steps require your identity. Everything after
account creation can be done from this repository.

Official links:

- AWS sign-up: <https://portal.aws.amazon.com/billing/signup>
- Bedrock console: <https://console.aws.amazon.com/bedrock/home>
- Bedrock model access notes: <https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html>
- AWS Free Tier: <https://aws.amazon.com/free/>
- AWS Activate credits: <https://aws.amazon.com/startups/credits/>

---

## New-account credits

> **Hackathon credit note (2026-10-21):** the challenge resources page says the
> AWS credit codes are exhausted as of **October 7, 2026**, and it is no longer
> accepting credit requests. Do not wait for a challenge code. Use the standard
> AWS Free Tier offer below, or your own account.

You do **not** need a promo code for the standard new-customer offer. AWS does
not publish a public “Bedrock coupon code,” and codes sold or shared by
third-party coupon sites are not trustworthy. Use the official programs:

- **AWS Free Tier** — new customers can receive **$100 in credits at sign-up**
  and earn **up to $100 more** by completing activities in the console’s
  *Explore AWS* widget: **up to $200 total for 6 months**. Bedrock is listed as
  available on both the Free and Paid plans, and credits can cover eligible
  usage. The Free plan is limited to select services and is **not** eligible for
  AWS Activate credits.
- **AWS Activate** — for eligible startups: **up to $5,000** for self-funded
  founders (starting at $1,000) or **up to $200,000** for portfolio startups.
  Eligibility requires a Paid-plan AWS account, pre-Series B status, formation
  within the last 10 years, and an accelerator/VC Org ID for the portfolio tier.
  Applications take about 5–10 business days. Activate credits can be used for
  third-party models on Amazon Bedrock.
- **AWS Educate** — free hands-on labs and training, but not a general AWS
  credit grant for this project.

A Gmail address is fine for the AWS Free Tier account and for the personal AWS
Builder ID used to sign in. **AWS Activate is different:** its application guide
asks for a business email address matching the startup's domain, and the
Founders tier requires a fully functioning company website. A Gmail-only
hackathon project with no website is therefore a weak application and may be
rejected. AWS's public checklist does not explicitly demand incorporation
papers, but you must provide accurate startup/business details and not claim
funding, registration, or traction you do not have.

Do not create multiple AWS accounts to farm credits; that violates the program
terms. For a hackathon demo, the standard Free Tier credits are usually enough.

---

## 1. Create the AWS account

1. Open the [AWS sign-up page](https://portal.aws.amazon.com/billing/signup).
2. Enter an email address you control, a password, and an AWS account name.
3. Verify the email code.
4. Choose **Personal** or **Business** account type.
5. Enter contact information.
6. Add a payment method **if prompted**. A payment method is not required for
   most new Free Tier sign-ups, but AWS may request one for identity
   verification or paid services. Some Bedrock models or Marketplace
   subscriptions require a Paid-plan account or a valid payment method.
7. Verify your phone number with the one-time code.
8. Choose the **Basic support plan** (free).
9. Complete identity verification if AWS requests it.
10. Sign in to the AWS Management Console.

Before doing anything else:

- Enable MFA on the root account.
- Do not use the root account for daily API calls.
- Set an AWS Budget alarm if you want a hard cost warning.

---

## 2. Choose a region

Use the region selector in the console. The repository default is:

```text
us-east-1
```

Bedrock model availability varies by region. If the model you want is not
listed in `us-east-1`, try `us-west-2` and set `AWS_REGION` to the same region
in the app.

---

## 3. Create a local IAM identity

For a hackathon, the simplest path is an IAM user with programmatic access and
a least-privilege policy. For a longer-lived deployment, use an IAM role or
IAM Identity Center instead.

1. Open **IAM** in the console.
2. Choose **Users** → **Create user**.
3. Name it something like `screentostream-dev`.
4. Do not enable console access for this user.
5. Create the user.
6. Attach an inline policy using the JSON below.
7. Create an access key for **Application running outside AWS**.
8. Save the access key in a local password manager, not in the repository.

Replace `YOUR_ACCOUNT_ID` with the 12-digit AWS account ID. You can find it in
the account menu in the console.

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
        "arn:aws:bedrock:*:YOUR_ACCOUNT_ID:inference-profile/*"
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

The AWS Marketplace permissions are needed for the first invocation of a
third-party model. After the model subscription is enabled, normal inference
only needs the Bedrock runtime actions.

---

## 4. Complete the Anthropic first-time-use form

Commercial AWS Regions generally enable most Bedrock models on first use rather
than through a separate “Model access” request page. Anthropic models have one
extra requirement.

1. Open the [Amazon Bedrock console](https://console.aws.amazon.com/bedrock/home).
2. Open **Model catalog**.
3. Choose an Anthropic Claude model available in your region.
4. Open it in the playground, or start the first invocation.
5. If prompted, complete the Anthropic first-time-use form:
   - company or project name;
   - website, GitHub profile, or project URL;
   - intended users;
   - use case description.
6. Submit the form.

Access is usually granted quickly, but the agreement can remain pending for a
few minutes. During that time invocations can still return
`AccessDeniedException`. If access still fails after checking the region,
payment method, IAM permissions, and the exact model ID, contact AWS Sales for
additional account-level access.

For this repo, a good use-case description is:

> ScreenToStream Concierge is a self-hosted Alexa+ MCP demo that turns a Fire TV
> viewing moment into a travel and culinary itinerary. Amazon Bedrock Claude is
> used for structured trip intent, narrative, and multimodal frame
> understanding. Nova Canvas (or Titan) is used for itinerary card artwork.

---

## 5. Configure credentials locally

Choose **one** authentication method.

### Option A — short-term Bedrock API key

This is the fastest path and avoids long-lived IAM access keys.

1. In the Bedrock console, open **API keys**.
2. Create a short-term key.
3. Add it to `.env`:

```dotenv
BEDROCK_MODE=live
AWS_REGION=us-east-1
AWS_BEARER_TOKEN_BEDROCK=your-short-term-key
BEDROCK_TEXT_MODEL_ID=the-model-id-from-the-model-card
BEDROCK_VISION_MODEL_ID=the-same-model-id
```

`.env` is ignored by Git and Docker. Do not commit the real value.

The app mirrors this setting into `AWS_BEARER_TOKEN_BEDROCK` before creating
the `boto3` client. It requires `boto3>=1.39`; this repo pins `boto3>=1.43`.

### Option B — IAM access key

Create the standard AWS files locally:

```bash
mkdir -p ~/.aws
chmod 700 ~/.aws
```

`~/.aws/credentials`:

```ini
[default]
aws_access_key_id = YOUR_ACCESS_KEY
aws_secret_access_key = YOUR_SECRET
```

`~/.aws/config`:

```ini
[default]
region = us-east-1
```

Then:

```bash
chmod 600 ~/.aws/credentials ~/.aws/config
```

Set:

```dotenv
BEDROCK_MODE=live
AWS_REGION=us-east-1
```

Never paste access keys into chat, source, issues, or the repository.

### Option C — role or SSO

If you use an EC2 instance profile, ECS task role, or AWS IAM Identity Center,
set `BEDROCK_MODE=live` and the correct `AWS_PROFILE` / role environment. The
standard `boto3` credential chain handles the rest.

---

## 6. Set the model IDs

Open the model card in the Bedrock console and copy the exact ID. Newer models
may use a global or regional inference profile. Examples:

```dotenv
BEDROCK_TEXT_MODEL_ID=global.anthropic.claude-sonnet-4-6
BEDROCK_VISION_MODEL_ID=global.anthropic.claude-sonnet-4-6
```

or a regional profile:

```dotenv
BEDROCK_TEXT_MODEL_ID=us.anthropic.claude-sonnet-4-6
BEDROCK_VISION_MODEL_ID=us.anthropic.claude-sonnet-4-6
```

Do not guess. Use the ID shown for your account and region. The adapter accepts
any Converse-capable model.

For generated hero art, the default is native 16:9:

```dotenv
BEDROCK_IMAGE_MODEL_ID=amazon.nova-canvas-v1:0
```

Titan v1 remains supported:

```dotenv
BEDROCK_IMAGE_MODEL_ID=amazon.titan-image-generator-v1
```

---

## 7. Verify live access

From the repository:

```bash
make bedrock-check
```

This makes one real `Converse` call. It never substitutes a mock response. If
it fails, the error tells you whether the problem is credentials, region,
model access, IAM, or the model ID.

Then start the server with Bedrock live:

```bash
BEDROCK_MODE=live \
PUBLIC_BASE_URL=http://127.0.0.1:8000 \
  uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check the effective provider:

```bash
curl -sS http://127.0.0.1:8000/api/simulator/status | jq '.providers'
```

Expected:

```json
{
  "reasoner": "bedrock",
  "vision": "bedrock",
  "simulator_agent": "bedrock"
}
```

Run one real simulator turn:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/simulator/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Plan a weekend in Kyoto next spring."}' | jq
```

---

## 8. Cost and cleanup

Bedrock bills per input/output token and per generated image. New accounts may
have up to $200 in Free Tier credits, but eligible services, model availability,
and expiration dates vary; some models may bill normally even during the Free
plan. There is no guarantee that Claude inference is free. To keep the demo
cheap:

- use the deterministic rules profile for the public demo;
- keep `BEDROCK_MODE=live` only for the recording/verification;
- use small `PROVIDER_TIMEOUT_SECONDS` and retry limits;
- delete the IAM access key or short-term API key when finished;
- delete any test IAM user you no longer need;
- set an AWS Budget alert before experimenting;
- check the **Credits** page and expiration date in Billing and Cost Management.

The repository has no hardcoded credentials and does not require AWS for the
test suite.

---

## 9. What to send back

When the account is ready, tell me:

- “AWS credentials are configured locally” (do **not** send the secret);
- the region;
- the exact model/inference-profile ID;
- whether you used `.env` bearer token or `~/.aws` access key.

Then I can run `make bedrock-check`, switch the running server to live Bedrock,
and verify the MCP path end to end.
