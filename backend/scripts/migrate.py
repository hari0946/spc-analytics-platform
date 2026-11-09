"""Apply backend/sql/*.sql in order, once each (tracked in METADATA.SCHEMA_MIGRATIONS).

001 always runs first (idempotent) because it creates the database that holds the tracking table.
Usage (from backend/):  python -m scripts.migrate
"""
import sys
from pathlib import Path

from app.core import snowflake as sf

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"
TRACK = "SPC_PLATFORM.METADATA.SCHEMA_MIGRATIONS"


def main() -> int:
    files = sorted(SQL_DIR.glob("*.sql"))
    conn = sf.get_connection()
    try:
        first, rest = files[0], files[1:]
        print(f"Applying {first.name} ...")
        conn.execute_string(first.read_text(encoding="utf-8"))
        sf.execute(f"CREATE TABLE IF NOT EXISTS {TRACK} (FILE_NAME VARCHAR NOT NULL, APPLIED_AT TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP())")
        applied = {r[0] for r in sf.execute(f"SELECT FILE_NAME FROM {TRACK}")}
        for f in rest:
            if f.name in applied:
                print(f"Skipping {f.name} (already applied)")
                continue
            print(f"Applying {f.name} ...")
            conn.execute_string(f.read_text(encoding="utf-8"))
            sf.execute(f"INSERT INTO {TRACK} (FILE_NAME) VALUES (%s)", (f.name,))
        print("Done.")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"Migration failed: {e}", file=sys.stderr)
        return 1
    finally:
        sf.close_connection()


if __name__ == "__main__":
    raise SystemExit(main())
