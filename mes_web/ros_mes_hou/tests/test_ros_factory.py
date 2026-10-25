import os
import unittest
from unittest.mock import patch

from app.ros import get_ros_dispatcher, reset_ros_dispatcher
from app.ros.base import RosDispatchError
from app.ros.ros1 import Ros1Dispatcher
from app.ros.ros2 import Ros2Dispatcher


class RosDispatcherFactoryTest(unittest.TestCase):
    def setUp(self):
        reset_ros_dispatcher()

    def tearDown(self):
        reset_ros_dispatcher()

    def test_default_ros_version_returns_ros1_dispatcher(self):
        with patch.dict(os.environ, {"ROS_VERSION": "1"}):
            self.assertIsInstance(get_ros_dispatcher(), Ros1Dispatcher)

    def test_ros_version_2_returns_ros2_dispatcher_with_ros2_dialect(self):
        with patch.dict(os.environ, {"ROS_VERSION": "2"}):
            dispatcher = get_ros_dispatcher()
            self.assertIsInstance(dispatcher, Ros2Dispatcher)
            self.assertEqual(
                dispatcher.profile.message_type("IntCmd"),
                "robot_control_backend/msg/IntCmd",
            )

    def test_ros2_adapter_has_no_native_channel(self):
        with patch.dict(os.environ, {"ROS_VERSION": "2"}):
            dispatcher = get_ros_dispatcher()
            self.assertFalse(dispatcher.profile.native_node)
            with self.assertRaises(RosDispatchError):
                dispatcher.publish_web_cmd("send_ros", {})
            self.assertFalse(dispatcher.native_status["ros_ready"])

    def test_invalid_ros_version_raises_runtime_error(self):
        with patch.dict(os.environ, {"ROS_VERSION": "3"}):
            with self.assertRaises(RuntimeError):
                get_ros_dispatcher()


if __name__ == "__main__":
    unittest.main()
