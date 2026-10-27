# ROS MES 全流程测试报告（模拟下位机）

- 测试日期：2026-10-25
- 测试范围：ROS 1 Noetic 节点层、rosbridge、FastAPI 后端、Vue3 前端、点云/选点/逆解链路
- 测试方式：`sim_lower_machine.py` 在 ROS 话题层模拟 STM32 + CAN 下位机，不接真实硬件

## 1. 总体结论

在纯 ROS 仿真环境下，前后端与 ROS 全链路已打通并可稳定运行：

- 51 个后端单元测试全部通过
- 端到端 API 回归 21/21 通过
- 前端登录、看板、模块管理、微调页、图纸管理、ROS 测试页均可正常操作
- 图纸下发可完整触发：点云处理 → 模块切分 → 最优点选取 → 逆解 → 15 轴增量下发 → 仿真下位机执行 → 反馈回传 → 前端 WebSocket 实时刷新
- 急停/解除、超限丢弃、模块会话握手、压力 5s 采样均按预期工作

未覆盖项：真实 CAN 硬件状态接口、串口测试、IMU 姿态精度、前端生产构建（详见第 6 节）。

## 2. 测试环境

| 项目 | 版本/配置 |
| --- | --- |
| 操作系统 | Ubuntu 20.04.6 LTS |
| ROS | ROS 1 Noetic（`/opt/ros/noetic`） |
| Python | 3.8.10（系统解释器，已具备 FastAPI/SQLAlchemy/websockets） |
| Node / npm | v20.20.2 / 10.8.2 |
| ROS 工作空间 | `/home/yang/workspaces/robot_ws` |
| 项目目录 | `/home/yang/workspaces/robot_ws/src/ROS_MES` |
| ROS 消息 | 21 个 `.msg`，已全部编译 |
| ROS 节点脚本 | 19 个 `.py` |
| 数据库 | `ROS_MES/ros_database.db`（绝对路径写入 `rob_arm.env`） |
| 端口 | ROS Master 11311、rosbridge 9010、点云视图 5000、后端 8000、前端 5173 |

## 3. 数据流

```text
浏览器(5173) → FastAPI(8000) → rosbridge(9010) → ROS Master(11311)
                                  ├─ /control/*            控制/微调/急停
                                  ├─ /hardware/*           编码器/压力/IMU 反馈
                                  └─ /frontend_pointcloud_topic  图纸路径
图纸链路：
图纸 → data_process_node(5000 三视图) → /module_arm_task → calculation_node
     → /arm_alpha_beta → kinematics_node → /control/kinematics_*_sequenced
     → rotation/swing/telescopic → /hardware/*_output → feedback_node
     → /arm/cmd_vel → sim_lower_machine → /hardware/can/*_raw → feedback_node
     → /hardware/*_feedback → rosbridge → 后端 WebSocket → 前端
```

## 4. 完整测试表

### A. 构建与环境

| # | 测试项 | 操作 | 预期 | 实测 | 结论 |
| --- | --- | --- | --- | --- | --- |
| A1 | ROS Noetic | `rosversion -d` | `noetic` | `noetic` | 通过 |
| A2 | catkin 编译 | `catkin_make -j2` | 编译成功 | 成功，21 个 msg 生成 | 通过 |
| A3 | 节点可执行包装 | `devel/lib/robot_control_backend` | 全部脚本有包装 | 19 个脚本可 `rosrun/roslaunch` | 通过 |
| A4 | rosbridge 包装 | `rosbridge_wrapper.py` | 已安装 | 原 CMake 未安装，已补 | 修复后通过 |
| A5 | 数据库路径 | `roslaunch` 下节点 cwd | 读取仓库 DB | 原 cwd=`~/.ros` 读错库，已改绝对路径 | 修复后通过 |
| A6 | Python 3.8 兼容 | 导入后端子路由 | 可导入 | `tuple[...]` 语法报错，已加 future annotations | 修复后通过 |
| A7 | python-can / CAN | `import can` / `can0` | 真实硬件才需要 | 未安装、无 can0 | 不适用（仿真绕过） |

### B. ROS 节点与话题层

