# Published Dataset Contract

## Purpose

This document defines how recorded ROS episodes are converted into the
published LeRobot dataset.

The verified archive is the preferred ROS source. The capture bag is used until
that archive exists. The published dataset is a fixed-rate aligned view of the
selected ROS source.


## Core Design Rule

The published dataset is derived from one recorded episode.

That means:

- the ROS source preserves asynchronous topic truth
- conversion builds one explicit aligned learning view
- published artifacts must not silently redefine what the recorded episode
  meant

`--bag-source auto` prefers a fully verified archive and falls back to the
capture bag. An unverified archive is rejected rather than trusted silently.


## Conversion Profile

The current pipeline uses one checked-in conversion profile at `20 Hz`:

- `multisensor_20hz`

That file defines:

- published frame rate
- timestamp and alignment policy
- missing-data policy
- diagnostics policy

It does not hardcode one fixed embodiment or one fixed sensor set.

Current implementation note:

- raw recording uses `multisensor_20hz.yaml` plus the session's active-arm set and enabled sensor keys
- conversion uses `multisensor_20hz.yaml` plus the manifest's active-arm set and
  the `sensor_key` values from `sensors.devices`
- `--profile` is now overriding the generic conversion policy, not choosing between arm-specific profile files

### Why

If GelSight is a first-class published modality, then 20 Hz is the most honest default common rate. A faster published rate would either duplicate tactile frames too aggressively or claim more temporal precision than the raw streams actually support.

The checked-in policy is intentionally generic:

- one conversion policy
- schema derived from manifest active arms and the `sensor_key` values under
  `sensors.devices`

That is simpler and more honest than maintaining near-copy profile files only to
encode embodiment differences.

Depth publication also requires one explicit dataset-wide encoder range:

```yaml
depth_encoding:
  depth_min_m: <approved_minimum>
  depth_max_m: <approved_maximum>
```

No range is inferred from one episode. Use `--analyze-depth-only` over
representative recordings before approving these values.


## Raw vs Published

The raw bag may contain one active arm or two active arms.

That does not mean all raw episodes should be coerced into one published schema.

Rules:

- raw recording should preserve whichever `/spark/...` robot topics are actually present
- published conversion should derive its effective schema from the recorded embodiment and sensors
- do not zero-fill an inactive arm into a bimanual schema by default
- do not append episodes from different active-arm or sensor layouts into the same `dataset_id`

### Why

The storage cost of zero-filling an inactive arm is small, but the semantic cost is not. It mixes single-arm and bimanual behavior into one schema and makes downstream training depend on implicit padding conventions instead of explicit embodiment choice.


## Published Folder Contract

One published folder must represent one coherent dataset contract.

In practice that means:

- one effective low-dimensional schema
- one image/depth field set
- one embodiment and sensor-layout interpretation
- one native-depth encoder range

Do not append episodes with incompatible published schemas into the same folder.

If the effective schema changes, the correct action is:

- use a new published folder

not:

- silently append and hope downstream code tolerates shape drift


## Effective Schema Resolution

For each recorded episode:

1. Read the generic conversion profile.
2. Read the manifest active-arm set.
3. Read the recorded sensor keys from `sensors.devices`.
4. Derive the effective published schema from those two pieces of episode truth.
5. Fail conversion if the arm presence is ambiguous or inconsistent.

Examples of inconsistent episodes that should fail:

- `lightning` state exists but `lightning` action does not
- `thunder` action exists but `thunder` state does not
- an arm comes and goes in a way that makes the effective published schema ambiguous for the episode


## Canonical Published Time Grid

For each recorded episode:

1. Load all required published streams.
2. Compute:
   - `t_start = max(first timestamp of each required published stream)`
   - `t_end = min(last timestamp of each required published stream)`
3. Define:
   - `t_k = t_start + k / 20.0`
4. Keep frame indices while `t_k <= t_end`.

### Why

This creates one explicit frame timeline for the published episode. The timeline is no longer implicit in whichever modality happened to be processed first. This is the cleanest form for LeRobot and for downstream learning code.


## Teleop Activity And Valid Published Frames

`/spark/session/teleop_active` is now part of the raw conversion contract for
supported episodes.

Why:

- pedal-off spans are intentional inactivity, not missing-action failures
- published conversion should remove those spans from the usable interval
- missing activity should fail conversion instead of inventing fallback behavior

So the published dataset is not just “raw topics sampled at 20 Hz.” It is the
usable active teleoperation interval sampled at 20 Hz under explicit validity
rules.


## Published Observation Schema

The published dataset includes:

- `observation.state`
- `action`
- one image field for each recorded sensor with a color stream
- one depth field for each recorded sensor with a depth stream that the effective schema includes

### Why

This keeps the published schema honest to what was actually recorded while still using one stable conversion policy.

Field names are derived mechanically from sensor keys. Examples:

- `/spark/cameras/lightning/wrist_1`
  - `observation.images.lightning.wrist_1`
  - `observation.depth.lightning.wrist_1`
