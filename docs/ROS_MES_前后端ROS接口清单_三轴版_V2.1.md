# ROS_MES 前后端 ↔ ROS 接口清单（三轴版 V2.1）

> **收录说明**（2026-10-25）：本文档由队友基于 ROS 源码现状整理（整理日期 2026-10-24），收录进仓库时仅把原文中的本机绝对路径链接改为仓库相对路径；**取代**《ROS_MES_前后端接口字段文档.md》中的 ROS 话题 / 消息 / device_id 编码部分（四轴布局已失效），该文档的业务接口与数据库部分仍有效。
>
> **后端已处理项**（同日提交，勿重复修改）：7.1 模块确认回执超时已调至 35s（`ROS_MODULE_CONFIRM_ACK_WAIT_S`）；7.2 模块确认已带工位号（`position=[100, station]`，默认 `station=module_id+1`，前端可显式传 `station`）；7.3 的后端部分（校验 1~15、反馈标签表三轴化）已完成；7.4 的 `/hardware/cmd_ack` 已加入反馈流。**前端已处理项**：`deviceEncoding.ts` 已改三轴编码（`AXES_PER_ARM=3`、偏移 `{1,2,3}`）。

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
| `/api/module/` | POST | `{module_id}` | `/control/module_cmd`（IntCmd）→ module_confirme_node |
| `/api/control/feedback/ws` | WebSocket | — | 后端订阅 8 路 ROS 反馈话题转发（含 V2.1 新增 `/hardware/cmd_ack`） |

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
| `/hardware/cmd_ack` | CmdAck | `cmd_ack`（透传 `ack_msg/result/detail`） | 指令回执（V2.1，超位拒绝 result=3） |

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

后端已发 `position=[100, station]`：默认 `station = module_id + 1`（模块号=SEG=工位-1），前端可在 `/module/` 请求体显式传 `station` 覆盖。**前端仍需让操作员选择"确认第几组"并把工位号带到后端**（待办）。

### 🔴 7.3 device_id 三轴化 —— 后端已改，前端已同步，种子数据待决（**部分待办**）

前端 `deviceEncoding.ts` 与后端已同步为三轴：`(arm-1)*3 + {1,2,3}`（1~15）、臂级 21~25 不变。
**待决**：`armIndexFromUnitId` 按 Unit_ID÷32 推臂号（种子数据 32/64/96 仅 3 臂），臂 4/5 无法识别，需补种子或改映射。

### 🟠 7.4 微调无回执 —— 超位拒绝前端无感知（**后端已修，前端待接**）

`/hardware/cmd_ack` 已加入后端反馈流（`data_type=cmd_ack`，透传 `result/detail`）。前端 FineTuningPage 可按该标签提示"超位被拒绝"。

### 🟡 7.5 伸缩反馈单位是"度"不是"mm"（**待办，前端**）

`/hardware/telescope_feedback` 的 position 为电机转角（°）。前端若按 mm 展示会差 `MM_PER_REV/360` 倍（当前 MM_PER_REV=0.7 假设值）。要么前端换算，要么 ROS 侧加一路 mm 话题。

### 🟡 7.6 反馈 module_id 是 SEG，不是业务模块号

hardware_node 上行反馈 `module_id = CAN SEG 位域`（0~14），与前端下发的业务 module_id 不同空间。当前前端按 device_id 匹配所以未暴露；任何按 module_id 关联的逻辑都会错。

### 🟢 7.7 急停链路（**现状核对**）

ROS 侧系统级指令走 `/arm/cmd_vel`（device_id=0，position=[1]急停/[4]解除/[8]模块停）。后端 `POST /api/control/emergency_stop` 已存在，发布 `/control/softstop`，由 softstop_node 转发到 `/arm/cmd_vel` position=[1]（链路在 rob_arm.env 中仍配置，2026-10-25 核对）。**急停/解除的"解除"（[4]）与"模块停"（[8]）前后端均无入口**，如需要另行立项。

### 🟢 7.8 旧路径残留（**已解决**）

rospy 直发 `/web_cmd` 的旧路径已迁移为 `app/ros/ros1/native.py`（ROS1 适配器专属通道，ROS2 适配器无此通道），与 rosbridge 网关各司其职。
