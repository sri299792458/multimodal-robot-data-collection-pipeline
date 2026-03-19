# Data Pipeline V1

This directory contains the V1 raw-capture and LeRobot conversion stack.

The implementation contract lives in [V1_SPEC.md](./V1_SPEC.md). The running implementation log lives in [notes/running-notes.md](./notes/running-notes.md).


## Current Scope

The non-hardware V1 path is in place:

- stable `/spark/...` topic contract for robot, camera, and tactile streams
- raw episode recording as one rosbag per demo
- raw-to-LeRobot conversion for the `multisensor_20hz` profile
- dummy-data eval path
- RealSense SDK bridge with host-capture timestamp semantics
- GelSight ROS2 bridge process for the declared tactile topics

The remaining work is live hardware validation and any fixes that fall out of that.


## Runtime Split

Live ROS capture should use system ROS Jazzy and `/usr/bin/python3`.

Offline conversion and LeRobot export should use the local `.venv` created by:

```bash
./data_pipeline/setup_converter_env.sh
```


## One-Time Setup

### Offline converter environment

```bash
./data_pipeline/setup_converter_env.sh
source .venv/bin/activate
```

### RealSense ROS2 bridge build

```bash
source /opt/ros/jazzy/setup.bash
./data_pipeline/setup_realsense_contract_runtime.sh
source install/spark_realsense_bridge/setup.bash
```


## Launching The Sensor Contract

To launch the RealSense contract publishers:

```bash
source /opt/ros/jazzy/setup.bash
source install/spark_realsense_bridge/setup.bash
ros2 launch data_pipeline/launch/realsense_contract.launch.py \
  wrist_serial_no:=<WRIST_SERIAL> \
  scene_serial_no:=<SCENE_SERIAL>
```

To launch the GelSight contract publishers:

```bash
/usr/bin/python3 data_pipeline/gelsight_bridge.py --sensor-name left --device-path /dev/v4l/by-id/<LEFT_GELSIGHT>
/usr/bin/python3 data_pipeline/gelsight_bridge.py --sensor-name right --device-path /dev/v4l/by-id/<RIGHT_GELSIGHT>
```

Or with the existing launch wrapper:

```bash
ros2 launch data_pipeline/launch/gelsight_contract.launch.py \
  left_device_path:=/dev/v4l/by-id/<LEFT_GELSIGHT> \
  right_device_path:=/dev/v4l/by-id/<RIGHT_GELSIGHT>
```

The arm-side `/spark/{arm}/...` robot topics still come from the Teleop runtime in [TeleopSoftware](../TeleopSoftware/).


## Recording One Episode

Dry-run the topic selection first:

```bash
/usr/bin/python3 data_pipeline/record_episode.py \
  --dataset-id spark_multisensor_v1 \
  --task-name pick_place \
  --robot-id spark_bimanual \
  --operator <operator_name> \
  --sensors-file data_pipeline/configs/sensors.example.yaml \
  --dry-run
```

Record one episode:

```bash
/usr/bin/python3 data_pipeline/record_episode.py \
  --dataset-id spark_multisensor_v1 \
  --task-name pick_place \
  --robot-id spark_bimanual \
  --operator <operator_name> \
  --sensors-file data_pipeline/configs/sensors.example.yaml
```

Each episode is written under `raw_episodes/<episode_id>/` with:

- `bag/`
- `episode_manifest.json`
- `notes.md`


## Offline Conversion

Convert one episode:

```bash
source .venv/bin/activate
python data_pipeline/convert_episode_bag_to_lerobot.py \
  raw_episodes/<episode_id> \
  --published-root published
```

## Standing Eval

Run the no-hardware eval path:

```bash
source .venv/bin/activate
python data_pipeline/validate_eval_set.py \
  --work-root /tmp/pipeline_eval \
  --clean
```

Once a real raw episode exists, include it:

```bash
python data_pipeline/validate_eval_set.py \
  --work-root /tmp/pipeline_eval \
  --real-episode raw_episodes/<episode_id> \
  --require-real \
  --clean
```


## Sensor Metadata Overrides

`record_episode.py` can merge operator-supplied sensor metadata into the manifest with `--sensors-file`.

Start from [configs/sensors.example.yaml](./configs/sensors.example.yaml) and replace the placeholder serials and calibration references with the real inventory values for the capture rig.
