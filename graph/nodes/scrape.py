"""Scraper nodes — fan-out + per-source nodes + join.

fan_out_scrapers emits one Send("scrape_{source}", ...) per active source.
Each scrape_* node returns {"jobs_found": N, "jobs_new": M}.
State uses Annotated[int, operator.add] reducers so parallel results are summed.
"""
import logging
import re
import time
from pathlib import Path

import yaml
from langgraph.types import Send

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.yaml"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


# ── Fan-out ────────────────────────────────────────────────────────────────

def fan_out_scrapers(state: dict) -> list[Send]:
    """Emit one Send per active source — LangGraph runs them in parallel."""
    sources = state.get("active_sources", [])
    if not sources:
        return []
    return [Send(f"scrape_{src}", {"source": src}) for src in sources]


# ── Shared insert helper ───────────────────────────────────────────────────

def _run_source(source: str, jobs_iter) -> dict:
    """Insert jobs from an iterator and return counts."""
    from utils.db import insert_job

    seen_slugs: set[tuple] = set()
    total = new = 0
    errors: list[str] = []

    for job in jobs_iter:
        try:
            slug_key = (_slug(job["company"]), _slug(job["title"]))
            if slug_key in seen_slugs:
                continue
            seen_slugs.add(slug_key)
            total += 1
            if insert_job(job):
                new += 1
        except Exception as e:
            errors.append(f"{source}: {e}")

    return {"jobs_found": total, "jobs_new": new, "scrape_errors": errors}


def _log_search(source: str, counts: dict):
    from utils.db import log_search
    log_search("(graph)", source, source, counts["jobs_found"], counts["jobs_new"])


# ── Per-source nodes ───────────────────────────────────────────────────────

def scrape_jobspy(state: dict) -> dict:
    config = _load_config()
    search_cfg = config["search"]
    delay = search_cfg.get("delay_between_sources", 8)
    hours_old = search_cfg.get("hours_old", 72)
    results_per = search_cfg.get("results_per_query", 30)
    terms = search_cfg.get("terms", [])
    locations = search_cfg.get("locations", ["Remote"])

    from scrapers import jobspy_scraper
    total = new = 0
    errors: list[str] = []
    seen_slugs: set[tuple] = set()

    from utils.db import insert_job, log_search

    for term in terms:
        for loc in locations:
            t = n = 0
            try:
                for job in jobspy_scraper.scrape(term, loc, hours_old, results_per, delay):
                    slug_key = (_slug(job["company"]), _slug(job["title"]))
                    if slug_key in seen_slugs:
                        continue
                    seen_slugs.add(slug_key)
                    t += 1
                    if insert_job(job):
                        n += 1
                log_search(term, loc, "jobspy", t, n)
            except Exception as e:
                errors.append(f"jobspy/{term}/{loc}: {e}")
            total += t
            new += n
            time.sleep(delay)

    return {"jobs_found": total, "jobs_new": new, "scrape_errors": errors}


def scrape_journalismjobs(state: dict) -> dict:
    from scrapers import journalismjobs_scraper
    counts = _run_source("journalismjobs", journalismjobs_scraper.scrape())
    _log_search("journalismjobs", counts)
    return counts


def scrape_usajobs(state: dict) -> dict:
    config = _load_config()
    delay = config["search"].get("delay_between_sources", 8)

    from scrapers import usajobs_scraper
    from utils.db import insert_job, log_search

    seen_slugs: set[tuple] = set()
    total = new = 0
    errors: list[str] = []
    usa_terms = ["software engineer", "web developer", "full stack developer"]

    for term in usa_terms:
        t = n = 0
        try:
            for job in usajobs_scraper.scrape(term, remote=True):
                slug_key = (_slug(job["company"]), _slug(job["title"]))
                if slug_key in seen_slugs:
                    continue
                seen_slugs.add(slug_key)
                t += 1
                if insert_job(job):
                    n += 1
            log_search(term, "remote", "usajobs", t, n)
        except Exception as e:
            errors.append(f"usajobs/{term}: {e}")
        total += t
        new += n
        time.sleep(delay)

    return {"jobs_found": total, "jobs_new": new, "scrape_errors": errors}


def scrape_techjobsforgood(state: dict) -> dict:
    config = _load_config()
    delay = config["search"].get("delay_between_sources", 8)
    from scrapers import techjobsforgood_scraper
    counts = _run_source("techjobsforgood", techjobsforgood_scraper.scrape(delay=delay))
    _log_search("techjobsforgood", counts)
    return counts


def scrape_fastforward(state: dict) -> dict:
    from scrapers import fastforward_scraper
    counts = _run_source("fastforward", fastforward_scraper.scrape())
    _log_search("fastforward", counts)
    return counts


def scrape_levelsfyi(state: dict) -> dict:
    config = _load_config()
    terms = config["search"].get("terms", [])
    delay = config["search"].get("delay_between_sources", 8)
    from scrapers import levelsfyi_scraper
    counts = _run_source("levelsfyi", levelsfyi_scraper.scrape(terms, delay))
    _log_search("levelsfyi", counts)
    return counts


def scrape_email(state: dict) -> dict:
    from scrapers import email_scraper
    counts = _run_source("email", email_scraper.scrape())
    if counts["jobs_found"]:
        _log_search("email", counts)
    return counts


# ── Join node (collects parallel results) ─────────────────────────────────

def join_scrapers(state: dict) -> dict:
    """Runs after all scrape_* branches complete. Logs the totals."""
    found = state.get("jobs_found", 0)
    new = state.get("jobs_new", 0)
    errors = state.get("scrape_errors", [])
    logger.info("Scrape complete — found=%d new=%d errors=%d", found, new, len(errors))
    for err in errors:
        logger.warning("Scrape error: %s", err)
    return {}
