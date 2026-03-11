"""
Fast Forward (jobs.ffwd.org) scraper — tech nonprofit jobs.
Uses Playwright because the site is a JS-rendered React app.
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

logger = logging.getLogger(__name__)

BASE_URL = "https://jobs.ffwd.org"
# NOTE: jobs.ffwd.org is Cloudflare-protected and returns 403/download for direct requests.
# This scraper uses Playwright to run a real browser session, which passes Cloudflare's
# JS challenge. If it still fails, the site may require additional Cloudflare bypass tools.
JOBS_URL = f"{BASE_URL}/jobs"

TECH_KEYWORDS = re.compile(
    r"\b(engineer|developer|software|tech|web|frontend|backend|full.?stack|"
    r"programmer|devops|data|python|javascript|react|node|cto|vp.?engineering)\b",
    re.IGNORECASE,
)


def _parse_salary(text: str) -> tuple[int | None, int | None]:
    nums = re.findall(r"\$?([\d,]+)k?", text, re.IGNORECASE)
    vals = []
    for n in nums:
        v = int(n.replace(",", ""))
        if v < 1000:
            v *= 1000  # e.g. "150k"
        if v > 20000:  # plausible annual salary
            vals.append(v)
    if len(vals) >= 2:
        return min(vals), max(vals)
    if len(vals) == 1:
        return vals[0], None
    return None, None


async def _scrape_async() -> list[dict]:
    jobs: list[dict] = []

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

        # Intercept API responses to capture job data directly
        api_jobs: list[dict] = []

        async def handle_response(response):
            if "/api/" in response.url and response.status == 200:
                try:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        data = await response.json()
                        if isinstance(data, list) and data and "title" in data[0]:
                            api_jobs.extend(data)
                        elif isinstance(data, dict) and "results" in data:
                            api_jobs.extend(data["results"])
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto(JOBS_URL, wait_until="networkidle", timeout=30000)
        except PWTimeout:
            logger.warning("FastForward: page load timed out, continuing with partial data")

        # Wait for job cards to render
        try:
            await page.wait_for_selector("[class*='job'], [class*='Job'], .listing", timeout=10000)
        except PWTimeout:
            logger.warning("FastForward: job cards did not appear")

        # If API interception caught structured data, use that
        if api_jobs:
            logger.info("FastForward: captured %d jobs from API", len(api_jobs))
            for j in api_jobs:
                if not isinstance(j, dict):
                    continue
                title = j.get("title", "") or j.get("name", "")
                if not TECH_KEYWORDS.search(title):
                    desc = j.get("description", "") or j.get("summary", "")
                    if not TECH_KEYWORDS.search(desc[:300]):
                        continue

                url = j.get("url") or j.get("link") or j.get("job_url", "")
                if not url:
                    slug = j.get("slug") or j.get("id", "")
                    url = f"{BASE_URL}/jobs/{slug}" if slug else ""

                sal_text = str(j.get("salary", "") or j.get("compensation", ""))
                sal_min, sal_max = _parse_salary(sal_text)

                location = j.get("location", "") or j.get("city", "")
                is_remote = 1 if re.search(r"\bremote\b", str(location) + sal_text, re.IGNORECASE) else 0

                jobs.append({
                    "source": "fastforward",
                    "external_id": str(j.get("id", url)),
                    "title": title,
                    "company": j.get("company", "") or j.get("organization", ""),
                    "location": str(location),
                    "is_remote": is_remote,
                    "url": url,
                    "description": j.get("description", "") or j.get("summary", ""),
                    "salary_min": sal_min,
                    "salary_max": sal_max,
                    "date_posted": j.get("date_posted") or j.get("posted_at"),
                    "date_found": datetime.now(timezone.utc).isoformat(),
                })
        else:
            # Fall back to HTML scraping
            html = await page.content()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")

            # Try JSON-LD first
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    if isinstance(data, list):
                        items = data
                    elif data.get("@type") == "ItemList":
                        items = [e.get("item", e) for e in data.get("itemListElement", [])]
                    else:
                        items = [data] if data.get("@type") == "JobPosting" else []

                    for item in items:
                        if item.get("@type") != "JobPosting":
                            continue
                        title = item.get("title", "")
                        if not TECH_KEYWORDS.search(title):
                            continue
                        desc = item.get("description", "")
                        url = item.get("url", "")
                        org = item.get("hiringOrganization", {})
                        location_data = item.get("jobLocation", [{}])
                        if isinstance(location_data, dict):
                            location_data = [location_data]
                        loc = ""
                        if location_data:
                            addr = location_data[0].get("address", {})
                            loc = f"{addr.get('addressLocality', '')}, {addr.get('addressRegion', '')}".strip(", ")

                        sal_data = item.get("baseSalary", {}).get("value", {})
                        sal_min = int(sal_data["minValue"]) if sal_data.get("minValue") else None
                        sal_max = int(sal_data["maxValue"]) if sal_data.get("maxValue") else None
                        is_remote = 1 if item.get("jobLocationType") == "TELECOMMUTE" else 0

                        jobs.append({
                            "source": "fastforward",
                            "external_id": url.split("/")[-1] or url,
                            "title": title,
                            "company": org.get("name", "") if isinstance(org, dict) else "",
                            "location": loc,
                            "is_remote": is_remote,
                            "url": url,
                            "description": desc,
                            "salary_min": sal_min,
                            "salary_max": sal_max,
                            "date_posted": item.get("datePosted"),
                            "date_found": datetime.now(timezone.utc).isoformat(),
                        })
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue

            if not jobs:
                logger.warning(
                    "FastForward: no jobs extracted from HTML. "
                    "Site may have changed structure — manual inspection needed."
                )

        await browser.close()

    logger.info("FastForward: %d tech jobs found", len(jobs))
    return jobs


def scrape() -> list[dict]:
    return asyncio.run(_scrape_async())
