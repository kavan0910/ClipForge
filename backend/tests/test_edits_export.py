import json

import numpy as np
import pytest

from clipforge import edits as ed
from clipforge import export as ex
from clipforge.curate.schema import Clip, HookCheck, Scores
from clipforge.edl import EDL
from clipforge.models import Word
from clipforge.reframe import plan as pl
from tests.test_reframe import analysis


def transcript(n=120, gap_every=15):
    words, t = [], 10.0
    for i in range(n):
        words.append(
            Word(
                i=i,
                w="um" if i in (30, 61) else f"w{i}",
                start=round(t, 3),
                end=round(t + 0.35, 3),
                prob=1,
            )
        )
        t += 0.4 + (1.2 if i and i % gap_every == 0 else 0.05)
    rms = [-60.0] * int((t + 5) / 0.1)
    for w in words:
        for k in range(int(w.start / 0.1), int(w.end / 0.1) + 1):
            rms[k] = -20.0
    return words, rms, t + 5


def clip_for(words, lo=5, hi=100):
    return Clip(id="c001", start_sentence="S0001", end_sentence="S0002", start=words[lo].start - 0.05, end=words[hi].end + 0.05, duration=1,
                title="T", hook="H", summary="s", why_it_works="w", scores=Scores(hook=1, self_contained=1, single_idea=1, payoff=1, emotion_novelty_utility=1, shareability=1),
                overall=1, signal_score=0, rank_score=0.5, emphasis_word_indices=[], description="d", hashtags=["a"], risk_flags=[], hook_check=HookCheck(passed=True, coverage=1, missing=[]))  # fmt: skip


def no_mid_word(edl, words):
    return all(
        not any(w.start < c < w.end for w in words)
        for s in edl.segments
        for c in (s.src_in, s.src_out)
    )


def test_untouched_edits_reproduce_the_curated_span():
    words, rms, dur = transcript()
    c = clip_for(words)
    edl = ed.build_edl(c, words, ed.ClipEdits(cleanup="off"), rms, dur)
    assert (
        len(edl.segments) == 1
        and edl.segments[0].src_in == c.start
        and edl.segments[0].src_out == c.end
    )


def test_trim_by_words_snaps_into_pauses_and_never_cuts_a_word():
    words, rms, dur = transcript()
    c = clip_for(words)
    e = ed.ClipEdits(cleanup="off", start_word=20, end_word=80)
    edl = ed.build_edl(c, words, e, rms, dur)
    assert no_mid_word(edl, words)
    assert words[19].end <= edl.segments[0].src_in <= words[20].start
    assert words[80].end <= edl.segments[-1].src_out <= words[81].start


def test_text_based_cut_removes_the_selected_words_and_reports_them():
    words, rms, dur = transcript()
    c = clip_for(words)
    e = ed.ClipEdits(cleanup="off", exclude=[(40, 45)])
    edl = ed.build_edl(c, words, e, rms, dur)
    kept = {w.i for w in edl.remap_words(words)}
    assert not kept & set(range(40, 46)) and {39, 46} <= kept
    assert [r.kind for r in edl.removed] == ["manual"] and edl.removed[0].text.startswith("w40")
    assert no_mid_word(edl, words)
    # Edge selections are handled by trimming, not by a cut at the clip boundary.
    edge = ed.build_edl(c, words, ed.ClipEdits(cleanup="off", exclude=[(5, 8)]), rms, dur)
    assert len(edge.segments) == 1


def test_restore_puts_an_automatic_removal_back():
    words, rms, dur = transcript()
    c = clip_for(words)
    auto = ed.build_edl(c, words, ed.ClipEdits(cleanup="light"), rms, dur)
    filler = next(r for r in auto.removed if r.kind == "filler")
    restored = ed.build_edl(
        c, words, ed.ClipEdits(cleanup="light", restored=[filler.src_in]), rms, dur
    )
    assert restored.duration > auto.duration
    assert {w.i for w in restored.remap_words(words)} >= {30} or {
        w.i for w in restored.remap_words(words)
    } >= {61}
    assert any(r.restored for r in restored.removed)


def test_effective_clip_applies_text_edits_and_status():
    words, _, _ = transcript()
    c = ed.effective_clip(
        clip_for(words), ed.ClipEdits(title="New", hashtags=["x", "y"], status="approved")
    )
    assert (c.title, c.hashtags, c.status, c.hook) == ("New", ["x", "y"], "approved", "H")


