import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_db, get_project_or_404
from app.services import inventory, matcher

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    set_num: str = Field(min_length=1)
    name: str | None = None


class PartCreate(BaseModel):
    part_num: str = Field(min_length=1)
    color_id: int
    quantity: int = Field(default=1, gt=0)
    source: Literal["build", "found"] = "build"


def lookup_part_names(conn: sqlite3.Connection, part_nums: set[str]) -> dict[str, str]:
    if not part_nums:
        return {}
    placeholders = ", ".join("?" for _ in part_nums)
    rows = conn.execute(
        f"SELECT part_num, name FROM parts WHERE part_num IN ({placeholders})",
        tuple(part_nums),
    )
    return {r["part_num"]: r["name"] for r in rows}


def lookup_colors(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {r["id"]: r for r in conn.execute("SELECT * FROM colors")}


def lookup_part_images(conn: sqlite3.Connection, set_num: str) -> dict[tuple[str, int], str]:
    """Rebrickable element images for the parts of a set (incl. minifig parts)."""
    rows = conn.execute(
        "SELECT part_num, color_id, img_url FROM inventory_parts WHERE img_url IS NOT NULL"
        " AND inventory_id IN (SELECT id FROM inventories WHERE set_num = ?"
        "   UNION SELECT i.id FROM inventories i JOIN inventory_minifigs m ON m.fig_num = i.set_num"
        "   WHERE m.inventory_id IN (SELECT id FROM inventories WHERE set_num = ?))",
        (set_num, set_num),
    )
    return {(r["part_num"], r["color_id"]): r["img_url"] for r in rows}


def serialize_status(conn: sqlite3.Connection, items: list[matcher.MissingItem], set_num: str):
    names = lookup_part_names(conn, {i.part_num for i in items})
    colors = lookup_colors(conn)
    images = lookup_part_images(conn, set_num)
    result = []
    for item in items:
        color = colors.get(item.color_id)
        result.append(
            {
                "part_num": item.part_num,
                "part_name": names.get(item.part_num, item.part_num),
                "color_id": item.color_id,
                "color_name": color["name"] if color else str(item.color_id),
                "color_rgb": color["rgb"] if color else None,
                "img_url": images.get((item.part_num, item.color_id)),
                "needed": item.needed,
                "present": item.present,
                "missing": item.missing,
            }
        )
    return result


def serialize_project(conn: sqlite3.Connection, project: sqlite3.Row) -> dict:
    status = matcher.inventory_status(conn, project["id"], project["set_num"])
    set_row = inventory.get_set(conn, project["set_num"])
    pending = conn.execute(
        "SELECT COUNT(*) FROM detections d JOIN photos p ON p.id = d.photo_id"
        " WHERE p.project_id = ? AND d.status = 'pending'",
        (project["id"],),
    ).fetchone()[0]
    return {
        **dict(project),
        "set": dict(set_row) if set_row else None,
        "parts_needed": sum(i.needed for i in status),
        "parts_present": sum(min(i.present, i.needed) for i in status),
        "parts_missing": sum(i.missing for i in status),
        "pending_reviews": pending,
    }


@router.get("")
def list_projects(conn: sqlite3.Connection = Depends(get_db)):
    projects = conn.execute("SELECT * FROM projects ORDER BY created_at DESC, id DESC").fetchall()
    return [serialize_project(conn, p) for p in projects]


@router.post("", status_code=201)
def create_project(body: ProjectCreate, conn: sqlite3.Connection = Depends(get_db)):
    set_num = inventory.normalize_set_num(body.set_num)
    set_row = inventory.get_set(conn, set_num)
    if set_row is None or not inventory.get_inventory(conn, set_num):
        raise HTTPException(
            422, f"Set {set_num} has no inventory. Is the Rebrickable catalog imported?"
        )
    with conn:
        cursor = conn.execute(
            "INSERT INTO projects (name, set_num) VALUES (?, ?)",
            (body.name or set_row["name"], set_num),
        )
    return serialize_project(conn, get_project_or_404(conn, cursor.lastrowid))


@router.get("/{project_id}")
def get_project(project_id: int, conn: sqlite3.Connection = Depends(get_db)):
    return serialize_project(conn, get_project_or_404(conn, project_id))


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, conn: sqlite3.Connection = Depends(get_db)):
    get_project_or_404(conn, project_id)
    with conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


@router.get("/{project_id}/inventory")
def project_inventory(project_id: int, conn: sqlite3.Connection = Depends(get_db)):
    project = get_project_or_404(conn, project_id)
    items = matcher.inventory_status(conn, project_id, project["set_num"])
    return serialize_status(conn, items, project["set_num"])


@router.get("/{project_id}/missing")
def project_missing(project_id: int, conn: sqlite3.Connection = Depends(get_db)):
    project = get_project_or_404(conn, project_id)
    items = matcher.missing_parts(conn, project_id, project["set_num"])
    return serialize_status(conn, items, project["set_num"])


@router.get("/{project_id}/parts")
def list_parts(project_id: int, conn: sqlite3.Connection = Depends(get_db)):
    get_project_or_404(conn, project_id)
    rows = conn.execute(
        "SELECT * FROM project_parts WHERE project_id = ? ORDER BY id DESC", (project_id,)
    )
    return [dict(r) for r in rows]


@router.post("/{project_id}/parts", status_code=201)
def add_part(project_id: int, body: PartCreate, conn: sqlite3.Connection = Depends(get_db)):
    get_project_or_404(conn, project_id)
    with conn:
        cursor = conn.execute(
            "INSERT INTO project_parts (project_id, part_num, color_id, quantity, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (project_id, body.part_num, body.color_id, body.quantity, body.source),
        )
    row = conn.execute("SELECT * FROM project_parts WHERE id = ?", (cursor.lastrowid,))
    return dict(row.fetchone())


@router.delete("/{project_id}/parts/{part_id}", status_code=204)
def delete_part(project_id: int, part_id: int, conn: sqlite3.Connection = Depends(get_db)):
    with conn:
        cursor = conn.execute(
            "DELETE FROM project_parts WHERE id = ? AND project_id = ?", (part_id, project_id)
        )
    if cursor.rowcount == 0:
        raise HTTPException(404, "Part not found")
