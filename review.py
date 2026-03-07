import json
import sys
import webbrowser

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt
from rich import box

from utils.db import init_db, get_queued_jobs, update_job_field

console = Console()


def run():
    init_db()
    jobs = get_queued_jobs(limit=200)

    if not jobs:
        console.print("[yellow]No jobs in the review queue.[/yellow]")
        return

    console.print(f"[bold green]{len(jobs)} jobs in queue[/bold green]\n")

    idx = 0
    while idx < len(jobs):
        job = jobs[idx]
        _display_job(job, idx + 1, len(jobs))

        action = Prompt.ask(
            "\n[bold]\\[a]pprove  \\[s]kip  \\[e]dit notes  \\[v]iew cover letter  "
            "\\[r]egenerate  \\[q]uit[/bold]",
            choices=["a", "s", "e", "v", "r", "q"],
            default="s",
        ).lower()

        if action == "q":
            break
        elif action == "a":
            update_job_field(job["url"], "status", "reviewing")
            webbrowser.open(job["url"])
            console.print("[green]Approved - opening job URL[/green]")
            idx += 1
        elif action == "s":
            update_job_field(job["url"], "status", "pass")
            console.print("[dim]Skipped[/dim]")
            idx += 1
        elif action == "e":
            note = Prompt.ask("Note")
            update_job_field(job["url"], "notes", note)
            console.print("[green]Note saved[/green]")
        elif action == "v":
            _view_cover_letter(job)
        elif action == "r":
            _regenerate(job)
            # Refresh job data
            jobs[idx] = get_queued_jobs(limit=200)[idx] if idx < len(jobs) else job


def _display_job(job: dict, current: int, total: int):
    console.clear()

    breakdown = {}
    if job.get("score_breakdown"):
        try:
            breakdown = json.loads(job["score_breakdown"])
        except (json.JSONDecodeError, TypeError):
            pass

    highlights = []
    concerns = []
    if job.get("highlights"):
        try:
            highlights = json.loads(job["highlights"])
        except (json.JSONDecodeError, TypeError):
            pass
    if job.get("concerns"):
        try:
            concerns = json.loads(job["concerns"])
        except (json.JSONDecodeError, TypeError):
            pass

    salary = "Not listed"
    if job.get("salary_min") or job.get("salary_max"):
        lo = f"${job['salary_min']:,}" if job.get("salary_min") else "?"
        hi = f"${job['salary_max']:,}" if job.get("salary_max") else "?"
        salary = f"{lo} - {hi}"

    header = (
        f"[bold]{job['title']}[/bold]  @  [cyan]{job['company']}[/cyan]\n"
        f"[dim]{job['location']}[/dim]  |  "
        f"{'[green]REMOTE[/green]' if job['is_remote'] else '[dim]On-site[/dim]'}  |  "
        f"Score: [bold yellow]{job['score']}[/bold yellow]  |  "
        f"Salary: {salary}\n"
        f"Posted: {job.get('date_posted', 'unknown')}  |  "
        f"[link={job['url']}]{job['url'][:60]}...[/link]"
    )
    console.print(Panel(header, title=f"Job {current}/{total}", box=box.ROUNDED))

    if breakdown:
        t = Table(box=box.SIMPLE)
        t.add_column("Category")
        t.add_column("Score", justify="right")
        for k, v in breakdown.items():
            t.add_row(k.replace("_", " ").title(), str(v))
        console.print(t)

    if highlights:
        console.print("[green]Highlights:[/green] " + " · ".join(highlights))
    if concerns:
        console.print("[yellow]Concerns:[/yellow] " + " · ".join(concerns))

    if job.get("cover_letter_path"):
        try:
            from pathlib import Path
            text = Path(job["cover_letter_path"]).read_text()
            preview = text[:300] + ("..." if len(text) > 300 else "")
            console.print(Panel(preview, title="Cover Letter Preview", expand=False))
        except (OSError, IOError):
            console.print("[dim](cover letter file not found)[/dim]")
    else:
        console.print("[dim](no cover letter generated yet)[/dim]")


def _view_cover_letter(job: dict):
    path = job.get("cover_letter_path")
    if not path:
        console.print("[yellow]No cover letter for this job.[/yellow]")
        return
    try:
        from pathlib import Path
        text = Path(path).read_text()
        console.print(Panel(text, title="Full Cover Letter"))
    except (OSError, IOError):
        console.print("[red]Could not read cover letter file.[/red]")
    Prompt.ask("[dim]Press Enter to continue[/dim]", default="")


def _regenerate(job: dict):
    console.print("[cyan]Regenerating cover letter...[/cyan]")
    try:
        from agents.tailor import tailor_one
        path = tailor_one(job)
        console.print(f"[green]Regenerated: {path}[/green]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    run()
