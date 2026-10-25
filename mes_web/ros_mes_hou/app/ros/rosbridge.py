# app/ros/rosbridge.py
"""rosbridge WebSocket 通道（ROS1/ROS2 通用实现）。

协议操作（advertise/publish/subscribe）两代 ROS 完全一致，
消息类型串与 header.stamp 字段名等差异由 `app.ros.profile.RosProfile` 承载。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict

try:
    import websockets
except ImportError:  # pragma: no cover - depends on deployment environment
    websockets = None

from app.core.config import settings
from app.ros.base import RosDispatcher, RosbridgeError
from app.ros.normalizer import normalize_feedback_message
from app.ros.profile import RosProfile, get_active_profile

# V2：module_id / device_id 不做静态配置，全部取自前端微调请求（record.module_id / record.device_id）
# device_id 即 AXIS 寻址单元：1~20=轴号（J1旋转 1,5,9,13,17 / J2摆动 2,6,10,14,18 / J4伸缩 4,8,12,16,20），21~25=臂级

# V2：全局急停为系统级指令，module_id=0 / device_id=0（不使用静态模块编号），
# softstop_node 收到后转发 position=[0x01] 全局急停
SYSTEM_MODULE_ID = 0
SYSTEM_DEVICE_ID = 0


def _stamp(profile: RosProfile) -> Dict[str, int]:
    now = time.time()
    secs = int(now)
    nsecs = int((now - secs) * 1_000_000_000)
    first, second = profile.stamp_fields
    return {first: secs, second: nsecs}


def _zero_stamp(profile: RosProfile) -> Dict[str, int]:
    first, second = profile.stamp_fields
    return {first: 0, second: 0}


def _fine_tuning_topic_map(profile: RosProfile) -> Dict[str, Dict[str, Any]]:
    return {
        "rotation": {
            "topic": profile.topics["fine_tuning_rotation"],
            "message_type": profile.message_type("RotationCmd"),
        },
        "swing": {
            "topic": profile.topics["fine_tuning_swing"],
            "message_type": profile.message_type("SwingCmd"),
        },
        "telescopic": {
            "topic": profile.topics["fine_tuning_telescopic"],
            "message_type": profile.message_type("TelescopicCmd"),
        },
    }


def build_fine_tuning_publish_payload(
    parameter_name: str,
    position: float,
    module_id: int,
    device_id: int,
    profile: RosProfile | None = None,
) -> Dict[str, Any]:
    """构造微调下发消息。module_id/device_id 必须由前端请求传入，禁止节点内写死。"""
    profile = profile or get_active_profile()
    mapping = _fine_tuning_topic_map(profile).get(parameter_name)
    if mapping is None:
        raise RosbridgeError(f"unsupported fine-tuning parameter: {parameter_name}")
    try:
        module_id = int(module_id)
        device_id = int(device_id)
    except (TypeError, ValueError):
        raise RosbridgeError(
            f"invalid module_id/device_id: module_id={module_id!r}, device_id={device_id!r}")
    # V2：微调轴 device_id 必须落在 1~20 轴号空间
    if not (1 <= device_id <= 20):
        raise RosbridgeError(f"微调 device_id={device_id} 非法（V2 允许轴号 1~20）")
    if module_id <= 0:
        raise RosbridgeError(f"微调 module_id={module_id} 非法（必须由前端下发正整数模块号）")

    return {
        "topic": mapping["topic"],
        "message_type": mapping["message_type"],
        "message": {
            "header": {"stamp": _stamp(profile), "frame_id": ""},
            "module_id": module_id,
            "device_id": device_id,
            "position": [float(position)],
        },
        "parameter_name": parameter_name,
    }


def build_drawing_path_publish_payload(
    file_path: str,
    profile: RosProfile | None = None,
) -> Dict[str, Any]:
    profile = profile or get_active_profile()
    return {
        "topic": profile.topics["drawing_path"],
        "message_type": profile.std_message_type("String"),
        "message": {
            "data": json.dumps({"file_path": file_path}, ensure_ascii=False, separators=(",", ":")),
        },
    }


def build_module_confirm_publish_payload(
    module_id: int,
    profile: RosProfile | None = None,
    station: int | None = None,
) -> Dict[str, Any]:
    """构造模块确认握手消息。

    ROS 端 V2.1 会话式握手契约（module_confirme_node.py）：
      - 请求 position = [100, station]，station=工位号 1~15；
      - 模块号 = SEG（0~14），工位 N（1~15）→ SEG = N-1，故默认 station = module_id + 1；
      - 回执 module_id 原样返回、device_id=0、position=[100|1]。
    前端可用请求字段 station 显式指定工位号，覆盖默认推导。
    """
    profile = profile or get_active_profile()
    module_id = int(module_id)
    if station is None:
        station = module_id + 1
    station = int(station)
    if not (1 <= station <= 15):
        raise RosbridgeError(
            f"工位号 station={station} 非法（ROS 端握手协议要求 1~15；"
            f"模块号=SEG=工位-1，即 module_id 应在 0~14，或显式传 station）"
        )
    return {
        "topic": profile.topics["module_cmd"],
        "message_type": profile.message_type("IntCmd"),
        "message": {
            "header": {"stamp": _stamp(profile), "frame_id": ""},
            "module_id": module_id,
            "device_id": 0,
            "position": [100, station],
        },
    }


class RosbridgeClient:
    """rosbridge WebSocket 一次性客户端：每次下发新建连接，subscribe 后逐条收事件。"""

    def __init__(self, profile: RosProfile, url: str | None = None, timeout: float = 5.0):
        self.profile = profile
        self.url = url or settings.ROSBRIDGE_URL
        self.timeout = timeout

    def dispatch(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if action not in {"fine_tuning", "drawing_path", "module_confirm"}:
            raise RosbridgeError(f"unsupported rosbridge action: {action}")
        return asyncio.run(self.publish(action, payload))

    async def publish(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if websockets is None:
            raise RosbridgeError("missing websockets dependency")

        advertise_json = json.dumps(
            {
                "op": "advertise",
                "topic": payload["topic"],
                "type": payload["message_type"],
            },
            ensure_ascii=False,
        )
        publish_json = json.dumps(
            {
                "op": "publish",
                "topic": payload["topic"],
                "msg": payload["message"],
            },
            ensure_ascii=False,
        )
        confirm_msg = None
        success_topic = self.profile.topics["module_confirm_success"]
        failed_topic = self.profile.topics["module_confirm_failed"]

        try:
            async with websockets.connect(self.url, close_timeout=1) as ws:
                if action == "module_confirm":
                    for confirm_topic in (success_topic, failed_topic):
                        subscribe_json = json.dumps(
                            {
                                "op": "subscribe",
                                "topic": confirm_topic,
                                "type": self.profile.message_type("IntCmd"),
                            },
                            ensure_ascii=False,
                        )
                        await asyncio.wait_for(ws.send(subscribe_json), self.timeout)

                await asyncio.wait_for(ws.send(advertise_json), self.timeout)
                await asyncio.sleep(0.1)
                await asyncio.wait_for(ws.send(publish_json), self.timeout)

                if action == "module_confirm":
                    confirm_msg = await self._wait_for_module_confirm_success(ws, payload)
        except ConnectionRefusedError as exc:
            raise RosbridgeError(f"unable to connect to rosbridge: {self.url}") from exc
        except asyncio.TimeoutError as exc:
            raise RosbridgeError(f"rosbridge timeout while waiting for {action}: {self.url}") from exc
        except Exception as exc:
            raise RosbridgeError(f"rosbridge publish failed: {exc}. URL: {self.url}") from exc

        return {
            "sent": True,
            "mode": "rosbridge",
            "url": self.url,
            "action": action,
            "payload": payload,
            "confirmed": action != "module_confirm" or confirm_msg is not None,
            "confirm_topic": success_topic if action == "module_confirm" else None,
            "confirm_message": confirm_msg,
        }

    async def _wait_for_module_confirm_success(self, ws: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        expected_module_id = int(payload["message"]["module_id"])
        success_topic = self.profile.topics["module_confirm_success"]
        failed_topic = self.profile.topics["module_confirm_failed"]

        while True:
            raw = await asyncio.wait_for(ws.recv(), self.timeout)
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if event.get("op") != "publish":
                continue
            topic = event.get("topic")
            if topic not in {success_topic, failed_topic}:
                continue

            msg = event.get("msg") or {}
            module_id = int(msg.get("module_id", -1))
            device_id = int(msg.get("device_id", -1))
            position = msg.get("position") or []

            # 失败码契约：ROS 端 V2.1 已将 RESULT_FAILED 对齐为 1（此前发 -1 的
            # 不一致已由 ROS 端修复），这里维持 position[0]==1 判失败
            if (
                topic == failed_topic
                and module_id == expected_module_id
                and device_id == 0
                and position
                and int(position[0]) == 1
            ):
                raise RosbridgeError(f"module confirm failed: module_id={expected_module_id}")

            if (
                module_id == expected_module_id
                and device_id == 0
                and position
                and int(position[0]) == 100
            ):
                return msg

    async def publish_softstop(self) -> bool:
        """全局急停：/control/softstop 的 typeless publish（与 ROS1 现网行为一致）。"""
        if websockets is None:
            print("[紧急故障] websockets 库未安装，无法发送急停指令")
            return False

        softstop_topic = self.profile.topics["softstop"]
        emergency_msg = json.dumps(
            {
                "op": "publish",
                "topic": softstop_topic,
                "msg": {
                    "header": {"stamp": _zero_stamp(self.profile), "frame_id": ""},
                    "module_id": SYSTEM_MODULE_ID,
                    "device_id": SYSTEM_DEVICE_ID,
                    "position": [0x01],
                },
            },
            ensure_ascii=False,
        )

        try:
            async with websockets.connect(self.url, close_timeout=1) as ws:
                await asyncio.wait_for(ws.send(emergency_msg), timeout=2.0)
            print(f"[急停] 指令已发送: {softstop_topic}")
            return True
        except asyncio.TimeoutError:
            print("[紧急故障] 急停 WebSocket 发送超时")
            return False
        except Exception as exc:
            print(f"[紧急故障] 急停系统异常: {exc}")
            return False

    async def stream_feedback(self) -> AsyncIterator[Dict[str, Any]]:
        if websockets is None:
            yield {"data_type": "error", "message": "missing websockets dependency"}
            return

        feedback_topics = [
            self.profile.topics[key]
            for key in ("feedback_rotation", "feedback_swing", "feedback_telescope",
                        "feedback_sensor", "feedback_imu",
                        "feedback_pressure_avg", "feedback_vacuum_loss")
        ]

        try:
            async with websockets.connect(self.url, close_timeout=1) as ws:
                for topic in feedback_topics:
                    await ws.send(json.dumps({"op": "subscribe", "topic": topic}, ensure_ascii=False))

                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if event.get("op") != "publish":
                        continue
                    topic = event.get("topic", "")
                    if topic not in feedback_topics:
                        continue
                    yield normalize_feedback_message(topic, event.get("msg") or {}, self.profile.topics)
        except Exception as exc:
            yield {"data_type": "error", "message": f"rosbridge feedback failed: {exc}"}


class RosbridgeTransport(RosDispatcher):
    """基于 rosbridge 通道的适配器基类：ROS1/ROS2 共用的下发/反馈/急停实现。

    子类只需声明 `profile` 类属性并按需补充原生节点通道。
    """

    def __init__(self):
        self.client = RosbridgeClient(self.profile)

    def dispatch(self, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.client.dispatch(action, payload)

    async def stream_feedback(self) -> AsyncIterator[Dict[str, Any]]:
        async for feedback in self.client.stream_feedback():
            yield feedback

    async def trigger_emergency_stop(self) -> bool:
        return await self.client.publish_softstop()
