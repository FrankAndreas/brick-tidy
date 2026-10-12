import sqlite3
from pathlib import Path

SCHEMA = """
-- Rebrickable catalog (imported from CSV downloads)
CREATE TABLE IF NOT EXISTS colors (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    rgb       TEXT,
    is_trans  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS parts (
    part_num    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    part_cat_id INTEGER
);
CREATE TABLE IF NOT EXISTS sets (
    set_num   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    year      INTEGER,
    theme_id  INTEGER,
    num_parts INTEGER,
    img_url   TEXT
);
CREATE TABLE IF NOT EXISTS inventories (
    id      INTEGER PRIMARY KEY,
    version INTEGER NOT NULL,
    set_num TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inventories_set ON inventories(set_num);
CREATE TABLE IF NOT EXISTS inventory_parts (
    inventory_id INTEGER NOT NULL,
    part_num     TEXT NOT NULL,
    color_id     INTEGER NOT NULL,
    quantity     INTEGER NOT NULL,
    is_spare     INTEGER NOT NULL DEFAULT 0,
    img_url      TEXT
);
CREATE INDEX IF NOT EXISTS ix_inventory_parts_inv ON inventory_parts(inventory_id);
CREATE TABLE IF NOT EXISTS inventory_minifigs (
    inventory_id INTEGER NOT NULL,
    fig_num      TEXT NOT NULL,
    quantity     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_inventory_minifigs_inv ON inventory_minifigs(inventory_id);
-- M = mold variant, A = alternate, P = print (child is a printed version of parent), ...
CREATE TABLE IF NOT EXISTS part_relationships (
    rel_type        TEXT NOT NULL,
    child_part_num  TEXT NOT NULL,
    parent_part_num TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_part_rel_child ON part_relationships(child_part_num);
CREATE INDEX IF NOT EXISTS ix_part_rel_parent ON part_relationships(parent_part_num);

-- App state
CREATE TABLE IF NOT EXISTS projects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    set_num    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('build', 'pile')),
    path       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- One row per brick crop; candidates is the recognizer output as JSON.
CREATE TABLE IF NOT EXISTS detections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id   INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    crop_path  TEXT NOT NULL,
    bbox       TEXT,
    candidates TEXT NOT NULL DEFAULT '[]',
    error      TEXT,
    status     TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Parts known to be present: in the partial build, or found later in a pile.
CREATE TABLE IF NOT EXISTS project_parts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    part_num     TEXT NOT NULL,
    color_id     INTEGER NOT NULL,
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    source       TEXT NOT NULL CHECK (source IN ('build', 'found')),
    detection_id INTEGER REFERENCES detections(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_project_parts_project ON project_parts(project_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
