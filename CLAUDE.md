# Job Hunter - CLAUDE.md

## Development

Always use `uv run` instead of `python` to run scripts. Example: `uv run python main.py --score-only`

## Project Purpose

Autonomous job search and application pipeline for DJ Scruggs (Senior Web Software Engineer). The system scrapes multiple job boards, scores listings against DJ's profile and preferences, generates tailored application materials (cover letters, resume bullets), and maintains a human-in-the-loop review queue. DJ reviews and submits; the agent does everything else.

---

## Candidate Profile

**Name:** DJ Scruggs  
**Role targets:** Senior Software Engineer, Senior Full Stack Engineer, Senior Frontend Engineer, Senior Backend Engineer, Staff Software Engineer, Principal Software Engineer, Senior Web Developer

**Target industries (boost scores):**

- Technology companies and tech-enabled businesses (tech is core to product, not just IT)
- Nonprofits focused on elections, voting rights, democracy, and civic health
- Local and regional journalism / media organizations
- Government - especially state and local; also civic tech orgs (18F, USDS, Code for America)

**Hard disqualifiers (auto-reject, score = 0):**

- Crypto / Web3 / blockchain / NFT
- Gambling / casino / gaming
- Defense / weapons / military contractors
- Surveillance / ad-tech / data brokerage
- Pure body-shop staffing agencies (TEKsystems, Infosys BPO, etc.)
- Junior or mid-level roles only (titles like "Associate Engineer", "Engineer I/II")

**Tech stack (used in scoring - edit in config.yaml):**  
React, TypeScript, Python, Node.js, PostgreSQL, GraphQL, REST APIs, AWS, Docker

**Preferences (configurable in config.yaml):**

- Location: Oklahoma City, OK (also open to remote; check config.yaml)
- Salary floor: set in config.yaml
- Remote preference: set in config.yaml

---

## Architecture

text

`   job-hunter/  ├── CLAUDE.md  ├── requirements.txt  ├── .env  ├── .env.example  ├── config.yaml  ├── main.py  ├── review.py  ├── resume/  │   ├── resume.md               # DJ's full resume in Markdown (MUST FILL IN)  │   └── skills.yaml  ├── templates/  │   ├── cover_letter.md         # Sample cover letter capturing DJ's voice (MUST FILL IN)  │   └── cold_outreach.md        # Short LinkedIn/email cold contact template  ├── agents/  │   ├── __init__.py  │   ├── scraper.py  │   ├── scorer.py  │   ├── tailor.py  │   └── tracker.py  ├── scrapers/  │   ├── __init__.py  │   ├── jobspy_scraper.py  │   ├── usajobs_scraper.py  │   ├── idealist_scraper.py  │   ├── journalismjobs_scraper.py  │   ├── wellfound_scraper.py  │   └── builtin_scraper.py  ├── utils/  │   ├── __init__.py  │   ├── db.py  │   ├── claude_client.py  │   └── export.py  └── data/      ├── jobs.db      ├── api_calls.log      └── output/          ├── cover_letters/          └── tailored_resumes/   `

---

## Database Schema

Auto-created in `utils/db.py` on first run.

sql

`   CREATE TABLE IF NOT EXISTS jobs (      id INTEGER PRIMARY KEY AUTOINCREMENT,      source TEXT NOT NULL,      external_id TEXT,      title TEXT NOT NULL,      company TEXT NOT NULL,      location TEXT,      is_remote INTEGER DEFAULT 0,      url TEXT UNIQUE NOT NULL,      description TEXT,      salary_min INTEGER,      salary_max INTEGER,      date_posted TEXT,      date_found TEXT NOT NULL,      score INTEGER,      score_breakdown TEXT,      score_reason TEXT,      highlights TEXT,      concerns TEXT,      auto_disqualified INTEGER DEFAULT 0,      disqualify_reason TEXT,      status TEXT DEFAULT 'new',      cover_letter_path TEXT,      tailored_bullets TEXT,      cold_outreach TEXT,      applied_at TEXT,      notes TEXT  );  CREATE TABLE IF NOT EXISTS searches (      id INTEGER PRIMARY KEY AUTOINCREMENT,      query TEXT,      location TEXT,      source TEXT,      run_at TEXT NOT NULL,      jobs_found INTEGER DEFAULT 0,      jobs_new INTEGER DEFAULT 0  );  CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);  CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);  CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);   `

