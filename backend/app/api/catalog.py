import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_db
from app.services import inventory

router = APIRouter(prefix="/api", tags=["catalog"])


@router.get("/status")
def status(conn: sqlite3.Connection = Depends(get_db)):
    return {"catalog_loaded": inventory.catalog_is_loaded(conn)}


@router.get("/sets")
def search_sets(q: str, conn: sqlite3.Connection = Depends(get_db)):
    return [dict(row) for row in inventory.search_sets(conn, q)]


@router.get("/sets/{set_num}")
def get_set(set_num: str, conn: sqlite3.Connection = Depends(get_db)):
    row = inventory.get_set(conn, set_num)
    if row is None:
        raise HTTPException(404, "Set not found")
    return dict(row)


@router.get("/colors")
def list_colors(conn: sqlite3.Connection = Depends(get_db)):
    return [dict(row) for row in conn.execute("SELECT * FROM colors ORDER BY name")]
