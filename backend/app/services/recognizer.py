"""Part recognition via the Brickognize API."""

from dataclasses import asdict, dataclass
from pathlib import Path

import httpx


class RecognitionError(Exception):
    pass


@dataclass(frozen=True)
class Candidate:
    part_num: str
    name: str
    score: float
    img_url: str | None = None

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
                    files={"query_image": (image_path.name, fh, _content_type(image_path))},
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


def _content_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/jpeg")
