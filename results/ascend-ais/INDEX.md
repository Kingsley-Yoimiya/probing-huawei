# ascend-ais INDEX（probing-huawei 本机/编排备份）

**对外终态包（给人看）**：[`probing-test/results/ascend-ais/`](https://github.com/Kingsley-Yoimiya/probing-test/tree/main/results/ascend-ais)

本仓保留：台账引用的 SCORED run、对照瘦身 VERDICT（无 GB 级 trace）。

| run_id | case | status | note |
|---|---|---|---|
| 见 probing-test INDEX | 14 SCORED | 终态 | 2026-07-25 战役 |
| baseline/*/contrast-* | GH+XPU | 瘦身 | CONTRAST_VERDICT only |

| P3-SW-C | loud | SCORED D4 | `20260725_135238-yjr-as-c-p3-sw-c-loud` | C1/C0=2.33；SQL PASS_D4 |

## 2026-07-26 Dose Sweep Quiet+Masked 收官

- 代表集 + 扩展集 Quiet/Masked 表序格全 DONE（见 `docs/fail-slow/DOSE_QUEUE.md`）
- 末格 P3-EXT-B：quiet formal `065841` / masked formal `154204`；对照 GH/XPU 均 detect_ok=no
- 大 dump：AFS `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais`（含 pillar_c≈42G、baseline 自 w2 同步）；本仓只瘦身入库

### 备份位置（检查用）

| 落点 | 路径 | 体量 |
|---|---|---|
| **ais-cf3e61a5 · Dose** | `/root/backups/ascend-ais-dose-sweep-20260726` | ≈1.7G 解压；tar.gz 60M（md5 `28732ddd…c5df`） |
| **ais-cf3e61a5 · Pillar C v2** | `/root/backups/ascend-ais-pillar-c-v2-20260726` | ≈11G 解压；tar.gz **54M**（md5 `9b65f31d…235bc`） |
| ais README | `/root/backups/README-ascend-ais-*.md` | 备份说明 |
| AFS 全量（pod 内） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais` | Dose + `pillar_c`≈42G + `pillar_c_v2`≈11G |

## 2026-07-26 Pillar C v2 收官

- 摘要：`pillar_c_v2/CAMPAIGN_SUMMARY.md`（动态/全量 **72.6%**；E2 rate=0；E4 掉级；S1 热接入）
- 本仓瘦身入库：`results/ascend-ais/pillar_c_v2/`（无 `probing_data`）；全量见 ais / AFS
- 细节：`docs/fail-slow/{BACKUP,PILLAR_C_QUEUE}.md`

```bash
ssh ais-cf3e61a5 "du -sh /root/backups/ascend-ais-pillar-c-v2-20260726*; cat /root/backups/ascend-ais-pillar-c-v2-20260726.md5"
```
