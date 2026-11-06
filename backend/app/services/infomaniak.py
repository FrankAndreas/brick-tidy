"""Infomaniak's AI (OpenAI-compatible chat API, processed in Switzerland) as the AI provider.

The schema is asked for with `response_format: json_schema`. If the service rejects
that, it is left out from then on. Either way the schema is also described in the
prompt, the answer is checked against it, and a wrong answer gets one retry.
The token and the product ID (part of the URL) never go into an error message.
"""

import json
import re

import httpx

from app.services.ai import AiError, schema_errors

API_BASE = "https://api.infomaniak.com/2/ai/{product_id}/openai/v1"
TIMEOUT_SECONDS = 90
MAX_TOKENS = 4000


class InfomaniakClient:
    name = "infomaniak"

    def __init__(self, token: str, product_id: str, model: str, client: httpx.Client | None = None):
        self.model = model
        self.url = API_BASE.format(product_id=product_id) + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        self.json_schema = True  # False once the service has rejected response_format

    def ask_json(self, system: str, content: list[dict], schema: dict) -> dict:
        messages = [
            {"role": "system", "content": f"{system}\n\n{schema_prompt(schema)}"},
            {"role": "user", "content": [openai_part(b) for b in content]},
        ]
        answer = self._complete(messages, schema)
        try:
            return parse(answer, schema)
        except AiError as exc:
            # One retry, telling the model what was wrong.
            messages += [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": f"{exc} Answer again with only the JSON object."},
            ]
        answer = self._complete(messages, schema)
        try:
            return parse(answer, schema)
        except AiError as exc:
            raise AiError(f"Infomaniak AI gave no usable answer: {exc}") from exc

    def _complete(self, messages: list[dict], schema: dict) -> str:
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": MAX_TOKENS}
        if self.json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": True},
            }
        try:
            response = self.client.post(self.url, json=body, headers=self.headers)
            if response.status_code in (400, 422) and self.json_schema:
                # Model or service without structured output: rely on prompt and check.
                self.json_schema = False
                body.pop("response_format")
                response = self.client.post(self.url, json=body, headers=self.headers)
        except httpx.TimeoutException as exc:
            raise AiError("Infomaniak AI did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise AiError("Cannot reach the Infomaniak AI") from exc

        if response.status_code in (401, 403):
            raise AiError("Infomaniak rejected INFOMANIAK_AI_TOKEN (needs the ai-tools scope)")
        if response.status_code == 404:
            raise AiError("Infomaniak AI not found: check INFOMANIAK_AI_PRODUCT_ID and AI_MODEL")
        if response.status_code == 429:
            raise AiError("Infomaniak AI is rate limited; try again later")
        if response.is_error:
            raise AiError(f"Infomaniak AI request failed (HTTP {response.status_code})")
        try:
            choice = response.json()["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AiError("Infomaniak AI sent an unexpected answer") from exc
        if choice.get("finish_reason") == "length":
            raise AiError("Infomaniak AI's answer was cut off")
        return str(text or "")


def openai_part(block: dict) -> dict:
    if block["type"] == "image":
        url = f"data:{block['media_type']};base64,{block['data']}"
        return {"type": "image_url", "image_url": {"url": url}}
    return {"type": "text", "text": block["text"]}  # no prompt caching here


def schema_prompt(schema: dict) -> str:
    return (
        "Answer with only one JSON object, no text before or after it, matching this JSON schema:\n"
        + json.dumps(schema)
    )


def parse(answer: str, schema: dict) -> dict:
    """The JSON object in the answer (also inside a ``` fence), checked against the schema."""
    text = answer.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        value = json.loads(text[start : end + 1]) if 0 <= start < end else json.loads(text)
    except ValueError as exc:
        raise AiError("The answer was not valid JSON.") from exc
    errors = schema_errors(value, schema)
    if errors:
        raise AiError("The answer did not match the schema: " + "; ".join(errors[:5]) + ".")
    return value
