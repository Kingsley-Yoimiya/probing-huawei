# PARAM · ③-A 升采样率 D-level 增益

> case=`P3-SW-A` · parent=`20260727_014151-3a-p3-sw-a-loud` · 自变量=触发后 `probing.torch.profiling` rate
> 尺：采集侧 RSS / SET / torch_trace；**禁止**训练 step_ms；**禁止**只报 cold。
> rate≈0 端点挂 E4（`182630`）；不作默认结论，只作曲线一端。

## 结论：够归因 D4 的最小 rate\* = **`0.001`**

| rate | D | enough_D4 | RSS | SET | TT rows | TT ranks | note |
|------|---|-----------|-----|-----|---------|----------|------|
| 0 | D2 | N | Y | N | 0 | 0 | E4 hung rate≈0 endpoint (forbid SET) |
| 0.001 | D4 | Y | Y | Y | 81552 | 16 | rise_kb=224012:max_kb=2444412:n=200 |
| 0.05 | D4 | Y | Y | Y | 81552 | 16 | rise_kb=230980:max_kb=2443044:n=200 |
| 0.5 | D4 | Y | Y | Y | 81552 | 16 | rise_kb=234844:max_kb=2464028:n=200 |
| 1.0 | D4 | Y | Y | Y | 81552 | 16 | rise_kb=220512:max_kb=2458492:n=200 |

## 曲线要点

- **rate=0（E4）**：禁升详 → TT=0 → D2（RSS 周期小表仍在）；证明升精度是上根因层的必要机制。
- **rate≥0.001**：RSS∧SET∧TT>0 → 全部 D4；rate\*=0.001 为扫点内首次够归因。
- **诚实**：0.001/0.05/0.5/1.0 的 TT 环均满填（81552 行），本尺看不到 rate 梯度；跃迁主在有无 SET↑。
- SET 键：`probing.torch.profiling=`；scope=victim（多 rank 全升曾死锁，INVALID `012805`）。

## 这数据证明为什么这么设

对 P3-SW-A loud：常驻 rate=0 → 触发后 SET `probing.torch.profiling=on,rate=R`（scope=victim，SET@L≥100）。rate≈0 挂 E4 `182630`（禁 SET，TT=0，RSS 仍 Y）作必要性端点。扫 R∈{0.001,0.05,0.5,1.0} 均 RSS∧SET∧TT>0 → D4；够归因 D4 的最小 rate* = **0.001**。诚实：本轮各升详率下 python.torch_trace MEMT 环均满填（每 rank 5097 行 / 总 81552），行数尺无法区分 rate 梯度；曲线主跃迁在 0→0.001（有无 SET↑）。多 rank 全升曾死锁，正式扫改 victim-only。

## 证据路径

- `/Users/yinjinrun/Codespace/myportal/results/ascend-ais/param_calib/3A_upgrade_rate/PARAM.json`
- `/Users/yinjinrun/Codespace/myportal/results/ascend-ais/param_calib/3A_upgrade_rate/20260727_014151-3a-p3-sw-a-loud/`（AFS 镜像：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/param_calib/3A_upgrade_rate/20260727_014151-3a-p3-sw-a-loud`）
- E4 锚：`results/ascend-ais/pillar_c_v2` / AFS `…/20260726_182630-pillar-c-e4-p3-sw-a-loud`

