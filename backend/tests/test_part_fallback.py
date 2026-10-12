import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.recognizer import Candidate, ClaudePartRecognizer, RecognitionError
from tests.images import RED, make_photo
from tests.test_set_identifier import FakeClaude

SET_PARTS = {"3001": "Brick 2 x 4", "3003": "Brick 2 x 2"}


@pytest.fixture
def crop(tmp_path):
    path = tmp_path / "crop.jpg"
    cv2.imwrite(str(path), np.full((200, 300, 3), 180, np.uint8))
    return path


def test_claude_picks_from_the_set_parts_and_list_is_cached(crop):
    client = FakeClaude(
        [
            {"part_num": "3003", "confidence": 0.8, "reason": "2x2 studs"},
            {"part_num": "9999", "confidence": 0.5, "reason": "not in the list"},
        ]
    )
    candidates = ClaudePartRecognizer("claude-opus-5-5", client).recognize(crop, SET_PARTS)
    assert candidates == [Candidate("3003", "Brick 2 x 2", 0.8, source="claude", reason="2x2 studs")]
    [request] = client.requests
    parts_block = request["messages"][0]["content"][0]
    assert "3001\tBrick 2 x 4" in parts_block["text"]
    assert parts_block["cache_control"] == {"type": "ephemeral"}


def test_claude_failure_becomes_recognition_error(crop):
    with pytest.raises(RecognitionError):
        ClaudePartRecognizer("m", FakeClaude(stop_reason="refusal")).recognize(crop, SET_PARTS)


class FakeFallback:
    def __init__(self):
        self.calls = []

    def recognize(self, image_path, set_parts):
        self.calls.append(set_parts)
        return [Candidate("3003", set_parts["3003"], 0.9, source="claude", reason="looks 2x2")]


@pytest.fixture
def fallback_client(settings, db, recognizer, set_identifier, rebrickable):
    fallback = FakeFallback()
    app = create_app(settings, recognizer=recognizer, set_identifier=set_identifier,
                     rebrickable=rebrickable, part_fallback=fallback)
    client = TestClient(app)
    project = client.post("/api/projects", json={"set_num": "1234"}).json()
    return client, project, fallback


def upload(client, project_id, tmp_path):
    photo = make_photo(tmp_path / "p.jpg", [(40, 40, 1500, 1100, RED)])
    with open(photo, "rb") as fh:
        return client.post(f"/api/projects/{project_id}/photos",
                           files={"file": ("p.jpg", fh, "image/jpeg")}, data={"kind": "pile"})


def test_confident_brickognize_does_not_ask_claude(fallback_client, tmp_path):
    client, project, fallback = fallback_client
    detection = upload(client, project["id"], tmp_path).json()["detections"][0]
    assert fallback.calls == []
    assert detection["candidates"][0]["source"] == "brickognize"


def test_unsure_brickognize_asks_claude_with_set_parts(fallback_client, brickognize, tmp_path):
    client, project, fallback = fallback_client
    brickognize["items"] = [{"id": "3001", "name": "Brick 2 x 4", "type": "part", "score": 0.2}]
    detection = upload(client, project["id"], tmp_path).json()["detections"][0]
    assert set(fallback.calls[0]) == {"3001", "3003", "973c01"}
    assert [(c["part_num"], c["source"]) for c in detection["candidates"]] == [
        ("3003", "claude"),
        ("3001", "brickognize"),
    ]
    assert detection["suggestion"]["part_num"] == "3003"


def test_brickognize_down_claude_answers(fallback_client, brickognize, tmp_path):
    client, project, fallback = fallback_client
    brickognize["status"] = 503
    detection = upload(client, project["id"], tmp_path).json()["detections"][0]
    assert detection["error"] is None
    assert detection["candidates"][0]["part_num"] == "3003"