**Status field values:** `new | scored | queued | reviewing | applied | rejected | interview | offer | pass`

---

## Agent: Scraper (`agents/scraper.py`)

Orchestrates all scrapers. Deduplicates by URL first, then by normalized (company_slug + title_slug) pair. Saves all results to DB. Never delete records - use status field.

**Search terms to cycle through:**

python

`   SEARCH_TERMS = [      "senior software engineer",      "senior full stack engineer",      "senior frontend engineer",      "senior backend engineer",      "staff software engineer",      "principal software engineer",      "senior web developer",      '"senior engineer" civic',      '"senior software" nonprofit',      '"senior engineer" government',      '"senior engineer" journalism',      '"senior engineer" media',  ]   `

### JobSpy scraper (`scrapers/jobspy_scraper.py`)

- pip package name is `python-jobspy`, import as `from jobspy import scrape_jobs`
- Python >= 3.10 required
- Sites: `["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"]`
- Set `linkedin_fetch_description=True` to get full descriptions (slower, more requests)
- LinkedIn rate-limits aggressively around page 10. Add 10-15s delay between LinkedIn calls. Use `proxies` param if hitting 429s. Indeed has no rate limiting.
- Run once with `is_remote=True`, then again without for local results
- Use `hours_old` from config.yaml (default 72)
- Results are a pandas DataFrame - map columns to job schema before DB insert
- Set `enforce_annual_salary=True` to normalize salary data

### USAJOBS scraper (`scrapers/usajobs_scraper.py`)

