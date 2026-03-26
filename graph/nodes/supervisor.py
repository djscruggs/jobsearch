"""Supervisor node — Claude decides which scrapers to run this cycle.

Reads recent DB stats (yield per source, days since last run) and returns
a prioritised list of active_sources plus human-readable reasoning.
Falls back to all sources if Claude is unavailable.
"""
import json
import logging
from pathlib import Path

import yaml

from utils.claude_client import get_client
from utils.db import get_conn

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"

ALL_SOURCES = ["jobspy", "journalismjobs", "usajobs", "techjobsforgood", "fastforward", "levelsfyi", "email"]


def _source_stats() -> list[dict]:
    """Return per-source stats from the searches table (last 7 days)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT source,
                   COUNT(*)         AS runs,
                   SUM(jobs_new)    AS total_new,
                   MAX(run_at)      AS last_run
            FROM searches
            WHERE run_at >= datetime('now', '-7 days')
            GROUP BY source
        """).fetchall()
    return [dict(r) for r in rows]


def _zero_yield_sources() -> set[str]:
    """Sources with 0 new jobs in their last 3 consecutive runs."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT source, jobs_new
            FROM (
                SELECT source, jobs_new,
                       ROW_NUMBER() OVER (PARTITION BY source ORDER BY run_at DESC) AS rn
                FROM searches
            ) ranked
            WHERE rn <= 3
        """).fetchall()

    from collections import defaultdict
    by_source: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        by_source[r["source"]].append(r["jobs_new"])

    zero = set()
    for src, counts in by_source.items():
        if len(counts) >= 3 and all(c == 0 for c in counts):
            zero.add(src)
    return zero


def supervisor_node(state: dict) -> dict:
    """LangGraph node: pick which scrapers to run via Claude."""
    run_mode = state.get("run_mode", "full")

    # Non-scraping modes skip the supervisor
    if run_mode in ("score_only", "tailor_only", "review_only"):
        return {"active_sources": [], "supervisor_reasoning": "Skipped (non-scraping mode)"}

    stats = _source_stats()
    zero_yield = _zero_yield_sources()

    config = yaml.safe_load(CONFIG_PATH.read_text())
    model = config["scoring"].get("model", "claude-sonnet-4-6")

    system = (
        "You are a job-search pipeline supervisor. "
        "Given recent scraper statistics, decide which sources to run this cycle. "
        "Skip sources with consistently zero yield to save time. "
        "Always include at least 2 sources. "
        "Return ONLY valid JSON: {\"sources\": [\"...\"], \"reasoning\": \"...\"}"
    )
    user = (
        f"Available sources: {ALL_SOURCES}\n"
        f"Sources with 0 new jobs in last 3 runs (consider skipping): {sorted(zero_yield)}\n"
        f"Recent stats (last 7 days):\n{json.dumps(stats, indent=2)}\n\n"
        "Which sources should run this cycle? Include mission-critical niche boards "
        "(journalismjobs, techjobsforgood, fastforward) even if yield is low."
    )

    try:
        client = get_client()
        resp = client.messages.create(
            model=model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        sources = data.get("sources", ALL_SOURCES)
        reasoning = data.get("reasoning", "")
    except Exception as e:
        logger.warning("Supervisor Claude call failed (%s), using all sources", e)
        sources = ALL_SOURCES
        reasoning = f"Fallback to all sources due to error: {e}"

    # Guarantee list contains only valid source names
    sources = [s for s in sources if s in ALL_SOURCES]
    if not sources:
        sources = ALL_SOURCES

    logger.info("Supervisor selected sources: %s", sources)
    return {"active_sources": sources, "supervisor_reasoning": reasoning}
