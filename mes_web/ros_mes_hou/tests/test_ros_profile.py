import os
import unittest
from unittest.mock import patch

from app.ros.profile import ROS1_PROFILE, ROS2_PROFILE
from app.ros.rosbridge import (
    build_drawing_path_publish_payload,
    build_fine_tuning_publish_payload,
    build_module_confirm_publish_payload,
)


class RosProfileDialectTest(unittest.TestCase):
    """ROS1/ROS2 协议差异契约：类型串格式与 header.stamp 字段名。"""

    def test_ros1_profile_uses_ros1_dialect(self):
        self.assertEqual(ROS1_PROFILE.message_type("IntCmd"), "robot_control_backend/IntCmd")
        self.assertEqual(ROS1_PROFILE.std_message_type("String"), "std_msgs/String")
        self.assertEqual(ROS1_PROFILE.stamp_fields, ("secs", "nsecs"))
        self.assertTrue(ROS1_PROFILE.native_node)

    def test_ros2_profile_uses_ros2_dialect(self):
        self.assertEqual(ROS2_PROFILE.message_type("IntCmd"), "robot_control_backend/msg/IntCmd")
        self.assertEqual(ROS2_PROFILE.std_message_type("String"), "std_msgs/msg/String")
        self.assertEqual(ROS2_PROFILE.stamp_fields, ("sec", "nanosec"))
        self.assertFalse(ROS2_PROFILE.native_node)

    def test_payload_builders_accept_explicit_ros2_profile(self):
        fine = build_fine_tuning_publish_payload("rotation", 1.5, 2, 5, profile=ROS2_PROFILE)
        self.assertEqual(fine["message_type"], "robot_control_backend/msg/RotationCmd")
        self.assertEqual(sorted(fine["message"]["header"]["stamp"]), ["nanosec", "sec"])

        module = build_module_confirm_publish_payload(5, station=6, profile=ROS2_PROFILE)
        self.assertEqual(module["message_type"], "robot_control_backend/msg/IntCmd")
        self.assertEqual(module["message"]["position"], [100, 6])
        self.assertEqual(sorted(module["message"]["header"]["stamp"]), ["nanosec", "sec"])

        drawing = build_drawing_path_publish_payload("a/b.json", profile=ROS2_PROFILE)
        self.assertEqual(drawing["message_type"], "std_msgs/msg/String")

    def test_fine_tuning_payload_rejects_out_of_range_axis(self):
        # V2.1 三轴布局：轴号 1~15，16 起非法（四轴残留 17~20 一并拒绝）
        with self.assertRaises(Exception):
            build_fine_tuning_publish_payload("rotation", 1.0, 2, 16)

    def test_default_builders_keep_ros1_contract(self):
        with patch.dict(os.environ, {"ROS_VERSION": "1"}):
            module = build_module_confirm_publish_payload(5, station=6)
            self.assertEqual(module["message_type"], "robot_control_backend/IntCmd")
            self.assertEqual(sorted(module["message"]["header"]["stamp"]), ["nsecs", "secs"])


if __name__ == "__main__":
    unittest.main()