- `/spark/cameras/world/scene_1`
  - `observation.images.world.scene_1`
  - `observation.depth.world.scene_1`
- `/spark/tactile/lightning/finger_left`
  - `observation.images.tactile.lightning.finger_left`

For arm-dependent low-dimensional features, the effective schema uses a fixed arm order:

1. `lightning`
2. `thunder`

That ordering must not change across episodes.

If only one arm is active, only that arm's low-dimensional slice appears in the effective schema.


## Native Depth Contract

Each published depth field is a LeRobot video feature with:

- `info.is_depth_map: true`
- float input values converted to meters using the episode manifest's recorded
  `depth_scale_meters_per_unit`
- bounded 12-bit depth encoding
- encoder metadata stored in `meta/info.json`

Invalid sensor value `0` is counted in diagnostics and decodes to the configured
minimum. Values outside the approved range are clipped and their fractions are
reported. The verified archive retains the original lossless values.


## Published Provenance

The published dataset keeps a copy of the episode source snapshot under:

- `meta/spark_source/<episode_id>/episode_manifest.json`
- `meta/spark_source/<episode_id>/notes.md`
- `meta/spark_source/<episode_id>/archive_manifest.json` when the archive was
  used

And the converter writes episode-level conversion artifacts under:

- `meta/spark_conversion/<episode_id>/diagnostics.json`
- `meta/spark_conversion/<episode_id>/conversion_summary.json`
- `meta/spark_conversion/<episode_id>/effective_profile.yaml`

This is deliberate.

Dataset-level metadata alone is not enough to reconstruct the exact episode
truth later. The copied snapshot ties the learning artifact to the recorded
episode and selected ROS source.


## Observation State Definition

The current bimanual `multisensor_20hz` profile uses this flat `observation.state` order:

### `lightning`

1. `lightning_joint_pos_1`
2. `lightning_joint_pos_2`
3. `lightning_joint_pos_3`
4. `lightning_joint_pos_4`
5. `lightning_joint_pos_5`
6. `lightning_joint_pos_6`
7. `lightning_eef_x`
8. `lightning_eef_y`
9. `lightning_eef_z`
10. `lightning_eef_rx`
11. `lightning_eef_ry`
12. `lightning_eef_rz`
13. `lightning_gripper_position`
14. `lightning_ft_fx`
15. `lightning_ft_fy`
16. `lightning_ft_fz`
17. `lightning_ft_tx`
18. `lightning_ft_ty`
19. `lightning_ft_tz`

### `thunder`

20. `thunder_joint_pos_1`
21. `thunder_joint_pos_2`
22. `thunder_joint_pos_3`
23. `thunder_joint_pos_4`
24. `thunder_joint_pos_5`
25. `thunder_joint_pos_6`
26. `thunder_eef_x`
27. `thunder_eef_y`
28. `thunder_eef_z`
29. `thunder_eef_rx`
30. `thunder_eef_ry`
31. `thunder_eef_rz`
32. `thunder_gripper_position`
33. `thunder_ft_fx`
34. `thunder_ft_fy`
35. `thunder_ft_fz`
36. `thunder_ft_tx`
37. `thunder_ft_ty`
38. `thunder_ft_tz`

For each arm, `*_eef_rx`, `*_eef_ry`, and `*_eef_rz` are the three components
of an axis-angle rotation vector in radians. They are not roll, pitch, and yaw.
The converter derives this rotation vector from the valid quaternion carried by
the source `PoseStamped` message.

### Why

This keeps all robot-side low-dimensional state in one compact feature, which is the easiest shape for LeRobot-style datasets and policy code. It also avoids prematurely creating many custom low-dimensional namespaces.

For both arms:

- `*_gripper_position` is normalized measured opening on `0..1`
- `0.0 = fully open`
- `1.0 = fully closed`


## Action Definition

The current bimanual `multisensor_20hz` profile uses this flat `action` order:

### `lightning`

1. `lightning_cmd_joint_1`
2. `lightning_cmd_joint_2`
3. `lightning_cmd_joint_3`
4. `lightning_cmd_joint_4`
5. `lightning_cmd_joint_5`
6. `lightning_cmd_joint_6`
7. `lightning_cmd_gripper`

### `thunder`

8. `thunder_cmd_joint_1`
9. `thunder_cmd_joint_2`
10. `thunder_cmd_joint_3`
11. `thunder_cmd_joint_4`
12. `thunder_cmd_joint_5`
13. `thunder_cmd_joint_6`
14. `thunder_cmd_gripper`

### Why

The published `action` is the command sent by the teleoperation/runtime stack.
This is more stable and more semantically honest than silently replacing action
with a derived delta later in the pipeline.

For both arms:

- `*_cmd_gripper` is commanded gripper opening on `0..1`
- `0.0 = fully open`
- `1.0 = fully closed`

For single-arm episodes, use only the corresponding arm slice.


## Per-Topic Alignment Rules

### Robot state

Sources:

