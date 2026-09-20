import numpy as np

from clipforge.edl import EDL
from clipforge.reframe import plan as pl
from clipforge.reframe.camera import Rect
from clipforge.reframe.solve import FrameSpec, solve
from clipforge.render.video import compose


def gradient(w=1920, h=1080):
    """Left half red, right half blue, with a white square at a known place."""
    f = np.zeros((h, w, 3), dtype=np.uint8)
    f[:, : w // 2] = (0, 0, 220)  # BGR red
    f[:, w // 2 :] = (220, 0, 0)  # BGR blue
    f[100:300, 200:400] = 255
    return f


def test_single_crop_maps_exactly_the_requested_region():
    f = gradient()
    out = compose(f, FrameSpec("single", [Rect(100, 0, 608, 1080)]))
    assert out.shape == (1920, 1080, 3)
    # White square (x 200-400, y 100-300) lands at (100..) scaled by 1080/608 horizontally, 1920/1080 vertically.
    assert (
        out[round(200 * 1920 / 1080), round((300 - 100) * 1080 / 608)].min() > 240
    )  # centre of the square
    assert out[1800, 500, 2] > 200 and out[1800, 500, 0] < 30  # red side, away from the square


def test_stacked_shows_two_crops_top_and_bottom():
    f = gradient()
    out = compose(f, FrameSpec("stacked", [Rect(0, 0, 960, 1080), Rect(960, 0, 960, 1080)]))
    assert out.shape == (1920, 1080, 3)
    assert out[100, 540, 2] > 200 and out[100, 540, 0] < 30  # top half: red crop
    assert out[1800, 540, 0] > 200 and out[1800, 540, 2] < 30  # bottom half: blue crop


def test_screen_cam_puts_content_on_top_and_cam_below():
    f = gradient()
    out = compose(f, FrameSpec("screen_cam", [Rect(0, 0, 1920, 1080), Rect(1400, 200, 400, 490)]))
    assert out.shape == (1920, 1080, 3)
    top_h = round(1080 * 1080 / 1920)
    assert (
        out[top_h // 2, 200, 2] > 200
    )  # screen content fills the width at the top (red left half)
    assert (
        out[top_h + 200, 540, 0] > 200 and out[top_h + 200, 540, 2] < 40
    )  # cam crop below is the blue side


def test_fit_blur_keeps_the_whole_frame_centred_over_a_darkened_blur():
    f = gradient()
    out = compose(f, FrameSpec("fit_blur", [Rect(0, 0, 1920, 1080)]))
    assert out.shape == (1920, 1080, 3)
    fit_h = round(1080 * 1080 / 1920)
    y0 = (1920 - fit_h) // 2
    assert out[y0 + fit_h // 2, 100, 2] > 200  # sharp foreground band (red left)
    assert out[100, 100].max() < 160  # background is the darkened blur, not full brightness


def test_punch_in_alternates_zoom_on_jump_cuts_only():
    from tests.test_reframe import analysis

    an = analysis([(640, 260, 200)], 12)
    scene = pl.scene_from_analysis(an)
    plan = pl.build_plan(scene, [(0, 12)], [], 405)
    edl = EDL.from_ranges("c", [(0, 4), (5, 8), (9, 12)])
    plain = solve(scene, plan, edl, 30.0)
    punched = solve(scene, plan, edl, 30.0, punch_in=1.08)
    f_seg0, f_seg1 = 15, 4 * 30 + 15  # inside segment 0 and 1
    assert punched.frames[f_seg0].rects[0].w == plain.frames[f_seg0].rects[0].w
    assert abs(punched.frames[f_seg1].rects[0].w - plain.frames[f_seg1].rects[0].w / 1.08) < 1e-6
    assert punched.frames[2 * 4 * 30 - 10 + 30].rects[0].w > 0
