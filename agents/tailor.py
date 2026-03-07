import json
import logging
import re
from datetime import date
from pathlib import Path

import yaml

from utils.db import get_queued_jobs, update_job_field
from utils.claude_client import chat

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
RESUME_PATH = Path(__file__).parent.parent / "resume" / "resume.md"
COVER_LETTER_TEMPLATE = Path(__file__).parent.parent / "templates" / "cover_letter.md"
COLD_OUTREACH_TEMPLATE = Path(__file__).parent.parent / "templates" / "cold_outreach.md"
OUTPUT_COVER = Path(__file__).parent.parent / "data" / "output" / "cover_letters"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _read_optional(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _build_system(resume: str, cover_template: str, cold_template: str) -> str:
    return f"""You are a job application assistant for DJ Scruggs, a Senior Software Engineer.

FULL RESUME:
{resume}

COVER LETTER VOICE SAMPLE (match this tone exactly):
{cover_template}

COLD OUTREACH SAMPLE (match this tone):
{cold_template}

Rules:
- NEVER fabricate experience. Only rephrase/reframe real experience from the resume.
- Cover letter structure: hook (why THIS company/mission), fit (relevant skills), proof (one concrete achievement), close.
- Reference at least 2 specific details from the job description. If you cannot find 2+, set cover_letter_flag=true.
- Resume bullets: rewrite 3-5 existing bullets to mirror the JD's language. Don't invent new accomplishments.
- Cold outreach: 4-5 sentences max for LinkedIn InMail or email.

OUTPUT: Return ONLY valid JSON with keys: cover_letter (string), bullets (array of strings), cold_outreach (string), cover_letter_flag (bool)"""


def _build_prompt(job: dict) -> str:
    return (
        f"Generate application materials for this job:\n\n"
        f"Title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Location: {job['location']}\n"
        f"Remote: {bool(job['is_remote'])}\n"
        f"Salary: {job['salary_min']} - {job['salary_max']}\n\n"
        f"JOB DESCRIPTION:\n{(job['description'] or '')[:4000]}\n\n"
        "Return JSON with keys: cover_letter, bullets, cold_outreach, cover_letter_flag"
    )


def _save_cover_letter(job: dict, text: str) -> Path:
    OUTPUT_COVER.mkdir(parents=True, exist_ok=True)
    filename = f"{_slug(job['company'])}_{date.today().isoformat()}.md"
    path = OUTPUT_COVER / filename
    path.write_text(text)
    return path


def run(limit: int | None = None):
    config = _load_config()
    max_cl = config["pipeline"].get("max_cover_letters_per_run", 20)
    model = config["scoring"].get("model", "claude-sonnet-4-6")
    if limit is not None:
        max_cl = limit

    resume = _read_optional(RESUME_PATH)
    cover_template = _read_optional(COVER_LETTER_TEMPLATE)
    cold_template = _read_optional(COLD_OUTREACH_TEMPLATE)
    system = _build_system(resume, cover_template, cold_template)

    jobs = get_queued_jobs(limit=max_cl)
    if not jobs:
        logger.info("No queued jobs to tailor.")
        return

    logger.info("Generating materials for %d jobs...", len(jobs))

    for job in jobs:
        if job.get("cover_letter_path"):
            continue  # already tailored

        prompt = _build_prompt(job)
        try:
            raw = chat(system=system, user=prompt, model=model, max_tokens=8192)
            data = _parse_json(raw)
        except Exception as e:
            logger.error("Tailor failed for %s: %s", job["url"], e)
            continue

        cover_path = _save_cover_letter(job, data.get("cover_letter", ""))
        update_job_field(job["url"], "cover_letter_path", str(cover_path))
        update_job_field(job["url"], "tailored_bullets", json.dumps(data.get("bullets", [])))
        update_job_field(job["url"], "cold_outreach", data.get("cold_outreach", ""))

        if data.get("cover_letter_flag"):
            logger.warning("Cover letter flagged (< 2 specifics): %s @ %s",
                           job["title"], job["company"])

        logger.info("Tailored: %s @ %s", job["title"], job["company"])


def tailor_one(job: dict, model: str | None = None):
    """Tailor a single job (for review.py regenerate action)."""
    config = _load_config()
    if not model:
        model = config["scoring"].get("model", "claude-sonnet-4-6")

    resume = _read_optional(RESUME_PATH)
    cover_template = _read_optional(COVER_LETTER_TEMPLATE)
    cold_template = _read_optional(COLD_OUTREACH_TEMPLATE)
    system = _build_system(resume, cover_template, cold_template)
    prompt = _build_prompt(job)

    raw = chat(system=system, user=prompt, model=model, max_tokens=8192)
    data = _parse_json(raw)

    cover_path = _save_cover_letter(job, data.get("cover_letter", ""))
    update_job_field(job["url"], "cover_letter_path", str(cover_path))
    update_job_field(job["url"], "tailored_bullets", json.dumps(data.get("bullets", [])))
    update_job_field(job["url"], "cold_outreach", data.get("cold_outreach", ""))
    return cover_path


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
