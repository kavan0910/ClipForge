import wave

import numpy as np
import pytest

from clipforge.captions import onsets
from clipforge.models import Word


def synth(tmp_path, onsets_s, dur=12.0, rate=16000, noise=0.002):
    """Speech-like bursts (tone + envelope) at known onsets over low room noise."""
    rng = np.random.default_rng(1)
    x = rng.normal(0, noise, int(dur * rate)).astype(np.float32)
    for o in onsets_s:
        n = int(0.45 * rate)
        t = np.arange(n) / rate
        env = np.minimum(t / 0.008, 1.0) * np.exp(-t * 3.0)  # 8 ms attack
        i = int(o * rate)
        x[i : i + n] += (0.3 * env * np.sin(2 * np.pi * 220 * t)).astype(np.float32)[: len(x) - i]
    p = tmp_path / "a.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return p


def test_detector_finds_known_onsets_within_20ms(tmp_path):
    true = [1.0, 3.2, 6.1, 9.4]
    rms = onsets.rms_db_10ms(synth(tmp_path, true))
    for o in true:
        found = onsets.detect_onset(
            rms, prev_end=o - 1.0, near=o + 0.06
        )  # transcript claims 60 ms late
        assert found is not None and abs(found - o) <= 0.02, (o, found)


def test_no_clean_onset_means_no_measurement(tmp_path):
    rms = onsets.rms_db_10ms(synth(tmp_path, [1.0, 1.3]))
    assert (
        onsets.detect_onset(rms, prev_end=1.1, near=1.3) is None
    )  # only 0.2 s of "silence": untrustworthy


def test_refine_snaps_late_post_pause_words_and_leaves_others(tmp_path):
    true = [1.0, 3.2, 6.1]
    rms = onsets.rms_db_10ms(synth(tmp_path, true))
    words = [
        Word(i=0, w="a", start=1.09, end=1.4, prob=1),  # 90 ms late, first word: no previous word
        Word(i=1, w="b", start=3.29, end=3.6, prob=1),  # 90 ms late, after a pause
        Word(i=2, w="c", start=3.65, end=3.9, prob=1),  # continuous speech: untouched
        Word(i=3, w="d", start=6.18, end=6.5, prob=1),  # 80 ms late after a pause
    ]
    out = onsets.refine(words, rms)
    assert out[0].start == 1.09 and out[2].start == 3.65
    assert out[1].start == pytest.approx(3.2, abs=0.02) and out[3].start == pytest.approx(
        6.1, abs=0.02
    )
    before = onsets.summarize([o - w.start for w, o in ((words[1], 3.2), (words[3], 6.1))])
    after = onsets.summarize([o - w.start for w, o in ((out[1], 3.2), (out[3], 6.1))])
    assert after["median_ms"] < before["median_ms"] and after["within_50ms"] == 1.0


def test_caption_lead_shifts_words_earlier_but_never_negative():
    from clipforge.captions.timeline import apply_lead
    from clipforge.edl import RemappedWord

    w = [RemappedWord(i=0, w="x", src_start=0.02, src_end=0.3, out_start=0.02, out_end=0.3)]
    (s,) = apply_lead(w, 0.05)
    assert s.out_start == 0.0 and s.out_end == pytest.approx(0.25)
