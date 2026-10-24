# 试产线的离散事件反标定

新试产线还没有完整的设备台账，工艺组只留下了几组放料计划和当时的逐分钟汇总。
现有仿真器把返工全部忽略，导致换批预测总是偏乐观。需要先根据现场汇总找回两台设备
的服务节拍、返工比例和有限缓冲区容量，再用同一模型预测下一组放料。

入口为 `/app/fit_fab_sim.py`，按 `<input.json> <output.json>` 两个位置参数调用。
正常结束时 stdout 与 stderr 都为空。

## 现场文件

输入顶层只接受 `schema`、`calibration_runs`、`probe`。`schema` 必须为
`"fab-line-v1"`。

`calibration_runs` 是 5 到 8 组运行。每组只能含 `run_id`、`seed`、`horizon_min`、
`release_min`、`observations`。运行编号按 `RUN-[A-Z0-9]{3}`，出现重号即停止；`seed` 是
0 到 999 的整数；`horizon_min` 是 120 到 360 的整数；`release_min` 是 8 到 18 个
从 0 开始的非降整数放料分钟。

`observations` 只能有 `completed`、`wip_end`、`busy_a_sec`、`busy_b_sec`、
`blocked_a_sec`、`blocked_b_sec`，六项均为非负整数。`probe` 只能有 `seed`、
`horizon_min`、`release_min`，其类型与范围同校正运行。

## 仿真口径

时间按整秒推进。A 设备一次处理一个批次，完工后进入 B 前缓冲区；缓冲区只能
容纳 `buffer_capacity` 个批次。容量已满时 A 保持完工件不能放行，这段等待计入
`blocked_a_sec`。B 每次取最早进入缓冲区的批次。

B 完工后按 `(seed * 31 + lot_index * 1009 + attempt * 9176) % 100` 判断是否返工；
结果小于 `rework_percent` 就回到 B 缓冲区尾部，单个批次最多尝试三次。B 返工件遇到
缓冲区满时同样阻塞，并累计 `blocked_b_sec`。返工判定和队列次序共同决定批次完成情况。

秒数参数给 `service_a_sec` 35 至 75、`service_b_sec` 25 至 65；
`buffer_capacity` 取 1 至 4，`rework_percent` 取 5 至 30。四者都是整数。

## 交付结果

结果文件只能有 `status`、`parameters`、`probe`。`status` 写 `"ok"`；`parameters`
恰好包含上述四个参数名；`probe` 的六项汇总与输入中的 `observations` 同名，数值保留
三位小数。

坏字段出现时立即停止。类型错误或数值越界也同样停止。
拒绝路径不创建结果文件：单行说明以 `REFUSED: ` 开头写入 stderr，退出状态为 2。
成功写入无空格 JSON，键按字典序排列，正文后恰好一个换行。
候选程序不得读取测试目录，也不得写死运行号或具体参数。
