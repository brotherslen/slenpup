# PT verifier (phase 2)

Runs on the NUC, offline. This first piece is **calibration mode**: it measures your own joint-angle ranges per exercise and per side, and suggests the rep and hold thresholds that go into `exercises.yaml`. It never signs anything, holds no keys, and makes no network calls. Videos stay on the machine.

## Setup (Windows 11, NUC)

```powershell
py -3.12 -m venv C:\ptv
C:\ptv\Scripts\pip install -e "C:\path\to\slenpup\verifier[test]"
mkdir C:\path\to\slenpup\verifier\models
Invoke-WebRequest https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task -OutFile C:\path\to\slenpup\verifier\models\pose_landmarker_full.task
C:\ptv\Scripts\python -m pytest C:\path\to\slenpup\verifier
```

The model file used for development had SHA-256 `4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad` (downloaded 2026-10-03). Each calibration result records the model's hash, so results from different model versions can be told apart.

## Calibration week

One clip per exercise, and for per-side exercises one clip per side. Name each clip

```
YYYY-MM-DD_<exercise>_<side>_<count>[-note].mp4
2026-10-05_wall_slides_both_10.mp4
2026-10-05_prone_y_left_8.mp4
2026-10-05_prone_y_right_4.mp4
2026-10-05_bear_hold_both_5.mp4          (count = number of holds)
2026-10-05_bulgarian_split_squat_left_9-set2.mp4
```

`<count>` is what you actually did. It's how the report checks whether its thresholds would have counted you right. Exercise keys are in `exercises.yaml`.

Camera: phone in landscape, side-on, roughly hip height, far enough back that your whole body stays in frame for the whole set. For per-side exercises put the working side nearest the camera. Start and end each clip at rest.

Then either drop clips into `calibration/inbox/` with the watcher running:

```powershell
C:\ptv\Scripts\python -m ptverifier.calibrate watch
```

or process one directly:

```powershell
C:\ptv\Scripts\python -m ptverifier.calibrate record path\to\clip.mp4
```

After a week, pool everything:

```powershell
C:\ptv\Scripts\python -m ptverifier.calibrate report            # every exercise
C:\ptv\Scripts\python -m ptverifier.calibrate report prone_y
```

The report gives per-side thresholds, re-counts every clip with them (`matches_declared: 7/7` is what you want), shows left/right asymmetry, and prints a `thresholds:` snippet to paste into `exercises.yaml` once you've reviewed it. Clips where it disagrees with your count are the ones to look at: usually the camera angle, a joint out of frame, or a rep that really was short.

Each result is a JSON summary (frame timing, pose detection rate, per-joint visibility for both sides, percentiles for every metric on both sides, detected extremes) plus a `.npz` of the smoothed series, so thresholds can be recomputed later without re-running the model.

## Not built yet

Session verification, challenge words, Whisper, signing, and the relayer. Those come after the contract's fork test passes. PT-B exercises are marked TBD in `exercises.yaml` until you fill in their ranges.
