# LOOP_LAST · PR-2 B6 offline breakdown

- **时间**：2026-07-28T14:40:00+08:00
- **campaign**：pillar_c_v3
- **phase**：B / PR-2 localize + culprit-only SET
- **parent**：`20260728_141052-pillar-c-v3-pr2-e3-b5d`
- **verdict**：PARTIAL
- **B5d 事实**：dense=1；culprit=7；LOCALIZE_FALLBACK=0；SET_OK；SET_DOWNGRADE=1；headline=115.05%；same_cover=N
- **本轮产物**：`PR2_B6_VOLUME_BREAKDOWN.md`
- **核心发现**：W* 估算已把空 `python.torch_trace` 按 0 计；超标主因是 main_empty 非 TT 固定环（1020.10MiB）+ extra_pid 非 TT dump（864.10MiB）。
- **反事实**：去掉 main_empty `python.torch_step_timing`+`python.comm_collective` 后估算 ratio≈79.94%；去掉 extra_pid 后≈64.49%。
- **next_round**：派 PR-2 B6 code/smoke；优先让非 culprit/rate=0 不预分配 step+comm 大环，并过滤 extra_pid dump；短 smoke 验证 dense=1、culprit=7、fallback=0、SET_OK、extra_pid 显著下降后再长跑。
- **blockers**：未实时查 yysong-w0；本轮未上机。
