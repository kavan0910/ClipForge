import json
from typing import Any

import pytest

from clipforge.config import Settings
from clipforge.curate.run import CurateParams, curate, preset_models
from clipforge.curate.schema import CurateResult, ScanResult
from clipforge.errors import ClipforgeError
from clipforge.llm.client import Block, LLMClient, LLMError, loose_schema
from clipforge.llm.meter import CostCapExceeded, CostMeter
from clipforge.llm.pricing import price_for, usd
from clipforge.models import Sentence, Source, Transcript, Word
from clipforge.store import Project
from tests.fake_anthropic import fake_client


def test_pricing_matches_published_rates():
    assert usd("claude-sonnet-5", 1_000_000, 0) == 2.0
    assert usd("claude-haiku-4-5-20251001", 0, 1_000_000) == 5.0
    assert usd("claude-opus-5", 0, 0, cache_write=1_000_000) == 6.25
    assert usd("claude-opus-5", 0, 0, cache_read=1_000_000) == 0.5
    assert price_for("some-new-model").input == 10.0  # unknown models priced high (safe)


def test_meter_records_usage_fields_and_enforces_the_cap(tmp_path):
    m = CostMeter(cap_usd=0.01, path=tmp_path / "usage.jsonl")
    u = type(
        "U",
        (),
        {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 2000,
            "cache_read_input_tokens": 4000,
        },
    )()
    rec = m.record("scan", "claude-haiku-4-5-20251001", u)
    assert rec.usd == pytest.approx((1000 * 1 + 500 * 5 + 2000 * 1.25 + 4000 * 0.1) / 1e6)
    assert json.loads((tmp_path / "usage.jsonl").read_text())["stage"] == "scan"
    assert m.totals()["output_tokens"] == 500
    with pytest.raises(CostCapExceeded) as e:
        m.check("claude-opus-5", 50_000, 8000)
    assert "MAX_JOB_COST_USD" in e.value.action
    m.check("claude-haiku-4-5-20251001", 100, 100)  # cheap call still allowed


def test_loose_schema_is_closed_and_has_no_local_constraints():
    s = loose_schema(CurateResult)
    dumped = json.dumps(s)
    assert "maxLength" not in dumped and "minimum" not in dumped and "$ref" not in dumped
    clip = s["properties"]["clips"]["items"]
    assert clip["additionalProperties"] is False and "scores" in clip["required"]


