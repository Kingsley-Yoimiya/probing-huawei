# Fail-Slow 台账 — 华为昇腾（Ascend / AIS）

> **这份文档是什么**：华为侧**会变的执行现状**——身份、门禁、剂量、已跑 case。Agent 边跑边改。
>
> **和规则的分工**：方法论见同目录 [`rules.md`](rules.md)。沐曦台账在 `project/probing-test/docs/fail-slow/ledger.md`——**两份分开维护，禁止把昇腾 run 写进沐曦台账或 `results/muxi-h3c/`**。
>
> **故障定义**：论文 OUTLINE 27-case；排期见 [`CASE_QUEUE.md`](CASE_QUEUE.md)。

---

## 维护纪律

- 改「§2.1 全局固定」任一格 = 之前所有昇腾 run 不可比 → 先停、写清理由再改。
- case **不写预写检测文档**；检测方案探索后冻结进脚本，速览只记本文件 §3。
- 借 `songyiyang.p` **只用于进集群**；落盘、作业前缀、镜像 secret 一律 `yinjinrun.p-huawei`。**禁止**碰 `yysong-*` / `songyiyang` AFS / `/afs-a3-241ceshi-shared/yysong`。

---

# 一、环境

## 1.1 集群与身份

| 项 | 值 |
|---|---|
| 集群 | `vc-a3-241ceshi`（华为 AIS） |
| 进集群身份（借用） | `songyiyang.p`（SYY）— `config/identity/songyiyang.p-huawei.yaml` |
| shortcut | `config/shortcuts/huawei-ais-syy.yaml` |
| 本机 kube | `~/.kube/config-vc-a3-241ceshi-songyiyang.yaml`（alias `config_vc-a3-syy.yaml`） |
| 跳板 | `ais-cf3e61a5`；kube=`/tmp/config-vc-a3-241ceshi-songyiyang.yaml`；**kubectl=`/root/.cache/volcano/kubectl/kubectl`**（PATH 无 kubectl） |
| 落盘身份 | **`yinjinrun.p-huawei`**（不随借用 kube 改变） |
| AFS | `/afs-a3-weight-share/yinjinrun.p-huawei/{lab-workspace,results,probing-huawei}` |
| 卡面（2026-07-24 实测） | `huawei.com/Ascend910` 可分配合计 **128**（8 节点 × 16） |
| **我们管的 64** | **`yysong`**（submitter=`songyiyang.p`，4×16）；hold-exec，直接用 |
| 他人（勿碰） | `a3-megatron-32card`（张文胜）、`grj-megatron-32card-0716`（葛瑞君） |
| 默认实验规模（Case） | **16 卡** on `yysong-master-0`；标签 `yjr-as-c-*` |
| 代理 | 本机经 Clash 到跳板；跳板上操作集群时 `unset ALL_PROXY` |

默认自有身份 shortcut 仍是 `huawei-ais`（`yinjinrun.p`）。**需要 64/128 卡面时用 SYY**。

## 1.2 镜像与 Probing 版本

| 项 | 值 / 状态 |
|---|---|
| 训练镜像 | 候选 `registry2.d.pjlab.org.cn/ccr-geruijun/mindspeed-llm-megatron-0.12.1:v1` （2026-07-24 仍见于 Running：`a3-megatron-32card` / `grj-megatron-32card-0716`；pullSecret `default/imagesetup` 可用；**未自验**） |
| Probing 包 | 本仓 `project/probing-huawei` 构建的 wheel；`PROBING_GPU_BACKEND=npu` |
| AFS 代码树 | `/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei` |
| host 注入工具 | `stress-ng` / `fio`（镜像内需确认） |
| 编排脚本真相源 | `project/probing-test/scripts/fail-slow/` + `platform/ascend/` |
| 本仓薄包装 | `project/probing-huawei/scripts/fail-slow/`（`env.sh` / dose） |

