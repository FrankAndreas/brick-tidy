"""Compare the AI providers on real hard cases, to choose the default (issue #14).

Every case is sent to every provider that has credentials. The report shows, per
job and provider, how often the right answer is first and how often it is in the
top 3, plus errors, the time per case and the tokens (and cost, if prices are given).

Running it costs money: one request per case and provider (two if Infomaniak's
answer needs a retry). Run it only by hand, never in CI.

Cases live in a folder with a `cases.json`; paths are relative to that folder:

    {
      "sets": [
        {"photos": ["box-10696.jpg"], "expected": "10696-1", "note": "number on the box"},
        {"photos": ["build-a.jpg", "build-b.jpg"], "expected": "31058-1", "note": "no number"}
      ],
      "parts": [
        {"crop": "parts/1.jpg", "set_num": "10696-1", "expected": "3003"}
      ]
    }

`expected` for a part may also be a list of acceptable part numbers. Mold variants
and prints of the expected part count as right too.

Part cases can be taken from real use: `export` copies the crops of bricks that
Brickognize was unsure about (or failed on), and that you confirmed in the app,
with the confirmed part as the expected answer. Set photos are not kept by the
app; put them into the folder and add them to `cases.json` by hand. Keep the
folder out of git if photos show private rooms.

Run from backend/ (settings come from .env, as for the app; both providers are
used if their credentials are set, whatever AI_PROVIDER says):

    .venv/bin/python tools/compare_ai.py export ~/brick-cases
    .venv/bin/python tools/compare_ai.py run ~/brick-cases \\
        --price claude=IN,OUT --price infomaniak=IN,OUT --details ~/brick-cases/results.json

Prices are per million input and output tokens, in your currency; look up the current
ones. Claude's cached input is counted at the full input price, so its cost is an
upper bound.
"""

import argparse
import json
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic  # noqa: E402
import httpx  # noqa: E402

from app.api.projects import lookup_part_names  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import connect  # noqa: E402
from app.services.ai import AiClient  # noqa: E402
from app.services.claude import ClaudeClient  # noqa: E402
from app.services.infomaniak import TIMEOUT_SECONDS, InfomaniakClient  # noqa: E402
from app.services.inventory import (  # noqa: E402
    catalog_is_loaded,
    equivalent_parts,
    get_inventory,
    normalize_set_num,
)
from app.services.recognizer import AiPartRecognizer, RecognitionError  # noqa: E402
from app.services.set_identifier import (  # noqa: E402
    AiSetIdentifier,
    SetIdentificationError,
    merge_known,
)

JOBS = ("sets", "parts")


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0


@dataclass
class Tally:
    cases: int = 0
    top1: int = 0
    top3: int = 0
    errors: int = 0
    seconds: float = 0.0
    usage: Usage = field(default_factory=Usage)


# --- providers, with token counting -------------------------------------------------


def infomaniak(settings: Settings, usage: Usage) -> InfomaniakClient:
    def count(response: httpx.Response) -> None:
        response.read()
        if response.is_success:
            tokens = response.json().get("usage") or {}
            usage.input_tokens += tokens.get("prompt_tokens") or 0
            usage.output_tokens += tokens.get("completion_tokens") or 0
        usage.requests += 1

    http = httpx.Client(timeout=TIMEOUT_SECONDS, event_hooks={"response": [count]})
    return InfomaniakClient(
        settings.infomaniak_ai_token, settings.infomaniak_ai_product_id, settings.ai_model, http
    )


def claude(settings: Settings, usage: Usage) -> ClaudeClient:
    real = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def create(**kwargs):
        usage.requests += 1
        response = real.beta.messages.create(**kwargs)
        u = response.usage
        usage.input_tokens += (
            u.input_tokens
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        )
        usage.output_tokens += u.output_tokens
        return response

    counting = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return ClaudeClient(settings.claude_model, counting)


def providers(settings: Settings, wanted: list[str]) -> dict[str, tuple[AiClient, Usage]]:
    found = {}
    for name in wanted:
        usage = Usage()
        if name == "infomaniak" and settings.infomaniak_ai_token and settings.infomaniak_ai_product_id:
            found[name] = (infomaniak(settings, usage), usage)
        elif name == "claude" and settings.anthropic_api_key:
            found[name] = (claude(settings, usage), usage)
        else:
            print(f"skipping {name}: no credentials in the settings", file=sys.stderr)
    return found


