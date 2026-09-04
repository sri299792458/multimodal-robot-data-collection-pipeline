from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from builtin_interfaces.msg import Time
from scipy.spatial.transform import Rotation

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "TeleopSoftware"))

from teleop_runtime_core import _pose_message  # noqa: E402

from data_pipeline.convert_episode_bag_to_lerobot import parse_message  # noqa: E402


class EndEffectorOrientationTest(unittest.TestCase):
    def test_rtde_rotation_vector_round_trips_through_pose_message(self) -> None:
        rotvec = np.asarray([0.61257446, -1.57243924, 2.18729392])
        pose = [0.55, 0.34, 0.01, *rotvec]

        msg = _pose_message(Time(sec=12, nanosec=34), pose)
        quaternion = msg.pose.orientation
        published_rotation = Rotation.from_quat(
            [quaternion.x, quaternion.y, quaternion.z, quaternion.w]
        )
        expected_rotation = Rotation.from_rotvec(rotvec)
        self.assertAlmostEqual(
            (expected_rotation.inv() * published_rotation).magnitude(), 0.0, places=12
        )

        _, converted = parse_message(
            "/spark/thunder/robot/eef_pose",
            msg,
            bag_timestamp_ns=0,
            parse_value=True,
        )
        np.testing.assert_allclose(converted[3:6], rotvec, rtol=0.0, atol=1e-6)


if __name__ == "__main__":
    unittest.main()
