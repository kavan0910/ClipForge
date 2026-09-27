from clipforge.render import video


def test_editor_preview_uses_high_quality_proxy_settings(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(video.subprocess, "Popen", fake_popen)
    video._start_preview(tmp_path / "base_preview.mp4", tmp_path / "audio.wav", 30, 300)
    argv = captured["argv"]

    assert argv[argv.index("-s") + 1] == "720x1280"
    assert argv[argv.index("-preset") + 1] == "veryfast"
    assert argv[argv.index("-crf") + 1] == "21"
    assert argv[argv.index("-b:a") + 1] == "128k"
    assert argv[-1] == str(tmp_path / "base_preview.partial.mp4")
    assert captured["kwargs"]["stdin"] is video.subprocess.PIPE
