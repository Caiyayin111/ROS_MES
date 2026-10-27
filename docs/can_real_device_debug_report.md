# ROS_MES 真机 CAN 联调问题记录与修复方案

> 测试环境：Ubuntu 20.04 / ROS Noetic / ZLG USBCAN-II (libusbcan) / 真机 3 轴（轴1旋转、轴2摆动、轴3伸缩）

---

## 一、问题总览

| # | 问题分类 | 严重度 | 影响范围 | 状态 |
|---|---|---|---|---|
| 1 | Udev 权限不足 | 高 | USBCAN 打不开 | ✅ 已修复 |
| 2 | InitCAN 首次失败 | 高 | CAN 总线无法初始化 | ✅ 已修复 |
| 3 | USB 端口卡死 | 高 | 整个 CAN 链路断 | ✅ 已规避 |
| 4 | hardware_node 用 socketcan，ZLG 适配器不支持 | 高 | 核心链路不可用 | ✅ 已修复 |
| 5 | 上位机不主动回零，运动指令被接受但不执行 | 高 | 真机"死"状态 | ✅ 已规避 |
| 6 | `hardware_node.py` 解析真机短帧时越界崩溃 | 高 | 收到第一帧反馈即 crash | ✅ 已修复 |
| 7 | catkin 包装脚本导致 `import zlg_bus` 失败 | 中 | ZLG 后端无法加载 | ✅ 已修复 |
| 8 | 真机急停指令会锁死系统，只能重新上电 | 高 | 软件无解 | ⚠️ 物理硬限 |
| 9 | 真机连续发指令会进入"忙"状态锁存 | 中 | 多指令场景不可靠 | ⚠️ 物理硬限 |
| 10 | rosbridge_websocket 在 launch 中未启动 | 中 | API/WebSocket 全断 | 🔍 待查 |

---

## 二、各问题详述

### 问题 1：Udev 权限不足

**现象**：`VCI_OpenDevice(4, 0, 0)` 返回 `0`，设备无法打开。

**原因**：ZLG USBCAN-II 以 `root` 权限创建 `/dev/bus/usb/xxx`，普通用户无读权限。

**修复**：写入 udev 规则 `/etc/udev/rules.d/50-usbcan.rules`：
```
SUBSYSTEM=="usb", ATTR{idVendor}=="0471", ATTR{idProduct}=="1200", MODE="0666"
```
然后 `sudo udevadm control --reload-rules && sudo udevadm trigger`。

---

### 问题 2：InitCAN 首次调用失败

**现象**：`VCI_InitCAN` 第一次返回 `0`（失败），第二次返回 `1`（成功）。

**原因**：ZLG 官方驱动对 `USBCAN-II` 固件的初始化时序有特殊要求——首次调用可能失败。

**修复**：连续调用两次 InitCAN：
```python
lib.VCI_InitCAN(4, 0, 0, ctypes.byref(cfg))  # 第一次忽略返回
r = lib.VCI_InitCAN(4, 0, 0, ctypes.byref(cfg))  # 第二次取返回值
```
**文件**：`zlg_bus.py` / `scripts/zlg_bus.py`

---

### 问题 3：USB 端口卡死 (err=-110)

**现象**：`dmesg` 持续输出：
```
usb 2-2-port1: cannot reset (err = -110)
usb 2-2-port1: Cannot enable. Maybe the USB cable is bad?
```
`VCI_OpenDevice` / `InitCAN` / `StartCAN` 全部返回 0。

**原因**：ZLG 驱动使用 libusb 与硬件通信，如果进程没有正常 `VCI_CloseDevice`，USB 内核层会残留一个 half-open 的端口状态。

**规避**：
1. 确保每次退出都调用 `VCI_ResetCAN` + `VCI_CloseDevice`
2. 如果已卡死，在**物理宿主机**上拔插适配器（VMware 虚拟机内插拔无效）
3. 可用 `usbreset 0471:1200` 做软件复位（需 root）

---

### 问题 4：hardware_node 默认使用 socketcan，ZLG 适配器不支持

**现象**：`can.interface.Bus(channel="can0", interface="socketcan")` 需要内核 `SocketCAN` 驱动加载的网卡，ZLG USBCAN-II 没有对应的 `ip link set can0 type can bitrate 500000`。