| # | 测试项 | 操作/输入 | 预期 | 实测 | 结论 |
| --- | --- | --- | --- | --- | --- |
| B1 | 节点启动 | `sim_ros_robot.launch` | 关键节点全部在线 | 17 个业务节点 + rosapi/rosbridge 在线 | 通过 |
| B2 | 15 轴反馈 | 观察 `/hardware/can/*_raw` | 2Hz 原始位置 | 旋转/摆动 0.01°、伸缩 0.01mm 周期上报 | 通过 |
| B3 | 单位换算 | `/hardware/rotation_feedback` | 原始 0.01° 转 ° | 示例 -18.5°、摆动 5.0° | 通过 |
| B4 | 旋转微调 | +3° → 轴 1 | 下发 +300 原始单位 | 仿真位置 +300，反馈同步 | 通过 |
| B5 | 摆动微调 | +2° → 轴 2 | 下发 +200 | 位置变量 5°→7° | 通过 |
| B6 | 伸缩微调 | +5mm → 轴 3 | 下发 +500 | 位置 +5mm | 通过 |
| B7 | 摆角超限 | +20°，限幅 0~15° | 丢弃并告警 | 日志：27° 超出 [0,15]，指令已丢弃 | 通过 |
| B8 | 全局急停 | `/control/softstop` | 下位机锁存 0x01 | 仿真日志：收到 0x01 并锁存 | 通过 |
| B9 | 急停解除 | `/control/softstop_release` | 下发 0x04 | 仿真日志：收到 0x04 解锁 | 通过 |
| B10 | 急停期间轴指令 | 锁存后下发 +1° | 拒绝并回 CmdAck | `/hardware/cmd_ack`：result=5 | 通过（ROS 层） |
| B11 | 模块会话握手 | `/control/module_cmd` 工位 1 | 宣告→分配→确认→成功 | 0.6s 完成，可重复握手 | 通过 |
| B12 | 压力采样 | `/control/sensor_cmd` 臂 1 | 启停 + 5s 均值 | `/hardware/pressure_avg` 输出 11.14 | 通过 |
| B13 | IMU 链路 | `/hardware/imu_angles` | 稳定输出姿态 | 水平静止 0.0°/0.0°（仅链路验证） | 通过（精度不适用） |
| B14 | 图纸全链路 | 下发图纸 ID=1 | 三视图 + 5 臂逆解 | 切 1 模块，节点 B 110ms 输出 5 臂增量 | 通过 |

### C. 后端 API

| # | 测试项 | 操作/输入 | 预期 | 实测 | 结论 |
| --- | --- | --- | --- | --- | --- |
| C1 | 单元测试 | `unittest discover` | 全部通过 | 51/51 OK | 通过 |
| C2 | 应用导入 | `from app.main import app` | 正常注册路由 | 89 条路由 | 通过 |
| C3 | 登录成功 | admin / 123456 | 200 + token | 200 | 通过 |
| C4 | 登录失败 | 错误密码 | 401 | 401 | 通过 |
| C5 | 未授权读取 | 无 token 请求 `/api/device/` | 401 | 200 | 风险：该接口未挂鉴权 |
| C6 | ROS 状态 | `/api/ros/get_ros_status` | `ros_ready=true` | true | 通过 |
| C7 | 基础数据 | 设备/单元/传感器/图纸/看板 | 200 | 全部 200 | 通过 |
| C8 | 微调下发 | 三轴正增量 | 200 + 下发 | 全部 200 | 通过 |
| C9 | 非法轴号 | `device_id=16` | 422 | 原 500，修复后 422 且不落库 | 修复后通过 |
| C10 | 模块确认缺工位 | 不传 station | 422 | 422 | 通过 |
| C11 | 模块确认握手 | 工位 1，连续两次 | 200，秒级 | 0.6s，两次均成功 | 通过 |
| C12 | 急停/解除 | 两个接口 | 200 success | 200 | 通过 |
| C13 | 图纸导入 | 导入 800 点样例 | 返回 drawingId | 成功，Drawing_ID=1 | 通过 |
| C14 | 图纸下发 | `/api/coordination/send` | 200 + 视图就绪 | 首次 3s、缓存 0.2s | 通过 |
| C15 | 三视图代理 | top/front/side | 200 PNG | 16KB 级 PNG | 通过（已绕开 HTTP_PROXY） |
| C16 | ROS 测试下发 | `/api/ros/send_ros?msg=...` | 发布 `/web_cmd` | 200，消息已发送 | 通过 |
| C17 | WebSocket 反馈 | `/api/control/feedback/ws` | 持续推送 | 6 条，rotation/swing 实时 | 通过 |
| C18 | 串口测试 | `/api/control/serial_test` | 无串口时优雅失败 | 200，提示 `/dev/ttyUSB0` 不存在 | 通过（降级） |
| C19 | 硬件实时状态 | `/api/control/hardware/realtime` | 返回硬件状态 | 500，缺 python-can/CAN | 不适用（需真实硬件） |

