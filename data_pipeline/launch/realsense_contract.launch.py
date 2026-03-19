from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

try:
    from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
except ImportError:  # pragma: no cover - depends on ROS environment
    PackageNotFoundError = Exception
    get_package_share_directory = None


def _is_unset(value: str) -> bool:
    return value.strip() in {"", "''", '""'}


def _resolve_realsense_launch_file() -> Path:
    if get_package_share_directory is not None:
        try:
            package_share = Path(get_package_share_directory("realsense2_camera"))
            installed_launch = package_share / "launch" / "rs_launch.py"
            if installed_launch.is_file():
                return installed_launch
        except PackageNotFoundError:
            pass

    repo_root = Path(__file__).resolve().parents[2]
    local_launch = repo_root / "realsense-ros" / "realsense2_camera" / "launch" / "rs_launch.py"
    if local_launch.is_file():
        return local_launch

    raise FileNotFoundError(
        "Could not locate realsense2_camera/launch/rs_launch.py in the ROS installation "
        "or the local realsense-ros checkout."
    )


def _camera_include(camera_name: str, serial_key: str) -> IncludeLaunchDescription:
    launch_file = _resolve_realsense_launch_file()
    return IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(launch_file)),
        launch_arguments={
            "camera_namespace": LaunchConfiguration("camera_namespace"),
            "camera_name": camera_name,
            "serial_no": LaunchConfiguration(serial_key),
            "output": "screen",
            "log_level": LaunchConfiguration("log_level"),
            "enable_color": "true",
            "enable_depth": LaunchConfiguration("enable_depth"),
            "rgb_camera.color_profile": LaunchConfiguration(f"{camera_name}_color_profile"),
            "depth_module.depth_profile": LaunchConfiguration(f"{camera_name}_depth_profile"),
            "pointcloud.enable": "false",
            "align_depth.enable": "false",
            "publish_tf": LaunchConfiguration("publish_tf"),
            "diagnostics_period": "0.0",
        }.items(),
    )


def _launch_setup(context):
    wrist_serial = LaunchConfiguration("wrist_serial_no").perform(context)
    scene_serial = LaunchConfiguration("scene_serial_no").perform(context)
    if _is_unset(wrist_serial) or _is_unset(scene_serial):
        raise RuntimeError(
            "Both wrist_serial_no and scene_serial_no must be provided. "
            "The V1 manifest requires explicit RealSense serial numbers."
        )

    return [
        _camera_include("wrist", "wrist_serial_no"),
        _camera_include("scene", "scene_serial_no"),
    ]


def generate_launch_description() -> LaunchDescription:
    arguments = [
        DeclareLaunchArgument("camera_namespace", default_value="spark/cameras"),
        DeclareLaunchArgument("wrist_serial_no", default_value=""),
        DeclareLaunchArgument("scene_serial_no", default_value=""),
        DeclareLaunchArgument("wrist_color_profile", default_value="640,480,30"),
        DeclareLaunchArgument("scene_color_profile", default_value="640,480,30"),
        DeclareLaunchArgument("wrist_depth_profile", default_value="640,480,30"),
        DeclareLaunchArgument("scene_depth_profile", default_value="640,480,30"),
        DeclareLaunchArgument("enable_depth", default_value="true"),
        DeclareLaunchArgument("publish_tf", default_value="false"),
        DeclareLaunchArgument("log_level", default_value="info"),
    ]
    return LaunchDescription(arguments + [OpaqueFunction(function=_launch_setup)])
