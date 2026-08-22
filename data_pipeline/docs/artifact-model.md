# Capture, Archive, And Published Artifacts

## Purpose

One demonstration moves through three artifacts with different jobs. Recording,
lossless retention, and training publication stay separate so a failure in one
stage does not silently damage another.


## Episode Folder

Recording creates:

```text
raw_episodes/<episode_id>/
├── bag/
├── episode_manifest.json
└── notes.md
```

The manifest and notes identify the take across every later artifact. The bag
is the immediate capture, not automatically the final retained representation.


## 1. Capture Bag

The capture bag is the direct ROS-native output of recording.

Current policy:

- one plain MCAP bag per demonstration
- no live trim or rewrite
- minimal work on the recording critical path
- preserve asynchronous topics exactly as received

Keep it until archive generation and full payload verification succeed. If
archive generation fails, the capture remains available for retry and
debugging.


## 2. Verified Archive

The archive is the long-term ROS-native artifact.

It contains:

- the selected active interval
- RGB and tactile images as lossless PNG-compressed ROS messages
- depth as lossless `compressedDepth` PNG
- MCAP zstd chunk compression
- `archive/archive_manifest.json` with source, trim, topic mapping, and
  verification results

An archive becomes authoritative only after full payload verification confirms
that every archived image decodes exactly to its source pixels. At that point,
the capture bag may be deleted according to lab retention policy.

The converter's default `--bag-source auto` behavior is:

1. use the verified archive when it exists
2. otherwise use the capture bag
3. reject an archive whose manifest does not record successful verification


## 3. Published Dataset

The published dataset under `published/<dataset_id>/` is the aligned,
training-facing LeRobot artifact.

It contains:

- fixed-rate state and action frames
- RGB/tactile video features
- native LeRobot depth-map video features in meters
- LeRobot metadata
- per-episode conversion diagnostics and source provenance

Published depth is bounded 12-bit quantized video. It is appropriate for the
approved learning range, but it is not a lossless replacement for the archive.


## Provenance

Each converted episode records:

```text
meta/spark_source/<episode_id>/episode_manifest.json
meta/spark_source/<episode_id>/notes.md
meta/spark_source/<episode_id>/archive_manifest.json  # archive source only
meta/spark_conversion/<episode_id>/diagnostics.json
meta/spark_conversion/<episode_id>/conversion_summary.json
meta/spark_conversion/<episode_id>/effective_profile.yaml
```

The source manifest preserves sensor identities, intrinsics, and recorded depth
scales. The archive manifest proves which ROS artifact was used. Conversion
diagnostics record alignment and native-depth clipping/reconstruction behavior.


## Retention Rule

- Do not delete a capture before full archive payload verification succeeds.
- Retain the verified archive when future debugging, recalibration, or
  republishing must remain possible.
- Treat the published dataset as a reproducible training derivative, not as
  the only copy of sensor truth.
- Keep one depth encoder range for every episode appended to the same published
  dataset.
