import time
import logging
from datetime import datetime, timezone
from typing import Generator

import pandas as pd
from jobspy import scrape_jobs

logger = logging.getLogger(__name__)

SITES = ["indeed", "linkedin", "zip_recruiter", "glassdoor"]


def _df_to_jobs(df: pd.DataFrame, source_tag: str) -> list[dict]:
    jobs = []
    for _, row in df.iterrows():
        url = str(row.get("job_url", "")).strip()
        title = str(row.get("title", "")).strip()
        company = str(row.get("company", "")).strip()
        if not url or not title or not company:
            continue

        sal_min = row.get("min_amount")
        sal_max = row.get("max_amount")

        jobs.append({
            "source": source_tag,
            "external_id": str(row.get("id", "")),
            "title": title,
            "company": company,
            "location": str(row.get("location", "")),
            "is_remote": 1 if row.get("is_remote") else 0,
            "url": url,
            "description": str(row.get("description", "")),
            "salary_min": int(sal_min) if pd.notna(sal_min) else None,
            "salary_max": int(sal_max) if pd.notna(sal_max) else None,
            "date_posted": str(row.get("date_posted", "")) or None,
            "date_found": datetime.now(timezone.utc).isoformat(),
        })
    return jobs


def scrape(
    search_term: str,
    location: str,
    hours_old: int = 72,
    results_per_query: int = 30,
    delay: float = 10.0,
) -> Generator[dict, None, None]:
    for is_remote in [True, False]:
        try:
            df = scrape_jobs(
                site_name=SITES,
                search_term=search_term,
                location=location if not is_remote else "Remote",
                results_wanted=results_per_query,
                hours_old=hours_old,
                is_remote=is_remote,
                linkedin_fetch_description=True,
                enforce_annual_salary=True,
                country_indeed="USA",
            )
            jobs = _df_to_jobs(df, "jobspy")
            logger.info(
                "JobSpy: %s / %s / remote=%s → %d results",
                search_term, location, is_remote, len(jobs)
            )
            yield from jobs
        except Exception as e:
            logger.warning("JobSpy error for '%s' remote=%s: %s", search_term, is_remote, e)

        time.sleep(delay)
