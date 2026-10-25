import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.endpoints.ros import get_ros_status, send_ros
from app.ros.base import RosbridgeError


class _BrokenDispatcher:
    """模拟 rosbridge 不可达时的 ROS2 适配器。"""

    def publish_web_cmd(self, command, payload):
        raise RosbridgeError("unable to connect to rosbridge: ws://127.0.0.1:9010")

    @property
    def native_status(self):
        return {
            "robot_status": "unknown",
            "node": "ros2_rosbridge",
            "test_data": True,
            "ros_ready": False,
        }


class RosEndpointsTest(unittest.TestCase):
    def test_send_ros_returns_503_when_rosbridge_unreachable(self):
        # 契约（ROS 测试页任务）：连接断开时状态报未连接，发送接口返回 503
        with patch("app.api.endpoints.ros.get_ros_dispatcher", return_value=_BrokenDispatcher()):
            with self.assertRaises(HTTPException) as ctx:
                send_ros(msg="hello")

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("unable to connect", ctx.exception.detail)

    def test_get_ros_status_reports_not_ready(self):
        with patch("app.api.endpoints.ros.get_ros_dispatcher", return_value=_BrokenDispatcher()):
            response = get_ros_status()

        self.assertEqual(response["code"], 200)
        self.assertFalse(response["data"]["ros_ready"])
        self.assertTrue(response["data"]["test_data"])


if __name__ == "__main__":
    unittest.main()
