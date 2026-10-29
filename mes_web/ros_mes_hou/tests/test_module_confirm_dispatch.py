import unittest
from unittest.mock import Mock, patch

from app.api.endpoints.module import lock_and_dispatch_module
from app.ros.base import RosbridgeError
from app.ros.profile import ROS1_PROFILE
from app.ros.rosbridge import RosbridgeClient, build_module_confirm_publish_payload


class DummyDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, action, payload):
        self.calls.append((action, payload))
        return {"sent": True, "mode": "test", "action": action, "payload": payload}


class FailingDispatcher(DummyDispatcher):
    def __init__(self, fail_on_call):
        super().__init__()
        self.fail_on_call = fail_on_call

    def dispatch(self, action, payload):
        self.calls.append((action, payload))
        if len(self.calls) == self.fail_on_call:
            raise RosbridgeError("simulated module confirm failure")
        return {"sent": True, "mode": "test", "action": action, "confirmed": True, "payload": payload}


class ModuleConfirmDispatchTest(unittest.TestCase):
    def test_build_module_confirm_payload_matches_topic_contract(self):
        # 新握手契约：position=[100, seg, arm]；module_id 为业务模块号原样回执。
        payload = build_module_confirm_publish_payload(18, seg=3, arm=2)

        self.assertEqual(payload["topic"], "/control/module_cmd")
        self.assertEqual(payload["message_type"], "robot_control_backend/IntCmd")
        self.assertEqual(payload["message"]["module_id"], 18)
        self.assertEqual(payload["message"]["device_id"], 0)
        self.assertEqual(payload["message"]["position"], [100, 3, 2])
        self.assertIn("stamp", payload["message"]["header"])

    def test_build_module_confirm_payload_rejects_invalid_seg_or_arm(self):
        with self.assertRaises(RosbridgeError):
            build_module_confirm_publish_payload(18, seg=15, arm=1)
        with self.assertRaises(RosbridgeError):
            build_module_confirm_publish_payload(18, seg=0, arm=6)

    def test_build_module_confirm_payload_accepts_seg_and_arm_boundaries(self):
        self.assertEqual(
            build_module_confirm_publish_payload(1, seg=0, arm=1)["message"]["position"],
            [100, 0, 1],
        )
        self.assertEqual(
            build_module_confirm_publish_payload(1, seg=14, arm=5)["message"]["position"],
            [100, 14, 5],
        )

    def test_lock_module_dispatches_module_confirm_topic_before_returning_success(self):
        dispatcher = DummyDispatcher()

        response = lock_and_dispatch_module(
            {"x": 1, "y": 2, "module_id": 17, "device_id": 3, "position": 0,
             "unit_ids": [32, 64, 96]},
            dispatcher=dispatcher,
        )

        self.assertEqual(response["code"], 200)
        self.assertEqual(response["data"]["module_id"], 17)
        self.assertEqual(response["data"]["unit_ids"], [32, 64, 96])
        self.assertEqual(response["dispatch"]["action"], "module_confirm")
        self.assertEqual(len(dispatcher.calls), 3)
        for index, (action, payload) in enumerate(dispatcher.calls, start=1):
            self.assertEqual(action, "module_confirm")
            self.assertEqual(payload["topic"], "/control/module_cmd")
            self.assertEqual(payload["message_type"], "robot_control_backend/IntCmd")
            self.assertEqual(payload["message"]["module_id"], 17)
            self.assertEqual(payload["message"]["device_id"], 0)
            self.assertEqual(payload["message"]["position"], [100, 0, index])
            self.assertEqual(payload["business"]["x"], 1)
            self.assertEqual(payload["business"]["y"], 2)
            self.assertEqual(payload["business"]["device_id"], 3)
            self.assertEqual(payload["business"]["unit_id"], index * 32)
            self.assertEqual(payload["business"]["arm"], index)
            self.assertEqual(payload["business"]["seg"], 0)

    def test_lock_module_without_unit_ids_is_rejected_with_422(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            lock_and_dispatch_module(
                {"x": 1, "y": 2, "module_id": 17, "device_id": 3, "position": 0},
                dispatcher=DummyDispatcher(),
            )

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("unit_ids", ctx.exception.detail)

    def test_lock_module_stops_after_first_failed_arm(self):
        from fastapi import HTTPException

        dispatcher = FailingDispatcher(fail_on_call=2)
        with self.assertRaises(HTTPException) as ctx:
            lock_and_dispatch_module(
                {"x": 1, "y": 2, "module_id": 17, "device_id": 3,
                 "unit_ids": [32, 64, 96]},
                dispatcher=dispatcher,
            )

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(len(dispatcher.calls), 2)
        self.assertEqual(ctx.exception.detail["failed_unit_id"], 64)
        self.assertEqual(ctx.exception.detail["confirmed_unit_ids"], [32])
        self.assertEqual(ctx.exception.detail["pending_unit_ids"], [64, 96])

    def test_module_confirm_dispatch_subscribes_success_topic_and_waits_for_matching_ack(self):
        sent_messages = []

        class FakeWebSocket:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, message):
                sent_messages.append(message)

            async def recv(self):
                return (
                    '{"op":"publish","topic":"/hardware/module_confirm_success",'
                    '"msg":{"module_id":18,"device_id":0,"position":[100]}}'
                )

        dispatcher = RosbridgeClient(
            ROS1_PROFILE, url="ws://test", timeout=0.1, module_confirm_timeout=0.1)
        payload = build_module_confirm_publish_payload(18, seg=3, arm=2)

        with patch("app.ros.rosbridge.websockets") as websockets_mock:
            websockets_mock.connect = Mock(return_value=FakeWebSocket())
            result = dispatcher.dispatch("module_confirm", payload)

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirm_topic"], "/hardware/module_confirm_success")
        self.assertIn('"op": "subscribe"', sent_messages[0])
        self.assertIn('/hardware/module_confirm_success', sent_messages[0])
        self.assertIn('"op": "subscribe"', sent_messages[1])
        self.assertIn('/hardware/web_module_cmd', sent_messages[1])
        self.assertIn('"op": "advertise"', sent_messages[2])
        self.assertIn('"op": "publish"', sent_messages[3])

    def test_module_confirm_dispatch_subscribes_hardware_web_cmd_and_fails_on_position_one(self):
        sent_messages = []

        class FakeWebSocket:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, message):
                sent_messages.append(message)

            async def recv(self):
                return (
                    '{"op":"publish","topic":"/hardware/web_module_cmd",'
                    '"msg":{"module_id":18,"device_id":0,"position":[1]}}'
                )

        dispatcher = RosbridgeClient(
            ROS1_PROFILE, url="ws://test", timeout=0.1, module_confirm_timeout=0.1)
        payload = build_module_confirm_publish_payload(18, seg=3, arm=2)

        with patch("app.ros.rosbridge.websockets") as websockets_mock:
            websockets_mock.connect = Mock(return_value=FakeWebSocket())
            with self.assertRaises(RosbridgeError) as exc:
                dispatcher.dispatch("module_confirm", payload)

        self.assertIn("module confirm failed", str(exc.exception))
        self.assertIn('/hardware/module_confirm_success', sent_messages[0])
        self.assertIn('/hardware/web_module_cmd', sent_messages[1])
        self.assertIn('"op": "advertise"', sent_messages[2])
        self.assertIn('"op": "publish"', sent_messages[3])


if __name__ == "__main__":
    unittest.main()
