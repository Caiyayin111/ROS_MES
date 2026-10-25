# SegConfirm.msg —— 会话式模块确认：设备分配确认（hardware_node → 模块确认节点）
# 对应 CAN 帧 ID = 0x15BC0050，DLC=8，data[0..6]=Uid56，data[7]=(SEG<<4)|Result
std_msgs/Header header
uint64 uid56            # 设备 UID 低 56 位
uint8 seg               # 设备本次写入的 SEG（data[7] 高 4 位）
uint8 result            # 0=写入成功，其他=失败
