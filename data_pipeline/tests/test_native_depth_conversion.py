from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from data_pipeline.convert_episode_bag_to_lerobot import (
    build_depth_validation_report,
    convert_depth_to_meters,
    make_depth_encoder,
    resolve_bag_source,
    resolve_depth_encoder,
    resolve_depth_scales,
)


DEPTH_SPEC = {
    "field": "observation.depth.world.scene_1",
    "topic": "/spark/cameras/world/scene_1/depth/image_rect_raw",
    "sensor_key": "/spark/cameras/world/scene_1",
}


class NativeDepthConversionTest(unittest.TestCase):
    def test_manifest_depth_scale_is_required(self) -> None:
        manifest = {"sensors": {"devices": [{"sensor_key": DEPTH_SPEC["sensor_key"]}]}}
        with self.assertRaisesRegex(RuntimeError, "depth_scale_meters_per_unit"):
            resolve_depth_scales(manifest, [DEPTH_SPEC])

    def test_raw_depth_is_converted_to_float_meters(self) -> None:
        manifest = {
            "sensors": {
                "devices": [
                    {
                        "sensor_key": DEPTH_SPEC["sensor_key"],
                        "depth_scale_meters_per_unit": 0.00025,
                    }
                ]
            }
        }
        scales = resolve_depth_scales(manifest, [DEPTH_SPEC])
        converted = convert_depth_to_meters(
            {DEPTH_SPEC["field"]: [np.asarray([[0, 2000]], dtype=np.uint16)]},
            scales,
        )[DEPTH_SPEC["field"]][0]
        self.assertEqual(converted.dtype, np.float32)
        self.assertEqual(converted.shape, (1, 2, 1))
        np.testing.assert_allclose(converted[:, :, 0], [[0.0, 0.5]])

    def test_depth_report_exposes_invalid_and_clipped_pixels(self) -> None:
        field = DEPTH_SPEC["field"]
        encoder = make_depth_encoder(0.4, 1.0)
        report = build_depth_validation_report(
            [DEPTH_SPEC],
            {field: [np.asarray([[0, 500, 1000, 2000]], dtype=np.uint16)]},
            {field: 0.001},
            encoder,
        )["fields"][field]
        self.assertEqual(report["zero_fraction"], 0.25)
        self.assertEqual(
            report["encoder"]["invalid_zero_behavior"], "decoded_as_depth_min"
        )
        self.assertEqual(report["encoder"]["valid_fraction_above_max"], 1 / 3)

    def test_profile_can_supply_dataset_wide_depth_bounds(self) -> None:
        encoder = resolve_depth_encoder(
            {"depth_encoding": {"depth_min_m": 0.4, "depth_max_m": 2.5}},
            None,
            None,
        )
        self.assertIsNotNone(encoder)
        self.assertEqual(encoder.depth_min, 0.4)
        self.assertEqual(encoder.depth_max, 2.5)

    def test_depth_bounds_must_be_finite(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "finite"):
            make_depth_encoder(float("nan"), 2.5)

    def test_verified_archive_topics_replace_logical_image_topics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            episode_dir = Path(temp_dir)
            archive_dir = episode_dir / "archive"
            (archive_dir / "bag").mkdir(parents=True)
            archive_manifest = {
                "archive_storage": {"storage_id": "mcap"},
                "archive_output": {
                    "verified": True,
                    "verification": {
                        "lightweight": {"status": "ok"},
                        "full_payload": {"status": "ok"},
                    },
                },
                "image_transcode": {
                    "source_topics": [
                        {
                            "source_topic": DEPTH_SPEC["topic"],
                            "archive_topic": f"{DEPTH_SPEC['topic']}/compressedDepth",
                            "modality": "depth",
                        }
                    ]
                },
            }
            (archive_dir / "archive_manifest.json").write_text(
                json.dumps(archive_manifest),
                encoding="utf-8",
            )

            source = resolve_bag_source(episode_dir, {DEPTH_SPEC["topic"]}, "archive")
            self.assertEqual(source.kind, "archive")
            self.assertEqual(
                source.logical_to_physical_topic[DEPTH_SPEC["topic"]],
                f"{DEPTH_SPEC['topic']}/compressedDepth",
            )
            self.assertEqual(source.image_modalities[DEPTH_SPEC["topic"]], "depth")

    def test_archive_requires_recorded_full_payload_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            episode_dir = Path(temp_dir)
            archive_dir = episode_dir / "archive"
            (archive_dir / "bag").mkdir(parents=True)
            (archive_dir / "archive_manifest.json").write_text(
                json.dumps({"archive_output": {"verified": True}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "full payload"):
                resolve_bag_source(episode_dir, {DEPTH_SPEC["topic"]}, "archive")


if __name__ == "__main__":
    unittest.main()
