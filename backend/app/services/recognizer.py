"""Part recognition: Brickognize, with an AI (Infomaniak or Claude) as fallback for unsure crops."""

from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from app.services.ai import AiClient, AiError, image, text


class RecognitionError(Exception):
    pass


@dataclass(frozen=True)
class Candidate:
    part_num: str
    name: str
    score: float
    img_url: str | None = None
    source: str = "brickognize"  # or the AI provider: "infomaniak" or "claude"
    reason: str | None = None  # the AI's explanation

    def to_dict(self) -> dict:
        return asdict(self)


class BrickognizeRecognizer:
    """Sends one crop per request; Brickognize returns ranked part candidates.

    Brickognize does not report a reliable color, so color is chosen during review.
    Its part IDs follow BrickLink, which match Rebrickable for most common parts.
    """

    def __init__(self, url: str, client: httpx.Client | None = None, max_candidates: int = 5):
        self.url = url
        self.client = client or httpx.Client(timeout=30)
        self.max_candidates = max_candidates

    def recognize(self, image_path: Path) -> list[Candidate]:
        try:
            with open(image_path, "rb") as fh:
                response = self.client.post(
                    self.url,
                    files={"query_image": (image_path.name, fh, content_type(image_path))},
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RecognitionError(f"Brickognize request failed: {exc}") from exc

        items = [
            i for i in payload.get("items", []) if i.get("id") and i.get("type", "part") == "part"
        ]
        items.sort(key=lambda i: i.get("score", 0), reverse=True)
        return [
            Candidate(
                part_num=str(item["id"]),
                name=item.get("name", ""),
                score=float(item.get("score", 0)),
                img_url=item.get("img_url"),
            )
            for item in items[: self.max_candidates]
        ]


AI_PART_SYSTEM = """\
You identify a single LEGO part in a photo. It belongs to the set whose parts list \
is given (Rebrickable part numbers and names). Choose up to 3 parts from that list \
that the photo most likely shows, most likely first, with a short reason. \
"confidence" is your probability from 0 to 1. Ignore the color; it is checked \
separately. Return an empty list if the photo shows no part from the list."""

AI_PART_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part_num": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["part_num", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


class AiPartRecognizer:
    """Fallback for crops Brickognize is unsure about: the AI picks from the set's parts."""

    def __init__(self, ai: AiClient):
        self.ai = ai

    def recognize(self, image_path: Path, set_parts: dict[str, str]) -> list[Candidate]:
        parts_list = "\n".join(f"{num}\t{name}" for num, name in sorted(set_parts.items()))
        content = [
            # The list is the same for every crop of a project: cache it.
            text(f"Parts list:\n{parts_list}", cache=True),
            image(image_path),
            text("Which part from the list is this?"),
        ]
        try:
            answer = self.ai.ask_json(AI_PART_SYSTEM, content, AI_PART_SCHEMA)
        except AiError as exc:
            raise RecognitionError(str(exc)) from exc
        return [
            Candidate(
                part_num=c["part_num"],
                name=set_parts[c["part_num"]],
                score=min(max(float(c["confidence"]), 0.0), 1.0),
                source=self.ai.name,
                reason=c["reason"] or None,
            )
            for c in answer["candidates"]
            if c["part_num"] in set_parts  # only parts that are really in the list
        ]


def content_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