## 1.3 门禁清单（正式 run 前逐条过）

| # | 检查项 | 状态 | 备注 |
|---|---|---|---|
| 1 | 跳板 SSH `ais-cf3e61a5` 通 | ✅ | 2026-07-24 |
| 2 | SYY kube 在跳板 `/tmp/config-vc-a3-241ceshi-songyiyang.yaml` | ✅ | 已 sync |
| 3 | `auth can-i get/create pods` = yes | ✅ | user=`songyiyang.p` |
| 4 | Ascend910 可分配合计可见 | ✅ | 128 |
| 5 | **用 `yysong` 跑**；**不碰** a3/grj；不写宋 AFS | ✅ | 借权=用宋发射的 64 卡作业 |
| 6 | 自有 AFS 可写（pod 内 → yinjinrun.p-huawei） | ✅ | pod 内 `/data/yinjinrun.p-huawei` |
| 7 | 镜像内 torch_npu + HCCL + `npu-smi` | ✅ | llm_test；torch_npu 2.7.1；HCCL ok |
| 8 | Probing wheel 可 import + SQL 有值 | ⬜ | 镜像尚无 probing 包 |
| 9 | hold-exec 池：Case=master；GH=w1；XPU=w2 | ✅ | RESOURCE.md |
| 10 | `NO_PROXY` / `unset ALL_PROXY`（跳板侧） | ⬜ | |
| 11 | 跳板 kubectl 绝对路径可用 | ✅ | `/root/.cache/volcano/kubectl/kubectl` |

## 1.4 平台 know-how（探索后填；勿把答案写进检测 SQL）

| 主题 | 现状 |
|---|---|
| NPU util / 功率 / 温度 | 本仓 DCMI → 回退 `npu-smi`；表名仍 `gpu.*`，`backend=npu` |
| HCCS | `gpu.hccs`；昂贵 `hccs-bw` 默认关 |
| P1 D4 旁路 | 优先 SQL；不足则同窗 `npu-smi` → `host_npu_smi_*`（仿沐曦 mx-smi 旁路） |
| P3 D4 旁路 | 同窗 `/proc/pressure` → `host_psi_*`；勿假设与 MetaX 同走 memory PSI |
| HCCL vs NCCL | baseline / FR 变量名见 `platform/ascend/`；**勿假设 nccl\* 符号** |
| PFC | 多数 Pod 无计数器 → `pfc_available=0` 属预期 |

## 1.5 结果落盘

| 位置 | 路径 |
|---|---|
| 本机主备份 | `$LOCAL_RESULT_ROOT_BASE/<run_id>/`（默认本仓 `results/ascend-ais/`；**不依赖 myportal**） |
| AFS（若挂上） | `/afs-a3-weight-share/yinjinrun.p-huawei/results/<run_id>/` |
| 索引 | `results/ascend-ais/INDEX.md` |

每 run 至少：`manifest.yaml`、`training/*.jsonl`、`injection/`、`probing/`、`verdict/`、`system/env_snapshot.yaml`。

---

# 二、控制变量（具体设置）

## 2.1 全局固定 · 控制变量 ⚠️

> 起步对齐沐曦 GPT-2 124M 管线；昇腾上若必须改 dtype/batch，**改之前先停**并记理由。

| 参数 | 值 | 备注 |
|---|---|---|
| 模型 | GPT-2 124M | 与沐曦可比时保持 |
| batch / seq | 8 / 1024 | 待 smoke 确认 OOM |
| dtype | bfloat16（若 NPU 栈要求改 fp16，记此处） | |
| seed | 42 | 正式扫 42/43/44 |
| iters / warmup | 500 / 50 | |
| DataLoader | 开；host 类前提 | |
| Checkpoint | 每 100 步 | |
| 通信 | HCCL（MindSpeed / torch_npu） | |

## 2.2 每 run 变化 · 自变量

