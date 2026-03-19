# Data Pipeline Agent Notes

## Purpose

`data_pipeline/` is the clean collection and conversion layer for SPARK.

It is responsible for:

- raw episode recording
- metadata capture
- raw-to-published conversion
- validation and diagnostics

It is not responsible for:

- teleop runtime logic
- controller logic
- policy inference


## Repo Boundary

- Treat `TeleopSoftware/` as a legacy ROS topic producer.
- Do not import random teleop internals into new pipeline code unless there is no cleaner topic-based option.
- Prefer consuming the stable `/spark/...` topic contract.


## Environment

- Use system ROS 2 Jazzy for live capture and bag recording.
- Offline conversion and analysis may use a separate Python environment.
- Do not make live ROS capture depend on Conda activation.


## Main Files

- `V1_SPEC.md`: implementation-facing v1 contract
- `docs/topic-contract.md`: stable topic names and timestamp meanings
- `docs/dataset-mapping.md`: raw-to-published mapping rules
- `configs/multisensor_20hz.yaml`: first published profile
- `notes/running-notes.md`: dated implementation log


## Build Order

1. finalize `/spark/...` topic contract
2. add stamped bridge from legacy runtime topics
3. implement `record_episode.py`
4. implement `generate_dummy_episode.py`
5. implement `convert_episode_bag_to_lerobot.py`
6. validate on dummy data
7. validate on one real episode


## Validation Rules

- Do not change timestamp semantics silently.
- Do not change vector ordering silently.
- Keep `multisensor_20hz` bimanual with fixed arm order:
  - `lightning`
  - `thunder`
- Save machine-readable diagnostics for every conversion run.
- Update `notes/running-notes.md` when the implementation plan changes in a meaningful way.
