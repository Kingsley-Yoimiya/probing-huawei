# 任务卡 · Pillar-C Runner（数据量小 · 正式采集）

> **这是什么**：支柱 C（"数据量小"这条腿）编排的**第二张卡**——正式采集。跑三臂 + 三场景，产出
> **同覆盖数据量比**和三条系统曲线。**硬依赖 [`PILLAR_C_PILOT.md`](PILLAR_C_PILOT.md) 门禁全绿**。
>
> **方案真相源**：`project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md`（§2 主实验 + §3 三场景）。
> **方法论**：[`../rules.md`](../rules.md)；**环境**：[`../ledger.md`](../ledger.md)；**范式**：[`CASE_RUNNER.md`](CASE_RUNNER.md)。

## 硬前置（不满足不许开跑）

1. **读 `$LOCAL_RESULT_ROOT_BASE/_prep/pillar_c_gate/GATE.md`**：G1–G6 必须**全绿**。
   - G1 冷层默认开、G2 存储预算旋钮**有确切真名**——这两条是地基，任一为 N → **停，回 pilot 修**，不许拿假设旋钮名瞎跑。
   - 从 GATE.md 抄**三臂的确切旋钮值**（pilot 已填），本卡不重新猜。
2. `source project/probing-huawei/scripts/fail-slow/env.sh`；SYY kube；落盘 `yinjinrun.p-huawei`。
3. 确认目标 pod IDLE（`pgrep` 无活 torchrun）。

> **为什么这么严**：冷层没默认开 / 存储预算旋钮拨不动时，"塌缩臂数据量小"是假的（其实是没在留冷段），整条数据量比曲线作废。门禁是 C 的生命线。

## C 测什么（一句话，别跑偏成检测）

C **不判 D-level、不设计检测 SQL**。C 量的是**系统指标**：达到同样覆盖（同 D-level，复用 B 已判的分）时，**Probing 塌缩臂花的数据量/存储 vs 全量精采臂**。主尺 = **同覆盖下的数据量比**。

## 资源与多轨切分（关键：不抢 B 的池）

今晚默认：**C 用 `grj-megatron-32card-0716-worker-0`**（空闲借用，16 卡）；Dose 用 grj-master；对照用 yysong-w2。详见 RESOURCE.md。

| 轨 | 内容 | 能否并行 | 落点 |
|----|------|---------|------|
| **轨 C-1 · 三臂采集** | 同 case 跑 全量精采 / 塌缩 / 砍量 三臂 | 三臂**互不依赖**，但一个 pod 一次一条 16 卡训练 → **同 pod 内串行三臂**；**跨 case 可借错峰** | worker-0 |
| **轨 C-2 · 回溯窗扫预算** | 固定 case，扫 `COLD_MAX`=128/256/512… 长 run | 不同预算**互相独立** → 若有第二个空闲 pod 可并行；否则串行 | worker-0（或错峰借 B 空档 pod） |
| **轨 C-3 · 中途接入 + SET升保真** | 单 run 内**时序操作**（先跑→后 attach / 运行中 SET） | **不好切**，是一条 run 里的动作序列 | worker-0 |

> **诚实的并行边界**：C 的"多轨并行"主要靠**跨 case / 跨预算点**铺开，不是单个 run 内并行（单 run 是时序的）。若只有 worker-0 一个 pod，C 内部大体是**串行队列**；要真并行，需向用户申请错峰借 B 的空档 pod（B 某池 IDLE 时）——**这属于要批准的扩池，不自作主张抢**。

## 三臂配置（从 GATE.md 抄确切值，这里只给语义）

| 臂 | 采集策略 | 唯一变量 |
|----|---------|---------|
| **全量精采**（上界锚点） | `rate=1.0` + 采样最密 + 冷层无上限（G6+G4+G2 的确切值） | — |
| **Probing 塌缩**（我们） | 低保真常驻 + 触发后 `SET sample_rate↑` 局部升保真（G3） | 采集策略 |
| **朴素砍量**（反例臂） | 只低保真常驻、**禁**现采升保真 | — |

训练/seed/注入/窗全固定（继承 ledger §2.1 控变）；三臂唯一变量 = 采集策略。

## 用哪些 case（辩护够用，不铺 27 格）

方案 §2.3 的代表集（起病早 + 明显对照）：

| case | 类型 | C 里看什么 | 现有 D |
|------|------|-----------|--------|
| **P3-SW-A/B** 泄漏 | 渐进/host | 低保真够不够留住早期弱信号 | 华为 D4 已在 |
| **P1-SW-C** 编译尖刺 | 一次性/gpu | 塌缩会不会漏掉关键帧 | 沐曦 D3（昇腾看排期） |
| **P1-HW-B** HBM 渐衰 | 渐进 ramp | onset 早期斜率靠冷段回溯 | 华为 D3 已在 |
| **P1-EXT-A** | 明显（**阴性对照**） | 塌缩臂该和全量**打平**，不该有差距 | 华为 D2 已在 |

