"""Clipforge command line interface."""

import typer

from clipforge.doctor import run_doctor

app = typer.Typer(no_args_is_help=True, help="Clipforge: local-first AI clipping studio.")


@app.callback()
def main() -> None:
    """Clipforge command group (run and eval arrive in later phases)."""


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
