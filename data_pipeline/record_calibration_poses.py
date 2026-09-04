#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.calibration.ur import CalibrationArm, load_arm_connection_info
from data_pipeline.pipeline_utils import (
    REPO_ROOT as PIPELINE_REPO_ROOT,
    normalize_active_arms,
    parse_task_list,
)


DEFAULT_POSES_FILE = PIPELINE_REPO_ROOT / "data_pipeline" / "configs" / "calibration_poses.local.json"
DEFAULT_SENSORS_FILE = PIPELINE_REPO_ROOT / "data_pipeline" / "configs" / "sensors.local.yaml"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record wrist-calibration robot poses using UR tool-flange TCP.")
    parser.add_argument("--active-arms", required=True, help="Comma-separated arms: lightning, thunder, or lightning,thunder")
    parser.add_argument("--output-file", default=str(DEFAULT_POSES_FILE))
    parser.add_argument("--min-poses", type=int, default=5)
    parser.add_argument("--preview-camera", default="", help="Optional camera key for live ChArUco preview.")
    parser.add_argument("--sensors-file", default=str(DEFAULT_SENSORS_FILE))
    parser.add_argument("--dictionary", default="DICT_4X4_50")
    parser.add_argument("--squares-x", type=int, default=9)
    parser.add_argument("--squares-y", type=int, default=6)
    parser.add_argument("--square-length", type=float, default=0.03)
    parser.add_argument("--marker-length", type=float, default=0.022)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    return parser


def save_poses(path: Path, active_arms: list[str], poses: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "timestamp": datetime.now().isoformat(),
        "active_arms": active_arms,
        "tcp_frame_assumption": "tool_flange",
        "poses": poses,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def start_preview_process(args: argparse.Namespace) -> subprocess.Popen[bytes] | None:
    if not str(args.preview_camera).strip():
        return None
    cmd = [
        sys.executable,
        str(REPO_ROOT / "data_pipeline" / "debug_charuco_detection.py"),
        "--camera",
        str(args.preview_camera).strip(),
        "--sensors-file",
        str(args.sensors_file),
        "--width",
        str(args.width),
        "--height",
        str(args.height),
        "--fps",
        str(args.fps),
        "--squares-x",
        str(args.squares_x),
        "--squares-y",
        str(args.squares_y),
        "--square-length",
        str(args.square_length),
        "--marker-length",
        str(args.marker_length),
        "--dictionary",
        str(args.dictionary),
    ]
    log_path = Path("/tmp/spark_calibration_pose_preview.log")
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)
    print(f"Starting camera preview for {args.preview_camera} (log: {log_path})")
    return process


def stop_preview_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def main() -> int:
    args = build_arg_parser().parse_args()
    active_arms = normalize_active_arms(parse_task_list(args.active_arms))
    if not active_arms:
        raise RuntimeError("No active arms selected for calibration pose recording.")

    arm_info = load_arm_connection_info(active_arms)
    missing = [arm for arm in active_arms if arm not in arm_info]
    if missing:
        raise RuntimeError(f"Missing runtime connection info for arms: {missing}")

    preview_process = start_preview_process(args)
    arms: dict[str, CalibrationArm] = {}
    poses: list[dict[str, object]] = []
    try:
        arms = {arm: CalibrationArm(info, connect_control=True) for arm, info in arm_info.items()}
        for arm in active_arms:
            arms[arm].enable_freedrive()

        print("\nCalibration pose recording")
        print("==========================")
        print("UR TCP is assumed to be set to the tool flange.")
        print("Move the arm(s) in freedrive so the ChArUco board is well observed.")
        print("Commands:")
        print("  r  record current pose")
        print("  d  delete last pose")
        print("  l  list recorded poses")
        print("  q  save and quit")

        while True:
            command = input("\n[r/d/l/q] > ").strip().lower()
            if command == "r":
                pose_index = len(poses) + 1
                pose_entry: dict[str, object] = {
                    "name": f"pose_{pose_index:03d}",
                    "arms": {},
                }
                for arm in active_arms:
                    pose_entry["arms"][arm] = {
                        "joint_positions": arms[arm].get_actual_q(),
                        "tcp_pose": arms[arm].get_actual_tcp_pose(),
                    }
                poses.append(pose_entry)
                print(f"Recorded {pose_entry['name']}")
            elif command == "d":
                if not poses:
                    print("No poses to delete.")
                    continue
                removed = poses.pop()
                print(f"Deleted {removed['name']}")
            elif command == "l":
                if not poses:
                    print("No poses recorded yet.")
                    continue
                for index, pose in enumerate(poses, start=1):
                    print(f"{index:02d}: {pose['name']}")
            elif command == "q":
                if len(poses) < int(args.min_poses):
                    print(f"Need at least {args.min_poses} poses, currently have {len(poses)}.")
                    continue
                output_path = Path(args.output_file).expanduser()
                save_poses(output_path, active_arms, poses)
                print(f"Saved {len(poses)} poses to {output_path}")
                return 0
            else:
                print("Unknown command.")
    finally:
        stop_preview_process(preview_process)
        for arm in active_arms:
            arm_handle = arms.get(arm)
            if arm_handle is not None:
                arm_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
