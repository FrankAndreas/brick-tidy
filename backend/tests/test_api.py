import io
import tempfile
from pathlib import Path

import pytest

from tests.images import BLUE, RED, YELLOW, make_photo


@pytest.fixture
def project(client) -> dict:
    response = client.post("/api/projects", json={"set_num": "1234"})
    assert response.status_code == 201
    return response.json()


def upload(client, project_id: int, kind: str = "build", image: bytes | None = None):
    if image is None:
        image = make_photo_bytes([(40, 40, 1500, 1100, RED)])
    return client.post(
        f"/api/projects/{project_id}/photos",
        files={"file": ("brick.jpg", io.BytesIO(image), "image/jpeg")},
        data={"kind": kind},
    )


def make_photo_bytes(bricks) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        return make_photo(Path(tmp) / "photo.jpg", bricks).read_bytes()


def missing_map(client, project_id: int) -> dict:
    return {
        (m["part_num"], m["color_id"]): m["missing"]
        for m in client.get(f"/api/projects/{project_id}/missing").json()
    }


def test_status_and_set_search(client):
    assert client.get("/api/status").json() == {"catalog_loaded": True}
    assert [s["set_num"] for s in client.get("/api/sets", params={"q": "house"}).json()] == [
        "1234-1"
    ]
    assert client.get("/api/sets/nope").status_code == 404
    assert [c["name"] for c in client.get("/api/colors").json()] == [
        "Black",
        "Light Bluish Gray",
        "Red",
    ]


def test_create_project_defaults_name_and_counts(project):
    assert project["set_num"] == "1234-1"
    assert project["name"] == "Test House"
    assert project["parts_needed"] == 9
    assert project["parts_missing"] == 9


def test_create_project_for_unknown_set_fails(client):
    assert client.post("/api/projects", json={"set_num": "9999"}).status_code == 422


def test_manual_parts_reduce_missing_list(client, project):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/parts", json={"part_num": "3003", "color_id": 0, "quantity": 2})
    part = client.post(
        f"/api/projects/{pid}/parts", json={"part_num": "3001", "color_id": 4, "quantity": 3}
    ).json()

    missing = client.get(f"/api/projects/{pid}/missing").json()
    assert {(m["part_num"], m["color_id"]): m["missing"] for m in missing} == {
        ("3001", 4): 1,
        ("3001", 71): 1,
        ("973c01", 4): 2,
    }
    red_brick = next(m for m in missing if m["color_id"] == 4 and m["part_num"] == "3001")
    assert red_brick["part_name"] == "Brick 2 x 4"
    assert red_brick["color_name"] == "Red"
    assert red_brick["img_url"] == "https://example.com/3001-4.jpg"

    assert client.delete(f"/api/projects/{pid}/parts/{part['id']}").status_code == 204
    assert missing_map(client, pid)[("3001", 4)] == 4


def test_extra_parts_do_not_go_negative(client, project):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/parts", json={"part_num": "3003", "color_id": 0, "quantity": 5})
    assert ("3003", 0) not in missing_map(client, pid)
    assert client.get(f"/api/projects/{pid}").json()["parts_present"] == 2


def test_upload_recognizes_and_suggests_in_set_candidate(client, project, settings):
    image = make_photo_bytes([(40, 40, 1500, 1100, RED)])
    response = upload(client, project["id"], image=image)
    assert response.status_code == 201
    photo = response.json()
    assert (settings.data_dir / photo["url"].removeprefix("/media/")).read_bytes() == image

    [detection] = photo["detections"]
    assert detection["status"] == "pending"
    assert detection["low_confidence"] is False
    # Sorted by score, minifig results dropped.
    assert [c["part_num"] for c in detection["candidates"]] == ["3001", "3002"]
    assert [c["in_set"] for c in detection["candidates"]] == [True, False]
    assert {c["color_name"] for c in detection["candidates"][0]["set_colors"]} == {
        "Red",
        "Light Bluish Gray",
    }
    # Red has the most bricks missing.
    assert detection["suggestion"] == {"part_num": "3001", "color_id": 4}
    assert detection["bbox"] is None  # close-up: the whole photo is the crop
    assert client.get(detection["crop_url"]).content == image


