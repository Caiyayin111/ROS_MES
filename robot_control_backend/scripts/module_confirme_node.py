#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块确认节点 V2.1 —— 固定工位会话式握手（UID 领号 + SEG 会话分配）

方案来源：《固定工位会话式模块确认方案》
核心规则：
  - 模块号 = SEG（0~14），由固定工位决定：工位 N（1~15）→ SEG = N-1
  - F407 上电即未编号（运行期 SEG=0xFF），周期发送未编号宣告，不读 Flash
  - 上位机收到宣告后按工位分配 SEG，设备回确认帧；
    必须同时核对 UID56、SEG、Result=0 三项才判定握手成功
  - 断电后重新上电必须重新握手（会话表仅本次运行有效，不入库）

接口（与后端原契约保持一致）：
  输入：/control/module_cmd (IntCmd)
        module_id 原样透传（后端业务模块号，用于回执匹配）
        device_id 固定 0
        position = [100, station]：100=触发握手，station=工位号 1~15
  输出：/hardware/module_confirm_success（position=[100]）
        /hardware/web_module_cmd（position=[1]，失败，后端回 HTTP 502）

状态机（同一时间只确认一组，新指令会取消进行中的握手）：
  IDLE → WAIT_ANNOUNCE →(收到宣告)→ WAIT_CONFIRM →(三项核对通过)→ IDLE + 工位 READY
  等待宣告超时 / 分配确认 3 次无应答 → 失败回执 → IDLE
