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


class ModuleConfirmDispatchTest(unittest.TestCase):
    def test_build_module_confirm_payload_matches_topic_contract(self):
        # ROS 端 V2.1 握手契约：position=[100, station]，模块号=SEG=工位-1
        payload = build_module_confirm_publish_payload(5)

        self.assertEqual(payload["topic"], "/control/module_cmd")
        self.assertEqual(payload["message_type"], "robot_control_backend/IntCmd")
        self.assertEqual(payload["message"]["module_id"], 5)
        self.assertEqual(payload["message"]["device_id"], 0)
        self.assertEqual(payload["message"]["position"], [100, 6])
        self.assertIn("stamp", payload["message"]["header"])

    def test_build_module_confirm_payload_accepts_explicit_station(self):
        payload = build_module_confirm_publish_payload(5, station=3)

        self.assertEqual(payload["message"]["module_id"], 5)
        self.assertEqual(payload["message"]["position"], [100, 3])

    def test_build_module_confirm_payload_rejects_out_of_range_module_id(self):
        # module_id 18 → 推导工位 19 超出 1~15，必须在构造期就报错而不是等 ROS 端拒绝
        with self.assertRaises(RosbridgeError):
            build_module_confirm_publish_payload(18)

    def test_lock_module_dispatches_module_confirm_topic_before_returning_success(self):
        dispatcher = DummyDispatcher()

        response = lock_and_dispatch_module(
            {"x": 1, "y": 2, "module_id": 5, "device_id": 3, "position": 0},
            dispatcher=dispatcher,
        )

        self.assertEqual(response["code"], 200)
        self.assertEqual(response["data"]["module_id"], 5)
        self.assertEqual(response["dispatch"]["action"], "module_confirm")
        action, payload = dispatcher.calls[0]
        self.assertEqual(action, "module_confirm")
        self.assertEqual(payload["topic"], "/control/module_cmd")
        self.assertEqual(payload["message_type"], "robot_control_backend/IntCmd")
        self.assertEqual(payload["message"]["module_id"], 5)
        self.assertEqual(payload["message"]["device_id"], 0)
        self.assertEqual(payload["message"]["position"], [100, 6])
        self.assertEqual(payload["business"]["x"], 1)
        self.assertEqual(payload["business"]["y"], 2)
        self.assertEqual(payload["business"]["device_id"], 3)

    def test_lock_module_dispatches_with_explicit_station_from_request(self):
        dispatcher = DummyDispatcher()

        response = lock_and_dispatch_module(
            {"x": 1, "y": 2, "module_id": 5, "device_id": 3, "position": 0, "station": 2},
            dispatcher=dispatcher,
        )

        self.assertEqual(response["code"], 200)
        action, payload = dispatcher.calls[0]
        self.assertEqual(payload["message"]["position"], [100, 2])
        self.assertEqual(payload["business"]["station"], 2)

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
                    '"msg":{"module_id":5,"device_id":0,"position":[100]}}'
                )

        dispatcher = RosbridgeClient(
            ROS1_PROFILE, url="ws://test", timeout=0.1, module_confirm_timeout=0.1)
        payload = build_module_confirm_publish_payload(5)

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
                    '"msg":{"module_id":5,"device_id":0,"position":[1]}}'
                )

        dispatcher = RosbridgeClient(
            ROS1_PROFILE, url="ws://test", timeout=0.1, module_confirm_timeout=0.1)
        payload = build_module_confirm_publish_payload(5)

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
