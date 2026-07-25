# 任务卡 · Baseline Agent · Flight Recorder（可选轻轨）

> 资源紧张时可后开。默认 **不占** 与 Greyhound/XPUTimer 冲突的 16 卡；有空闲再开 `pool-fr`。

## 身份

- **前缀**：`yjr-as-b-fr-*`
- **状态**：`results/ascend-ais/baseline/flight_recorder/STATUS.md`
- **边界**：环形缓冲 + 超时 dump；**P2 通信类**更对口；P1/P3 结构上看不到属预期，不记成「检不出所以 D0 丢脸」——记 **out-of-scope**。

## 里程碑

1. 确认 Ascend PyTorch 认哪些 env（`TORCH_HCCL_*` vs `TORCH_NCCL_*`）。  
2. 短训产生可读 dump。  
3. 标明触发协议：自主超时 vs **oracle**（已知故障时刻触发 → 不能算检出率）。  
4. HANDOFF：哪些 case 族可进对照分母。

## 派发提示词骨架

```text
你是昇腾 Flight Recorder 适配 Agent。前缀 yjr-as-b-fr-*。
先确认 env 与 dump 非空；触发协议必须写清 oracle 与否。更新 baseline/flight_recorder/STATUS.md。
```
