# app/core/config.py
import os
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _find_project_root(start_dir: str) -> str:
    """从 start_dir 逐级向上查找仓库根目录。

    判定标记：目录下存在 robot_control_backend（ROS 端包）或 .git。
    这样前后端文件夹无论放在仓库哪一层，都无需修改层级数字。
    """
    current = os.path.abspath(start_dir)
    while True:
        if os.path.isdir(os.path.join(current, "robot_control_backend")) or os.path.isdir(
            os.path.join(current, ".git")
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return current
        current = parent


class Settings:
    PROJECT_NAME: str = "ROS MES Backend"
    API_STR: str = os.getenv("API_STR", "/api")
    # 后端根目录（ros_mes_hou）：config.py 位于 app/core/ 下，向上 3 级
    BACKEND_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # 仓库根目录：向上自动查找，也可用环境变量 PROJECT_ROOT 直接指定
    PROJECT_ROOT: str = os.getenv("PROJECT_ROOT") or _find_project_root(BACKEND_ROOT)
    # 数据库文件位于仓库根目录
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(PROJECT_ROOT, 'ros_database.db')}")
    # ROS 端脚本目录（robot_control_backend/scripts），供按需拉起底层脚本；
    # ROS2 部署或脚本换位置时，用环境变量 ROS_SCRIPTS_DIR 覆盖即可
    ROS_SCRIPTS_DIR: str = os.getenv(
        "ROS_SCRIPTS_DIR", os.path.join(PROJECT_ROOT, "robot_control_backend", "scripts")
    )
    # rosbridge WebSocket 地址（ROS1/ROS2 通用；ROS_VERSION 环境变量见 app/ros/profile.py）
    ROSBRIDGE_URL: str = os.getenv("ROSBRIDGE_URL", "ws://localhost:9010")
    # 模块确认会话式握手的回执等待上限。
    # ROS 端 V2.1：等宣告窗口 30s（含操作员上电）+ 确认重试，后端必须 ≥35s，否则必然 502
    MODULE_CONFIRM_ACK_WAIT_S: float = float(os.getenv("ROS_MODULE_CONFIRM_ACK_WAIT_S", "35.0"))
    BACKEND_CORS_ORIGINS: List[str] = [
        origin.strip()
        for origin in os.getenv("BACKEND_CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ]


settings = Settings()
