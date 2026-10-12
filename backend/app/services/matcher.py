"""Diff a set inventory against the parts known to be present."""

import sqlite3
from dataclasses import dataclass

from app.services.inventory import get_inventory


@dataclass(frozen=True)
class MissingItem:
    part_num: str
    color_id: int
    needed: int
    present: int

    @property
    def missing(self) -> int:
        return max(self.needed - self.present, 0)


def present_parts(conn: sqlite3.Connection, project_id: int) -> dict[tuple[str, int], int]:
    rows = conn.execute(
        "SELECT part_num, color_id, SUM(quantity) AS quantity FROM project_parts"
        " WHERE project_id = ? GROUP BY part_num, color_id",
        (project_id,),
    )
    return {(r["part_num"], r["color_id"]): r["quantity"] for r in rows}


def inventory_status(conn: sqlite3.Connection, project_id: int, set_num: str) -> list[MissingItem]:
    """Every inventory line with how many are needed and how many are present."""
    present = present_parts(conn, project_id)
    return [
        MissingItem(
            part_num=item.part_num,
            color_id=item.color_id,
            needed=item.quantity,
            present=present.get((item.part_num, item.color_id), 0),
        )
        for item in get_inventory(conn, set_num)
    ]


def missing_parts(conn: sqlite3.Connection, project_id: int, set_num: str) -> list[MissingItem]:
    return [i for i in inventory_status(conn, project_id, set_num) if i.missing > 0]
