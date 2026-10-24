#!/usr/bin/env python
"""Explicit live Amazon Bedrock readiness check.

This script is intentionally not a mock. It uses the standard boto3 credential
chain and makes one small Converse call against the configured Claude model.
If credentials or model access are missing, it fails with an actionable error
instead of silently returning a deterministic payload.

Usage:
    uv run python scripts/bedrock_check.py
    uv run python scripts/bedrock_check.py --model us.anthropic.claude-sonnet-4-6

The default is the current global Claude Sonnet 4.6 inference profile. Use
``--model`` when your account exposes a regional or direct model ID instead.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from app.config import settings


def _text_from_response(response: dict) -> str:
    blocks = ((response.get("output") or {}).get("message") or {}).get("content") or []
    return "".join(str(block.get("text") or "") for block in blocks).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check live Amazon Bedrock access.")
    parser.add_argument(
        "--model",
        default=settings.bedrock_text_model_id,
        help="Bedrock model or inference-profile ID to test.",
    )
    parser.add_argument(
        "--region",
        default=settings.bedrock_region,
        help="AWS region for the Bedrock Runtime call.",
    )
    args = parser.parse_args()

    bearer = os.getenv("AWS_BEARER_TOKEN_BEDROCK") or settings.aws_bearer_token_bedrock
    if bearer:
        os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", bearer)
    auth = "short-term Bedrock API key" if bearer else "AWS credential chain"
    print(f"BEDROCK_MODE: {settings.bedrock_mode}")
    print(f"AWS region:   {args.region}")
    print(f"Auth:         {auth}")
    print(f"Model:        {args.model}")
    print()

    try:
        client = boto3.client("bedrock-runtime", region_name=args.region)
        started = time.perf_counter()
        response = client.converse(
            modelId=args.model,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "Reply with exactly: Bedrock OK"}],
                }
            ],
            inferenceConfig={"maxTokens": 12, "temperature": 0},
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except NoCredentialsError:
        print("FAIL: no AWS credentials were found.", file=sys.stderr)
        print("      Configure ~/.aws/credentials, set AWS_PROFILE, use a role,", file=sys.stderr)
        print(
            "      or set AWS_BEARER_TOKEN_BEDROCK to a short-term Bedrock API key.",
            file=sys.stderr,
        )
        print("      Then set BEDROCK_MODE=live and restart the app.", file=sys.stderr)
        return 2
    except ClientError as exc:
        error = exc.response.get("Error") or {}
        code = str(error.get("Code") or "Unknown")
        message = str(error.get("Message") or exc)
        print(f"FAIL: Bedrock rejected the request ({code}).", file=sys.stderr)
        print(f"      {message}", file=sys.stderr)
        if code in {
            "AccessDeniedException",
            "UnauthorizedOperation",
            "ValidationException",
            "ResourceNotFoundException",
        }:
            print(
                "      Check model access in the Bedrock console for this region.", file=sys.stderr
            )
            print(
                "      Claude may require a global or regional inference-profile ID,",
                file=sys.stderr,
            )
            print(
                "      e.g. global.anthropic... or us.anthropic...",
                file=sys.stderr,
            )
            print(
                "      Also confirm the IAM policy allows bedrock:Converse on that resource.",
                file=sys.stderr,
            )
        return 3
    except (BotoCoreError, OSError) as exc:
        print(f"FAIL: could not reach Bedrock: {exc}", file=sys.stderr)
        return 4

    text = _text_from_response(response)
    print(f"PASS: Converse returned in {elapsed_ms} ms.")
    print(f"Response: {text or '(empty text block)'}")
    print()
    print("Live Bedrock access is working. Set BEDROCK_MODE=live to force this provider.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
