import anthropic
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import catalog, photos, projects
from app.config import Settings
from app.db import init_db
from app.services.rebrickable import RebrickableClient
from app.services.recognizer import BrickognizeRecognizer, ClaudePartRecognizer
from app.services.set_identifier import (
    BrickognizeSetIdentifier,
    ClaudeSetIdentifier,
    SetIdentifier,
)


def create_app(
    settings: Settings | None = None,
    recognizer: BrickognizeRecognizer | None = None,
    set_identifier: SetIdentifier | None = None,
    rebrickable: RebrickableClient | None = None,
    part_fallback: ClaudePartRecognizer | None = None,
) -> FastAPI:
    settings = settings or Settings()
    init_db(settings.db_path)
    for directory in (settings.photos_dir, settings.crops_dir):
        directory.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Brick Tidy")
    app.state.settings = settings
    app.state.recognizer = recognizer or BrickognizeRecognizer(settings.brickognize_url)
    claude = claude_client(settings)
    app.state.set_identifier = set_identifier or default_set_identifier(settings, claude)
    if part_fallback is None and claude:
        part_fallback = ClaudePartRecognizer(settings.claude_model, claude)
    app.state.part_fallback = part_fallback
    if rebrickable is None and settings.rebrickable_api_key:
        rebrickable = RebrickableClient(settings.rebrickable_api_key)
    app.state.rebrickable = rebrickable

    app.include_router(catalog.router)
    app.include_router(projects.router)
    app.include_router(photos.router)

    # Only photo and crop directories are exposed, never the database.
    app.mount("/media/photos", StaticFiles(directory=settings.photos_dir), name="photos")
    app.mount("/media/crops", StaticFiles(directory=settings.crops_dir), name="crops")
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app



def claude_client(settings: Settings) -> anthropic.Anthropic | None:
    if not settings.anthropic_api_key:
        return None
    # Passed explicitly: a key from .env is not in the process environment.
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def default_set_identifier(
    settings: Settings, claude: anthropic.Anthropic | None = None
) -> SetIdentifier:
    return SetIdentifier(
        BrickognizeSetIdentifier(settings.brickognize_sets_url),
        ClaudeSetIdentifier(settings.claude_model, claude) if claude else None,
        settings.confidence_threshold,
    )
