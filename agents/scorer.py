import json
import logging
from pathlib import Path

import yaml

from utils.db import get_unscored_jobs, update_job_score
from utils.claude_client import chat

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
RESUME_PATH = Path(__file__).parent.parent / "resume" / "resume.md"


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _build_system_prompt(config: dict, resume: str) -> str:
    candidate = config["candidate"]
    scoring = config["scoring"]
    return f"""You are a job fit scorer for {candidate['name']}, a Senior Software Engineer.

CANDIDATE PROFILE:
{resume}

PREFERENCES:
- Location: {candidate['location']}
- Remote preference: {candidate['remote_preference']}
- Salary floor: ${candidate['salary_floor']:,}/yr
- Preferred stack: {', '.join(candidate['preferred_stack'])}

TARGET INDUSTRIES (score higher):
{chr(10).join('- ' + i for i in scoring['target_industries'])}

HARD DISQUALIFIERS (set auto_disqualify=true, score=0):
- Crypto / Web3 / blockchain / NFT
- Gambling / casino / gaming
- Defense / weapons / military contractors
- Surveillance / ad-tech / data brokerage
- Pure body-shop staffing agencies
- Junior or mid-level roles only (titles like "Associate Engineer", "Engineer I/II")
- Keywords that trigger disqualification: {', '.join(scoring['disqualify_keywords'])}

SCORING RUBRIC (each sub-score 0-10):
- level_match: Does the seniority match Senior/Staff/Principal?
- industry_fit: Is the industry in target list?
- mission_alignment: Civic tech, elections, journalism, nonprofit, public interest?
- tech_match: Does the stack match candidate's preferred stack?
- remote_match: Does remote/hybrid/onsite match candidate's preference?
- salary_match: Does salary range meet the floor? (if not listed, score 7 - don't penalize heavily)

Final score = weighted average × 10 (0-100).

OUTPUT: Return ONLY a valid JSON array of score objects. No markdown, no explanation outside JSON."""


def _batch_prompt(jobs: list[dict]) -> str:
    job_list = []
    for job in jobs:
        job_list.append({
            "url": job["url"],
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "is_remote": bool(job["is_remote"]),
            "salary_min": job["salary_min"],
            "salary_max": job["salary_max"],
            "description": (job["description"] or "")[:3000],  # cap tokens
        })
    return (
        "Score each of the following jobs. Return a JSON array with one object per job.\n\n"
        "Each object must have:\n"
        '{"url": "...", "score": 0-100, "breakdown": {"level_match": 0-10, '
        '"industry_fit": 0-10, "mission_alignment": 0-10, "tech_match": 0-10, '
        '"remote_match": 0-10, "salary_match": 0-10}, "auto_disqualify": bool, '
        '"disqualify_reason": null or string, "highlights": [...], '
        '"concerns": [...], "reasoning": "..."}\n\n'
        f"JOBS:\n{json.dumps(job_list, indent=2)}"
    )


def run():
    config = _load_config()
    scoring = config["scoring"]
    model = scoring.get("model", "claude-sonnet-4-6")
    batch_size = scoring.get("batch_size", 8)

    resume = RESUME_PATH.read_text() if RESUME_PATH.exists() else ""
    system = _build_system_prompt(config, resume)

    jobs = get_unscored_jobs(limit=200)
    if not jobs:
        logger.info("No unscored jobs.")
        return

    logger.info("Scoring %d jobs in batches of %d...", len(jobs), batch_size)
    scored = 0

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        prompt = _batch_prompt(batch)

        try:
            raw = chat(system=system, user=prompt, model=model)
            results = _parse_json_array(raw)
        except Exception as e:
            logger.error("Scoring batch %d failed: %s", i // batch_size, e)
            continue

        results_by_url = {r["url"]: r for r in results}
        for job in batch:
            result = results_by_url.get(job["url"])
            if result:
                update_job_score(job["url"], result)
                scored += 1
            else:
                logger.warning("No score returned for: %s", job["url"])

    logger.info("Scored %d/%d jobs.", scored, len(jobs))


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    return json.loads(text)