**修复**：
1. 新建 `scripts/zlg_bus.py`，用 `ctypes` 封装 ZLG `libusbcan.so` 的 `VCI_OpenDevice / InitCAN / StartCAN / Transmit / Receive / ResetCAN / CloseDevice`，提供与 `python-can` 的 `Bus` 类兼容的 `send()/recv()/shutdown()` 接口
2. 修改 `hardware_node.py` 的 `_connect_bus()` 方法，通过 `CAN_INTERFACE` 环境变量选择后端：
```python
def _connect_bus(self):
    iface = os.environ.get('CAN_INTERFACE', 'socketcan').lower()
    if iface == 'zlg':
        from zlg_bus import ZLGBus
        return ZLGBus(channel=self.channel, bitrate=self.bitrate)
    return can.interface.Bus(
        channel=self.channel, interface="socketcan", bitrate=self.bitrate,
        can_filters=self._make_filters())
```
3. `rob_arm.env` 增加：
```env
CAN_INTERFACE=zlg
CAN_CHANNEL=0
CAN_BITRATE=500000
```

**新增文件**：`scripts/zlg_bus.py`
**修改文件**：`scripts/hardware_node.py` L230-L239、`rob_arm.env` L25-L28

---

### 问题 5：真机上电后必须先发回零

**现象**：真机刚上电时，直接发运动指令（相对增量）会返回 `CmdAck result=0`（接受），但电机**不动作**，反馈角度恒定 `0.00°`。

**原因**：STM32 固件需要先建立基准（回零）才能让轴使能运动。没回零前固件接受指令但不下发电机。

**修复**（两种任选一种）：
- **策略 A**：程序启动后自动发回零帧
- **策略 B**：手动发 `MSG=4 data=0x01` 回零，固件可能返回 `result=3`（越界）但电机已经被使能——实测这时候发运动帧就能动

⚠️ 注意：回零帧的 `result=3`（参数越界）是固件实现，实际电机已经准备好。**这与固件文档不一致**，需要在代码里加注释说明。

---

### 问题 6：hardware_node 解析真机短帧越界崩溃

**现象**：运行日志：
```
❌ CAN 接收异常，准备重连：index out of range
✅ CAN 总线已连接：0   ← 自动重连后 OK
```
每次收到真机反馈帧就 crash 一次。

**根因**：真机某些反馈帧 DLC 小于代码期望（例如 `GRP=3 DIR=EVENT MSG=1` 停止通知真机发 `DLC=2`，但代码直接 `data[1]` 无 `len(data)>=2` 检查）。

**修复**：为所有直接 `data[x]` 访问增加长度前置检查：

| 分支 | 原代码 | 修复 |
|---|---|---|
| `GRP=0 MSG=1 看门狗告警` | `data[0], data[1]` 无检查 | `len(data)>=2` 前置 |
| `GRP=0 MSG=2 节点故障` | `data[0..3]` 无检查 | `len(data)>=4` 前置 |
| `GRP=0 MSG=3 启动握手` | `data[0,1,2,3]` 无检查 | `len(data)>=4` 前置 |
| `GRP=2 MSG=4 位置打包` | `data[0], data[1]` 无检查 | `len(data)>=2` 前置 |
| `GRP=3 MSG=1 停止通知` | `data[0], data[1]` 无检查 | `len(data)>=2` 前置 |
| `GRP=3 MSG=3 回零状态` | `data[0], data[1]` 无检查 | `len(data)>=2` 前置 |
| `GRP=3 MSG=4 限位事件` | `data[0], data[1]` 无检查 | `len(data)>=2` 前置 |

**修改文件**：`scripts/hardware_node.py` L560-L579, L603-L606, L646-L649, L669-L672, L683-L686

---

### 问题 7：catkin 包装脚本导致 `import zlg_bus` 失败

**现象**：ROS 节点启动时报：
```
import name 'ZLGBus' from 'zlg_bus' (/home/yang/workspaces/robot_ws/devel/lib/robot_control_backend/zlg_bus.py)
```

