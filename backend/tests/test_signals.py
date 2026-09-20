import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from clipforge.models import Sentence, Word
from clipforge.signals import audio, combine, platform, visual


def test_pct_rank_handles_ties_and_constants():
    assert combine.pct_rank([5, 5, 5]).tolist() == [0, 0, 0]
    r = combine.pct_rank([1, 2, 2, 3])
    assert r[0] == 0 and r[3] == 1 and r[1] == r[2] == 0.5


@given(st.lists(st.floats(-1e6, 1e6, allow_nan=False), min_size=2, max_size=200))
def test_pct_rank_bounds(xs):
    r = combine.pct_rank(xs)
    assert r.min() >= 0 and r.max() <= 1


def test_interest_curve_uses_weights_and_skips_empty_features():
    feats = {"energy": [1, 2, 3, 4], "laughter": [0, 0, 0, 0], "heatmap": [0, 0, 1, 0]}
    c = combine.interest_curve(feats, {"energy": 1, "laughter": 5, "heatmap": 1})
    # laughter is all zero so it is ignored; energy and heatmap are averaged.
    assert c.argmax() == 3 or c.argmax() == 2
    assert combine.interest_curve({}, None).size == 0


def test_span_score_and_tags():
    interest = [0.0, 0.0, 1.0, 1.0, 0.0]
    assert combine.span_score(interest, 2, 4) == 1.0
    assert combine.span_score(interest, 10, 12) == 0.0
    s = Sentence(id="S0001", word_lo=0, word_hi=3, start=2.0, end=4.0, text="x")
    tags = combine.sentence_tags(
        s,
        [-50, -50, -10, -10, -50],
        laughter=[0, 0, 0.6, 0, 0],
        heatmap=[0, 0, 0.83, 0, 0],
        energy_cuts=(-45, -30),
    )
    assert tags == {"energy": "high", "laughter": True, "heatmap": 0.83}
    assert combine.format_tags(tags) == "{energy:high, laughter, heatmap:0.83}"
    assert combine.format_tags({}) == ""


def test_heatmap_and_chat_resampling(tmp_path):
    hm = platform.heatmap_per_second(
        [
            {"start_time": 0, "end_time": 3, "value": 0.2},
            {"start_time": 3, "end_time": 6, "value": 0.8},
        ],
        6,
    )
    assert hm == [0.25, 0.25, 0.25, 1.0, 1.0, 1.0]
    f = tmp_path / "chat.json"
    lines = [
        f'{{"replayChatItemAction": {{"videoOffsetTimeMsec": "{ms}"}}}}'
        for ms in (1000, 1500, 1900, 5000)
    ]
    f.write_text("\n".join([*lines, "not json"]))
    rate = platform.chat_rate_per_second(f, 8, window=1)
    assert rate[1] == 3 and rate[5] == 1 and rate[0] == 0


def test_energy_and_speech_rate():
    e = audio.per_second_energy([-20.0] * 10 + [-90.0] * 10, 2.5)
    assert e[0] == -20 and e[1] == -80 and e[2] == -80  # floor and padding
    words = [Word(i=i, w="a", start=i * 0.25, end=i * 0.25 + 0.2, prob=1) for i in range(20)]
    rate, dens = audio.speech_rate_and_density(words, 5)
    assert max(rate) > 2 and all(0 <= d <= 1 for d in dens) and dens[0] > 0.9


def test_cut_density_counts_cuts():
    d = visual.cut_density([1.0, 1.5, 2.0], 5, window=1)
    assert d[1] == 2 and d[2] == 1 and np.isclose(sum(d), 3)