- `/spark/{arm}/robot/joint_state` for each active arm
- `/spark/{arm}/robot/eef_pose` for each active arm
- `/spark/{arm}/robot/tcp_wrench` for each active arm
- `/spark/{arm}/robot/gripper_state` for each active arm

Alignment rule:

- choose the latest sample with timestamp `<= t_k`

Validity threshold:

- max age 50 ms

### Why

Robot state is causal and should not look into the future relative to the published frame time. Latest-before is the correct rule for a state signal that is being sampled onto a coarser grid.


### Action

Sources:

- `/spark/{arm}/teleop/cmd_joint_state` for each active arm
- `/spark/{arm}/teleop/cmd_gripper_state` for each active arm

Alignment rule:

- choose the latest sample with timestamp `<= t_k`

Validity threshold:

- max age 150 ms

### Why

Action is also causal. A nearest-future command would make the published sample look as though the system already knew a command that had not yet been issued.

The action threshold is intentionally looser than the state threshold because the current Spark command path can exhibit isolated command gaps even when the demonstration is still semantically valid. The published action still uses bounded latest-before hold, but the bound is wide enough to tolerate short runtime hiccups without silently allowing large stale spans.

### Teleop activity mask

Raw source:

- `/spark/session/teleop_active`

Alignment rule:

- treat the Boolean value as a zero-order-held teleop-activity signal until the next sample
- keep published frames only while the held value is `true`

### Why

The action topics encode what command was issued, not whether the operator intended teleoperation to be active continuously. When the foot pedal is intentionally released in the middle of a raw episode, those pedal-off spans should be removed from the published demonstration rather than counted as stale-action failures.

The teleop-activity topic is part of the required raw contract for supported episodes. Conversion does not fall back to a command-only interpretation when that signal is missing.


### Camera RGB

Sources:

- every recorded camera color topic
- `/spark/cameras/{attachment}/{camera_slot}/color/image_raw`

Alignment rule:

- choose the nearest sample to `t_k`

Validity threshold:

- max skew 25 ms

### Why

Image streams are observations, not control signals. Nearest is the correct rule for selecting the frame that best represents the scene around the target time.


### Tactile RGB

Sources:

- every recorded tactile color topic
- `/spark/tactile/{arm}/{finger_slot}/color/image_raw`

Alignment rule:

- choose the nearest sample to `t_k`

Validity threshold:

- max skew 25 ms

### Why

GelSight RGB is treated like a tactile image stream. Nearest-to-grid keeps the published episode visually coherent without pretending tactile updates happen exactly on the grid.


## Missing Data Policy

If a required modality is outside its validity threshold:

- if the failure occurs only at the episode tail, truncate the episode at the last valid frame
- otherwise fail conversion for that episode

Exception:

- frames masked out by the teleop-activity signal are not treated as failures
- they are removed from the published timeline before action-age validity is applied

### Why

Silent filling of large gaps hides real collection problems and makes the dataset look healthier than it is. Tail truncation is acceptable because it only shortens the usable interval. Mid-episode failures should be made explicit.


## Raw-Only Modalities

The following topics remain raw-only unless the effective schema explicitly
publishes them:

- `/spark/session/teleop_active`
- optional point cloud or debugging topics

Depth is not automatically discarded. If a recorded sensor has a publishable
depth stream under the effective schema, it becomes a published depth field
derived mechanically from that sensor key.

### Why

These topics are valuable to preserve, but they complicate the first published dataset contract without being necessary for the first training and visualization workflows.


## Multi-Sensor Rules

The current pipeline supports:

- multiple RealSense sensors
- multiple GelSight sensors

The effective published schema includes every recorded sensor that resolves to a publishable stream under the shared topic contract.

The raw manifest should still preserve every recorded sensor as a sensor instance with:

- `sensor_key`
- `serial_number`
- sensor-specific metadata captured at record time when available
  - for RealSense: stream profiles, intrinsics, firmware version, and `depth_scale_meters_per_unit`

### Why

Support for multiple sensors comes from the raw-first design, not from trying to cram every modality into one live fused message. The generic conversion policy keeps alignment rules stable while still allowing each session to publish the sensors it actually recorded.


## Conversion Outputs

For each recorded episode, conversion produces:

- one published episode in the shared LeRobot dataset
- episode-level diagnostics
- a source snapshot under:
  - `meta/spark_source/<episode_id>/episode_manifest.json`
  - `meta/spark_source/<episode_id>/notes.md`
- the archive manifest in that source directory when the archive was used

The copied source manifest is the canonical per-episode sensor provenance
record inside the published dataset. Native depth layout and encoder metadata
are part of LeRobot's `meta/info.json`.

Diagnostics should include:

- usable interval
- number of published frames
- per-modality alignment error summary
- count of invalid or dropped frames
- native-depth zero, clipping, and reconstruction-error summaries

### Why

A successful conversion should mean more than "the script finished." We need enough diagnostics to judge whether the episode quality is acceptable.
