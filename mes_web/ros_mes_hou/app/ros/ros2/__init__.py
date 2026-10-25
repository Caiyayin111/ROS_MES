# app/ros/ros2/__init__.py
"""ROS2 适配器：与 ROS1 共用 rosbridge 通道，协议差异全部在 profile.py。

- 下发（微调/模块确认/图纸/急停/解除）：与 ROS1 同一套 RosbridgeTransport；
- 测试通道（/ros/** 接口）：web_cmd 走 rosbridge 发布，/robot_status 由
  后台常驻订阅线程维护连接状态与最新状态缓存（等价于 ROS1 的 rospy 节点）；
- ROS 端 ROS2 移植定稿后，通常只需微调 `app/ros/profile.py` 的 ROS2_PROFILE。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any, Dict

from app.ros.profile import ROS2_PROFILE, RosProfile
from app.ros.rosbridge import RosbridgeTransport


class _RosbridgeStatusWatcher:
    """常驻后台线程：订阅 /robot_status，维护 rosbridge 连接状态与最新测试状态。

    - 连接断开自动重连（5 秒间隔），连接状态反映在 connected 标志上；
    - 收到的状态若是 JSON 对象则合并进缓存，普通字符串则记为 robot_status；
    - 所有数据均来自 ROS 端测试程序，缓存恒带 test_data=True 标记。
    """

    RECONNECT_INTERVAL_S = 5.0

    def __init__(self, profile: RosProfile, url: str):
        self._profile = profile
        self._url = url
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.connected = False
        self.latest_status: Dict[str, Any] = {
            "robot_status": "unknown",
            "node": "ros2_rosbridge",
            "test_data": True,
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="ros2-status-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {**self.latest_status, "ros_ready": self.connected}

    def _set_connected(self, value: bool) -> None:
        with self._lock:
            self.connected = value

    def _ingest(self, raw: str, status_topic: str) -> None:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        if event.get("op") != "publish" or event.get("topic") != status_topic:
            return
        data = (event.get("msg") or {}).get("data")
        with self._lock:
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    self.latest_status.update(parsed)
                else:
                    self.latest_status["robot_status"] = data
            self.latest_status["test_data"] = True

    def _run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        try:
            import websockets
        except ImportError:  # pragma: no cover - 部署环境缺依赖时保持未连接状态
            return

        status_topic = self._profile.topics["robot_status"]
        subscribe_json = json.dumps(
            {
                "op": "subscribe",
                "topic": status_topic,
                "type": self._profile.std_message_type("String"),
            }
        )

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._url, close_timeout=1, open_timeout=3
                ) as ws:
                    await ws.send(subscribe_json)
                    self._set_connected(True)
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue  # 周期醒来检查停止标志，保证 stop() 秒级生效
                        self._ingest(raw, status_topic)
            except Exception:
                pass  # 连接失败/中断：走统一重连
            finally:
                self._set_connected(False)

            # 断线后等待重连间隔（可被 stop() 快速打断）
            deadline = time.monotonic() + self.RECONNECT_INTERVAL_S
            while not self._stop.is_set() and time.monotonic() < deadline:
                time.sleep(0.1)


class Ros2Dispatcher(RosbridgeTransport):
    profile = ROS2_PROFILE

    def __init__(self):
        super().__init__()
        self._watcher = _RosbridgeStatusWatcher(self.profile, self.client.url)

    def start_native_node(self) -> None:
        """启动 /robot_status 后台订阅线程（rosbridge 版"原生通道"）。"""
        self._watcher.start()

    def stop(self) -> None:
        self._watcher.stop()

    def publish_web_cmd(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return asyncio.run(self.client.publish_web_cmd(command, payload))

    @property
    def native_status(self) -> Dict[str, Any]:
        return self._watcher.snapshot()
