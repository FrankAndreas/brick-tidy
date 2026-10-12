import httpx

from app.services.rebrickable import RebrickableClient


def make_client(responses: list):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    return RebrickableClient("secret", httpx.Client(transport=httpx.MockTransport(handler))), calls


def test_looks_up_bricklink_id_and_caches_answer(db):
    client, calls = make_client([httpx.Response(200, json={"results": [{"part_num": "6141"}]})])
    assert client.parts_for_bricklink_id(db, "4073") == ["6141"]
    assert client.parts_for_bricklink_id(db, "4073") == ["6141"]  # from the cache
    assert len(calls) == 1
    assert calls[0].url.params["bricklink_id"] == "4073"
    assert calls[0].headers["Authorization"] == "key secret"


def test_no_match_is_cached_but_failures_are_not(db):
    client, calls = make_client(
        [httpx.Response(200, json={"results": []}), httpx.Response(503), httpx.Response(503)]
    )
    assert client.parts_for_bricklink_id(db, "x1") == []
    assert client.parts_for_bricklink_id(db, "x1") == []
    assert client.parts_for_bricklink_id(db, "x2") == []
    assert client.parts_for_bricklink_id(db, "x2") == []
    assert len(calls) == 3  # x1 once, x2 twice
