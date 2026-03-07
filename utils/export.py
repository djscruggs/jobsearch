import csv
import json
from pathlib import Path
from utils.db import get_conn

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"


def export_csv(path: str | None = None) -> Path:
    out = Path(path) if path else OUTPUT_DIR / "jobs_export.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY score DESC NULLS LAST").fetchall()

    if not rows:
        print("No jobs to export.")
        return out

    fieldnames = list(rows[0].keys())
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    print(f"Exported {len(rows)} jobs to {out}")
    return out