def test_edits_persist_atomically(tmp_path):
    from clipforge.store import Project

    p = Project.create(tmp_path, "abc")
    assert ed.load_edits(p, "c001") == ed.ClipEdits()
    ed.save_edits(p, "c001", ed.ClipEdits(exclude=[(3, 4)], status="rejected"))
    assert (
        ed.load_edits(p, "c001").exclude == [(3, 4)]
        and ed.load_edits(p, "c001").status == "rejected"
    )


def test_layout_override_replaces_the_planned_layout_only_in_its_range():
    scene = pl.scene_from_analysis(
        analysis([(200, 260, 160), (1080, 260, 160)], 20, [(0, 10, 0), (10, 20, 1)])
    )
    overrides = ed.layout_overrides(
        ed.ClipEdits(layouts=[ed.LayoutOverride(t0=5, t1=9, layout="stacked")])
    )
    plan = pl.build_plan(
        scene, [(0, 20)], [pl.Turn(0, 10, "S0"), pl.Turn(10, 20, "S1")], 405, overrides=overrides
    )
    kinds = [(s.layout, round(s.t0), round(s.t1)) for s in plan]
    assert ("stacked", 5, 9) in kinds and kinds[0][1] == 0 and kinds[-1][2] == 20
    st = next(s for s in plan if s.layout == "stacked")
    assert len(st.tracks) == 2 and st.override


def test_export_roundtrips_through_every_nle_format(tmp_path):
    edl = EDL.from_ranges("c001", [(10.0, 20.0), (22.5, 30.0), (31.0, 40.0)])
    import subprocess

    master = tmp_path / "master.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=64x36:r=24:d=45",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            "45",
            "-shortest",
            str(master),
        ],
        check=True,
    )
    fps = 23.976
    from clipforge.captions.spec import load_template
    from clipforge.captions.timeline import build_timeline

    tl_cap = build_timeline(
        edl.remap_words(
            [
                Word(i=i, w=f"w{i}", start=10 + i * 0.5, end=10.4 + i * 0.5, prob=1)
                for i in range(30)
            ]
        ),
        load_template("karaoke-pop"),
        edl.duration,
    )
    warned = {}
    for fmt in ("otio", "fcpxml", "fcp7", "edl"):
        path, warnings = ex.export_cut(
            "Clip 1", master, edl, fps, fmt, tmp_path / f"clip_{fmt}", tl_cap, 45.0
        )
        assert path.exists() and path.stat().st_size > 100
        warned[fmt] = warnings
        check = ex.roundtrip_check(path, fmt, edl, 24.0 if fmt == "fcpxml" else fps)
        assert check["ok"], (fmt, check)
    assert warned["otio"] == warned["fcp7"] == warned["edl"] == []
    assert (
        len(warned["fcpxml"]) == 1 and "0.1%" in warned["fcpxml"][0]
    )  # the NTSC limitation is reported, not hidden
    _, integer_warnings = ex.export_cut(
        "Clip 1", master, edl, 25.0, "fcpxml", tmp_path / "pal", tl_cap, 45.0
    )
    assert integer_warnings == []
    tl = ex.build_timeline("Clip 1", master, edl, fps, tl_cap, 45.0)
    j = json.loads((tmp_path / "clip_otio.otio").read_text())
    assert "Marker" in json.dumps(j)  # captions travel as markers
    with pytest.raises(ValueError, match="Unknown export format"):
        ex.export_timeline(tl, "mov", tmp_path / "x")


