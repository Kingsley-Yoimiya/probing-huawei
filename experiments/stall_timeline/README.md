# Stall Timeline Observer（2026-08-06）

目标：在训练式 `compute -> collective -> synchronize` 循环中，用低开销采样同时记录
device phase 时间和 host 同步时间，验证能否捕捉约 500 ms 的局部 stall 及其跨 rank 传播。

## 假设

| 编号 | 假设 | 可观测签名 |
|---|---|---|
| H1 | 本地 device phase 发生 stall | culprit 的 `device_compute_ms` 突增 |
| H2 | 本地 stall 经 collective 传播 | peer 的 `host_sync_ms` / `host_iter_ms` 同 step 突增 |
| H3 | host-only 不能可靠定位起点 | host 最大等待 rank 不稳定，device top-1 更接近 culprit |
| H4 | 稀疏采样仍可见，但召回受 inclusion probability 限制 | 条件召回高；无条件召回约等于采样覆盖率 |
| H5 | source rank 可能先在 step `t` 单独慢、再让 peers 在 `t+1` 等待 | `singleton -> all_but_one`，两步的 complement 指向同一 rank |

受控注入仅用于验证捕获链，不等同于自然硬件 stall：

- `host`：culprit 在 collective 前 `sleep(500ms)`；验证 host 传播签名。
- `device`：culprit 在 device event 包围的 compute phase 内增加校准后的 matmul；验证
  device-origin + peer-wait 签名。

## 五种采样方案

| scheme | device event | 用途 |
|---|---|---|
| `host` | 不记录 | 最低开销基线，只看 host timeline |
| `sentinel` | 每 rank、每 step 一个粗粒度 event pair | 期望的常驻原语 |
| `aligned` | 所有 rank 同步抽取同一批 step | 跨 rank 对齐最好，但被采 step 全员有开销 |
| `rotate` | 每 step 轮换一小组、拓扑分散的 rank | 固定每步事件预算；每步跨节点，随时间覆盖所有卡 |
| `random` | 每个 `(rank, step)` 独立稳定哈希抽样 | 无偏探索流，避免异常周期与采样周期锁相 |

`aligned`、`rotate` 和 `random` 都把 `sample_probability` 写入每条记录。触发式升详数据不能直接
用于无偏估计自然发生率；后续应保留独立的随机探索流。

## 时间语义

- `device_compute_ms`：同一 NPU stream 上、compute phase 前后 Event 的 elapsed time。
- `device_collective_ms`：sampled step 上 collective 前后 Event 的 elapsed time，用来区分
  compute kernel stall 与 HCCL/device 通信阶段 stall。
- `host_allreduce_call_ms`：Python `dist.all_reduce()` 调用的 host wall time；不等价于 HCCL
  device execution time。
- `host_sync_ms`：紧随 collective 的 `torch.npu.synchronize()` host wall time，会包含尚未
  完成的本地/通信工作。
- 不直接比较 host 绝对时钟与 device 绝对时钟；用 `(rank, step, op_seq)` 关联。

## 产物

每个 rank 写：

- `rank_XXXX/timeline.jsonl`：独立真值/备份路径；
- `python.stall_timeline`：Probing 自定义表；
- `rank_XXXX/probing_timeline.jsonl`：结束前从 Probing SQL 回读，验证表可查询；
- `rank_XXXX/meta.json`：采样与 device 注入校准信息。

分析脚本输出 `analysis.json` 与 `SUMMARY.md`，报告 host/device 捕获率、device top-1
定位率、peer 传播比例和各 scheme 的 clean-step 中位开销。

## 诚实边界

1. 本原型的 device event 包围 phase，不是逐 kernel profiler。
2. `device` 注入是额外 device workload，不是自然 stall；只验证观测链。
3. 自然 stall 率需要长窗与无偏探索采样；短 smoke 零事件是允许结果。
4. host sync 变慢只能证明等待暴露在该同步点，不能单独定位根因。
5. collective device event 变长也可能只是 sampled peer 在等待；需结合跨 step slow-set
   complement，不能把 device top-1 直接叫 source。
