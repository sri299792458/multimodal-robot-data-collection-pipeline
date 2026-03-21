#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSPresetProfiles
from rosidl_runtime_py.utilities import get_message


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Return success once one ROS message is observed on a topic.")
    parser.add_argument("--topic", required=True, help="Topic name to probe.")
    parser.add_argument("--topic-type", default="", help="ROS interface type, e.g. sensor_msgs/msg/Image.")
    parser.add_argument("--timeout", type=float, default=1.0, help="Seconds to wait for one message.")
    return parser.parse_args(argv)


def resolve_topic_type(node: Node, topic: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for topic_name, type_names in node.get_topic_names_and_types():
            if topic_name == topic and type_names:
                return type_names[0]
        rclpy.spin_once(node, timeout_sec=0.1)
    raise RuntimeError(f"Topic type not found for {topic}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    rclpy.init(args=None)
    node = rclpy.create_node("data_pipeline_topic_probe")
    subscription = None
    received = False

    try:
        topic_type = args.topic_type or resolve_topic_type(node, args.topic, args.timeout)
        msg_type = get_message(topic_type)

        def _callback(_msg: object) -> None:
            nonlocal received
            received = True

        subscription = node.create_subscription(
            msg_type,
            args.topic,
            _callback,
            QoSPresetProfiles.SENSOR_DATA.value,
        )

        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline and not received:
            remaining = max(0.0, deadline - time.monotonic())
            rclpy.spin_once(node, timeout_sec=min(0.2, remaining))
        return 0 if received else 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        if subscription is not None:
            node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