# --- jobs ------------------------------------------------------------------------------


def set_answers(ai: AiClient, conn: sqlite3.Connection | None, case: dict, folder: Path) -> list[str]:
    candidates = AiSetIdentifier(ai).identify([folder / p for p in case["photos"]])
    candidates.sort(key=lambda c: c.score, reverse=True)
    if conn is not None:  # as in the app: set numbers that don't exist are dropped
        return [c["set_num"] for c in merge_known(conn, candidates)]
    return [c.set_num for c in candidates]


def set_expected(conn, case: dict) -> set[str]:
    return {normalize_set_num(case["expected"])}


def part_answers(ai: AiClient, conn: sqlite3.Connection, case: dict, folder: Path) -> list[str]:
    candidates = AiPartRecognizer(ai).recognize(folder / case["crop"], set_parts(conn, case["set_num"]))
    return [c.part_num for c in candidates]


def part_expected(conn, case: dict) -> set[str]:
    expected = case["expected"]
    accepted = set()
    for part_num in [expected] if isinstance(expected, str) else expected:
        accepted.update(equivalent_parts(conn, part_num))
    return accepted


_parts_cache: dict[str, dict[str, str]] = {}


def set_parts(conn: sqlite3.Connection, set_num: str) -> dict[str, str]:
    """Part number -> name for the set's parts, as the app sends them."""
    if set_num not in _parts_cache:
        part_nums = {item.part_num for item in get_inventory(conn, set_num)}
        if not part_nums:
            raise SystemExit(f"Set {set_num} has no inventory in the catalog; import it first")
        names = lookup_part_names(conn, part_nums)
        _parts_cache[set_num] = {num: names.get(num, num) for num in part_nums}
    return _parts_cache[set_num]


JOB_FUNCTIONS = {"sets": (set_answers, set_expected), "parts": (part_answers, part_expected)}


def run(args: argparse.Namespace) -> None:
    settings = Settings(_env_file=args.env_file)
    folder = args.folder.expanduser()
    cases = json.loads((folder / "cases.json").read_text())
    conn = connect(settings.db_path) if settings.db_path.exists() else None
    if conn is not None and not catalog_is_loaded(conn):
        conn = None
    if conn is None and cases.get("parts") and "parts" in args.jobs:
        raise SystemExit(f"Part cases need the catalog; none at {settings.db_path} (set DATA_DIR)")

    clients = providers(settings, args.providers)
    if not clients:
        raise SystemExit("No provider has credentials; nothing to compare")
    prices = dict(args.price)

    tallies: dict[tuple[str, str], Tally] = {}
    details = []
    for job in args.jobs:
        answer, expected_of = JOB_FUNCTIONS[job]
        for index, case in enumerate(cases.get(job, [])):
            expected = expected_of(conn, case)
            label = case.get("note") or case.get("crop") or ", ".join(case.get("photos", []))
            print(f"{job} #{index + 1} ({label}): expected {sorted(expected)[0]}")
            for name, (ai, usage) in clients.items():
                tally = tallies.setdefault((job, name), Tally(usage=Usage()))
                before = (usage.input_tokens, usage.output_tokens, usage.requests)
                start = time.monotonic()
                error, answers = None, []
                try:
                    answers = answer(ai, conn, case, folder)
                except (SetIdentificationError, RecognitionError) as exc:
                    error = str(exc)
                seconds = time.monotonic() - start
                rank = next((i + 1 for i, a in enumerate(answers) if a in expected), None)

                tally.cases += 1
                tally.top1 += rank == 1
                tally.top3 += rank is not None and rank <= 3
                tally.errors += error is not None
                tally.seconds += seconds
                tally.usage.input_tokens += usage.input_tokens - before[0]
                tally.usage.output_tokens += usage.output_tokens - before[1]
                tally.usage.requests += usage.requests - before[2]

                shown = error or ", ".join(answers[:3]) or "(nothing)"
                mark = "✓" if rank == 1 else f"#{rank}" if rank else "✗"
                print(f"  {name:<10} {mark:<3} {seconds:5.1f}s  {shown}")
                details.append({"job": job, "case": index + 1, "provider": name, "expected": sorted(expected),
                                "answers": answers, "rank": rank, "error": error, "seconds": round(seconds, 2)})

    print()
    print(report(tallies, prices))
    if args.details:
        args.details.expanduser().write_text(json.dumps(details, indent=2))
        print(f"\nDetails written to {args.details}")


