import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import get_db, get_project_or_404, get_recognizer, get_settings
from app.api.projects import lookup_colors
from app.config import Settings
from app.services import detector, matcher
from app.services.inventory import equivalent_parts
from app.services.recognizer import BrickognizeRecognizer, RecognitionError

router = APIRouter(prefix="/api", tags=["photos"])

ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
RECOGNITION_WORKERS = 4


class DetectionConfirm(BaseModel):
    part_num: str = Field(min_length=1)
    color_id: int
    quantity: int = Field(default=1, gt=0)


def media_url(path: str) -> str:
    """Paths are stored relative to DATA_DIR; photos/ and crops/ are mounted under /media."""
    return "/media/" + path


def relative(settings: Settings, path: Path) -> str:
    return path.relative_to(settings.data_dir).as_posix()


def serialize_detection(
    conn: sqlite3.Connection,
    settings: Settings,
    detection: sqlite3.Row,
    status_by_part: dict[str, list[matcher.MissingItem]],
) -> dict:
    """Detection plus, per candidate, the set's inventory lines it may match.

    A candidate matches its own part number or an equivalent one (mold variant,
    alternate, print), so a BrickLink ID still finds the Rebrickable part of the
    set. The suggestion is the best-scoring candidate that matches the set, in the
    color with the most bricks still missing; the user confirms or corrects it.
    A detection is "wanted" when its suggested part is still missing in some color.
    """
    colors = lookup_colors(conn)
    candidates = []
    suggestion = None
    wanted = False
    for cand in json.loads(detection["candidates"]):
        set_colors = [
            {
                "part_num": item.part_num,
                "color_id": item.color_id,
                "color_name": colors[item.color_id]["name"] if item.color_id in colors else "",
                "color_rgb": colors[item.color_id]["rgb"] if item.color_id in colors else None,
                "needed": item.needed,
                "missing": item.missing,
            }
            for part_num in equivalent_parts(conn, cand["part_num"])
            for item in status_by_part.get(part_num, [])
        ]
        candidates.append({**cand, "in_set": bool(set_colors), "set_colors": set_colors})
        if suggestion is None and set_colors:
            best = max(
                set_colors,
                key=lambda c: (c["missing"] > 0, c["part_num"] == cand["part_num"], c["missing"]),
            )
            suggestion = {"part_num": best["part_num"], "color_id": best["color_id"]}
            wanted = best["missing"] > 0

    confirmed = conn.execute(
        "SELECT part_num, color_id, quantity FROM project_parts WHERE detection_id = ?",
        (detection["id"],),
    ).fetchone()
    return {
        "id": detection["id"],
        "photo_id": detection["photo_id"],
        "kind": detection["kind"],
        "crop_url": media_url(detection["crop_path"]),
        "bbox": json.loads(detection["bbox"]) if detection["bbox"] else None,
        "status": detection["status"],
        "error": detection["error"],
        "low_confidence": not candidates
        or candidates[0]["score"] < settings.confidence_threshold,
        "candidates": candidates,
        "suggestion": suggestion,
        "wanted": wanted,
        "confirmed": dict(confirmed) if confirmed else None,
    }


def status_by_part(conn: sqlite3.Connection, project: sqlite3.Row) -> dict[str, list]:
    grouped: dict[str, list[matcher.MissingItem]] = {}
    for item in matcher.inventory_status(conn, project["id"], project["set_num"]):
        grouped.setdefault(item.part_num, []).append(item)
    return grouped


DETECTION_SELECT = (
    "SELECT d.*, p.kind, p.project_id FROM detections d JOIN photos p ON p.id = d.photo_id"
)


