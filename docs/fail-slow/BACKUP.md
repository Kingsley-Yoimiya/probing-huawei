# 结果备份位置（Fail-Slow Ascend）

检查优先看本文件 + [`../../results/ascend-ais/INDEX.md`](../../results/ascend-ais/INDEX.md)。

## Dose Sweep（Quiet+Masked）

| 落点 | 路径 | 体量 / 说明 |
|---|---|---|
| **ais-cf3e61a5 开发机** | `/root/backups/ascend-ais-dose-sweep-20260726` | ≈**1.7G** 解压；同目录 `…tar.gz` **60M** |
| ais 说明 | `/root/backups/README-ascend-ais-dose-sweep.md` | md5 `28732ddd3621bc7f86e5b367a82bc5df` |
| AFS（pod 内真盘） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais` | 含 Dose formal + baseline |
| 本机 | `results/ascend-ais/`（本仓） | 日常主拷贝；git 只瘦身入库 |

## Pillar C v2（E1–E4 + S1 · 2026-07-26）

| 落点 | 路径 | 体量 / 说明 |
|---|---|---|
| **ais-cf3e61a5 开发机** | `/root/backups/ascend-ais-pillar-c-v2-20260726` | 解压 ≈**11G**（含 probing_data） |
| ais 归档 | `/root/backups/ascend-ais-pillar-c-v2-20260726.tar.gz`（**54M**）+ `README-….md` + `….md5` | md5 `9b65f31dc97010fd0232bee40c5235bc` |
| AFS（pod 内真盘） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2` | ≈**11G** |
| 本机 git | `results/ascend-ais/pillar_c_v2/` | **瘦身**：摘要 / `E*_*.md|json` / logs / ranks / query / `_gate`；**无** `probing_data` |
| 战役摘要 | `results/ascend-ais/pillar_c_v2/CAMPAIGN_SUMMARY.md` | 头条 72.6% 等 |

```bash
ssh ais-cf3e61a5 "du -sh /root/backups/ascend-ais-pillar-c-v2-20260726*; cat /root/backups/ascend-ais-pillar-c-v2-20260726.md5"
```

旧 `pillar_c/`（cold 三臂）仍只留 AFS，标 SUPERSEDED，不进本波备份包。