**原因**：catkin 会为每个 `.py` 脚本生成一个**包装脚本**（`devel/lib/.../xxx.py`），这个包装脚本用 `exec()` 执行源代码但**清空了 `__name__`**。导致 `from zlg_bus import ZLGBus` 找不到真正的模块文件（包装脚本本身不可 `import`）。

**修复**：在 `_connect_bus()` 里用 `sys.path.insert(0, __file__所在目录)` 把**源脚本目录**加入搜索路径：
```python
if iface == 'zlg':
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from zlg_bus import ZLGBus
    return ZLGBus(channel=self.channel, bitrate=self.bitrate)
```
**文件**：`scripts/hardware_node.py` L233-L237

另外还需要在 `CMakeLists.txt` 里把 `scripts/zlg_bus.py` 加入 `catkin_install_python`，让 catkin 生成包装脚本（否则这个文件根本没被部署）。

**修改文件**：`CMakeLists.txt` L219

---

### 问题 8：真机急停锁死（物理硬限）

**现象**：
1. 发 `CAN ID=0x00000000 data=0x01`（全局急停）后，**所有轴锁死**
2. 紧接着发 `CAN ID=0x00000004 data=0x01`（急停解除）无效
3. 只有**重新上电**才能恢复

**根因**：STM32 固件把急停做了**锁存**（latch），固件层无法用软件解除。

**规避**：
- ❌ **不要在调试阶段发急停指令**
- ✅ 改用单轴停止帧 `MSG=3 data=0x00`（只停某轴，不锁系统）

**关键代码**：`scripts/hardware_node.py` L290-L307（系统指令分支 `device_id==0`）

---

### 问题 9：真机连续运动指令进入"忙"状态锁存（物理硬限）

**现象**：
- 第一次发运动指令 `CmdAck result=0 (OK)`，电机动作
- 紧接着发第二条运动指令 `CmdAck result=1 (忙)`，拒绝
- 等 2-3 秒再发可能 OK，也可能继续忙
- 发停止帧 `MSG=3 data=0x00` 能让真机响应，但紧接着的运动指令仍可能是忙

**根因**：STM32 固件内部有一个"忙锁"状态机，需要**足够的间隔时间**让固件把运动队列清空。

**规避策略**：

| 方案 | 间隔 | 说明 |
|---|---|---|
| 保守方案 | 3-5 秒/帧 | 每帧运动指令之间等 3-5 秒，稳定可靠 |
| 激进方案 | 1.5-2 秒/帧 | 真机固件偶尔能接受，但易触发忙锁 |
| 最优方案 | 等到位通知 `GRP=3 MSG=0 DLC=8` | 真机主动上报到位后再发下一条 |

当前硬件节点配置：
```env
CAN_TX_WAIT_TIMEOUT=5.0      # 发送后等待反馈 5s
CAN_CHUNK_PERIOD=0.05        # 分帧周期 50ms
CAN_MAX_DELTA_CENTIDEG=30000 # 单帧最大增量 300°
```

⚠️ **摆动轴(axis=2)** 比伸缩轴(axis=3)更易触发忙锁，原因是摆动轴固件存在硬件级问题（之前直接 CAN 测试也表现出偶发忙锁）。

---

### 问题 10：rosbridge_websocket 在 launch 中未启动（待查）

**现象**：`rosnode list` 没有 `/rosbridge_websocket` 节点，`9010` 端口无监听。

**影响**：
- 后端 `/api/control/finetuning` 超时（通过 rosbridge publish）
- 前端 WebSocket 订阅反馈不可用
- rosbridge 消息传输层完全断

**临时修复**：单独启动：
```bash
roslaunch rosbridge_server rosbridge_websocket.launch
```

**根因待查**：可能是 launch 文件里依赖顺序（例如需要 roscore 先起）或者 ROS 环境路径问题。单独启动正常工作，说明不是代码 bug。

---

## 三、关键文件索引

| 文件 | 作用 | 本次改动 |
|---|---|---|
| `scripts/zlg_bus.py` | ZLG USBCAN-II → python-can 兼容层 | **新建** |
| `scripts/hardware_node.py` | CAN 桥接节点 | 修改 `_connect_bus()` + 修 `_parse_can_frame` 越界 |
| `rob_arm.env` | 运行时环境变量 | `CAN_INTERFACE=zlg`, `CAN_CHANNEL=0`, `CAN_HOME_SUPPORTED=true` |
| `CMakeLists.txt` | catkin 编译 | 加入 `zlg_bus.py` 到 `catkin_install_python` |
| `/etc/udev/rules.d/50-usbcan.rules` | USB 权限 | **新建**（需手动创建） |
| `/home/yang/.local/lib/usbcan/libusbcan.so` | ZLG 官方驱动 | 手动安装 |

