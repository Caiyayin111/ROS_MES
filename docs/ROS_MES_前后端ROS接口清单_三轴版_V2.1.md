# ROS_MES 前后端 ↔ ROS 接口清单（三轴版 V2.1）

> **收录说明**（2026-10-25）：本文档由队友基于 ROS 源码现状整理（整理日期 2026-10-24），收录进仓库时仅把原文中的本机绝对路径链接改为仓库相对路径；**取代**《ROS_MES_前后端接口字段文档.md》中的 ROS 话题 / 消息 / device_id 编码部分（四轴布局已失效），该文档的业务接口与数据库部分仍有效。**团队已确认：本文档为 ROS 对接唯一权威。**
>
> **后端已处理项**（同日提交，勿重复修改）：7.1 模块确认回执超时已调至 35s（`ROS_MODULE_CONFIRM_ACK_WAIT_S`）；7.2 后端已按 §7.2 实现——`/module/` 请求体 **station 必传**（操作员选组，后端不做默认推导），module_id 保持业务模块号原样回执；7.3 的后端部分（校验 1~15、反馈标签表三轴化）已完成，且种子数据已补齐 5 臂（Unit_ID 128/160，延续每臂 +32 规律）；7.4 的 `/hardware/cmd_ack` 已加入反馈流，**枚举已从源码提取**：仅超位拒绝一种（`ack_msg=1, result=3, detail=1上限/2下限`），无成功回执；7.8 已解决（旧 rospy 路径迁入 `app/ros/ros1/native.py`）。**前端已处理项**：`deviceEncoding.ts` 已改三轴编码。**新增**：急停解除接口 `POST /api/control/emergency_stop/release`（发布 `/control/softstop_release`，softstop_node 源码确认转发 `[0x04]`）。
>
> **待 ROS 端修正**：已推送的 `rob_arm.env` 中 `ROS_TOPIC_FRONTEND_POINTCLOUD=/control/frontend_pointcloud_topic` 与本文档 §2/§3 的 `/frontend_pointcloud_topic`（无前缀）矛盾——按本文档为准，请 ROS 端修正 env。

---

## 1. 链路结构

```
前端 Vue ──HTTP/WS──> 后端 FastAPI ──rosbridge ws:9010──> ROS 话题
                      （app/ros/rosbridge.py 是唯一桥）
```

- 前端**不直连** rosbridge，全部经后端转发。
- 反馈流：后端订阅 ROS 话题 → 标准化 → 经自有 WebSocket `/api/control/feedback/ws` 推给前端。
- rosbridge 地址：`ROSBRIDGE_URL` 环境变量，默认 `ws://localhost:9010`（launch 里 rosbridge 端口 9010）。

---

## 2. 前端 → 后端 HTTP 接口

| 接口 | 方法 | payload | 触发的 ROS 话题 |
|---|---|---|---|
| `/api/control/finetuning` | POST | `{module_id, device_id, unit_id, parameter_name, position}` | 按 `parameter_name` 路由到 `/control/adjust_{rotation,swing,telescopic}_cmd` |
| `/api/coordination/send` | POST | 图纸文件路径 | `/frontend_pointcloud_topic`（std_msgs/String，JSON `{"file_path":...}`）→ data_process_node |
| `/api/module/` | POST | `{module_id, station}` | `/control/module_cmd`（IntCmd）→ module_confirme_node；**station 必传**（操作员选组 1~15），module_id 业务模块号原样回执 |
| `/api/control/feedback/ws` | WebSocket | — | 后端订阅 8 路 ROS 反馈话题转发（含 V2.1 新增 `/hardware/cmd_ack`） |
| `/api/control/emergency_stop` | POST | — | `/control/softstop` → softstop_node 转发 `/arm/cmd_vel` [1] |
| `/api/control/emergency_stop/release` | POST | — | `/control/softstop_release` → softstop_node 转发 `/arm/cmd_vel` [4]（V2.1 新增） |

- `parameter_name` 取值：`"rotation"` / `"swing"` / `"telescopic"`，其他值网关直接报错。
- 微调 `position` 语义：**增量**（旋转/摆动单位°，伸缩单位 mm）。

---

## 3. 后端 → ROS 话题（发布）

来源：`mes_web/ros_mes_hou/app/ros/rosbridge.py`（2026-10-25 前为 `app/services/rosbridge_gateway.py`）

