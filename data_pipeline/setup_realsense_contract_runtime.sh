#!/usr/bin/env bash

set -euo pipefail

set +u
source /opt/ros/jazzy/setup.bash
set -u

if ! command -v ros2 >/dev/null 2>&1; then
  echo "ros2 was not found after sourcing /opt/ros/jazzy/setup.bash" >&2
  exit 1
fi

if ! ros2 pkg prefix realsense2_camera >/dev/null 2>&1; then
  echo "realsense2_camera is not installed in the system ROS environment" >&2
  echo "Install: sudo apt install ros-jazzy-realsense2-camera ros-jazzy-realsense2-camera-msgs" >&2
  exit 1
fi

if ! command -v rs-enumerate-devices >/dev/null 2>&1; then
  echo "rs-enumerate-devices was not found in PATH" >&2
  exit 1
fi

echo "Using the system-installed realsense2_camera runtime from /opt/ros/jazzy."
echo "No local build step is required for the current pipeline path."
