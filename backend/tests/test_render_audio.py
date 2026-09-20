import subprocess

import numpy as np

from clipforge.edl import EDL
from clipforge.render import audio as ra
from clipforge.render.measure import audio_click_ratio


def sine_file(path, seconds=6.0):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
         "-af", "volume=0.5", "-c:a", "pcm_s16le", str(path)],
        check=True,
    )  # fmt: skip


def test_filter_shape_and_fades_only_at_joins():
    edl = EDL.from_ranges("c", [(0.0, 2.0), (3.0, 5.0), (5.5, 6.0)])
    f = ra.edl_audio_filter(edl, 0)
    assert f.count("atrim") == 3 and "concat=n=3:v=0:a=1[out]" in f
    assert f.count("afade=t=in") == 2 and f.count("afade=t=out") == 2  # not on the clip's own ends


def test_cuts_mid_wave_are_click_free_and_length_is_exact(tmp_path):
    src = tmp_path / "sine.wav"
    sine_file(src)
    edl = EDL.from_ranges("c", [(0.5, 2.0013), (3.0007, 5.0)])  # cut points at arbitrary phases
    out = tmp_path / "out.wav"
    ra.render_pcm(src, 0, edl, out)
    ratio = audio_click_ratio(out, edl.cut_points_out)
    assert ratio["worst_ratio"] < 1.5  # no discontinuity larger than normal waveform movement
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True, check=True).stdout)  # fmt: skip
    assert abs(dur - edl.duration) < 0.002  # fades never change the timeline length

    # Control: the same cut with no fade does click, so the measurement is meaningful.
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
         "[0:0]atrim=0.5:2.0013,asetpts=PTS-STARTPTS[a];[0:0]atrim=3.0007:5.0,asetpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=0:a=1[o]",
         "-map", "[o]", "-c:a", "pcm_s16le", str(tmp_path / "hard.wav")],
        capture_output=True, check=False,
    )  # fmt: skip
    assert raw.returncode == 0
    hard = audio_click_ratio(tmp_path / "hard.wav", edl.cut_points_out)
    assert hard["worst_ratio"] > ratio["worst_ratio"] * 2


def test_two_pass_loudnorm_hits_target_on_quiet_and_loud_material(tmp_path):
    for gain, name in ((0.02, "quiet"), (0.9, "loud")):
        src = tmp_path / f"{name}.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=8",
             "-af", f"volume={gain},tremolo=f=3:d=0.6", str(src)],
            check=True,
        )  # fmt: skip
        m = ra.measure(src)
        norm = tmp_path / f"{name}_n.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-af",
                ra.normalise_filter(m),
                "-ar",
                "48000",
                str(norm),
            ],
            check=True,
        )
        enc = tmp_path / f"{name}.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(norm),
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(enc),
            ],
            check=True,
        )
        rep = ra.loudness_report(enc)
        assert abs(rep["lufs"] - (-14.0)) <= 1.0, (name, rep)
        assert rep["true_peak_dbtp"] <= -1.0, (name, rep)
        assert np.isfinite(rep["lufs"])
