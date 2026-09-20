import subprocess

import pytest

from clipforge import media as media_mod
from clipforge.errors import MediaError


def test_probe_valid_file(media):
    pr = media_mod.probe(media["av"])
    assert pr.video and (pr.video.width, pr.video.height) == (320, 240)
    assert pr.video.fps == 25 and not pr.video.variable_fps
    assert pr.audio[0].sample_rate == 44100 and pr.duration == pytest.approx(3, abs=0.2)


def test_rejects_garbage_and_no_audio(media):
    with pytest.raises(MediaError, match="not readable"):
        media_mod.probe(media["garbage"])
    with pytest.raises(MediaError, match="no audio stream"):
        media_mod.probe(media["noaudio"])


def test_rotation_swaps_dimensions(media):
    v = media_mod.probe(media["rotated"]).video
    assert v and abs(v.rotation) == 90 and (v.width, v.height) == (240, 320)


def test_audio_only_and_track_selection(media):
    assert media_mod.probe(media["audio"]).video is None
    pr = media_mod.probe(media["twotrack"])
    assert len(pr.audio) == 2
    assert media_mod.default_audio_track(pr) in {a.index for a in pr.audio}


def test_quality_report_warns_below_1440p(media):
    pr = media_mod.probe(media["av"])
    q = media_mod.quality_report(pr, media_mod.default_audio_track(pr))
    assert q.resolution == "320x240" and any("soft" in w for w in q.warnings)
    hd = media_mod.probe(media["hd"])
    q2 = media_mod.quality_report(hd, media_mod.default_audio_track(hd))
    assert not any("soft" in w for w in q2.warnings)


def test_fast_hash_stable_and_content_sensitive(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 10_000_000)
    b.write_bytes(b"x" * 10_000_000)
    assert media_mod.fast_hash(a) == media_mod.fast_hash(b)
    b.write_bytes(b"x" * 9_999_999 + b"y")
    assert media_mod.fast_hash(a) != media_mod.fast_hash(b)


def test_wav_and_proxy_outputs(media, tmp_path):
    pr = media_mod.probe(media["av"])
    progress: list[float] = []
    wav = tmp_path / "a.wav"
    media_mod.make_wav(media["av"], wav, pr.audio[0].index, pr.duration, progress.append)
    w = media_mod.probe(wav)
    assert w.audio[0].sample_rate == 16000 and w.audio[0].channels == 1
    proxy = tmp_path / "p.mp4"
    assert pr.video
    media_mod.make_proxy(media["av"], proxy, pr.video, pr.duration, has_zimg=False)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=height", "-of", "csv=p=0", str(proxy)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert int(out.stdout) == 240


def test_proxy_filter_tonemaps_only_when_possible():
    from clipforge.models import VideoInfo

    v = VideoInfo(codec="hevc", width=3840, height=2160, fps=30, variable_fps=False, hdr=True)
    assert "tonemap" in media_mod.proxy_filter(v, True)
    assert "tonemap" not in media_mod.proxy_filter(v, False)


def test_disk_space_gate(tmp_path):
    from clipforge.errors import DiskSpaceError

    with pytest.raises(DiskSpaceError, match="Not enough"):
        media_mod.check_free_space(tmp_path, needed=10**18)


@pytest.mark.skipif("zscale" not in media_mod.ffmpeg_filters(), reason="ffmpeg lacks zscale")
def test_hdr_source_is_detected_and_tonemapped_to_sdr(tmp_path):
    hdr = tmp_path / "hdr.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=320x240:rate=25:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-vf", "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
         "zscale=t=smpte2084:p=bt2020:m=bt2020nc:r=tv,format=yuv420p10le",
         "-c:v", "libx265", "-x265-params",
         "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
         "-c:a", "aac", "-shortest", str(hdr)],
        check=True,
    )  # fmt: skip
    pr = media_mod.probe(hdr)
    assert pr.video and pr.video.hdr and pr.video.color_transfer == "smpte2084"
    assert any("tone-mapped" in w for w in media_mod.quality_report(pr, pr.audio[0].index).warnings)
    proxy = tmp_path / "p.mp4"
    media_mod.make_proxy(hdr, proxy, pr.video, pr.duration, has_zimg=True)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=pix_fmt,color_transfer,color_primaries", "-of", "csv=p=0", str(proxy)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert out.stdout.strip().startswith("yuv420p")
    assert "smpte2084" not in out.stdout  # no longer PQ


def test_decode_handles_rotation_vfr_conform_and_hdr_tonemap(media, tmp_path):
    import numpy as np

    from clipforge.render.video import decode_segment, fps_arg

    pr = media_mod.probe(media["rotated"])
    assert pr.video
    frames = list(decode_segment(media["rotated"], pr.video, 0.5, 10, 25.0, True))
    assert len(frames) == 10 and frames[0].shape == (320, 240, 3)  # rotation applied: h x w swapped
    assert (
        fps_arg(24000 / 1001) == "24000/1001"
        and fps_arg(25.0) == "25"
        and fps_arg(29.97) == "30000/1001"
    )

    if "zscale" in media_mod.ffmpeg_filters():
        hdr = tmp_path / "hdr.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
             "-vf", "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709,"
             "zscale=t=smpte2084:p=bt2020:m=bt2020nc:r=tv,format=yuv420p10le",
             "-c:v", "libx265", "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc", str(hdr)],
            check=True,
        )  # fmt: skip
        from clipforge.models import VideoInfo

        ref = tmp_path / "ref.mp4"  # the same picture as plain SDR BT.709
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
             "-vf", "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709",
             "-c:v", "libx264", str(ref)],
            check=True,
        )  # fmt: skip
        vi = VideoInfo(codec="hevc", width=320, height=240, fps=25, variable_fps=False, hdr=True)
        vs = VideoInfo(codec="h264", width=320, height=240, fps=25, variable_fps=False)
        sdr_ref = np.stack(list(decode_segment(ref, vs, 0.0, 5, 25.0, True))).astype(float)
        tonemapped = np.stack(list(decode_segment(hdr, vi, 0.0, 5, 25.0, True))).astype(float)
        untouched = np.stack(list(decode_segment(hdr, vi, 0.0, 5, 25.0, False))).astype(float)
        # Colours must come back close to the SDR original; skipping tone-mapping is much worse.
        assert np.abs(tonemapped - sdr_ref).mean() < np.abs(untouched - sdr_ref).mean() * 0.6
