import math

import pytest

from clipforge.evalkit import metrics as m
from clipforge.evalkit.replay import CacheMiss, RecordReplayClient, request_key
from clipforge.models import Sentence, Word


def test_overlap_and_iou():
    a, b = m.Span(0, 10), m.Span(5, 20)
    assert m.iou(a, b) == pytest.approx(5 / 20)
    assert m.overlap_ratio(a, b) == 0.5
    assert m.iou(a, m.Span(20, 30)) == 0


def test_recall_and_precision_at_k_use_the_top_k_only():
    gold = [m.Span(0, 40), m.Span(100, 140), m.Span(500, 540)]
    pred = [m.Span(2, 42), m.Span(300, 340), m.Span(101, 139), m.Span(505, 545)]
    assert m.recall_at_k(pred, gold, 1) == pytest.approx(1 / 3)
    assert m.recall_at_k(pred, gold, 3) == pytest.approx(2 / 3)
    assert m.recall_at_k(pred, gold, 4) == 1.0
    assert m.precision_at_k(pred, gold, 4) == 0.75
    assert math.isnan(m.recall_at_k(pred, [], 3))


def test_bad_hit_rate_and_boundaries():
    assert m.bad_hit_rate([m.Span(0, 30), m.Span(100, 130)], [m.Span(5, 25)]) == 0.5
    words = [Word(i=i, w=f"w{i}.", start=i * 1.0, end=i * 1.0 + 0.5, prob=1) for i in range(10)]
    sents = [
        Sentence(
            id=f"S{i + 1:04d}", word_lo=i, word_hi=i, start=i * 1.0, end=i * 1.0 + 0.5, text="x."
        )
        for i in range(10)
    ]
    assert m.starts_on_sentence_boundary(m.Span(2.8, 9), sents, words)  # in the pause before word 3
    assert not m.starts_on_sentence_boundary(m.Span(2.2, 9), sents, words)  # inside word 2
    assert m.cut_inside_word(2.2, words) and not m.cut_inside_word(2.7, words)


def test_replay_client_records_then_replays_and_refuses_unknown_requests(tmp_path):
    from types import SimpleNamespace

    calls = []

    class Real:
        class messages:
            @staticmethod
            def create(**req):
                calls.append(req)
                b = SimpleNamespace(
                    type="text",
                    text='{"a": 1}',
                    model_dump=lambda: {"type": "text", "text": '{"a": 1}'},
                )
                u = SimpleNamespace(
                    input_tokens=5,
                    output_tokens=2,
                    model_dump=lambda: {"input_tokens": 5, "output_tokens": 2},
                )
                return SimpleNamespace(content=[b], usage=u, stop_reason="end_turn")

    req = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}
    recording = RecordReplayClient(tmp_path, Real())
    r1 = recording.messages.create(**req)
    replay = RecordReplayClient(tmp_path, None)
    r2 = replay.messages.create(**req)
    assert r2.content[0].text == r1.content[0].text and r2.usage.input_tokens == 5
    assert len(calls) == 1 and replay.messages.hits == 1
    with pytest.raises(CacheMiss, match="--live"):
        replay.messages.create(model="x", messages=[{"role": "user", "content": "different"}])
    assert request_key(req) == request_key(dict(reversed(list(req.items()))))
