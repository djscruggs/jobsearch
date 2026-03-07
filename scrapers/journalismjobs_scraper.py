import logging
import re
from datetime import datetime, timezone

import feedparser

logger = logging.getLogger(__name__)

RSS_URL = "https://www.journalismjobs.com/rss/1"

TECH_KEYWORDS = re.compile(
    r"\b(engineer|developer|software|tech|web|frontend|backend|full.?stack|"
    r"programmer|devops|data|python|javascript|react|node)\b",
    re.IGNORECASE,
)


def scrape() -> list[dict]:
    feed = feedparser.parse(RSS_URL)
    jobs = []

    for entry in feed.entries:
        title = entry.get("title", "").strip()
        # Full description is in content:encoded
        content = ""
        if hasattr(entry, "content") and entry.content:
            content = entry.content[0].get("value", "")
        if not content:
            content = entry.get("summary", "")

        if not TECH_KEYWORDS.search(title) and not TECH_KEYWORDS.search(content[:500]):
            continue

        url = entry.get("link", "").strip()
        if not url:
            continue

        pub = entry.get("published", "")

        jobs.append({
            "source": "journalismjobs",
            "external_id": entry.get("id", url),
            "title": title,
            "company": _extract_company(entry),
            "location": "",
            "is_remote": 1 if re.search(r"\bremote\b", content, re.IGNORECASE) else 0,
            "url": url,
            "description": content,
            "salary_min": None,
            "salary_max": None,
            "date_posted": pub or None,
            "date_found": datetime.now(timezone.utc).isoformat(),
        })

    logger.info("JournalismJobs: %d tech jobs found", len(jobs))
    return jobs


def _extract_company(entry) -> str:
    # Try author field, else parse from title "Role at Company"
    author = entry.get("author", "").strip()
    if author:
        return author
    title = entry.get("title", "")
    if " at " in title:
        return title.split(" at ", 1)[1].strip()
    return "Unknown"
