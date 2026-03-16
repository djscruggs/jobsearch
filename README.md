# Job Hunter

An autonomous job search pipeline for senior software engineers. Scrapes multiple job boards, uses Claude to score and filter listings against your profile, then generates tailored application materials for the jobs worth pursuing. You review and submit; the pipeline does everything else.

## How it works

```
Scrape → Score → Tailor → Review → Apply
```

1. **Scrape** — pulls listings from LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs, USAJOBS, JournalismJobs, TechJobsForGood, Fast Forward, and Gmail job alert emails
2. **Score** — Claude reads each job description and scores it against your profile (0–100), with automatic disqualification for hard no's
3. **Tailor** — for high-scoring jobs, Claude generates a cover letter, rewritten resume bullets, and a cold outreach message
4. **Review** — terminal UI to approve, skip, or edit jobs in your queue
5. **Apply** — you open the URL and submit manually

## How Claude scores jobs

This is the core of the system. The scorer (`agents/scorer.py`) sends batches of 8 jobs at a time to Claude with a system prompt containing your full resume, location preference, salary floor, target industries, and hard disqualifiers.

**Six scoring dimensions (each 0–10):**

| Dimension | What it measures |
|-----------|-----------------|
| `level_match` | Does the seniority match Senior/Staff/Principal? |
| `industry_fit` | Is the company in a target industry (civic tech, journalism, nonprofit, etc.)? |
| `mission_alignment` | Elections, voting rights, democracy, public interest, climate, education? |
| `tech_match` | Does the stack overlap with your preferred tools (React, TypeScript, Python, etc.)? |
| `remote_match` | Does the remote/hybrid/onsite arrangement match your preference? |
| `salary_match` | Does the listed range clear your floor? Missing salary scores 7 — not penalized heavily. |

Final score = weighted average × 10, producing a 0–100 value.

**Hard disqualifiers** (score forced to 0, status set to `rejected`): crypto/web3/blockchain, gambling, defense contractors, surveillance/adtech, pure staffing agencies, junior-only roles.

**Age penalty**: listings older than 3 days lose up to 30% of their score (linear decay, capped at 14+ days). A concern note is added automatically.

**What Claude returns per job:**

```json
{
  "url": "https://...",
  "score": 78,
  "breakdown": {
    "level_match": 9,
    "industry_fit": 8,
    "mission_alignment": 7,
    "tech_match": 7,
    "remote_match": 9,
    "salary_match": 8
  },
  "auto_disqualify": false,
  "disqualify_reason": null,
  "highlights": ["Civic mission - election administration", "Remote-first", "Python + React stack"],
  "concerns": ["Series A - some risk", "Equity comp unclear"],
  "reasoning": "Strong match - nonprofit focused on election tech with exactly DJ's stack..."
}
```

**Score thresholds** (configured in `config.yaml`):
- **80+** → auto-queued, materials generated immediately
- **50–79** → added to review queue, you decide
- **< 50** → archived as `pass`, never deleted

## How Claude generates application materials

For queued jobs, the tailor (`agents/tailor.py`) sends the job description alongside your full resume and a voice sample cover letter. Claude generates three things per job:

- **Cover letter** — follows the structure: hook (why this specific company/mission), fit (relevant skills), proof (one concrete achievement), close. Must reference at least 2 specific details from the JD; flags if it can't find them. Never fabricates experience.
- **Tailored resume bullets** — rewrites 3–5 existing bullets from your resume to mirror the JD's language and priorities. No invented accomplishments.
- **Cold outreach message** — 4–5 sentences for LinkedIn InMail or email.

Cover letters are saved to `data/output/cover_letters/` as Markdown files.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env   # add ANTHROPIC_API_KEY, USAJOBS_API_KEY
# Edit config.yaml: salary_floor, remote_preference, locations, preferred_stack
# Add resume/resume.md (your full resume)
# Add templates/cover_letter.md (a past cover letter capturing your voice)
# Add templates/cold_outreach.md (a short outreach message sample)
```

## Usage

```bash
# Full pipeline (scrape + score + generate materials)
uv run python main.py

# Individual stages
uv run python main.py --scrape-only
uv run python main.py --score-only
uv run python main.py --tailor-only

# Interactive review queue
uv run python review.py

# Daemon mode (runs every N hours, set in config.yaml)
uv run python main.py --daemon

# Stats
uv run python main.py --stats

# Export to CSV
uv run python main.py --export csv
```

## Review queue actions

```
[a] Approve   — set status=reviewing, open job URL in browser
[s] Skip      — set status=pass
[e] Edit      — add a note to the DB record
[v] View      — read full cover letter in terminal pager
[r] Regen     — regenerate cover letter for this job
[q] Quit
```

## Job sources

| Source | Type | Notes |
|--------|------|-------|
| LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs | JobSpy | LinkedIn rate-limits; add delays |
| USAJOBS | REST API | Federal only; free API key required |
| JournalismJobs | RSS feed | Tech/dev roles in media |
| TechJobsForGood | HTTP scraper | Mission-driven tech orgs |
| Fast Forward | HTTP scraper | Nonprofit tech accelerator job board |
| Gmail | Email scraper | Parses job alert emails via Gmail API |

## Configuration

Edit `config.yaml` to set:
- `candidate.salary_floor` — minimum acceptable annual salary
- `candidate.remote_preference` — `"remote"`, `"hybrid"`, or `"onsite"`
- `scoring.auto_queue_threshold` — score above which materials are generated automatically (default 80)
- `scoring.auto_pass_threshold` — score below which jobs are archived (default 50)
- `scoring.target_industries` — industries that get a scoring boost
- `scoring.disqualify_keywords` — keywords that trigger auto-rejection

## Data

All jobs stored in SQLite at `data/jobs.db`. Records are never deleted — use the `status` field to filter. Status values: `new | scored | queued | reviewing | applied | rejected | interview | offer | pass`.

API usage logged to `data/api_calls.log`. At ~$0.50 per 100 jobs scored with Claude Sonnet, a typical run costs well under $1.
