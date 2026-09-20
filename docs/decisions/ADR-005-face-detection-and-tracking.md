# ADR-005: face detector, tracker and active speaker

- Status: accepted
- Date: 2026-09-19

## Candidates
| Detector | Runtime | Notes |
|---|---|---|
| **SCRFD-500M** (InsightFace `buffalo_sc`) | onnxruntime, own decoder | chosen |
| YuNet 2023mar (OpenCV zoo) | `cv2.FaceDetectorYN` | tested |
| MediaPipe | not tried | needs `opencv-contrib-python`, which clashes with the installed `opencv-python-headless` (both provide `cv2`, and PyAV/OpenCV already collide on libavdevice) |
| YOLO-face / TalkNet-ASD | not tried | weights hosting uncertain; ASD kept behind a future flag |

## Benchmark (`scripts/bench_faces.py`, M1, 120 frames each at 2 fps from real fixtures, 1280 px wide)
| | SCRFD-500M | YuNet |
|---|---|---|
| Interview 1 (Kende), face recall | 99% | 99% |
| Interview 2 (Hannah), face recall | 100% | 100% |
| Lecture with Zoom gallery (Flynn), frames with a face | 79% | 79% |
| Screen recording, false-positive frames | **5%** | 22% |
| Static graphic, false-positive frames | 0% | 0% |
| Time per frame | **11 ms** | 17 ms |

Ground truth is by construction (single-person interviews, a screen recording and a graphic with no faces) plus a
visual check of annotated frames: SCRFD boxes were tight and correct on every real face. The Flynn video turned out to
contain a 16-face Zoom gallery, so "exactly one face" does not apply there; detection itself was right.
The set is small (5 clips) and not a labelled benchmark; a mislabelled-frame study is future work.

## Decision
SCRFD-500M for detection (fewer false positives, faster, tighter boxes, gives eye landmarks used for the eye-line).
A ByteTrack-style two-stage IoU tracker (own implementation, scipy assignment): stable IDs through noise,
0.3-1.5 s occlusions, and no false merge across long absences (tested on synthetic scenes).

## Active speaker
Baseline: per (speaker, face track) mouth-motion contrast (frame difference of the lower face region while that
speaker talks minus while others talk), one-to-one Hungarian assignment over the whole clip. This is a lip-motion
proxy, not lip landmarks (no landmark model is installed). Verified on synthetic two-person scenes only; **no real
multi-speaker footage has been evaluated yet** (no CC conversation fixture was fetched), so real active-speaker
accuracy is unmeasured. TalkNet-ASD/Light-ASD remain an optional future flag.

## Isolation
OpenCV, onnxruntime and PyAV must not share a process (duplicate libavdevice, exit-time crash observed and fixed):
face analysis, visual signals and the render face check each run in their own subprocess.
