"""Minimal Bedrock connectivity check.

Run after filling in .env:
    source .venv/bin/activate && python3 scripts/verify_bedrock.py

Confirms the Bedrock API key, region, and Claude Haiku 4.5 model id all work.
If Tokyo cannot reach the global model, set AWS_REGION=us-east-1 in .env and retry.
"""

from __future__ import annotations

import sys

from anthropic import AnthropicBedrock

from c3po.config import config


def main() -> int:
    print(f"Region:   {config.aws_region}")
    print(f"Model id: {config.bedrock_model_id}")
    print("Calling Bedrock...")

    # api_key resolves from AWS_BEARER_TOKEN_BEDROCK in the environment.
    client = AnthropicBedrock(aws_region=config.aws_region)

    try:
        message = client.messages.create(
            model=config.bedrock_model_id,
            max_tokens=64,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly: c3po bedrock ok",
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface the raw error to the user
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nHints:\n"
            "  - Ensure AWS_BEARER_TOKEN_BEDROCK is set in .env\n"
            "  - Ensure the region has Claude Haiku 4.5 access enabled\n"
            "  - If the region rejects the global model id, try AWS_REGION=us-east-1",
            file=sys.stderr,
        )
        return 1

    text = "".join(block.text for block in message.content if block.type == "text")
    print(f"\nResponse: {text}")
    print(f"Usage: in={message.usage.input_tokens} out={message.usage.output_tokens}")
    print("\nSUCCESS: Bedrock reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