> 阴性对照必须在：证明塌缩没在简单 case 上引入无谓损失。

## 量什么（把"数据量"说死）

每臂每 case 记：
1. **常驻数据速率**：每 rank 每步字节数。
2. **达 D-level 时动用的数据量**：塌缩臂把"触发后升保真那段"字节算进去（公平口径）。
3. **常驻存储**：全程冷段总字节。
- **白盒佐证**：热层 `rows_overwritten`（onset 前窗应 =0 证明没丢）+ 冷段 `ColdStats`（留了多少）。数据量从工具内部计数器读，不外部估。

## 三场景产出（对齐 outline §5.2 Eval-C）

| 场景 | 做什么 | 产出 |
|------|--------|------|
| **中途接入回溯** | 跑到 step 300 才 attach → 查 150–300 历史 | 回溯窗长度 vs 对手重启代价 GPU-hours |
| **SET 升保真** | step 200 发现异常 → `SET rate=1.0` → D2 升 D4 | 生效延迟(<200ms) + D-level 增益 |
| **回溯窗 vs 预算** | 扫 `COLD_MAX` 跑 2000 步 | 回溯窗曲线（Probing 独有指标） |

## 允许 / 禁止

**允许**：修 Probing 通用能力把计数器读通；错峰借 B 空档 pod **需用户批准**；产物回拉本机 + 更新 ledger。
**禁止**：
- **门禁没绿就开跑**（头号禁令）。
- 抢 Dose/对照活作业；碰 a3；写宋 AFS / geruijun / grj-shared；对方在 grj 再现时不让路。
- 判 D-level / 设计检测 SQL（C 复用 B 判分，不重判）。
- 拿假设的旋钮名跑（必须从 GATE.md 抄实测值）。

## 产出（交还最小集）

| 产物 | 内容 |
|------|------|
| 数据量比 | 每 case：塌缩 vs 全量 的常驻速率/存储/达D动用量；落 `results/ascend-ais/pillar_c/<run_id>/` |
| 三场景曲线 | 回溯窗 vs 预算、SET 升保真延迟+增益、中途接入窗长 |
| 白盒佐证 | `rows_overwritten`/`ColdStats` 读数 |
| ledger 速览 | §3 加 "C 数据量" 区一行/case |
| 阻断 | `BLOCKED.md`（门禁未绿最可能） |

## 派发提示词骨架（给 Loop / 用户粘贴）

```text
你是「昇腾 Fail-Slow Pillar-C Runner」执行者。任务=C 正式采集（三臂数据量比 + 三场景曲线），不判 D-level、不设计检测 SQL。
【硬前置】先读 $LOCAL_RESULT_ROOT_BASE/_prep/pillar_c_gate/GATE.md：G1–G6 未全绿（尤其 G1 冷层默认开、G2 存储预算旋钮真名）→ 停，回 PILLAR_C_PILOT 修，不许拿假设旋钮名跑。三臂旋钮值从 GATE.md 抄，不重猜。
必读：EVAL-GAP-AND-PILLAR-C-PLAN.md（§2/§3）；agents/{PILLAR_C_RUNNER,PILLAR_C_PILOT,CASE_RUNNER,RESOURCE,CONCERNS}.md；rules/ledger。
source env.sh；SYY kube；跳板 kubectl=/root/.cache/volcano/kubectl/kubectl；C 用 yysong-worker-0，勿抢 master-0/worker-1/worker-2（B 在用）、勿碰 a3/grj；落盘 yinjinrun.p-huawei。
三臂（全量精采/塌缩/砍量）× 代表 case（P3-SW-A/B、P1-SW-C、P1-HW-B、P1-EXT-A 阴性对照）；量常驻速率/存储/达D动用量 + 白盒 rows_overwritten/ColdStats。三场景：中途接入回溯、SET升保真、回溯窗vs预算曲线。多轨主要靠跨case/跨预算铺开，单run内是时序不并行；要借B空档pod需用户批准。产物回拉 + 更新 ledger §3。守红线。
```

---

## 采集进度（执行 Agent 回填）

> 每 case × 每臂一行；未跑=留空。

| case | 臂 | 常驻速率 | 达D动用量 | 常驻存储 | rows_overwritten(onset前) | run_id |
|------|----|---------|----------|---------|--------------------------|--------|
| | | ⬜ | | | | |
