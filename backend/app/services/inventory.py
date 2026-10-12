"""Rebrickable catalog: CSV import and set inventory lookup."""

import csv
import gzip
import io
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REBRICKABLE_DOWNLOAD_URL = "https://cdn.rebrickable.com/media/downloads/{name}.csv.gz"

# CSV name -> (table, columns to import). Extra CSV columns are ignored so newer
# Rebrickable exports keep working.
CSV_TABLES: dict[str, tuple[str, list[str]]] = {
    "colors": ("colors", ["id", "name", "rgb", "is_trans"]),
    "parts": ("parts", ["part_num", "name", "part_cat_id"]),
    "sets": ("sets", ["set_num", "name", "year", "theme_id", "num_parts", "img_url"]),
    "inventories": ("inventories", ["id", "version", "set_num"]),
    "inventory_parts": (
        "inventory_parts",
        ["inventory_id", "part_num", "color_id", "quantity", "is_spare", "img_url"],
    ),
    "inventory_minifigs": ("inventory_minifigs", ["inventory_id", "fig_num", "quantity"]),
    "part_relationships": (
        "part_relationships",
        ["rel_type", "child_part_num", "parent_part_num"],
    ),
}
BOOL_COLUMNS = {"is_trans", "is_spare"}


@dataclass(frozen=True)
class InventoryItem:
    part_num: str
    color_id: int
    quantity: int


def normalize_set_num(set_num: str) -> str:
    """'10696' -> '10696-1'; set numbers in Rebrickable always carry a variant suffix."""
    set_num = set_num.strip()
    return set_num if "-" in set_num else f"{set_num}-1"


def _parse_bool(value: str) -> int:
    return 1 if value.strip().lower() in {"t", "true", "1"} else 0


def _open_csv(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(path), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def find_csv(directory: Path, name: str) -> Path | None:
    for candidate in (directory / f"{name}.csv", directory / f"{name}.csv.gz"):
        if candidate.exists():
            return candidate
    return None


def import_csv(conn: sqlite3.Connection, name: str, path: Path) -> int:
    """Replace the contents of one catalog table with the given CSV file."""
    table, columns = CSV_TABLES[name]
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    with _open_csv(path) as fh:
        reader = csv.DictReader(fh)
        rows = (
            tuple(
                _parse_bool(row[c]) if c in BOOL_COLUMNS else (row.get(c) or None)
                for c in columns
            )
            for row in reader
        )
        conn.execute(f"DELETE FROM {table}")
        cursor = conn.executemany(sql, rows)
    return cursor.rowcount


def import_directory(conn: sqlite3.Connection, directory: Path) -> dict[str, int]:
    """Import every known Rebrickable CSV found in `directory`."""
    counts = {}
    with conn:
        for name in CSV_TABLES:
            path = find_csv(directory, name)
            if path is not None:
                counts[name] = import_csv(conn, name, path)
    return counts


def _latest_inventory_id(conn: sqlite3.Connection, set_num: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM inventories WHERE set_num = ? ORDER BY version DESC LIMIT 1",
        (set_num,),
    ).fetchone()
    return row["id"] if row else None


def get_inventory(conn: sqlite3.Connection, set_num: str) -> list[InventoryItem]:
    """Non-spare parts of a set, including the parts of its minifigs."""
    inventory_id = _latest_inventory_id(conn, normalize_set_num(set_num))
    if inventory_id is None:
        return []

    totals: Counter[tuple[str, int]] = Counter()

    def add_parts(inv_id: int, multiplier: int) -> None:
        for row in conn.execute(
            "SELECT part_num, color_id, quantity FROM inventory_parts"
            " WHERE inventory_id = ? AND is_spare = 0",
            (inv_id,),
        ):
            totals[(row["part_num"], row["color_id"])] += row["quantity"] * multiplier

    add_parts(inventory_id, 1)
    for fig in conn.execute(
        "SELECT fig_num, quantity FROM inventory_minifigs WHERE inventory_id = ?",
        (inventory_id,),
    ).fetchall():
        fig_inventory_id = _latest_inventory_id(conn, fig["fig_num"])
        if fig_inventory_id is not None:
            add_parts(fig_inventory_id, fig["quantity"])

    return [
        InventoryItem(part_num, color_id, quantity)
        for (part_num, color_id), quantity in sorted(totals.items())
    ]


def equivalent_parts(conn: sqlite3.Connection, part_num: str) -> list[str]:
    """Part numbers that may be the same brick as `part_num`, the part itself first.

    Recognizers use BrickLink IDs, which sometimes differ from Rebrickable's (e.g.
    BrickLink 61678 is Rebrickable 11153). Rebrickable links them as mold variants
    or alternates; printed versions of any of these are included as well.
    """
    rows = conn.execute(
        """
        WITH RECURSIVE molds(p) AS (
            SELECT ?
            UNION
            SELECT r.parent_part_num FROM part_relationships r JOIN molds ON r.child_part_num = molds.p
             WHERE r.rel_type IN ('M', 'A')
            UNION
            SELECT r.child_part_num FROM part_relationships r JOIN molds ON r.parent_part_num = molds.p
             WHERE r.rel_type IN ('M', 'A')
        )
        SELECT p FROM molds
        UNION
        SELECT child_part_num FROM part_relationships
         WHERE rel_type = 'P' AND parent_part_num IN (SELECT p FROM molds)
        """,
        (part_num,),
    )
    return [part_num] + sorted({r[0] for r in rows} - {part_num})


def get_set(conn: sqlite3.Connection, set_num: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM sets WHERE set_num = ?", (normalize_set_num(set_num),)
    ).fetchone()


def search_sets(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    query = query.strip()
    like = f"%{query}%"
    # Exact set number matches ("10696" -> "10696-1") first, then newest sets.
    return conn.execute(
        "SELECT * FROM sets WHERE set_num LIKE ? OR name LIKE ?"
        " ORDER BY (set_num = ? OR set_num LIKE ?) DESC, year DESC, set_num LIMIT ?",
        (like, like, query, f"{query}-%", limit),
    ).fetchall()


def catalog_is_loaded(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM inventories LIMIT 1").fetchone() is not None
