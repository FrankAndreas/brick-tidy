import json
import logging

import cv2
import httpx
import numpy as np
import pytest

from app.config import Settings
from app.main import ai_client, ai_provider
from app.services.ai import AiError, image, schema_errors, text
from app.services.claude import ClaudeClient
from app.services.infomaniak import InfomaniakClient
from app.services.recognizer import AiPartRecognizer, Candidate, RecognitionError
from app.services.set_identifier import AI_SCHEMA, AiSetIdentifier, SetCandidate

ANSWER = {"candidates": [{"set_num": "1234", "name": "House", "confidence": 0.7, "evidence": "box"}]}


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.jpg"
    cv2.imwrite(str(path), np.full((400, 300, 3), 200, np.uint8))
    return path


class FakeInfomaniak:
    """The chat completions endpoint: answers in turn from a list of responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        content = answer if isinstance(answer, str) else json.dumps(answer)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        )

    def client(self) -> InfomaniakClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return InfomaniakClient("secret-token", "4711", "google/gemma-4-31B-it", http)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def test_infomaniak_request_shape(photo):
    fake = FakeInfomaniak(ANSWER)
    content = [text("Parts list: …", cache=True), image(photo), text("Which set?")]
    assert fake.client().ask_json("You identify sets.", content, AI_SCHEMA) == ANSWER

    [request] = fake.requests
    assert str(request.url) == "https://api.infomaniak.com/2/ai/4711/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-token"
    body = fake.body()
    assert body["model"] == "google/gemma-4-31B-it"
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == AI_SCHEMA
    system, user = body["messages"]
    assert system["role"] == "system" and system["content"].startswith("You identify sets.")
    assert '"set_num"' in system["content"]  # the schema is described in the prompt too
    first, picture, last = user["content"]
    assert first == {"type": "text", "text": "Parts list: …"}  # no cache_control
    assert picture["type"] == "image_url"
    assert picture["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert last == {"type": "text", "text": "Which set?"}


def test_infomaniak_answer_in_a_code_fence():
    fake = FakeInfomaniak(f"Here you go:\n```json\n{json.dumps(ANSWER)}\n```")
    assert fake.client().ask_json("s", [text("q")], AI_SCHEMA) == ANSWER


def test_infomaniak_invalid_json_is_retried_once():
    fake = FakeInfomaniak("I think it is the house set.", ANSWER)
    assert fake.client().ask_json("s", [text("q")], AI_SCHEMA) == ANSWER
    retry = fake.body(1)["messages"]
    assert retry[-2] == {"role": "assistant", "content": "I think it is the house set."}
    assert "not valid JSON" in retry[-1]["content"]


def test_infomaniak_schema_mismatch_is_retried_once():
    wrong = {"candidates": [{"set_num": 1234, "name": "House", "confidence": "high", "evidence": ""}]}
    fake = FakeInfomaniak(wrong, ANSWER)
    assert fake.client().ask_json("s", [text("q")], AI_SCHEMA) == ANSWER
    hint = fake.body(1)["messages"][-1]["content"]
    assert "set_num is not a string" in hint and "confidence is not a number" in hint


def test_infomaniak_second_wrong_answer_raises():
    fake = FakeInfomaniak("nope", "still nope")
    with pytest.raises(AiError, match="no usable answer"):
        fake.client().ask_json("s", [text("q")], AI_SCHEMA)
    assert len(fake.requests) == 2


def test_infomaniak_without_structured_output_falls_back_to_the_prompt():
    rejected = httpx.Response(400, json={"error": "response_format not supported"})
    fake = FakeInfomaniak(rejected, ANSWER, ANSWER)
    client = fake.client()
    assert client.ask_json("s", [text("q")], AI_SCHEMA) == ANSWER
    assert "response_format" in fake.body(0) and "response_format" not in fake.body(1)
    client.ask_json("s", [text("q")], AI_SCHEMA)
    assert "response_format" not in fake.body(2)  # remembered


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(401), "rejected INFOMANIAK_AI_TOKEN"),
        (httpx.Response(403), "rejected INFOMANIAK_AI_TOKEN"),
        (httpx.Response(404), "INFOMANIAK_AI_PRODUCT_ID"),
        (httpx.Response(429), "rate limited"),
        (httpx.Response(502), "HTTP 502"),
        (httpx.Response(200, json={"unexpected": True}), "unexpected answer"),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}),
            "cut off",
        ),
        (httpx.ConnectError("no route"), "Cannot reach"),
        (httpx.ReadTimeout("slow"), "in time"),
    ],
)
def test_infomaniak_errors(response, message):
    with pytest.raises(AiError, match=message) as info:
        FakeInfomaniak(response).client().ask_json("s", [text("q")], AI_SCHEMA)
    assert "secret-token" not in str(info.value) and "4711" not in str(info.value)


def test_set_and_part_fallback_through_infomaniak(photo):
    sets = FakeInfomaniak(ANSWER).client()
    assert AiSetIdentifier(sets).identify([photo]) == [SetCandidate("1234-1", 0.7, "infomaniak", "box")]

    parts = FakeInfomaniak(
        {"candidates": [{"part_num": "3003", "confidence": 0.6, "reason": "2x2"}]}
    ).client()
    recognizer = AiPartRecognizer(parts)
    assert recognizer.recognize(photo, {"3003": "Brick 2 x 2"}) == [
        Candidate("3003", "Brick 2 x 2", 0.6, source="infomaniak", reason="2x2")
    ]
    with pytest.raises(RecognitionError, match="rate limited"):
        AiPartRecognizer(FakeInfomaniak(httpx.Response(429)).client()).recognize(photo, {})


def test_schema_errors():
    assert schema_errors(ANSWER, AI_SCHEMA) == []
    assert schema_errors({}, AI_SCHEMA) == ["$.candidates is missing"]
    assert schema_errors({"candidates": [{"set_num": "1", "x": 1}]}, AI_SCHEMA) == [
        "$.candidates[0].name is missing",
        "$.candidates[0].confidence is missing",
        "$.candidates[0].evidence is missing",
        "$.candidates[0].x is not allowed",
    ]
    assert schema_errors({"candidates": "none"}, AI_SCHEMA) == ["$.candidates is not an array"]
    assert schema_errors(True, {"type": "number"}) == ["$ is not a number"]


def settings(**values) -> Settings:
    # Every AI setting given, so variables in the test process's environment don't count.
    base = dict(
        ai_provider=None,
        infomaniak_ai_token=None,
        infomaniak_ai_product_id=None,
        anthropic_api_key=None,
        _env_file=None,
    )
    return Settings(**{**base, **values})


INFOMANIAK = dict(infomaniak_ai_token="t", infomaniak_ai_product_id="1")


@pytest.mark.parametrize(
    "values, provider",
    [
        ({}, "none"),
        (INFOMANIAK, "infomaniak"),
        ({"anthropic_api_key": "k"}, "claude"),  # existing setups keep using Claude
        ({**INFOMANIAK, "anthropic_api_key": "k"}, "infomaniak"),
        ({"infomaniak_ai_token": "t", "anthropic_api_key": "k"}, "claude"),  # no product ID
        ({**INFOMANIAK, "anthropic_api_key": "k", "ai_provider": "claude"}, "claude"),
        ({**INFOMANIAK, "ai_provider": "None"}, "none"),
        ({"anthropic_api_key": "k", "ai_provider": ""}, "claude"),  # AI_PROVIDER= in .env
    ],
)
def test_provider_choice(values, provider):
    assert ai_provider(settings(**values)) == provider


def test_unknown_provider_is_an_error():
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        ai_provider(settings(ai_provider="openai"))


def test_ai_client_and_startup_log(caplog):
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    client = ai_client(settings(**INFOMANIAK))
    assert isinstance(client, InfomaniakClient) and client.model == "google/gemma-4-31B-it"
    claude = ai_client(settings(anthropic_api_key="k", claude_model="claude-x"))
    assert isinstance(claude, ClaudeClient) and claude.model == "claude-x"
    assert ai_client(settings(**INFOMANIAK, ai_provider="none")) is None
    assert ai_client(settings(ai_provider="infomaniak")) is None  # no credentials: off, no error

    assert [r.getMessage() for r in caplog.records] == [
        "AI: infomaniak (google/gemma-4-31B-it)",
        "AI: claude (claude-x)",
        "AI: off",
        "AI: infomaniak needs INFOMANIAK_AI_TOKEN and INFOMANIAK_AI_PRODUCT_ID",
        "AI: off",
    ]
