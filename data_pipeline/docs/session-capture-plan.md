# Session Capture Plan V2

## Purpose

This document defines the session-level object that sits between:

- the shared V2 topic contract
- live hardware discovery
- local defaults and rig facts
- operator choices for this session
- published-profile compatibility

The goal is to stop mixing these different concerns into one layer.


## Ground Rules

### Discovery is truth

If a device is not discovered, it is not a live session device.

Presets and local overlays may describe expected devices, but they must not fabricate live devices in the session plan.

### Presets and overlays are expectations, not reality

Presets and local overlays may contribute:

- expected devices
- default enabled flags
- default role suggestions
- display labels
- rig metadata

They must not claim that an undiscovered device is present.

### The operator confirms intent, not hardware identity

The operator may choose:

- which discovered devices are enabled for this session
- whether the suggested canonical role for a discovered device is correct
- which optional discovered devices are recorded

The operator must not choose:

- new canonical role names
- ad hoc topic names
- device kind
- device identifier
- timestamp semantics

### Published profiles are conversion contracts

Published profiles matter for:

- profile compatibility checks
- later conversion into a fixed dataset schema

They do not define what hardware exists in the live session.


## Design Objects

### Shared contract

The shared contract defines:

- canonical role vocabulary
- canonical raw topic names
- timestamp semantics
- dataset-facing field semantics

See [topic-contract.md](./topic-contract.md).

### Discovered device

A discovered device is a live hardware fact seen on the current machine.

Examples:

- `realsense/130322273305`
- `realsense/213622251272`
- `gelsight/28D8PXEC`

A discovered device may carry:

- `device_id`
- `kind`
- `model`
- `serial_number`
- `device_path` when relevant
- optional live metadata

### Expected device

An expected device comes from:

- a checked-in preset
- a local overlay

It represents a remembered lab setup, for example:

- `scene_1` is usually RealSense `213622251272`
- `lightning_finger_left` is usually GelSight `28D8PXEC`

Expected devices are not live devices.

### Resolved session device

A resolved session device is a discovered device after the system has applied:

- overlay and preset matches
- default enable flags
- default role suggestions
- operator confirmation or correction

This is the object the session actually launches and records.

### Session capture plan

The session capture plan is the resolved session truth:

- discovered devices
- expected devices
- missing expected devices
- resolved session devices
- selected topics
- local overlays used
- published-profile compatibility


## Canonical Role Vocabulary

The first V2 vocabulary is:

- arms
  - `lightning`
  - `thunder`
- wrist cameras
  - `lightning_wrist_1`
  - `thunder_wrist_1`
- scene cameras
  - `scene_1`
  - `scene_2`
  - `scene_3`
- tactile sensors
  - `lightning_finger_left`
  - `lightning_finger_right`
  - `thunder_finger_left`
  - `thunder_finger_right`

Rules:

- canonical role names are lab-controlled
- operators do not invent canonical names at runtime
- display labels may differ locally, but canonical role names must stay stable
- role assignment must be filtered by device kind

### Role-to-kind constraints

- `realsense` may resolve only to:
  - `lightning_wrist_1`
  - `thunder_wrist_1`
  - `scene_1`
  - `scene_2`
  - `scene_3`
- `gelsight` may resolve only to:
  - `lightning_finger_left`
  - `lightning_finger_right`
  - `thunder_finger_left`
  - `thunder_finger_right`


## Role To Topic Mapping

The session capture plan resolves canonical roles into canonical V2 raw topics.

Examples:

- `lightning_wrist_1` maps to:
  - `/spark/cameras/lightning/wrist_1/color/image_raw`
  - `/spark/cameras/lightning/wrist_1/depth/image_rect_raw`
- `scene_1` maps to:
  - `/spark/cameras/world/scene_1/color/image_raw`
  - `/spark/cameras/world/scene_1/depth/image_rect_raw`
- `lightning_finger_left` maps to:
  - `/spark/tactile/lightning/finger_left/color/image_raw`
  - `/spark/tactile/lightning/finger_left/depth/image_raw`
  - `/spark/tactile/lightning/finger_left/marker_offset`

The session plan does not invent ad hoc topic names. It only resolves canonical role names into the canonical V2 surface.


## Session Workflow

The intended workflow is:

1. discover live devices
2. load local overlays and the chosen preset
3. build expected-device matches and identify missing expected devices
4. suggest canonical roles for discovered devices
5. let the operator confirm or correct those suggested roles once
6. let the operator enable or disable discovered devices for this session
7. compute selected topics and profile compatibility
8. record multiple episodes under that session plan

