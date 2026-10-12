import base64
import json
from types import SimpleNamespace

import anthropic
import cv2
import httpx
import numpy as np
import pytest

from app.services.set_identifier import (
    BrickognizeSetIdentifier,
    ClaudeSetIdentifier,
    SetCandidate,
    SetIdentificationError,
    SetIdentifier,
)


@pytest.fixture
def photos(tmp_path):
    paths = []
    for index, size in enumerate([(3000, 4000), (600, 800)]):
        path = tmp_path / f"photo{index}.jpg"
        cv2.imwrite(str(path), np.full((*size, 3), 200, np.uint8))
        paths.append(path)
    return paths


def brickognize(items=None, status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.content.count(b'name="query_image"'))
        return httpx.Response(status, json={"items": items or []})

    return BrickognizeSetIdentifier(
        "https://brickognize.test/predict/sets/",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class FakeClaude:
    """Stands in for anthropic.Anthropic: records the request, returns a canned answer."""

    def __init__(self, candidates=None, stop_reason="end_turn", error=None):
        self.requests = []
        self.candidates = candidates or []
        self.stop_reason = stop_reason
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        text = json.dumps({"candidates": self.candidates})
        return SimpleNamespace(
            stop_reason=self.stop_reason, content=[SimpleNamespace(type="text", text=text)]
        )


def claude_candidate(set_num, confidence=0.9, evidence="number on the box"):
    return {"set_num": set_num, "name": "x", "confidence": confidence, "evidence": evidence}


def test_brickognize_sends_all_photos_and_keeps_sets_only(photos):
    seen = []
    identifier = brickognize(
        [
            {"id": "5678", "name": "Car", "type": "set", "score": 0.3},
            {"id": "1234-1", "name": "House", "type": "set", "score": 0.8},
            {"id": "fig-1", "name": "Fig", "type": "fig", "score": 0.9},
        ],
        seen=seen,
    )
    assert identifier.identify(photos) == [
        SetCandidate("1234-1", 0.8, "brickognize"),
        SetCandidate("5678-1", 0.3, "brickognize"),
    ]
    assert seen == [2]


def test_brickognize_failure_raises(photos):
    with pytest.raises(SetIdentificationError):
        brickognize(status=503).identify(photos)


def test_claude_request_and_parsing(photos):
    client = FakeClaude([claude_candidate("1234", 1.4), claude_candidate("5678-1", 0.2, "")])
    candidates = ClaudeSetIdentifier("claude-opus-5-5", client).identify(photos)

    assert candidates == [
        SetCandidate("1234-1", 1.0, "claude", "number on the box"),
        SetCandidate("5678-1", 0.2, "claude", None),
    ]
    [request] = client.requests
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["output_config"]["format"]["type"] == "json_schema"
    images = [b for b in request["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 2
    decoded = cv2.imdecode(
        np.frombuffer(base64.b64decode(images[0]["source"]["data"]), np.uint8), cv2.IMREAD_COLOR
    )
    assert max(decoded.shape[:2]) == 1568  # 4000 px phone photo downscaled


def test_claude_refusal_raises(photos):
    with pytest.raises(SetIdentificationError, match="declined"):
        ClaudeSetIdentifier("m", FakeClaude(stop_reason="refusal")).identify(photos)


def test_claude_connection_error_raises(photos):
    error = anthropic.APIConnectionError(request=None)
    with pytest.raises(SetIdentificationError, match="reach"):
        ClaudeSetIdentifier("m", FakeClaude(error=error)).identify(photos)


def test_confident_brickognize_result_skips_claude(db, photos):
    claude = FakeClaude([claude_candidate("5678-1")])
    identifier = SetIdentifier(
        brickognize([{"id": "1234-1", "type": "set", "score": 0.8}]),
        ClaudeSetIdentifier("m", claude),
        confidence_threshold=0.5,
    )
    result = identifier.identify(db, photos)
    assert [c["set_num"] for c in result.candidates] == ["1234-1"]
    assert result.candidates[0]["name"] == "Test House"
    assert result.sources == ["brickognize"]
    assert claude.requests == []


def test_unsure_brickognize_asks_claude_and_merges(db, photos):
    claude = FakeClaude([claude_candidate("5678-1", 0.9), claude_candidate("1234-1", 0.4),
                         claude_candidate("9999-1", 0.8)])  # not in the catalog
    identifier = SetIdentifier(
        brickognize([{"id": "1234-1", "type": "set", "score": 0.3}]),
        ClaudeSetIdentifier("m", claude),
        confidence_threshold=0.5,
    )
    result = identifier.identify(db, photos)
    assert [(c["set_num"], c["score"], c["sources"]) for c in result.candidates] == [
        ("5678-1", 0.9, ["claude"]),
        ("1234-1", 0.4, ["claude", "brickognize"]),
    ]
    assert result.sources == ["brickognize", "claude"]


def test_unknown_brickognize_sets_count_as_nothing_found(db, photos):
    claude = FakeClaude([claude_candidate("5678-1")])
    identifier = SetIdentifier(
        brickognize([{"id": "9999-1", "type": "set", "score": 0.9}]),
        ClaudeSetIdentifier("m", claude),
        confidence_threshold=0.5,
    )
    assert [c["set_num"] for c in identifier.identify(db, photos).candidates] == ["5678-1"]


def test_brickognize_down_falls_back_to_claude(db, photos):
    identifier = SetIdentifier(
        brickognize(status=503), ClaudeSetIdentifier("m", FakeClaude([claude_candidate("1234")])), 0.5
    )
    result = identifier.identify(db, photos)
    assert [c["set_num"] for c in result.candidates] == ["1234-1"]
    assert result.sources == ["claude"]
    assert result.errors and "Brickognize" in result.errors[0]


def test_without_claude_only_brickognize_is_used(db, photos):
    identifier = SetIdentifier(brickognize(status=503), None, 0.5)
    result = identifier.identify(db, photos)
    assert result.candidates == [] and result.sources == [] and len(result.errors) == 1
