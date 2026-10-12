import sqlite3
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_db, get_set_identifier
from app.services import inventory
from app.services.set_identifier import SetIdentifier

MAX_IDENTIFY_PHOTOS = 3

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/status")
def status(conn: sqlite3.Connection = Depends(get_db)):
    return {"catalog_loaded": inventory.catalog_is_loaded(conn)}


@router.get("/sets")
def search_sets(q: str, conn: sqlite3.Connection = Depends(get_db)):
    return [dict(row) for row in inventory.search_sets(conn, q)]


@router.post("/sets/identify")
def identify_set(
    files: list[UploadFile] = File(...),
    conn: sqlite3.Connection = Depends(get_db),
    identifier: SetIdentifier = Depends(get_set_identifier),
):
    """Suggest sets from 1-3 photos of a build, box or instructions. Photos are not kept."""
    if len(files) > MAX_IDENTIFY_PHOTOS:
        raise HTTPException(422, f"Send at most {MAX_IDENTIFY_PHOTOS} photos")
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for index, upload in enumerate(files):
            suffix = Path(upload.filename or "").suffix.lower() or ".jpg"
            path = Path(tmp) / f"{index}{suffix}"
            path.write_bytes(upload.file.read())
            paths.append(path)
        try:
            result = identifier.identify(conn, paths)
        except ValueError as exc:  # unreadable image
            raise HTTPException(415, str(exc)) from exc
    return {"candidates": result.candidates, "sources": result.sources, "errors": result.errors}


@router.get("/sets/{set_num}")
def get_set(set_num: str, conn: sqlite3.Connection = Depends(get_db)):
    row = inventory.get_set(conn, set_num)
    if row is None:
        raise HTTPException(404, "Set not found")
    return dict(row)


@router.get("/colors")
def list_colors(conn: sqlite3.Connection = Depends(get_db)):
    return [dict(row) for row in conn.execute("SELECT * FROM colors ORDER BY name")]
