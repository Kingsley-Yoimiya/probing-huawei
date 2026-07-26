# 任务卡 · Pillar-C Pilot（数据量小 · 机制门禁验证）

> **这是什么**：支柱 C（时间保留 / "数据量小"这条腿）编排的**第一张卡**——**机制门禁验证**。
> C 的整套"数据量比 + 回溯窗 + 升保真"实验都依赖几个 Probing 机制旋钮真的能拨。本卡先把它们
> **验证一遍、产出一张可用性表**，全绿才谈正式采集。**不绿就先修工具（属通用能力，非作弊）**。
>
> **方案真相源**：`project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md`。
> **方法论**：[`../rules.md`](../rules.md)；**环境事实**：[`../ledger.md`](../ledger.md)；**编排范式**：本目录 B 的 [`CASE_RUNNER.md`](CASE_RUNNER.md)/[`CONCERNS.md`](CONCERNS.md)。

## 为什么 C 的编排比 B 简单（先讲清，免得照搬 B 的重流程）

| 维度 | B（Case Runner） | C（本卡及后续） |
|------|------------------|-----------------|
| 要不要设计检测 SQL | 要（探索→冻结，防红线 2 把答案焊进去） | **不要**——C 测的是数据量/存储/回溯窗，是**系统指标**，判据是工具内部计数器，读出来即可 |
| 红线 2 负担 | 重（检测不准出现答案） | **几乎没有**——不写检测逻辑 |
| case 规模 | 27 格 × 多档 × 多工具 | **几个代表 case**（辩护够用，见方案 §2.3） |
| D-level 判分 | 每 case 现场判 | **复用 B 已有判分**；C 不重判 D-level |
| C 特有的前置 | — | **本卡**：机制旋钮门禁（B 没有，因为 B 不碰冷层/存储预算/升保真） |

> 一句话：**C = B 的骨架 − 检测探索 − 27 格队列 + 本卡（机制门禁）+ 三臂采集配置。** 本轮只交付本卡。

## 身份 / 落盘 / 资源（继承 env.sh，勿另立）

- `source project/probing-huawei/scripts/fail-slow/env.sh`；进集群 **SYY**；落盘 **`yinjinrun.p-huawei`** → `$LOCAL_RESULT_ROOT_BASE`（默认 `results/ascend-ais/`）。
- 跳板 kubectl=`/root/.cache/volcano/kubectl/kubectl`；hold-exec 默认 **`grj-megatron-32card-0716-worker-0`**（空闲借用）。
- **本卡可单卡小跑**：门禁是短跑；勿抢 Dose 的 grj-master；**仍不碰 a3**；grj 对方训练再现则让路。
- grj 环境：`POD_BUNDLE` / `POD_RESULTS` 见 RESOURCE.md（无 `/data/yinjinrun.p-huawei`）。
- 结果落 `$LOCAL_RESULT_ROOT_BASE/_prep/pillar_c_gate/`。

## 门禁清单（逐条验，全绿才进正式采集）

> 每条记：**能拨吗（Y/N）+ 确切旋钮名/路径 + 默认值 + 怎么读出来**。这张表本身就是本卡的头号产出。

