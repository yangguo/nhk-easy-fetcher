"""Typer CLI for nhk-easy-fetcher."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from nhk_easy_fetcher import __version__
from nhk_easy_fetcher.app import FetchApplication
from nhk_easy_fetcher.client import HttpClient
from nhk_easy_fetcher.config import load_config
from nhk_easy_fetcher.discovery import discover_article_urls
from nhk_easy_fetcher.models import RunSummary
from nhk_easy_fetcher.page_mode import detect_page_mode
from nhk_easy_fetcher.storage import StateStore

app = typer.Typer(
    name="nhk-easy",
    help="Fetch NHK NEWS WEB EASY articles for personal study. "
    "NHK content is for personal use only — do not redistribute.",
    no_args_is_help=True,
)
console = Console(stderr=True)


def _print_summary(summary: RunSummary, *, as_json: bool = False) -> None:
    if as_json:
        payload = {
            "completed": summary.completed,
            "skipped": summary.skipped,
            "authorization_required": summary.authorization_required,
            "partial": summary.partial,
            "failed": summary.failed,
            "results": [result.model_dump() for result in summary.results],
        }
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    table = Table(title="Fetch summary")
    table.add_column("Article ID")
    table.add_column("Status")
    table.add_column("Message")
    for result in summary.results:
        table.add_row(result.article_id or "-", result.status, result.message[:120])
    console.print(table)
    console.print(
        f"completed={summary.completed} skipped={summary.skipped} "
        f"auth_required={summary.authorization_required} partial={summary.partial} "
        f"failed={summary.failed}"
    )


@app.command("fetch")
def fetch_cmd(
    date_arg: Annotated[
        str | None,
        typer.Option("--date", help="YYYY-MM-DD or 'today'"),
    ] = None,
    latest: Annotated[
        bool,
        typer.Option("--latest", help="Fetch the newest article from the sitemap"),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Output directory"),
    ] = None,
    formats: Annotated[
        str,
        typer.Option("--format", help="Comma-separated: markdown,json,text,html"),
    ] = "markdown,json,text",
    audio: Annotated[
        str,
        typer.Option("--audio", help="Audio mode: off, m4a, mp3, manifest"),
    ] = "off",
    max_articles: Annotated[int, typer.Option("--max-articles")] = 1,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    allow_partial: Annotated[bool, typer.Option("--allow-partial")] = False,
    force: Annotated[bool, typer.Option("--force")] = False,
    auth: Annotated[
        str | None,
        typer.Option("--auth", help="Auth provider: none or cookie_jar"),
    ] = None,
    cookie_jar: Annotated[
        Path | None,
        typer.Option("--cookie-jar", help="Path to cookie jar JSON"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Discover and fetch NHK EASY articles."""
    config = load_config()
    if output is not None:
        config.storage.output_dir = output.expanduser().resolve()
    if allow_partial:
        config.fetch.allow_partial = True
    if auth is not None:
        if auth not in {"none", "cookie_jar"}:
            console.print(f"[red]Unknown auth provider: {auth}[/red]")
            raise typer.Exit(2)
        config.auth.provider = auth  # type: ignore[assignment]
    if cookie_jar is not None:
        config.auth.provider = "cookie_jar"
        config.auth.cookie_jar_path = cookie_jar.expanduser().resolve()
    if audio not in {"off", "m4a", "mp3", "manifest"}:
        console.print(f"[red]Unknown audio mode: {audio}[/red]")
        raise typer.Exit(2)
    config.audio.mode = audio  # type: ignore[assignment]

    target_date: date | None = None
    if date_arg == "today":
        from zoneinfo import ZoneInfo

        target_date = date.today()
        if config.fetch.timezone:
            from datetime import datetime

            target_date = datetime.now(ZoneInfo(config.fetch.timezone)).date()
    elif date_arg is not None:
        try:
            target_date = date.fromisoformat(date_arg)
        except ValueError:
            console.print(f"[red]Invalid date: {date_arg}[/red]")
            raise typer.Exit(2) from None

    if not latest and date_arg is None:
        latest = True

    application = FetchApplication(config)
    summary = application.fetch(
        target_date=target_date if not latest else None,
        latest=latest,
        max_articles=max_articles,
        formats=formats,
        dry_run=dry_run,
        force=force,
    )
    _print_summary(summary, as_json=as_json)
    raise typer.Exit(summary.exit_code)


