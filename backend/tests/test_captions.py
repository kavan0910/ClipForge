from itertools import pairwise

import numpy as np
import pytest

from clipforge.captions.sidecars import to_srt, to_vtt
from clipforge.captions.spec import list_templates, load_template
from clipforge.captions.timeline import (
    CAPTION_LEAD,
    FRAME_H,
    FRAME_W,
    SAFE,
    build_timeline,
    detect_script,
    display_text,
)
from clipforge.edl import EDL
from clipforge.models import Word
from tests.captions_util import bbox_diff, render_frame, timeline_for, words_from

ALL = list_templates()
SAMPLES = {
    "plain": "So the thing that really surprised me about this whole project was how quickly people adopted it",
    "long words": "Antidisestablishmentarianism and pneumonoultramicroscopicsilicovolcanoconiosis are famously long words",
    "cjk": "这是一个关于人工智能如何改变我们生活方式的非常有趣的故事 我们今天来聊聊",
    "arabic": "هذا مثال على نص عربي طويل يجب أن يظهر داخل المنطقة الآمنة من الشاشة بشكل صحيح",
}


def test_eight_templates_are_valid_and_distinct():
    assert len(ALL) == 8
    ts = [load_template(i) for i in ALL]
    assert len({t.font.family + str(t.active.mode) + t.motion.kind for t in ts}) >= 7
    assert all(t.chunking.max_lines <= 2 for t in ts)


@pytest.mark.parametrize("tid", ALL)
@pytest.mark.parametrize("key", ["plain", "long words"])
def test_chunks_never_exceed_two_lines_or_break_words(tid, key):
    t = load_template(tid)
    src = SAMPLES[key].split()
    tl = timeline_for(SAMPLES[key], t)
    assert all(1 <= len(c.lines) <= 2 for c in tl.chunks)
    flat = [w.w.lower() for c in tl.chunks for w in c.words]
    assert flat == [display_text(x, t).lower() for x in src]  # every word once, unbroken, in order
    for c in tl.chunks:
        assert sorted(i for ln in c.lines for i in ln) == list(range(len(c.words)))
        assert c.box_w * FRAME_W <= FRAME_W * (1 - SAFE["left"] - SAFE["right"]) + 1
    times = [(c.start, c.end) for c in tl.chunks]
    assert all(a[1] <= b[0] + 1e-6 for a, b in pairwise(times))  # never overlap


def test_pauses_and_punctuation_end_chunks_and_speed_changes_chunk_size():
    t = load_template("karaoke-pop")
    ws = words_from("one two three four five six", wps=3.0)
    ws[3] = ws[3].model_copy(
        update={"out_start": ws[3].out_start + 1.0, "out_end": ws[3].out_end + 1.0}
    )
    ws[4] = ws[4].model_copy(
        update={"out_start": ws[3].out_start + 0.4, "out_end": ws[3].out_start + 0.7}
    )
    ws[5] = ws[5].model_copy(
        update={"out_start": ws[3].out_start + 0.8, "out_end": ws[3].out_start + 1.1}
    )
    tl = build_timeline(ws, t, 6.0)
    assert any(
        c.words[0].w.lower() == "four" for c in tl.chunks
    )  # the 1 s pause starts a new chunk
    ws2 = words_from("Hello there. Next sentence begins here", wps=3.0)
    tl2 = build_timeline(ws2, t, 5.0)
    assert any(c.words[-1].w.lower() == "there." for c in tl2.chunks)  # sentence end closes a chunk


def test_long_word_shrinks_the_font_instead_of_overflowing():
    t = load_template("heavy-outline")
    tl = timeline_for("pneumonoultramicroscopicsilicovolcanoconiosis", t)
    assert tl.chunks[0].scale < 1.0 and tl.chunks[0].box_w * FRAME_W <= FRAME_W * 0.8


def test_display_transforms_and_scripts():
    t = load_template("karaoke-pop").model_copy(
        update={"strip_punctuation": True, "mask_profanity": True}
    )
    assert display_text("Hello,", t) == "HELLO"
    assert display_text("shit!", t) == "S***"
    assert (
        detect_script(SAMPLES["cjk"]) == "cjk"
        and detect_script(SAMPLES["arabic"]) == "arabic"
        and detect_script("hi") == "latin"
    )
    tl = timeline_for(SAMPLES["cjk"].replace(" ", ""), load_template("clean-minimal"))
    assert tl.chunks[0].font is not None and tl.chunks[0].font.family == "Noto Sans SC"