### D. 前端 UI（真实浏览器）

| # | 测试项 | 操作 | 预期 | 实测 | 结论 |
| --- | --- | --- | --- | --- | --- |
| D1 | 依赖安装 | `npm install` | 成功 | 149 包 | 通过 |
| D2 | 开发服务 | `npm run dev` | 5173 可访问 | 正常 | 通过 |
| D3 | 登录流程 | admin / 123456 | 进入看板 | 成功 | 通过 |
| D4 | 看板 | 打开 `/Dashboard` | 卡片有数据 | 设备/任务/故障/在线数均显示 | 通过 |
| D5 | 模块管理 | 打开 `/moduleManagement` | 坐标矩阵可用 | 正常 | 通过 |
| D6 | 锁定并下发 | X=1,Y=1,工位1 | 进入微调页 | 跳转 `FineTuningPage?module_id=17...` | 通过 |
| D7 | 微调页实时数据 | 观察 5s | 压力/角度刷新 | 压力 0.93→1.38，角度显示 2.93/5/10 | 通过 |
| D8 | 图纸管理 | 打开 `/DrawingManage` | 显示导入图纸 | “仿真测试图纸” ID=1 | 通过 |
| D9 | ROS 测试页 | 发送 `ui-smoke` | 提示已发送 | code=200 | 通过 |
| D10 | 控制台错误 | 检查 console | 无红色错误 | 仅 1 条 Vue reactive 警告 | 通过 |
| D11 | 生产构建 | `npm run build` | 成功 | 失败：多处 TS 类型错误（见 6.4） | 不通过（开发模式不受影响） |

### E. 端到端场景

| # | 场景 | 结果 |
| --- | --- | --- |
| E1 | 上位机下发图纸 → 自动完成点云切分/选点/逆解/15 轴下发 | 通过 |
| E2 | 微调 → ROS 三轴节点 → 仿真下位机 → 反馈 → 前端 WS | 通过 |
| E3 | 模块锁定 → 会话握手 → 微调页 | 通过 |
| E4 | 急停 → 轴指令拒绝 → 解除 | 通过（拒绝结果经 CmdAck 异步回传） |
| E5 | 压力触发 → 5s 窗口 → 平均值 | 通过 |

## 5. 本次发现并修复的问题

| # | 文件 | 问题 | 修复 |
| --- | --- | --- | --- |
| 1 | `mes_web/ros_mes_hou/app/api/endpoints/coordination.py` | Python 3.8 下 `tuple[bytes, str]` 导致模块导入失败 | 增加 `from __future__ import annotations` |
| 2 | 同上 | 点云视图请求走系统 HTTP_PROXY，5s 超时误判失败 | 使用无代理 `ProxyHandler({})` opener |
| 3 | `mes_web/ros_mes_hou/app/ros/base.py` | 子进程写死 `python`，Linux 无该别名导致 500 | 默认使用 `sys.executable`，可用 `ROS_PYTHON_CMD` 覆盖 |
| 4 | `mes_web/ros_mes_hou/app/api/endpoints/control.py` | 非法轴号先落库再抛 500 | 先校验后落库，非法参数返回 422 |
| 5 | `robot_control_backend/scripts/data_process_node.py` | roslaunch 把 cwd 切到 `~/.ros`，相对图纸路径找不到 | 相对路径按 scripts 目录解析为绝对路径 |
| 6 | `robot_control_backend/rob_arm.env` | 节点读到 `~/.ros/ros_database.db` 空库 | `DB_PATH` 改为仓库内绝对路径 |
| 7 | `robot_control_backend/CMakeLists.txt` | `rosbridge_wrapper.py`、仿真节点未安装可执行包装 | 加入 `catkin_install_python` |
| 8 | 新增 `scripts/sim_lower_machine.py`、`launch/sim_ros_robot.launch` | 无 CAN 硬件时无法联调 | ROS 话题层模拟 15 轴/压力/IMU/握手，替代 `hardware_node` |

