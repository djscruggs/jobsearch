import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://techjobsforgood.com"
SEARCH_TERMS = [
    "software engineer",
    "developer",
    "frontend",
    "backend",
    "full stack",
    "engineering",
]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
}


def _parse_salary(text: str) -> tuple[int | None, int | None]:
    nums = re.findall(r"\$?([\d,]+)", text.replace("K", "000"))
    vals = [int(n.replace(",", "")) for n in nums if int(n.replace(",", "")) > 1000]
    if len(vals) >= 2:
        return min(vals), max(vals)
    if len(vals) == 1:
        return vals[0], None
    return None, None


def _fetch_job_detail(client: httpx.Client, path: str) -> dict:
    """Fetch JSON-LD schema from a job detail page."""
    try:
        resp = client.get(f"{BASE_URL}{path}", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        script = soup.find("script", type="application/ld+json")
        if script and script.string:
            return json.loads(script.string)
    except Exception as e:
        logger.debug("Detail fetch failed for %s: %s", path, e)
    return {}


def _cards_from_page(html: str) -> list[tuple[str, str, str, str, str]]:
    """Return (path, title, company, location, salary_text) tuples from a listing page."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for card in soup.select(".job-card"):
        link = card.select_one("a[href^='/jobs/']")
        if not link:
            continue
        path = link["href"].split("?")[0]
        title = card.select_one(".job-title")
        company = card.select_one(".company_name")
        location = card.select_one(".location")
        salary = card.select_one(".salary")
        results.append((
            path,
            title.get_text(strip=True) if title else "",
            company.get_text(strip=True) if company else "",
            location.get_text(strip=True) if location else "",
            salary.get_text(strip=True) if salary else "",
        ))
    return results


def scrape(delay: float = 2.0) -> list[dict]:
    seen_paths: set[str] = set()
    jobs: list[dict] = []

    with httpx.Client(follow_redirects=True) as client:
        # Collect all unique job paths from search results
        for term in SEARCH_TERMS:
            page = 1
            while True:
                try:
                    resp = client.get(
                        f"{BASE_URL}/jobs/",
                        params={"q": term, "page": page},
                        headers=HEADERS,
                        timeout=15,
                    )
                    resp.raise_for_status()
                except Exception as e:
                    logger.warning("TechJobsForGood fetch error (term=%s page=%d): %s", term, page, e)
                    break

                cards = _cards_from_page(resp.text)
                if not cards:
                    break

                new_cards = [(p, t, c, l, s) for p, t, c, l, s in cards if p not in seen_paths]
                for path, *_ in new_cards:
                    seen_paths.add(path)

                # Fetch detail pages for new cards only
                for path, title, company, location, salary_text in new_cards:
                    schema = _fetch_job_detail(client, path)

                    description = schema.get("description", "")
                    date_posted = schema.get("datePosted")
                    is_telecommute = schema.get("jobLocationType", "") == "TELECOMMUTE"
                    is_remote = 1 if (
                        is_telecommute
                        or re.search(r"\bremote\b", location, re.IGNORECASE)
                    ) else 0

                    sal_min, sal_max = None, None
                    base_sal = schema.get("baseSalary", {})
                    if base_sal:
                        val = base_sal.get("value", {})
                        sal_min = val.get("minValue")
                        sal_max = val.get("maxValue")
                        if sal_min:
                            sal_min = int(sal_min)
                        if sal_max:
                            sal_max = int(sal_max)
                    if sal_min is None:
                        sal_min, sal_max = _parse_salary(salary_text)

                    jobs.append({
                        "source": "techjobsforgood",
                        "external_id": path.strip("/").split("/")[-1],
                        "title": title,
                        "company": company,
                        "location": location,
                        "is_remote": is_remote,
                        "url": f"{BASE_URL}{path}",
                        "description": description,
                        "salary_min": sal_min,
                        "salary_max": sal_max,
                        "date_posted": date_posted,
                        "date_found": datetime.now(timezone.utc).isoformat(),
                    })
                    time.sleep(delay)

                # Stop if we got fewer cards than a full page
                if len(cards) < 30:
                    break
                page += 1
                time.sleep(delay)

    logger.info("TechJobsForGood: %d jobs found", len(jobs))
    return jobs