The operator should not redo this flow for every episode unless the rig changed.


## UI Rules

### Session Devices table

The main device table must show discovered devices only.

It must not show fabricated rows for:

- preset-only devices
- overlay-only devices
- manually typed devices

Allowed row actions:

- toggle `Record`
- change the canonical role within the allowed role set for that device kind

Read-only row fields:

- `Kind`
- `Identifier`
- `Model`
- discovery-backed device identity

The table must not support:

- `Add Camera`
- `Add GelSight`
- deleting discovered devices from the inventory view

If the operator does not want a discovered device in this session, they should uncheck `Record`.

### Expected but Missing

Devices expected by preset or overlay but not discovered must appear in a separate section, for example:

- `Expected but Missing`

Those entries are useful for operator clarity, but they are not live session devices and must not be launch targets.

### Source or match labels

If the UI shows an origin column, it must describe expectation matching, not device existence.

Acceptable meanings include:

- `discovered`
- `matched overlay`
- `matched preset`
- `unmatched`

Unacceptable meaning:

- `manual` as if the operator authored a live device into existence


## Preset And Overlay Semantics

### Preset

A preset may define:

- metadata defaults
- expected devices
- default enabled roles
- default dataset and robot identifiers

A preset must not be treated as a live device list.

### Local overlay

A local overlay may define:

- serial-to-role defaults
- display labels
- enabled-by-default flags
- calibration references
- geometry references or matrices
- other rig-specific facts

A local overlay may help match a discovered device to a canonical role.
It must not redefine canonical meaning.


## Session Capture Plan Shape

The storage format may be JSON or YAML, but the resolved object should contain these sections:

```yaml
schema_version: 2
contract_version: v2
session_id: session-20260323-101500

active_arms:
  - lightning

local_overlays:
  - path: data_pipeline/configs/sensors.local.yaml
    exists: true
    kind: local_defaults

expected_devices:
  - expected_id: preset/lightning_wrist_1
    kind: realsense
    expected_role: lightning_wrist_1
    preferred_identifier: "130322273305"
    required: true
    source: preset

discovered_devices:
  - device_id: realsense/130322273305
    kind: realsense
    model: Intel RealSense D405
    serial_number: "130322273305"
    matched_by:
      - overlay
      - preset

  - device_id: realsense/213622251272
    kind: realsense
    model: Intel RealSense D455
    serial_number: "213622251272"
    matched_by:
      - overlay
      - preset

  - device_id: realsense/f1380660
    kind: realsense
    model: Intel RealSense L515
    serial_number: "f1380660"
    matched_by: []

missing_expected_devices:
  - expected_id: preset/lightning_finger_left
    kind: gelsight
    expected_role: lightning_finger_left
    preferred_identifier: "28D8PXEC"
    source: preset

resolved_devices:
  - device_id: realsense/130322273305
    kind: realsense
    enabled: true
    suggested_role: lightning_wrist_1
    resolved_role: lightning_wrist_1
    source: discovered

  - device_id: realsense/213622251272
    kind: realsense
    enabled: true
    suggested_role: scene_1
    resolved_role: scene_1
    source: discovered

  - device_id: realsense/f1380660
    kind: realsense
    enabled: false
    suggested_role: scene_2
    resolved_role: scene_2
    source: discovered

selected_topics:
  - /spark/session/teleop_active
  - /spark/lightning/robot/joint_state
  - /spark/lightning/robot/eef_pose
  - /spark/lightning/robot/tcp_wrench
  - /spark/lightning/robot/gripper_state
  - /spark/lightning/teleop/cmd_joint_state
  - /spark/lightning/teleop/cmd_gripper_state
  - /spark/cameras/lightning/wrist_1/color/image_raw
  - /spark/cameras/lightning/wrist_1/depth/image_rect_raw
  - /spark/cameras/world/scene_1/color/image_raw
  - /spark/cameras/world/scene_1/depth/image_rect_raw

selected_extra_topics: []

profile_compatibility:
  publishable_profiles:
    - name: multisensor_20hz_lightning
      compatible: true
  incompatible_profiles:
    - name: multisensor_20hz
      compatible: false
      reasons:
        - missing required arm thunder
```


## Episode Snapshot Rule

Each episode manifest should snapshot the relevant resolved session information:

- active arms
- local overlays used
- missing expected devices
- resolved devices and roles
- selected topics
- profile compatibility at record time

The operator resolves the session once, but every episode remains self-describing.