## 6. 遗留问题与未覆盖项

1. **硬件实时状态**：`/api/control/hardware/realtime` 仍需要真实 CAN；仿真下返回 500。上真机前需安装 `python-can` 并启动 `can0`。
2. **串口测试**：仅验证了无设备时的降级提示，真实串口需接 `/dev/ttyUSB0`。
3. **IMU 姿态精度**：仿真器只发水平静止数据（0°/0°），只验证了链路；Mahony 滤波精度需真实 IMU 标定。
4. **前端生产构建**：`npm run build` 失败，集中在 `AddItem.vue`、`MainPage.vue`、`UserManagement.vue`、`router/index.ts` 的 TS 类型问题；Vite 开发模式不受影响。本次未改动，避免扩大范围。
5. **部分只读接口无鉴权**：如 `/api/device/` 无 token 返回 200，建议后续补 `get_current_user`。
6. **微调接口为异步下发**：急停锁存期间接口仍返回“下发成功”，实际底层通过 `CmdAck.result=5` 拒绝；如需前端感知，应增加 ack 回查。
7. **本机 HTTP_PROXY**：命令行 curl/requests 访问 127.0.0.1 需加 `NO_PROXY=127.0.0.1,localhost`；后端点云视图已代码内绕开。

## 7. 启动命令

### 7.1 首次编译（只需一次）

```bash
cd /home/yang/workspaces/robot_ws
source /opt/ros/noetic/setup.bash
catkin_make -j2
```

### 7.2 终端 1：ROS（仿真下位机）

```bash
source /opt/ros/noetic/setup.bash
source /home/yang/workspaces/robot_ws/devel/setup.bash
cd /home/yang/workspaces/robot_ws/src/ROS_MES
roslaunch robot_control_backend sim_ros_robot.launch
```

启动后端口：rosbridge 9010、点云视图 5000、ROS Master 11311。

### 7.3 终端 2：后端

```bash
source /opt/ros/noetic/setup.bash
cd /home/yang/workspaces/robot_ws/src/ROS_MES/mes_web/ros_mes_hou
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

接口文档：`http://127.0.0.1:8000/docs`

### 7.4 终端 3：前端

```bash
cd /home/yang/workspaces/robot_ws/src/ROS_MES/mes_web/ros_mes_front
npm install            # 首次
VITE_DEBUG_NO_ROS=false npm run dev -- --host 0.0.0.0 --port 5173
```

浏览器打开 `http://localhost:5173`，账号 `admin`，密码 `123456`。

> 说明：`.env.development` 中 `VITE_DEBUG_NO_ROS` 默认为 `true`（前端走 mock）。联调真实后端时必须像上面一样覆盖为 `false`，或直接把该文件改为 `false`。

### 7.5 真机模式（有 CAN 硬件）

```bash
sudo ip link set can0 up type can bitrate 1000000
pip3 install python-can
source /opt/ros/noetic/setup.bash
source /home/yang/workspaces/robot_ws/devel/setup.bash
cd /home/yang/workspaces/robot_ws/src/ROS_MES
roslaunch robot_control_backend ros_robot.launch
```

### 7.6 停止

```bash
rosnode kill -a
pkill -f "uvicorn app.main:app"
pkill -f "vite"
pkill -f "rosmaster"
```

### 7.7 日志

| 服务 | 日志 |
| --- | --- |
| ROS 节点 | `~/.ros/log/<最新会话>/*.log` |
| 后端（本次测试） | `/tmp/ros_mes_backend.log` |
| 前端（本次测试） | `/tmp/ros_mes_front.log` |
| roslaunch 控制台 | `/tmp/ros_mes_sim.log` |

## 8. 测试资产

- 下位机模拟节点：`robot_control_backend/scripts/sim_lower_machine.py`
- 仿真启动文件：`robot_control_backend/launch/sim_ros_robot.launch`
- 测试图纸：已通过 `/api/drawing/import` 导入 `Drawing_ID=1`（“仿真测试图纸”，800 点样例）
- 临时回归脚本位于 `/tmp/ros_mes_api_smoke.py`、`/tmp/ros_mes_api_smoke2.py`，未写入仓库
