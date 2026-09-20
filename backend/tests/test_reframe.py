import numpy as np
import pytest

from clipforge.edl import EDL
from clipforge.reframe import camera as cam
from clipforge.reframe import plan as pl
from clipforge.reframe.solve import solve

W, H, FPS = 1280, 720, 8.0


def analysis(people, seconds=30.0, speaking=(), static=None):
    """Synthetic analysis JSON. people: list of (cx, cy, size). speaking: (t0, t1, person)."""
    frames = []
    for k in range(int(seconds * FPS)):
        t = k / FPS
        faces = []
        for i, (cx, cy, sz) in enumerate(people):
            if static is not None and i in static:
                x, y = cx, cy
            else:
                x, y = cx + 8 * np.sin(t * 0.6 + i), cy + 4 * np.cos(t * 0.4 + i)
            talking = any(a <= t < b and p == i for a, b, p in speaking)
            faces.append({"id": i + 1, "x": x - sz / 2, "y": y - sz / 2, "w": sz, "h": sz, "score": 0.9,
                          "eye_y": y - 0.1 * sz, "mouth": 0.05 if talking else 0.004})  # fmt: skip
        frames.append({"t": round(t, 3), "faces": faces})
    return {"fps": FPS, "width": W, "height": H, "start": 0.0, "end": seconds, "frames": frames}


def turns_for(speaking):
    return [pl.Turn(a, b, f"S{p}") for a, b, p in speaking]


def test_speaker_to_track_mapping_uses_mouth_motion():
    sp = [(0, 5, 0), (5, 10, 1), (10, 15, 0), (15, 20, 1)]
    scene = pl.scene_from_analysis(analysis([(400, 250, 160), (900, 250, 160)], 20, sp))
    m = pl.map_speakers_to_tracks(scene, turns_for(sp))
    assert m["S0"][0] == 1 and m["S1"][0] == 2 and m["S0"][1] > 0.5


def test_single_speaker_talking_head_is_a_steady_single_layout():
    scene = pl.scene_from_analysis(analysis([(640, 260, 200)], 20))
    segs = pl.build_plan(scene, [(0, 20)], [], crop_w=405)
    assert [s.layout for s in segs] == ["single"] and segs[0].focus[0][2] == 1


def test_slow_alternation_follows_speaker_with_delay_and_dwell():
    sp = [(0, 8, 0), (8, 16, 1), (16, 24, 0)]
    scene = pl.scene_from_analysis(analysis([(200, 260, 160), (1080, 260, 160)], 24, sp))
    turns = turns_for(sp)
    mapping = pl.map_speakers_to_tracks(scene, turns)
    tl = pl.focus_timeline(scene, turns, mapping)
    assert [x[2] for x in tl] == [1, 2, 1]
    assert tl[1][0] == pytest.approx(8.0 + pl.SWITCH_DELAY, abs=0.25)  # waits ~0.4 s after onset
    assert all(b - a >= pl.MIN_FOCUS_DWELL for a, b, _ in tl)


def test_fast_alternation_uses_stacked_split():
    sp = [(k * 1.0, k * 1.0 + 1.0, k % 2) for k in range(0, 24)]
    scene = pl.scene_from_analysis(analysis([(200, 260, 160), (1080, 260, 160)], 24, sp))
    seg = pl.plan_shot(scene, 0, 24, turns_for(sp), 405)
    assert seg.layout == "stacked" and len(seg.tracks) == 2


def test_two_people_close_together_share_one_crop():
    scene = pl.scene_from_analysis(
        analysis([(560, 260, 120), (720, 260, 120)], 20, [(0, 10, 0), (10, 20, 1)])
    )
    seg = pl.plan_shot(scene, 0, 20, turns_for([(0, 10, 0), (10, 20, 1)]), 405)
    assert seg.layout == "two"


