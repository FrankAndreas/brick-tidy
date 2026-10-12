"""Set identification from photos: Brickognize first, Claude as an optional fallback.

Both only suggest candidates; the user confirms the set (or types its number).
Candidates are checked against the Rebrickable catalog so that only sets with a
known inventory are offered.
"""

import base64
import json
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import cv2
import httpx

from app.services.inventory import get_set, normalize_set_num
from app.services.recognizer import content_type


class SetIdentificationError(Exception):
    pass


@dataclass(frozen=True)
class SetCandidate:
    set_num: str
    score: float
    source: str  # "brickognize" or "claude"
    evidence: str | None = None  # Claude's reason, e.g. a set number read on the box


class BrickognizeSetIdentifier:
    """Brickognize's set recognition; several views of one model can be sent at once."""

    def __init__(self, url: str, client: httpx.Client | None = None, max_candidates: int = 5):
        self.url = url
        self.client = client or httpx.Client(timeout=30)
        self.max_candidates = max_candidates

    def identify(self, images: list[Path]) -> list[SetCandidate]:
        try:
            with ExitStack() as stack:
                files = [
                    ("query_image", (p.name, stack.enter_context(open(p, "rb")), content_type(p)))
                    for p in images
                ]
                response = self.client.post(self.url, files=files)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SetIdentificationError(f"Brickognize request failed: {exc}") from exc

        items = [
            i for i in payload.get("items", []) if i.get("id") and i.get("type", "set") == "set"
        ]
        items.sort(key=lambda i: i.get("score", 0), reverse=True)
        return [
            SetCandidate(normalize_set_num(str(i["id"])), float(i.get("score", 0)), "brickognize")
            for i in items[: self.max_candidates]
        ]


CLAUDE_MAX_IMAGE_SIDE = 1568  # larger images are downscaled by the API anyway

CLAUDE_SYSTEM = """\
You identify official LEGO sets from photos. The photos may show a partially \
assembled model, the box, building instructions, stickers or loose parts of one set.

Return up to 5 candidate sets, most likely first. Use Rebrickable set numbers \
with the variant suffix, e.g. "10696-1". If a set number is printed or readable \
anywhere in the photos, list that set first and say where you read it in \
"evidence"; otherwise name the features that point to the set. "confidence" is \
your probability from 0 to 1 that the candidate is correct. Return an empty \
list if the photos do not show LEGO."""

CLAUDE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "set_num": {"type": "string"},
                    "name": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["set_num", "name", "confidence", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


class ClaudeSetIdentifier:
    def __init__(self, model: str, client: anthropic.Anthropic):
        self.model = model
        self.client = client

    def identify(self, images: list[Path]) -> list[SetCandidate]:
        content = [image_block(p) for p in images]
        content.append({"type": "text", "text": "Which LEGO set is this?"})
        try:
            # On a safety decline, the API retries on Anthropic's recommended fallback model.
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=CLAUDE_SYSTEM,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": CLAUDE_SCHEMA},
                },
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.AuthenticationError as exc:
            raise SetIdentificationError("Claude rejected ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise SetIdentificationError("Claude is rate limited; try again later") from exc
        except anthropic.APIStatusError as exc:
            raise SetIdentificationError(f"Claude request failed: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise SetIdentificationError("Cannot reach the Claude API") from exc

        if response.stop_reason == "refusal":
            raise SetIdentificationError("Claude declined to identify these photos")
        if response.stop_reason == "max_tokens":
            raise SetIdentificationError("Claude's answer was cut off")
        text = next(b.text for b in response.content if b.type == "text")
        return [
            SetCandidate(
                normalize_set_num(c["set_num"]),
                min(max(float(c["confidence"]), 0.0), 1.0),
                "claude",
                c["evidence"] or None,
            )
            for c in json.loads(text)["candidates"]
        ]


def image_block(path: Path) -> dict:
    """JPEG-encode a photo for Claude, downscaled so phone photos stay small."""
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read image {path.name}")
    scale = min(1.0, CLAUDE_MAX_IMAGE_SIDE / max(image.shape[:2]))
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


@dataclass
class IdentificationResult:
    candidates: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # services that answered
    errors: list[str] = field(default_factory=list)


class SetIdentifier:
    """Ask Brickognize; ask Claude too when Brickognize is unsure, fails or finds nothing."""

    def __init__(
        self,
        brickognize: BrickognizeSetIdentifier,
        claude: ClaudeSetIdentifier | None,
        confidence_threshold: float,
    ):
        self.brickognize = brickognize
        self.claude = claude
        self.confidence_threshold = confidence_threshold

    def identify(self, conn: sqlite3.Connection, images: list[Path]) -> IdentificationResult:
        result = IdentificationResult()
        found = self._ask("brickognize", self.brickognize, images, result)
        known = merge_known(conn, found)
        if self.claude and (not known or known[0]["score"] < self.confidence_threshold):
            found += self._ask("claude", self.claude, images, result)
            found.sort(key=lambda c: c.score, reverse=True)
            known = merge_known(conn, found)
        result.candidates = known
        return result

    @staticmethod
    def _ask(name, identifier, images, result: IdentificationResult) -> list[SetCandidate]:
        try:
            candidates = identifier.identify(images)
        except SetIdentificationError as exc:
            result.errors.append(str(exc))
            return []
        result.sources.append(name)
        return candidates


def merge_known(conn: sqlite3.Connection, candidates: list[SetCandidate]) -> list[dict]:
    """One entry per set in the catalog, best score first; unknown set numbers are dropped."""
    merged: dict[str, dict] = {}
    for cand in candidates:
        if cand.set_num in merged:
            entry = merged[cand.set_num]
            entry["sources"].append(cand.source)
            entry["evidence"] = entry["evidence"] or cand.evidence
            continue
        row = get_set(conn, cand.set_num)
        if row is None:
            continue
        merged[cand.set_num] = {
            **dict(row),
            "score": cand.score,
            "sources": [cand.source],
            "evidence": cand.evidence,
        }
    return list(merged.values())