- API docs: [https://developer.usajobs.gov/api-reference/](https://developer.usajobs.gov/api-reference/)
- Endpoint: `GET https://data.usajobs.gov/api/search`
- Required headers: `Host`, `User-Agent` (must include contact email per their TOS), `Authorization-Key`
- Key query params: `Keyword`, `LocationName`, `RemoteIndicator`, `ResultsPerPage` (max 500)
- Get free API key at [https://developer.usajobs.gov/](https://developer.usajobs.gov/)
- Note: USAJOBS is federal only. State/local gov jobs are NOT here - use general scrapers for those.

### Idealist scraper (`scrapers/idealist_scraper.py`)

- Idealist blocks simple HTTP scrapers. Use Playwright with `headless=True` + realistic delays.
- Target URL: `https://www.idealist.org/en/jobs?q=software+engineer&type=JOB`
- Add random delays of 2-5s between page loads

### JournalismJobs scraper (`scrapers/journalismjobs_scraper.py`)

- Easiest scraper: they publish an RSS feed at `https://www.journalismjobs.com/rss/1`
- Parse with `feedparser`. Each entry has title, link, summary, published date.
- Full descriptions are in `<content:encoded>`, not `<description>`
- Filter for tech/dev roles by title or summary keyword match

### Wellfound scraper (`scrapers/wellfound_scraper.py`)

- Use Playwright (site is JS-heavy)
- URL: `https://wellfound.com/jobs?role=software-engineer&jobType=fulltime`
- Good source for mission-driven startups in civic tech and media

### Builtin scraper (`scrapers/builtin_scraper.py`)

- More REST-friendly than Wellfound
- Has city-specific variants: `builtinaustin.com`, `builtinnyc.com`, `builtincolorado.com`, etc.
- Good for finding tech-company culture signals

---

## Agent: Scorer (`agents/scorer.py`)

Uses Claude to score unscored jobs in the DB. Batch 5-10 jobs per API call to minimize cost.

**Model:** Use model specified in `config.yaml` (default: `claude-sonnet-4-5`)

**Prompt structure:**

- System prompt: DJ's full profile, target industries, disqualifiers, preferred stack (loaded from `resume/resume.md` + `config.yaml`)
- User message: Batch of job JSON objects (title, company, description, location, salary, is_remote)
- Output: JSON array of score objects, one per job

**Score object schema:**

json

`   {    "url": "https://...",    "score": 78,    "breakdown": {      "level_match": 9,      "industry_fit": 8,      "mission_alignment": 7,      "tech_match": 7,      "remote_match": 9,      "salary_match": 8    },    "auto_disqualify": false,    "disqualify_reason": null,    "highlights": ["Civic mission - election administration", "Remote-first", "Python + React stack"],    "concerns": ["Series A - some risk", "Equity comp unclear"],    "reasoning": "Strong match - nonprofit focused on election tech with exactly DJ's stack..."  }   `

**Score thresholds (from config.yaml):**

- 80+: Auto-queue. Generate materials immediately.
- 50-79: Add to review queue. DJ decides whether to proceed.
- Below 50: Mark `status='pass'`. Archive, don't delete.
- `auto_disqualify=true`: Set `status='rejected'`, `score=0`.

**Cost note:** ~$0.50 per 100 jobs scored with Sonnet. Log all API calls to `data/api_calls.log`.

---

## Agent: Tailor (`agents/tailor.py`)

For jobs with score >= 80 (or DJ-approved from review queue), generates three things:

**1. Cover letter** - saved to `data/output/cover_letters/{company_slug}_{YYYY-MM-DD}.md`

- Must match DJ's voice from `templates/cover_letter.md`
- Structure: hook (why this company/mission specifically), fit (relevant skills), proof (one concrete achievement), close
- Reference at least 2 specific details from the job description
- If you can't find 2+ specific things to reference, flag it rather than writing something generic

**2. Tailored resume bullets** - stored as JSON array in `jobs.tailored_bullets`

- Rewrite 3-5 experience bullets to mirror the JD's language and priorities
- Do not fabricate experience - only rephrase/reframe real experience from `resume/resume.md`

**3. Cold outreach message** - stored in `jobs.cold_outreach`

- 4-5 sentences max for LinkedIn InMail or email
- Use template in `templates/cold_outreach.md` for tone reference

---

## Review Queue (`review.py`)

Interactive terminal UI using `rich`. Shows one job at a time from status='queued' pool.

**Display per job:**

- Title, Company, Location, Remote flag, Score, Salary range, Date posted
- Score breakdown table (level, industry, mission, tech, remote, salary)
- Highlights and concerns
- Cover letter preview (first 300 chars, with option to expand)
- Direct URL to job posting

**Actions:**

- `[a]` Approve - set `status='reviewing'`, open job URL in browser
- `[s]` Skip - set `status='pass'`
- `[e]` Edit notes - add a note to the DB record
- `[v]` View full cover letter in terminal pager
- `[r]` Regenerate cover letter (re-runs tailor agent for this job)
- `[q]` Quit

---

## Running the Pipeline

bash

`   # Initial setup  pip install -r requirements.txt  playwright install chromium  cp .env.example .env          # then fill in keys  # Edit config.yaml  # Add resume to resume/resume.md  # Add voice sample to templates/cover_letter.md  # Full pipeline (scrape + score + generate materials)  python main.py  # Individual stages  python main.py --scrape-only  python main.py --score-only  python main.py --tailor-only  # Review queue  python review.py  # Export all jobs to CSV  python main.py --export csv  # Daemon mode (runs every N hours, set in config.yaml)  python main.py --daemon  # Stats summary  python main.py --stats   `

---

## Key Design Decisions

1. **No auto-apply.** Claude queues and prepares; DJ reviews and submits manually. Quality over volume. Also avoids bot-detection bans from job boards.
    
2. **SQLite, not CSV.** Simple, portable, no server needed. CSV export available for external use.
    
3. **Batched scoring.** 5-10 jobs per Claude API call. Cheaper and faster than one-at-a-time.
    
4. **Niche boards are non-negotiable.** Civic tech, elections, and journalism roles rarely appear on LinkedIn/Indeed. Custom scrapers for Idealist, JournalismJobs, USAJOBS are essential for DJ's target mix.
    
5. **Never delete records.** Use the status field. Rejected/passed jobs are useful for later analysis and avoiding re-scraping.
    
6. **Templates preserve voice.** `cover_letter.md` is authoritative for tone. Claude fills in specifics; it never restructures from scratch.
    
7. **Dedup aggressively.** The same job appears on multiple boards constantly. Deduplicate by URL first, then by (company_slug, title_slug) pair. Keep first-seen record; log the duplicate source.
    
8. **Rate limiting is real.** LinkedIn 429s quickly. Build in delays. Indeed is fine. Playwright scrapers need realistic random delays (2-7s) to avoid blocks.
    

---

## Niche Job Board Reference

|Board|Target Audience|Method|Notes|
|---|---|---|---|
|LinkedIn|General tech|JobSpy|Rate-limits; add delays|
|Indeed|General|JobSpy|Best scraper, no rate limit|
|Glassdoor|General|JobSpy|Use `country_indeed='USA'`|
|Google Jobs|Aggregator|JobSpy|Test `google_search_term` carefully|
|ZipRecruiter|General|JobSpy|US only|
|USAJOBS.gov|Federal government|REST API|Free API key required|
|Idealist.org|Nonprofits|Playwright|Blocks simple HTTP|
|JournalismJobs.com|Media|RSS feed|Use feedparser|
|Wellfound|Tech startups|Playwright|Good for civic tech startups|
|Built In|Tech by city|HTTP|Has city variants|
|INN Job Board|Nonprofit media|HTTP|inn.org/jobs|

---

## Required Setup Checklist

- Fill in `resume/resume.md` with full resume
- Fill in `templates/cover_letter.md` with a past cover letter that captures DJ's voice
- Fill in `templates/cold_outreach.md` with a short outreach message sample
- Set `ANTHROPIC_API_KEY` in `.env`
- Set `USAJOBS_API_KEY` in `.env` (free from [https://developer.usajobs.gov/](https://developer.usajobs.gov/))
- Edit `config.yaml`: location, salary_floor, remote_preference, preferred_stack, locations list
- Run `playwright install chromium`

---

## Gotchas and Edge Cases

- JobSpy pip package name is `python-jobspy`, import is `from jobspy import scrape_jobs`
- LinkedIn needs `linkedin_fetch_description=True` for full text - significantly more requests
- USAJOBS `User-Agent` header must include a real contact email (their TOS requirement)
- Idealist pagination is JS-driven - use Playwright scroll/click, not URL param increments
- JournalismJobs full descriptions are in `<content:encoded>` tag, not `<description>`
- Don't heavily penalize jobs with no salary listed - many good mission-driven orgs don't post it
- Wellfound requires login for some data - scrape public search results only
- State/local government jobs are NOT on USAJOBS (federal only). For Oklahoma state jobs specifically, scrape `https://oklahoma.gov/careers.html` separately if targeting local gov.
- Google Jobs via JobSpy requires specific `google_search_term` format - test manually first before automating

---

---

# requirements.txt

text

`   # Core  anthropic>=0.49.0  python-jobspy>=1.1.79  # Data  pandas>=2.2.0  pydantic>=2.6.0  openpyxl>=3.1.2  # Web scraping  httpx>=0.27.0  beautifulsoup4>=4.12.3  lxml>=5.1.0  playwright>=1.44.0  feedparser>=6.0.11  # CLI and output  rich>=13.7.3  click>=8.1.7  Jinja2>=3.1.4  # Utilities  python-dotenv>=1.0.1  pyyaml>=6.0.1  tenacity>=8.3.0  schedule>=1.2.2   `

---

---

# config.yaml

yaml

`   candidate:    name: "DJ Scruggs"    email: "me@djscruggs.com"    location: "Oklahoma City, OK"    remote_preference: "hybrid"       # "remote", "hybrid", or "onsite"    salary_floor: 130000              # minimum acceptable annual USD - EDIT THIS    preferred_stack:      - React      - TypeScript      - Python      - Node.js      - PostgreSQL      - GraphQL      - AWS      - Docker  search:    hours_old: 72    results_per_query: 30    delay_between_sources: 8          # seconds between scraper calls    locations:      - "Remote"      - "Oklahoma City, OK"      - "Austin, TX"      - "Denver, CO"      - "Washington, DC"    terms:      - "senior software engineer"      - "senior full stack engineer"      - "senior frontend engineer"      - "senior backend engineer"      - "staff software engineer"      - "principal software engineer"      - "senior web developer"  scoring:    model: "claude-sonnet-4-5"    batch_size: 8    auto_queue_threshold: 80    auto_pass_threshold: 50    target_industries:      - civic tech      - elections      - voting rights      - democracy      - government tech      - journalism      - local media      - nonprofit      - public interest      - climate      - education      - healthcare    disqualify_keywords:      - crypto      - web3      - blockchain      - nft      - gambling      - casino      - defense contractor      - weapons      - surveillance      - ad tech      - adtech  pipeline:    max_cover_letters_per_run: 20    daemon_interval_hours: 6   `

---

---

# .env.example

```
ANTHROPIC_API_KEY=sk-ant-...
USAJOBS_API_KEY=your-key-here
USAJOBS_EMAIL=me@djscruggs.com
```