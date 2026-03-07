import re
import time
import logging
from pathlib import Path

import yaml

from utils.db import insert_job, log_search
from scrapers import jobspy_scraper, journalismjobs_scraper, usajobs_scraper

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def run(scrape_jobspy: bool = True, scrape_journalism: bool = True, scrape_usa: bool = True):
    config = _load_config()
    search_cfg = config["search"]
    delay = search_cfg.get("delay_between_sources", 8)
    hours_old = search_cfg.get("hours_old", 72)
    results_per = search_cfg.get("results_per_query", 30)
    terms = search_cfg.get("terms", [])
    locations = search_cfg.get("locations", ["Remote"])

    seen_slugs: set[str] = set()

    def _try_insert(job: dict) -> bool:
        # Dedup by URL (primary) then by (company_slug, title_slug)
        slug_key = (_slug(job["company"]), _slug(job["title"]))
        if slug_key in seen_slugs:
            logger.debug("Slug dedup: %s @ %s", job["title"], job["company"])
            return False
        seen_slugs.add(slug_key)
        return insert_job(job)

    # --- JobSpy ---
    if scrape_jobspy:
        for term in terms:
            for loc in locations:
                total = new = 0
                for job in jobspy_scraper.scrape(term, loc, hours_old, results_per, delay):
                    total += 1
                    if _try_insert(job):
                        new += 1
                log_search(term, loc, "jobspy", total, new)
                time.sleep(delay)

    # --- JournalismJobs ---
    if scrape_journalism:
        total = new = 0
        for job in journalismjobs_scraper.scrape():
            total += 1
            if _try_insert(job):
                new += 1
        log_search("tech", "journalismjobs.com", "journalismjobs", total, new)
        time.sleep(delay)

    # --- USAJOBS ---
    if scrape_usa:
        usa_terms = [
            "software engineer",
            "web developer",
            "full stack developer",
        ]
        for term in usa_terms:
            total = new = 0
            jobs = usajobs_scraper.scrape(term, remote=True)
            for job in jobs:
                total += 1
                if _try_insert(job):
                    new += 1
            log_search(term, "remote", "usajobs", total, new)
            time.sleep(delay)

    logger.info("Scrape complete.")
