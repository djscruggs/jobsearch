"""
Email scraper: polls Gmail for job alert emails and extracts job listings.

Setup:
1. Enable Gmail API in Google Cloud Console
2. Create OAuth 2.0 credentials → download credentials.json
3. Set GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH, GMAIL_JOB_LABEL in .env
4. First run triggers browser OAuth flow; token cached to token.json thereafter

Gmail filter to create manually:
  From: *@linkedin.com OR *@glassdoor.com OR *@indeed.com OR *@ziprecruiter.com
  → Apply label: job-alerts
"""

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PROCESSED_LABEL_NAME = "job-alerts-processed"
SAMPLES_DIR = Path(__file__).parent.parent / "data" / "email_samples"

TECH_KEYWORDS = re.compile(
    r"\b(engineer|developer|software|tech|web|frontend|backend|full.?stack|"
    r"programmer|devops|data|python|javascript|react|node)\b",
    re.IGNORECASE,
)


def _get_gmail_service():
    """Build and return authenticated Gmail API service."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    token_path = os.getenv("GMAIL_TOKEN_PATH", "token.json")

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def _get_or_create_label(service, name: str) -> str:
    """Return label ID for name, creating it if needed."""
    result = service.users().labels().list(userId="me").execute()
    for label in result.get("labels", []):
        if label["name"] == name:
            return label["id"]
    created = service.users().labels().create(
        userId="me", body={"name": name, "labelListVisibility": "labelShow"}
    ).execute()
    return created["id"]


def _get_label_id(service, name: str) -> str | None:
    """Return label ID for name, or None if not found."""
    result = service.users().labels().list(userId="me").execute()
    for label in result.get("labels", []):
        if label["name"] == name:
            return label["id"]
    return None


def _decode_body(msg_data: dict) -> str:
    """Extract HTML (preferred) or plain text body from Gmail message payload."""
    payload = msg_data.get("payload", {})

    def _find_html(part):
        mime = part.get("mimeType", "")
        if mime == "text/html":
            data = part.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
        for sub in part.get("parts", []):
            result = _find_html(sub)
            if result:
                return result
        return ""

    def _find_plain(part):
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
        for sub in part.get("parts", []):
            result = _find_plain(sub)
            if result:
                return result
        return ""

    html = _find_html(payload)
    if html:
        return html
    return _find_plain(payload)


def _sender_domain(msg_data: dict) -> str:
    """Extract sender domain from message headers."""
    headers = msg_data.get("payload", {}).get("headers", [])
    for h in headers:
        if h["name"].lower() == "from":
            match = re.search(r"@([\w.-]+)", h["value"])
            if match:
                return match.group(1).lower()
    return ""


def _parse_linkedin(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    # LinkedIn digest emails contain job cards with title, company, location
    for card in soup.select("table[data-testid='jobCard'], div.jobs-unified-top-card, td.job-card"):
        title_el = card.select_one("a[data-tracking-control-name], h3, strong")
        link_el = card.select_one("a[href*='linkedin.com/jobs']")
        if not title_el or not link_el:
            continue
        title = title_el.get_text(strip=True)
        if not TECH_KEYWORDS.search(title):
            continue
        url = link_el["href"].split("?")[0]  # strip tracking params
        company_el = card.select_one("span.company-name, p.company, span[data-testid='company']")
        location_el = card.select_one("span.job-location, span.location")
        jobs.append({
            "source": "email_linkedin",
            "title": title,
            "company": company_el.get_text(strip=True) if company_el else "Unknown",
            "location": location_el.get_text(strip=True) if location_el else "",
            "url": url,
        })

    # Fallback: any LinkedIn job URLs in the email
    if not jobs:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "linkedin.com/jobs/view/" in href:
                title = a.get_text(strip=True)
                if not title or not TECH_KEYWORDS.search(title):
                    continue
                url = re.sub(r"\?.*", "", href)
                jobs.append({
                    "source": "email_linkedin",
                    "title": title,
                    "company": "Unknown",
                    "location": "",
                    "url": url,
                })

    return jobs


def _parse_glassdoor(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "glassdoor.com/partner/jobListing" not in href and "glassdoor.com/job-listing" not in href:
            continue
        jid_match = re.search(r"jobListingId=(\d+)", href)
        if not jid_match:
            continue
        jid = jid_match.group(1)
        url = f"https://www.glassdoor.com/job-listing/j?jl={jid}"

        # Card text is in sibling elements within the containing td
        card_td = a.parent
        texts = [t.strip() for t in card_td.stripped_strings if t.strip()]

        # Skip malformed cards (e.g. featured ads with "Employment Type" noise)
        if len(texts) < 2 or texts[0].startswith("Employment Type"):
            continue

        # Card layout: [company, title, location, ...] OR [title, location, ...]
        # Distinguish by checking if texts[0] looks like a job title keyword
        if TECH_KEYWORDS.search(texts[0]):
            title, company, location = texts[0], "Unknown", texts[1] if len(texts) > 1 else ""
        else:
            company, title = texts[0], texts[1] if len(texts) > 1 else ""
            location = texts[2] if len(texts) > 2 else ""

        # Strip rating suffix (e.g. "Acme Corp 3.9 ★" → "Acme Corp")
        company = re.sub(r"\s+\d+\.\d+\s*★.*$", "", company).strip()

        if not title or not TECH_KEYWORDS.search(title):
            continue

        jobs.append({
            "source": "email_glassdoor",
            "external_id": jid,
            "title": title,
            "company": company,
            "location": location,
            "url": url,
        })

    return jobs


def _parse_indeed(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "indeed.com/viewjob" not in href and "indeed.com/rc/clk" not in href:
            continue
        title = a.get_text(strip=True)
        if not title or not TECH_KEYWORDS.search(title):
            continue
        # Normalize Indeed URL to stable viewjob form
        jk_match = re.search(r"jk=([a-f0-9]+)", href)
        url = f"https://www.indeed.com/viewjob?jk={jk_match.group(1)}" if jk_match else href
        parent = a.find_parent(["td", "div", "li", "tr"])
        company = "Unknown"
        location = ""
        if parent:
            text_nodes = [t.strip() for t in parent.stripped_strings if t.strip() != title]
            if text_nodes:
                company = text_nodes[0]
            if len(text_nodes) > 1:
                location = text_nodes[1]
        jobs.append({
            "source": "email_indeed",
            "title": title,
            "company": company,
            "location": location,
            "url": url,
        })

    return jobs


def _parse_ziprecruiter(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "ziprecruiter.com/jobs/" not in href and "ziprecruiter.com/c/" not in href:
            continue
        title = a.get_text(strip=True)
        if not title or not TECH_KEYWORDS.search(title):
            continue
        url = re.sub(r"\?.*", "", href)
        parent = a.find_parent(["td", "div", "li", "tr"])
        company = "Unknown"
        location = ""
        if parent:
            text_nodes = [t.strip() for t in parent.stripped_strings if t.strip() != title]
            if text_nodes:
                company = text_nodes[0]
            if len(text_nodes) > 1:
                location = text_nodes[1]
        jobs.append({
            "source": "email_ziprecruiter",
            "title": title,
            "company": company,
            "location": location,
            "url": url,
        })

    return jobs


DOMAIN_PARSERS = {
    "linkedin.com": _parse_linkedin,
    "glassdoor.com": _parse_glassdoor,
    "indeed.com": _parse_indeed,
    "ziprecruiter.com": _parse_ziprecruiter,
}


def _save_sample(domain: str, msg_id: str, html: str):
    """Save HTML for unknown domains — one sample per domain for debugging/training."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    safe_domain = re.sub(r"[^\w.-]", "_", domain)
    path = SAMPLES_DIR / f"{safe_domain}_{msg_id}.html"
    if not path.exists():
        path.write_text(html)
        logger.info("Saved unknown email sample: %s", path)


