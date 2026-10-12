from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect, init_db
from app.main import create_app
from app.services.inventory import import_directory
from app.services.recognizer import BrickognizeRecognizer
from app.services.set_identifier import BrickognizeSetIdentifier, SetIdentifier

FIXTURES = Path(__file__).parent / "fixtures"
BRICKOGNIZE_URL = "https://brickognize.test/predict/"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, brickognize_url=BRICKOGNIZE_URL, _env_file=None)


@pytest.fixture
def db(settings: Settings):
    init_db(settings.db_path)
    conn = connect(settings.db_path)
    import_directory(conn, FIXTURES)
    yield conn
    conn.close()


@pytest.fixture
def brickognize() -> dict:
    """What the fake Brickognize answers; tests may mutate it."""
    return {
        "status": 200,
        "items": [
            {"id": "3002", "name": "Brick 2 x 3", "type": "part", "score": 0.4},
            {"id": "3001", "name": "Brick 2 x 4", "type": "part", "score": 0.9},
            {"id": "fig-1", "name": "Some Minifig", "type": "fig", "score": 0.95},
        ],
    }


@pytest.fixture
def recognizer(brickognize) -> BrickognizeRecognizer:
    def handler(request: httpx.Request) -> httpx.Response:
        assert b'name="query_image"' in request.content
        return httpx.Response(
            brickognize["status"], json={"listing_id": "x", "items": brickognize["items"]}
        )

    return BrickognizeRecognizer(
        BRICKOGNIZE_URL, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


@pytest.fixture
def brickognize_sets() -> list[dict]:
    """Items the fake Brickognize set endpoint returns; tests may mutate it."""
    return [{"id": "1234-1", "name": "Test House", "type": "set", "score": 0.7}]


@pytest.fixture
def set_identifier(brickognize_sets) -> SetIdentifier:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": brickognize_sets})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SetIdentifier(BrickognizeSetIdentifier(BRICKOGNIZE_URL, client=client), None, 0.5)


@pytest.fixture
def client(settings, db, recognizer, set_identifier) -> TestClient:
    return TestClient(create_app(settings, recognizer=recognizer, set_identifier=set_identifier))
