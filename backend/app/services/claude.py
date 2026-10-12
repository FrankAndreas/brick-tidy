"""Shared Claude call: photos in, JSON (validated against a schema) out."""

import base64
import json
from pathlib import Path

import anthropic
import cv2

MAX_IMAGE_SIDE = 1568  # larger images are downscaled by the API anyway


class ClaudeError(Exception):
    pass


def ask_json(
    client: anthropic.Anthropic, model: str, system: str, content: list[dict], schema: dict
) -> dict:
    """One request with structured JSON output; errors become ClaudeError with a short message."""
    try:
        # On a safety decline, the API retries on Anthropic's recommended fallback model.
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.AuthenticationError as exc:
        raise ClaudeError("Claude rejected ANTHROPIC_API_KEY") from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeError("Claude is rate limited; try again later") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeError(f"Claude request failed: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError("Cannot reach the Claude API") from exc

    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined to answer for these photos")
    if response.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer was cut off")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def image_block(path: Path) -> dict:
    """JPEG-encode a photo for Claude, downscaled so phone photos stay small."""
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image {path.name}")
    scale = min(1.0, MAX_IMAGE_SIDE / max(image.shape[:2]))
    if scale < 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError(f"Cannot encode image {path.name}")
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg.tobytes()).decode("ascii"),
        },
    }
