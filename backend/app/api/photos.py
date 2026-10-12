import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import (
    get_db,
    get_part_fallback,
    get_project_or_404,
    get_rebrickable,
    get_recognizer,
    get_settings,
)
from app.api.projects import lookup_colors, lookup_part_names
from app.config import Settings
from app.services import detector, matcher
from app.services.colors import MATCH_DISTANCE, color_distance, measure_color
from app.services.inventory import catalog_aliases, equivalent_parts
from app.services.rebrickable import RebrickableClient
from app.services.recognizer import BrickognizeRecognizer, ClaudePartRecognizer, RecognitionError

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

    A candidate matches its Rebrickable aliases (resolved at upload) or an
    equivalent part (mold variant, alternate, print), so a BrickLink ID still
    finds the Rebrickable part of the set. When the brick's color was measured, each line gets a color distance and
    lines are ordered by it. The suggestion is the best-scoring candidate in the
    set, in the closest color (without a measurement: the color with the most
    bricks missing). A detection is "wanted" when that part is still missing in a
    color that matches the photo (any color without a measurement).
    """
    colors = lookup_colors(conn)
    measured = detection["color"]
    candidates = []
    suggestion = None
    wanted = False
    for cand in json.loads(detection["candidates"]):
        # Candidates stored before aliases existed use their own ID.
        exact = cand.get("aliases") or [cand["part_num"]]
        part_nums = list(dict.fromkeys(p for a in exact for p in equivalent_parts(conn, a)))
        set_colors = []
        for part_num in part_nums:
            for item in status_by_part.get(part_num, []):
                color = colors.get(item.color_id)
                rgb = color["rgb"] if color else None
                set_colors.append(
                    {
                        "part_num": item.part_num,
                        "color_id": item.color_id,
                        "color_name": color["name"] if color else "",
                        "color_rgb": rgb,
                        "needed": item.needed,
                        "missing": item.missing,
                        "distance": round(color_distance(measured, rgb), 1)
                        if measured and rgb
                        else None,
                    }
                )
                distance = set_colors[-1]["distance"]
                set_colors[-1]["matches_photo"] = distance is not None and distance <= MATCH_DISTANCE
        if measured:
            # Matching colors first; among them the recognized part before equivalents.
            set_colors.sort(
                key=lambda c: (
                    not c["matches_photo"],
                    c["part_num"] not in exact,
                    c["distance"] if c["distance"] is not None else 1e9,
                )
            )
        candidates.append({**cand, "in_set": bool(set_colors), "set_colors": set_colors})
        if suggestion is None and set_colors:
            if measured:
                best = set_colors[0]
                wanted = any(c["missing"] > 0 and c["matches_photo"] for c in set_colors)
            else:
                best = max(
                    set_colors,
                    key=lambda c: (c["missing"] > 0, c["part_num"] in exact, c["missing"]),
                )
                wanted = best["missing"] > 0
            suggestion = {"part_num": best["part_num"], "color_id": best["color_id"]}

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
        "color": measured,
        "confirmed": dict(confirmed) if confirmed else None,
    }


def project_part_names(conn: sqlite3.Connection, project: sqlite3.Row) -> dict[str, str]:
    """Part number -> name for every part in the project's set."""
    part_nums = {i.part_num for i in matcher.inventory_status(conn, project["id"], project["set_num"])}
    names = lookup_part_names(conn, part_nums)
    return {num: names.get(num, num) for num in part_nums}


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
    rebrickable: RebrickableClient | None = Depends(get_rebrickable),
    part_fallback: ClaudePartRecognizer | None = Depends(get_part_fallback),
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

    set_parts = project_part_names(conn, project) if part_fallback else {}

    def recognize(crop: detector.Crop) -> tuple[list[dict], str | None, str | None]:
        color = measure_color(crop.path)
        candidates, error = [], None
        try:
            candidates = recognizer.recognize(crop.path)
        except RecognitionError as exc:
            error = str(exc)
        if part_fallback and (not candidates or candidates[0].score < settings.confidence_threshold):
            # Brickognize failed or is unsure: Claude picks from the set's parts.
            try:
                candidates = part_fallback.recognize(crop.path, set_parts) + candidates
                error = None
            except RecognitionError as exc:
                error = f"{error}; {exc}" if error else str(exc)
        return [c.to_dict() for c in candidates], error, color

    # Brickognize takes about a second per crop; a few requests in parallel.
    # The brick's color is measured too, since Brickognize does not report it.
    with ThreadPoolExecutor(max_workers=RECOGNITION_WORKERS) as pool:
        results = list(pool.map(recognize, crops))

    # Map recognizer (BrickLink) IDs to Rebrickable part numbers once, here.
    for candidates, _, _ in results:
        for cand in candidates:
            cand["aliases"] = catalog_aliases(conn, cand["part_num"])
            if not cand["aliases"] and rebrickable:
                cand["aliases"] = rebrickable.parts_for_bricklink_id(conn, cand["part_num"])

    with conn:
        photo_id = conn.execute(
            "INSERT INTO photos (project_id, kind, path) VALUES (?, ?, ?)",
            (project_id, kind, relative(settings, photo_path)),
        ).lastrowid
        conn.executemany(
            "INSERT INTO detections (photo_id, crop_path, bbox, candidates, error, color)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    photo_id,
                    relative(settings, crop.path),
                    json.dumps(crop.bbox) if crop.bbox else None,
                    json.dumps(candidates),
                    error,
                    color,
                )
                for crop, (candidates, error, color) in zip(crops, results)
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
