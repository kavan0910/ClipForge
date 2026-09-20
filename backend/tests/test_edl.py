import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from clipforge.cleanup import find_word_removals, jump_cut_zoom, plan_cleanup
from clipforge.edl import EDL, Removed
from clipforge.models import Word


def ranges():
    """Random sorted, non-overlapping kept ranges."""
    return st.lists(st.floats(0.05, 10.0), min_size=1, max_size=12).flatmap(
        lambda lens: st.lists(st.floats(0.0, 5.0), min_size=len(lens), max_size=len(lens)).map(
            lambda gaps: _layout(lens, gaps)
        )
    )


def _layout(lens, gaps):
    t, out = 3.0, []
    for ln, g in zip(lens, gaps, strict=True):
        t += g
        out.append((t, t + ln))
        t += ln
    return out


@given(ranges())
def test_edl_invariants_and_duration(keep):
    edl = EDL.from_ranges("c1", keep)
    edl.validate_invariants()
    assert edl.duration == pytest.approx(sum(b - a for a, b in keep))
    assert len(edl.cut_points_out) <= len(keep) - 1


@given(ranges(), st.data())
def test_roundtrip_and_monotonic_mapping(keep, data):
    edl = EDL.from_ranges("c1", keep)
    a, b = data.draw(st.sampled_from(keep))
    t = data.draw(st.floats(a, b))
    out = edl.src_to_out(t)
    assert out is not None and 0 <= out <= edl.duration + 1e-9
    back = edl.out_to_src(out)
    if (
        abs(t - b) < 1e-6
    ):  # the end of one kept range and the start of the next share one output instant
        assert edl.src_to_out(back) == pytest.approx(out, abs=1e-6)
    else:
        assert back == pytest.approx(t, abs=1e-6)
    o1, o2 = sorted(data.draw(st.floats(0, max(edl.duration, 0.1))) for _ in range(2))
    assert edl.out_to_src(o1) <= edl.out_to_src(o2) + 1e-9


@given(ranges())
def test_removed_instants_map_to_none_but_nearest_is_defined(keep):
    edl = EDL.from_ranges("c1", keep)
    gap_t = keep[0][0] - 0.01
    assert edl.src_to_out(gap_t) is None
    assert 0 <= edl.src_to_out_nearest(gap_t) <= edl.duration


@given(ranges(), st.data())
@settings(max_examples=100)
def test_remove_then_restore_is_identity(keep, data):
    edl = EDL.from_ranges("c1", keep)
    a, b = data.draw(st.sampled_from(keep))
    if b - a < 0.2:
        return
    lo = data.draw(st.floats(a + 0.01, b - 0.1))
    r = Removed(id="r1", kind="manual", src_in=lo, src_out=lo + 0.05)
    edited = edl.with_removal(r)
    edited.validate_invariants()
    assert edited.duration == pytest.approx(edl.duration - 0.05)
    back = edited.restore("r1")
    assert back.duration == pytest.approx(edl.duration)
    assert [(round(s.src_in, 6), round(s.src_out, 6)) for s in back.segments] == [
        (round(s.src_in, 6), round(s.src_out, 6)) for s in edl.segments
    ]
    assert back.removed[0].restored


def test_word_remap_drops_removed_words_and_shifts_times():
    words = [Word(i=i, w=f"w{i}", start=10 + i, end=10.8 + i, prob=1) for i in range(6)]
    edl = EDL.from_ranges("c", [(10.0, 12.9), (14.0, 16.9)])  # word 3 (13.0-13.8) removed
    out = edl.remap_words(words)
    assert [w.i for w in out] == [0, 1, 2, 4, 5]
    assert out[0].out_start == 0 and out[3].out_start == pytest.approx(2.9)
    assert out[3].out_end == pytest.approx(3.7)
    assert edl.cut_points_out == [pytest.approx(2.9)]


def test_remap_series_samples_the_source_function_per_output_frame():
    edl = EDL.from_ranges("c", [(10.0, 11.0), (20.0, 21.0)])
    xs = edl.remap_series(lambda t: t, fps=10)
    assert len(xs) == 20
    assert xs[0] == pytest.approx(10.05) and xs[10] == pytest.approx(20.05)


def spoken(text, gap=0.15, t0=0.0, dur=0.3):
    out, t = [], t0
    for i, tok in enumerate(text.split()):
        out.append(Word(i=i, w=tok, start=round(t, 3), end=round(t + dur, 3), prob=1))
        t += dur + gap
    return out


def rms_for(words, total, hop=0.1):
    rms = [-60.0] * (int(total / hop) + 5)
    for w in words:
        for k in range(int(w.start / hop), int(w.end / hop) + 1):
            rms[k] = -20.0
    return rms


def test_filler_detection_levels():
    ws = spoken("so um I uh went to the the store you know, it was uhm fine")
    light = find_word_removals(ws, "light")
    assert [ws[r.lo].w for r in light] == ["um", "uh", "uhm"]
    agg = find_word_removals(ws, "aggressive")
    kinds = {(ws[r.lo].w, r.kind) for r in agg}
    assert ("the", "repeat") in kinds and ("you", "filler") in kinds
    assert find_word_removals(ws, "off") == []
    meaningful = spoken("no no no I know you know the answer")
    assert find_word_removals(meaningful, "aggressive") == []  # emphasis and real meaning kept


def test_plan_cleanup_removes_fillers_without_cutting_words_and_compresses_pauses():
    ws = spoken("so this is um really important", gap=0.4)
    # a long pause after "important-1": shift later words
    ws = [
        w.model_copy(
            update={
                "start": w.start + (2.0 if w.i >= 4 else 0),
                "end": w.end + (2.0 if w.i >= 4 else 0),
            }
        )
        for w in ws
    ]
    rms = rms_for(ws, 12)
    edl = plan_cleanup("c", ws, 0.0, ws[-1].end + 0.3, "light", rms, 12)
    edl.validate_invariants()
    kinds = sorted(r.kind for r in edl.removed)
    assert kinds == ["filler", "pause"]
    for cut in [t for s in edl.segments for t in (s.src_in, s.src_out)]:
        assert not any(w.start < cut < w.end for w in ws)
    kept_words = [w.w for w in edl.remap_words(ws)]
    assert "um" not in kept_words and kept_words.count("important") == 1
    assert edl.duration < ws[-1].end - 1.0  # pause + filler removed
    # Off level is a plain trim.
    assert len(plan_cleanup("c", ws, 0.0, 9.0, "off", rms, 12).segments) == 1


def test_removal_never_trims_first_or_last_word_or_leaves_slivers():
    ws = spoken("um hello um", gap=0.05)
    rms = rms_for(ws, 5)
    edl = plan_cleanup("c", ws, 0.0, ws[-1].end, "aggressive", rms, 5)
    assert all(s.length >= 0.3 for s in edl.segments) and not edl.removed


def test_jump_cut_zoom_alternates():
    edl = EDL.from_ranges("c", [(0, 1), (2, 3), (4, 5)])
    assert jump_cut_zoom(edl, 1.08) == [1.0, 1.08, 1.0]
