# Plan: Claude Fallback Parser for Unknown Email Domains

## Problem
`email_scraper.py` only has hardcoded parsers for LinkedIn, Glassdoor, Indeed, ZipRecruiter.
Emails from other domains (tagged manually by DJ) are silently dropped.

## Approach: Save samples + Claude fallback

Two-part solution:
1. **Save HTML samples** for any unrecognized domain to `data/email_samples/<domain>_<msg_id>.html`
2. **Claude fallback parser** — for unrecognized domains, call Claude API with the HTML and ask it to extract job listings in the standard schema

### Why Claude fallback over "add a new hardcoded parser":
- Zero config for new sources — just tag the email and it works
- Email HTML structures change frequently; Claude adapts
- Cost: ~$0.01-0.05 per email (Haiku), acceptable for occasional unknown sources

---

## Implementation

### `scrapers/email_scraper.py` changes

**1. Save samples for unknown domains**
```python
SAMPLES_DIR = Path(__file__).parent.parent / "data" / "email_samples"

def _save_sample(domain: str, msg_id: str, html: str):
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    path = SAMPLES_DIR / f"{domain}_{msg_id}.html"
    if not path.exists():  # don't overwrite — one sample per domain is enough
        path.write_text(html)
        logger.info("Saved unknown email sample: %s", path)
```

**2. Claude fallback parser**
```python
def _parse_with_claude(html: str, domain: str) -> list[dict]:
    """Use Claude Haiku to extract jobs from an unrecognized email format."""
    from anthropic import Anthropic
    client = Anthropic()

    # Truncate HTML to avoid huge token counts — first 30k chars is enough
    truncated = html[:30000]

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
{truncated}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    import json, re
    text = response.content[0].text.strip()
    # Extract JSON array from response
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if not match:
        return []
    jobs = json.loads(match.group())
    for job in jobs:
        job["source"] = f"email_{domain.replace('.', '_')}"
    return jobs
```

**3. Update `_parse_email()` to use fallback**
```python
def _parse_email(html: str, domain: str, msg_id: str) -> list[dict]:
    for key, parser in DOMAIN_PARSERS.items():
        if key in domain:
            return parser(html)

    # Unknown domain — save sample and try Claude
    _save_sample(domain, msg_id, html)
    if os.getenv("ANTHROPIC_API_KEY"):
        logger.info("Unknown domain %s — trying Claude fallback parser", domain)
        return _parse_with_claude(html, domain)

    logger.warning("Unknown email domain %s — no parser, no API key, skipping", domain)
    return []
```

**4. Pass `msg_id` through to `_parse_email`** — minor signature change in `scrape()`

---

## Files Changed
- `scrapers/email_scraper.py` — add `_save_sample`, `_parse_with_claude`, update `_parse_email` signature

## No new deps needed
- `anthropic` already in pyproject.toml
- `ANTHROPIC_API_KEY` already in .env

## Verification
1. Tag a job email from an unknown domain with `job-alerts` label in Gmail
2. Run `uv run python scrapers/email_scraper.py`
3. Check `data/email_samples/` — HTML file saved
4. Check output — jobs extracted via Claude
5. Check `source` column in DB — should be `email_<domain>`

## Cost estimate
- Claude Haiku: ~$0.25/MTok input, ~$1.25/MTok output
- 30k chars HTML ≈ ~7k tokens ≈ $0.002 per email
- Negligible for occasional unknown sources