---

## 四、一键启动脚本

```bash
#!/bin/bash
# 真机联调一键启动
set -e
WS=/home/yang/workspaces/robot_ws
source $WS/devel/setup.bash

# 0. 确保设备就绪
echo "🔍 检查 ZLG 设备..."
lsusb | grep -q "0471:1200" || { echo "❌ ZLG 设备未连接！"; exit 1; }
echo "✅ ZLG 就绪"

# 1. 启动 roscore
roscore &
ROSCORE_PID=$!
sleep 3

# 2. 启动 ROS 完整栈（含 hardware_node/stm32_bridge）
roslaunch robot_control_backend ros_robot.launch &
LAUNCH_PID=$!
sleep 12

# 3. 启动 rosbridge_websocket（传输层）
roslaunch rosbridge_server rosbridge_websocket.launch &
RB_PID=$!
sleep 5

# 4. 启动后端 API
cd $WS/src/ROS_MES/mes_web/ros_mes_hou
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
API_PID=$!
sleep 4

# 5. 启动前端
cd $WS/src/ROS_MES/mes_web/ros_mes_front
npm run dev &
FE_PID=$!
sleep 3

echo ""
echo "================================"
echo "  全栈启动完成"
echo "  roscore        : PID $ROSCORE_PID"
echo "  roslaunch      : PID $LAUNCH_PID"
echo "  rosbridge      : PID $RB_PID"
echo "  后端 API       : PID $API_PID → http://localhost:8000"
echo "  前端 Vite      : PID $FE_PID  → http://localhost:5173"
echo "================================"
echo ""
echo "📋 真机上电后执行（解锁状态）："
echo "  rostopic pub -1 /arm/cmd_vel robot_control_backend/IntCmd \\"
echo "    \"{module_id:1, device_id:0, position:[4]}\"  # 急停解除"
echo "  rostopic pub -1 /arm/cmd_vel robot_control_backend/IntCmd \\"
echo "    \"{module_id:1, device_id:3, position:[200]}\"  # 伸缩轴 +2mm"
```

---

## 五、真机 CAN 协议速查

### CAN ID 位域（29 位扩展帧）

```
PRI(3) | GRP(4) | SEG(4) | NODE(5) | AXIS(5) | DIR(2) | MSG(4) | RSV(2)
```

### 常用 ID 构造

| 帧类型 | ID 计算 | 十六进制 |
|---|---|---|
| 急停解除 | `PRI=0, GRP=0, DIR=DOWN, MSG=1` | `0x00000004` |
| 回零(axis=n) | `PRI=1, GRP=1, NODE=1, AXIS=n, MSG=4` | `0x04400{4+n}` |
| 运动(axis=n, +delta) | `PRI=1, GRP=1, NODE=1, AXIS=n, MSG=1` | `0x04400{4+n}` |
| 停止(axis=n) | `PRI=1, GRP=1, NODE=1, AXIS=n, MSG=3` | `0x04400{4+n}` |
| CmdAck 反馈(axis=n) | `GRP=2, MSG=5, DIR=PERIODIC` | `0x04800{4+n}` |

### 载荷格式

| 帧类型 | Payload |
|---|---|
| 运动指令 | `struct.pack('>hH', delta_centideg, speed)` —— int16 增量 + uint16 速度，大端 |
| 回零 | `[home_mode]` 单字节，`0x01`=回零，`0x02`=清除基准 |
| 停止 | `[0x00]` 单字节 |
| CmdAck 反馈 | `[ack_msg, result, detail]` 3 字节，`result: 0=OK 1=忙 2=基准无效 3=越界 4=故障 5=急停锁存` |
| 角度反馈 | `struct.unpack('>i', data[0:4])[0] / 100.0` —— int32 大端，单位 0.01° 或 0.01mm |

---

*文档生成时间：2026-10-25*