@app.command("fetch-latest")
def fetch_latest_cmd(
    output: Annotated[Path | None, typer.Option("--output")] = None,
    formats: Annotated[str, typer.Option("--format")] = "markdown,json,text",
    audio: Annotated[str, typer.Option("--audio")] = "off",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    auth: Annotated[str | None, typer.Option("--auth")] = None,
    cookie_jar: Annotated[Path | None, typer.Option("--cookie-jar")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fetch the single newest article from the sitemap (alias for fetch --latest)."""
    fetch_cmd(
        date_arg=None,
        latest=True,
        output=output,
        formats=formats,
        audio=audio,
        max_articles=1,
        dry_run=dry_run,
        allow_partial=False,
        force=False,
        auth=auth,
        cookie_jar=cookie_jar,
        as_json=as_json,
    )


@app.command("status")
def status_cmd(
    output: Annotated[Path | None, typer.Option("--output")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show local fetch state."""
    config = load_config()
    out = (output or config.storage.output_dir).expanduser().resolve()
    store = StateStore(out)
    db_path = store.db_path
    if not db_path.exists():
        console.print("No local state yet.")
        raise typer.Exit(0)

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT article_id, content_status, completed_at
            FROM articles
            ORDER BY completed_at DESC
            """
        ).fetchall()

    if as_json:
        payload = [{"article_id": r[0], "status": r[1], "completed_at": r[2]} for r in rows]
        console.print(json.dumps(payload))
    else:
        table = Table(title=f"Articles in {out}")
        table.add_column("Article ID")
        table.add_column("Status")
        table.add_column("Completed")
        for row in rows:
            table.add_row(row[0], row[1], row[2] or "")
        console.print(table)
    raise typer.Exit(0)


@app.command("probe")
def probe_cmd(
    live: Annotated[bool, typer.Option("--live")] = False,
    understand: Annotated[
        bool,
        typer.Option("--i-understand-live-requests", help="Confirm live requests to NHK"),
    ] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Probe sitemap and page mode (offline fixtures by default)."""
    if live:
        if os.environ.get("NHK_EASY_LIVE_TEST") != "1":
            console.print(
                "[red]Live probe disabled. Set NHK_EASY_LIVE_TEST=1 and pass "
                "--i-understand-live-requests.[/red]"
            )
            raise typer.Exit(2)
        if not understand:
            console.print("[red]Pass --i-understand-live-requests to confirm.[/red]")
            raise typer.Exit(2)

        config = load_config()
        with HttpClient(config.fetch) as client:
            sitemap = client.get_text(config.discovery.sitemap_url)
            articles = discover_article_urls(sitemap, tz=config.fetch.timezone)
            if not articles:
                console.print("[red]No articles in sitemap[/red]")
                raise typer.Exit(4)
            latest = articles[0]
            html = client.get_text(latest.source_url)
            mode = detect_page_mode(html)
            result = {
                "sitemap_status": "ok",
                "latest_article_id": latest.article_id,
                "latest_source_url": latest.source_url,
                "page_mode": mode.value,
                "has_article_title": ".article-title" in html or "article-title" in html,
                "has_article_body": "#js-article-body" in html or 'id="js-article-body"' in html,
            }
            if as_json:
                console.print(json.dumps(result, indent=2))
            else:
                for key, value in result.items():
                    console.print(f"{key}: {value}")
            raise typer.Exit(0)

    fixture_dir = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    classic = (fixture_dir / "classic_complete.html").read_text(encoding="utf-8")
    partial = (fixture_dir / "next_partial.html").read_text(encoding="utf-8")
    result = {
        "mode": "offline",
        "classic_fixture_mode": detect_page_mode(classic).value,
        "partial_fixture_mode": detect_page_mode(partial).value,
    }
    if as_json:
        console.print(json.dumps(result, indent=2))
    else:
        classic_mode = result["classic_fixture_mode"]
        partial_mode = result["partial_fixture_mode"]
        console.print(f"offline probe: classic={classic_mode} partial={partial_mode}")
    raise typer.Exit(0)


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=None, is_eager=True),
    ] = None,
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit(0)


def main_entry() -> None:
    app()


if __name__ == "__main__":
    main_entry()
