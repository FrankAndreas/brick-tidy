"""Claude as the AI provider: structured JSON output, safety fallback, cached parts list."""

import json

import anthropic

from app.services.ai import AiError


class ClaudeClient:
    name = "claude"

    def __init__(self, model: str, client: anthropic.Anthropic):
        self.model = model
        self.client = client

    def ask_json(self, system: str, content: list[dict], schema: dict) -> dict:
        """One request with structured JSON output; errors become AiError with a short message."""
        try:
            # On a safety decline, the API retries on Anthropic's recommended fallback model.
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": [anthropic_block(b) for b in content]}],
            )
        except anthropic.AuthenticationError as exc:
            raise AiError("Claude rejected ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise AiError("Claude is rate limited; try again later") from exc
        except anthropic.APIStatusError as exc:
            raise AiError(f"Claude request failed: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AiError("Cannot reach the Claude API") from exc

        if response.stop_reason == "refusal":
            raise AiError("Claude declined to answer for these photos")
        if response.stop_reason == "max_tokens":
            raise AiError("Claude's answer was cut off")
        return json.loads(next(b.text for b in response.content if b.type == "text"))


def anthropic_block(block: dict) -> dict:
    if block["type"] == "image":
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": block["media_type"], "data": block["data"]},
        }
    converted = {"type": "text", "text": block["text"]}
    if block.get("cache"):
        converted["cache_control"] = {"type": "ephemeral"}
    return converted
