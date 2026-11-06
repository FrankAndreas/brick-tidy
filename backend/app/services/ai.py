"""The AI fallback for sets and parts: one small interface, several providers.

A provider takes a system prompt, content and a JSON schema, and returns the answer
as a dict that matches the schema, or raises AiError with a short message.

Content is provider-neutral: text blocks from `text()` and photos from `image()`.
Each provider turns them into its own request format. Only the photos leave the
house, downscaled and re-encoded as JPEG (which drops EXIF, e.g. GPS).
"""

import base64
from pathlib import Path
from typing import Protocol

import cv2

MAX_IMAGE_SIDE = 1568  # Claude downscales larger images anyway; plenty for Gemma too


class AiError(Exception):
    pass


class AiClient(Protocol):
    name: str  # "claude" or "infomaniak", also the candidates' source
    model: str

    def ask_json(self, system: str, content: list[dict], schema: dict) -> dict: ...


def text(value: str, cache: bool = False) -> dict:
    """A text block; `cache` marks a long prefix that repeats (only Claude uses it)."""
    return {"type": "text", "text": value, "cache": cache}


def image(path: Path) -> dict:
    """A photo as a JPEG block, downscaled so phone photos stay small."""
    picture = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if picture is None:
        raise ValueError(f"Cannot read image {path.name}")
    scale = min(1.0, MAX_IMAGE_SIDE / max(picture.shape[:2]))
    if scale < 1.0:
        picture = cv2.resize(picture, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", picture, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError(f"Cannot encode image {path.name}")
    return {
        "type": "image",
        "media_type": "image/jpeg",
        "data": base64.standard_b64encode(jpeg.tobytes()).decode("ascii"),
    }


def schema_errors(value, schema: dict, path: str = "$") -> list[str]:
    """Checks `value` against the small JSON-schema subset our schemas use."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path} is not an object"]
        errors = [f"{path}.{key} is missing" for key in schema.get("required", []) if key not in value]
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors += schema_errors(item, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key} is not allowed")
        return errors
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path} is not an array"]
        return [e for i, item in enumerate(value) for e in schema_errors(item, schema["items"], f"{path}[{i}]")]
    if kind == "string" and not isinstance(value, str):
        return [f"{path} is not a string"]
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        return [f"{path} is not a number"]
    return []
