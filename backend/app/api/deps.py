import sqlite3
from collections.abc import Iterator

from fastapi import HTTPException, Request

from app.config import Settings
from app.db import connect
from app.services.recognizer import BrickognizeRecognizer
from app.services.set_identifier import SetIdentifier


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_recognizer(request: Request) -> BrickognizeRecognizer:
    return request.app.state.recognizer


def get_set_identifier(request: Request) -> SetIdentifier:
    return request.app.state.set_identifier


def get_project_or_404(conn: sqlite3.Connection, project_id: int) -> sqlite3.Row:
    project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project is None:
        raise HTTPException(404, "Project not found")
    return project
