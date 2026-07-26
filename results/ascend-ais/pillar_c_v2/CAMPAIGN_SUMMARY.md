# Pillar C v2 · 战役摘要（2026-07-26）

> 方案：`project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md`  
> 队列：`docs/fail-slow/PILLAR_C_QUEUE.md`  
> 产物根：`results/ascend-ais/pillar_c_v2/`  
> 旧 `pillar_c/*/VOLUME_RATIO.md`（cold 三臂）= **SUPERSEDED**。

## 头条（可写论文）

| 主张 | 数字 / 结论 | 锚点 |
|------|-------------|------|
| 同覆盖下数据量小 | **动态/全量 = 72.6%**（W\*=100 content est；on-disk raw 90.16%） | E3 `181423` P3-SW-A |
| 常驻可极稀 | 够触发最稀 rate = **0** | E2 `173134` |
| 设计追溯窗 | W\* = **100**（E1-off；正式 E1 未复现不推翻） | E1-off `W_STAR.md` |
| 省量须配升详 | 砍量禁 SET → path_enough **掉级**（TT 0 vs 54054） | E4 `182630` |
| 中途接入代价 | 热接入 restart=**0** vs 对手≈**150** 步；冷启动晚接入 **不见 onset 前** | S1 `184311` |

## 流水线状态

| ID | 状态 | 关键 run |
|----|------|----------|
| C0 | ✅ rate=0 + SET→live | `c0_mech_20260726_172201` |
| E1-off | ✅ W\*=100 @P1-SW-C | `pillar_c_v2/E1_off/` |
| E1 | ✅ 收口 NO_W_STAR（旧 SET 键） | `173830` |
| E2 | ✅ BOUNDARY 0 | `173134` |
| E3 | ✅ 72.6% | `181423` |
| E4 | ✅ PASS 掉级 | `182630` |
| S1 | ✅ PASS_ATTACH_NO_PRE_ONSET | `184311` |

## 机制教训（已修 / 标定）

1. SET 真相键：`probing.torch.profiling=`（勿 `torch.profiling=`）
2. jexec PATH 须含 `/usr/bin:/bin`
3. grj：`POD_RESULTS` = AFS（无 `/data/...`）
4. SET 须打 **全部** worker（勿首 pid 后 `break`）
5. 环 20MB ≈ **546** 步；只留已采史，不能发明 attach 前样本
6. P3 主证常在周期 `cpu.utilization` RSS，不依赖全 rank torch_trace

## 可选补测（非阻塞）

- attach ≤ onset 正对照（证「接入后仍见 onset 前」）
- E3 多 rank 全量升详后重算 raw %（当前 1/16 TT）
- P1-EXT-A 阴性；P3/HW 正式 W\*；online「只留最近 W 步」API

## 喂下游

- Eval「数据量小」腿：E3 72.6% + E4 反例 + E2 rate=0  
- Eval-A / 时间维：S1 restart=0 vs ≈150  
