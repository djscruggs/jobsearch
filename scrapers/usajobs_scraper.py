import os
import logging
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://data.usajobs.gov/api/search"


def _get_headers() -> dict:
    key = os.environ.get("USAJOBS_API_KEY", "")
    email = os.environ.get("USAJOBS_EMAIL", "me@djscruggs.com")
    if not key:
        raise RuntimeError("USAJOBS_API_KEY not set")
    return {
        "Host": "data.usajobs.gov",
        "User-Agent": email,
        "Authorization-Key": key,
    }


def _parse_salary(position: dict) -> tuple[int | None, int | None]:
    remuneration = position.get("PositionRemuneration", [])
    if not remuneration:
        return None, None
    r = remuneration[0]
    try:
        low = int(float(r.get("MinimumRange", 0)))
        high = int(float(r.get("MaximumRange", 0)))
        if r.get("RateIntervalCode") == "PA":  # Per Annum
            return low or None, high or None
    except (ValueError, TypeError):
        pass
    return None, None


def scrape(
    keyword: str,
    location: str = "",
    remote: bool = False,
    results_per_page: int = 100,
) -> list[dict]:
    headers = _get_headers()
    params = {
        "Keyword": keyword,
        "ResultsPerPage": results_per_page,
        "Fields": "min",
    }
    if location:
        params["LocationName"] = location
    if remote:
        params["RemoteIndicator"] = "Y"

    try:
        resp = httpx.get(BASE_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("USAJOBS request failed: %s", e)
        return []

    data = resp.json()
    items = data.get("SearchResult", {}).get("SearchResultItems", [])
    jobs = []

    for item in items:
        pos = item.get("MatchedObjectDescriptor", {})
        url = pos.get("PositionURI", "").strip()
        title = pos.get("PositionTitle", "").strip()
        org = pos.get("OrganizationName", "Unknown").strip()

        if not url or not title:
            continue

        locations = pos.get("PositionLocation", [])
        location_str = locations[0].get("LocationName", "") if locations else ""
        is_remote = any(
            "anywhere" in loc.get("LocationName", "").lower() or
            loc.get("Telecommute", "") == "True"
            for loc in locations
        )

        sal_min, sal_max = _parse_salary(pos)

        jobs.append({
            "source": "usajobs",
            "external_id": pos.get("PositionID", ""),
            "title": title,
            "company": org,
            "location": location_str,
            "is_remote": 1 if is_remote else 0,
            "url": url,
            "description": pos.get("UserArea", {}).get("Details", {}).get("JobSummary", ""),
            "salary_min": sal_min,
            "salary_max": sal_max,
            "date_posted": pos.get("PublicationStartDate", None),
            "date_found": datetime.now(timezone.utc).isoformat(),
        })

    logger.info("USAJOBS: '%s' → %d results", keyword, len(jobs))
    return jobs
