# Profiler Level 证据（D51 V4 L 路线）

**版本：** CANN 8.5.1 / torch_npu 2.9.0 / PyTorch 2.9.0+cpu  
**容器：** `montyyin_reduce_ws16`  
**日期：** 2026-08-23

## 选定候选 Level

**唯一候选：`ProfilerLevel.Level2`**

## 文档来源与摘录

1. **昇腾 CANN 8.5.0 商用文档 — Ascend PyTorch Profiler APIs**  
   URL: https://www.hiascend.com/document/detail/en/canncommercial/850/devaids/profiling/atlasprofiling_16_0033.html  
   摘录（profiler_level 参数）：
   - Level1：在 Level0 基础上采集 CANN 层 AscendCL 数据、AI Core 指标、HCCL communication 文件等。
   - **Level2：在 Level1 基础上额外采集 CANN 层 Runtime 数据以及 AI CPU 数据（data_preprocess.csv）。**

2. **torch_npu.profiler.ProfilerLevel API 参考（Ascend Extension for PyTorch 6.0.RC3）**  
   URL: https://www.hiascend.com/document/detail/zh/Pytorch/60RC3/apiref/apilist/ptaoplist_000269.html  
   摘录：
   - `Level2`：在 Level1 基础上多采集 **CANN 层 Runtime 数据**以及 AI CPU。

3. **本机安装源码交叉验证**  
   路径：`/usr/local/python3.11.14/lib/python3.11/site-packages/torch_npu/profiler/analysis/_profiler_config.py`  
   - `LEVEL_TRACE_PRUNE_CONFIG[LEVEL0]` 裁剪 `Runtime` 等轨；**Level1/Level2 不裁剪**。
   - `LEVEL_PARSER_CONFIG[LEVEL2]` 在 Level1 之上增加 `AI_CPU` 解析。

## 为何预期 Level2 有助于 EVENT_WAIT TASK

- 缺口现象：Level1 DB 中 CANN `aclrtStreamWaitEvent` **24** 条，但 `EVENT_WAIT` TASK 仅 **22**；2 条 Wait 无任何 TASK 行（含 Notify Wait）。
- `aclrtStreamWaitEvent` 属于 **CANN Runtime（AscendCL/RT）API**；官方文档明确 Level2 相对 Level1 **额外采集 Runtime 层数据**。
- Level1 已导出 `EVENT_RECORD`（51/51 双向唯一），说明 event 类 TASK 在 Level1 部分可见；缺失的 Wait 更可能落在 Runtime 增采范围，而非再扫更高无文档 Level。
- **不选 Level0**：会裁剪 Runtime 轨，与目标相反。

## 验收预期（smoke 门禁）

单卡两流 smoke（`smoke_profiler_level.py`）须在同一 DB 内同时满足：

- `EVENT_RECORD` TASK ≥1 且与 CANN `aclrtRecordEvent` 计数一致；
- `EVENT_WAIT` TASK ≥1 且与 CANN `aclrtStreamWaitEvent` 计数一致。

未通过则 **不进入** 16-rank 正式配对。
