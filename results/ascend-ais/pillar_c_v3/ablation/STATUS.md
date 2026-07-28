# 附录 A 离线消融 · 执行状态

**结论**：**PARTIAL**（核心叙事可写；与手册预期有两处 scorer 粒度差异）

## 完成情况

| 项 | 状态 |
|---|---|
| full_fidelity dump | ✅ 本机已有（无需 AFS 拉取） |
| 判分脚本 | ✅ `project/probing-test/scripts/fail-slow/score_dlevel_sql.py` |
| 对照 D4 基线 | ✅ |
| 小表 4 张删表臂 | ✅ 已跑（仅 `cpu.utilization` 致掉级） |
| 大表 5 张删表臂 | ✅ 已跑（均保持 D4） |
| `ABLATION_MATRIX.md` | ✅ |

## 数据来源

- **Dump**：`pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity`（AFS 原路径 `/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c/…`；本机 du≈1.6 GiB，`total_dump_bytes.txt`=1791975360）
- **训练 jsonl**：`20260725_012957-yjr-as-c-p3-sw-a-loud`（同 P3-SW-A Loud 配方；手册允许与 full_fidelity 配方一致）
- **未写宋盘**；未占集群

## 与手册差异（诚实记录）

1. **`cpu.utilization` 掉至 D3 而非 D1–D2**：离线埋点仍到 D3（C1/C0、victim 定位不变），仅 SQL 层 `TABLE_MISSING` 阻断 D4。方向符合「小表关键」，级数比手册保守一级。
2. **`gpu.utilization` / `cpu.tasks` 删表未掉级**：当前 `score_dlevel_sql.py` 的 P3-SW 路径不查这两表；手册若期望掉级，需扩展判分或换 P3-EXT 类 case 做交叉验证。
3. **体积数字**：手册写 64 KiB/表、20 MB 大表；实测 full_fidelity 为 **~33 KiB/rank** 小表、**~19 MiB/rank** 大表（16 rank 合计见 `ABLATION_MATRIX.md`）。比例关系一致（小表 ≪1%，大表 ~18%/张）。

## 复现

```bash
cd project/probing-huawei/results/ascend-ais/pillar_c_v3/ablation
python3 run_ablation_matrix.py
```

预计耗时：~2 min（APFS `cp -c` 克隆 `probing_data`）。
