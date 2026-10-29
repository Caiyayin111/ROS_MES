import json
import os
import unittest
from unittest.mock import Mock, patch

from app.ros import get_ros_dispatcher, reset_ros_dispatcher
from app.ros.base import RosDispatchError
from app.ros.profile import ROS1_PROFILE, ROS2_PROFILE
from app.ros.ros1 import Ros1Dispatcher
from app.ros.ros2 import Ros2Dispatcher
from app.ros.rosbridge import _RosbridgeStatusWatcher


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

    def test_ros2_adapter_publishes_web_cmd_via_rosbridge(self):
        with patch.dict(os.environ, {"ROS_VERSION": "2"}):
            dispatcher = get_ros_dispatcher()
            sent = []

            class FakeWebSocket:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def send(self, message):
                    sent.append(message)

            with patch("app.ros.rosbridge.websockets") as websockets_mock:
                websockets_mock.connect = Mock(return_value=FakeWebSocket())
                result = dispatcher.publish_web_cmd("send_ros", {"msg": "hello"})

            self.assertEqual(result["topic"], "/web_cmd")
            self.assertEqual(result["mode"], "rosbridge")
            advertise = json.loads(sent[0])
            publish = json.loads(sent[1])
            self.assertEqual(advertise["op"], "advertise")
            self.assertEqual(advertise["type"], "std_msgs/msg/String")
            self.assertEqual(
                json.loads(publish["msg"]["data"]),
                {"command": "send_ros", "payload": {"msg": "hello"}},
            )

    def test_ros1_adapter_also_publishes_web_cmd_via_rosbridge(self):
        # 测试页通道统一走 rosbridge：干净 venv（无 rospy）下 ROS1 模式同样可用
        with patch.dict(os.environ, {"ROS_VERSION": "1"}):
            dispatcher = get_ros_dispatcher()
            sent = []

            class FakeWebSocket:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def send(self, message):
                    sent.append(message)

            with patch("app.ros.rosbridge.websockets") as websockets_mock:
                websockets_mock.connect = Mock(return_value=FakeWebSocket())
                result = dispatcher.publish_web_cmd("send_ros", {"msg": "hello"})

            self.assertEqual(result["topic"], "/web_cmd")
            self.assertEqual(json.loads(sent[0])["type"], "std_msgs/String")

    def test_ros2_adapter_status_defaults_to_unknown_until_connected(self):
        with patch.dict(os.environ, {"ROS_VERSION": "2"}):
            status = get_ros_dispatcher().native_status

        self.assertFalse(status["ros_ready"])
        self.assertEqual(status["robot_status"], "unknown")
        self.assertTrue(status["test_data"])

    def test_ros2_status_watcher_ingests_robot_status(self):
        watcher = _RosbridgeStatusWatcher(ROS2_PROFILE, url="ws://test")

        watcher._ingest(
            '{"op":"publish","topic":"/robot_status","msg":{"data":"测试运行中"}}',
            "/robot_status",
        )
        self.assertEqual(watcher.latest_status["robot_status"], "测试运行中")

        watcher._ingest(
            '{"op":"publish","topic":"/robot_status",'
            '"msg":{"data":"{\\"state\\":\\"ok\\"}"}}',
            "/robot_status",
        )
        self.assertEqual(watcher.latest_status["state"], "ok")
        self.assertTrue(watcher.latest_status["test_data"])

    def test_invalid_ros_version_raises_runtime_error(self):
        with patch.dict(os.environ, {"ROS_VERSION": "3"}):
            with self.assertRaises(RuntimeError):
                get_ros_dispatcher()


if __name__ == "__main__":
    unittest.main()
