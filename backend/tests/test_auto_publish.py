"""Auto-render-top-clip (stages.py) and auto-package-after-render (renderjob.py): the
zero-click path from clip selection to a TikTok-ready folder."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from clipforge import jobs, renderjob, stages
from clipforge.curate.schema import Clip, HookCheck, Scores
from clipforge.store import Project


def make_clip(
    id: str, rank_score: float, status: Literal["proposed", "approved", "rejected"] = "proposed"
) -> Clip:
    return Clip(
        id=id, start_sentence="S0001", end_sentence="S0002", start=0.0, end=30.0, duration=30.0,
        title="T", hook="H", summary="s", why_it_works="w",
        scores=Scores(hook=1, self_contained=1, single_idea=1, payoff=1, emotion_novelty_utility=1, shareability=1),
        overall=1, signal_score=0, rank_score=rank_score, emphasis_word_indices=[], description="d",
        hashtags=["a"], risk_flags=[], hook_check=HookCheck(passed=True, coverage=1, missing=[]), status=status,
    )  # fmt: skip


def test_auto_render_starts_the_best_ranked_clip(tmp_path: Path, monkeypatch):
    p = Project.create(tmp_path, "proj")
    started: list[tuple[str, dict]] = []
    monkeypatch.setattr(jobs, "start_render", lambda proj, cid, opts: started.append((cid, opts)))

    clips = [make_clip("c001", 0.4), make_clip("c002", 0.9), make_clip("c003", 0.6)]
    stages._auto_render_top(p, clips)

    assert [cid for cid, _ in started] == ["c002"]


def test_auto_render_skips_a_rejected_top_clip(tmp_path: Path, monkeypatch):
    p = Project.create(tmp_path, "proj")
    started: list[str] = []
    monkeypatch.setattr(jobs, "start_render", lambda proj, cid, opts: started.append(cid))

    clips = [make_clip("c001", 0.9, status="rejected"), make_clip("c002", 0.5)]
    stages._auto_render_top(p, clips)

    assert started == []


def test_auto_render_skips_a_clip_already_rendered(tmp_path: Path, monkeypatch):
    p = Project.create(tmp_path, "proj")
    (p.path("clips", "c001")).mkdir(parents=True)
    (p.path("clips", "c001", "out.mp4")).write_bytes(b"x")
    started: list[str] = []
    monkeypatch.setattr(jobs, "start_render", lambda proj, cid, opts: started.append(cid))

    stages._auto_render_top(p, [make_clip("c001", 0.9)])

    assert started == []


def test_auto_render_never_raises_when_start_render_fails(tmp_path: Path, monkeypatch):
    p = Project.create(tmp_path, "proj")

    def boom(proj, cid, opts):
        raise RuntimeError("no fork slots")

    monkeypatch.setattr(jobs, "start_render", boom)
    stages._auto_render_top(p, [make_clip("c001", 0.9)])  # must not raise


def test_auto_package_writes_a_ready_folder_with_tiktok_copy(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    p = Project.create(data_dir / "projects", "proj")
    clip_dir = p.path("clips", "c001")
    clip_dir.mkdir(parents=True)
    (clip_dir / "out.mp4").write_bytes(b"video")

    renderjob._auto_package(p, "c001", make_clip("c001", 0.9))

    dest = data_dir / "exports" / "ready" / "proj-c001"
    assert (dest / "out.mp4").exists()
    assert '"tiktok"' in (dest / "copy.json").read_text()


def test_auto_package_never_raises_if_render_output_is_missing(tmp_path: Path):
    data_dir = tmp_path / "data"
    p = Project.create(data_dir / "projects", "proj")
    renderjob._auto_package(p, "c001", make_clip("c001", 0.9))  # no out.mp4: must not raise
