# PT verifier (phase 2)

Runs on the NUC, offline. The verifier confirms **position**: for each exercise in the day's session, your joints have to sit inside that exercise's calibrated position for at least 5 seconds straight. It doesn't count reps. Getting into every position is the bar; doing the work once there is on you.

This first piece is **calibration mode**. It learns what each position looks like on your body, separately for left and right, and writes the bands that go into `exercises.yaml`. It never signs anything, holds no keys, and makes no network calls. Videos stay on the machine.

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

One clip per exercise, and for per-side exercises one clip per side. In each clip, get into the exercise's position within a few seconds and **hold it still for about 10 seconds**. Name each clip

```
YYYY-MM-DD_<exercise>_<side>[-note].mp4
2026-10-05_wall_slides_both.mp4
2026-10-05_prone_y_left.mp4
2026-10-05_prone_y_right.mp4
2026-10-05_bear_hold_both.mp4
2026-10-05_bulgarian_split_squat_left-second.mp4
```

Exercise keys are in `exercises.yaml`. Calibration uses the longest still stretch in the clip, so keep the standing-around time before and after short; if you stand still longer than you hold the position, it learns standing. Every result lists the still stretches it found so you can check.

Camera: phone in landscape, side-on, roughly hip height, far enough back that your whole body stays in frame. For per-side exercises put the working side nearest the camera.

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

For each exercise and side the report gives:

- `bands`: the low-high range per metric, from your clips plus a margin (8° for angles).
- `own_clips_pass`: every clip of this exercise re-checked against the pooled bands. Want `7/7`.
- `false_matches`: clips of *other* exercises that would pass this one's bands. Want none. If, say, prone Y and prone T match each other, those two positions aren't distinguishable from this camera angle and need a different view or an extra metric.
- `left_minus_right_midpoint`: how far your left and right positions differ per metric.
- `yaml_snippet`: paste into `exercises.yaml` once you've reviewed it.

Each result is a JSON summary (frame timing, pose detection rate, visibility per side, metric percentiles over the held stretch) plus a `.npz` of the raw landmarks, so bands can be recomputed after changing `exercises.yaml` without re-running the model.

## Not built yet

Daily session verification (continuity, challenge words via Whisper, each position held 5 s), signing, and the relayer. Those come after the contract's fork test passes. PT-B exercises are marked TBD in `exercises.yaml` until you fill in their ranges.
