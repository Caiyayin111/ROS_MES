# SegAssign.msg —— 会话式模块确认：上位机分配 SEG 请求（模块确认节点 → hardware_node）
# 对应 CAN 帧 ID = 0x15BC0010，DLC=8，data[0..6]=TargetUid56，data[7]=(SEG<<4)|Seq
std_msgs/Header header
uint64 target_uid56     # 目标设备 UID 低 56 位（必须等于宣告帧解析出的值）
uint8 seg               # 分配的组号 0~14（SEG=15 仅用于领号，不可分配给正常组）
uint8 seq               # 重试序号，每次重发递增
