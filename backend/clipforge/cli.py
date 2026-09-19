"""Clipforge command line interface."""

import typer

from clipforge.doctor import run_doctor

app = typer.Typer(no_args_is_help=True, help="Clipforge: local-first AI clipping studio.")


@app.callback()
def main() -> None:
    """Clipforge command group (run and eval arrive in later phases)."""


@app.command()
def run(
    source: str = typer.Argument(..., help="URL or path to a video/audio file"),
    language: str | None = typer.Option(None, help="Force the spoken language (e.g. en)"),
    project_id: str | None = typer.Option(None, "--project", help="Reuse or name a project"),
    no_diarize: bool = typer.Option(False, help="Skip speaker diarization"),
) -> None:
    """Ingest a source and transcribe it (later phases add curation, reframing and rendering)."""
    import secrets

    from clipforge.config import get_settings
    from clipforge.errors import ClipforgeError
    from clipforge.pipeline import Reporter, ingest, transcribe
    from clipforge.procs import Cancelled, CancelToken, cancel_on_signals
    from clipforge.providers.path import PathSource
    from clipforge.providers.url import UrlSource
    from clipforge.store import Project

    settings = get_settings()
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    pid = project_id or secrets.token_hex(4)
    project = Project.create(settings.projects_dir, pid)
    cancel = CancelToken()
    cancel_on_signals(cancel)
    reporter = Reporter(project, cancel)
    provider = (
        UrlSource(source, settings.max_source_height, settings.ytdlp_cookies_from_browser)
        if source.startswith(("http://", "https://"))
        else PathSource(source)
    )
    try:
        typer.echo(f"project {pid}: {project.root}")
        src = ingest(project, provider, settings, reporter, cancel)
        for w in src.quality.warnings:
            typer.echo(typer.style("note: ", fg=typer.colors.YELLOW) + w)
        t = transcribe(project, src, settings, reporter, cancel, language, diarize=not no_diarize)
        typer.echo(f"transcript: {len(t.words)} words, {len(t.sentences)} sentences ({t.language})")
    except Cancelled:
        typer.echo("cancelled; run the same command again to resume")
        raise typer.Exit(130) from None
    except ClipforgeError as e:
        typer.echo(typer.style("error: ", fg=typer.colors.RED) + e.message)
        if e.action:
            typer.echo("  fix: " + e.action)
        raise typer.Exit(1) from e


_ICON = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
_COLOR = {"ok": typer.colors.GREEN, "warn": typer.colors.YELLOW, "fail": typer.colors.RED}


@app.command()
def doctor() -> None:
    """Check hardware, ffmpeg, yt-dlp, runtimes, keys and disk."""
    report = run_doctor()
    for c in report.checks:
        typer.echo(typer.style(_ICON[c.status], fg=_COLOR[c.status]) + f" {c.name}: {c.detail}")
        if c.fix and c.status != "ok":
            typer.echo(f"       fix: {c.fix}")
    raise typer.Exit(0 if report.ok else 1)


if __name__ == "__main__":
    app()
