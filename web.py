"""Web UI for job-hunter review queue."""
import json
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from utils.db import get_jobs, get_job_by_id, get_stats, update_job_field, init_db

init_db()

app = FastAPI(title="Job Hunter")
TEMPLATES_DIR = Path(__file__).parent / "web_templates"
TEMPLATES_DIR.mkdir(exist_ok=True)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

COVER_LETTERS_DIR = Path(__file__).parent / "data" / "output" / "cover_letters"

STATUS_LABELS = {
    "new": ("New", "#6b7280"),
    "scored": ("Scored", "#3b82f6"),
    "queued": ("Queued", "#8b5cf6"),
    "reviewing": ("Reviewing", "#f59e0b"),
    "applied": ("Applied", "#10b981"),
    "pass": ("Pass", "#6b7280"),
    "rejected": ("Rejected", "#ef4444"),
    "interview": ("Interview", "#06b6d4"),
    "offer": ("Offer", "#22c55e"),
}


def _parse_json_field(value) -> list | dict:
    if not value:
        return []
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return []


def _enrich(job: dict) -> dict:
    job["highlights"] = _parse_json_field(job.get("highlights"))
    job["concerns"] = _parse_json_field(job.get("concerns"))
    job["score_breakdown"] = _parse_json_field(job.get("score_breakdown"))
    job["tailored_bullets"] = _parse_json_field(job.get("tailored_bullets"))

    label, color = STATUS_LABELS.get(job.get("status", "new"), ("Unknown", "#6b7280"))
    job["status_label"] = label
    job["status_color"] = color

    # Load cover letter content if file exists
    cl_path = job.get("cover_letter_path")
    if cl_path and Path(cl_path).exists():
        job["cover_letter_text"] = Path(cl_path).read_text()
    else:
        job["cover_letter_text"] = None

    return job


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = get_stats()
    return templates.TemplateResponse("index.html", {"request": request, "stats": stats, "status_labels": STATUS_LABELS})


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(
    request: Request,
    status: str = "queued,scored,reviewing",
    search: str = "",
    min_score: int | None = None,
    page: int = 1,
):
    limit = 25
    offset = (page - 1) * limit
    rows, total = get_jobs(
        status=status or None,
        min_score=min_score,
        search=search or None,
        limit=limit,
        offset=offset,
    )
    jobs = [_enrich(j) for j in rows]
    pages = max(1, (total + limit - 1) // limit)
    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": jobs,
        "total": total,
        "page": page,
        "pages": pages,
        "status": status,
        "search": search,
        "min_score": min_score or "",
        "status_labels": STATUS_LABELS,
    })


@app.get("/api/jobs/{job_id}/neighbors")
async def job_neighbors(
    job_id: int,
    status: str = "queued,scored,reviewing",
    search: str = "",
    min_score: int | None = None,
):
    rows, _ = get_jobs(status=status or None, min_score=min_score, search=search or None, limit=10000)
    ids = [r["id"] for r in rows]
    if job_id not in ids:
        return JSONResponse({"prev": None, "next": None})
    idx = ids.index(job_id)
    return JSONResponse({
        "prev": ids[idx - 1] if idx > 0 else None,
        "next": ids[idx + 1] if idx < len(ids) - 1 else None,
    })


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_detail(
    request: Request,
    job_id: int,
    status: str = "queued,scored,reviewing",
    search: str = "",
    min_score: int | None = None,
):
    job = get_job_by_id(job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    job = _enrich(job)
    # Build back URL preserving filter context
    params = f"status={status}"
    if search:
        params += f"&search={search}"
    if min_score:
        params += f"&min_score={min_score}"
    back_url = f"/jobs?{params}"
    return templates.TemplateResponse("job_detail.html", {
        "request": request,
        "job": job,
        "status_labels": STATUS_LABELS,
        "back_url": back_url,
        "filter_params": {"status": status, "search": search, "min_score": min_score or ""},
    })


@app.post("/jobs/{job_id}/status")
async def update_status(job_id: int, status: str = Form(...)):
    job = get_job_by_id(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    update_job_field(job["url"], "status", status)
    return JSONResponse({"ok": True, "status": status})


@app.post("/jobs/bulk-status")
async def bulk_status(request: Request):
    data = await request.json()
    ids = data.get("ids", [])
    status = data.get("status")
    if not ids or not status:
        return JSONResponse({"error": "ids and status required"}, status_code=400)
    updated = 0
    for job_id in ids:
        job = get_job_by_id(job_id)
        if job:
            update_job_field(job["url"], "status", status)
            updated += 1
    return JSONResponse({"ok": True, "updated": updated})


@app.post("/jobs/{job_id}/notes")
async def update_notes(job_id: int, notes: str = Form(...)):
    job = get_job_by_id(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    update_job_field(job["url"], "notes", notes)
    return JSONResponse({"ok": True})


@app.get("/api/stats")
async def api_stats():
    return get_stats()
