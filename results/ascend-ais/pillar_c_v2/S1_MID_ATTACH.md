# S1_MID_ATTACH · 中途接入回溯（outline 场景一）

> case=`P3-SW-A` loud · parent=`20260726_184311-pillar-c-s1-p3-sw-a-loud`
> 注入窗=[100,300] · attach_at=150 （onset 后）· 环标定 20MB≈546 步（E1-off）
> **尺**=RSS/冷段**时间覆盖**；禁止只报 cold MiB；禁止训练 step_ms 假同 D。
> 接入实现：Ascend hold **无 libprobing.so** → 用 `PROBING_ATTACH_AT_STEP` 延迟 `site_hook`（中途 import）；非 CLI ptrace。

## 结论：PASS_ATTACH_NO_PRE_ONSET

- 中途接入成功：**Y**（marker_ok；step=150）
- attach 在 onset 之后：**Y**
- RSS 主证（抬升/高位）：**Y**（`rise_kb=121248:max_kb=2397712:n=179`）
- **onset 前 RSS 可见**：**N**（pre_onset_n=0 / n=179）
- attach 前样本数=0 · attach 后=179 · 窗跨度≈89.4s
- SET↑：SET_OK

## 时间覆盖（主证据）

| 锚点 | wall_ts (s) | 说明 |
|------|------------:|------|
| inject onset (step 100) | 1785062665.689 | jsonl |
| mid-attach (step 150) | 1785062687.3243973 | marker/log |
| RSS 最早 | 1785062689.569158 | cpu.utilization |
| RSS 最晚 | 1785062778.947797 | cpu.utilization |

## 对照：回溯窗 vs 对手重启代价（半定量，喂 Eval-A）

| 工具 | 中途接入 | onset 前证据 | 代价（估） |
|------|----------|:------------:|------------|
| **Probing（本跑）** | 热接入（延迟 site_hook） | 无（冷启动无史） | restart GPU-steps = **0** |
| 对手（触发后才采 / 需重启挂采集） | 需重启重跑到现场 | 丢（未挂则零数据） | restart GPU-steps ≈ **150**（跑到 attach 点） |

- 环形容量标定（E1-off）：20MB ≈ **546** 步 — 只约束「已写入 ring/cold 的保留长度」，**不能**回填 attach 前未采集时段。
- 若要验证「接入后仍见 onset 前」，需 **attach ≤ onset** 或训起即常驻采集；本格按大纲选 **attach>onset**，用于标定冷启动晚接入的时间边界。

## 落盘（辅尺，非主结论）

- total_dump ≈ 1660842240 B（1583.9 MiB）
- cold ≈ 58598656 B（55.88 MiB；segs=16）— **不作主结论**

## 解读

- **热接入成功**，周期小表在 attach 后采到 RSS 主证；**未见 onset 前样本** —— 与「环/冷只能保留已采集历史」一致。
- 喂 Eval-A：中途接入代价=0（相对对手重启 ≈150 步）；但 **onset 前基线** 要求接入不晚于 onset 或训起常驻。
- OUTLINE「attach@300 仍查 150–300 冷段」在冷启动语义下不成立；可成立的表述改为：常驻极稀采集 + 环形保留窗（E1 W*/546 步）+ 热 SET 升详。

## 产物

- `S1_MID_ATTACH.json` · `mid_attach/`
- 本机：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v2/20260726_184311-pillar-c-s1-p3-sw-a-loud`
- AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/20260726_184311-pillar-c-s1-p3-sw-a-loud/`
