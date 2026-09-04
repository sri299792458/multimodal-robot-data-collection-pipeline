#!/usr/bin/env python3

"""Convert one recorded episode into a native LeRobot dataset."""

from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
import sys
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rosbag2_py
import yaml
from geometry_msgs.msg import PoseStamped, WrenchStamped
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message
from scipy.spatial.transform import Rotation
from sensor_msgs.msg import CompressedImage, Image, JointState
from std_msgs.msg import Bool

try:
    from realsense2_camera_msgs.msg import Metadata
except ImportError:
    Metadata = None

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.archive_verification import decode_archive_image_to_array  # noqa: E402
from data_pipeline.pipeline_utils import (  # noqa: E402
    detect_bag_storage_id,
    effective_profile_for_session,
    load_profile,
    manifest_active_arms,
    manifest_clock_policy,
    manifest_episode_id,
    manifest_language_instruction,
    manifest_profile_name,
    manifest_sensors,
    manifest_task_name,
    manifest_topic_types,
    normalize_active_arms,
    profile_required_arms,
    write_json,
)
from lerobot.configs import DepthEncoderConfig, RGBEncoderConfig  # noqa: E402
from lerobot.datasets.depth_utils import dequantize_depth, quantize_depth  # noqa: E402
from lerobot.datasets.lerobot_dataset import LeRobotDataset  # noqa: E402

DEPTH_U16_VALUE_COUNT = 1 << 16
DEPTH_REPORT_PERCENTILES = (
    0.0,
    0.1,
    1.0,
    5.0,
    25.0,
    50.0,
    75.0,
    95.0,
    99.0,
    99.9,
    100.0,
)


@dataclass
class TopicSeries:
    topic: str
    type_name: str
    timestamps_ns: list[int]
    values: list[Any]
    bag_timestamps_ns: list[int]

    def __post_init__(self) -> None:
        self._ts_array: np.ndarray | None = None

    @property
    def ts_array(self) -> np.ndarray:
        if self._ts_array is None:
            self._ts_array = np.asarray(self.timestamps_ns, dtype=np.int64)
        return self._ts_array

    def first_ts(self) -> int:
        if not self.timestamps_ns:
            raise ValueError(f"No samples recorded for topic {self.topic}")
        return self.timestamps_ns[0]

    def last_ts(self) -> int:
        if not self.timestamps_ns:
            raise ValueError(f"No samples recorded for topic {self.topic}")
        return self.timestamps_ns[-1]

    def latest_before(self, target_ns: int) -> tuple[Any, int] | None:
        idx = bisect_right(self.timestamps_ns, target_ns) - 1
        if idx < 0:
            return None
        ts_ns = self.timestamps_ns[idx]
        return self.values[idx], target_ns - ts_ns

    def latest_before_index(self, target_ns: int) -> tuple[int, int] | None:
        idx = bisect_right(self.timestamps_ns, target_ns) - 1
        if idx < 0:
            return None
        ts_ns = self.timestamps_ns[idx]
        return idx, target_ns - ts_ns

    def nearest(self, target_ns: int) -> tuple[Any, int] | None:
        idx = bisect_left(self.timestamps_ns, target_ns)
        candidates: list[tuple[Any, int]] = []
        if idx < len(self.timestamps_ns):
            candidates.append(
                (self.values[idx], abs(self.timestamps_ns[idx] - target_ns))
            )
        if idx > 0:
            candidates.append(
                (self.values[idx - 1], abs(self.timestamps_ns[idx - 1] - target_ns))
            )
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[1])

    def nearest_index(self, target_ns: int) -> tuple[int, int] | None:
        idx = bisect_left(self.timestamps_ns, target_ns)
        candidates: list[tuple[int, int]] = []
        if idx < len(self.timestamps_ns):
            candidates.append((idx, abs(self.timestamps_ns[idx] - target_ns)))
        if idx > 0:
            candidates.append((idx - 1, abs(self.timestamps_ns[idx - 1] - target_ns)))
        if not candidates:
            return None
        return min(candidates, key=lambda item: item[1])

    def diagnostics(self) -> dict[str, Any]:
        if len(self.timestamps_ns) < 2:
            return {
                "count": len(self.timestamps_ns),
                "observed_rate_hz": 0.0,
                "inter_arrival_ms": None,
            }

        ts = self.ts_array
        diffs_ms = np.diff(ts).astype(np.float64) / 1_000_000.0
        duration_s = (ts[-1] - ts[0]) / 1_000_000_000.0
        observed_rate_hz = (len(ts) - 1) / duration_s if duration_s > 0 else 0.0
        return {
            "count": int(len(ts)),
            "observed_rate_hz": observed_rate_hz,
            "inter_arrival_ms": {
                "min": float(diffs_ms.min()),
                "max": float(diffs_ms.max()),
                "mean": float(diffs_ms.mean()),
                "std": float(diffs_ms.std()),
            },
        }


@dataclass
class AlignmentFailure:
    frame_index: int
    timestamp_ns: int
    reason: str


@dataclass
class DepthSelection:
    field: str
    topic: str
    sample_index: int
    frame_index: int
    timestamp_ns: int
    skew_ms: float


@dataclass
class ActiveInterval:
    start_ns: int
    end_ns: int


@dataclass(frozen=True)
class BagSource:
    kind: str
    bag_dir: Path
    storage_id: str
    logical_to_physical_topic: dict[str, str]
    image_modalities: dict[str, str]
    archive_manifest_path: Path | None = None


def stamp_to_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def quaternion_to_rotation_vector(
    x: float, y: float, z: float, w: float
) -> tuple[float, float, float]:
    rotvec = Rotation.from_quat([x, y, z, w]).as_rotvec()
    return tuple(float(value) for value in rotvec)


def decode_image_to_rgb(msg: Image) -> np.ndarray:
    encoding = msg.encoding.lower()
    if encoding in {"rgb8", "8uc3"}:
        array = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width, 3
        )
        return array.copy()
    if encoding == "bgr8":
        array = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width, 3
        )
        return array[:, :, ::-1].copy()
    if encoding == "rgba8":
        array = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width, 4
        )
        return array[:, :, :3].copy()
    if encoding == "bgra8":
        array = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width, 4
        )
        return array[:, :, 2::-1].copy()
    if encoding in {"mono8", "8uc1"}:
        array = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width)
        return np.repeat(array[:, :, None], 3, axis=2)
    raise ValueError(
        f"Unsupported image encoding for published RGB conversion: {msg.encoding}"
    )


