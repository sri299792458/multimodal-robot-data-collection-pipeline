# Calibration

This is the camera-calibration workflow for the pipeline.

For the design rationale behind the calibration split, see
[Calibration Design](./calibration-design.md).

The calibration system has two parts:

- `data_pipeline/configs/sensors.local.yaml`
  - local rig serial-to-sensor mapping
- `data_pipeline/configs/calibration.local.json`
  - solved camera intrinsics and extrinsics / hand-eye results

The sensors file tells the pipeline which physical device is which sensor key.
The calibration results file tells the pipeline where that camera is.

Current printed lab board:

- dictionary: `DICT_4X4_50`
- squares: `9 x 6`
- square length: `0.03 m`
- marker length: `0.022 m`

The examples below pass these board settings explicitly so the calibration run
matches the printed target.


## Generate The Board

Use this if the calibration board needs to be reprinted:

```python
import cv2
import numpy as np
from PIL import Image

cols = 9
rows = 6
square_length_mm = 30
marker_length_mm = 22
dpi = 300

px_per_mm = dpi / 25.4
page_width_px = round(11 * dpi)
page_height_px = round(8.5 * dpi)
board_width_px = round(cols * square_length_mm * px_per_mm)
board_height_px = round(rows * square_length_mm * px_per_mm)

aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
board = cv2.aruco.CharucoBoard(
    (cols, rows),
    square_length_mm,
    marker_length_mm,
    aruco_dict,
)
board_img = board.generateImage((board_width_px, board_height_px), marginSize=0, borderBits=1)

page = 255 * np.ones((page_height_px, page_width_px), dtype=np.uint8)
x0 = (page_width_px - board_width_px) // 2
y0 = (page_height_px - board_height_px) // 2
page[y0 : y0 + board_height_px, x0 : x0 + board_width_px] = board_img

img = Image.fromarray(page)
img.save("charuco_9x6_30mm_22mm_DICT4X4_50_LETTER_landscape.png", dpi=(dpi, dpi))
img.convert("RGB").save("charuco_9x6_30mm_22mm_DICT4X4_50_LETTER_landscape.pdf", resolution=dpi)
```

Print the PDF at `100%` scale and measure one square. It should be `30 mm`.


## Current Assumptions

- RealSense cameras are calibrated with a ChArUco board.
- Wrist-camera hand-eye calibration uses UR `getActualTCPPose()`.
- During wrist calibration, the active UR TCP must be the tool flange.
- Static scene cameras are solved automatically in the reference wrist arm base frame from shared board observations.


## Files

Default local files:

- `data_pipeline/configs/sensors.local.yaml`
- `data_pipeline/configs/calibration_poses.local.json`
- `data_pipeline/configs/calibration.local.json`


## 1. Fill In The Sensors File

Start from:

```bash
cp data_pipeline/configs/sensors.example.yaml data_pipeline/configs/sensors.local.yaml
```

Fill in the real serial numbers and calibration metadata for the cameras you want to calibrate.


## 2. Record Wrist Calibration Poses

Record a set of diverse robot poses while the board is visible to:

- the wrist camera that will anchor the calibration frame
- any static scene camera you want to calibrate in that same reference frame

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python data_pipeline/record_calibration_poses.py --active-arms lightning
```

To see the wrist camera while selecting poses, pass a preview camera:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python data_pipeline/record_calibration_poses.py \
  --active-arms lightning \
  --preview-camera /spark/cameras/lightning/wrist_1 \
  --sensors-file data_pipeline/configs/sensors.local.yaml \
  --squares-x 9 \
  --squares-y 6 \
  --square-length 0.03 \
  --marker-length 0.022 \
  --dictionary DICT_4X4_50
```

The preview window overlays the live marker and ChArUco counts. Record poses
when the board is clearly visible and the ChArUco count is stable.

Controls:

- `r`
  - record current pose
- `d`
  - delete last pose
- `l`
  - list poses
- `q`
  - save and quit

This writes:

- `data_pipeline/configs/calibration_poses.local.json`


## 3. Run Calibration

Calibrate all cameras found in the sensors file:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python data_pipeline/calibrate_rig.py \
  --sensors-file data_pipeline/configs/sensors.local.yaml \
  --squares-x 9 \
  --squares-y 6 \
  --square-length 0.03 \
  --marker-length 0.022 \
  --dictionary DICT_4X4_50
```

Use different board flags only if you are calibrating against a different
ChArUco target.

To calibrate only selected cameras:

```bash
python data_pipeline/calibrate_rig.py \
  --sensors-file data_pipeline/configs/sensors.local.yaml \
  --camera /spark/cameras/lightning/wrist_1 \
  --camera /spark/cameras/world/scene_1 \
  --squares-x 9 \
  --squares-y 6 \
  --square-length 0.03 \
  --marker-length 0.022 \
  --dictionary DICT_4X4_50
```

Notes:

- if any selected camera is a wrist camera, `calibrate_rig.py` requires `calibration_poses.local.json`
- if any selected camera is a static scene camera, the runner also needs a wrist camera reference:
  - if a wrist camera is already selected, it uses that
  - otherwise it auto-picks a configured wrist camera from the sensors file
  - if both `/spark/cameras/lightning/wrist_1` and `/spark/cameras/thunder/wrist_1` are available and no explicit `--reference-wrist-camera` is given, the default is `/spark/cameras/lightning/wrist_1`
    - mnemonic: lightning travels before thunder
- the runner reads factory intrinsics from the RealSense SDK and solves:
  - wrist cameras as hand-eye calibration
  - scene cameras automatically from the wrist-calibrated board observations
- scene-camera extrinsics are expressed in the selected reference wrist arm base frame, for example:
  - `lightning_base`
  - `thunder_base`

This writes:

- `data_pipeline/configs/calibration.local.json`


## 4. Validate With Click-Point Inspection

Static scene camera example:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python data_pipeline/validate_calibration_click.py \
  --camera /spark/cameras/world/scene_1
```

Wrist camera example:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
python data_pipeline/validate_calibration_click.py \
  --camera /spark/cameras/lightning/wrist_1
```

Click a pixel in the RGB image window. The tool prints:

- depth
- camera-frame point
- calibrated reference-frame point
- current tip point
- current tip delta to the clicked point

Then it asks whether to move. If you confirm:

- the validation arm moves to its configured home joint pose first
- the home orientation is reused
- the tip offset defaults to `0 0 0.162`
- the robot moves the tip to the clicked point
- the script prints the reached tip delta and exits


## 5. Recording Integration

`record_episode.py` automatically snapshots the current calibration results into `episode_manifest.json` when:

- `data_pipeline/configs/calibration.local.json` exists
- or `--calibration-file` is provided explicitly

That snapshot is stored per sensor under:

- `sensors.devices[].calibration_snapshot`

and the manifest also records:

- `sensors.sensors_file`
- `sensors.calibration_results_file`

So raw episodes stay self-describing even if local calibration files change later.
