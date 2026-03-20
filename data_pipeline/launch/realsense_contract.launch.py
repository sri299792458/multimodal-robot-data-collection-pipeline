from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _is_unset(value: str) -> bool:
    return value.strip() in {"", "''", '""'}


def _normalize_serial_for_ros(value: str) -> str:
    serial = value.strip().strip("'").strip('"')
    if not serial:
        return "''"
    if serial.startswith("_"):
        return serial
    if serial.isdigit():
        return f"_{serial}"
    return serial


def _camera_node(context, camera_name: str, serial_key: str, device_type_key: str) -> Node:
    camera_namespace = LaunchConfiguration("camera_namespace").perform(context).strip("/")
    namespace = f"/{camera_namespace}".rstrip("/")
    return Node(
        package="realsense2_camera",
        executable="realsense2_camera_node",
        namespace=namespace,
        name=camera_name,
        output="screen",
        emulate_tty=True,
        arguments=["--ros-args", "--log-level", LaunchConfiguration("log_level")],
        remappings=[
            ("~/color/image_rect_raw", "~/color/image_raw"),
        ],
        parameters=[
            {
                "serial_no": _normalize_serial_for_ros(LaunchConfiguration(serial_key).perform(context)),
                "device_type": LaunchConfiguration(device_type_key).perform(context),
                "rgb_camera.color_profile": LaunchConfiguration(f"{camera_name}_color_profile"),
                "depth_module.color_profile": LaunchConfiguration(f"{camera_name}_color_profile"),
                "depth_module.depth_profile": LaunchConfiguration(f"{camera_name}_depth_profile"),
                "enable_color": True,
                "enable_depth": LaunchConfiguration("enable_depth"),
                "initial_reset": LaunchConfiguration("initial_reset"),
                "publish_tf": False,
                "tf_publish_rate": 0.0,
                "pointcloud.enable": False,
                "align_depth.enable": False,
                "enable_sync": False,
                "diagnostics_period": 0.0,
                "wait_for_device_timeout": LaunchConfiguration("wait_for_device_timeout"),
                "reconnect_timeout": LaunchConfiguration("reconnect_timeout"),
                "rgb_camera.global_time_enabled": True,
                "depth_module.global_time_enabled": True,
            }
        ],
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
        _camera_node(context, "wrist", "wrist_serial_no", "wrist_device_type"),
        _camera_node(context, "scene", "scene_serial_no", "scene_device_type"),
    ]


def generate_launch_description() -> LaunchDescription:
    arguments = [
        DeclareLaunchArgument("camera_namespace", default_value="spark/cameras"),
        DeclareLaunchArgument("wrist_serial_no", default_value=""),
        DeclareLaunchArgument("scene_serial_no", default_value=""),
        DeclareLaunchArgument("wrist_device_type", default_value=""),
        DeclareLaunchArgument("scene_device_type", default_value=""),
        DeclareLaunchArgument("wrist_color_profile", default_value="640,480,30"),
        DeclareLaunchArgument("scene_color_profile", default_value="640,480,30"),
        DeclareLaunchArgument("wrist_depth_profile", default_value="640,480,30"),
        DeclareLaunchArgument("scene_depth_profile", default_value="640,480,30"),
        DeclareLaunchArgument("enable_depth", default_value="true"),
        DeclareLaunchArgument("initial_reset", default_value="false"),
        DeclareLaunchArgument("wait_for_device_timeout", default_value="-1."),
        DeclareLaunchArgument("reconnect_timeout", default_value="6."),
        DeclareLaunchArgument("log_level", default_value="info"),
    ]
    return LaunchDescription(arguments + [OpaqueFunction(function=_launch_setup)])
