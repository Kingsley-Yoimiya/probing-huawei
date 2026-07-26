# 结果备份位置（Dose Sweep / Quiet+Masked）

检查优先看本文件 + [`../../results/ascend-ais/INDEX.md`](../../results/ascend-ais/INDEX.md)。

| 落点 | 路径 | 体量 / 说明 |
|---|---|---|
| **ais-cf3e61a5 开发机** | `/root/backups/ascend-ais-dose-sweep-20260726` | ≈**1.7G** 解压；同目录 `…tar.gz` **60M** |
| ais 说明 | `/root/backups/README-ascend-ais-dose-sweep.md` | md5 `28732ddd3621bc7f86e5b367a82bc5df` |
| AFS（pod 内真盘） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais` | ≈**44G**，含 `pillar_c`≈42G + baseline 全量 |
| 本机 | `results/ascend-ais/`（本仓） | 日常主拷贝；git 只瘦身入库 |

```bash
ssh ais-cf3e61a5 "du -sh /root/backups/ascend-ais-dose-sweep-20260726*; md5sum /root/backups/ascend-ais-dose-sweep-20260726.tar.gz"
```

备份内容：本机 `results/ascend-ais` 排除 `pillar_c`（Quiet/Masked formal+stub、baseline 对照、`_prep`）。`pillar_c` 只留 AFS。
