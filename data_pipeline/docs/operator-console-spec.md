# Operator Console V2 Spec

## Goal

Build a lab-facing operator console for:

- session setup
- readiness validation
- raw recording
- conversion
- viewer launch

without turning the UI into:

- a robot-control replacement
- a device authoring tool
- a second source of truth for the data contract


## Scope

The console is a local desktop workflow wrapper around the existing runtime and data-pipeline commands.

It must orchestrate:

- `TeleopSoftware/launch_devs.py`
- `TeleopSoftware/launch.py`
- `data_pipeline/launch/realsense_contract.launch.py`
- `data_pipeline/launch/gelsight_contract.launch.py`
- `data_pipeline/record_episode.py`
- `data_pipeline/convert_episode_bag_to_lerobot.py`

It must not:

- replace Teleop control logic
- invent new device identities
- invent new canonical roles
- invent new raw topic names


## Governing Principle

The operator console must follow the session-capture-plan model, not redefine it.

See:

- [session-capture-plan.md](./session-capture-plan.md)
- [topic-contract.md](./topic-contract.md)
- [../V2_SPEC.md](../V2_SPEC.md)


## Core Separation

The UI must keep these concepts separate:

### Live discovered devices

What hardware is actually present right now.

### Expected devices

What a preset or local overlay says is usually present.

### Resolved session selection

Which discovered devices are enabled for this session and what canonical roles they resolve to.

### Published profile compatibility

Which published datasets the resolved session could later convert into.

The current UI must never collapse these into one editable list of “devices”.


## Workflow

The intended operator flow is:

1. choose preset and episode metadata
2. discover live devices
3. review discovered devices and any missing expected devices
4. enable or disable discovered devices for this session
5. confirm or correct canonical role suggestions
6. start session
7. validate
8. record one or more episodes
9. convert
10. open viewer

The operator should not need to rebuild the rig model every episode.


## UI Model

The console should have these sections:

### 1. Preset and metadata

Includes:

- preset
- dataset id
- robot id
- task name
- language instruction
- operator
- active arms
- sensors file
- viewer URL

This section is about session defaults and recording metadata, not live device identity.

The V2 console must not expose a freeform `extra topics` field in the main workflow.

### 2. Discovered Devices

This is the primary device table.

It must show discovered devices only.

Recommended columns:

- `Record`
- `Kind`
- `Model`
- `Identifier`
- `Suggested Role`
- `Resolved Role`
- `Match`

Required behavior:

- `Kind`, `Model`, and `Identifier` are read-only
- `Record` is editable
- `Resolved Role` is editable
- role choices are filtered by device kind

Forbidden behavior:

- add arbitrary camera rows
- add arbitrary GelSight rows
- delete discovered rows
- type a new device identifier into the main table
- change device kind in the main table

If the operator does not want a device in this session, they uncheck `Record`.

### 3. Expected but Missing

This section is separate from discovered devices.

It must show devices expected by preset or overlay that are not currently discovered.

Examples:

- expected `lightning_finger_left`, not found
- expected `scene_2`, not found

These entries must never be launch targets.

### 4. Session Plan

This section shows the resolved plan:

- default published profile
- selected topics
- publishable profiles
- blocked profiles
- overlays used
- resolved devices

This is a read-only explanation pane.

### 5. Subsystem Health

This section shows:

- SPARK devices
- Teleop GUI
- RealSense
- GelSight
- recorder
- converter

Readiness must be based on measured health, not merely “process started”.

### 6. Action output and logs

The operator must always be able to see:

- exact failure point
- last validation output
- last recording check
- last conversion output
- recent process logs

### 7. Latest episode notes

Post-take episode notes are optional.

They must:

- attach to the latest recorded episode only
- be saved after a take, not before recording starts
- reset to blank when a new episode becomes current

They must not be treated as session-level configuration.


## Source And Match Semantics

If the UI shows where a device assignment came from, it must describe matching, not existence.

Acceptable values:

- `overlay`
- `preset`
- `overlay + preset`
- `unmatched`

Acceptable wording for the row itself:

- `discovered`

Unacceptable wording:

- `manual`

Reason:

`manual` suggests the operator authored a live device into existence, which is not part of the model.


## Role Editing Rules

The UI may let the operator correct role assignments, but only within the canonical vocabulary and only within the allowed kind-specific set.

Examples:

- a `realsense` may become `scene_1` or `lightning_wrist_1`
- a `gelsight` may become `lightning_finger_left`
- a `gelsight` may not become `scene_1`

The UI must not permit:

- arbitrary new role strings
- freeform role typing


## Preset Rules

Presets are convenience defaults.

They may define:

- metadata defaults
- expected devices
- preferred identifiers
- default enabled roles
- default dataset and robot ids

They must not:

- create live device rows when discovery found nothing
- override actual discovery results
- redefine the canonical role vocabulary


## Local Overlay Rules

Local overlays may provide:

- serial-to-role defaults
- display labels
- default enabled flags
- calibration references
- geometry references
- other local rig facts

They are allowed to inform suggestions.
They are not allowed to fabricate discovered devices.


## Session Launch Rules

When the operator clicks `Start Session`, the backend must launch only from the resolved session plan:

- selected discovered devices
- resolved canonical roles
- current metadata and paths

Missing expected devices must not be launched.


## Validate Rules

`Validate` must evaluate the resolved session plan, not the preset alone.

It should check:

- required core processes
- required topics for enabled discovered devices
- message flow on those topics
- profile compatibility state

`Validate` should fail clearly if:

- a required discovered device is not healthy
- a required profile role is missing
- a selected device does not produce its expected canonical topics


## Record Rules

`Record` must be available only when:

- the session is up
- required subsystems are healthy
- validation has passed for the current resolved session plan

The recorder must use:

- the resolved selected topics
- not a fabricated preset list
- not a hardcoded device list


## Non-Goals

- no arbitrary device authoring in the main UI
- no hidden fallback device identities
- no dual V1/V2 device table behavior
- no reintroduction of `wrist/scene/left/right` as live UI concepts


## Immediate Cleanup Required

The active UI should be aligned to this spec with these concrete changes:

1. discovered-device table shows discovered devices only
2. preset-only or overlay-only devices move to `Expected but Missing`
3. `Kind` and `Identifier` become read-only in the main table
4. role choices become kind-filtered
5. remove add/remove device-row behavior from the main workflow
6. remove `manual` as a device-source concept

Until those are done, the current UI should be treated as transitional rather than spec-complete.