| # | 门禁项 | 为什么关键（不绿则 C 塌） | 怎么验 | 已知线索（源码） |
|---|--------|--------------------------|--------|------------------|
| **G1** | **冷层 memc 默认启用？** | 塌缩臂靠冷段"便宜地一直留"；冷层没开 = 环形一覆盖 onset 前就没了，数据量比假优、回溯窗假低 | 起最小 Probing run，查冷段是否真在写：看 `cold_dir()` 落点有无 sealed 段产生 | `probing/core/src/core/memtable_sql.rs`：`cold_dir()`、`HotColdTable::new(ring, cold_dir(), …)`、`cold_scan(...)`——**冷热已接进 SQL 查询路径**；默认路径由 `cold_dir()` 决定，**需实测确认默认开** |
| **G2** | **存储预算旋钮真名 + 默认值**（头号未知） | §3 场景三"回溯窗 vs 存储预算"要扫这个；主尺"数据量"也要它封顶 | 找到设 cold 总量上限的**确切环境变量/SET 名**，设小值验证冷段被限流 | ledger 提过 `PROBING_COLD_MAX_TOTAL_MB` 但**源码未搜到该名**——**pilot 必须查清真名**（可能在 memc store/compactor 配置里，或另一个名）；查不到就记 BLOCKED 交还 |
| **G3** | **SET 热更采样精度可用？** | §3 场景二"按需升保真"的机制；升不上去则前瞻这条腿断 | 运行中执行 `SET probing.sample_rate=<x>`，确认采样率即时变、无需重启 | `probing/server/src/mcp/mod.rs:518` 在测 `SET probing.sample_rate=0.1` 语法——**语法真实存在**，需实测热更生效 + 测生效延迟（目标 <200ms） |
| **G4** | **采样间隔可拨？** | 三臂（全量/塌缩/砍量）靠不同采样密度区分 | 设 `gpu.sample_interval`（别名 `sample_interval`/`interval`）验证生效 | `probing/extensions/gpu/src/extensions/extension.rs`：option 别名 `["sample_interval","interval","gpu.interval"]` |
| **G5** | **覆盖/保留计数器可读？** | 数据量比的**白盒佐证**：`rows_overwritten` 证明 onset 前"一行没丢"，`ColdStats` 证明留了多少 | 查这两个计数器能否通过 SQL 表 / API 读出 | `memtable/src/discover.rs:195` `ring_overwrite_stats()→(chunks_recycled,rows_overwritten)`；`memtable_sql.rs:1260` `stats()→ColdStats`——**需确认有对外读取入口**（SQL 表还是仅内部） |
| **G6** | **torch profiling rate 可设**（全量精采臂需要） | 全量精采臂 = `rate=1.0`；MetaX/昇腾上 import 期 SET 曾 SIGSEGV（ledger 门禁 #3） | 显式 `PROBING_TORCH_PROFILING=on:rate=1.0` 起训不炸 | ledger §1.2/§1.3：**勿默认开**，要用得显式 `rate=1.0`；昇腾需实测不 SIGSEGV |

## 三臂配置定义（门禁绿后正式采集用；本卡先把配置写死备用）

> 唯一变量 = 采集策略；训练/seed/注入/窗全固定（继承 B 的控变，ledger §2.1）。

| 臂 | 采集配置（门禁确认后填确切旋钮值） | 预期 |
|----|-----------------------------------|------|
| **全量精采**（上界锚点） | `rate=1.0` + 采样间隔最密 + 冷层无上限（G6+G4+G2） | 数据量大、达 D4 |
| **Probing 塌缩**（我们） | 低保真常驻 + 触发后 `SET sample_rate↑` 局部升保真（G3） | 数据量小、也达 D4 |
| **朴素砍量**（反例臂） | 只低保真常驻、**禁**现采升保真 | 数据量小、覆盖掉到 D2/D3 |

## 允许做 / 禁止做

**允许**：
1. 起最小 Probing run（单卡/单进程短跑）探旋钮；改 **Probing 通用能力**把旋钮/计数器读通（属修工具，非作弊）。
2. 找不到 G2 存储预算真名 → 记 `BLOCKED.md`（现象/已试/需要什么），交还，不臆造变量名。
3. 冷层默认没开 → 记录如何开（配置/环境变量），这是合法的通用能力修复。
4. 产出**旋钮可用性表** + 三臂配置的确切旋钮值，写回本卡下方"实测结果"区 + ledger。

**禁止**：
- 碰 `a3-megatron-*`；占 Dose 的 grj-master 跑门禁；对方在 grj 再现时不让路。
- 写宋 AFS / geruijun / `results/muxi-h3c/`。
- 把门禁"验证不了"包装成 `ENV-BLOCKED`——未穷尽先记 `PENDING`（红线 5）。
- 在本阶段设计任何检测 SQL 或判 D-level（那是 B 的事；C 复用 B 判分）。

## 产出（交还的最小集）

| 产物 | 内容 |
|------|------|
| **旋钮可用性表** | G1–G6 逐条：Y/N + 确切名 + 默认值 + 读法；落 `_prep/pillar_c_gate/GATE.md` |
| 三臂配置 | 每臂的确切旋钮值（可直接喂正式采集脚本） |
| ledger 速览 | 新增"C 门禁"一行：全绿/卡在哪 |
| 阻断（若有） | `BLOCKED.md` 三行：现象 / 已试 / 需要 Loop 或用户做什么（G2 真名查不到最可能） |