def test_words_are_remapped_through_the_edl_so_captions_follow_cuts():
    src = [Word(i=i, w=f"w{i}", start=10 + i, end=10.8 + i, prob=1) for i in range(8)]
    edl = EDL.from_ranges("c", [(10.0, 12.9), (15.0, 18.0)])  # words 3-4 removed
    remapped = edl.remap_words(src)
    tl = build_timeline(remapped, load_template("clean-minimal"), edl.duration)
    starts = {w.src_i: w.start for c in tl.chunks for w in c.words}
    assert 3 not in starts and 4 not in starts
    assert starts[5] == pytest.approx(
        2.9 - CAPTION_LEAD
    )  # word 5 (src 15.0) follows word 2's end (out 2.9), 50 ms early


def test_srt_and_vtt_sidecars_are_well_formed():
    tl = timeline_for("Hello there friend. How are you today", load_template("classic-subtitle"))
    srt, vtt = to_srt(tl), to_vtt(tl)
    assert srt.startswith("1\n00:00:00,") and " --> " in srt and srt.count("\n\n") >= 1
    assert vtt.startswith("WEBVTT") and "00:00:00." in vtt


def test_emoji_is_capped_and_hook_fits():
    t = load_template("karaoke-pop")
    tl = timeline_for(
        "money money money love love love fire fire idea idea idea win win",
        t,
        hook="Why this idea could change everything you know",
    )
    assert sum(1 for c in tl.chunks if c.emoji) <= max(1, len(tl.chunks) // 4 + 1)
    assert (
        tl.hook
        and 1 <= len(tl.hook.lines) <= 3
        and tl.hook.end - tl.hook.start <= t.hook.seconds + 1e-6
    )


@pytest.mark.parametrize("tid", ALL)
@pytest.mark.parametrize("key", ["plain", "long words", "cjk", "arabic"])
def test_libass_render_stays_inside_the_safe_zone(tid, key, tmp_path):
    t = load_template(tid)
    tl = timeline_for(SAMPLES[key], t)
    c = tl.chunks[min(1, len(tl.chunks) - 1)]
    frame = render_frame(tl, t, tmp_path, at=(c.words[0].start + c.words[-1].end) / 2)
    box = bbox_diff(frame, (0, 0, 0))
    assert box is not None, "nothing was drawn"
    x0, y0, x1, y1 = box
    assert x0 >= FRAME_W * SAFE["left"] - 8 and x1 <= FRAME_W * (1 - SAFE["right"]) + 8, box
    assert y0 >= FRAME_H * SAFE["top"] and y1 <= FRAME_H * (1 - SAFE["bottom"]), box
    assert 0.5 <= (y0 + y1) / 2 / FRAME_H <= 0.78  # sits in the lower-middle band
    assert np.count_nonzero(frame) > 500


@pytest.mark.parametrize("tid", ALL)
def test_captions_stay_readable_on_white_and_black_backgrounds(tid, tmp_path):
    from tests.captions_util import readability

    t = load_template(tid)
    tl = timeline_for(SAMPLES["plain"], t)
    c = tl.chunks[1]
    at = (c.words[0].start + c.words[-1].end) / 2
    scores = {}
    for name, rgb in (("white", (255, 255, 255)), ("black", (0, 0, 0))):
        scores[name] = readability(render_frame(tl, t, tmp_path, at, name), rgb, t.fill)
    assert min(scores.values()) >= 3.0, (
        scores
    )  # WCAG large-text contrast against what surrounds the letters


def test_intro_offset_shifts_captions_and_hook_past_the_card():
    t = load_template("karaoke-pop")
    from clipforge.edl import RemappedWord

    ws = [
        RemappedWord(
            i=i,
            w=f"w{i}",
            src_start=i,
            src_end=i + 0.5,
            out_start=1.6 + i * 0.6,
            out_end=1.6 + i * 0.6 + 0.4,
        )
        for i in range(6)
    ]
    tl = build_timeline(ws, t, 1.6 + 6 + 1.6, hook="A true hook", hook_offset=1.6)
    assert tl.chunks[0].start >= 1.6 - 0.06  # lead may pull it 50 ms early, never into the card
    assert tl.hook and tl.hook.start == pytest.approx(1.75) and tl.hook.end > tl.hook.start
    assert tl.duration == pytest.approx(9.2)
