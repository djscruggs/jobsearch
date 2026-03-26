"""
levels.fyi job scraper — high-signal board for senior/staff/principal tech roles.

Strategy: levels.fyi is a Next.js SPA whose API responses are encrypted. However,
the initial SSR page embeds plain JSON in __NEXT_DATA__ (up to ~25 jobs per search
term). We extract that directly — fast, reliable, no decryption needed.

Pagination beyond the initial batch requires clicking "Show More" and reading the
React fiber tree, which is fragile. For now we take the SSR batch per term and
cycle through all search terms to maximize coverage.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

BASE_URL = "https://www.levels.fyi"
JOBS_URL = (
    f"{BASE_URL}/jobs"
    "?locationSlug=united-states"
    "&workArrangements=remote"
    "&postedAfterTimeType=days"
    "&postedAfterValue=3"
    "&searchText={term}"
)

# levels.fyi salary is in the currency specified by baseSalaryCurrency.
# We only keep USD salaries; others get None'd out.
USD_SALARY_FLOOR = 30_000   # sanity check: ignore implausible values


def _usd_salary(value, currency: str) -> int | None:
    if not value or currency != "USD":
        return None
    try:
        v = int(value)
        return v if v >= USD_SALARY_FLOOR else None
    except (TypeError, ValueError):
        return None


_US_PATTERN = re.compile(
    r"\b(United States|USA|\bUS\b|Remote"
    r"|Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia"
    r"|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts"
    r"|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey"
    r"|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania"
    r"|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia"
    r"|Washington|West Virginia|Wisconsin|Wyoming"
    r"|, AK|, AL|, AR|, AZ|, CA|, CO|, CT|, DC|, DE|, FL|, GA|, HI|, IA|, ID|, IL|, IN"
    r"|, KS|, KY|, LA|, MA|, MD|, ME|, MI|, MN|, MO|, MS|, MT|, NC|, ND|, NE|, NH|, NJ"
    r"|, NM|, NV|, NY|, OH|, OK|, OR|, PA|, RI|, SC|, SD|, TN|, TX|, UT|, VA|, VT|, WA"
    r"|, WI|, WV|, WY)\b",
    re.IGNORECASE,
)


def _is_us_or_remote(locations: list[str], arrangement: str) -> bool:
    """Return True if the job is remote or located in the US."""
    if arrangement == "remote":
        return True
    if not locations:
        # No location listed — could be fully remote; include it
        return True
    combined = " ".join(locations)
    return bool(_US_PATTERN.search(combined))


def _map_company_result(company: dict) -> list[dict]:
    """Flatten one company result block (which contains multiple job listings) into job dicts."""
    company_name = company.get("companyName") or company.get("name") or ""
    jobs_out = []

    for j in company.get("jobs", []):
        title = j.get("title") or ""
        if not title:
            continue

        locations = j.get("locations") or []
        arrangement = (j.get("workArrangement") or "").lower()

        # Filter: only US-based or remote jobs
        if not _is_us_or_remote(locations, arrangement):
            continue

        job_id = str(j.get("id") or "")
        url = j.get("applicationUrl") or (f"{BASE_URL}/jobs/{job_id}" if job_id else "")

        location = ", ".join(locations) if locations else ""
        is_remote = 1 if arrangement == "remote" or re.search(r"\bremote\b", location, re.IGNORECASE) else 0

        currency = j.get("baseSalaryCurrency") or ""
        sal_min = _usd_salary(j.get("minBaseSalary"), currency)
        sal_max = _usd_salary(j.get("maxBaseSalary"), currency)

        date_posted = j.get("postingDate")

        jobs_out.append({
            "source": "levelsfyi",
            "external_id": job_id or url,
            "title": title,
            "company": company_name,
            "location": location,
            "is_remote": is_remote,
            "url": url,
            "description": "",  # not included in SSR batch
            "salary_min": sal_min,
            "salary_max": sal_max,
            "date_posted": date_posted,
            "date_found": datetime.now(timezone.utc).isoformat(),
        })

    return jobs_out


async def _scrape_term_async(term: str) -> list[dict]:
    url = JOBS_URL.format(term=term.replace(" ", "+"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
        except PWTimeout:
            logger.warning("levelsfyi: page load timed out for term=%r, continuing", term)

        # Extract SSR data from __NEXT_DATA__
        jobs: list[dict] = []
        try:
            raw = await page.evaluate(
                "() => { const el = document.getElementById('__NEXT_DATA__'); return el ? el.textContent : null }"
            )
            if not raw:
                logger.warning("levelsfyi: no __NEXT_DATA__ found for term=%r at %s", term, url)
            else:
                data = json.loads(raw)
                page_props = data.get("props", {}).get("pageProps", {})
                initial_jobs_data = page_props.get("initialJobsData", {})
                results = initial_jobs_data.get("results", [])
                for company in results:
                    jobs.extend(_map_company_result(company))
                logger.info("levelsfyi: %d jobs from __NEXT_DATA__ (term=%r)", len(jobs), term)
        except Exception as e:
            logger.error("levelsfyi: failed to extract __NEXT_DATA__ for term=%r: %s", term, e)

        await browser.close()

    return jobs


async def _scrape_async(terms: list[str], delay: float = 5.0) -> list[dict]:
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()

    for i, term in enumerate(terms):
        if i > 0:
            await asyncio.sleep(delay)
        try:
            term_jobs = await _scrape_term_async(term)
            for job in term_jobs:
                uid = job.get("external_id") or job.get("url") or ""
                if uid and uid in seen_ids:
                    continue
                url = job.get("url") or ""
                if url and url in seen_urls:
                    continue
                if uid:
                    seen_ids.add(uid)
                if url:
                    seen_urls.add(url)
                all_jobs.append(job)
        except Exception as e:
            logger.error("levelsfyi: error scraping term=%r: %s", term, e)

    logger.info("levelsfyi: %d total jobs across %d terms", len(all_jobs), len(terms))
    return all_jobs


def scrape(terms: list[str] | None = None, delay: float = 5.0) -> list[dict]:
    if terms is None:
        terms = ["senior software engineer"]
    return asyncio.run(_scrape_async(terms, delay))
