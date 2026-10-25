# app/ros/normalizer.py
"""ROS 反馈消息归一化：把 rosbridge 收到的原始消息转成前端反馈形状。

纯业务规则，与 ROS1/ROS2 无关（时间戳字段的差异只存在于下发方向的
header 序列化，反馈方向的原始消息原样透传在 `raw` 字段里）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from app.ros.profile import TOPIC_DEFAULTS

# V2 device_id → 反馈标签
#   旋转 J1：1,5,9,13,17；摆动 J2：2,6,10,14,18；伸缩 J4：4,8,12,16,20
#   压力/IMU 同为臂级 21~25，按话题区分（见 normalize_feedback_message）
def _build_feedback_labels() -> Dict[int, tuple]:
    labels: Dict[int, tuple] = {}
    type_by_joint = {
        0: ("rotation_axis_encoder", "旋转轴编码器"),
        1: ("swing_axis_encoder", "摆动轴编码器"),
        3: ("telescope_axis_encoder", "伸缩轴编码器"),
    }
    for arm in range(1, 6):                       # 臂 1~5
        for joint, (data_type, name) in type_by_joint.items():
            axis = (arm - 1) * 4 + joint + 1      # 4轴ID布局
            labels[axis] = (data_type, f"臂{arm}{name}")
    for arm in range(1, 6):
        labels[20 + arm] = ("pressure_sensor", f"臂{arm}压力传感器")
    return labels


FEEDBACK_LABELS = _build_feedback_labels()
# 臂级 device_id 21~25 在 IMU 话题上覆盖为陀螺仪标签
IMU_FEEDBACK_LABEL = ("imu_pose", "陀螺仪姿态")


def _position_value(value: Any) -> float:
    if isinstance(value, list):
        return float(value[0]) if value else 0.0
    return float(value)


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_feedback_message(
    topic: str,
    msg: Dict[str, Any],
    topics: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """归一化一条反馈消息；topics 缺省用默认话题表（与 profile.topics 同构）。"""
    topics = topics or TOPIC_DEFAULTS
    device_id = int(msg.get("device_id", 0))
    # 新话题分支先于默认表查找，保证标签稳定（即使 FEEDBACK_LABELS 未覆盖）
    if topic == topics["feedback_pressure_avg"] and 21 <= device_id <= 25:
        data_type = "pressure_average"
        feedback_type = f"臂{device_id - 20}压力平均值(5s窗口)"
    elif topic == topics["feedback_vacuum_loss"] and 21 <= device_id <= 25:
        data_type = "vacuum_loss_event"
        feedback_type = f"臂{device_id - 20}吸盘失压告警"
    # 臂级 21~25：IMU 话题识别为陀螺仪，其余按压力传感器
    elif topic == topics["feedback_imu"] and 21 <= device_id <= 25:
        data_type, feedback_type = IMU_FEEDBACK_LABEL[0], f"臂{device_id - 20}陀螺仪姿态"
    else:
        data_type, feedback_type = FEEDBACK_LABELS.get(device_id, ("unknown", "unknown feedback"))
    header = msg.get("header") or {}
    stamp = header.get("stamp") if isinstance(header, dict) else None

    normalized = {
        "time_id": time.time(),
        "topic": topic,
        "header": stamp or header,
        "module_id": int(msg.get("module_id", 0)),
        "device_id": device_id,
        "position": _position_value(msg.get("position", 0)),
        "data_type": data_type,
        "feedback_type": feedback_type,
        "raw": msg,
    }
    if "id" in msg:
        normalized["id"] = msg["id"]

    if data_type == "imu_pose":
        normalized.update(
            {
                "swing_angle": _float_value(msg.get("swing_angle")),
                "rotation_angle": _float_value(msg.get("rotation_angle")),
                "x": _float_value(msg.get("x")),
                "y": _float_value(msg.get("y")),
                "z": _float_value(msg.get("z")),
            }
        )

    return normalized