| 自变量 | 取值 |
|---|---|
| case_id | 27 格之一 |
| 剂量档 | Loud / Quiet / Masked |
| 检测工具 | 无 / Probing / Greyhound / XPUTimer / Dynolog / FR / … |
| 规模 | 8 / 16 / … / 128 |

平行 run 标签同沐曦：`C0` 健康 / `C1` 纯注入 / `C2` 注入+Probing。

## 2.3 注入时序（默认）

| 参数 | 值 |
|---|---|
| warmup | 前 50 步 |
| N_inject | 150（窗起对齐全局 step 150） |
| 注入持续 | 200 步（内部窗 [100,300]） |
| victim | `sidecar_local_rank=7`（除非节点卡数不足） |

剂量真相源：`scripts/fail-slow/dose_recipes.yaml`（本仓；起步从沐曦抄骨架，**未标定**）。

---

# 三、测试用例（cases）

## 3.1 已跑 case 速览

| Case | 名称 | 注入 | 模式 | Loud C1/C0 | 到达 D | run_id / 备注 |
|---|---|---|---|---|---|---|
| P1-EXT-A | 同卡算力 | **inline_cube** | gpu_bound | **3.87** | **D2** | `20260725_011129` INLINE 8192×mm64；C1/C0=3.87 PASS；offline+SQL **D2**（D3 rank_4≠7）；DUMP_OK |
| P1-EXT-B | 同卡带宽 | **inline_hbm** | gpu_bound | **2.02** | **D3** | `20260725_014350` INLINE 512MB×copies48；C1/C0=2.02 PASS；offline **D3**（min_compute→rank_7）；SQL attach 失败不升 D4；dose calibrated |
| P3-EXT-A | 抢 CPU | stress_cpu | host_bound | **1.97** | **D3** | C2 `20260725_001251-yjr-as-c-p3-ext-a-loud`；Loud `20260724_231918`；证据 `data_ms`/onset；SQL dump PATH 失败→D4 SQL_PENDING |
| P3-EXT-B | 抢磁盘 IO | **stress_io/fio** | host_bound | **2.13** | **D3** | `20260725_020212` fio nj16+ckpt20+pread；C1/C0=2.13 PASS；SQL attach/PSI 未升 D4；dose calibrated |
| P3-EXT-C | 抢内存带宽 | **stress_vm** | host_bound | **1.59** | **D3** | `20260725_021906` vm 96×6G；C1/C0=1.59 PASS；PSI_UNAVAIL（无 /proc/pressure）；SQL attach 失败不升 D4；dose calibrated |
| P3-SW-A | 对象泄漏→GC | 8a inline | host_bound | **2.93** | **D4** | Loud+C2 `20260725_012957-yjr-as-c-p3-sw-a-loud`；证据 `data_ms`/onset + SQL `cpu.utilization_rss`；stall=0.25 calibrated |

## 3.2 baseline 状态

| 工具 | 状态 | 备注 |
|---|---|---|
| Greyhound | **S4_DETECT** | **yysong-worker-1**；P3-EXT-A 对照 `yjr-as-b-gh-s4-20260725_002805/`；dose_OK(1.94) 但自主 no_bite（coll=1.058 / n_cp=0）。Rbeast 已升级喂**真实 per-rank 序列**+C0 假阳性对照（`collect_seq.py`），acf_period≈8，待 pod 重跑刷新产物 |
| XPUTimer | **S4_DETECT** | **yysong-worker-2**；P3-EXT-A 对照 `yjr-as-b-xpu-s4-20260724_233105/`；**自主 flags=0**（hang/slow 在 C0/C1 均 0）；跨-run 中位比 C1/C0=1.032 **无咬合**；`detect_mode=cross_run_contrast`（原误标 autonomous 已纠）；公平性：SLOW 阈按 C0 p99.9 冻结仍 0.99×，host CPU 抢占结构性不可见 |
| Dynolog | PENDING | |
| Flight Recorder | PENDING | |
| StragglerAnalysis | PENDING | |