"""
import os
import threading

import rospy
from std_msgs.msg import Header
from robot_control_backend.msg import IntCmd, UidAnnounce, SegAssign, SegConfirm

# 握手状态
IDLE, WAIT_ANNOUNCE, WAIT_CONFIRM = 0, 1, 2


def load_env_config():
    """从 .env 文件加载配置"""
    env_path = os.path.join(os.path.dirname(__file__), '../rob_arm.env')
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value
        rospy.loginfo("✅ 模块确认节点已从 rob_arm.env 加载配置")


class ModuleSessionConfirmer:
    """固定工位会话式模块确认：宣告 → 分配 → 核对确认 → READY"""

    RESULT_SUCCESS = 100   # 后端契约：position[0]==100 成功
    RESULT_FAILED = 1      # 后端契约：position[0]==1 失败（HTTP 502）

    def __init__(self):
        load_env_config()
        rospy.init_node("module_confirme_node")

        # 参数全部只读 rob_arm.env，无硬编码默认值
        self.wait_announce_s = float(os.environ['MODULE_CONFIRM_WAIT_ANNOUNCE_S'])
        self.ack_timeout_s = float(os.environ['MODULE_CONFIRM_ACK_TIMEOUT_S'])
        self.max_retry = int(os.environ['MODULE_CONFIRM_MAX_RETRY'])

        # 话题全部只读 rob_arm.env
        self.pub_assign = rospy.Publisher(
            os.environ['ROS_TOPIC_SEG_ASSIGN'], SegAssign, queue_size=10)
        self.pub_success = rospy.Publisher(
            os.environ['ROS_TOPIC_MODULE_CONFIRM_SUCCESS'], IntCmd, queue_size=10)
        self.pub_failed = rospy.Publisher(
            os.environ['ROS_TOPIC_MODULE_CONFIRM_FAILED'], IntCmd, queue_size=10)
        # 握手成功/失效后向 hardware_node 同步 SEG 绑定，用于运动帧动态寻址
        self.pub_seg_bind = rospy.Publisher(
            os.environ['ROS_TOPIC_SEG_BIND'], IntCmd, queue_size=10)
        rospy.Subscriber(os.environ['ROS_TOPIC_MODULE_CMD'], IntCmd, self.cb_module_cmd)
        rospy.Subscriber(os.environ['ROS_TOPIC_UID_ANNOUNCE'], UidAnnounce, self.cb_announce)
        rospy.Subscriber(os.environ['ROS_TOPIC_SEG_CONFIRM'], SegConfirm, self.cb_confirm)

        self.lock = threading.Lock()
        self.state = IDLE
        # 当前握手上下文：{req_module_id, station, seg, uid56, seq, retries, deadline}
        self.current = None
        # 会话表：工位号 1~15 -> {"uid56", "seg", "ts"}；仅本次运行有效，断电需重新握手
        self.session = {}

        rospy.Timer(rospy.Duration(0.05), self._check_timeout)
        rospy.loginfo("✅ 模块确认节点已启动（固定工位会话式握手：等宣告 %.1fs / 确认 %.2fs × %d 次）",
                      self.wait_announce_s, self.ack_timeout_s, self.max_retry)

    # ================= 入口：后端模块确认指令 =================
    def cb_module_cmd(self, msg):
        """/control/module_cmd：position=[100, station]，station=工位号 1~15（组号）"""
        if not msg.position or int(msg.position[0]) != 100:
            rospy.logwarn("⚠️ 模块确认指令 position[0] 应为 100（握手触发），收到 %s，已忽略",
                          list(msg.position))
            return
        if len(msg.position) < 2:
            self._publish_result(self.pub_failed, int(msg.module_id),
                                 self.RESULT_FAILED, "缺少工位号：position 需为 [100, station(1~15)]")
            return
        station = int(msg.position[1])
        if not (1 <= station <= 15):
            self._publish_result(self.pub_failed, int(msg.module_id),
                                 self.RESULT_FAILED, f"工位号 {station} 非法（1~15）")
            return

        with self.lock:
            if self.state != IDLE:
                rospy.logwarn("↪️ 取消工位%d 进行中的握手，切换至工位%d",
                              self.current["station"] if self.current else -1, station)
            # 工位 N → SEG = N-1；进入等待宣告（操作员此时给目标组上电）
            self.current = {
                "req_module_id": int(msg.module_id),   # 后端业务模块号，回执原样返回
                "station": station,
                "seg": station - 1,
                "uid56": None,
                "seq": 0,
                "retries": 0,
                "deadline": rospy.Time.now() + rospy.Duration(self.wait_announce_s),
            }
            self.state = WAIT_ANNOUNCE
        rospy.loginfo("🔌 开始确认工位%d（期望 SEG=%d），请给该组上电，等待未编号宣告（%.1fs）",
                      station, station - 1, self.wait_announce_s)

    # ================= 宣告处理 =================
    def cb_announce(self, msg):
        """收到未编号宣告：握手中则立即分配；否则检测"已 READY 组重新上电" """
        with self.lock:
            if self.state == WAIT_ANNOUNCE:
                self.current["uid56"] = int(msg.uid56)
                self._send_assign_locked(seq=1)
                self.current["retries"] = 1
                self.current["deadline"] = rospy.Time.now() + rospy.Duration(self.ack_timeout_s)
                self.state = WAIT_CONFIRM
                rospy.loginfo("📢 收到宣告：UID56=%X（槽%d）→ 分配 SEG=%d，Seq=1",
                              msg.uid56, msg.slot, self.current["seg"])
                return
            # 非握手期间收到宣告：若为已 READY 工位的 UID，说明该组断电重启，会话失效
            stale = [s for s, rec in self.session.items() if rec["uid56"] == int(msg.uid56)]
            for s in stale:
                rec = self.session.pop(s)
                rospy.logwarn("🔌 工位%d（SEG=%d）重新上电，会话已失效，需重新确认", s, s - 1)
                # 解绑该业务模块的 SEG 路由，避免运动帧继续发往已重启（未编号）的组
                self._publish_seg_bind(rec.get("module_id", -1), -1)
            if not stale:
                rospy.loginfo("📢 收到未请求宣告：UID56=%X（槽%d），如需启用请下发模块确认指令",
                              msg.uid56, msg.slot)

    # ================= 分配确认处理 =================
    def cb_confirm(self, msg):
        """分配确认帧：三项核对（UID56、SEG、Result=0）全部通过才判定握手成功"""
        with self.lock:
            if self.state != WAIT_CONFIRM or self.current is None:
                return
            cur = self.current
            if int(msg.uid56) != cur["uid56"] or int(msg.seg) != cur["seg"]:
                rospy.logwarn("⚠️ 确认帧不匹配（UID56=%X/SEG=%d，期望 %X/%d），忽略",
                              msg.uid56, msg.seg, cur["uid56"], cur["seg"])
                return
            if int(msg.result) != 0:
                rospy.logwarn("⚠️ 设备写入 SEG 失败 Result=%d，按重试处理", msg.result)
                self._retry_or_fail_locked("设备返回 Result=%d" % msg.result)
                return
            # 三项核对通过：握手成功，登记会话表
            self.session[cur["station"]] = {
                "uid56": cur["uid56"], "seg": cur["seg"],
                "module_id": cur["req_module_id"],
                "ts": rospy.Time.now().to_sec(),
            }
            station, req_mid, seg = cur["station"], cur["req_module_id"], cur["seg"]
            self.current = None
            self.state = IDLE
        self._publish_result(self.pub_success, req_mid, self.RESULT_SUCCESS,
                             "工位%d（SEG=%d）握手成功 UID56=%X" % (station, seg, msg.uid56))
        # 通知 hardware_node：该业务模块的运动帧改用 SEG 寻址
        self._publish_seg_bind(req_mid, seg)

    # ================= 超时与重试 =================
    def _check_timeout(self, _event):
        with self.lock:
            if self.state == IDLE or self.current is None:
                return
            if rospy.Time.now() < self.current["deadline"]:
                return
            if self.state == WAIT_ANNOUNCE:
                cur = self.current
                self.current = None
                self.state = IDLE
                text = "等待宣告超时（%.1fs）：请检查该组电源、CAN 接线" % self.wait_announce_s
            else:  # WAIT_CONFIRM 超时：Seq 递增重发，最多 max_retry 次
                if self.current["retries"] < self.max_retry:
                    self.current["seq"] += 1
                    self.current["retries"] += 1
                    self._send_assign_locked(self.current["seq"])
                    self.current["deadline"] = rospy.Time.now() + rospy.Duration(self.ack_timeout_s)
                    rospy.logwarn("⏳ 分配确认超时，重发第 %d/%d 次（Seq=%d）",
                                  self.current["retries"], self.max_retry, self.current["seq"])
                    return
                cur = self.current
                self.current = None
                self.state = IDLE
                text = "分配确认 %d 次无应答：请检查该组电源、CAN 接线、UID 是否变化" % self.max_retry
        self._publish_result(self.pub_failed, cur["req_module_id"], self.RESULT_FAILED,
                             "工位%d 握手失败：%s" % (cur["station"], text))

    def _retry_or_fail_locked(self, reason):
        """设备回了非 0 确认：按一次失败重试处理（持锁调用）"""
        if self.current["retries"] < self.max_retry:
            self.current["seq"] += 1
            self.current["retries"] += 1
            self._send_assign_locked(self.current["seq"])
            self.current["deadline"] = rospy.Time.now() + rospy.Duration(self.ack_timeout_s)
        else:
            cur = self.current
            self.current = None
            self.state = IDLE
            self._publish_result(self.pub_failed, cur["req_module_id"], self.RESULT_FAILED,
                                 "工位%d 握手失败：%s（重试 %d 次）" % (cur["station"], reason, self.max_retry))

    def _send_assign_locked(self, seq):
        """发分配请求（持锁调用）：hardware_node 桥接为 CAN 帧 0x15BC0010"""
        m = SegAssign()
        m.header = Header(stamp=rospy.Time.now())
        m.target_uid56 = self.current["uid56"]
        m.seg = self.current["seg"]
        m.seq = seq
        self.pub_assign.publish(m)

    # ================= 回执（保持后端契约） =================
    def _publish_seg_bind(self, module_id, seg):
        """SEG 绑定通知：position=[seg] 绑定，position=[-1] 解绑（hardware_node 订阅）"""
        if int(module_id) < 0:
            return
        m = IntCmd()
        m.header = Header(stamp=rospy.Time.now())
        m.module_id = int(module_id)
        m.device_id = 0
        m.position = [int(seg)]
        self.pub_seg_bind.publish(m)

    def _publish_result(self, publisher, req_module_id, result_code, log_text):
        """成功/失败回执：module_id 原样返回请求值，device_id=0，position=[100|1]"""
        m = IntCmd()
        m.header = Header(stamp=rospy.Time.now())
        m.module_id = req_module_id
        m.device_id = 0
        m.position = [result_code]
        publisher.publish(m)
        if result_code == self.RESULT_SUCCESS:
            rospy.loginfo("[模块确认成功] %s", log_text)
        else:
            rospy.logwarn("[模块确认失败] %s", log_text)


if __name__ == "__main__":
    try:
        node = ModuleSessionConfirmer()
        rospy.spin()
    except rospy.ROSInterruptException:
        rospy.loginfo("❌ 模块确认节点停止")
    except Exception as e:
        rospy.logerr("❌ 模块确认节点异常: %s" % str(e))
