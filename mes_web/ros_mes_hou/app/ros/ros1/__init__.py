# app/ros/ros1/__init__.py
"""ROS1 适配器：rosbridge 通道 + rospy 原生节点（/web_cmd、/robot_status）。"""
from typing import Any, Dict

from app.ros.profile import ROS1_PROFILE
from app.ros.ros1 import native
from app.ros.rosbridge import RosbridgeTransport


class Ros1Dispatcher(RosbridgeTransport):
    profile = ROS1_PROFILE

    def start_native_node(self) -> None:
        native.start_ros_thread()

    def publish_web_cmd(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return native.publish_ros_command(command, payload)

    @property
    def native_status(self) -> Dict[str, Any]:
        return {**native.latest_status, "ros_ready": native.ros_ready}