def parse_price(value: str) -> tuple[str, tuple[float, float]]:
    try:
        name, prices = value.split("=")
        per_input, per_output = (float(p) for p in prices.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--price wants name=INPUT,OUTPUT, not {value!r}") from exc
    return name, (per_input, per_output)


def report(tallies: dict[tuple[str, str], Tally], prices: dict[str, tuple[float, float]]) -> str:
    lines = [
        f"{'job':<6} {'provider':<11} {'cases':>5} {'top 1':>7} {'top 3':>7} {'errors':>6}"
        f" {'s/case':>7} {'tokens in/out':>15} {'cost':>9}"
    ]
    for (job, name), t in tallies.items():
        n = t.cases or 1
        cost = ""
        if name in prices:
            per_input, per_output = prices[name]
            cost = f"{(t.usage.input_tokens * per_input + t.usage.output_tokens * per_output) / 1e6:9.3f}"
        lines.append(
            f"{job:<6} {name:<11} {t.cases:>5} {t.top1 / n:>7.0%} {t.top3 / n:>7.0%} {t.errors:>6}"
            f" {t.seconds / n:>7.1f} {f'{t.usage.input_tokens}/{t.usage.output_tokens}':>15} {cost:>9}"
        )
    return "\n".join(lines)


# --- export ----------------------------------------------------------------------------


def export(args: argparse.Namespace) -> None:
    """Part cases from confirmed bricks that Brickognize was unsure about or failed on."""
    settings = Settings(_env_file=args.env_file)
    folder = args.folder.expanduser()
    conn = connect(settings.db_path)
    rows = conn.execute(
        """
        SELECT d.id, d.crop_path, d.candidates, d.error, pr.set_num, pp.part_num
          FROM detections d
          JOIN photos ph ON ph.id = d.photo_id
          JOIN projects pr ON pr.id = ph.project_id
          JOIN project_parts pp ON pp.detection_id = d.id
         WHERE d.status = 'confirmed'
         ORDER BY d.id DESC
        """
    ).fetchall()

    cases_file = folder / "cases.json"
    cases = json.loads(cases_file.read_text()) if cases_file.exists() else {"sets": [], "parts": []}
    known = {c["crop"] for c in cases.setdefault("parts", [])}
    (folder / "parts").mkdir(parents=True, exist_ok=True)
    added = 0
    for row in rows:
        if added >= args.limit:
            break
        brickognize = [c for c in json.loads(row["candidates"]) if c.get("source") == "brickognize"]
        unsure = row["error"] or not brickognize or brickognize[0]["score"] < settings.confidence_threshold
        crop = f"parts/{row['id']}.jpg"
        source = settings.data_dir / row["crop_path"]
        if not unsure or crop in known or not source.exists():
            continue
        shutil.copyfile(source, folder / crop)
        cases["parts"].append({"crop": crop, "set_num": row["set_num"], "expected": row["part_num"]})
        added += 1
    cases.setdefault("sets", [])
    cases_file.write_text(json.dumps(cases, indent=2))
    print(f"Added {added} part cases to {cases_file} ({len(cases['parts'])} in all).")
    if not cases["sets"]:
        print("No set cases yet: add photos and entries under \"sets\" by hand.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env-file", default=".env", help="settings file, as for the app (default .env)")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="ask the providers about every case and report")
    run_parser.add_argument("folder", type=Path)
    run_parser.add_argument("--providers", type=lambda v: v.split(","), default=["infomaniak", "claude"])
    run_parser.add_argument("--jobs", type=lambda v: v.split(","), default=list(JOBS))
    run_parser.add_argument("--price", action="append", default=[], type=parse_price, metavar="NAME=IN,OUT",
                            help="price per million input and output tokens (look up the current ones)")
    run_parser.add_argument("--details", type=Path, help="write every answer to this JSON file")
    run_parser.set_defaults(func=run)

    export_parser = commands.add_parser("export", help="add part cases from confirmed unsure bricks")
    export_parser.add_argument("folder", type=Path)
    export_parser.add_argument("--limit", type=int, default=30)
    export_parser.set_defaults(func=export)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
