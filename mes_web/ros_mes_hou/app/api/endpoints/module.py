# app/api/endpoints/module.py

from fastapi import APIRouter, Body, HTTPException

from app.ros import get_ros_dispatcher
from app.ros.base import RosbridgeError
from app.ros.rosbridge import build_module_confirm_publish_payload

router = APIRouter()


def first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None


@router.post("/")
def lock_and_dispatch_module(payload: dict = Body(...), dispatcher=None):
    dispatcher = dispatcher or get_ros_dispatcher()
    x = first_not_none(
        payload.get("x"),
        payload.get("X"),
        payload.get("targetX"),
        payload.get("moduleX"),
        payload.get("col"),
    )

    y = first_not_none(
        payload.get("y"),
        payload.get("Y"),
        payload.get("targetY"),
        payload.get("moduleY"),
        payload.get("row"),
    )

    position = payload.get("position")
    module_id = payload.get("module_id")
    device_id = payload.get("device_id")
    # V2.1 握手契约（三轴版清单 §6/§7.2）：station=工位号 1~15，由操作员选择确认第几组，
    # 前端必须显式下发；module_id 为业务模块号，ROS 端原样回执
    station_raw = first_not_none(payload.get("station"), payload.get("Station"))
    if station_raw is None:
        raise HTTPException(
            status_code=422,
            detail="缺少工位号 station（1~15）：请选择确认第几组后随请求下发",
        )

    if isinstance(position, dict):
        x = first_not_none(x, position.get("x"))
        y = first_not_none(y, position.get("y"))

    if x is None or y is None:
        raise HTTPException(status_code=422, detail="missing x or y coordinate")

    try:
        x = int(x)
        y = int(y)
        module_id = int(module_id) if module_id is not None else x * 16 + y
        device_id = int(device_id) if device_id is not None else None
        station = int(station_raw) if station_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid module parameters")

    dispatch_payload = {
        "x": x,
        "y": y,
        "module_id": module_id,
        "device_id": device_id,
        "station": station,
        "position": position,
        "raw": payload,
    }

    try:
        ros_payload = build_module_confirm_publish_payload(module_id, station=station)
    except RosbridgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ros_payload["business"] = dispatch_payload

    try:
        dispatch_result = dispatcher.dispatch("module_confirm", ros_payload)
    except RosbridgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "code": 200,
        "message": "module locked and confirmation command dispatched",
        "data": dispatch_payload,
        "dispatch": dispatch_result,
    }
