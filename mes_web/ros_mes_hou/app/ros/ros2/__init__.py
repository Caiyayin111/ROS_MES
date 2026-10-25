# app/ros/ros2/__init__.py
"""ROS2 适配器：与 ROS1 共用 rosbridge 通道，协议差异全部在 profile.py。

下发（微调/模块确认/图纸/急停/解除/测试页）与状态订阅均由 RosbridgeTransport
统一实现；ROS 端 ROS2 移植定稿后，通常只需微调
`app/ros/profile.py` 中的 ROS2_PROFILE（类型串前缀、stamp 字段、话题名）。
"""
from app.ros.profile import ROS2_PROFILE
from app.ros.rosbridge import RosbridgeTransport


class Ros2Dispatcher(RosbridgeTransport):
    profile = ROS2_PROFILE