## 3.3 判分证据口径（探索后填）

| Case 族 | 证据字段（草稿） |
|---|---|
| P1-EXT | `gpu.utilization` / `host_npu_smi_*` |
| P3-EXT | `host_psi_*` / `cpu.utilization`（本核常 **PSI_UNAVAIL**；旁证 loadavg + `data_ms`） |
| P3-SW | `cpu.utilization_rss` 等（对齐沐曦后再定） |

---

# 四、Agent 双轨（编排）

| 轨 | 文档 | 状态落点 |
|---|---|---|
| Loop 状态机 | [`agents/LOOP.md`](agents/LOOP.md) | `results/ascend-ais/_prep/LOOP_LAST.md` |
| Case 16 卡 | [`agents/CASE_RUNNER.md`](agents/CASE_RUNNER.md) | CASE_QUEUE + 本文件 §3.1 |
| Greyhound | [`agents/BASELINE_GREYHOUND.md`](agents/BASELINE_GREYHOUND.md) | `results/ascend-ais/baseline/greyhound/STATUS.md` |
| XPUTimer | [`agents/BASELINE_XPUTIMER.md`](agents/BASELINE_XPUTIMER.md) | `results/ascend-ais/baseline/xputimer/STATUS.md` |
| 共通原则 | [`agents/BASELINE_COMMON.md`](agents/BASELINE_COMMON.md) | 采集 / 检测 两轴分开记 |

Case 本阶段只跑 Probing；Baseline 未达 S2 **不阻塞** 27-case。对照波次另开。

# 五、变更记录

