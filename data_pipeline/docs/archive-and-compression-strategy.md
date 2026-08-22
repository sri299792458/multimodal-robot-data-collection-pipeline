# Archive And Compression Strategy

## Core Decision

Live capture optimizes for recording reliability. Offline archive generation
creates the smaller, lossless ROS-native artifact used for long-term retention
and future conversion.

The stages are:

1. capture one plain MCAP bag
2. generate and fully verify a lossless archive
3. publish an aligned LeRobot dataset from the verified archive

Conversion falls back to the capture only when a verified archive does not yet
exist.


## Capture Policy

The live recorder performs no trim or image transcode. This avoids placing CPU,
storage-rewrite, or compression failure modes on the demonstration critical
path.

The capture remains unchanged while archive generation runs. A failed archive
job can therefore be retried safely.


## Archive Policy

`archive_episode.py` performs:

- head/tail trim
- RGB and tactile conversion to PNG-backed `/compressed` topics
- depth conversion to PNG-backed `/compressedDepth` topics
- MCAP zstd chunk compression
- structural and full decoded-payload verification
- provenance recording in `archive/archive_manifest.json`

The current presets are `zstd_fast` and `zstd_small`; `zstd_small` is the
default.

The archive is accepted as a conversion source only when its manifest records
`archive_output.verified: true`. Full payload verification checks decoded image
content, not just topic counts or bag readability.


## Published Policy

The published dataset is optimized for aligned training and review:

- fixed-rate low-dimensional frames
- RGB/tactile video
- native LeRobot depth-map video
- copied source provenance and conversion diagnostics

Published RGB/tactile streams use the configured LeRobot video encoder. Native
depth uses LeRobot's bounded 12-bit quantization, so publication requires
explicit minimum and maximum depths in meters. Diagnostics report invalid
zeros, values outside the range, and reconstruction error.

The published dataset is not the retention artifact. The verified archive keeps
lossless visual sensor values needed to republish with different encoder
settings later.


## Deletion Gate

The capture bag may be removed only after:

- archive generation completed
- the archive manifest records successful full payload verification
- the archive files and episode manifest/notes are retained

Publishing a LeRobot dataset alone is not sufficient reason to delete the
capture, because the published depth representation is intentionally bounded
and quantized.