@router.post("/projects/{project_id}/photos", status_code=201)
def upload_photo(
    project_id: int,
    file: UploadFile = File(...),
    kind: Literal["build", "pile"] = Form("build"),
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
    recognizer: BrickognizeRecognizer = Depends(get_recognizer),
):
    project = get_project_or_404(conn, project_id)
    suffix = Path(file.filename or "").suffix.lower() or ".jpg"
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"Unsupported image type {suffix}")

    photo_dir = settings.photos_dir / str(project_id)
    photo_dir.mkdir(parents=True, exist_ok=True)
    photo_path = photo_dir / f"{uuid.uuid4().hex}{suffix}"
    photo_path.write_bytes(file.file.read())

    try:
        crops = detector.detect(photo_path, settings.crops_dir / str(project_id))
    except ValueError as exc:
        photo_path.unlink()
        raise HTTPException(415, str(exc)) from exc

    def recognize(crop: detector.Crop) -> tuple[list[dict], str | None]:
        try:
            return [c.to_dict() for c in recognizer.recognize(crop.path)], None
        except RecognitionError as exc:
            return [], str(exc)

    # Brickognize takes about a second per crop; a few requests in parallel.
    with ThreadPoolExecutor(max_workers=RECOGNITION_WORKERS) as pool:
        results = list(pool.map(recognize, crops))

    with conn:
        photo_id = conn.execute(
            "INSERT INTO photos (project_id, kind, path) VALUES (?, ?, ?)",
            (project_id, kind, relative(settings, photo_path)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO detections (photo_id, crop_path, bbox, candidates, error)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (
                    photo_id,
                    relative(settings, crop.path),
                    json.dumps(crop.bbox) if crop.bbox else None,
                    json.dumps(candidates),
                    error,
                )
                for crop, (candidates, error) in zip(crops, results)
            ],
        )

    grouped = status_by_part(conn, project)
    detections = conn.execute(DETECTION_SELECT + " WHERE d.photo_id = ?", (photo_id,))
    return {
        "id": photo_id,
        "kind": kind,
        "url": media_url(relative(settings, photo_path)),
        "detections": [serialize_detection(conn, settings, d, grouped) for d in detections],
    }


@router.get("/projects/{project_id}/detections")
def list_detections(
    project_id: int,
    status: Literal["pending", "confirmed", "rejected"] | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    project = get_project_or_404(conn, project_id)
    sql = DETECTION_SELECT + " WHERE p.project_id = ?"
    params: list = [project_id]
    if status:
        sql += " AND d.status = ?"
        params.append(status)
    grouped = status_by_part(conn, project)
    # Newest photo first; its crops in reading order.
    rows = conn.execute(sql + " ORDER BY d.photo_id DESC, d.id", params).fetchall()
    return [serialize_detection(conn, settings, d, grouped) for d in rows]


@router.get("/projects/{project_id}/photos")
def list_photos(
    project_id: int,
    kind: Literal["build", "pile"] | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Photos, newest first, each with its detections for drawing boxes on the photo."""
    project = get_project_or_404(conn, project_id)
    sql = "SELECT * FROM photos WHERE project_id = ?"
    params: list = [project_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    photos = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
    grouped = status_by_part(conn, project)
    result = []
    for photo in photos:
        rows = conn.execute(DETECTION_SELECT + " WHERE d.photo_id = ? ORDER BY d.id", (photo["id"],))
        detections = [serialize_detection(conn, settings, d, grouped) for d in rows]
        result.append(
            {
                "id": photo["id"],
                "kind": photo["kind"],
                "url": media_url(photo["path"]),
                "created_at": photo["created_at"],
                "detections": detections,
                "wanted": sum(d["wanted"] and d["status"] == "pending" for d in detections),
            }
        )
    return result


def get_detection_or_404(conn: sqlite3.Connection, detection_id: int) -> sqlite3.Row:
    row = conn.execute(DETECTION_SELECT + " WHERE d.id = ?", (detection_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Detection not found")
    return row


def _respond(conn, settings, detection_id: int) -> dict:
    detection = get_detection_or_404(conn, detection_id)
    project = get_project_or_404(conn, detection["project_id"])
    return serialize_detection(conn, settings, detection, status_by_part(conn, project))


@router.post("/detections/{detection_id}/confirm")
def confirm_detection(
    detection_id: int,
    body: DetectionConfirm,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Record the brick as present: part of the build, or found in a pile."""
    detection = get_detection_or_404(conn, detection_id)
    source = "build" if detection["kind"] == "build" else "found"
    with conn:
        conn.execute("DELETE FROM project_parts WHERE detection_id = ?", (detection_id,))
        conn.execute(
            "INSERT INTO project_parts"
            " (project_id, part_num, color_id, quantity, source, detection_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                detection["project_id"],
                body.part_num,
                body.color_id,
                body.quantity,
                source,
                detection_id,
            ),
        )
        conn.execute("UPDATE detections SET status = 'confirmed' WHERE id = ?", (detection_id,))
    return _respond(conn, settings, detection_id)


@router.post("/detections/{detection_id}/reject")
def reject_detection(
    detection_id: int,
    conn: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    get_detection_or_404(conn, detection_id)
    with conn:
        conn.execute("DELETE FROM project_parts WHERE detection_id = ?", (detection_id,))
        conn.execute("UPDATE detections SET status = 'rejected' WHERE id = ?", (detection_id,))
    return _respond(conn, settings, detection_id)
