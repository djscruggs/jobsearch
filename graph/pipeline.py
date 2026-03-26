"""LangGraph StateGraph definition for the job-hunter pipeline.

Topology:
  START → route → supervisor → fan_out_scrapers → [parallel scrape_*] → join_scrapers
        → prescreen → score_jobs → tailor_jobs → check_review
        → review_gate (interrupt) → tailor_single (optional regen) → check_review
        → emit_stats → END

run_mode controls which stages execute:
  full         - all stages
  scrape_only  - supervisor + scrape only
  score_only   - prescreen + score only
  tailor_only  - tailor only
  review_only  - review gate only
  daemon       - same as full, called in a loop
"""
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from graph.state import PipelineState
from graph.nodes.supervisor import supervisor_node
from graph.nodes.scrape import (
    fan_out_scrapers,
    join_scrapers,
    scrape_jobspy,
    scrape_journalismjobs,
    scrape_usajobs,
    scrape_techjobsforgood,
    scrape_fastforward,
    scrape_levelsfyi,
    scrape_email,
)
from graph.nodes.prescreen import prescreen_node
from graph.nodes.score import score_jobs_node
from graph.nodes.tailor import tailor_jobs_node, tailor_single_node
from graph.nodes.review import check_review_node, review_gate_node
from graph.nodes.stats import emit_stats_node

import sqlite3
from pathlib import Path

CHECKPOINT_DB = Path(__file__).parent.parent / "data" / "checkpoints.db"


def _route(state: dict) -> str:
    """Entry router — skip stages not needed for this run_mode."""
    mode = state.get("run_mode", "full")
    if mode in ("score_only",):
        return "prescreen"
    if mode == "tailor_only":
        return "tailor_jobs"
    if mode == "review_only":
        return "check_review"
    # full, scrape_only, daemon
    return "supervisor"


def _after_tailor(state: dict) -> str:
    """After tailoring, go to review if full/daemon, else stats."""
    mode = state.get("run_mode", "full")
    if mode in ("tailor_only",):
        return "emit_stats"
    return "check_review"


def _after_score(state: dict) -> str:
    """After scoring, tailor queued jobs (unless scrape_only)."""
    mode = state.get("run_mode", "full")
    if mode in ("scrape_only",):
        return "emit_stats"
    if mode in ("score_only",):
        return "emit_stats"
    return "tailor_jobs"


def _after_join(state: dict) -> str:
    """After scraping join, continue to prescreen (unless scrape_only)."""
    mode = state.get("run_mode", "full")
    if mode == "scrape_only":
        return "emit_stats"
    return "prescreen"


def _check_review_route(state: dict) -> str:
    """Route from check_review: go to review_gate if a job is waiting, else stats."""
    if state.get("review_job") is not None:
        return "review_gate"
    return "emit_stats"


def build_graph(checkpointer=None) -> StateGraph:
    """Build and compile the pipeline StateGraph."""
    builder = StateGraph(PipelineState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    builder.add_node("supervisor", supervisor_node)
    # Scraper nodes — each receives {source: str} from Send dispatch
    builder.add_node("scrape_jobspy", scrape_jobspy)
    builder.add_node("scrape_journalismjobs", scrape_journalismjobs)
    builder.add_node("scrape_usajobs", scrape_usajobs)
    builder.add_node("scrape_techjobsforgood", scrape_techjobsforgood)
    builder.add_node("scrape_fastforward", scrape_fastforward)
    builder.add_node("scrape_levelsfyi", scrape_levelsfyi)
    builder.add_node("scrape_email", scrape_email)
    builder.add_node("join_scrapers", join_scrapers)
    builder.add_node("prescreen", prescreen_node)
    builder.add_node("score_jobs", score_jobs_node)
    builder.add_node("tailor_jobs", tailor_jobs_node)
    builder.add_node("tailor_single", tailor_single_node)
    builder.add_node("check_review", check_review_node)
    builder.add_node("review_gate", review_gate_node)
    builder.add_node("emit_stats", emit_stats_node)

    # ── Entry ──────────────────────────────────────────────────────────────
    builder.add_conditional_edges(
        START,
        _route,
        {
            "supervisor": "supervisor",
            "prescreen": "prescreen",
            "tailor_jobs": "tailor_jobs",
            "check_review": "check_review",
        },
    )

    # ── Supervisor → parallel scraper fan-out via Send objects ────────────
    # fan_out_scrapers returns list[Send(node_name, state)] — LangGraph
    # dispatches each Send as an independent parallel branch.
    builder.add_conditional_edges(
        "supervisor",
        fan_out_scrapers,
        [
            "scrape_jobspy",
            "scrape_journalismjobs",
            "scrape_usajobs",
            "scrape_techjobsforgood",
            "scrape_fastforward",
            "scrape_levelsfyi",
            "scrape_email",
        ],
    )

    # All scraper branches converge at join_scrapers
    for src in ("jobspy", "journalismjobs", "usajobs", "techjobsforgood", "fastforward", "levelsfyi", "email"):
        builder.add_edge(f"scrape_{src}", "join_scrapers")

    # ── After join: prescreen or stats ─────────────────────────────────────
    builder.add_conditional_edges(
        "join_scrapers",
        _after_join,
        {"prescreen": "prescreen", "emit_stats": "emit_stats"},
    )

    # ── Linear scoring/tailor chain ────────────────────────────────────────
    builder.add_edge("prescreen", "score_jobs")
    builder.add_conditional_edges(
        "score_jobs",
        _after_score,
        {"tailor_jobs": "tailor_jobs", "emit_stats": "emit_stats"},
    )
    builder.add_conditional_edges(
        "tailor_jobs",
        _after_tailor,
        {"check_review": "check_review", "emit_stats": "emit_stats"},
    )

    # ── Review loop ────────────────────────────────────────────────────────
    builder.add_conditional_edges(
        "check_review",
        _check_review_route,
        {"review_gate": "review_gate", "emit_stats": "emit_stats"},
    )

    # review_gate returns Command(goto=...) — LangGraph routes automatically
    # tailor_single → back to review_gate
    builder.add_edge("tailor_single", "review_gate")

    # ── End ────────────────────────────────────────────────────────────────
    builder.add_edge("emit_stats", END)

    # ── Checkpointing ──────────────────────────────────────────────────────
    if checkpointer is None:
        CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    return builder.compile(checkpointer=checkpointer)
