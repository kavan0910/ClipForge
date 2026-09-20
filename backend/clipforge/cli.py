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
    clips: int | None = typer.Option(None, "--clips", help="Number of clips (default: auto)"),
    duration: str = typer.Option(
        "20-90", "--duration", help="Clip length range in seconds, e.g. 30-60"
    ),
    preset: str = typer.Option("balanced", "--preset", help="economy | balanced | max"),
    steer: str = typer.Option(
        "", "--steer", help="Free-text steering, e.g. 'find the funny moments'"
    ),
    no_curate: bool = typer.Option(False, "--no-curate", help="Stop after transcription"),
) -> None:
    """Ingest a source and transcribe it (later phases add curation, reframing and rendering)."""
    import secrets

    from clipforge.config import get_settings
    from clipforge.curate.run import CurateParams
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
        if not no_curate and t.words:
            lo, hi = (float(x) for x in duration.split("-"))
            _curate(project, settings, reporter, cancel, CurateParams(
                n_clips=clips, min_duration=lo, max_duration=hi, steering=steer, preset=preset,
                language=language,
            ))  # fmt: skip
    except Cancelled:
        typer.echo("cancelled; run the same command again to resume")
        raise typer.Exit(130) from None
    except ClipforgeError as e:
        typer.echo(typer.style("error: ", fg=typer.colors.RED) + e.message)
        if e.action:
            typer.echo("  fix: " + e.action)
        raise typer.Exit(1) from e


def _curate(project, settings, reporter, cancel, params) -> None:
    from clipforge.stages import curate_project

    if not settings.anthropic_api_key:
        typer.echo(
            typer.style("note: ", fg=typer.colors.YELLOW)
            + "no ANTHROPIC_API_KEY; skipping clip selection"
        )
        return
    out = curate_project(project, settings, reporter, cancel, params)
    typer.echo(f"\n{len(out.clips)} clips (Clip score is a heuristic, not a virality prediction):")
    for c in out.clips:
        typer.echo(f"  {c.id}  {c.start:7.1f}-{c.end:7.1f}s  score {c.rank_score:.2f}  {c.title}")
        typer.echo(f"        hook: {c.hook}")
    if out.rejected:
        typer.echo(f"  ({len(out.rejected)} proposals rejected by hard rules)")
    u = out.usage
    typer.echo(
        f"cost ${u['usd']:.4f}  (in {u['input_tokens']:.0f}, cached-read {u['cache_read_tokens']:.0f}, "
        f"out {u['output_tokens']:.0f} tokens)  -> {project.path('curation', 'clips.json')}"
    )


@app.command()
def curate(
    project_id: str = typer.Argument(..., help="Project id (see ~/Clipforge/projects)"),
    clips: int | None = typer.Option(None, "--clips"),
    duration: str = typer.Option("20-90", "--duration"),
    preset: str = typer.Option("balanced", "--preset"),
    steer: str = typer.Option("", "--steer"),
    mode: str = typer.Option(
        "fresh", "--mode", help="fresh | more_like | shorter | different_topic"
    ),
    like: str | None = typer.Option(None, "--like", help="Clip id for --mode more_like"),
) -> None:
    """(Re-)curate an existing project without redoing transcription or signals."""
    from clipforge.config import get_settings
    from clipforge.curate.run import CurateParams
    from clipforge.errors import ClipforgeError
    from clipforge.pipeline import Reporter
    from clipforge.procs import Cancelled, CancelToken, cancel_on_signals
    from clipforge.store import Project

    settings = get_settings()
    project = Project.open(settings.projects_dir, project_id)
    cancel = CancelToken()
    cancel_on_signals(cancel)
    lo, hi = (float(x) for x in duration.split("-"))
    params = CurateParams(
        n_clips=clips, min_duration=lo, max_duration=hi, steering=steer, preset=preset,
        mode=mode, reference_clip_id=like,  # type: ignore[arg-type]
    )  # fmt: skip
    try:
        _curate(project, settings, Reporter(project, cancel), cancel, params)
    except Cancelled:
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


@app.command(name="eval")
def eval_(
    suite: str = typer.Argument("core", help="Suite name in eval/suites/"),
    preset: str = typer.Option("balanced", "--preset"),
    live: bool = typer.Option(
        False, "--live", help="Call the real API for unrecorded requests (costs money)"
    ),
) -> None:
    """Score curation on cached transcripts. Offline by default: replays recorded LLM responses."""
    import tempfile
    from pathlib import Path

    import anthropic

    from clipforge.config import get_settings
    from clipforge.evalkit.harness import run_suite
    from clipforge.evalkit.replay import CacheMiss

    settings = get_settings()
    real = None
    if live:
        if not settings.anthropic_api_key:
            typer.echo(
                typer.style("error: ", fg=typer.colors.RED) + "--live needs ANTHROPIC_API_KEY"
            )
            raise typer.Exit(1)
        real = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), max_retries=3
        )
    try:
        with tempfile.TemporaryDirectory() as work:
            report = run_suite(suite, settings, preset, Path(work), real)
    except CacheMiss as e:
        typer.echo(typer.style("error: ", fg=typer.colors.RED) + str(e))
        raise typer.Exit(1) from e
    agg = report["aggregate"]
    typer.echo(f"suite {suite}  prompt {report['prompt_version']}  models {report['models']}")
    for k, v in agg.items():
        typer.echo(f"  {k:20s} {v}")