| 话题 | 消息类型 | 字段构造 |
|---|---|---|
| `/control/adjust_rotation_cmd` | RotationCmd | `module_id/device_id` 前端透传；`position=[float(增量°)]` |
| `/control/adjust_swing_cmd` | SwingCmd | 同上 |
| `/control/adjust_telescopic_cmd` | TelescopicCmd | 同上（增量 mm） |
| `/control/module_cmd` | IntCmd | `{module_id, device_id:0, position:[100, station]}`，见 §6 握手协议 |
| `/frontend_pointcloud_topic` | std_msgs/String | `{"file_path": "<图纸路径>"}` |

发布流程：`advertise` → 等 0.1s → `publish`。仅**模块确认**会临时订阅回执话题等待结果。

网关校验：`device_id` 校验范围 1~15（三轴，V2.1）；`module_id` 必须为正整数，禁止后端写死。

---

## 4. 后端订阅的 ROS 话题（→ 前端 WS）

来源：`app/ros/profile.py` 话题表 + `app/ros/normalizer.py`

| 话题 | 消息 | data_type 标签 | 单位/语义 |
|---|---|---|---|
| `/hardware/rotation_feedback` | RotationCmd | `rotation_axis_encoder` | **度**（feedback_node 已 ÷100） |
| `/hardware/swing_feedback` | SwingCmd | `swing_axis_encoder` | 度 |
| `/hardware/telescope_feedback` | TelescopicCmd | `telescope_axis_encoder` | **度（电机转角），不是 mm** |
| `/hardware/sensor_feedback` | SensorCmd | `pressure_sensor` | 压力瞬时值（臂级 device_id 21~25） |
| `/hardware/pressure_avg` | SensorCmd | `pressure_average` | 5s 窗口均值（臂级 21~25） |
| `/hardware/imu_angles` | 陀螺姿态 | `imu_pose`，附带 `swing_angle/rotation_angle/x/y/z` | 末端位姿（臂级 21~25） |
| `/hardware/vacuum_loss` | VacuumLoss | `vacuum_loss_event` | 失压告警（臂级 21~25） |
| `/hardware/cmd_ack` | CmdAck | `cmd_ack`（透传 `ack_msg/result/detail`） | 指令回执（V2.1）。**源码提取枚举**：仅超位拒绝一种——`ack_msg=1, result=3, detail=1上限/2下限`；无成功回执 |

标准化后转发字段：`{time_id, topic, header, module_id, device_id, position, data_type, feedback_type, raw, ...}`。前端按 `device_id` 映射到臂/轴显示（FineTuningPage.vue）。

---

## 5. device_id 编码（三轴布局，ROS 侧现行）

| 范围 | 含义 |
|---|---|
| 0 | 系统级（急停/解除/模块停，`/arm/cmd_vel` position=[1]/[4]/[8]） |
| 1~15 | 轴级：`(臂号-1)*3 + 关节`；关节 1=旋转 / 2=摆动 / 3=伸缩 |
| 21~25 | 臂级传感器（压力/IMU 共用，按话题区分），臂号 = device_id - 20 |

轴号速查：臂1=1/2/3，臂2=4/5/6，臂3=7/8/9，臂4=10/11/12，臂5=13/14/15。

---

## 6. 模块确认（会话式握手）契约

方案来源：《固定工位会话式模块确认方案》。流程：宣告 → 分配 → 三项核对（UID56+SEG+Result=0）→ READY。

**下行** `/control/module_cmd`（IntCmd）：

| 字段 | 值 |
|---|---|
| `module_id` | 后端业务模块号，**回执原样返回**（后端按它匹配 ACK） |
| `device_id` | 固定 0 |
| `position` | `[100, station]`：100=触发握手；**station=工位号 1~15（SEG=station-1）** |

**回执**（二选一，网关等待其中一个）：

| 话题 | 判定 | HTTP |
|---|---|---|
| `/hardware/module_confirm_success` | `module_id` 匹配 且 `device_id==0` 且 `position[0]==100` | 200 |
| `/hardware/web_module_cmd` | `module_id` 匹配 且 `device_id==0` 且 `position[0]==1` | 502 |

ROS 侧超时参数（rob_arm.env）：等宣告 `MODULE_CONFIRM_WAIT_ANNOUNCE_S=30.0`；等确认 `MODULE_CONFIRM_ACK_TIMEOUT_S=0.3`、重试 `MODULE_CONFIRM_MAX_RETRY=3`（Seq 递增）。
后端回执等待上限：`ROS_MODULE_CONFIRM_ACK_WAIT_S`（默认 35s，须 ≥ 宣告窗口 + 重试时间）。

---

## 7. 注意事项（按严重度）

### 🔴 7.1 模块确认网关超时 5s vs 握手窗口 30s —— 必然 502（**后端已修**）

