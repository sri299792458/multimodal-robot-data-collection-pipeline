# Episode Curation and Discard Spec

This document defines the intended workflow for removing bad takes without breaking the raw-source-of-truth model.

## Problem

The immediate cleanup case is:

1. A bad take is noticed immediately after recording.

Raw episodes are the source of truth. If a take is obviously bad before conversion,
the safest supported action is to delete that raw episode entirely.

## Decisions

### 1. Do not ship `delete_published_episodes.py` as the supported path

The experimental helper can rewrite the core LeRobot dataset and then patch Spark-specific metadata back in, but that is still the wrong design center.

Why:

- it depends on the current Spark artifact layout under:
  - `meta/spark_conversion/<episode_id>/`
  - `meta/spark_source/<episode_id>/`
  - `depth/.../file-{episode_index:03d}.parquet`
  - `depth_preview/.../file-{episode_index:03d}.mp4`
- it will drift if conversion artifacts change
- it treats published data surgery as the normal path instead of an exceptional cleanup path

Conclusion:

- keep it out of the supported workflow
- prefer raw discard as the first-class cleanup path

### 2. Add `Discard Last Take` for raw cleanup

This is the fast operator path.

Behavior:

- available only after recording has stopped
- only targets the latest raw episode known to the operator console
- deletes the entire raw episode directory:
  - `raw_episodes/<episode_id>/`
- not just the bag directory
- requires explicit confirmation with the episode id

Why delete the whole raw episode directory:

- `episode_manifest.json`
- `notes.md`
- any later sidecars or local analysis outputs

all belong to the same take. Deleting only `bag/` would leave broken partial state.

Constraints:

- disabled while recorder or converter is running
- disabled once that take has already been converted or archived
- on success, clears the operator console’s pointers to that take

### 3. Published-dataset surgery remains unresolved

Later review-time cleanup of already-published episodes is still an open design problem.

Why:

- upstream `lerobot-edit-dataset` only understands the core LeRobot dataset layout
- our published datasets also carry Spark-specific artifacts and sidecars
- no supported Spark-aware published-episode delete/exclude workflow exists yet

So this implementation intentionally stops at raw discard.

## Intended Workflow

### Immediate bad take

1. Record a take.
2. Notice it is bad before conversion.
3. Use `Discard Last Take`.
4. Record again.

## Non-goals

- deleting one published episode by manually removing files
- deleting raw source episodes after they have already become the provenance source for a published dataset

## Implementation order

1. operator console: `Discard Last Take`
2. later: revisit published-dataset cleanup as a separate design
