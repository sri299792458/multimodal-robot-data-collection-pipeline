from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_SCRIPT = REPO_ROOT / "data_pipeline" / "gelsight_bridge.py"


def generate_launch_description() -> LaunchDescription:
    arguments = [
        DeclareLaunchArgument("enable_left", default_value="true"),
        DeclareLaunchArgument("enable_right", default_value="true"),
        DeclareLaunchArgument("left_device_path", default_value=""),
        DeclareLaunchArgument("right_device_path", default_value=""),
        DeclareLaunchArgument("left_device_index", default_value="-1"),
        DeclareLaunchArgument("right_device_index", default_value="-1"),
        DeclareLaunchArgument("width", default_value="320"),
        DeclareLaunchArgument("height", default_value="240"),
        DeclareLaunchArgument("border_fraction", default_value="0.15"),
        DeclareLaunchArgument("fps", default_value="20.0"),
    ]

    left_process = ExecuteProcess(
        cmd=[
            "/usr/bin/python3",
            str(BRIDGE_SCRIPT),
            "--sensor-name",
            "left",
            "--device-path",
            LaunchConfiguration("left_device_path"),
            "--device-index",
            LaunchConfiguration("left_device_index"),
            "--width",
            LaunchConfiguration("width"),
            "--height",
            LaunchConfiguration("height"),
            "--border-fraction",
            LaunchConfiguration("border_fraction"),
            "--fps",
            LaunchConfiguration("fps"),
            "--frame-id",
            "spark_tactile_left_optical_frame",
        ],
        condition=IfCondition(LaunchConfiguration("enable_left")),
        output="screen",
    )

    right_process = ExecuteProcess(
        cmd=[
            "/usr/bin/python3",
            str(BRIDGE_SCRIPT),
            "--sensor-name",
            "right",
            "--device-path",
            LaunchConfiguration("right_device_path"),
            "--device-index",
            LaunchConfiguration("right_device_index"),
            "--width",
            LaunchConfiguration("width"),
            "--height",
            LaunchConfiguration("height"),
            "--border-fraction",
            LaunchConfiguration("border_fraction"),
            "--fps",
            LaunchConfiguration("fps"),
            "--frame-id",
            "spark_tactile_right_optical_frame",
        ],
        condition=IfCondition(LaunchConfiguration("enable_right")),
        output="screen",
    )

    return LaunchDescription(arguments + [left_process, right_process])