## 成功标准（本卡）——三问（仿 CONCERNS §2，为 C 改写）

1. **(a) 机制在不在**：G1 冷层默认开、G2 存储预算旋钮找到真名——这两条是 C 的地基，任一为 N 则 C 主实验需先修工具。
2. **(b) 旋钮拨得动**：G3–G6 逐条 Y，且记录了确切名/默认/读法。
3. **(c) 佐证读得出**：G5 `rows_overwritten`/`ColdStats` 有对外读取入口——数据量比才有白盒铁证，不靠外部估。

## 与后续 C 编排的关系

- 本卡绿 → 才写 **PILLAR_C_RUNNER**（三臂正式采集 + 三场景）与是否需要独立 Loop（本轮不决定，先验证机制）。
- C 不进 B 的 CASE_QUEUE/CONTRAST_QUEUE（不同性质）；后续给 C 单列一个轻队列或直接手派，视门禁结果定。

## 派发提示词骨架（给 Loop / 用户粘贴）

```text
你是「昇腾 Fail-Slow Pillar-C Pilot」执行者。任务=验证 C（数据量小）依赖的 Probing 机制旋钮门禁，不做正式采集、不判 D-level、不设计检测 SQL。
必读：project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md；project/probing-huawei/docs/fail-slow/{rules,ledger}.md + agents/{PILLAR_C_PILOT,CONCERNS,RESOURCE}.md。
source project/probing-huawei/scripts/fail-slow/env.sh；SYY kube；跳板 kubectl=/root/.cache/volcano/kubectl/kubectl；hold-exec 在 yysong-*（门禁是单卡小跑，勿占 master-0 大卡、勿碰 a3/grj）；落盘 yinjinrun.p-huawei。
逐条验 G1–G6（冷层默认开？存储预算旋钮真名？SET sample_rate 热更？采样间隔？rows_overwritten/ColdStats 可读？torch rate=1.0 不炸？），产出旋钮可用性表 GATE.md + 三臂配置的确切旋钮值。G2 存储预算真名源码未定位——查清；查不到记 BLOCKED，勿臆造。全程守红线5（未穷尽不写 ENV-BLOCKED）。结束更新 ledger 一行 + 回传 BLOCKED.md（若有）。
```

---

## 实测结果（执行 Agent 回填）

> 门禁验证跑完后，把 G1–G6 的实测填这里 + `_prep/pillar_c_gate/GATE.md`。未跑=留空。

| # | 项 | Y/N | 确切名 / 默认 / 读法 | 备注 |
|---|----|-----|---------------------|------|
| G1 | 冷层默认启用 | **Y** | **默认开**（unset）；`PROBING_COLD=off` 可关；读 `$DATA/<pid>/cold/*.memc` | `g1_fix_20260725_225835` unset segs=23 / off=0；见 GATE + `G1_FIX.md` |
| G2 | 存储预算旋钮 | **Y** | `PROBING_COLD_MAX_TOTAL_MB` / `memtable.cold_max_total_mb`；默认无限 | 源码+SET 读回 |
| G3 | SET sample_rate 热更 | **Y** | **无** sample_rate；用 `torch.profiling`；SQL SET 可读回 | keyfix 证实 |
| G4 | 采样间隔可拨 | **Y** | ENV `PROBING_GPU_SAMPLE_MS`；SET `gpu.gpu_sample_interval_ms`；已设后不可再改 | 三臂分进程 |
| G5 | 覆盖/保留计数器可读 | **Y\*** | FS：MEMT@60 `rows_overwritten`；冷目录 ColdStats 同形字段；**无** SQL 表 | \*=FS 非 SQL |
| G6 | torch rate=1.0 不炸 | **Y** | 用 `on,rate=1.0` 或 `on:1.0`；勿用 `on:rate=1.0` | 短步 SUCCESS |

> 总判 **全绿**（G1=Y；G5=Y\* FS）。真相源：`results/ascend-ais/_prep/pillar_c_gate/GATE.md`。本收口未开 Runner。
