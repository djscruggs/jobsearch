import logging
import sys

import click
import schedule
import time as time_mod
import yaml
from pathlib import Path
from rich.console import Console
from rich.table import Table

from utils.db import init_db, get_stats
from utils.export import export_csv

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


@click.command()
@click.option("--scrape-only", is_flag=True, help="Only run scrapers")
@click.option("--score-only", is_flag=True, help="Only run scorer")
@click.option("--tailor-only", is_flag=True, help="Only run tailor agent")
@click.option("--export", type=click.Choice(["csv"]), help="Export jobs to format")
@click.option("--stats", is_flag=True, help="Show pipeline stats and exit")
@click.option("--daemon", is_flag=True, help="Run pipeline repeatedly on schedule")
def main(scrape_only, score_only, tailor_only, export, stats, daemon):
    init_db()

    if stats:
        _show_stats()
        return

    if export == "csv":
        export_csv()
        return

    if daemon:
        config = yaml.safe_load(Path("config.yaml").read_text())
        interval = config["pipeline"].get("daemon_interval_hours", 6)
        console.print(f"[bold green]Daemon mode: running every {interval}h[/bold green]")
        _run_pipeline(scrape_only, score_only, tailor_only)
        schedule.every(interval).hours.do(_run_pipeline, scrape_only, score_only, tailor_only)
        while True:
            schedule.run_pending()
            time_mod.sleep(60)
        return

    _run_pipeline(scrape_only, score_only, tailor_only)


def _run_pipeline(scrape_only=False, score_only=False, tailor_only=False):
    run_all = not (scrape_only or score_only or tailor_only)

    if run_all or scrape_only:
        console.print("[bold cyan]Scraping job boards...[/bold cyan]")
        from agents import scraper
        scraper.run()

    if run_all or score_only:
        console.print("[bold cyan]Scoring jobs...[/bold cyan]")
        from agents import scorer
        scorer.run()

    if run_all or tailor_only:
        console.print("[bold cyan]Generating application materials...[/bold cyan]")
        from agents import tailor
        tailor.run()

    console.print("[bold green]Pipeline complete.[/bold green]")
    _show_stats()


def _show_stats():
    s = get_stats()
    console.print(f"\n[bold]Total jobs in DB:[/bold] {s['total']}\n")

    table = Table(title="Jobs by Status")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    for status, count in sorted(s["by_status"].items()):
        table.add_row(status, str(count))
    console.print(table)

    if s["top_jobs"]:
        top = Table(title="Top Scored Jobs")
        top.add_column("Score", justify="right")
        top.add_column("Title")
        top.add_column("Company")
        for job in s["top_jobs"]:
            top.add_row(str(job["score"]), job["title"], job["company"])
        console.print(top)


if __name__ == "__main__":
    main()
