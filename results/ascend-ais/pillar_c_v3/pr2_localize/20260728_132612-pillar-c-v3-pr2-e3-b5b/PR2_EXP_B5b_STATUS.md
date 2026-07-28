# PR-2 实验 B5b · **BLOCKED**

**日期**：2026-07-28  
**parent**：`20260728_132612-pillar-c-v3-pr2-e3-b5b`

| 项 | 值 | 判据 |
|----|-----|------|
| bundle `_sync_live_tracers` | **✅**（178 行；grep=4） | jsync OK |
| culprit_rank | **7** ✅ | GT=7 |
| SET_UPGRADE | step=130 pid=3602544 rate=1.0 | SET_OK |
| SET_DOWNGRADE_OK | **1** reason=**time**（原生） | ≥1 |
| inject_stop marker | **0** | ≥1 |
| jsonl 末行 | **L=134** @ 13:29:33 | 训完 1800 |
| HANG_DETECTED | **manual** 13:42:29 stall≥720s | — |
| dense_ranks | **0** | == 1 |
| culprit TT rows | **0** | >0 |
| 非 culprit max rows | **0** ✅ | == 0 |
| 头条比 | **116.72%**（未完成；不可比） | <100% |

## 结论

**BLOCKED**：bundle 热更代码已到位，但 **culprit SET rate=1.0 后仍零行**；训卡在 L=134（SET 降回后 ~4 步），未过 inject_stop。

## 现象链

1. jsync `ext/torch.py` + `torch_probe.py` → `probe-bundle/pydeps` ✅
2. localize culprit=7 → SET rate=1.0 @ L=130（latency 22s）→ 原生 SET_DOWNGRADE @ L=134（elapsed 45s）
3. 全 rank jsonl **134 行**后 stall ≥12min；`node_0.log` 无 `Torch profiling hot-updated` 日志
4. 拉取 `probing_data`：16 rank 均有 20MB 环文件，**n_rows=0**（含 culprit 3602544）
5. babysit stop_hang → HANG_DETECTED + pkill（SIGTERM crash spill）

## 根因判断（待短测验证，**勿立刻 R2 全训**）

| 假设 | 证据 |
|------|------|
| A. 热更仍未通（hook 未跑 / spec 未变 / 非 pydeps import） | 零行 + 无 hot-updated log |
| B. hang@134 太快，rate=1.0 窗仅 ~4 step 且 stall 阻断落盘 | jsonl 停 @134；与 B2/B3 同阶 |
| C. rate=1.0 升详本身触发 stall（非零行根因） | B5（无 ext/torch jsync）训完 1800；B5b 卡 134 |

## 下一步（修复点前禁止 R2）

1. **短自检**（pod IDLE）：`PROBING=1` 小步训 + `probing config rate=1.0` → 查 rank7 log `hot-updated` + `/dev/shm/.../python.torch_trace` n_rows
2. 确认 import：`probing.ext.torch.__file__` 指向 `probe-bundle/pydeps`
3. 若热更 OK 但仍 hang@134：拆 **hang 与 dense**——先试 `PILLAR_C_SET_WINDOW_S=10` 或步数窗，或 post-SET 延迟 dump
4. 若热更仍 FAIL：查 `_last_spec` 比较 / optimizer hook 是否在 rank7 注册

- 判分：`PR2_E3_RATIO_B5b.md`
- 发射：`PR2_EXP_B5b_LAUNCH.md`
