from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import catalog, photos, projects
from app.config import Settings
from app.db import init_db
from app.services.recognizer import BrickognizeRecognizer


def create_app(
    settings: Settings | None = None, recognizer: BrickognizeRecognizer | None = None
) -> FastAPI:
    settings = settings or Settings()
    init_db(settings.db_path)
    for directory in (settings.photos_dir, settings.crops_dir):
        directory.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Brick Tidy")
    app.state.settings = settings
    app.state.recognizer = recognizer or BrickognizeRecognizer(settings.brickognize_url)

    app.include_router(catalog.router)
    app.include_router(projects.router)
    app.include_router(photos.router)

    # Only photo and crop directories are exposed, never the database.
    app.mount("/media/photos", StaticFiles(directory=settings.photos_dir), name="photos")
    app.mount("/media/crops", StaticFiles(directory=settings.crops_dir), name="crops")
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app

