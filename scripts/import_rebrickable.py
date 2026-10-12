#!/usr/bin/env python3
"""Download (optional) and import the Rebrickable CSV catalog into SQLite.

    python scripts/import_rebrickable.py --download   # fetch latest CSVs, then import
    python scripts/import_rebrickable.py              # import CSVs already in DATA_DIR/rebrickable
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.services.inventory import CSV_TABLES, REBRICKABLE_DOWNLOAD_URL, import_directory  # noqa: E402


def download(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for name in CSV_TABLES:
            url = REBRICKABLE_DOWNLOAD_URL.format(name=name)
            target = directory / f"{name}.csv.gz"
            print(f"Downloading {url}")
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(target, "wb") as fh:
                    for chunk in response.iter_bytes():
                        fh.write(chunk)


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--download", action="store_true", help="download the latest CSVs first")
    parser.add_argument("--dir", type=Path, default=settings.rebrickable_dir, help="CSV directory")
    args = parser.parse_args()

    if args.download:
        download(args.dir)

    init_db(settings.db_path)
    conn = connect(settings.db_path)
    try:
        counts = import_directory(conn, args.dir)
    finally:
        conn.close()
    if not counts:
        sys.exit(f"No Rebrickable CSVs found in {args.dir}. Use --download.")
    for name, count in counts.items():
        print(f"{name:20} {count:>9} rows")


if __name__ == "__main__":
    main()