def test_upload_with_several_bricks_creates_one_detection_per_crop(client, project, settings):
    image = make_photo_bytes(
        [(100, 100, 240, 120, RED), (800, 150, 120, 120, BLUE), (400, 800, 360, 120, YELLOW)]
    )
    photo = upload(client, project["id"], kind="pile", image=image).json()
    detections = photo["detections"]
    assert len(detections) == 3
    assert all(len(d["bbox"]) == 4 for d in detections)
    assert len({d["crop_url"] for d in detections}) == 3
    for d in detections:
        assert d["crop_url"].startswith(f"/media/crops/{project['id']}/")
        assert client.get(d["crop_url"]).status_code == 200

    # Newest photo first, crops in reading order.
    later = upload(client, project["id"]).json()["detections"][0]
    listed = client.get(f"/api/projects/{project['id']}/detections").json()
    assert [d["id"] for d in listed] == [later["id"]] + [d["id"] for d in detections]


def test_rejects_unreadable_image(client, project, settings):
    response = upload(client, project["id"], image=b"not really a jpeg")
    assert response.status_code == 415
    assert not any((settings.photos_dir / str(project["id"])).iterdir())
    assert client.get(f"/api/projects/{project['id']}/detections").json() == []


def test_bricklink_id_is_mapped_to_set_part_via_mold_relationship(client, project, brickognize):
    brickognize["items"] = [{"id": "3003a", "name": "Brick 2 x 2 (old mold)", "score": 0.8}]
    [candidate] = upload(client, project["id"]).json()["detections"][0]["candidates"]
    assert candidate["in_set"] is True
    assert [(c["part_num"], c["color_name"]) for c in candidate["set_colors"]] == [("3003", "Black")]


def test_suggestion_maps_to_printed_set_part(client, project, brickognize):
    brickognize["items"] = [{"id": "973", "name": "Torso", "score": 0.8}]
    detection = upload(client, project["id"]).json()["detections"][0]
    assert detection["suggestion"] == {"part_num": "973c01", "color_id": 4}


def test_confirm_build_detection_then_reject(client, project):
    pid = project["id"]
    detection = upload(client, pid).json()["detections"][0]

    confirmed = client.post(
        f"/api/detections/{detection['id']}/confirm",
        json={"part_num": "3001", "color_id": 4, "quantity": 2},
    ).json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed"] == {"part_num": "3001", "color_id": 4, "quantity": 2}
    assert missing_map(client, pid)[("3001", 4)] == 2

    # Re-confirming replaces rather than adds.
    client.post(f"/api/detections/{detection['id']}/confirm", json={"part_num": "3001", "color_id": 4})
    assert missing_map(client, pid)[("3001", 4)] == 3
    assert client.get(f"/api/projects/{pid}/parts").json()[0]["source"] == "build"

    rejected = client.post(f"/api/detections/{detection['id']}/reject").json()
    assert rejected["status"] == "rejected"
    assert missing_map(client, pid)[("3001", 4)] == 4


def test_pile_detection_is_recorded_as_found(client, project):
    pid = project["id"]
    detection = upload(client, pid, kind="pile").json()["detections"][0]
    client.post(f"/api/detections/{detection['id']}/confirm", json={"part_num": "3001", "color_id": 71})
    assert client.get(f"/api/projects/{pid}/parts").json()[0]["source"] == "found"
    assert ("3001", 71) not in missing_map(client, pid)


def test_list_pending_detections(client, project):
    pid = project["id"]
    first = upload(client, pid).json()["detections"][0]
    upload(client, pid)
    client.post(f"/api/detections/{first['id']}/reject")
    assert len(client.get(f"/api/projects/{pid}/detections", params={"status": "pending"}).json()) == 1
    assert client.get(f"/api/projects/{pid}").json()["pending_reviews"] == 1


def test_recognizer_failure_still_creates_reviewable_detection(client, project, brickognize):
    brickognize["status"] = 503
    detection = upload(client, project["id"]).json()["detections"][0]
    assert detection["candidates"] == []
    assert detection["error"]
    assert detection["low_confidence"] is True


def test_rejects_non_image_upload(client, project):
    response = client.post(
        f"/api/projects/{project['id']}/photos",
        files={"file": ("notes.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert response.status_code == 415


def test_delete_project(client, project):
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
