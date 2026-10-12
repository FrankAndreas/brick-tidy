"""Optional Rebrickable API lookups (needs REBRICKABLE_API_KEY), cached in SQLite."""

import json
import sqlite3

import httpx

API_URL = "https://rebrickable.com/api/v3/lego/parts/"


class RebrickableClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=15)

    def parts_for_bricklink_id(self, conn: sqlite3.Connection, bricklink_id: str) -> list[str]:
        """Rebrickable part numbers for a BrickLink part ID (e.g. 4073 -> 6141).

        Answers, including "no match", are cached; failed requests are not.
        """
        row = conn.execute(
            "SELECT part_nums FROM bricklink_parts WHERE bricklink_id = ?", (bricklink_id,)
        ).fetchone()
        if row:
            return json.loads(row["part_nums"])
        try:
            response = self.client.get(
                API_URL,
                params={"bricklink_id": bricklink_id},
                headers={"Authorization": f"key {self.api_key}"},
            )
            response.raise_for_status()
            part_nums = [r["part_num"] for r in response.json().get("results", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return []
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO bricklink_parts (bricklink_id, part_nums) VALUES (?, ?)",
                (bricklink_id, json.dumps(part_nums)),
            )
        return part_nums