def decode_image_to_depth(msg: Image) -> np.ndarray:
    encoding = msg.encoding.lower()
    if encoding in {"16uc1", "mono16"}:
        dtype = np.dtype(">u2") if bool(msg.is_bigendian) else np.dtype("<u2")
        row_width = int(msg.width)
        return (
            np.frombuffer(msg.data, dtype=dtype)
            .reshape(int(msg.height), int(msg.step) // 2)[:, :row_width]
            .astype(np.uint16, copy=True)
        )
    raise ValueError(
        f"Unsupported image encoding for published depth conversion: {msg.encoding}"
    )


def decode_archive_rgb(msg: CompressedImage, modality: str) -> np.ndarray:
    image = decode_archive_image_to_array(msg, modality)
    if image.dtype != np.uint8:
        raise ValueError(
            f"Expected uint8 archive image, got {image.dtype} for {modality}"
        )
    if image.ndim == 2:
        image = np.repeat(image[:, :, None], 3, axis=2)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(
            f"Expected HxWx3 archive image, got {image.shape} for {modality}"
        )
    return np.ascontiguousarray(image)


def load_archive_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        archive_manifest = json.load(handle)
    output = archive_manifest.get("archive_output") or {}
    verification = output.get("verification") or {}
    lightweight = verification.get("lightweight") or {}
    full_payload = verification.get("full_payload") or {}
    if (
        output.get("verified") is not True
        or lightweight.get("status") != "ok"
        or full_payload.get("status") != "ok"
    ):
        raise RuntimeError(f"Archive has not passed full payload verification: {path}")
    return archive_manifest


def resolve_bag_source(
    episode_dir: Path, logical_topics: set[str], requested: str
) -> BagSource:
    raw_bag_dir = episode_dir / "bag"
    archive_manifest_path = episode_dir / "archive" / "archive_manifest.json"
    archive_bag_dir = episode_dir / "archive" / "bag"

    use_archive = requested == "archive"
    if (
        requested == "auto"
        and archive_manifest_path.is_file()
        and archive_bag_dir.is_dir()
    ):
        archive_manifest = load_archive_manifest(archive_manifest_path)
        use_archive = True
    elif use_archive:
        if not archive_manifest_path.is_file():
            raise FileNotFoundError(
                f"Missing archive manifest: {archive_manifest_path}"
            )
        if not archive_bag_dir.is_dir():
            raise FileNotFoundError(f"Missing archive bag: {archive_bag_dir}")
        archive_manifest = load_archive_manifest(archive_manifest_path)

    if use_archive:
        logical_to_physical = {topic: topic for topic in logical_topics}
        image_modalities: dict[str, str] = {}
        for entry in (archive_manifest.get("image_transcode") or {}).get(
            "source_topics", []
        ):
            source_topic = str(entry["source_topic"])
            if source_topic not in logical_topics:
                continue
            logical_to_physical[source_topic] = str(entry["archive_topic"])
            image_modalities[source_topic] = str(entry["modality"])
        storage_id = str(
            (archive_manifest.get("archive_storage") or {}).get("storage_id") or "mcap"
        )
        return BagSource(
            kind="archive",
            bag_dir=archive_bag_dir,
            storage_id=storage_id,
            logical_to_physical_topic=logical_to_physical,
            image_modalities=image_modalities,
            archive_manifest_path=archive_manifest_path,
        )

    if requested not in {"auto", "raw"}:
        raise ValueError(f"Unsupported bag source: {requested}")
    if not raw_bag_dir.is_dir():
        raise FileNotFoundError(f"Missing raw bag directory: {raw_bag_dir}")
    return BagSource(
        kind="raw",
        bag_dir=raw_bag_dir,
        storage_id=detect_bag_storage_id(raw_bag_dir),
        logical_to_physical_topic={topic: topic for topic in logical_topics},
        image_modalities={},
    )


def extract_message_timestamp_ns(msg: Any, bag_timestamp_ns: int) -> int:
    header = getattr(msg, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return bag_timestamp_ns
    return stamp_to_ns(stamp) or bag_timestamp_ns


def parse_message(
    topic: str,
    msg: Any,
    bag_timestamp_ns: int,
    parse_value: bool,
    image_modality: str | None = None,
) -> tuple[int, Any]:
    ts_ns = extract_message_timestamp_ns(msg, bag_timestamp_ns)
    if not parse_value:
        return ts_ns, None

    if Metadata is not None and isinstance(msg, Metadata):
        payload = json.loads(msg.json_data)
        return ts_ns, payload

    if isinstance(msg, Image):
        return ts_ns, decode_image_to_rgb(msg)

    if isinstance(msg, CompressedImage):
        if image_modality is None or image_modality == "depth":
            raise ValueError(
                f"Archive image modality is missing or invalid for parsed RGB topic {topic}"
            )
        return ts_ns, decode_archive_rgb(msg, image_modality)

    if isinstance(msg, JointState):
        positions = np.asarray(msg.position, dtype=np.float32)
        if topic.endswith("/joint_state") or topic.endswith("/cmd_joint_state"):
            if positions.shape[0] < 6:
                raise ValueError(
                    f"Expected at least 6 joint positions on {topic}, got {positions.shape[0]}"
                )
            return ts_ns, positions[:6]
        if positions.shape[0] < 1:
            raise ValueError(f"Expected at least 1 gripper position on {topic}")
        return ts_ns, np.asarray([positions[0]], dtype=np.float32)

    if isinstance(msg, PoseStamped):
        q = msg.pose.orientation
        rx, ry, rz = quaternion_to_rotation_vector(q.x, q.y, q.z, q.w)
        value = np.asarray(
            [
                msg.pose.position.x,
                msg.pose.position.y,
                msg.pose.position.z,
                rx,
                ry,
                rz,
            ],
            dtype=np.float32,
        )
        return ts_ns, value

    if isinstance(msg, WrenchStamped):
        value = np.asarray(
            [
                msg.wrench.force.x,
                msg.wrench.force.y,
                msg.wrench.force.z,
                msg.wrench.torque.x,
                msg.wrench.torque.y,
                msg.wrench.torque.z,
            ],
            dtype=np.float32,
        )
        return ts_ns, value

    if isinstance(msg, Bool):
        return ts_ns, bool(msg.data)

    raise TypeError(f"Unsupported message type for topic {topic}: {type(msg)}")


def read_manifest(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_value_topics(profile: dict[str, Any]) -> set[str]:
    topics: set[str] = set()
    published = profile["published"]
    for arm_sources in published["observation_state"]["sources"].values():
        topics.update(arm_sources.values())
    for arm_sources in published["action"]["sources"].values():
        topics.update(arm_sources.values())
    for image_spec in published["images"]:
        topics.add(image_spec["topic"])
    return topics


def teleop_activity_topic(profile: dict[str, Any]) -> str:
    return str(profile.get("teleop_activity", {}).get("topic", "")).strip()


def build_selected_image_specs(
    profile: dict[str, Any], topics_with_data: set[str]
) -> list[dict[str, Any]]:
    selected_specs: list[dict[str, Any]] = []
    for image_spec in profile["published"]["images"]:
        if image_spec["required"] or image_spec["topic"] in topics_with_data:
            selected_specs.append(copy.deepcopy(image_spec))
    return selected_specs


def build_selected_depth_specs(
    profile: dict[str, Any], topics_with_data: set[str]
) -> list[dict[str, Any]]:
    selected_specs: list[dict[str, Any]] = []
    for depth_spec in profile.get("published_depth", []):
        if depth_spec["required"] or depth_spec["topic"] in topics_with_data:
            selected_specs.append(copy.deepcopy(depth_spec))
    return selected_specs


def build_parse_topics(
    profile: dict[str, Any],
    topics_to_read: set[str],
    topic_types: dict[str, str],
    value_topics: set[str],
) -> set[str]:
    parse_topics = set(value_topics)
    activity_topic = teleop_activity_topic(profile)
    if activity_topic and activity_topic in topics_to_read:
        parse_topics.add(activity_topic)
    parse_topics.update(
        topic
        for topic in topics_to_read
        if topic_types.get(topic) == "realsense2_camera_msgs/msg/Metadata"
    )
    return parse_topics


def read_topic_series(
    bag_source: BagSource,
    topics_to_read: set[str],
    parse_topics: set[str],
) -> dict[str, TopicSeries]:
    reader = rosbag2_py.SequentialReader()
    storage_options = rosbag2_py.StorageOptions(
        uri=str(bag_source.bag_dir), storage_id=bag_source.storage_id
    )
    converter_options = rosbag2_py.ConverterOptions("", "")
    reader.open(storage_options, converter_options)

    physical_topic_types = {
        topic.name: topic.type for topic in reader.get_all_topics_and_types()
    }
    logical_to_physical = {
        logical_topic: bag_source.logical_to_physical_topic[logical_topic]
        for logical_topic in topics_to_read
        if bag_source.logical_to_physical_topic.get(logical_topic)
        in physical_topic_types
    }
    physical_to_logical = {
        physical: logical for logical, physical in logical_to_physical.items()
    }
    message_types = {
        physical: get_message(physical_topic_types[physical])
        for physical in physical_to_logical
    }
    series = {
        logical: TopicSeries(
            topic=logical,
            type_name=physical_topic_types[physical],
            timestamps_ns=[],
            values=[],
            bag_timestamps_ns=[],
        )
        for logical, physical in logical_to_physical.items()
    }

    while reader.has_next():
        physical_topic, data, bag_timestamp_ns = reader.read_next()
        logical_topic = physical_to_logical.get(physical_topic)
        if logical_topic is None:
            continue
        msg = deserialize_message(data, message_types[physical_topic])
        ts_ns, value = parse_message(
            logical_topic,
            msg,
            bag_timestamp_ns,
            parse_value=logical_topic in parse_topics,
            image_modality=bag_source.image_modalities.get(logical_topic),
        )
        series[logical_topic].timestamps_ns.append(ts_ns)
        series[logical_topic].values.append(value)
        series[logical_topic].bag_timestamps_ns.append(bag_timestamp_ns)

    return series


def apply_realsense_metadata_timestamps(series: dict[str, TopicSeries]) -> None:
    image_to_metadata: dict[str, str] = {}
    for topic in series:
        if topic.endswith("/color/image_raw"):
            image_to_metadata[topic] = topic.replace(
                "/color/image_raw", "/color/metadata"
            )
        elif topic.endswith("/depth/image_rect_raw"):
            image_to_metadata[topic] = topic.replace(
                "/depth/image_rect_raw", "/depth/metadata"
            )

    for image_topic, metadata_topic in image_to_metadata.items():
        image_series = series.get(image_topic)
        metadata_series = series.get(metadata_topic)
        if not image_series or not metadata_series:
            continue
        if not metadata_series.values:
            continue

        stamp_to_toa_ns: dict[int, list[int]] = {}
        for ts_ns, value in zip(
            metadata_series.timestamps_ns, metadata_series.values, strict=False
        ):
            if not isinstance(value, dict):
                continue
            time_of_arrival_ms = value.get("time_of_arrival")
            if time_of_arrival_ms is None:
                continue
            toa_ns = int(round(float(time_of_arrival_ms) * 1_000_000.0))
            stamp_to_toa_ns.setdefault(ts_ns, []).append(toa_ns)

        if not stamp_to_toa_ns:
            continue

        replaced = 0
        new_timestamps_ns: list[int] = []
        for ts_ns in image_series.timestamps_ns:
            candidates = stamp_to_toa_ns.get(ts_ns)
            if candidates:
                new_timestamps_ns.append(candidates.pop(0))
                replaced += 1
            else:
                new_timestamps_ns.append(ts_ns)

        if replaced:
            image_series.timestamps_ns = new_timestamps_ns
            image_series._ts_array = None


def ensure_series_present(series: dict[str, TopicSeries], topics: list[str]) -> None:
    missing = [
        topic
        for topic in topics
        if topic not in series or not series[topic].timestamps_ns
    ]
    if missing:
        raise RuntimeError(f"Bag is missing required topics or samples: {missing}")


def build_effective_profile(
    profile: dict[str, Any],
    selected_image_specs: list[dict[str, Any]],
    selected_depth_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    effective = copy.deepcopy(profile)
    effective["published"]["images"] = selected_image_specs
    effective["published_depth"] = selected_depth_specs
    return effective


def build_features(
    effective_profile: dict[str, Any],
    image_shapes: dict[str, tuple[int, int, int]],
    depth_shapes: dict[str, tuple[int, int, int]],
) -> dict[str, dict[str, Any]]:
    features = {
        "observation.state": {
            "dtype": "float32",
            "shape": (
                len(effective_profile["published"]["observation_state"]["names"]),
            ),
            "names": effective_profile["published"]["observation_state"]["names"],
        },
        "action": {
            "dtype": "float32",
            "shape": (len(effective_profile["published"]["action"]["names"]),),
            "names": effective_profile["published"]["action"]["names"],
        },
    }

    for image_spec in effective_profile["published"]["images"]:
        field = image_spec["field"]
        shape = image_shapes[field]
        features[field] = {
            "dtype": "video",
            "shape": shape,
            "names": ["height", "width", "channels"],
        }
    for depth_spec in effective_profile["published_depth"]:
        field = depth_spec["field"]
        features[field] = {
            "dtype": "video",
            "shape": depth_shapes[field],
            "names": ["height", "width", "channels"],
            "info": {"is_depth_map": True},
        }
    return features


def compare_feature_specs(existing: dict[str, dict], expected: dict[str, dict]) -> None:
    existing_core = {
        k: v
        for k, v in existing.items()
        if not k.startswith("meta/")
        and k
        not in {"index", "episode_index", "task_index", "timestamp", "frame_index"}
    }
    if set(existing_core) != set(expected):
        raise RuntimeError(
            "Existing dataset features do not match this episode conversion.\n"
            f"existing={sorted(existing_core)}\nexpected={sorted(expected)}"
        )
    for key in expected:
        if (
            existing_core[key]["dtype"] != expected[key]["dtype"]
            or tuple(existing_core[key]["shape"]) != tuple(expected[key]["shape"])
            or existing_core[key].get("names") != expected[key].get("names")
            or bool((existing_core[key].get("info") or {}).get("is_depth_map"))
            != bool((expected[key].get("info") or {}).get("is_depth_map"))
        ):
            raise RuntimeError(
                f"Existing dataset feature mismatch for {key}: "
                f"existing={existing_core[key]} expected={expected[key]}"
            )


def get_or_create_dataset(
    dataset_root: Path,
    dataset_id: str,
    fps: int,
    features: dict[str, dict[str, Any]],
    rgb_encoder: RGBEncoderConfig,
    depth_encoder: DepthEncoderConfig | None,
) -> LeRobotDataset:
    info_path = dataset_root / "meta" / "info.json"
    if info_path.is_file():
        dataset = LeRobotDataset.resume(
            repo_id=dataset_id,
            root=dataset_root,
            rgb_encoder=rgb_encoder,
            depth_encoder=depth_encoder,
        )
        if dataset.fps != fps:
            raise RuntimeError(
                f"Existing dataset fps {dataset.fps} does not match expected fps {fps}"
            )
        compare_feature_specs(dataset.meta.features, features)
        if depth_encoder is not None:
            for key in dataset.meta.depth_keys:
                info = dataset.meta.features[key].get("info") or {}
                existing_min = info.get("video.depth_min")
                existing_max = info.get("video.depth_max")
                if existing_min is None or existing_max is None:
                    raise RuntimeError(
                        f"Existing depth feature {key} has no native encoder bounds."
                    )
                if not math.isclose(
                    float(existing_min), depth_encoder.depth_min
                ) or not math.isclose(float(existing_max), depth_encoder.depth_max):
                    raise RuntimeError(
                        f"Existing depth encoder bounds for {key} are [{existing_min}, {existing_max}], "
                        f"not [{depth_encoder.depth_min}, {depth_encoder.depth_max}]."
                    )
        return dataset

    if dataset_root.exists():
        if not dataset_root.is_dir():
            raise RuntimeError(
                f"Published dataset target is not a directory: {dataset_root}"
            )
        if any(dataset_root.iterdir()):
            raise RuntimeError(
                "Published dataset folder exists but is not an initialized local dataset.\n"
                f"folder={dataset_root}\n"
                "Expected meta/info.json for an existing dataset, or an empty folder for a new dataset."
            )
        dataset_root.rmdir()

    dataset_root.parent.mkdir(parents=True, exist_ok=True)
    return LeRobotDataset.create(
        repo_id=dataset_id,
        root=dataset_root,
        fps=fps,
        features=features,
        rgb_encoder=rgb_encoder,
        depth_encoder=depth_encoder,
    )


def ns_grid(t_start_ns: int, t_end_ns: int, fps: int) -> list[int]:
    step_ns = int(round(1_000_000_000 / fps))
    if t_end_ns < t_start_ns:
        return []
    frame_count = ((t_end_ns - t_start_ns) // step_ns) + 1
    return [t_start_ns + idx * step_ns for idx in range(frame_count)]


def summarize_errors(values_ms: list[float]) -> dict[str, float] | None:
    if not values_ms:
        return None
    arr = np.asarray(values_ms, dtype=np.float64)
    return {
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
    }


def build_active_intervals(
    activity_series: TopicSeries,
    *,
    active_value: bool,
    clamp_start_ns: int,
    clamp_end_ns: int,
) -> list[ActiveInterval]:
    if clamp_end_ns < clamp_start_ns or not activity_series.timestamps_ns:
        return []

    intervals: list[ActiveInterval] = []
    timestamps = activity_series.timestamps_ns
    values = activity_series.values
    current_active = bool(values[0]) == active_value
    current_start_ns = timestamps[0] if current_active else None

    for ts_ns, value in zip(timestamps[1:], values[1:], strict=False):
        next_active = bool(value) == active_value
        if current_active and not next_active:
            if current_start_ns is not None:
                interval_start_ns = max(current_start_ns, clamp_start_ns)
                interval_end_ns = min(ts_ns - 1, clamp_end_ns)
                if interval_end_ns >= interval_start_ns:
                    intervals.append(
                        ActiveInterval(
                            start_ns=interval_start_ns, end_ns=interval_end_ns
                        )
                    )
            current_start_ns = None
        elif not current_active and next_active:
            current_start_ns = ts_ns
        current_active = next_active

    if current_active and current_start_ns is not None:
        interval_start_ns = max(current_start_ns, clamp_start_ns)
        interval_end_ns = clamp_end_ns
        if interval_end_ns >= interval_start_ns:
            intervals.append(
                ActiveInterval(start_ns=interval_start_ns, end_ns=interval_end_ns)
            )

    return intervals


def filter_grid_to_intervals(
    grid: list[int], intervals: list[ActiveInterval]
) -> list[int]:
    if not intervals:
        return []
    filtered: list[int] = []
    interval_index = 0
    for t_ns in grid:
        while (
            interval_index < len(intervals) and t_ns > intervals[interval_index].end_ns
        ):
            interval_index += 1
        if interval_index >= len(intervals):
            break
        interval = intervals[interval_index]
        if interval.start_ns <= t_ns <= interval.end_ns:
            filtered.append(t_ns)
    return filtered


def activity_interval_diagnostics(intervals: list[ActiveInterval]) -> dict[str, Any]:
    active_duration_ns = sum(
        interval.end_ns - interval.start_ns for interval in intervals
    )
    return {
        "activity_interval_count": len(intervals),
        "activity_intervals_ns": [
            {"start_ns": interval.start_ns, "end_ns": interval.end_ns}
            for interval in intervals
        ],
        "active_duration_s": float(active_duration_ns / 1_000_000_000.0),
    }


def align_episode(
    series: dict[str, TopicSeries],
    profile: dict[str, Any],
    selected_image_specs: list[dict[str, Any]],
    selected_depth_specs: list[dict[str, Any]],
    task_name: str,
    language_instruction: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[DepthSelection]], dict[str, Any], str]:
    arm_order = profile["notes"]["arm_order"]
    fps = int(profile["dataset"]["fps"])
    published = profile["published"]
    state_age_ns = int(
        round(float(published["observation_state"].get("max_age_ms", 50)) * 1_000_000.0)
    )
    action_age_ns = int(
        round(float(published["action"].get("max_age_ms", 50)) * 1_000_000.0)
    )
    activity_topic = teleop_activity_topic(profile)
    activity_active_value = bool(
        profile.get("teleop_activity", {}).get("active_value", True)
    )

    state_sources = published["observation_state"]["sources"]
    action_sources = published["action"]["sources"]
    expected_state_dim = len(published["observation_state"]["names"])
    expected_action_dim = len(published["action"]["names"])

    required_topics: list[str] = []
    for arm in arm_order:
        required_topics.extend(state_sources[arm].values())
        required_topics.extend(action_sources[arm].values())
    required_topics.extend(spec["topic"] for spec in selected_image_specs)
    required_topics.extend(spec["topic"] for spec in selected_depth_specs)
    if activity_topic:
        required_topics.append(activity_topic)

    ensure_series_present(series, required_topics)

    t_start_ns = max(series[topic].first_ts() for topic in required_topics)
    t_end_ns = min(series[topic].last_ts() for topic in required_topics)
    full_grid = ns_grid(t_start_ns, t_end_ns, fps)
    activity_intervals: list[ActiveInterval] = []
    activity_mode = "disabled"
    if (
        activity_topic
        and activity_topic in series
        and series[activity_topic].timestamps_ns
    ):
        activity_intervals = build_active_intervals(
            series[activity_topic],
            active_value=activity_active_value,
            clamp_start_ns=t_start_ns,
            clamp_end_ns=t_end_ns,
        )
        activity_mode = "filtered_by_enable"
        grid = filter_grid_to_intervals(full_grid, activity_intervals)
    else:
        raise RuntimeError(
            "Teleop activity topic is required for conversion but was missing or empty."
        )
    if not grid:
        raise RuntimeError(
            f"No valid {fps}Hz frame grid can be formed for interval [{t_start_ns}, {t_end_ns}] "
            f"after teleop-activity filtering."
        )

    frames: list[dict[str, Any]] = []
    failures: list[AlignmentFailure] = []
    state_alignment: dict[str, list[float]] = {
        topic: [] for arm in arm_order for topic in state_sources[arm].values()
    }
    action_alignment: dict[str, list[float]] = {
        topic: [] for arm in arm_order for topic in action_sources[arm].values()
    }
    image_alignment: dict[str, list[float]] = {
        spec["field"]: [] for spec in selected_image_specs
    }
    depth_alignment: dict[str, list[float]] = {
        spec["field"]: [] for spec in selected_depth_specs
    }
    depth_selections: dict[str, list[DepthSelection]] = {
        spec["field"]: [] for spec in selected_depth_specs
    }

    state_topic_order = []
    for arm in arm_order:
        state_topic_order.extend(
            [
                state_sources[arm]["joint_state"],
                state_sources[arm]["eef_pose"],
                state_sources[arm]["gripper_state"],
                state_sources[arm]["tcp_wrench"],
            ]
        )

    action_topic_order = []
    for arm in arm_order:
        action_topic_order.extend(
            [
                action_sources[arm]["cmd_joint_state"],
                action_sources[arm]["cmd_gripper_state"],
            ]
        )

    for frame_index, t_ns in enumerate(grid):
        state_parts: list[np.ndarray] = []
        action_parts: list[np.ndarray] = []
        image_values: dict[str, np.ndarray] = {}
        depth_frame_selections: list[DepthSelection] = []
        failure_reason: str | None = None

        for topic in state_topic_order:
            result = series[topic].latest_before(t_ns)
            if result is None:
                failure_reason = f"missing latest-before state sample for {topic}"
                break
            value, age_ns = result
            if age_ns > state_age_ns:
                failure_reason = (
                    f"state sample too old for {topic}: {age_ns / 1e6:.2f} ms"
                )
                break
            state_alignment[topic].append(age_ns / 1e6)
            state_parts.append(value)

        if failure_reason is None:
            for topic in action_topic_order:
                result = series[topic].latest_before(t_ns)
                if result is None:
                    failure_reason = f"missing latest-before action sample for {topic}"
                    break
                value, age_ns = result
                if age_ns > action_age_ns:
                    failure_reason = (
                        f"action sample too old for {topic}: {age_ns / 1e6:.2f} ms"
                    )
                    break
                action_alignment[topic].append(age_ns / 1e6)
                action_parts.append(value)

        if failure_reason is None:
            for image_spec in selected_image_specs:
                topic = image_spec["topic"]
                result = series[topic].nearest(t_ns)
                if result is None:
                    failure_reason = f"missing nearest image sample for {topic}"
                    break
                value, skew_ns = result
                image_skew_ns = int(
                    round(float(image_spec.get("max_skew_ms", 25)) * 1_000_000.0)
                )
                if skew_ns > image_skew_ns:
                    failure_reason = f"image sample too far from grid for {topic}: {skew_ns / 1e6:.2f} ms"
                    break
                image_alignment[image_spec["field"]].append(skew_ns / 1e6)
                image_values[image_spec["field"]] = value

        if failure_reason is None:
            for depth_spec in selected_depth_specs:
                topic = depth_spec["topic"]
                result = series[topic].nearest_index(t_ns)
                if result is None:
                    failure_reason = f"missing nearest depth sample for {topic}"
                    break
                sample_index, skew_ns = result
                depth_skew_ns = int(
                    round(float(depth_spec.get("max_skew_ms", 25)) * 1_000_000.0)
                )
                if skew_ns > depth_skew_ns:
                    failure_reason = f"depth sample too far from grid for {topic}: {skew_ns / 1e6:.2f} ms"
                    break
                depth_alignment[depth_spec["field"]].append(skew_ns / 1e6)
                depth_frame_selections.append(
                    DepthSelection(
                        field=depth_spec["field"],
                        topic=topic,
                        sample_index=sample_index,
                        frame_index=len(frames),
                        timestamp_ns=t_ns,
                        skew_ms=skew_ns / 1e6,
                    )
                )

        if failure_reason is not None:
            failures.append(
                AlignmentFailure(
                    frame_index=frame_index, timestamp_ns=t_ns, reason=failure_reason
                )
            )
            continue

        state_vector = np.concatenate(state_parts).astype(np.float32)
        action_vector = np.concatenate(action_parts).astype(np.float32)
        if state_vector.shape != (expected_state_dim,):
            raise RuntimeError(
                f"State vector shape mismatch: got {state_vector.shape}, expected {(expected_state_dim,)}"
            )
        if action_vector.shape != (expected_action_dim,):
            raise RuntimeError(
                f"Action vector shape mismatch: got {action_vector.shape}, expected {(expected_action_dim,)}"
            )
        frame = {
            "observation.state": state_vector,
            "action": action_vector,
            "task": language_instruction or task_name,
            **image_values,
        }
        frames.append(frame)
        for selection in depth_frame_selections:
            depth_selections[selection.field].append(selection)

    if not frames:
        raise RuntimeError("No valid published frames remained after alignment.")

    if failures:
        first_invalid = failures[0].frame_index
        contiguous_tail = [failure.frame_index for failure in failures] == list(
            range(first_invalid, len(grid))
        )
        if not contiguous_tail:
            raise RuntimeError(
                "Mid-episode alignment failure encountered.\n"
                f"first_failure={failures[0].__dict__}\n"
                f"num_failures={len(failures)}"
            )
        frames = frames[:first_invalid]
        if not frames:
            raise RuntimeError("All frames were truncated by tail-failure handling.")
        summary_status = "truncated_tail"
    else:
        summary_status = "pass"

    diagnostics = {
        "usable_interval_ns": {
            "t_start_ns": t_start_ns,
            "t_end_ns": t_end_ns,
        },
        "activity_filter": {
            "mode": activity_mode,
            "topic": activity_topic or None,
            "active_value": activity_active_value,
            "grid_frame_count_before_filter": len(full_grid),
            "grid_frame_count_after_filter": len(grid),
            "inactive_removed_frame_count": len(full_grid) - len(grid),
            "inactive_removed_duration_s": float((len(full_grid) - len(grid)) / fps)
            if fps > 0
            else 0.0,
            **activity_interval_diagnostics(activity_intervals),
        },
        "published_frame_count": len(frames),
        "invalid_frame_count": len(failures),
        "alignment_policy": {
            "state_max_age_ms": state_age_ns / 1_000_000.0,
            "action_max_age_ms": action_age_ns / 1_000_000.0,
            "image_max_skew_ms": {
                spec["field"]: float(spec.get("max_skew_ms", 25))
                for spec in selected_image_specs
            },
            "depth_max_skew_ms": {
                spec["field"]: float(spec.get("max_skew_ms", 25))
                for spec in selected_depth_specs
            },
        },
        "alignment_error_ms": {
            "state_topics": {
                topic: summarize_errors(values)
                for topic, values in state_alignment.items()
            },
            "action_topics": {
                topic: summarize_errors(values)
                for topic, values in action_alignment.items()
            },
            "image_fields": {
                field: summarize_errors(values)
                for field, values in image_alignment.items()
            },
            "depth_fields": {
                field: summarize_errors(values)
                for field, values in depth_alignment.items()
            },
        },
        "action_hold_diagnostics": {
            "topics": {
                topic: {
                    "max_action_age_ms": max(values) if values else 0.0,
                    "num_frames_over_50ms": int(
                        sum(1 for value in values if value > 50.0)
                    ),
                    "num_frames_over_100ms": int(
                        sum(1 for value in values if value > 100.0)
                    ),
                }
                for topic, values in action_alignment.items()
            }
        },
        "failures": [failure.__dict__ for failure in failures[:25]],
    }
    return frames, depth_selections, diagnostics, summary_status


def image_shapes_from_frames(
    frames: list[dict[str, Any]], image_fields: list[str]
) -> dict[str, tuple[int, int, int]]:
    shapes: dict[str, tuple[int, int, int]] = {}
    first_frame = frames[0]
    for field in image_fields:
        value = first_frame[field]
        if not isinstance(value, np.ndarray) or value.ndim != 3:
            raise RuntimeError(f"Image field {field} is not a 3D numpy array.")
        shapes[field] = tuple(int(dim) for dim in value.shape)
    return shapes


def decode_depth_message(msg: Image | CompressedImage) -> np.ndarray:
    if isinstance(msg, Image):
        depth = decode_image_to_depth(msg)
    elif isinstance(msg, CompressedImage):
        depth = decode_archive_image_to_array(msg, "depth")
    else:
        raise TypeError(f"Unsupported depth message type: {type(msg)}")
    if depth.dtype != np.uint16 or depth.ndim != 2:
        raise RuntimeError(
            f"Expected uint16 HxW depth, got {depth.dtype} {depth.shape}"
        )
    return np.ascontiguousarray(depth)


def extract_depth_arrays(
    bag_source: BagSource,
    depth_selections: dict[str, list[DepthSelection]],
) -> dict[str, list[np.ndarray]]:
    topic_to_requested_indices: dict[str, set[int]] = {}
    for selections in depth_selections.values():
        for selection in selections:
            topic_to_requested_indices.setdefault(selection.topic, set()).add(
                selection.sample_index
            )

    if not topic_to_requested_indices:
        return {field: [] for field in depth_selections}

    logical_to_physical = {
        logical: bag_source.logical_to_physical_topic[logical]
        for logical in topic_to_requested_indices
    }
    physical_to_logical = {
        physical: logical for logical, physical in logical_to_physical.items()
    }
    reader = rosbag2_py.SequentialReader()
    storage_options = rosbag2_py.StorageOptions(
        uri=str(bag_source.bag_dir), storage_id=bag_source.storage_id
    )
    converter_options = rosbag2_py.ConverterOptions("", "")
    reader.open(storage_options, converter_options)

    topic_types = {
        topic.name: topic.type for topic in reader.get_all_topics_and_types()
    }
    message_types = {
        physical: get_message(topic_types[physical])
        for physical in physical_to_logical
        if physical in topic_types
    }
    missing_physical_topics = sorted(set(physical_to_logical) - set(message_types))
    if missing_physical_topics:
        raise RuntimeError(
            f"Depth topics are missing from {bag_source.kind} bag: {missing_physical_topics}"
        )

    topic_indices = {topic: 0 for topic in topic_to_requested_indices}
    extracted: dict[tuple[str, int], np.ndarray] = {}

    while reader.has_next():
        physical_topic, data, _ = reader.read_next()
        logical_topic = physical_to_logical.get(physical_topic)
        if logical_topic is None:
            continue
        topic_index = topic_indices[logical_topic]
        topic_indices[logical_topic] += 1
        if topic_index not in topic_to_requested_indices[logical_topic]:
            continue
        msg = deserialize_message(data, message_types[physical_topic])
        extracted[(logical_topic, topic_index)] = decode_depth_message(msg)

    rows_by_field: dict[str, list[np.ndarray]] = {}
    for field, selections in depth_selections.items():
        rows: list[np.ndarray] = []
        for selection in selections:
            key = (selection.topic, selection.sample_index)
            if key not in extracted:
                raise RuntimeError(
                    f"Missing extracted depth sample for {selection.topic} index={selection.sample_index}"
                )
            rows.append(extracted[key])
        rows_by_field[field] = rows
    return rows_by_field


def resolve_depth_scales(
    manifest: dict[str, Any],
    depth_specs: list[dict[str, Any]],
) -> dict[str, float]:
    sensors = {
        str(sensor.get("sensor_key", "")).strip(): sensor
        for sensor in manifest_sensors(manifest)
        if isinstance(sensor, dict)
    }
    scales: dict[str, float] = {}
    for spec in depth_specs:
        field = str(spec["field"])
        sensor_key = str(spec.get("sensor_key", "")).strip()
        if not sensor_key:
            raise RuntimeError(
                f"Depth field {field} has no sensor_key in the effective profile."
            )
        sensor = sensors.get(sensor_key)
        if sensor is None:
            raise RuntimeError(
                f"Depth field {field} references unrecorded sensor {sensor_key}."
            )
        scale_value = sensor.get("depth_scale_meters_per_unit")
        if scale_value is None:
            raise RuntimeError(
                f"Manifest sensor {sensor_key} has no depth_scale_meters_per_unit. "
                "Native depth conversion requires the scale recorded at capture time."
            )
        scale = float(scale_value)
        if not math.isfinite(scale) or scale <= 0.0:
            raise RuntimeError(f"Invalid depth scale for {sensor_key}: {scale_value}")
        scales[field] = scale
    return scales


def convert_depth_to_meters(
    depth_arrays: dict[str, list[np.ndarray]],
    depth_scales: dict[str, float],
) -> dict[str, list[np.ndarray]]:
    return {
        field: [
            np.ascontiguousarray(
                array.astype(np.float32) * np.float32(depth_scales[field])
            )[:, :, None]
            for array in arrays
        ]
        for field, arrays in depth_arrays.items()
    }


def attach_depth_frames(
    frames: list[dict[str, Any]],
    metric_depth: dict[str, list[np.ndarray]],
) -> dict[str, tuple[int, int, int]]:
    shapes: dict[str, tuple[int, int, int]] = {}
    for field, arrays in metric_depth.items():
        if len(arrays) != len(frames):
            raise RuntimeError(
                f"Depth frame count mismatch for {field}: {len(arrays)} vs {len(frames)}"
            )
        if not arrays:
            raise RuntimeError(f"No depth frames were extracted for {field}")
        first_shape = tuple(int(dim) for dim in arrays[0].shape)
        if len(first_shape) != 3 or first_shape[2] != 1:
            raise RuntimeError(
                f"Native depth field {field} must have HxWx1 shape, got {first_shape}"
            )
        if any(tuple(array.shape) != first_shape for array in arrays):
            raise RuntimeError(f"Depth frame shapes are inconsistent for {field}")
        shapes[field] = first_shape
        for frame, array in zip(frames, arrays, strict=True):
            frame[field] = array
    return shapes


def percentile_label(percentile: float) -> str:
    return f"p{percentile:g}".replace(".", "_")


def weighted_percentiles(values: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    positive = weights > 0
    values = np.asarray(values[positive], dtype=np.float64)
    weights = np.asarray(weights[positive], dtype=np.int64)
    if not len(values):
        return {}
    order = np.argsort(values)
    values = values[order]
    cumulative = np.cumsum(weights[order], dtype=np.int64)
    total = int(cumulative[-1])
    result: dict[str, float] = {}
    for percentile in DEPTH_REPORT_PERCENTILES:
        rank = int(math.ceil((percentile / 100.0) * total))
        index = int(np.searchsorted(cumulative, max(rank, 1), side="left"))
        result[percentile_label(percentile)] = float(
            values[min(index, len(values) - 1)]
        )
    return result


def build_depth_validation_report(
    depth_specs: list[dict[str, Any]],
    depth_arrays: dict[str, list[np.ndarray]],
    depth_scales: dict[str, float],
    depth_encoder: DepthEncoderConfig | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    raw_values = np.arange(DEPTH_U16_VALUE_COUNT, dtype=np.float32)
    for spec in depth_specs:
        field = spec["field"]
        histogram = np.zeros(DEPTH_U16_VALUE_COUNT, dtype=np.int64)
        for array in depth_arrays[field]:
            histogram += np.bincount(array.reshape(-1), minlength=DEPTH_U16_VALUE_COUNT)
        total_pixels = int(histogram.sum())
        zero_pixels = int(histogram[0])
        valid_histogram = histogram[1:]
        valid_pixels = int(valid_histogram.sum())
        scale = depth_scales[field]
        metric_values = raw_values[1:] * np.float32(scale)
        report: dict[str, Any] = {
            "sensor_key": spec["sensor_key"],
            "source_topic": spec["topic"],
            "depth_scale_meters_per_unit": scale,
            "frame_count": len(depth_arrays[field]),
            "total_pixel_count": total_pixels,
            "zero_pixel_count": zero_pixels,
            "zero_fraction": float(zero_pixels / total_pixels) if total_pixels else 0.0,
            "valid_depth_percentiles_m": weighted_percentiles(
                metric_values, valid_histogram
            ),
        }
        if depth_encoder is not None and valid_pixels:
            below = int(valid_histogram[metric_values < depth_encoder.depth_min].sum())
            above = int(valid_histogram[metric_values > depth_encoder.depth_max].sum())
            quantized = quantize_depth(
                metric_values[None, :],
                depth_min=depth_encoder.depth_min,
                depth_max=depth_encoder.depth_max,
                shift=depth_encoder.shift,
                use_log=depth_encoder.use_log,
                video_backend=None,
                input_unit="m",
            )
            reconstructed = dequantize_depth(
                quantized,
                depth_min=depth_encoder.depth_min,
                depth_max=depth_encoder.depth_max,
                shift=depth_encoder.shift,
                use_log=depth_encoder.use_log,
                output_unit="m",
                output_tensor=False,
            )
            errors_mm = (
                np.abs(np.asarray(reconstructed).reshape(-1) - metric_values) * 1000.0
            )
            report["encoder"] = {
                "depth_min_m": depth_encoder.depth_min,
                "depth_max_m": depth_encoder.depth_max,
                "shift_m": depth_encoder.shift,
                "use_log": depth_encoder.use_log,
                "valid_fraction_below_min": float(below / valid_pixels),
                "valid_fraction_above_max": float(above / valid_pixels),
                "reconstruction_error_percentiles_mm": weighted_percentiles(
                    errors_mm, valid_histogram
                ),
                "invalid_zero_behavior": "decoded_as_depth_min",
            }
        fields[field] = report
    return {"native_depth_unit": "m", "fields": fields}


def make_depth_encoder(
    depth_min_m: float | None, depth_max_m: float | None
) -> DepthEncoderConfig | None:
    if (depth_min_m is None) != (depth_max_m is None):
        raise RuntimeError("Pass both --depth-min-m and --depth-max-m, or neither.")
    if depth_min_m is None or depth_max_m is None:
        return None
    if not math.isfinite(depth_min_m) or not math.isfinite(depth_max_m):
        raise RuntimeError("Depth encoder bounds must be finite meters.")
    if depth_min_m >= depth_max_m:
        raise RuntimeError("--depth-min-m must be less than --depth-max-m.")
    return DepthEncoderConfig(depth_min=depth_min_m, depth_max=depth_max_m)


def resolve_depth_encoder(
    profile: dict[str, Any],
    cli_depth_min_m: float | None,
    cli_depth_max_m: float | None,
) -> DepthEncoderConfig | None:
    if cli_depth_min_m is not None or cli_depth_max_m is not None:
        return make_depth_encoder(cli_depth_min_m, cli_depth_max_m)
    config = profile.get("depth_encoding") or {}
    return make_depth_encoder(config.get("depth_min_m"), config.get("depth_max_m"))


def write_conversion_artifacts(
    artifact_dir: Path,
    diagnostics: dict[str, Any],
    effective_profile: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    write_json(artifact_dir / "diagnostics.json", diagnostics)
    write_json(artifact_dir / "conversion_summary.json", summary)
    with (artifact_dir / "effective_profile.yaml").open(
        "w", encoding="utf-8"
    ) as handle:
        yaml.safe_dump(effective_profile, handle, sort_keys=False)


def copy_source_snapshot(
    dataset_root: Path,
    episode_dir: Path,
    episode_id: str,
    bag_source: BagSource,
) -> dict[str, str | None]:
    source_root = dataset_root / "meta" / "spark_source" / episode_id
    source_root.mkdir(parents=True, exist_ok=True)

    manifest_src = episode_dir / "episode_manifest.json"
    manifest_dst = source_root / "episode_manifest.json"
    shutil.copy2(manifest_src, manifest_dst)

    notes_src = episode_dir / "notes.md"
    notes_dst = source_root / "notes.md"
    notes_path: str | None = None
    if notes_src.is_file():
        shutil.copy2(notes_src, notes_dst)
        notes_path = str(notes_dst.relative_to(dataset_root))

    archive_manifest_path: str | None = None
    if bag_source.archive_manifest_path is not None:
        archive_manifest_dst = source_root / "archive_manifest.json"
        shutil.copy2(bag_source.archive_manifest_path, archive_manifest_dst)
        archive_manifest_path = str(archive_manifest_dst.relative_to(dataset_root))

    return {
        "root": str(source_root.relative_to(dataset_root)),
        "episode_manifest_path": str(manifest_dst.relative_to(dataset_root)),
        "notes_path": notes_path,
        "archive_manifest_path": archive_manifest_path,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_dir", type=Path)
    parser.add_argument("--profile", default="")
    parser.add_argument("--published-dataset-id", default="")
    parser.add_argument("--published-root", type=Path, default=REPO_ROOT / "published")
    parser.add_argument(
        "--bag-source", choices=("auto", "raw", "archive"), default="auto"
    )
    parser.add_argument("--vcodec", default="h264")
    parser.add_argument("--depth-min-m", type=float)
    parser.add_argument("--depth-max-m", type=float)
    parser.add_argument("--analyze-depth-only", action="store_true")
    parser.add_argument("--analysis-output", type=Path)
    parser.add_argument("--skip-validate-load", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    episode_dir = args.episode_dir.resolve()
    manifest_path = episode_dir / "episode_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing manifest: {manifest_path}")

    manifest = read_manifest(manifest_path)
    profile_ref = args.profile or manifest_profile_name(manifest)
    profile = load_profile(profile_ref)
    if manifest_profile_name(manifest) != profile["profile_name"]:
        raise RuntimeError(
            f"Manifest profile={manifest_profile_name(manifest)} does not match profile {profile['profile_name']}"
        )
    manifest_arms = manifest_active_arms(manifest)
    normalized_manifest_arms = (
        normalize_active_arms(manifest_arms) if manifest_arms else []
    )
    if manifest_arms:
        normalized_profile_arms = profile_required_arms(profile)
        if (
            normalized_profile_arms
            and normalized_manifest_arms != normalized_profile_arms
        ):
            raise RuntimeError(
                f"Manifest active_arms {normalized_manifest_arms} do not match profile arms {normalized_profile_arms}"
            )

    recorded_sensor_keys = [
        str(sensor.get("sensor_key", "")).strip()
        for sensor in manifest_sensors(manifest)
        if isinstance(sensor, dict)
    ]
    effective_profile = effective_profile_for_session(
        profile, normalized_manifest_arms, recorded_sensor_keys
    )

    topic_types = manifest_topic_types(manifest)
    all_topics_to_read = set(topic_types)
    bag_source = resolve_bag_source(episode_dir, all_topics_to_read, args.bag_source)
    value_topics = build_value_topics(effective_profile) & all_topics_to_read
    parse_topics = build_parse_topics(
        effective_profile, all_topics_to_read, topic_types, value_topics
    )
    series = read_topic_series(bag_source, all_topics_to_read, parse_topics)
    apply_realsense_metadata_timestamps(series)
    topics_with_data = {
        topic for topic, values in series.items() if values.timestamps_ns
    }

    selected_image_specs = build_selected_image_specs(
        effective_profile, topics_with_data
    )
    selected_depth_specs = build_selected_depth_specs(
        effective_profile, topics_with_data
    )
    effective_profile = build_effective_profile(
        effective_profile, selected_image_specs, selected_depth_specs
    )

    depth_encoder = resolve_depth_encoder(
        effective_profile, args.depth_min_m, args.depth_max_m
    )
    if args.analyze_depth_only:
        if not selected_depth_specs:
            raise RuntimeError("This episode has no selected depth streams to analyze.")
        all_depth_selections = {
            spec["field"]: [
                DepthSelection(
                    field=spec["field"],
                    topic=spec["topic"],
                    sample_index=index,
                    frame_index=index,
                    timestamp_ns=timestamp_ns,
                    skew_ms=0.0,
                )
                for index, timestamp_ns in enumerate(
                    series[spec["topic"]].timestamps_ns
                )
            ]
            for spec in selected_depth_specs
        }
        depth_arrays = extract_depth_arrays(bag_source, all_depth_selections)
        depth_scales = resolve_depth_scales(manifest, selected_depth_specs)
        depth_validation = build_depth_validation_report(
            selected_depth_specs,
            depth_arrays,
            depth_scales,
            depth_encoder,
        )
        depth_validation["selection"] = "all_recorded_depth_frames"
        if args.analysis_output is not None:
            output_path = args.analysis_output.expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(output_path, depth_validation)
            print(f"Depth analysis written to {output_path}")
        else:
            print(json.dumps(depth_validation, indent=2, sort_keys=True))
        return 0

    frames, depth_selections, alignment_diagnostics, summary_status = align_episode(
        series=series,
        profile=effective_profile,
        selected_image_specs=selected_image_specs,
        selected_depth_specs=selected_depth_specs,
        task_name=manifest_task_name(manifest),
        language_instruction=manifest_language_instruction(manifest),
    )

    image_fields = [spec["field"] for spec in selected_image_specs]
    image_shapes = image_shapes_from_frames(frames, image_fields)
    depth_arrays = extract_depth_arrays(bag_source, depth_selections)
    depth_scales = resolve_depth_scales(manifest, selected_depth_specs)

    depth_validation = build_depth_validation_report(
        selected_depth_specs,
        depth_arrays,
        depth_scales,
        depth_encoder,
    )

    if selected_depth_specs and depth_encoder is None:
        raise RuntimeError(
            "Native depth publication requires depth_encoding.depth_min_m and "
            "depth_encoding.depth_max_m in the profile, or both CLI overrides. "
            "Run --analyze-depth-only before choosing the dataset-wide range."
        )

    metric_depth = convert_depth_to_meters(depth_arrays, depth_scales)
    depth_shapes = attach_depth_frames(frames, metric_depth)
    features = build_features(effective_profile, image_shapes, depth_shapes)

    dataset_id = args.published_dataset_id
    if not dataset_id:
        raise RuntimeError("Conversion requires --published-dataset-id.")
    dataset_root = args.published_root / dataset_id
    artifact_dir = (
        dataset_root / "meta" / "spark_conversion" / manifest_episode_id(manifest)
    )
    if artifact_dir.exists():
        raise RuntimeError(
            f"Conversion artifacts already exist for {manifest_episode_id(manifest)} at {artifact_dir}"
        )

    dataset = get_or_create_dataset(
        dataset_root=dataset_root,
        dataset_id=dataset_id,
        fps=int(effective_profile["dataset"]["fps"]),
        features=features,
        rgb_encoder=RGBEncoderConfig(vcodec=args.vcodec),
        depth_encoder=depth_encoder,
    )
    dataset_episode_index = dataset.meta.total_episodes

    try:
        for frame in frames:
            dataset.add_frame(frame)
        dataset.save_episode()
    finally:
        dataset.finalize()

    if not args.skip_validate_load:
        reloaded = LeRobotDataset(
            repo_id=dataset_id,
            root=dataset_root,
            episodes=[dataset_episode_index],
            depth_output_unit="m",
        )
        sample = reloaded[0]
        for field, shape in depth_shapes.items():
            value = sample[field]
            if tuple(value.shape) != (shape[2], shape[0], shape[1]):
                raise RuntimeError(
                    f"Reloaded depth shape mismatch for {field}: {tuple(value.shape)}"
                )
        reloaded.finalize()

    source_snapshot = copy_source_snapshot(
        dataset_root=dataset_root,
        episode_dir=episode_dir,
        episode_id=manifest_episode_id(manifest),
        bag_source=bag_source,
    )

    diagnostics = {
        "episode_id": manifest_episode_id(manifest),
        "dataset_id": dataset_id,
        "dataset_root": str(dataset_root),
        "dataset_episode_index": dataset_episode_index,
        "clock_policy": manifest_clock_policy(manifest),
        "bag_source": {
            "kind": bag_source.kind,
            "bag_dir": str(bag_source.bag_dir),
            "storage_id": bag_source.storage_id,
            "archive_manifest_path": str(bag_source.archive_manifest_path)
            if bag_source.archive_manifest_path
            else None,
        },
        "summary_status": summary_status,
        "topic_diagnostics": {
            topic: series[topic].diagnostics() for topic in sorted(series)
        },
        "native_depth_validation": depth_validation,
        "source_snapshot": source_snapshot,
        **alignment_diagnostics,
    }
    summary = {
        "episode_id": manifest_episode_id(manifest),
        "dataset_id": dataset_id,
        "dataset_root": str(dataset_root),
        "dataset_episode_index": dataset_episode_index,
        "bag_source": bag_source.kind,
        "bag_storage_id": bag_source.storage_id,
        "published_frame_count": len(frames),
        "status": summary_status,
        "selected_image_fields": image_fields,
        "selected_depth_fields": [spec["field"] for spec in selected_depth_specs],
        "native_depth": bool(selected_depth_specs),
        "source_snapshot": source_snapshot,
    }
    write_conversion_artifacts(artifact_dir, diagnostics, effective_profile, summary)

    print(f"Converted {manifest_episode_id(manifest)} -> {dataset_root}")
    print(f"episode_index={dataset_episode_index}")
    print(f"status={summary_status}")
    print(f"published_frames={len(frames)}")
    print(f"artifacts={artifact_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
