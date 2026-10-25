# app/ros/ros2/__init__.py
"""ROS2 适配器：与 ROS1 共用 rosbridge 通道，协议差异全部在 profile.py。

当前按 rosbridge 通用协议预设实现（消息类型串 pkg/msg/Type、stamp 字段
sec/nanosec）。ROS 端 ROS2 移植定稿后，通常只需微调
`app/ros/profile.py` 中的 ROS2_PROFILE 与话题默认值；
若 ROS 端提供原生通道，再在本类补充 publish_web_cmd / start_native_node。
"""
from app.ros.profile import ROS2_PROFILE
from app.ros.rosbridge import RosbridgeTransport


class Ros2Dispatcher(RosbridgeTransport):
    profile = ROS2_PROFILE

    # 原生节点通道暂无：/ros 接口会返回 503（publish_web_cmd 默认抛 RosDispatchError）