def test_client_caches_blocks_repairs_once_and_falls_back_to_tools(tmp_path):
    meter = CostMeter(1.0, tmp_path / "u.jsonl")
    fake = fake_client(fail_first_json=True)
    llm = LLMClient("k", meter, tmp_path / "run", "v1", client=fake)
    text = "\n".join(f"S{i:04d} [00:{i:02d}.0-00:{i + 1:02d}.0] words" for i in range(1, 30))
    out = llm.structured(
        stage="scan", model="claude-haiku-4-5-20251001", system="sys",
        blocks=[Block(text, cache=True), Block("Return up to 3 candidates.")],
        model_cls=ScanResult, tool_name="report_candidates",
    )  # fmt: skip
    assert out.candidates  # the first reply was invalid; one repair retry fixed it
    assert len(fake.messages.calls) == 2
    first = fake.messages.calls[0]["messages"][0]["content"]
    assert first[0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in first[1]
    assert "invalid" in fake.messages.calls[1]["messages"][-1]["content"][0]["text"].lower()
    assert len(list((tmp_path / "run").glob("raw_*.json"))) == 2  # raw responses stored
    assert len(meter.records) == 2

    fake2 = fake_client(reject_output_config=True)
    llm2 = LLMClient("k", CostMeter(1.0), tmp_path / "run2", "v1", client=fake2)
    llm2.structured(
        stage="scan", model="claude-haiku-4-5-20251001", system="s", blocks=[Block(text)],
        model_cls=ScanResult, tool_name="report_candidates",
    )  # fmt: skip
    assert (
        "tools" in fake2.messages.calls[-1]
        and fake2.messages.calls[-1]["tool_choice"]["type"] == "tool"
    )


def test_client_gives_up_after_one_repair(tmp_path):
    fake = fake_client(fail_first_json=True)
    fake.messages._failed = False

    def always_bad(**req):
        fake.messages.calls.append(req)
        from types import SimpleNamespace

        c = SimpleNamespace(
            type="text", text='{"nope": 1}', model_dump=lambda: {"type": "text", "text": "{}"}
        )
        u = SimpleNamespace(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return SimpleNamespace(content=[c], usage=u, stop_reason="end_turn", model_dump=lambda: {})

    fake.messages.create = always_bad
    llm = LLMClient("k", CostMeter(1.0), tmp_path / "r", "v1", client=fake)
    with pytest.raises(LLMError, match="invalid output twice"):
        llm.structured(stage="s", model="claude-sonnet-5", system="s", blocks=[Block("x")],
                       model_cls=ScanResult, tool_name="t")  # fmt: skip
    assert len(fake.messages.calls) == 2


def test_presets_map_to_models():
    s = Settings()
    assert preset_models("economy", s) == (s.scan_model, s.scan_model)
    assert preset_models("balanced", s) == (s.scan_model, s.curate_model)
    assert preset_models("max", s) == (s.max_scan_model, s.max_curate_model)


def build_project(tmp_path, minutes=150):
    """A long synthetic transcript on disk so the full curate() plumbing runs offline."""
    import wave

    import numpy as np

    project = Project.create(tmp_path, "cur")
    words, sentences, t = [], [], 0.0
    while t < minutes * 60:
        lo = len(words)
        for j in range(9):
            words.append(
                Word(
                    i=len(words),
                    w=f"word{len(words)}" + ("." if j == 8 else ""),
                    start=round(t, 3),
                    end=round(t + 0.4, 3),
                    prob=1,
                )
            )
            t += 0.5
        sentences.append(
            Sentence(
                id=f"S{len(sentences) + 1:04d}",
                word_lo=lo,
                word_hi=len(words) - 1,
                start=words[lo].start,
                end=words[-1].end,
                text=" ".join(w.w for w in words[lo:]),
            )
        )
        t += 0.9
    wav = project.path("source", "audio_16k.wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        # Only 10 s of audio: plumbing does not need silence minima for 150 minutes.
        w.writeframes(np.zeros(10 * 16000, dtype=np.int16).tobytes())
    tr = Transcript(
        language="en", asr_backend="x", asr_model="x", duration=t, words=words, sentences=sentences
    )
    from clipforge.models import Probe, QualityReport

    src = Source(id="cur", kind="path", title="Synthetic talk", content_hash="h", master_path=str(wav),
                 probe=Probe(duration=t, format_name="wav", size=1, audio=[]), selected_audio=0,
                 quality=QualityReport(resolution=None, fps=None, video_bitrate_kbps=None, audio_summary=""),
                 audio_path=str(wav))  # fmt: skip
    return project, src, tr


def test_two_stage_curation_end_to_end_plumbing(tmp_path):
    project, src, tr = build_project(tmp_path)
    fake = fake_client()
    settings = Settings(ANTHROPIC_API_KEY="k", MAX_JOB_COST_USD=5.0)  # type: ignore[arg-type]
    meter = CostMeter(5.0, project.path("curation", "runs", "r1") / "usage.jsonl")
    project.path("curation", "runs", "r1").mkdir(parents=True, exist_ok=True)
    llm = LLMClient("k", meter, project.path("curation", "runs", "r1"), "v1", client=fake)
    out = curate(
        project, src, tr, None, settings, CurateParams(n_clips=4), llm=llm, meter=meter, run_id="r1"
    )
    models_used = {c["model"] for c in fake.messages.calls}
    assert settings.scan_model in models_used and settings.curate_model in models_used  # two stages
    assert out.clips and len(out.clips) <= 4
    words = {w.i: w for w in tr.words}
    for c in out.clips:
        assert 20 <= c.duration <= 90 and c.id.startswith("c")
        assert c.start_sentence <= c.end_sentence
        for w in tr.words:  # zero mid-word cuts, asserted for every clip
            assert not (w.start < c.start < w.end) and not (w.start < c.end < w.end)
    assert out.usage["usd"] > 0 and (project.path("curation", "runs", "r1", "usage.jsonl")).exists()
    saved = json.loads(project.path("curation", "clips.json").read_text())
    assert [c["id"] for c in saved] == [c.id for c in out.clips]
    assert words  # transcript intact

    # Re-curation appends different clips without redoing anything upstream.
    llm2 = LLMClient("k", meter, project.path("curation", "runs", "r2"), "v1", client=fake_client())
    more = curate(
        project,
        src,
        tr,
        None,
        settings,
        CurateParams(n_clips=3, mode="different_topic"),
        llm=llm2,
        meter=meter,
        run_id="r2",
    )
    ids = [c["id"] for c in json.loads(project.path("curation", "clips.json").read_text())]
    assert len(ids) == len(set(ids)) == len(out.clips) + len(more.clips)


def test_curation_stops_at_the_cost_cap_with_a_clear_message(tmp_path):
    project, src, tr = build_project(tmp_path)
    settings = Settings(ANTHROPIC_API_KEY="k", MAX_JOB_COST_USD=0.001)  # type: ignore[arg-type]
    meter = CostMeter(0.001)
    llm = LLMClient("k", meter, project.path("curation", "runs", "r"), "v1", client=fake_client())
    with pytest.raises(CostCapExceeded, match="job cap"):
        curate(project, src, tr, None, settings, CurateParams(n_clips=3), llm=llm, meter=meter)


def test_missing_key_is_an_actionable_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    project, src, tr = build_project(tmp_path)
    with pytest.raises(ClipforgeError, match="API key"):
        curate(
            project, src, tr, None, Settings.model_construct(anthropic_api_key=None), CurateParams()
        )  # type: ignore[call-arg]


def test_no_credit_is_reported_as_an_actionable_error(tmp_path):
    import anthropic
    import httpx

    class Broke:
        class messages:
            @staticmethod
            def create(**_req):
                resp: Any = httpx.Response(400, request=httpx.Request("POST", "https://x"))
                raise anthropic.BadRequestError(
                    "Your credit balance is too low to access the Anthropic API.",
                    response=resp,
                    body=None,
                )

    llm = LLMClient("k", CostMeter(1.0), tmp_path / "r", "v1", client=Broke())
    with pytest.raises(LLMError, match="no credit") as e:
        llm.structured(stage="s", model="claude-sonnet-5", system="s", blocks=[Block("x")],
                       model_cls=ScanResult, tool_name="t")  # fmt: skip
    assert "Plans & Billing" in e.value.action
