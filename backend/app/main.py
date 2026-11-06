import logging

import anthropic
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import catalog, photos, projects
from app.config import Settings
from app.db import init_db
from app.services.rebrickable import RebrickableClient
from app.services.ai import AiClient
from app.services.claude import ClaudeClient
from app.services.infomaniak import InfomaniakClient
from app.services.recognizer import AiPartRecognizer, BrickognizeRecognizer
from app.services.set_identifier import AiSetIdentifier, BrickognizeSetIdentifier, SetIdentifier

# uvicorn's logger is the one that is configured to print when the app is served.
logger = logging.getLogger("uvicorn.error")
PROVIDERS = ("infomaniak", "claude", "none")


def create_app(
    settings: Settings | None = None,
    recognizer: BrickognizeRecognizer | None = None,
    set_identifier: SetIdentifier | None = None,
    rebrickable: RebrickableClient | None = None,
    part_fallback: AiPartRecognizer | None = None,
) -> FastAPI:
    settings = settings or Settings()
    init_db(settings.db_path)
    for directory in (settings.photos_dir, settings.crops_dir):
        directory.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="Brick Tidy")
    app.state.settings = settings
    app.state.recognizer = recognizer or BrickognizeRecognizer(settings.brickognize_url)
    ai = ai_client(settings) if set_identifier is None or part_fallback is None else None
    app.state.set_identifier = set_identifier or default_set_identifier(settings, ai)
    if part_fallback is None and ai:
        part_fallback = AiPartRecognizer(ai)
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


def ai_provider(settings: Settings) -> str:
    """AI_PROVIDER if set; else Infomaniak if configured, else Claude if configured, else none."""
    provider = (settings.ai_provider or "").strip().lower()
    if provider:
        if provider not in PROVIDERS:
            raise ValueError(f"AI_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}")
        return provider
    if settings.infomaniak_ai_token and settings.infomaniak_ai_product_id:
        return "infomaniak"
    if settings.anthropic_api_key:
        return "claude"
    return "none"


def ai_client(settings: Settings) -> AiClient | None:
    """The AI for the set and part fallback, or None (the fallback is off); logs the choice."""
    provider = ai_provider(settings)
    ai: AiClient | None = None
    if provider == "infomaniak":
        if settings.infomaniak_ai_token and settings.infomaniak_ai_product_id:
            ai = InfomaniakClient(
                settings.infomaniak_ai_token, settings.infomaniak_ai_product_id, settings.ai_model
            )
        else:
            logger.warning("AI: infomaniak needs INFOMANIAK_AI_TOKEN and INFOMANIAK_AI_PRODUCT_ID")
    elif provider == "claude":
        if settings.anthropic_api_key:
            # Passed explicitly: a key from .env is not in the process environment.
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            ai = ClaudeClient(settings.claude_model, client)
        else:
            logger.warning("AI: claude needs ANTHROPIC_API_KEY")
    logger.info("AI: %s", f"{ai.name} ({ai.model})" if ai else "off")
    return ai


def default_set_identifier(settings: Settings, ai: AiClient | None = None) -> SetIdentifier:
    return SetIdentifier(
        BrickognizeSetIdentifier(settings.brickognize_sets_url),
        AiSetIdentifier(ai) if ai else None,
        settings.confidence_threshold,
    )