def test_bundle_to_folder_and_zip(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "a.srt"
    a.write_bytes(b"v")
    b.write_text("1")
    folder = ex.bundle({"clip-1": [a, b, tmp_path / "missing"]}, tmp_path / "out", as_zip=False)
    assert (folder / "clip-1" / "a.mp4").exists() and not (folder / "clip-1" / "missing").exists()
    z = ex.bundle({"clip-1": [a, b]}, tmp_path / "bundle", as_zip=True)
    import zipfile

    assert sorted(zipfile.ZipFile(z).namelist()) == ["clip-1/a.mp4", "clip-1/a.srt"]
    assert np.isfinite(1.0)


def test_model_rule_by_language():
    from clipforge.config import Settings
    from clipforge.pipeline import is_english, pick_model

    s = Settings(ASR_MODEL="large-v3-turbo", ASR_MODEL_NON_ENGLISH="large-v3")  # pyright: ignore[reportCallIssue]
    assert pick_model(s, "en") == "large-v3-turbo" and pick_model(s, None) == "large-v3-turbo"
    assert pick_model(s, "hi") == "large-v3" and pick_model(s, "es") == "large-v3"
    assert is_english("en-US") and not is_english("hi")
    same = Settings(ASR_MODEL="large-v3-turbo", ASR_MODEL_NON_ENGLISH="same")  # pyright: ignore[reportCallIssue]
    assert pick_model(same, "hi") == "large-v3-turbo"


def test_caption_text_and_toggle(tmp_path):
    from clipforge.captions.spec import load_template
    from clipforge.captions.timeline import build_timeline
    from clipforge.edits import ClipEdits, apply_caption_text
    from clipforge.models import Word

    words = [
        Word(i=i, w=w, start=i * 0.4, end=i * 0.4 + 0.3, prob=1.0)
        for i, w in enumerate(["helo", "wrld", "again"])
    ]
    e = ClipEdits(
        caption_text={"0": "Hello", "1": "  "}
    )  # blank text is ignored, never an empty caption
    out = apply_caption_text(words, e)
    assert [w.w for w in out] == ["Hello", "wrld", "again"] and words[0].w == "helo"
    tpl = load_template("karaoke-pop")
    tl = build_timeline([], tpl, 3.0, set(), hook="Hook", hook_enabled=True)
    assert tl.chunks == [] and tl.hook is not None  # captions off: the hook can still show


def test_english_regions_from_window_labels():
    from clipforge.pipeline import english_regions

    labels = ["en", "en", "en", "hi", "hi", "hi", "hi", "en", "en", "en", "en"]
    regions, skipped = english_regions(labels, 60.0, 660.0)
    assert regions == [(0.0, 180.0), (420.0, 660.0)]
    assert skipped == [{"start": 180.0, "end": 420.0, "language": "hi"}]
    noisy = ["en", "en", "hi", "en", "en", "en"]  # one odd window inside English is noise
    assert english_regions(noisy, 60.0, 360.0)[0] == [(0.0, 360.0)]
    assert english_regions(["hi"] * 4, 60.0, 240.0)[0] == []  # no English at all: caller falls back
    short = [
        "hi",
        "en",
        "hi",
        "hi",
    ]  # an English blip under 30 s... a single 60 s window is smoothed away
    assert english_regions(short, 60.0, 240.0)[0] == []


def test_merge_chunks_keeps_chunks_that_do_not_overlap():
    from clipforge.asr.chunking import merge_chunks
    from clipforge.asr.types import RawChunk, RawWord

    def w(t):
        return RawWord(
            w="x", start=t, end=t + 0.3, prob=1.0, no_speech=0.0, logprob=0.0, compression=1.0
        )

    a = RawChunk(index=0, start=0, end=120, language="en", words=[w(1), w(100)])
    b = RawChunk(
        index=1, start=240, end=371, language="en", words=[w(245), w(300)]
    )  # a gap (skipped section) between
    assert [x.start for x in merge_chunks([a, b])] == [1, 100, 245, 300]


def test_punch_curve_shape():
    from clipforge.reframe.punch import MIN_GAP, PEAK, apply_fit_punch, zoom_curve
    from clipforge.reframe.solve import FrameSpec

    fps = 30.0
    z = zoom_curve(300, fps, [2.0, 2.5, 6.0])  # the 2.5 s beat is too close to the first: skipped
    assert z.min() >= 1.0 and z.max() <= PEAK + 1e-6
    assert (
        z[int(1.9 * fps)] == 1.0 and z[int(2.5 * fps)] > 1.05
    )  # in at the beat, near the peak shortly after
    assert z[int(2.6 * fps)] > z[int(4.4 * fps)]  # decays slowly afterwards
    assert MIN_GAP > 0.5 and z[int(6.4 * fps)] > 1.05  # the later beat still fires
    drift = zoom_curve(300, fps, [], [(0.0, 10.0)])
    assert drift[0] < drift[150] < drift[299] <= 1.036
    frames = [FrameSpec("fit_blur") for _ in range(300)] + [FrameSpec("single")]
    moved = apply_fit_punch(frames, np.concatenate([z, [1.0]]), 1920, 1080, 0.5)
    assert moved > 20 and frames[int(2.5 * fps)].rects[0].w < 1920 and not frames[-1].rects
