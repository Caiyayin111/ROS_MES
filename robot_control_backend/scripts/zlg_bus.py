#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZLG USBCAN-II python-can 兼容 Bus 封装

用法（替代 socketcan）：
    from zlg_bus import ZLGBus
    bus = ZLGBus(channel=0, bitrate=500000)
    bus.send(can.Message(...))
    msg = bus.recv(timeout=0.5)
"""
import ctypes
import os
import time
from collections import deque

try:
    import can
    from can import Message
except ImportError:
    can = None
    Message = None

LIB_PATH = os.environ.get(
    "ZLG_LIBUSB_PATH",
    "/home/yang/.local/lib/usbcan/libusbcan.so",
)
DEV_TYPE = 4  # USBCAN-II


class _INIT_CONFIG(ctypes.Structure):
    _fields_ = [("AccCode", ctypes.c_uint32), ("AccMask", ctypes.c_uint32),
                ("Reserved", ctypes.c_uint32), ("Filter", ctypes.c_ubyte),
                ("Timing0", ctypes.c_ubyte), ("Timing1", ctypes.c_ubyte),
                ("Mode", ctypes.c_ubyte)]


class _CAN_OBJ(ctypes.Structure):
    _fields_ = [("ID", ctypes.c_uint32), ("TimeStamp", ctypes.c_uint32),
                ("TimeFlag", ctypes.c_ubyte), ("SendType", ctypes.c_ubyte),
                ("RemoteFlag", ctypes.c_ubyte), ("ExternFlag", ctypes.c_ubyte),
                ("DataLen", ctypes.c_ubyte), ("Data", ctypes.c_ubyte * 8),
                ("Reserved", ctypes.c_ubyte * 3)]


_BAUD_TIMING = {
    1000000: 0x1400,
    500000:  0x1C00,
    250000:  0x1C01,
    125000:  0x1C03,
    100000:  0x1C07,
    50000:   0x1C0F,
}


class ZLGBus:
    """ZLG USBCAN-II Bus，接口与 python-can Bus 兼容（send/recv/shutdown）"""

    def __init__(self, channel=0, bitrate=500000, **kwargs):
        self.channel = int(channel)
        self.bitrate = int(bitrate)
        self._lib = ctypes.CDLL(LIB_PATH)
        self._rx_queue = deque()
        self._open()

    def _open(self):
        if self._lib.VCI_OpenDevice(DEV_TYPE, 0, 0) != 1:
            raise RuntimeError("ZLG VCI_OpenDevice 失败")
        timing = _BAUD_TIMING.get(self.bitrate, 0x1C00)
        cfg = _INIT_CONFIG()
        cfg.AccCode, cfg.AccMask, cfg.Reserved = 0, 0xFFFFFFFF, 0
        cfg.Filter, cfg.Mode = 1, 0
        cfg.Timing0, cfg.Timing1 = timing & 0xFF, timing >> 8
        # 首次 InitCAN 在某些固件上会失败，重试一次
        self._lib.VCI_InitCAN(DEV_TYPE, 0, self.channel, ctypes.byref(cfg))
        if self._lib.VCI_InitCAN(DEV_TYPE, 0, self.channel, ctypes.byref(cfg)) != 1:
            raise RuntimeError("ZLG VCI_InitCAN 失败")
        if self._lib.VCI_StartCAN(DEV_TYPE, 0, self.channel) != 1:
            raise RuntimeError("ZLG VCI_StartCAN 失败")

    def send(self, msg, timeout=0.2):
        obj = _CAN_OBJ()
        obj.ID = msg.arbitration_id & 0x1FFFFFFF
        obj.SendType = 0
        obj.RemoteFlag = 0
        obj.ExternFlag = 1 if msg.is_extended_id else 0
        obj.DataLen = len(msg.data)
        for i, b in enumerate(msg.data):
            obj.Data[i] = b
        ret = self._lib.VCI_Transmit(DEV_TYPE, 0, self.channel, ctypes.byref(obj), 1)
        if ret != 1:
            raise can.CanError(f"ZLG VCI_Transmit 失败 ret={ret}")

    def recv(self, timeout=0.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 先取已缓存的
            if self._rx_queue:
                return self._rx_queue.popleft()
            n = self._lib.VCI_GetReceiveNum(DEV_TYPE, 0, self.channel)
            if n > 0:
                arr = (_CAN_OBJ * n)()
                got = self._lib.VCI_Receive(
                    DEV_TYPE, 0, self.channel, ctypes.byref(arr), n, 20)
                for i in range(got):
                    m = arr[i]
                    data = bytes(m.Data[:m.DataLen])
                    if Message is not None:
                        self._rx_queue.append(Message(
                            arbitration_id=m.ID & 0x1FFFFFFF,
                            data=data,
                            is_extended_id=bool(m.ExternFlag),
                            is_remote_frame=bool(m.RemoteFlag),
                        ))
                if self._rx_queue:
                    return self._rx_queue.popleft()
            time.sleep(0.01)
        return None

    def shutdown(self):
        try:
            self._lib.VCI_ResetCAN(DEV_TYPE, 0, self.channel)
        except Exception:
            pass
        try:
            self._lib.VCI_CloseDevice(DEV_TYPE, 0)
        except Exception:
            pass
