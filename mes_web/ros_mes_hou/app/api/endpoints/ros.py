# app/api/endpoints/ros.py

from fastapi import APIRouter, HTTPException

from app.ros import get_ros_dispatcher
from app.ros.base import RosDispatchError

router = APIRouter()


@router.get("/send_ros")
def send_ros(msg: str):
    try:
        dispatch_result = get_ros_dispatcher().publish_web_cmd(
            "send_ros",
            {
                "msg": msg,
            },
        )
    except RosDispatchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "code": 200,
        "message": f"已发送到 ROS：{msg}",
        "data": dispatch_result,
    }


@router.get("/get_ros_status")
def get_ros_status():
    return {
        "code": 200,
        "message": "获取 ROS 状态成功",
        "data": get_ros_dispatcher().native_status,
    }
