from types import SimpleNamespace
from typing import cast

from clipforge import jobrunner
from clipforge.config import Settings
from clipforge.curate.run import CurateOutcome, CurateParams
from clipforge.pipeline import Reporter
from clipforge.procs import CancelToken
from clipforge.store import Project


def test_empty_initial_curation_retries_for_60_to_120_second_clips(monkeypatch):
    calls = []
    settings_seen = []
    outcomes = [
        SimpleNamespace(clips=[], usage={"usd": 0.4}),
        SimpleNamespace(clips=["long-clip"], usage={"usd": 0.1}),
    ]

    def curate(project, settings, reporter, cancel, params):
        calls.append(params)
        settings_seen.append(settings)
        return cast(CurateOutcome, outcomes.pop(0))

    monkeypatch.setattr(jobrunner, "curate_project", curate)
    reporter = cast(Reporter, SimpleNamespace(progress=lambda *args, **kwargs: None))
    result = jobrunner.curate_with_longer_fallback(
        cast(Project, object()), Settings(), reporter, cast(CancelToken, object()), CurateParams()
    )

    assert result.clips == ["long-clip"]
    assert (calls[0].min_duration, calls[0].max_duration) == (20.0, 90.0)
    assert (calls[1].min_duration, calls[1].max_duration) == (60.0, 120.0)
    assert settings_seen[1].max_job_cost_usd == 0.6


def test_successful_initial_curation_does_not_retry(monkeypatch):
    calls = []

    def curate(project, settings, reporter, cancel, params):
        calls.append(params)
        return cast(CurateOutcome, SimpleNamespace(clips=["clip"]))

    monkeypatch.setattr(jobrunner, "curate_project", curate)
    reporter = cast(Reporter, SimpleNamespace(progress=lambda *args, **kwargs: None))
    result = jobrunner.curate_with_longer_fallback(
        cast(Project, object()), Settings(), reporter, cast(CancelToken, object()), CurateParams()
    )

    assert result.clips == ["clip"]
    assert len(calls) == 1


def test_empty_60_to_120_second_pass_does_not_loop(monkeypatch):
    calls = []

    def curate(project, settings, reporter, cancel, params):
        calls.append(params)
        return cast(CurateOutcome, SimpleNamespace(clips=[]))

    monkeypatch.setattr(jobrunner, "curate_project", curate)
    reporter = cast(Reporter, SimpleNamespace(progress=lambda *args, **kwargs: None))
    result = jobrunner.curate_with_longer_fallback(
        cast(Project, object()),
        Settings(),
        reporter,
        cast(CancelToken, object()),
        CurateParams(min_duration=60, max_duration=120),
    )

    assert result.clips == []
    assert len(calls) == 1


def test_empty_initial_pass_skips_retry_if_cost_cap_was_spent(monkeypatch):
    calls = []

    def curate(project, settings, reporter, cancel, params):
        calls.append(params)
        return cast(CurateOutcome, SimpleNamespace(clips=[], usage={"usd": 1.0}))

    monkeypatch.setattr(jobrunner, "curate_project", curate)
    notes = []
    reporter = cast(
        Reporter, SimpleNamespace(progress=lambda *args, **kwargs: notes.append(kwargs))
    )
    result = jobrunner.curate_with_longer_fallback(
        cast(Project, object()),
        Settings().model_copy(update={"max_job_cost_usd": 1.0}),
        reporter,
        cast(CancelToken, object()),
        CurateParams(),
    )

    assert result.clips == []
    assert len(calls) == 1
    assert "cost cap" in notes[-1]["note"]