| 日期 | 变更 |
|---|---|
| 2026-07-24 | 台账初建；SYY 门禁 1–4 绿；卡面 128；目录落在 probing-huawei |
| 2026-07-24 | 双轨 Agent 边界包；Case=16 卡；Baseline 另池适配 |
| 2026-07-24 | Case Runner 再探 P3-EXT-A：空闲仍 0；新 BLOCKED `20260724_225135-p3exta-blocked`；CASE_QUEUE 备注更新 |
| 2026-07-24 | **再纠正**：我们的 64=`yysong`（SYY 借权作业）；a3/grj 是他人勿碰；hold-exec 改落 yysong |
| 2026-07-24 | P3-EXT-A Loud pilot **LOUD_OK**：C1/C0=1.97；dose loud calibrated；AFS=`/data/yinjinrun.p-huawei`；缺 Probing wheel 未 C2 |
| 2026-07-24 | XPUTimer 达 **S2_COLLECT**（2-rank HCCL，80 events/rank）；STATUS 见 `baseline/xputimer/` |
| 2026-07-24 | XPUTimer 达 **S3_RULE**（SLOW+HANG oracle；peer-desync 未稳）；等 Case LOUD_OK 再派 S4 |
| 2026-07-24 | XPUTimer **S4_DETECT** P3-EXT-A：coll host-wall C1/C0=1.032 无咬合；证据 `baseline/xputimer/yjr-as-b-xpu-s4-20260724_233105/` |
| 2026-07-24 | Greyhound 达 **S2_COLLECT**（collect-min 336 行 JSONL）；Redis 缺记 PENDING |
| 2026-07-24 | Greyhound **S3_RULE**（Redis+ACF）；Rbeast aarch64 仍 PENDING；证据 `yjr-as-b-gh-s3-20260724_235443/` |
| 2026-07-25 | P3-SW-A Loud **SCORED D4**：C1/C0=2.93@`20260725_012957`；inline 8a stall=0.25 calibrated；证据 data_ms+RSS SQL |
| 2026-07-25 | P3-EXT-B Loud **PASS** C1/C0=**2.13**@`20260725_020212`；C2 SQL_NO_EXT_EVIDENCE → **SCORED D3**；fio+ckpt/payload 标定成功（沐曦曾 ineffective） |
| 2026-07-25 | P1-EXT-A Loud sidecar **INEFFECTIVE** C1/C0=1.00@`20260725_004124`（进程隔离）；改 INLINE；BLOCKED 多 Agent 抢 master |
| 2026-07-25 | P1-EXT-A INLINE Loud **INEFFECTIVE** C1/C0=1.06@`20260725_010451`（size=4096,mm=16；`INLINE_CUBE_ALLOC`）；hold_exec npu-smi set -u 已修；未 C2；不进分母 |
| 2026-07-25 | P1-EXT-A INLINE Loud **SCORED D2**：`20260725_011129` dose=8192×64 C1/C0=3.87 PASS；C2×16 + SQL DUMP_OK；D3 定位失败不升；dose calibrated；末次加剂 |
| 2026-07-25 | P3-EXT-A **SCORED D3**：wheel 0.2.6；C2 `20260725_001251-yjr-as-c-p3-ext-a-loud`；SQL dump PATH 失败→D4 SQL_PENDING（hold_exec 已修） |
| 2026-07-25 | Case Runner：本仓 aarch64 wheel 装入 `yinjinrun.p-huawei`（`wheels/`+`llm_test`+`pydeps`）；修 `hccs_collector` 死 stub；hold_exec/dump 已可采 host_psi。D4 复测因与 P1-EXT-A 抢 `master-0` 未跑完；**误 pkill 打断** P1 C0 `20260725_003404`（pod 现 IDLE，需 P1 自重试） |
| 2026-07-25 | Case Runner 补 C2/D4：wheel import_ok；D4 复测被 `yysong-master-0` 上 P1-EXT-A Loud（`20260725_003404`）占卡打断；维持 SCORED D3 |
| 2026-07-25 | P1-EXT-A Loud **INEFFECTIVE**：`20260725_004124` C0/C1×16 jsonl；C1/C0=1.00；`003404` 训练死→同 case 重跑；未叠第二 Loud；未碰 a3/grj；C2 未跑 |
| 2026-07-25 | Greyhound Rbeast 通：`detect_ok=yes`（oracle）；cyclecounter stub；证据 `yjr-as-b-gh-s3-rbeast-20260725_000302/` |
| 2026-07-25 | Greyhound **S4_DETECT**：dose_OK(1.94) 自主 no_bite；证据 `yjr-as-b-gh-s4-20260725_002805/` |
| 2026-07-25 | P1-EXT-B INLINE Loud **SCORED D3**：`20260725_014350` dose=512×48 C1/C0=2.02 PASS；C2×16；SQL attach 失败→D4 未升；dose calibrated；未走外挂 sidecar |
| 2026-07-25 | P3-EXT-C Loud **SCORED D3**：`20260725_021906` stress_vm 96×6G C1/C0=**1.59** PASS；C2×16；PSI_UNAVAIL + SQL attach 失败不升 D4；dose calibrated；hold_exec 接线 stress_vm |
| 2026-07-25 | **Baseline 公平性修正（审查后）**：① XPUTimer S4 `autonomous`→`cross_run_contrast`——它自主 flags(hang/slow)=0，中位比需外部 C0，非 run 内自主；`≥1.5×C0med` 计数降为噪声诊断（C0 自身 10327 误报）；公平性核验 SLOW 按 C0 p99.9 冻结仍 0.99×，host CPU 抢占结构性不可见。② Greyhound ACF 从人造 call_id(i%4/恒0) 改喂**真实 per-rank 序列**（`collect_seq.py`：pid 分 rank、(op,count)→call_id、真实 t0）+ C0 假阳性对照；本机验证 period≈8。均为「给对手它自己最佳算法」的公平性修工具，**不改对手判据阈值、不写 case 答案**（rules §三·五A / 红线2）；结论方向不变（两者 P3-EXT-A 仍无咬合） |
