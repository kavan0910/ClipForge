import numpy as np

from clipforge.vision.detect import Box, iou, nms
from clipforge.vision.track import FaceTracker, synthetic_scene


def run(frames):
    tr = FaceTracker()
    for t, dets in frames:
        tr.update(t, dets)
    return tr.finish()


def long_tracks(tracks, min_len=30):
    return [t for t in tracks if len(t.boxes) >= min_len]


def test_iou_and_nms():
    a, b = Box(0, 0, 10, 10, 0.9), Box(5, 0, 10, 10, 0.8)
    assert iou(a, a) == 1 and 0.3 < iou(a, b) < 0.4 and iou(a, Box(50, 50, 5, 5, 1)) == 0
    assert len(nms([a, b, Box(100, 100, 10, 10, 0.7)], 0.3)) == 2


def test_two_people_keep_two_stable_ids_despite_noise_and_false_positives():
    frames, _ = synthetic_scene(2, 30, seed=3)
    main = long_tracks(run(frames))
    assert len(main) == 2  # exactly two lasting identities, no ID switches


def test_track_survives_a_short_occlusion_with_the_same_id():
    frames, _ = synthetic_scene(2, 30, seed=1, occlusion=(10.0, 11.0))
    main = long_tracks(run(frames))
    assert len(main) == 2
    person0 = min(main, key=lambda t: t.boxes[0][1].cx)
    times = [t for t, _ in person0.boxes]
    assert min(times) < 1 and max(times) > 28  # one identity across the gap


def test_long_disappearance_starts_a_new_track():
    frames, _ = synthetic_scene(1, 30, seed=2, occlusion=(8.0, 14.0))
    main = long_tracks(run(frames), 20)
    assert len(main) == 2  # gone longer than max_gap: a fresh identity, not a wrong merge


def test_tracks_follow_the_true_positions():
    frames, truth = synthetic_scene(1, 10, seed=4)
    (trk,) = long_tracks(run(frames), 20)
    err = [abs(b.cx - truth[0][round(t * 8)][1]) for t, b in trk.boxes]
    assert np.mean(err) < 4