def test_no_faces_and_galleries_fall_back_to_blurred_fit():
    empty = pl.scene_from_analysis(
        {**analysis([], 10), "frames": [{"t": k / FPS, "faces": []} for k in range(80)]}
    )
    assert pl.plan_shot(empty, 0, 10, [], 405).layout == "fit_blur"
    grid = pl.scene_from_analysis(
        analysis([(150 + 220 * (i % 5), 100 + 200 * (i // 5), 90) for i in range(10)], 10)
    )
    assert pl.plan_shot(grid, 0, 10, [], 405).layout == "fit_blur"


def test_small_static_corner_face_is_detected_as_facecam():
    scene = pl.scene_from_analysis(analysis([(1150, 600, 90)], 20, static={0}))
    seg = pl.plan_shot(scene, 0, 20, [], 405)
    assert seg.layout == "screen_cam" and seg.cam_track == 1


def test_large_static_talking_head_near_an_edge_is_not_a_facecam():
    # Kende-like framing: one big face, static, left of centre. Must be a normal single layout.
    scene = pl.scene_from_analysis(analysis([(300, 200, 240)], 20, static={0}))
    assert pl.plan_shot(scene, 0, 20, [], 405).layout == "single"


def test_hysteresis_merges_flicker_and_overrides_win():
    a = pl.LayoutSegment(0, 10, "single")
    b = pl.LayoutSegment(10, 10.6, "fit_blur")  # 0.6 s blip < 1.2 s
    c = pl.LayoutSegment(10.6, 20, "single")
    merged = pl.enforce_hysteresis([a, b, c])
    assert [s.layout for s in merged] == ["single"] and merged[0].t0 == 0 and merged[0].t1 == 20
    scene = pl.scene_from_analysis(analysis([(640, 260, 200)], 20))
    plan = pl.build_plan(scene, [(0, 20)], [], 405, overrides=[pl.LayoutSegment(5, 12, "fit_blur")])
    assert [(s.layout, s.t0, s.t1) for s in plan] == [
        ("single", 0, 5),
        ("fit_blur", 5, 12),
        ("single", 12, 20),
    ]


def test_follow_holds_still_inside_the_dead_zone_and_limits_velocity():
    dt = 1 / cam.PATH_FPS
    n = int(6 / dt)
    wiggle = 640 + 6 * np.sin(np.arange(n) * dt * 2)
    path = cam.follow(wiggle, dt, 405)
    assert np.ptp(path) < 2.0  # small drift never moves the camera
    step = np.where(np.arange(n) * dt < 2, 300.0, 700.0)  # subject jumps 400 px
    p = cam.follow(step, dt, 405)
    v = np.abs(np.diff(p)) / dt
    assert (
        v.max() <= cam.V_MAX * 405 * 1.05 and p[-1] > 640
    )  # follows, but no faster than the limit


def test_pan_jerk_is_below_threshold_and_cut_rule_picks_cut_or_pan():
    dt = 1 / cam.PATH_FPS
    grid = np.arange(0, 20, dt)
    a = np.full(len(grid), 300.0)
    near, far = np.full(len(grid), 500.0), np.full(len(grid), 1100.0)
    xs, cuts = cam.stitch_focus(grid, [(0, 10, 1), (10, 20, 2)], {1: a, 2: near}, 1280)
    assert cuts == [] and 300 < xs[int(10.1 * cam.PATH_FPS)] < 500  # pan: mid-move shortly after
    xs2, cuts2 = cam.stitch_focus(grid, [(0, 10, 1), (10, 20, 2)], {1: a, 2: far}, 1280)
    assert len(cuts2) == 1 and xs2[cuts2[0]] == 1100.0  # 800 px > 35% of 1280: hard cut
    fps = 30
    frames = xs[:: int(cam.PATH_FPS / fps)] / 1280
    j = cam.path_jerk(frames, fps, [])
    assert j["p99"] < cam.JERK_P99_LIMIT and j["max"] < cam.JERK_MAX_LIMIT
    wobble = 0.5 + 0.002 * np.random.default_rng(0).standard_normal(600)  # ~2 px jitter at 1080 px
    assert cam.path_jerk(wobble, 30, [])["p99"] > 10 * cam.JERK_P99_LIMIT  # jitter is caught


def test_zoom_rule_raises_eye_line_when_the_face_sits_high():
    assert cam.zoom_for_face(200, 360, 720) == pytest.approx(1.0)  # centred: no zoom
    assert 1.2 < cam.zoom_for_face(200, 185, 720) <= cam.MAX_ZOOM  # face near the top (Kende)


def test_solve_end_to_end_keeps_the_speaker_in_crop_and_reports_jerk():
    sp = [(0, 8, 0), (8, 16, 1), (16, 24, 0)]
    an = analysis([(200, 260, 160), (1080, 260, 160)], 24, sp)
    scene = pl.scene_from_analysis(an)
    plan = pl.build_plan(scene, [(0, 24)], turns_for(sp), crop_w=405)
    edl = EDL.from_ranges("c", [(0, 24)])
    s = solve(scene, plan, edl, 30.0)
    assert len(s.frames) == 720 and all(f.layout == "single" for f in s.frames)
    inside = 0
    for i, f in enumerate(s.frames):
        t = i / 30
        person = 0 if (t < 8.4 or t >= 16.4) else 1
        cx = 200 if person == 0 else 1080
        r = f.rects[0]
        inside += r.x <= cx <= r.x + r.w
    assert inside / len(s.frames) >= 0.97  # slack only around the deliberate 0.4 s switch delay
    assert s.jerk()["p99"] < cam.JERK_P99_LIMIT and len(s.cut_frames) >= 2