后端等待回执超时已与普通超时分离：`RosbridgeClient(module_confirm_timeout=…)`，默认取 `ROS_MODULE_CONFIRM_ACK_WAIT_S`（35s）。

### 🔴 7.2 模块确认缺工位号 —— 新协议立即失败（**后端已修，前端待接**）

后端已发 `position=[100, station]`，且 **station 为 `/module/` 请求体必传字段**（操作员选择"确认第几组"），后端不做任何默认推导（推错组会误握手物理工位），缺 station 直接 422。module_id 保持业务模块号、无范围限制，ROS 端原样回执用于 ACK 匹配。**前端待办：模块锁定页增加"确认第几组"选择并把 station 带到请求里。**

### 🔴 7.3 device_id 三轴化 —— 后端已改，前端已同步，种子数据待决（**部分待办**）

前端 `deviceEncoding.ts` 与后端已同步为三轴：`(arm-1)*3 + {1,2,3}`（1~15）、臂级 21~25 不变。
**待决**：`armIndexFromUnitId` 按 Unit_ID÷32 推臂号（种子数据 32/64/96 仅 3 臂），臂 4/5 无法识别，需补种子或改映射。

### 🟠 7.4 微调无回执 —— 超位拒绝前端无感知（**后端已修，前端待接**）

`/hardware/cmd_ack` 已加入后端反馈流（`data_type=cmd_ack`，透传 `result/detail`）。前端 FineTuningPage 可按该标签提示"超位被拒绝"。

### 🟡 7.5 伸缩反馈单位是"度"不是"mm"（**待办，前端**）

`/hardware/telescope_feedback` 的 position 为电机转角（°）。前端若按 mm 展示会差 `MM_PER_REV/360` 倍（当前 MM_PER_REV=0.7 假设值）。要么前端换算，要么 ROS 侧加一路 mm 话题。

### 🟡 7.6 反馈 module_id 是 SEG，不是业务模块号

hardware_node 上行反馈 `module_id = CAN SEG 位域`（0~14），与前端下发的业务 module_id 不同空间。当前前端按 device_id 匹配所以未暴露；任何按 module_id 关联的逻辑都会错。

### 🟢 7.7 急停链路（**已闭环**）

ROS 侧系统级指令走 `/arm/cmd_vel`（device_id=0，position=[1]急停/[4]解除/[8]模块停）。后端现状：
- `POST /api/control/emergency_stop` → `/control/softstop`，softstop_node（源码已核对）转发 `/arm/cmd_vel` [0x01]；
- `POST /api/control/emergency_stop/release`（V2.1 新增）→ `/control/softstop_release`，softstop_node 转发 [0x04]；
- "模块停"（[0x08, module_id]）与"单轴急停"（[0x0C]）暂无入口，如需再立项。

### 🟢 7.8 旧路径残留（**已解决**）

rospy 直发 `/web_cmd` 的旧路径已迁移为 `app/ros/ros1/native.py`（ROS1 适配器专属通道，ROS2 适配器无此通道），与 rosbridge 网关各司其职。

---

## 8. ROS1 / ROS2 差异速查表

> 本文其余部分（话题、消息、device_id 编码、握手语义）是**版本无关的契约**，ROS1 与 ROS2 下应完全一致；两个版本的差异只有下表几项，代码层面的唯一真相是 `mes_web/ros_mes_hou/app/ros/profile.py`。后端切换用环境变量 `ROS_VERSION=1|2`（见 `deploy/config/backend.env`），业务接口不感知版本。

| 差异项 | ROS1（现网） | ROS2（预设，待移植定稿核对） |
|---|---|---|
| 自定义消息类型串 | `robot_control_backend/IntCmd` | `robot_control_backend/msg/IntCmd` |
| std_msgs 类型串 | `std_msgs/String` | `std_msgs/msg/String` |
| `header.stamp` 字段名 | `secs` / `nsecs` | `sec` / `nanosec` |
| rospy 原生通道（`/web_cmd`、`/robot_status`，`/ros/**` 接口） | 有 | 无（`/ros/**` 返回 503，ROS 端提供后补充） |
| 构建与启动 | catkin_make + roslaunch（Noetic） | colcon + ros2 launch（待 ROS 端移植） |

**对 ROS 端 ROS2 移植的要求**：话题名、消息字段、device_id 编码、模块确认握手语义保持与本文档一致（即与 ROS1 现网一致）；若必须调整，先改本文档并同步前后端，再动代码。移植出可跑分支后，后端切 `ROS_VERSION=2` 联调一次即可验证 `profile.py` 中的 ROS2 预设。