def _parse_with_claude(html: str, domain: str) -> list[dict]:
    """Use Claude Haiku to extract jobs from an unrecognized email format."""
    from anthropic import Anthropic
    client = Anthropic()

    prompt = f"""Extract job listings from this HTML email from {domain}.
Return ONLY a JSON array of objects with these fields:
- title (string, required)
- company (string, required, "Unknown" if not found)
- location (string, "" if not found)
- url (string, required - the apply/view link)

Rules:
- Only include software engineering / tech roles
- Skip non-tech roles entirely
- If no tech jobs found, return []
- Return ONLY the JSON array, no explanation

HTML:
{html[:30000]}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            return []
        jobs = json.loads(match.group())
        source = f"email_{re.sub(r'[^\w]', '_', domain.split('.')[0])}"
        for job in jobs:
            job["source"] = source
        logger.info("Claude extracted %d jobs from %s", len(jobs), domain)
        return jobs
    except Exception as e:
        logger.warning("Claude fallback failed for %s: %s", domain, e)
        return []


def _parse_email(html: str, domain: str, msg_id: str = "") -> list[dict]:
    for key, parser in DOMAIN_PARSERS.items():
        if key in domain:
            return parser(html)

    # Unknown domain — save sample and try Claude fallback
    _save_sample(domain, msg_id, html)
    if os.getenv("ANTHROPIC_API_KEY"):
        logger.info("Unknown domain %s — trying Claude fallback parser", domain)
        return _parse_with_claude(html, domain)

    logger.warning("Unknown email domain %s — no parser, no API key, skipping", domain)
    return []


def scrape() -> list[dict]:
    """Poll Gmail for unprocessed job alert emails and extract job listings."""
    credentials_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    if not os.path.exists(credentials_path):
        logger.info("Gmail credentials not found at %s — skipping email scraper", credentials_path)
        return []

    label_name = os.getenv("GMAIL_JOB_LABEL", "job-alerts")

    try:
        service = _get_gmail_service()
    except Exception as e:
        logger.warning("Gmail auth failed: %s", e)
        return []

    job_label_id = _get_label_id(service, label_name)
    if not job_label_id:
        logger.warning("Gmail label '%s' not found — create it and set up a filter", label_name)
        return []

    processed_label_id = _get_or_create_label(service, PROCESSED_LABEL_NAME)

    # Fetch unprocessed messages: has job-alerts label but NOT job-alerts-processed
    query = f"label:{label_name} -label:{PROCESSED_LABEL_NAME}"
    result = service.users().messages().list(userId="me", q=query, maxResults=100).execute()
    messages = result.get("messages", [])
    logger.info("Email scraper: %d unprocessed messages found", len(messages))

    all_jobs: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        try:
            msg_data = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            domain = _sender_domain(msg_data)
            html = _decode_body(msg_data)
            jobs = _parse_email(html, domain, msg_id)

            for job in jobs:
                job.setdefault("external_id", None)
                job.setdefault("is_remote", 1 if re.search(r"\bremote\b", job.get("location", ""), re.IGNORECASE) else 0)
                job.setdefault("description", "")
                job.setdefault("salary_min", None)
                job.setdefault("salary_max", None)
                job.setdefault("date_posted", None)
                job["date_found"] = now

            all_jobs.extend(jobs)
            logger.debug("Message %s (%s): %d jobs extracted", msg_id, domain, len(jobs))

            # Mark as processed
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [processed_label_id]},
            ).execute()

        except Exception as e:
            logger.warning("Failed to process message %s: %s", msg_id, e)

    logger.info("Email scraper: %d total jobs extracted", len(all_jobs))
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    jobs = scrape()
    for j in jobs:
        print(f"[{j['source']}] {j['title']} @ {j['company']} — {j['url']}")
