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
| 他人作业 | `a3-megatron-32card`（张文胜，**仍禁止**）；`grj-megatron-32card-0716`（葛瑞君，**2026-07-25 起可空闲借用**，让路规则见 RESOURCE） |
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
| 5 | 主池 `yysong`；**可空闲借 grj**；**不碰 a3**；不写宋/对方 AFS | ✅ | 2026-07-25 修订 |
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
| P1-EXT-A | 同卡算力 | **inline_cube** | gpu_bound | **3.87** | **D2** | `20260725_011129` INLINE 8192×mm64；C1/C0=3.87 PASS；offline+SQL **D2**（D3 rank_4≠7）；DUMP_OK；**Quiet formal SCORED D3** C1/C0=**1.156**@`20260726_013034`（size=4096 mm=32；pilot`012545`=1.159；offline D3 min_wait→rank_7；SQL_NO_EXT_EVIDENCE）；**Masked formal SCORED D3** C1/C0=**1.078**@`20260726_014611`（size=4096 mm=16；pilot`014128`=1.078；offline D3 min_wait→rank_8 ±1 hit；SQL_NO_EXT_EVIDENCE） |
| P1-EXT-B | 同卡带宽 | **inline_hbm** | gpu_bound | **2.02** | **D3** | `20260725_014350` INLINE 512MB×copies48；C1/C0=2.02 PASS；offline **D3**（min_compute→rank_7）；SQL attach 失败不升 D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.161**@`20260726_033758`（mb=256 copies=16；pilot`033227`=1.174；stub`032111` mb=128×3→1.015 ineffective）；GH quiet `035533` detect_ok=yes；XPU quiet `040543` detect_ok=no；**Masked formal SCORED D3** C1/C0=**1.070**@`20260726_040309`（mb=192 copies=10；pilot`035440`=1.075；offline D3+SQL_NO_EXT；GH masked `042310` detect_ok=no；XPU masked `042922` detect_ok=no） |
| P3-EXT-A | 抢 CPU | stress_cpu | host_bound | **1.97** | **D3** | C2 `20260725_001251-yjr-as-c-p3-ext-a-loud`；Loud `20260724_231918`；证据 `data_ms`/onset；SQL dump PATH 失败→D4 SQL_PENDING；**Quiet formal SCORED D3** C1/C0=**1.256**@`20260726_075912`（128@70；pilot`074315`=1.674；stub`074957`=1.131 FAIL_WEAK 保留；offline D3+SQL_NO_EXT；GH quiet `080959` detect_ok=no；XPU quiet `081751` detect_ok=no）；**Masked formal SCORED D3** C1/C0=**1.470**@`20260726_094648`（128@70＝quiet lean；pilot`083742`=1.073；offline D3+SQL_PENDING；DUMP=0 避 attach 挂；GH masked `095858` detect_ok=no；XPU masked `100915` detect_ok=no） |
| P3-EXT-B | 抢磁盘 IO | **stress_io/fio** | host_bound | **2.13** | **D3** | `20260725_020212` fio nj16+ckpt20+pread；C1/C0=2.13 PASS；SQL attach/PSI 未升 D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.709**@`20260726_065841`（pilot`063057`=1.709；fio_nj=4/iodepth=16/1G；C0≈106 C1≈181 C2≈147；offline D3+SQL_PENDING；fio@grj-m0；stub C0_noise 保留；GH quiet DONE `072233` detect_ok=no；XPU quiet DONE `073224` detect_ok=no）；**Masked formal SCORED D3** C1/C0=**1.078**@`20260726_154204`（=quiet lean；pilot`153104`=1.078；C0≈100 C1≈108 C2≈107；offline D3+SQL_PENDING；GH masked DONE `155151` detect_ok=no；XPU masked DONE `160000` detect_ok=no；Quiet+Masked 全齐） |
| P3-EXT-C | 抢内存带宽 | **stress_vm** | host_bound | **1.59** | **D3** | `20260725_021906` vm 96×6G；C1/C0=1.59 PASS；PSI_UNAVAIL（无 /proc/pressure）；SQL attach 失败不升 D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.906**@`20260726_102936`（vm 32×4G；pilot`095909`=1.722；stub`100606`/`101737` C0_noise 保留；offline D3+SQL_PENDING；GH quiet DONE `104300` detect_ok=no；XPU quiet DONE `105344` detect_ok=no）；**Masked formal SCORED D3** C1/C0=**1.744**@`20260726_122130`（32×4G＝quiet lean；pilot`110355`=1.085；GH masked `123500` detect_ok=no；XPU masked `124743` detect_ok=yes） |
| P3-SW-A | 对象泄漏→GC | 8a inline | host_bound | **2.93** | **D4** | Loud+C2 `20260725_012957-yjr-as-c-p3-sw-a-loud`；证据 `data_ms`/onset + SQL `cpu.utilization_rss`；stall=0.25 calibrated；**Quiet formal SCORED D4** C1/C0=**1.949**@`20260725_215903`；**Masked formal SCORED D4** C1/C0=**1.768**@`20260725_224156`（every=1 stall=0.05；pilot`222736`=1.856；thr1.05；offline D3+SQL PASS_D4；非 BOUNDARY） |
| P3-SW-B | dataloader 泄漏 | 8b inline | host_bound | **2.06** | **D4** | Loud+C2 `20260725_125558-yjr-as-c-p3-sw-b-loud`；证据 `data_ms`/onset + SQL PASS_D4；mb=16 stall=0.25 calibrated；**Quiet formal SCORED D4** C1/C0=**2.101**@`20260725_232814`（mb=8 stall=0.1；pilot`230345`=1.715；offline D3+SQL PASS_D4）；**Masked formal SCORED D3** C1/C0=**1.909**@`20260726_000113`（mb=6 stall=0.1；pilot`235216`=1.389；offline D3+SQL_PENDING；非 BOUNDARY） |
| P3-SW-C | 监控自身泄漏 | **sidecar_8c** | host_bound | **2.33** | **D4** | Loud+C2 `20260725_135238-yjr-as-c-p3-sw-c-loud`；pod-sup 准时 inject@step100；stress-ng nproc@90 + 1MB/s leak；offline D3 + SQL PASS_D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.95**@`20260726_125953`（80@70 leak_every=2 max_chunks=32；pilot`123508`=1.91；stub`124037`/`124908` 保留；offline D3+SQL_PENDING；GH quiet DONE `131244` detect_ok=no；XPU quiet DONE `132040` detect_ok=no）；**Masked formal SCORED D3** C1/C0=**1.649**@`20260726_135016`（=quiet lean；C0=90.26；pilot`131921`=1.050；stubs D0 保留；GH masked `140713` detect_ok=no；XPU masked `141517` detect_ok=yes） |
| P1-SW-A | 显存碎片化 | **inline_2a** | gpu_bound | **4.20** | **D3** | `20260725_114556` INLINE chunks12/stall768MB/0.25s；C1/C0=4.20 PASS；offline+SQL **D3**（min_compute→rank_7）；gap flat / SQL_NO_EXT_EVIDENCE 不升 D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.638**@`20260726_042922`（chunks=3/stall128/0.05；pilot`042251`=1.639；GH quiet `044118` detect_ok=yes；XPU quiet `044734` detect_ok=no）；**Masked formal SCORED D3** C1/C0=**1.259**@`20260726_044454`（chunks=1/stall64/0.02；pilot`044124`=1.250；offline D3+SQL_NO_EXT；GH masked `045441` detect_ok=yes；XPU masked `050018` detect_ok=no） |
| P1-SW-B | 罕见 shape | **inline_2b** | gpu_bound | **1.36** | **D3** | `20260725_115732` INLINE rare_seq=1536/every=1；C1/C0=1.36 PASS；offline+SQL **D3**（shape_seq_rare→rank_7）；SQL_NO_EXT_EVIDENCE 不升 D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.288**@`20260726_052307`（rare_seq=1408/every=1；pilot`051822`=1.284；offline D3 shape_seq_rare→rank_7 + SQL_NO_EXT；stub`045940`/`050506`/`051023` ineffective 保留；GH quiet `070049` detect_ok=yes；XPU quiet `070702` detect_ok=no）；**Masked formal SCORED D3** C1/C0=**1.287**@`20260726_072137`（rare_seq=1408/every=1＝quiet 剂量；pilot`071428`=1.283；stub`070504`/`070958` ineffective 保留；SQL_PENDING；GH masked `073636` detect_ok=yes；XPU masked `074217` detect_ok=no） |
| P1-SW-C | 编译尖刺 | **inline_2c** | gpu_bound | **4.63** tip | **D3** | `20260725_121105` INLINE n=1024/every=1/fallback=0.25；tip max=4.63 PASS（med=1.02 盲）；offline+SQL **D3**（min_compute_at_tip→rank_7）；SQL_NO_EXT_EVIDENCE 不升 D4；dose calibrated；**Quiet formal SCORED D3** tip max=**2.61**@`20260726_021606`（n=768 every=4；pilot`015328`=5.72；前次`020857` tip_fail D0 保留）；GH quiet `022611` tip max=2.727 detect_ok=no；XPU quiet `023654` tip max=1.293 detect_ok=no；**Masked formal SCORED D3** tip max=**2.61**@`20260726_025116`（n=768 every=4 fallback=0.05；pilot`024514`=2.53）；GH masked `030130` tip max=3.242 detect_ok=no；XPU masked `031321` tip max=2.462 detect_ok=no |
| P2-SW-B | 通信算法切换 | **hccl_algo** | gpu_bound | **1.82** comm | **D3** | `20260725_122911` ring+stress512+buffsize8；C1/C0_comm=1.82 PASS（step=1.13 不 FAIL）；offline+SQL **D3**（comm_phase_envwide→rank_7）；SQL_NO_EXT_EVIDENCE 不升 D4；dose calibrated；pilot1`122231` ring-only 咬空 |
| P2-SW-C | 拓扑映射漂移 | **topo_5c** | gpu_bound | **49.86** comm | **D3** | `20260725_124102` device_rev+AR512×262144；C1/C0_comm=49.86 step=5.06 PASS；offline+SQL **D3**（topo_phase_envwide→rank_7）；SQL_NO_EXT_EVIDENCE 不升 D4；dose calibrated |
| P1-HW-B | 显存带宽渐进 | **inline_1b_ramp** | gpu_bound | **1.57** | **D3** | Loud+C2 `20260725_142359`；INLINE HBM ramp copies 6→48 mb=512；C1/C0=1.57 PASS；offline **D3**（min_compute→rank_7）；SQL attach/mx-smi 失败不升 D4；dose calibrated；**Quiet formal SCORED D3** C1/C0=**1.219**@`20260726_005203`（mb=320 copies=5→30；pilot`004220`=1.218；offline D3+SQL_NO_EXT_EVIDENCE）；**Masked formal SCORED D3** C1/C0=**1.129**@`20260726_011501`（mb=256 copies=4→24；pilot`011036`=1.128；offline D3+SQL_NO_EXT_EVIDENCE） |

## 3.2 baseline 适配态

| 工具 | 状态 | 备注 |
|---|---|---|
| Greyhound | **S4_DETECT** | worker-1；公平性：真实序列+C0 假阳性；P3-EXT-A 对照 DONE |
| XPUTimer | **S4_DETECT** | worker-2；`cross_run_contrast`；P3-EXT-A DONE |
| Dynolog / FR / … | PENDING | **本波不进对照** |

## 3.2b 对照波次（流水线 2）

真相源：[`CONTRAST_QUEUE.md`](CONTRAST_QUEUE.md)。任务卡：[`agents/BASELINE_CONTRAST.md`](agents/BASELINE_CONTRAST.md)。

| case | tool | 状态 | evidence |
|---|---|---|---|
| P3-EXT-A | XPUTimer | DONE | `yjr-as-b-xpu-s4-20260724_233105` |
| P3-EXT-A | Greyhound | DONE | `contrast-p3-ext-a-20260725_114502`；detect_ok=no；旧 S4 保留 |
| P3-EXT-A | Greyhound | DONE（dose=quiet） | `contrast-p3-ext-a-quiet-20260726_080959`@worker-2；dose `cpu_n=128,cpu_load=70`；coll=**0.933** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**0.766** dose_WEAK（thr1.15；金标≈1.256）；detect_mode=no_bite；detect_ok=no；case_ref=`075912` |
| P3-EXT-A | XPUTimer | DONE（dose=quiet） | `contrast-p3-ext-a-quiet-20260726_081751`@worker-2；dose `cpu_n=128,cpu_load=70`；自主 hang/slow=**0**；跨-run coll=**1.056** FAIL（thr1.15）；dose_check step_ms=**1.248** PASS（金标≈1.256）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`075912` |
| P3-EXT-A | Greyhound | DONE（dose=masked） | `contrast-p3-ext-a-masked-20260726_095858`@worker-2；dose `cpu_n=128,cpu_load=70`；coll=**1.009** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**0.789** dose_WEAK（thr1.05；金标≈1.470）；detect_mode=no_bite；detect_ok=no；case_ref=`094648` |
| P3-EXT-A | XPUTimer | DONE（dose=masked） | `contrast-p3-ext-a-masked-20260726_100915`@worker-2；dose `cpu_n=128,cpu_load=70`；自主 hang/slow=**0**；跨-run coll=**0.939** FAIL（thr1.05）；dose_check step_ms=**0.674** dose_WEAK（金标≈1.470）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`094648` |
| P1-EXT-A | XPUTimer | DONE | `contrast-p1-ext-a-20260725_114546`；自主 hang/slow=0；coll C1/C0=1.036 FAIL；dose_check step_ms=3.955 PASS；detect_mode=cross_run_contrast |
| P1-EXT-A | Greyhound | DONE | `contrast-p1-ext-a-20260725_120526`@worker-1；coll=1.018 FAIL；Rbeast C1=2/C0=0 hit；step_ms=3.924 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-EXT-B | XPUTimer | DONE | `contrast-p1-ext-b-20260725_115717`；自主 hang/slow=0；coll C1/C0=0.982 FAIL；dose_check step_ms=2.069 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P1-EXT-B | Greyhound | DONE | `contrast-p1-ext-b-20260725_121407`@worker-1；coll=1.009 FAIL；Rbeast C1=2/C0=0 hit；step_ms=2.070 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P3-EXT-B | XPUTimer | DONE | `contrast-p3-ext-b-20260725_120235`；自主 hang/slow=0；coll C1/C0=1.048 FAIL；dose_check step_ms=1.793 PASS；detect_mode=cross_run_contrast；detect_ok=no；stress-ng fallback |
| P3-EXT-B | Greyhound | DONE | `contrast-p3-ext-b-20260725_122204`@worker-1；coll=1.049 FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=1.738 dose_OK；detect_ok=no；detect_mode=no_bite；stress-ng fallback |
| P3-EXT-B | Greyhound | DONE（dose=quiet） | `contrast-p3-ext-b-quiet-20260726_072233`@worker-2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`；coll=**1.015** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**1.020** dose_WEAK（thr1.15；金标≈1.709；fio-3.29 held≈38s@~20MiB/s）；detect_mode=no_bite；detect_ok=no；case_ref=`065841`；先轮 stress-ng `071352` 亦 dose_WEAK 保留 |
| P3-EXT-B | XPUTimer | DONE（dose=quiet） | `contrast-p3-ext-b-quiet-20260726_073224`@worker-2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`；自主 hang/slow=**0**；跨-run coll=**0.977** FAIL（thr1.15）；dose_check step_ms=**1.256** PASS（金标≈1.709；fio-3.29 held≈38s@~20.6MiB/s）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`065841` |
| P3-EXT-B | Greyhound | DONE（dose=masked） | `contrast-p3-ext-b-masked-20260726_155151`@worker-2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`；coll=**1.024** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**0.777** dose_WEAK（thr1.05；金标≈1.078；fio-3.29 held≈39s@~19.5MiB/s）；detect_mode=no_bite；detect_ok=no；case_ref=`154204` |
| P3-EXT-B | XPUTimer | DONE（dose=masked） | `contrast-p3-ext-b-masked-20260726_160000`@worker-2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`；自主 hang/slow=**0**；跨-run coll=**1.023** FAIL（thr1.05）；dose_check step_ms=**1.113** PASS（金标≈1.078；fio-3.29 held≈39s@~19.4MiB/s）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`154204` |
| P3-EXT-C | XPUTimer | DONE | `contrast-p3-ext-c-20260725_121535`；自主 hang/slow=0；coll C1/C0=1.184 FAIL；dose_check step_ms=1.780 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P3-EXT-C | Greyhound | DONE | `contrast-p3-ext-c-20260725_124257`@worker-1；coll=1.296 FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=1.063 dose_WEAK；detect_ok=no；detect_mode=no_bite；page-in+6Gi |
| P3-EXT-C | Greyhound | DONE（dose=quiet） | `contrast-p3-ext-c-quiet-20260726_104300`@worker-2；dose `vm_n=32,vm_bytes=4G`；coll=**1.017** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**0.624** dose_WEAK（thr1.15；金标≈1.906；PAGEIN_PARTIAL +4Gi）；detect_mode=no_bite；detect_ok=no；case_ref=`102936` |
| P3-EXT-C | XPUTimer | DONE（dose=quiet） | `contrast-p3-ext-c-quiet-20260726_105344`@worker-2；dose `vm_n=32,vm_bytes=4G`；自主 hang/slow=**0**；跨-run coll=**0.992** FAIL（thr1.15）；dose_check step_ms=**1.075** dose_WEAK（金标≈1.906；stress-ng-vm 部分 exit=5）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`102936` |
| P3-EXT-C | Greyhound | DONE（dose=masked） | `contrast-p3-ext-c-masked-20260726_123500`@worker-2；dose `vm_n=32,vm_bytes=4G`；coll=**1.029** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**1.542** dose_OK（thr1.05；金标≈1.744）；detect_mode=no_bite；detect_ok=no；case_ref=`122130` |
| P3-EXT-C | XPUTimer | DONE（dose=masked） | `contrast-p3-ext-c-masked-20260726_124743`@worker-2；dose `vm_n=32,vm_bytes=4G`；自主 hang/slow=**0**；跨-run coll=**1.095** PASS（thr1.05）；dose_check step_ms=**1.047** dose_WEAK（金标≈1.744）；detect_mode=cross_run_contrast；detect_ok=yes；case_ref=`122130` |
| P3-SW-A | XPUTimer | DONE | `contrast-p3-sw-a-20260725_122733`；自主 hang/slow=0；coll C1/C0=0.953 FAIL；dose_check step_ms=2.633 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P3-SW-A | Greyhound | DONE | `contrast-p3-sw-a-20260725_124837`@worker-1；coll=1.000 FAIL；Rbeast C1=2/C0=0 hit；step_ms=5.667 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P3-SW-A | Greyhound | DONE（dose=quiet） | `contrast-p3-sw-a-quiet-20260725_222610`@worker-2；dose `every=1,stall_s=0.1`；coll=**0.983** FAIL；Rbeast C1=2/C0=0 hit；step_ms=**2.419** dose_OK（thr1.15；金标≈1.949）；detect_ok=yes；detect_mode=autonomous；case_ref=`215903` |
| P3-SW-A | XPUTimer | DONE（dose=quiet） | `contrast-p3-sw-a-quiet-20260725_224059`@worker-2；dose `every=1,stall_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.071** FAIL（thr1.15）；dose_check step_ms=**3.421** PASS（金标≈1.949）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`215903` |
| P3-SW-A | Greyhound | DONE（dose=masked） | `contrast-p3-sw-a-masked-20260725_225517`@worker-2；dose `every=1,stall_s=0.05`；coll=**1.009** FAIL；Rbeast C1=2/C0=0 hit；step_ms=**2.058** dose_OK（thr1.05；金标≈1.768）；detect_ok=yes；detect_mode=autonomous；case_ref=`224156`；collect_seq+C0 FP |
| P3-SW-A | XPUTimer | DONE（dose=masked） | `contrast-p3-sw-a-masked-20260725_230400`@worker-2；dose `every=1,stall_s=0.05`；自主 hang/slow=**0**；跨-run coll=**1.033** FAIL（thr1.05）；dose_check step_ms=**1.564** PASS（金标≈1.768）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`224156` |
| P1-SW-A | XPUTimer | DONE | `contrast-p1-sw-a-20260725_123626`；自主 hang/slow=0；coll C1/C0=0.991 FAIL；dose_check step_ms=4.284 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P1-SW-A | Greyhound | DONE | `contrast-p1-sw-a-20260725_125949`@worker-1；coll=1.009 FAIL；Rbeast C1=2/C0=0 hit；step_ms=4.283 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-SW-A | Greyhound | DONE（dose=quiet） | `contrast-p1-sw-a-quiet-20260726_044118`@worker-2；dose `chunks=3,stall_mb=128,stall_s=0.05`；coll=**1.028** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.647** dose_OK（thr1.15；金标≈1.638）；detect_ok=yes；detect_mode=autonomous；case_ref=`042922` |
| P1-SW-A | XPUTimer | DONE（dose=quiet） | `contrast-p1-sw-a-quiet-20260726_044734`@worker-2；dose `chunks=3,stall_mb=128,stall_s=0.05`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.15）；dose_check step_ms=**1.647** PASS（金标≈1.638）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`042922` |
| P1-SW-A | Greyhound | DONE（dose=masked） | `contrast-p1-sw-a-masked-20260726_045441`@worker-2；dose `chunks=1,stall_mb=64,stall_s=0.02`；coll=**0.981** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.237** dose_OK（thr1.05；金标≈1.259）；detect_ok=yes；detect_mode=autonomous；case_ref=`044454` |
| P1-SW-A | XPUTimer | DONE（dose=masked） | `contrast-p1-sw-a-masked-20260726_050018`@worker-2；dose `chunks=1,stall_mb=64,stall_s=0.02`；自主 hang/slow=**0**；跨-run coll=**0.983** FAIL（thr1.05）；dose_check step_ms=**1.243** PASS（金标≈1.259）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`044454` |
| P1-SW-B | XPUTimer | DONE | `contrast-p1-sw-b-20260725_124414`；自主 hang/slow=0；coll C1/C0=0.991 FAIL；dose_check step_ms=1.372 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P1-SW-B | Greyhound | DONE | `contrast-p1-sw-b-20260725_132011`@worker-1；coll=1.020 FAIL；Rbeast C1=2/C0=0 hit；step_ms=1.386 dose_OK；detect_ok=yes；detect_mode=autonomous |
| P1-SW-B | Greyhound | DONE（dose=quiet） | `contrast-p1-sw-b-quiet-20260726_070049`@worker-2；dose `rare_seq=1408,every=1`；coll=**1.018** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.297** dose_OK（thr1.15；金标≈1.288）；detect_ok=yes；detect_mode=autonomous；case_ref=`052307` |
| P1-SW-B | XPUTimer | DONE（dose=quiet） | `contrast-p1-sw-b-quiet-20260726_070702`@worker-2；dose `rare_seq=1408,every=1`；自主 hang/slow=**0**；跨-run coll=**0.965** FAIL（thr1.15）；dose_check step_ms=**1.285** PASS（金标≈1.288）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`052307` |
| P1-SW-B | Greyhound | DONE（dose=masked） | `contrast-p1-sw-b-masked-20260726_073636`@worker-2；dose `rare_seq=1408,every=1`；coll=**0.991** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.299** dose_OK（thr1.05；金标≈1.287）；detect_ok=yes；detect_mode=autonomous；case_ref=`072137` |
| P1-SW-B | XPUTimer | DONE（dose=masked） | `contrast-p1-sw-b-masked-20260726_074217`@worker-2；dose `rare_seq=1408,every=1`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.05）；dose_check step_ms=**1.303** PASS（金标≈1.287）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`072137` |
| P1-SW-C | XPUTimer | DONE | `contrast-p1-sw-c-20260725_125656`；自主 hang/slow=0；coll C1/C0=0.991 FAIL；median step_ms=1.006 盲；tip max=4.897 PASS（金标≈4.63）；detect_mode=cross_run_contrast；detect_ok=no |
| P1-SW-C | Greyhound | DONE | `contrast-p1-sw-c-20260725_132954`@worker-1；coll=1.027 FAIL；Rbeast C1/C0 cp=0/0 miss；median step_ms=1.024 盲；tip max=4.038 PASS（金标≈4.63）；detect_mode=no_bite；detect_ok=no；SPIKE_OK×200 |
| P1-SW-C | Greyhound | DONE（dose=quiet） | `contrast-p1-sw-c-quiet-20260726_022611`@worker-2；dose `n=768,every=4,fallback_s=0.1`；coll=**1.029** FAIL；Rbeast C1/C0 cp=0/0 miss；median step_ms=**1.006** 盲；tip max=**2.727** PASS（金标≈2.61）；detect_mode=no_bite；detect_ok=no；SPIKE_OK×50；case_ref=`021606` |
| P1-SW-C | XPUTimer | DONE（dose=quiet） | `contrast-p1-sw-c-quiet-20260726_023654`@worker-2；dose `n=768,every=4,fallback_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.15）；median step_ms=**1.004** 盲；tip max=**1.293** FAIL（金标≈2.61；辅闸 max≥2.5）；detect_mode=cross_run_contrast；detect_ok=no；SPIKE_OK×50；case_ref=`021606` |
| P1-SW-C | Greyhound | DONE（dose=masked） | `contrast-p1-sw-c-masked-20260726_030130`@worker-2；dose `n=768,every=4,fallback_s=0.05`；coll=**0.973** FAIL；Rbeast C1/C0 cp=0/0 miss；median step_ms=**1.002** 盲；tip max=**3.242** PASS（金标≈2.61）；detect_mode=no_bite；detect_ok=no；SPIKE_OK×50；case_ref=`025116` |
| P1-SW-C | XPUTimer | DONE（dose=masked） | `contrast-p1-sw-c-masked-20260726_031321`@worker-2；dose `n=768,every=4,fallback_s=0.05`；自主 hang/slow=**0**；跨-run coll=**0.983** FAIL（thr1.05）；median step_ms=**1.005** 盲；tip max=**2.462** PASS（p99=2.839；金标≈2.61；辅闸 max≥2.5 略欠）；detect_mode=cross_run_contrast；detect_ok=no；SPIKE_OK×50；case_ref=`025116` |
| P2-SW-B | XPUTimer | DONE | `contrast-p2-sw-b-20260725_131251`；自主 hang/slow=0；coll=1.000 FAIL；dose_check **comm=1.875 PASS**（step=1.152 旁证）；detect_mode=cross_run_contrast；detect_ok=no |
| P2-SW-B | Greyhound | DONE | `contrast-p2-sw-b-20260725_134521`@worker-1；coll=0.982 FAIL；Rbeast C1/C0 cp=0/0 miss；dose_check **comm=1.862 PASS**（step=1.152 旁证）；detect_mode=no_bite；detect_ok=no |
| P2-SW-C | XPUTimer | DONE | `contrast-p2-sw-c-20260725_132235`；自主 hang/slow=0；coll=0.593 FAIL；dose_check **comm=13.910 PASS**（step=2.119 旁证；金标≈49.86/5.06）；detect_mode=cross_run_contrast；detect_ok=no |
| P2-SW-C | Greyhound | DONE | `contrast-p2-sw-c-20260725_135623`@worker-1；coll=0.564 FAIL；Rbeast C1/C0 cp=0/0 miss；dose_check **comm=15.016 PASS**（step=2.211 旁证；金标≈49.86）；detect_mode=no_bite；detect_ok=no |
| P3-SW-B | XPUTimer | DONE | `contrast-p3-sw-b-20260725_133435`；自主 hang/slow=0；跨-run coll=0.992 无咬合；dose_check step_ms=2.047 PASS；detect_mode=cross_run_contrast；detect_ok=no |
| P3-SW-B | Greyhound | DONE | `contrast-p3-sw-b-20260725_140639`@worker-1；coll=0.984 FAIL；Rbeast C1=2/C0=0 hit；step_ms=3.760 dose_OK（金标≈2.06）；detect_ok=yes；detect_mode=autonomous |
| P3-SW-B | Greyhound | DONE（dose=quiet） | `contrast-p3-sw-b-quiet-20260725_235209`@worker-2；dose `mb=8,stall_s=0.1`；coll=**0.943** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**1.213** dose_OK（thr1.15；金标≈2.101）；detect_mode=no_bite；detect_ok=no；case_ref=`232814` |
| P3-SW-B | XPUTimer | DONE（dose=quiet） | `contrast-p3-sw-b-quiet-20260726_000506`@worker-2；dose `mb=8,stall_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.016** FAIL（thr1.15）；dose_check step_ms=**1.125** FAIL（金标≈2.101）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`232814` |
| P3-SW-B | Greyhound | DONE（dose=masked） | `contrast-p3-sw-b-masked-20260726_003712`@worker-2；dose `mb=6,stall_s=0.1`；coll=**0.929** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**2.070** dose_OK（thr1.05；金标≈1.909）；detect_mode=no_bite；detect_ok=no；case_ref=`000113` |
| P3-SW-B | XPUTimer | DONE（dose=masked） | `contrast-p3-sw-b-masked-20260726_005624`@worker-2；dose `mb=6,stall_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.024** FAIL（thr1.05）；dose_check step_ms=**1.255** PASS（金标≈1.909）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`000113` |
| P3-SW-C | XPUTimer | DONE | `contrast-p3-sw-c-20260725_141815`@worker-2；自主 hang/slow=0；跨-run coll=0.917 FAIL；dose_check step_ms=2.504 PASS（金标≈2.49）；detect_mode=cross_run_contrast；detect_ok=no |
| P3-SW-C | Greyhound | DONE | `contrast-p3-sw-c-20260725_143448`@worker-1；coll=1.016 FAIL；Rbeast C1=1/C0=0 hit；step_ms=2.509 dose_OK（金标≈2.49）；detect_ok=yes；detect_mode=autonomous；旁证 `143010` |
| P3-SW-C | Greyhound | DONE（dose=quiet） | `contrast-p3-sw-c-quiet-20260726_131244`@worker-2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`；coll=**1.000** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**0.799** dose_WEAK（thr1.15；金标≈1.95；sidecar START ok）；detect_mode=no_bite；detect_ok=no；case_ref=`125953` |
| P3-SW-C | XPUTimer | DONE（dose=quiet） | `contrast-p3-sw-c-quiet-20260726_132040`@worker-2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`；自主 hang/slow=**0**；跨-run coll=**1.000** FAIL（thr1.15）；dose_check step_ms=**1.960** PASS（金标≈1.95）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`125953` |
| P3-SW-C | Greyhound | DONE（dose=masked） | `contrast-p3-sw-c-masked-20260726_140713`@worker-2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`；coll=**1.023** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**0.988** dose_WEAK（thr1.05；金标≈1.649）；detect_mode=no_bite；detect_ok=no；case_ref=`135016` |
| P3-SW-C | XPUTimer | DONE（dose=masked） | `contrast-p3-sw-c-masked-20260726_141517`@worker-2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`；自主 hang/slow=**0**；跨-run coll=**1.056** PASS（thr1.05）；dose_check step_ms=**1.517** PASS（金标≈1.649）；detect_mode=cross_run_contrast；detect_ok=yes；case_ref=`135016` |
| P1-HW-B | XPUTimer | DONE | `contrast-p1-hw-b-20260725_143531`@worker-2；自主 hang/slow=0；跨-run coll=0.991 FAIL；dose_check step_ms=1.585 PASS（金标≈1.57）；detect_mode=cross_run_contrast；detect_ok=no |
| P1-HW-B | Greyhound | DONE | `contrast-p1-hw-b-20260725_144607`@worker-1；coll=1.027 FAIL；Rbeast C1=2/C0=0 hit；step_ms=1.609 dose_OK（金标≈1.57）；detect_ok=yes；detect_mode=autonomous；GH 队无 PENDING |
| P1-HW-B | Greyhound | DONE（dose=quiet） | `contrast-p1-hw-b-quiet-20260726_010435`@worker-2；dose `mb=320,copies=5→30,ramp=1`；coll=**0.972** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.213** dose_OK（thr1.15；金标≈1.219）；detect_mode=autonomous；detect_ok=yes；case_ref=`005203` |
| P1-HW-B | XPUTimer | DONE（dose=quiet） | `contrast-p1-hw-b-quiet-20260726_011651`@worker-2；dose `mb=320,copies=5→30,ramp=1`；自主 hang/slow=**0**；跨-run coll=**1.000** FAIL（thr1.15）；dose_check step_ms=**1.237** PASS（金标≈1.219）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`005203` |
| P1-HW-B | Greyhound | DONE（dose=masked） | `contrast-p1-hw-b-masked-20260726_012256`@worker-2；dose `mb=256,copies=4→24,ramp=1`；coll=**0.991** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**1.153** dose_OK（thr1.05；金标≈1.129）；detect_mode=no_bite；detect_ok=no；case_ref=`011501` |
| P1-HW-B | XPUTimer | DONE（dose=masked） | `contrast-p1-hw-b-masked-20260726_012822`@worker-2；dose `mb=256,copies=4→24,ramp=1`；自主 hang/slow=**0**；跨-run coll=**0.982** FAIL（thr1.05）；dose_check step_ms=**1.125** PASS（金标≈1.129）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`011501` |
| P1-EXT-A | Greyhound | DONE（dose=quiet） | `contrast-p1-ext-a-quiet-20260726_013844`@worker-2；dose `size=4096,mm=32`；coll=**0.991** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.164** dose_OK（thr1.15；金标≈1.156）；detect_mode=autonomous；detect_ok=yes；case_ref=`013034` |
| P1-EXT-A | XPUTimer | DONE（dose=quiet） | `contrast-p1-ext-a-quiet-20260726_014833`@worker-2；dose `size=4096,mm=32`；自主 hang/slow=**0**；跨-run coll=**0.982** FAIL（thr1.15）；dose_check step_ms=**1.158** PASS（金标≈1.156）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`013034` |
| P1-EXT-A | Greyhound | DONE（dose=masked） | `contrast-p1-ext-a-masked-20260726_015409`@worker-2；dose `size=4096,mm=16`；coll=**1.000** FAIL；Rbeast C1/C0 cp=0/0 miss；step_ms=**1.072** dose_OK（thr1.05；金标≈1.078）；detect_mode=no_bite；detect_ok=no；case_ref=`014611` |
| P1-EXT-A | XPUTimer | DONE（dose=masked） | `contrast-p1-ext-a-masked-20260726_021212`@worker-2；dose `size=4096,mm=16`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.05）；dose_check step_ms=**1.077** PASS（金标≈1.078）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`014611` |
| P1-EXT-B | Greyhound | DONE（dose=quiet） | `contrast-p1-ext-b-quiet-20260726_035533`@worker-2；dose `mb=256,copies=16`；coll=**1.009** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.170** dose_OK（thr1.15；金标≈1.161）；detect_mode=autonomous；detect_ok=yes；case_ref=`033758` |
| P1-EXT-B | XPUTimer | DONE（dose=quiet） | `contrast-p1-ext-b-quiet-20260726_040543`@worker-2；dose `mb=256,copies=16`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.15）；dose_check step_ms=**1.168** PASS（金标≈1.161）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`033758` |
| P1-EXT-B | Greyhound | DONE（dose=masked） | `contrast-p1-ext-b-masked-20260726_042310`@worker-2；dose `mb=192,copies=10`；coll=**1.009** FAIL；Rbeast C1/C0 cp=**0/0** miss；step_ms=**1.076** dose_OK（thr1.05；金标≈1.070）；detect_mode=no_bite；detect_ok=no；case_ref=`040309` |
| P1-EXT-B | XPUTimer | DONE（dose=masked） | `contrast-p1-ext-b-masked-20260726_042922`@worker-2；dose `mb=192,copies=10`；自主 hang/slow=**0**；跨-run coll=**0.982** FAIL（thr1.05）；dose_check step_ms=**1.066** PASS（金标≈1.070）；detect_mode=cross_run_contrast；detect_ok=no；case_ref=`040309` |
| 其余 SCORED × GH/XPU | — | — | 见 CONTRAST_QUEUE（本波 GH/XPU 对照格已齐） |

## 3.3 判分证据口径（探索后填）

| Case 族 | 证据字段（草稿） |
|---|---|
| P1-EXT | `gpu.utilization` / `host_npu_smi_*` |
| P1-SW | `min_compute_ms`（2a）；`shape_seq_rare`（2b）；tip max + `min_compute_at_tip_step`（2c）；SQL 常无升 D4 路径 |
| P2-SW | `comm_ms` / `comm_phase_envwide`（P2-SW-B hccl_algo）；`topo_phase_envwide`（P2-SW-C topo_5c）；SQL 常无 duration 升 D4 |
| P3-EXT | `host_psi_*` / `cpu.utilization`（本核常 **PSI_UNAVAIL**；旁证 loadavg + `data_ms`） |
| P3-SW | `cpu.utilization_rss` 等（对齐沐曦后再定） |

---

# 四、Agent 双流水线（编排）

## 4.1 现役（2026-07-26 起）：Pillar C v2（E1–E4）

| 轨 | 文档 | 状态落点 |
|---|---|---|
| Loop | [`agents/LOOP.md`](agents/LOOP.md) / [`LOOP_PROMPT.md`](agents/LOOP_PROMPT.md) | `$LOCAL_RESULT_ROOT_BASE/_prep/LOOP_LAST.md` |
| Pillar C v2 | [`PILLAR_C_QUEUE.md`](PILLAR_C_QUEUE.md) + [`agents/PILLAR_C_RUNNER.md`](agents/PILLAR_C_RUNNER.md) | `_prep/pillar_c_gate/{GATE,MECH_FIX}.md` + `pillar_c_v2/<run_id>/` |
| 方案真相源 | `project/reading-paper/writing/probing-paper/EVAL-GAP-AND-PILLAR-C-PLAN.md` §2–§5 | E1–E4；主尺=总落盘 |
| Dose（收官/残留） | [`DOSE_QUEUE.md`](DOSE_QUEUE.md) | 代表+扩展已齐；P2-SW-B/C 顺手，不挡 C |
| 资源 | **w0=C**；m0=C0 短测/备用 | [`agents/RESOURCE.md`](agents/RESOURCE.md) |

**旧 C 作废**：`pillar_c/*/VOLUME_RATIO.md`（cold 三臂比）、C-2 `COLD_MAX` 主线、C-3 mid_set 当终态 → **SUPERSEDED**（尺用错 + SET 未进 live tracer + 用训练 D 判臂）。见队列 §0。

Loud 归档 [`agents/LOOP_LOUD.md`](agents/LOOP_LOUD.md)。

## 4.2 Loud 战役落点（归档参考）

| 轨 | 文档 | 状态落点 |
|---|---|---|
| Case Loud | CASE_RUNNER | CASE_QUEUE + §3.1 |
| 对照 Loud | BASELINE_CONTRAST | CONTRAST_QUEUE + §3.2b |

# 五、变更记录

| 日期 | 变更 |
|---|---|
| 2026-07-26 | **Pillar-C v2 备份**：ais `/root/backups/ascend-ais-pillar-c-v2-20260726`（11G / tar.gz 54M md5 `9b65f31d…`）；AFS 同路径；本仓瘦身入库 `results/ascend-ais/pillar_c_v2/`；见 `BACKUP.md` / `CAMPAIGN_SUMMARY.md` |
| 2026-07-26 | **Pillar-C v2 主线收官**：C0→E1-off→E1→E2→E3→E4→S1 全 ✅；头条动态/全量=**72.6%**（`181423`）；E2 最稀 rate=0；E4 PASS 掉级；S1 `184311` 热接入 restart=0、onset 前不可见；摘要 `pillar_c_v2/CAMPAIGN_SUMMARY.md`；15m loop 停 |
| 2026-07-26 | **Pillar-C E4 反例 DONE**：parent=`20260726_182630-pillar-c-e4-p3-sw-a-loud`@grj-w0；砍量=E3 动态去 SET↑（rate=0 SAMPLE_MS=500）；**PASS 掉级**（path_enough RSS∧SET：E3 Y→naive N）；禁 SET 控制 Y（log 缺席）；TT rows **0 vs E3 54054**；RSS 仍 Y（周期小表）；总落盘≈1.54GB 仍小；`E4_ABLATION.md`；未占 m0；w0 IDLE |
| 2026-07-26 | **Pillar-C E3 头条 DONE**：parent=`20260726_181423-pillar-c-e3-p3-sw-a-loud`@grj-w0；P3-SW-A；动态 rate=0→SET `probing.torch.profiling=on,rate=1.0`（SET_OK 931ms）vs 复用 full `230350`；**动态/全量=72.6%**（W\*=100 content est；raw=90.16%）；RSS 同覆盖；cold 12.8 vs 161.5 MiB；SET 仅首 worker（脚本 `break`）；`E3_RATIO.md`；未占 m0 |
| 2026-07-26 | **Pillar-C E3 头条 DONE**：parent=`20260726_181423-pillar-c-e3-p3-sw-a-loud`@grj-w0；P3-SW-A；动态/全量=**72.6%**（W\*=100 content est；raw=90.16%）；SET_OK（`probing.torch.profiling=`）；RSS 同覆盖；cold 7.9%；仅 1/16 rank 实写 TT（SET 脚本首 worker 后 break）；`E3_RATIO.md`；下一 E4 |
| 2026-07-26 | **Pillar-C E1 收口 NO_W_STAR**：`173830`@grj-m0 P1-SW-C；offline_truncate W=50/100/200/full 均无 duration 尖刺（top@269≈0.35s）；**未复现** E1-off W\*=100；SET 当时键=`torch.profiling=`（非 `probing.torch.profiling=`）；`hold_exec` 已改真相键；`173220` INVALID PATH；设计窗仍用 E1-off=**100** 供 E3 |
| 2026-07-26 | **Pillar-C E2 BOUNDARY DONE**：parent=`20260726_173134-pillar-c-e2-p3-sw-a-loud`@grj-w0；P3-SW-A；够触发最稀常驻率=**0**（0/0.05 均 RSS trigger_ok≈308/390MB）；中间 0.001/0.01 跳过；总落盘≈1.61GB/臂（cold≈9MB 非主尺）；SET↑ 两臂 FAIL（jexec PATH 缺 `/usr/bin`，非 rate 盲区）；`hold_exec` 已补 PATH、`env.sh` 去 `/data` 早默认；`E2_RATE.md`；`172752` 仍 INVALID |
| 2026-07-26 | **Pillar-C E1-off DONE**：`pillar_c_v2/E1_off/W_STAR.md`；P1-SW-C **W\*=100**（duration 尖刺@238）；P3-SW-A/B **UNRESOLVED**（`cpu.utilization` 环与注入窗时间错位；C2 可证 RSS rise）；P1-HW-B **NO_W_STAR**（注入窗 torch alloc 平坦）；环 20MB≈**546**步；脚本 `e1_offline_window_score.py`；未用 cold 冒充 |
| 2026-07-26 | **Pillar-C C0-a/b PASS**：`c0_mech_20260726_172201`@grj-w0；rate=0→`torch_trace=0`/`timing=28`；mid SET `0.05→1.0`→TT **29→309**（Δ=280）；Python 已同步两边 probe-bundle；`MECH_FIX.md` 勾放行；**可开 E1/E2**；E1-off/C0-c 并行 |
| 2026-07-26 | **Pillar C v2 重开**：对照 EVAL-GAP §2；旧 cold 三臂/`COLD_MAX`/mid_set 标 **SUPERSEDED**；新增 [`PILLAR_C_QUEUE.md`](PILLAR_C_QUEUE.md)；重写 `PILLAR_C_RUNNER`/`LOOP`/`LOOP_PROMPT` 对齐 E1–E4；主尺=总落盘；C0=§2.0 三缺口（SET→live / rate=0 / 窗）；产物目录 `pillar_c_v2/`；Dose 代表+扩展已收官，C 为主 |
| 2026-07-26 | **XPUTimer masked 对照 P3-EXT-B DONE**：`contrast-p3-ext-b-masked-20260726_160000`@yysong-w2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`（=quiet lean）；自主 hang/slow=**0**；跨-run coll=**1.023** FAIL（thr1.05）；dose_check step_ms=**1.113** PASS（金标≈1.078；fio-3.29 held≈39s@~19.4MiB/s）；detect_mode=`cross_run_contrast`；detect_ok=no；与 masked GH `155151`（no_bite）同向无咬合；未改对手阈值；未覆盖 Probing 分；未占 grj；w2 已 IDLE；DOSE_QUEUE masked XPU=DONE；**P3-EXT-B Quiet+Masked 全齐 → 扩展集收官** |
| 2026-07-26 | **Greyhound masked 对照 P3-EXT-B DONE**：`contrast-p3-ext-b-masked-20260726_155151`@yysong-w2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`（=quiet lean）；coll=**1.024** FAIL（thr1.05）；Rbeast collect_seq C1/C0 cp=**0/0** miss；step_ms=**0.777** dose_WEAK（金标≈1.078；fio-3.29 held≈39s@~19.5MiB/s）；detect_mode=`no_bite`；detect_ok=no；与 quiet/Loud GH 同向无咬合；未改对手阈值；未覆盖 Probing 分；未占 grj；w2 已 IDLE 交 XPU；DOSE_QUEUE masked GH=DONE |
| 2026-07-26 | **XPUTimer quiet 对照 P3-EXT-B DONE**：`contrast-p3-ext-b-quiet-20260726_073224`@yysong-w2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`；自主 hang/slow=**0**；跨-run coll=**0.977** FAIL（thr1.15）；dose_check step_ms=**1.256** PASS（金标≈1.709；fio-3.29 held≈38s@~20.6MiB/s）；detect_mode=`cross_run_contrast`；detect_ok=no；与 quiet GH `072233`（no_bite）同向无咬合；未改对手阈值；未覆盖 Probing 分；未占 grj；w2 已 IDLE；DOSE_QUEUE quiet XPU=DONE；P3-EXT-B quiet 格齐 |
| 2026-07-26 | **Greyhound quiet 对照 P3-EXT-B DONE**：`contrast-p3-ext-b-quiet-20260726_072233`@yysong-w2；dose `fio_nj=4,iodepth=16,bs=4k,size=1G,ckpt_every=50,io_read_kb=256`；coll=**1.015** FAIL（thr1.15）；Rbeast collect_seq C1/C0 cp=**0/0** miss；step_ms=**1.020** dose_WEAK（金标≈1.709@grj；fio-3.29 sidecar held≈38s@~20MiB/s）；detect_mode=`no_bite`；detect_ok=no；与 Loud GH `122204` 同向无咬合；先轮 stress-ng `071352` dose_WEAK 保留；未改对手阈值；未覆盖 Probing 分；未占 grj；w2 已 IDLE；DOSE_QUEUE quiet GH=DONE；XPU 后补 DONE `073224` |
| 2026-07-26 | **XPUTimer masked 对照 P3-SW-C DONE**：`contrast-p3-sw-c-masked-20260726_141517`@yysong-w2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`（=quiet lean）；自主 hang/slow=**0**；跨-run coll=**1.056** PASS（thr1.05）；dose_check step_ms=**1.517** PASS（金标≈1.649）；detect_mode=`cross_run_contrast`；detect_ok=yes；与 quiet XPU `132040`（coll FAIL）不同向、本格跨-run 刚过 thr；同档 GH masked `140713` 仍 no_bite；live_stress=0；未改对手规则；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P3-SW-C masked 格齐 |
| 2026-07-26 | **Greyhound masked 对照 P3-SW-C DONE**：`contrast-p3-sw-c-masked-20260726_140713`@yysong-w2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`（=quiet lean）；coll=**1.023** FAIL（thr1.05）；Rbeast C1/C0 cp=**0/0** miss；step_ms=**0.988** dose_WEAK（金标≈1.649；sidecar START@100→stop@300 ok）；detect_mode=`no_bite`；detect_ok=no；与 quiet GH `131244` 同向无咬合（Loud GH `143448` 曾 Rbeast hit）；live_busy=0 / 僵尸 stress 不占卡；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE；XPU 后补 DONE `141517` |
| 2026-07-26 | **XPUTimer quiet 对照 P3-SW-C DONE**：`contrast-p3-sw-c-quiet-20260726_132040`@yysong-w2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`；自主 hang/slow=**0**；跨-run coll=**1.000** FAIL（thr1.15）；dose_check step_ms=**1.960** PASS（金标≈1.95）；detect_mode=`cross_run_contrast`；detect_ok=no；与 quiet GH `131244`（no_bite）同向无咬合，但本格 dose_check PASS（GH 曾 dose_WEAK）；live_stress=0；未改对手规则；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE；P3-SW-C quiet 格齐 |
| 2026-07-26 | **Greyhound quiet 对照 P3-SW-C DONE**：`contrast-p3-sw-c-quiet-20260726_131244`@yysong-w2；dose `cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32`；coll=**1.000** FAIL；Rbeast C1/C0 cp=**0/0** miss；step_ms=**0.799** dose_WEAK（thr1.15；金标≈1.95；sidecar START@step100→stop@300 ok）；detect_mode=`no_bite`；detect_ok=no；与 Loud GH `143448`（Rbeast hit）不同向——quiet 剂量对 CCL/Rbeast 弱可见；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P3-SW-C masked PROBING_SCORED D3**：formal `20260726_135016`@grj-m0 C0+C1+C2 DUMP=0；=quiet lean `80@70 leak=2 max=32` C1/C0=**1.649** PASS(thr1.05)；C0=**90.26** CLEAN（闸误报后 salvage C1C2）；offline **D3** + SQL_PENDING；pilot`131921`=1.050 CALIBRATED；stub`131312`/`132433`/`133349` C0_noise D0 保留；CONTRAST_QUEUE 入队 GH+XPU masked PENDING；未回调 SQL/阈值；未占 w2；Loud 金标仍 `135238`≈2.33 D4 |
| 2026-07-26 | **Dose P3-SW-C masked formal retry tip_fail D0**：formal retry `20260726_133349`@grj-m0 同参 thr1.05；C1/C0=**0.576** ineffective → **D0**；前 stub`132433`=1.03 保留；pilot`131921`=1.050 仍 CALIBRATED；未入队 GH/XPU；未拧剂/未改闸；未占 w2 |
| 2026-07-26 | **Dose P3-SW-C masked formal tip_fail D0**：formal `20260726_132433`@grj-m0 C0+C1+C2 DUMP=0；=quiet lean `80@70 leak=2 max=32` C1/C0=**1.03** ineffective（thr1.05）→ **D0**；pilot`131921`=1.050 CALIBRATED 保留；stub`131312`=1.02 保留；未入队 GH/XPU；未回调 SQL/阈值；未占 w2 |
| 2026-07-26 | **Dose P3-SW-C quiet PROBING_SCORED D3**：formal `20260726_125953`@grj-m0 C0+C1+C2 DUMP=0；`cpu_n=80,cpu_load=70,mb=1,leak_every=2.0,max_chunks=32` C1/C0=**1.95** PASS(thr1.15)；C2/C0=**1.05**；offline **D3**（max_data→rank_7）+ SQL_PENDING；pilot`123508`=1.91 CALIBRATED；stub`124037`=1.01/`124908`=1.14 FAIL_WEAK 保留；跳板 nohup hold_exec；CONTRAST_QUEUE/DOSE_QUEUE 入队 GH+XPU quiet PENDING；未回调 SQL/阈值；未占 w2；Loud 金标仍 `135238`≈2.33 D4 |
| 2026-07-26 | **XPUTimer masked 对照 P3-EXT-C DONE**：`contrast-p3-ext-c-masked-20260726_124743`@yysong-w2；dose `vm_n=32,vm_bytes=4G`；自主 hang/slow=**0**；跨-run coll=**1.095** PASS（thr1.05）；dose_check step_ms=**1.047** dose_WEAK（金标≈1.744）；detect_mode=`cross_run_contrast`；detect_ok=yes；与 quiet XPU `105344`（coll FAIL）不同向、本格跨-run 刚过 thr；live_stress=0；未改对手规则；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P3-EXT-C masked 格齐 |
| 2026-07-26 | **Greyhound masked 对照 P3-EXT-C DONE**：`contrast-p3-ext-c-masked-20260726_123500`@yysong-w2；dose `vm_n=32,vm_bytes=4G`；coll=**1.029** FAIL（thr1.05）；Rbeast C1/C0 cp=**0/0** miss；step_ms=**1.542** dose_OK（金标≈1.744）；detect_mode=`no_bite`；detect_ok=no；与 quiet/Loud GH 同向（stress_vm 对 CCL/Rbeast 结构性弱可见）；live_stress=0；preexist zombie≈433 不占卡；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **XPUTimer quiet 对照 P3-EXT-C DONE**：`contrast-p3-ext-c-quiet-20260726_105344`@yysong-w2；dose `vm_n=32,vm_bytes=4G`；自主 hang/slow=**0**；跨-run coll=**0.992** FAIL（thr1.15）；dose_check step_ms=**1.075** dose_WEAK（金标≈1.906；stress-ng-vm 部分 exit=5）；detect_mode=`cross_run_contrast`；detect_ok=no；与 quiet GH `104300` / Loud XPU `121535` 同向；live_stress=0；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE；P3-EXT-C quiet 格齐 |
| 2026-07-26 | **Greyhound quiet 对照 P3-EXT-C DONE**：`contrast-p3-ext-c-quiet-20260726_104300`@yysong-w2；dose `vm_n=32,vm_bytes=4G`；coll=**1.017** FAIL；Rbeast C1/C0 cp=**0/0** miss；step_ms=**0.624** dose_WEAK（thr1.15；金标≈1.906；PAGEIN_PARTIAL +4Gi）；detect_mode=`no_bite`；detect_ok=no；与 Loud GH `124257` 同向（stress_vm 对 CCL/Rbeast 结构性弱可见）；live_stress=0 / AICore=0；preexist zombie≈433（PPID=1）不占卡；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P3-EXT-C quiet PROBING_SCORED D3**：formal `20260726_102936`@grj-m0 C0+C1+C2 DUMP=0；`vm_n=32,vm_bytes=4G` C1/C0=**1.906** PASS(thr1.15)；C2/C0=**1.089**；offline **D3** + SQL_PENDING；pilot`095909`=1.722 CALIBRATED；stub`100606`/`101737` C0_noise ineffective 保留；跳板 nohup hold_exec；CONTRAST_QUEUE/DOSE_QUEUE 入队 GH+XPU quiet PENDING；未回调 SQL/阈值；未占 w2；Loud 金标仍 `021906`≈1.59 D3 |
| 2026-07-26 | **XPUTimer masked 对照 P3-EXT-A DONE**：`contrast-p3-ext-a-masked-20260726_100915`@yysong-w2；dose `cpu_n=128,cpu_load=70`；自主 hang/slow=**0**；跨-run coll=**0.939** FAIL（thr1.05）；dose_check step_ms=**0.674** dose_WEAK（金标≈1.470）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P3-EXT-A masked 格齐 |
| 2026-07-26 | **Greyhound masked 对照 P3-EXT-A DONE**：`contrast-p3-ext-a-masked-20260726_095858`@yysong-w2；dose `cpu_n=128,cpu_load=70`；coll=**1.009** FAIL；Rbeast C1/C0 cp=**0/0** miss；step_ms=**0.789** dose_WEAK（thr1.05；金标≈1.470）；detect_mode=`no_bite`；detect_ok=no；与 quiet/Loud GH 同向（host CPU 抢占对 CCL/Rbeast 结构性不可见）；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P3-EXT-A masked PROBING_SCORED D3**：formal `20260726_094648`@grj-m0 C0+C1+C2 DUMP=0；`cpu_n=128,cpu_load=70`（=quiet lean；起点 2@50 咬空）C1/C0=**1.470** PASS(thr1.05)；C2/C0=**1.908**；offline **D3** + SQL_PENDING；pilot`083742`=1.073 CALIBRATED；stub`080947`/`@50 formals`/`@70+DUMP hang` 保留；跳板 nohup；CONTRAST_QUEUE/DOSE_QUEUE 入队 GH+XPU masked PENDING；未回调 SQL/阈值；未占 w2；Loud 金标仍 `231918`/`001251`≈1.97 D3 |
| 2026-07-26 | **XPUTimer quiet 对照 P3-EXT-A DONE**：`contrast-p3-ext-a-quiet-20260726_081751`@yysong-w2；dose `cpu_n=128,cpu_load=70`；自主 hang/slow=**0**；跨-run coll=**1.056** FAIL（thr1.15）；dose_check step_ms=**1.248** PASS（金标≈1.256）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE；P3-EXT-A quiet 格齐 |
| 2026-07-26 | **Greyhound quiet 对照 P3-EXT-A DONE**：`contrast-p3-ext-a-quiet-20260726_080959`@yysong-w2；dose `cpu_n=128,cpu_load=70`；coll=**0.933** FAIL；Rbeast C1/C0 cp=**0/0** miss；step_ms=**0.766** dose_WEAK（thr1.15；金标≈1.256）；detect_mode=`no_bite`；detect_ok=no；与 Loud GH `114502` 同向（host CPU 抢占对 CCL/Rbeast 结构性不可见）；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **XPUTimer masked 对照 P1-SW-B DONE**：`contrast-p1-sw-b-masked-20260726_074217`@yysong-w2；dose `rare_seq=1408,every=1`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.05）；dose_check step_ms=**1.303** PASS（金标≈1.287）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P1-SW-B masked 格齐 |
| 2026-07-26 | **Dose P3-EXT-A quiet PROBING_SCORED D3**：formal `20260726_075912`@grj-m0 C0+C1+C2；`cpu_n=128,cpu_load=70` C1/C0=**1.256** PASS(thr1.15)；offline **D3** + SQL_NO_EXT_EVIDENCE；pilot`074315`=1.674 CALIBRATED；stub formal`074957`=1.131 FAIL_WEAK 保留；跳板 nohup hold_exec；CONTRAST_QUEUE/DOSE_QUEUE 入队 GH+XPU quiet PENDING；未回调 SQL/阈值；未占 w2；Loud 金标仍 `231918`/`001251`≈1.97 D3 |
| 2026-07-26 | **Greyhound masked 对照 P1-SW-B DONE**：`contrast-p1-sw-b-masked-20260726_073636`@yysong-w2；dose `rare_seq=1408,every=1`；coll=**0.991** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.299** dose_OK（thr1.05；金标≈1.287）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P1-SW-B masked PROBING_SCORED D3**：formal `20260726_072137`@grj-m0 C0+C1+C2；`rare_seq=1408,every=1`（=quiet；起点 1152/8 咬空后向 quiet 靠）C1/C0=**1.287** PASS(thr1.05)；offline **D3**（shape_seq_rare→rank_7）；SQL_PENDING（C2 dump 窗本地 hold 断）；pilot`071428`=1.283 CALIBRATED；stub`070504`/`070958` ineffective 保留；CONTRAST_QUEUE 入队 GH+XPU masked PENDING；未回调 SQL/阈值；未占 w2 |
| 2026-07-26 | **XPUTimer quiet 对照 P1-SW-B DONE**：`contrast-p1-sw-b-quiet-20260726_070702`@yysong-w2；dose `rare_seq=1408,every=1`；自主 hang/slow=**0**；跨-run coll=**0.965** FAIL（thr1.15）；dose_check step_ms=**1.285** PASS（金标≈1.288）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE；P1-SW-B quiet 格齐 |
| 2026-07-26 | **Greyhound quiet 对照 P1-SW-B DONE**：`contrast-p1-sw-b-quiet-20260726_070049`@yysong-w2；dose `rare_seq=1408,every=1`；coll=**1.018** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.297** dose_OK（thr1.15；金标≈1.288）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P1-SW-B quiet PROBING_SCORED D3**：formal `20260726_052307`@grj-m0 C0+C1+C2；`rare_seq=1408,every=1` C1/C0=**1.288** PASS(thr1.15)；offline **D3**（shape_seq_rare→rank_7；score 按 recipes rare_seq）+ SQL_NO_EXT no D4；pilot`051822`=1.284 CALIBRATED；stub`045940`/`050506`/`051023` ineffective 保留；`045618`作废；CONTRAST_QUEUE 入队 GH+XPU quiet PENDING；未碰 w2；<loud1.36 |
| 2026-07-26 | **XPUTimer masked 对照 P1-SW-A DONE**：`contrast-p1-sw-a-masked-20260726_050018`@yysong-w2；dose `chunks=1,stall_mb=64,stall_s=0.02`；自主 hang/slow=**0**；跨-run coll=**0.983** FAIL（thr1.05）；dose_check step_ms=**1.243** PASS（金标≈1.259）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P1-SW-A masked 格齐 |
| 2026-07-26 | **Greyhound masked 对照 P1-SW-A DONE**：`contrast-p1-sw-a-masked-20260726_045441`@yysong-w2；dose `chunks=1,stall_mb=64,stall_s=0.02`；coll=**0.981** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.237** dose_OK（thr1.05；金标≈1.259）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **XPUTimer quiet 对照 P1-SW-A DONE**：`contrast-p1-sw-a-quiet-20260726_044734`@yysong-w2；dose `chunks=3,stall_mb=128,stall_s=0.05`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.15）；dose_check step_ms=**1.647** PASS（金标≈1.638）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE；P1-SW-A quiet 格齐 |
| 2026-07-26 | **Dose P1-SW-A masked PROBING_SCORED D3**：formal `20260726_044454`@grj-m0 C0+C1+C2；`chunks=1,stall_mb=64,stall_s=0.02` C1/C0=**1.259** PASS(thr1.05)；offline D3（min_compute→rank_7）+ SQL_NO_EXT_EVIDENCE；pilot`044124`=1.250 CALIBRATED；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 w2；<quiet1.638/<loud4.20 |
| 2026-07-26 | **Greyhound quiet 对照 P1-SW-A DONE**：`contrast-p1-sw-a-quiet-20260726_044118`@yysong-w2；dose `chunks=3,stall_mb=128,stall_s=0.05`；coll=**1.028** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.647** dose_OK（thr1.15；金标≈1.638）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **XPUTimer masked 对照 P1-EXT-B DONE**：`contrast-p1-ext-b-masked-20260726_042922`@yysong-w2；dose `mb=192,copies=10`；自主 hang/slow=**0**；跨-run coll=**0.982** FAIL（thr1.05）；dose_check step_ms=**1.066** PASS（金标≈1.070）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P1-EXT-B masked 格齐 |
| 2026-07-26 | **Dose P1-SW-A quiet PROBING_SCORED D3**：formal `20260726_042922`@grj-m0 C0+C1+C2；`chunks=3,stall_mb=128,stall_s=0.05` C1/C0=**1.638** PASS(thr1.15)；offline D3（min_compute→rank_7）+ SQL_NO_EXT_EVIDENCE；pilot`042251`=1.639 CALIBRATED；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 w2；Loud 金标仍 `114556` chunks12/768/0.25 |
| 2026-07-26 | **Greyhound masked 对照 P1-EXT-B DONE**：`contrast-p1-ext-b-masked-20260726_042310`@yysong-w2；dose `mb=192,copies=10`；coll=**1.009** FAIL；Rbeast C1/C0 cp=**0/0** miss；step_ms=**1.076** dose_OK（thr1.05；金标≈1.070）；detect_mode=`no_bite`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P1-EXT-B masked PROBING_SCORED D3**：formal `20260726_040309`@grj-m0 C0+C1+C2；`mb=192,copies=10` C1/C0=**1.070** PASS(thr1.05)；offline D3（min_compute→rank_7）+ SQL_NO_EXT_EVIDENCE；pilot`035440`=1.075 CALIBRATED；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 w0/w2；Loud 金标仍 `014350` 512×48 |
| 2026-07-26 | **XPUTimer quiet 对照 P1-EXT-B DONE**：`contrast-p1-ext-b-quiet-20260726_040543`@yysong-w2；dose `mb=256,copies=16`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.15）；dose_check step_ms=**1.168** PASS（金标≈1.161）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE；P1-EXT-B quiet 格齐 |
| 2026-07-26 | **Greyhound quiet 对照 P1-EXT-B DONE**：`contrast-p1-ext-b-quiet-20260726_035533`@yysong-w2；dose `mb=256,copies=16`；coll=**1.009** FAIL；Rbeast C1/C0 cp=**2/0** hit；step_ms=**1.170** dose_OK（thr1.15；金标≈1.161）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE；XPU 仍 PENDING |
| 2026-07-26 | **Dose P1-EXT-B quiet PROBING_SCORED D3**（扩展集首格）：formal `20260726_033758`@grj-m0；`mb=256,copies=16` C1/C0=**1.161** PASS(thr1.15)；offline D3 + SQL_NO_EXT；pilot`033227`=1.174；stub`032111`→1.015 ineffective 保留；DOSE_QUEUE GH/XPU=PENDING；代表集全齐后开扩展；未回调 SQL；未碰 w0/w2 |
| 2026-07-26 | **Dose 代表集收官**：P3-SW-A/B、P1-HW-B、P1-EXT-A、P1-SW-C 的 Quiet+Masked 均 PROBING_SCORED + GH_DONE + XPU_DONE；Pillar C C-1/C-2/C-3 已收口；按 DOSE_QUEUE 进扩展集（首格 P1-EXT-B quiet） |
| 2026-07-26 | **XPUTimer masked 对照 P1-SW-C DONE**：`contrast-p1-sw-c-masked-20260726_031321`@yysong-w2；dose `n=768,every=4,fallback_s=0.05`；自主 hang/slow=**0**；跨-run coll=**0.983** FAIL（thr1.05）；median step_ms=**1.005** 盲；tip max=**2.462** PASS（p99=2.839；金标 tip≈2.61；辅闸 max≥2.5 略欠）；detect_mode=`cross_run_contrast`；detect_ok=no；SPIKE_OK×50；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE；P1-SW-C masked 代表格齐 |
| 2026-07-26 | **Greyhound masked 对照 P1-SW-C DONE**：`contrast-p1-sw-c-masked-20260726_030130`@yysong-w2；dose `n=768,every=4,fallback_s=0.05`；coll=**0.973** FAIL；Rbeast C1/C0 cp=**0/0** miss；median step_ms=**1.002** 盲；tip max=**3.242** PASS（金标 tip≈2.61）；detect_mode=`no_bite`；detect_ok=no；SPIKE_OK×50；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE |
| 2026-07-26 | **Dose P1-SW-C masked PROBING_SCORED D3**：formal `20260726_025116`@grj-m0 C0+C1+C2；`n=768,every=4,fallback_s=0.05` tip max=**2.61** BITE_OK；offline D3 + SQL_NO_EXT；pilot`024514` tip max=2.53 CALIBRATED；stub`023225`/retune`023811` tip_fail 保留；DOSE_QUEUE GH/XPU=PENDING；未回调 SQL；未碰 w0/w2 |
| 2026-07-26 | **Dose P1-SW-C masked CALIBRATED**：pilot retune2 `20260726_024514`@grj-m0；`n=768,every=4,fallback_s=0.05` tip max=**2.53** BITE_OK；同参 formal 接续 |
| 2026-07-26 | **XPUTimer quiet 对照 P1-SW-C DONE**：`contrast-p1-sw-c-quiet-20260726_023654`@yysong-w2；dose `n=768,every=4,fallback_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.15）；median step_ms=**1.004** 盲；tip max=**1.293** FAIL（金标 tip≈2.61；辅闸 max≥2.5；C1 tip@100=944.7 / C0 max=730.8）；detect_mode=`cross_run_contrast`；detect_ok=no；SPIKE_OK×50；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE |
| 2026-07-26 | **Greyhound quiet 对照 P1-SW-C DONE**：`contrast-p1-sw-c-quiet-20260726_022611`@yysong-w2；dose `n=768,every=4,fallback_s=0.1`；coll=**1.029** FAIL；Rbeast C1/C0 cp=**0/0** miss；median step_ms=**1.006** 盲；tip max=**2.727** PASS（金标 tip≈2.61）；detect_mode=`no_bite`；detect_ok=no；SPIKE_OK×50；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE |
| 2026-07-26 | **Pillar-C C-3 mid_set 复测 SET_OK**：parent=`20260726_022234-pillar-c-c3-midset-p3swa-r3`@grj-w0；P3-SW-A loud；pod-watcher L=**254** SHOW TABLES→SET rate=1.0；latency=**889**ms；cold≈**9.64**MiB/segs=16（vs full 161.52≈6.0%；≈C-2~9.1）；前轮`021042`/`021709` INVALID；`DETECT.md`；未占 m0 |
| 2026-07-26 | **Dose P1-SW-C quiet PROBING_SCORED D3**：formal retry `20260726_021606`@grj-m0 C0+C1+C2；`n=768,every=4,fallback_s=0.1` tip max=**2.61** BITE_OK；offline D3（min_compute_at_tip→rank_7）+ SQL_NO_EXT_EVIDENCE；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；前次`020857` tip_fail D0 保留；未回调 SQL/阈值；未碰 grj-w0/w2；pilot`015328` tip max=5.72 |
| 2026-07-26 | **XPUTimer masked 对照 P1-EXT-A DONE**：`contrast-p1-ext-a-masked-20260726_021212`@yysong-w2；dose `size=4096,mm=16`；自主 hang/slow=**0**；跨-run coll=**1.009** FAIL（thr1.05）；dose_check step_ms=**1.077** PASS（金标≈1.078）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE |
| 2026-07-26 | **Pillar-C C-3 mid_set DONE（SET@250 FAIL）**：parent=`20260726_021042-pillar-c-c3-midset-p3swa`@grj-w0；P3-SW-A loud；cold≈**9.56**MiB/segs=16（vs full 161.52≈5.9%；≈C-2~9.1）；无 `set_upgrade.log`/无 step_250.marker（仅 100/300）；hold_exec FIRE_OK 后退出致 SET 未发；`DETECT.md`；未占 m0 |
| 2026-07-26 | **Dose P1-SW-C quiet formal tip_fail D0**：formal `20260726_020857`@grj-m0 C0+C1+C2；`n=768,every=4,fallback_s=0.1` victim tip max=**2.28**（<辅闸2.5）→ tip accept ineffective；offline/SQL **D0**；SPIKE_OK；pilot`015328` tip max=5.72 仍 CALIBRATED；未入队 GH/XPU；未回调 SQL；未碰 grj-w0/w2；建议同参再 formal |
| 2026-07-26 | **Greyhound masked 对照 P1-EXT-A DONE**：`contrast-p1-ext-a-masked-20260726_015409`@yysong-w2；dose `size=4096,mm=16`；coll=**1.000** FAIL；Rbeast C1/C0 cp=**0/0** miss；dose_check step_ms=**1.072** PASS（thr1.05；金标≈1.078）；detect_mode=`no_bite`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE |
| 2026-07-26 | **Pillar-C C-2 COLD_MAX 扫完**：parent=`20260726_015019-pillar-c-c2-coldmax-p3swa`@grj-w0；P3-SW-A loud；COLD_MAX=128/256/512 → cold≈**9.09/9.16/9.09** MiB；抬预算未抬冷量（未触顶）；`COLDMAX.md`；未占 m0 |
| 2026-07-26 | **Dose P1-SW-C quiet CALIBRATED**：pilot stub `20260726_015328`@grj-m0 C0+C1；`n=768,every=4,fallback_s=0.1` tip max C1/C0=**5.72** BITE_OK（med_thr1.15；median盲=1.02）；recipes `status=calibrated`；retune`020226` n=512→victim max=2.34 ineffective 保留；未跑 formal/GH；未回调 SQL；未碰 grj-w0/w2 |
| 2026-07-26 | **Dose P1-EXT-A masked PROBING_SCORED D3**：formal `20260726_014611`@grj-m0 C0+C1+C2；`size=4096,mm=16` C1/C0=**1.078** PASS(thr1.05)；offline D3（min_wait→rank_8 ±1 hit victim=7）+ SQL_NO_EXT_EVIDENCE（attach fail）；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0/w2；pilot`014128`=1.078；Loud 金标仍 D2 |
| 2026-07-26 | **XPUTimer quiet 对照 P1-EXT-A DONE**：`contrast-p1-ext-a-quiet-20260726_014833`@yysong-w2；dose `size=4096,mm=32`；自主 hang/slow=**0**；跨-run coll=**0.982** FAIL（thr1.15）；dose_check step_ms=**1.158** PASS（金标≈1.156）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE |
| 2026-07-26 | **Greyhound quiet 对照 P1-EXT-A DONE**：`contrast-p1-ext-a-quiet-20260726_013844`@yysong-w2；dose `size=4096,mm=32`；coll=**0.991** FAIL；Rbeast C1/C0 cp=**2/0** hit；dose_check step_ms=**1.164** PASS（thr1.15；金标≈1.156）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE |
| 2026-07-26 | **Dose P1-EXT-A masked CALIBRATED**：retune pilot `20260726_014128`@grj-m0 C0+C1；`size=4096,mm=16` C1/C0=**1.078** PASS(thr1.05；<quiet1.156)；recipes `status=calibrated`；stub`013734` 2048×1→0.998 ineffective 保留；未跑 formal/GH；未回调 SQL；未碰 grj-w0/w2 |
| 2026-07-26 | **Pillar-C P1-EXT-A 阴性 SAMPLE_MS 打平**：parent=`20260726_010201-pillar-c-p1-ext-a-loud`；`probing_collapse_neg` SAMPLE_MS=50 + SET↑（SHOW TABLES）；cold≈**48.62** vs full **150.01**（32.4%）；**NEGATIVE_FAIL**；旧 500ms 塌缩作废；未占 m0 |
| 2026-07-26 | **Dose P1-EXT-A quiet PROBING_SCORED D3**：formal `20260726_013034`@grj-m0 C0+C1+C2；`size=4096,mm=32` C1/C0=**1.156** PASS(thr1.15)；offline D3（min_wait→rank_7）+ SQL_NO_EXT_EVIDENCE（attach fail）；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0/w2；pilot`012545`=1.159；Loud 金标仍 D2 |
| 2026-07-26 | **Pillar-C Runner P1-SW-C 三臂齐**：parent=`20260726_012627-pillar-c-p1-sw-c-loud`@grj-w0；INLINE 2c n=1024/every=1/fallback=0.25；`full_fidelity` cold≈**139.46**MiB；`probing_collapse`≈**6.08**MiB（SET↑ OK）；`naive_downsample`≈**6.19**MiB；塌缩/全量≈**4.4%**；`VOLUME_RATIO.md`；EXT-A 阴性仍 INVALID；未判 D；未占 m0 |
| 2026-07-26 | **Dose P1-EXT-A quiet CALIBRATED**：retune pilot `20260726_012545`@grj-m0 C0+C1；`size=4096,mm=32` C1/C0=**1.159** PASS(thr1.15；<loud3.87)；recipes `status=calibrated`；stub`012159` 2048×4→0.990 ineffective 保留；未跑 formal/GH；未回调 SQL；未碰 grj-w0/w2 |
| 2026-07-26 | **Pillar-C：P1-EXT-A 阴性冷段 blocker → 转 P1-SW-C**：EXT-A parent=`20260726_010201-pillar-c-p1-ext-a-loud`；SET↑ 形式 OK 但 retry cold≈7.56/full=150（SAMPLE_MS=500）；collapse **INVALID as 阴性打平**；开火 P1-SW-C parent=`20260726_012627-pillar-c-p1-sw-c-loud` `20260726_012627-pillar-c-p1-sw-c-loud-full_fidelity`；未占 m0 |
| 2026-07-26 | **XPUTimer masked 对照 P1-HW-B DONE**：`contrast-p1-hw-b-masked-20260726_012822`@yysong-w2；dose `mb=256,copies=4→24,ramp=1`；自主 hang/slow=**0**；跨-run coll=**0.982** FAIL（thr1.05）；dose_check step_ms=**1.125** PASS（金标≈1.129）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE |
| 2026-07-26 | **Greyhound masked 对照 P1-HW-B DONE**：`contrast-p1-hw-b-masked-20260726_012256`@yysong-w2；dose `mb=256,copies=4→24,ramp=1`；coll=**0.991** FAIL；Rbeast C1/C0 cp=**0/0** miss；dose_check step_ms=**1.153** PASS（thr1.05；金标≈1.129）；detect_mode=`no_bite`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked GH=DONE |
| 2026-07-26 | **Pillar-C P1-EXT-A collapse SET↑ retry**：parent=`20260726_010201-pillar-c-p1-ext-a-loud`；根因=空 `query`/父 PID；路径改 `SHOW TABLES`→worker；`probing_collapse_retry` cold≈**7.56**MiB（full=150.01；原臂 INVALID 7.68）；SET=OK；未占 m0 |
| 2026-07-26 | **Dose P1-HW-B masked PROBING_SCORED D3**：formal `20260726_011501`@grj-m0 C0+C1+C2；`mb=256,copies=4→24,ramp=1` C1/C0=**1.129** PASS(thr1.05)；offline D3（min_compute→rank_7）+ SQL_NO_EXT_EVIDENCE（attach fail）；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0/w2；pilot`011036`=1.128 |
| 2026-07-26 | **XPUTimer quiet 对照 P1-HW-B DONE**：`contrast-p1-hw-b-quiet-20260726_011651`@yysong-w2；dose `mb=320,copies=5→30,ramp=1`；自主 hang/slow=**0**；跨-run coll=**1.000** FAIL（thr1.15）；dose_check step_ms=**1.237** PASS（金标≈1.219）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE |
| 2026-07-26 | **Pillar-C Runner P1-EXT-A 三臂齐（阴性对照）**：parent=`20260726_010201-pillar-c-p1-ext-a-loud`@grj-w0；INLINE cube 8192×64；`full_fidelity` cold≈**150.01**MiB；`probing_collapse`≈**7.68**MiB（**SET↑ FAIL**）；`naive_downsample`≈**8.03**MiB；塌缩/全量≈**5.1%**（SET 失败致偏小，非阴性打平）；`VOLUME_RATIO.md`；未判 D；未占 m0 |
| 2026-07-26 | **Dose P1-HW-B masked CALIBRATED**：retune pilot `20260726_011036`@grj-m0 C0+C1；`mb=256,copies=4→24,ramp=1` C1/C0=**1.128** PASS(thr1.05；<quiet1.219)；recipes `status=calibrated`；stub`010252` mb=128/copies=2→12→1.025 ineffective 保留；未跑 formal/GH；未回调 SQL；未碰 grj-w0/w2 |
| 2026-07-26 | **Greyhound quiet 对照 P1-HW-B DONE**：`contrast-p1-hw-b-quiet-20260726_010435`@yysong-w2；dose `mb=320,copies=5→30,ramp=1`；coll=**0.972** FAIL；Rbeast C1/C0 cp=**2/0** hit；dose_check step_ms=**1.213** PASS（thr1.15；金标≈1.219）；detect_mode=`autonomous`；detect_ok=yes；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE |
| 2026-07-26 | **Dose P1-HW-B quiet PROBING_SCORED D3**：formal `20260726_005203`@grj-m0 C0+C1+C2；`mb=320,copies=5→30,ramp=1` C1/C0=**1.219** PASS(thr1.15)；offline D3（min_compute→rank_7）+ SQL_NO_EXT_EVIDENCE（attach fail）；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0/w2；pilot`004220`=1.218 |
| 2026-07-26 | **Pillar-C Runner P1-HW-B 三臂齐**：parent=`20260726_001353-pillar-c-p1-hw-b-loud`@grj-w0；INLINE 1b ramp mb=512 copies=6→48；`full_fidelity` cold≈**137.72**MiB；`probing_collapse`≈**6.11**MiB（SET↑）；`naive_downsample`≈**18.02**MiB（无 SET↑，COLD_MAX=256）；塌缩/全量冷段≈**4.4%**；`VOLUME_RATIO.md`；未判 D；未占 m0 |
| 2026-07-26 | **XPUTimer masked 对照 P3-SW-B DONE**：`contrast-p3-sw-b-masked-20260726_005624`@yysong-w2；dose `mb=6,stall_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.024** FAIL（thr1.05）；dose_check step_ms=**1.255** PASS（金标≈1.909）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE masked XPU=DONE |
| 2026-07-26 | **Greyhound masked 对照 P3-SW-B DONE**：`contrast-p3-sw-b-masked-20260726_003712`@yysong-w2；dose `mb=6,stall_s=0.1`；coll=**0.929** FAIL；Rbeast C1/C0 cp=**0**/0 miss；dose_check step_ms=**2.070** PASS（thr1.05；金标≈1.909）；detect_mode=`no_bite`；detect_ok=no；Agent PING timeout 后据远端 VERDICT 收口；DOSE_QUEUE masked GH=DONE；未改对手规则 |
| 2026-07-26 | **Dose P1-HW-B quiet CALIBRATED**：retune pilot `20260726_004220`@grj-m0 C0+C1；`mb=320,copies=5→30,ramp=1` C1/C0=**1.218** PASS(thr1.15；<loud1.57)；recipes `status=calibrated`；stub`003744` mb=256/copies=4→24→1.129 FAIL_WEAK 保留；未跑 formal/GH；未回调 SQL；未碰 grj-w0/w2 |
| 2026-07-26 | **Dose P3-SW-B masked PROBING_SCORED D3**：formal `20260726_000113`@grj-m0 C0+C1+C2；`mb=6,stall_s=0.1` C1/C0=**1.909** PASS(thr1.05)；offline D3（victim rank_7）；SQL_PENDING（dump 挂 SHOW TABLES→C2 step414 停滞 partial）；DOSE_QUEUE probing=SCORED D3、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0；pilot`235216`=1.389 |
| 2026-07-26 | **Pillar-C Runner P3-SW-B 三臂齐**：parent=`20260725_233537-pillar-c-p3-sw-b-loud`@grj-w0；INLINE 8b mb=16/stall=0.25；`full_fidelity` cold≈**152.1**MiB；`probing_collapse`≈**7.79**MiB（SET↑）；`naive_downsample`≈**15.71**MiB（无 SET↑，COLD_MAX=256）；塌缩/全量冷段≈**5.1%**；`VOLUME_RATIO.md`；未判 D；未占 m0 |
| 2026-07-26 | **XPUTimer quiet 对照 P3-SW-B DONE**：`contrast-p3-sw-b-quiet-20260726_000506`@yysong-w2；dose `mb=8,stall_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.016** FAIL（thr1.15）；dose_check step_ms=**1.125** FAIL（金标≈2.101；同档 GH quiet≈1.213）；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet XPU=DONE |
| 2026-07-25 | **Greyhound quiet 对照 P3-SW-B DONE**：`contrast-p3-sw-b-quiet-20260725_235209`@yysong-w2；dose `mb=8,stall_s=0.1`；coll=**0.943** FAIL；Rbeast C1/C0 cp=**0/0** miss；dose_check step_ms=**1.213** PASS（thr1.15；金标≈2.101）；detect_mode=`no_bite`；detect_ok=no；collect_seq+C0 FP；未改对手阈值；未覆盖 Probing 分；未占 grj；DOSE_QUEUE quiet GH=DONE |
| 2026-07-25 | **Dose P3-SW-B masked CALIBRATED**：retune pilot `20260725_235216`@grj-m0 C0+C1；`mb=6,stall_s=0.1` C1/C0=**1.389** PASS(thr1.05；<quiet2.101)；recipes `status=calibrated`；stub`234531` mb=4/stall=0.05→0.880 ineffective 保留；未跑 formal/GH；未回调 SQL；未碰 grj-w0 |
| 2026-07-25 | **Dose P3-SW-B quiet PROBING_SCORED D4**：formal `20260725_232814`@grj-m0 C0+C1+C2；`mb=8,stall_s=0.1` C1/C0=**2.101** PASS(thr1.15)；offline D3 + SQL PASS_D4（`cpu.utilization_rss`）；DOSE_QUEUE probing=SCORED、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0；前序 `231336` C0 噪声 ineffective / `232443` SIGTERM 作废 |
| 2026-07-25 | **Pillar-C Runner P3-SW-A 三臂齐**：parent=`20260725_230350-pillar-c-p3-sw-a-loud`@grj-w0；`full_fidelity` cold≈**161.5**MiB；`probing_collapse`≈**9.29**MiB（SET↑）；`naive_downsample`≈**9.21**MiB（无 SET↑）；塌缩/砍量冷段约全量 **5.7%**；`VOLUME_RATIO.md`；未判 D；未占 m0 |
| 2026-07-25 | **XPUTimer masked 对照 P3-SW-A DONE**：`contrast-p3-sw-a-masked-20260725_230400`@yysong-w2；dose `every=1,stall_s=0.05`；自主 hang/slow=**0**；跨-run coll=**1.033** FAIL（thr1.05）；dose_check step_ms=**1.564** PASS；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；未占 grj-w0；DOSE_QUEUE masked XPU=DONE |
| 2026-07-25 | **Dose P3-SW-B quiet CALIBRATED**：pilot `20260725_230345`@grj-m0 C0+C1；`mb=8,stall_s=0.1` C1/C0=**1.715** PASS(thr1.15；<loud2.06)；recipes `status=calibrated`；未retune；未跑 formal/GH；未碰 grj-w0；前序 `225449`/`230148`/`230321` 启动中断作废 |
| 2026-07-25 | **C 门禁 G1 收口全绿**：源码默认开 cold（unset→on / `off` 可关）；新 so→POD_BUNDLE pydeps；`g1_fix_20260725_225835`@grj-w0 unset segs=**23** / off=0；GATE G1=Y 总判全绿；`G1_FIX.md`；未开 Runner；未碰 grj-m0 |
| 2026-07-25 | **Greyhound masked 对照 P3-SW-A DONE**：`contrast-p3-sw-a-masked-20260725_225517`@yysong-w2；dose `every=1,stall_s=0.05`；coll=**1.009** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms=**2.058** PASS（thr1.05；金标≈1.768）；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；未改对手阈值；未覆盖 Probing 分；未占 grj-w0；DOSE_QUEUE masked GH=DONE |
| 2026-07-25 | **Dose P3-SW-A masked PROBING_SCORED D4**：formal `20260725_224156`@grj-m0 C0+C1+C2；`every=1,stall_s=0.05` C1/C0=**1.768** PASS(thr1.05)；offline D3 + SQL PASS_D4（`cpu.utilization_rss`）；DOSE_QUEUE probing=SCORED、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0；前序 `223949`/`224131` 启动中断作废 |
| 2026-07-25 | **XPUTimer quiet 对照 P3-SW-A DONE**：`contrast-p3-sw-a-quiet-20260725_224059`@yysong-w2；dose `every=1,stall_s=0.1`；自主 hang/slow=**0**；跨-run coll=**1.071** FAIL（thr1.15）；dose_check step_ms=**3.421** PASS；detect_mode=`cross_run_contrast`；detect_ok=no；未改对手阈值；未覆盖 Probing 分；DOSE_QUEUE quiet XPU=DONE |
| 2026-07-25 | **Greyhound quiet 对照 P3-SW-A DONE**：`contrast-p3-sw-a-quiet-20260725_222610`@yysong-w2；dose `every=1,stall_s=0.1`；coll=**0.983** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms=**2.419** PASS（thr1.15）；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；未改对手阈值；未覆盖 Probing 分 |
| 2026-07-25 | **Dose P3-SW-A masked CALIBRATED**：pilot `20260725_222736`@grj-m0 C0+C1；`every=1,stall_s=0.05` C1/C0=**1.856** PASS(thr1.05；<quiet1.96/<loud2.93)；recipes `status=calibrated`；未retune；未跑 formal/GH；未碰 grj-w0；前序 `222445`/`222541` 启动中断作废 |
| 2026-07-25 | **Dose P3-SW-A quiet PROBING_SCORED D4**：formal `20260725_215903`@grj-m0 C0+C1+C2；`every=1,stall_s=0.1` C1/C0=**1.949** PASS(thr1.15)；offline D3 + SQL PASS_D4（`cpu.utilization_rss`）；DOSE_QUEUE probing=SCORED、GH/XPU=PENDING；未回调 SQL/阈值；未碰 grj-w0 |
| 2026-07-25 | **C 门禁**：`_prep/pillar_c_gate/GATE.md` **未全绿**（G1 默认关；G2–G6=Y/Y\*；首轮 Connection refused 不计绿）。附着补测@grj-w0 `retest_20260725_215000`+`keyfix`；冷开 segs=19；torch 用 `on,rate=1.0` |
| 2026-07-25 | **Dose P3-SW-A quiet CALIBRATED**：pilot2 `20260725_214923`@grj-m0；`every=1,stall_s=0.1` C1/C0=**1.962** PASS（thr1.15；<loud 2.93）；pilot1`214126` every=2 median-blind 作废；recipes `status=calibrated`；未跑 Masked/formal/GH；未碰 grj-w0 |
| 2026-07-25 | **Dose P3-SW-A quiet pilot1 INEFFECTIVE（已由 pilot2 覆盖）**：`20260725_214126` every=2/stall=0.1 → median C1/C0=0.549；根因隔步注入使中位落未注入步 |
| 2026-07-25 | **grj 空闲借用**：允许 `grj-megatron-32card-0716` 两台在 IDLE 时 hold-exec；落盘仍 weight-share；对方再现让路；a3 仍禁。Dose@grj-m0、C@grj-w0、GH@yysong-w2。规则同步 myportal cluster-identity / AGENTS |
| 2026-07-25 | **现役战役切换**：Loud 收官 → Dose Sweep（Quiet/Masked）∥ Pillar C；新增 `DOSE_QUEUE.md`；重写 `agents/LOOP.md`/`LOOP_PROMPT.md`；Loud 归档 `LOOP_LOUD.md`；`env.sh` 增 `FS_HOLD_PODS_C` |
| 2026-07-25 | Greyhound 对照 **P1-HW-B DONE**（末格）：`contrast-p1-hw-b-20260725_144607`@worker-1；冻结 dose mb=512,copies=6→48,ramp=1；coll=**1.027** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms C1/C0=**1.609** PASS（金标≈1.57）；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；MASTER_ADDR=127.0.0.1；未重跑 P3-SW-C`143010`/XPU`143531`；未碰 master-0/XPU；**GH 队无 PENDING** |
| 2026-07-25 | Greyhound 对照 **P3-SW-C DONE**：`contrast-p3-sw-c-20260725_143448`@worker-1；冻结 dose cpu_n=nproc,cpu_load=90,mb=1,leak_every=1.0,max_chunks=64；coll=**1.016** FAIL；Rbeast C1 cp=**1**/C0=**0** hit；dose_check step_ms C1/C0=**2.509** PASS（金标≈2.49）；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；MASTER_ADDR=127.0.0.1；旁证 `143010`；`142010` 无 sidecar 作废；未重跑 P3-SW-B`140639`；未碰 master-0 |
| 2026-07-25 | Greyhound 对照 **P3-SW-C DONE**：`contrast-p3-sw-c-20260725_143010`@worker-1；冻结 dose cpu_n=nproc,cpu_load=90,mb=1,leak_every=1.0,max_chunks=64；coll=**0.970** FAIL；Rbeast C1 cp=**1**/C0=**0** hit；dose_check step_ms C1/C0=**2.361** PASS（金标≈2.49）；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；MASTER_ADDR=127.0.0.1；`142010` 无 sidecar 注入作废；未重跑 XPU`141815`；未碰 master-0 |
| 2026-07-25 | XPUTimer 对照 **P1-HW-B DONE**：`contrast-p1-hw-b-20260725_143531`@worker-2；冻结 dose mb=512,copies=6→48,ramp=1；自主 hang/slow=**0**；跨-run coll=**0.991** FAIL；dose_check step_ms C1/C0=**1.585** PASS（金标≈1.57）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1；未重跑 P3-SW-C`141815`；未碰 worker-1/master-0 |
| 2026-07-25 | **P1-HW-B SCORED D3**：`142359` C1/C0=**1.57**；dose `mb=512,copies=6→48,ramp=1` calibrated（INLINE 渐进，非改频）；offline D3 min_compute→rank_7；SQL_NO_EXT_EVIDENCE；CONTRAST_QUEUE +GH/XPU PENDING；未改 P3-SW-C`135238` |
| 2026-07-25 | XPUTimer 对照 **P3-SW-C DONE**：`contrast-p3-sw-c-20260725_141815`@worker-2；冻结 dose cpu_n=nproc,cpu_load=90,mb=1,leak_every=1.0,max_chunks=64；自主 hang/slow=**0**；跨-run coll=**0.917** FAIL；dose_check step_ms C1/C0=**2.504** PASS（金标≈2.49）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1；未重跑 P3-SW-B`133435`；未碰 worker-1/master-0 |
| 2026-07-25 | Greyhound 对照 **P3-SW-B DONE**：`contrast-p3-sw-b-20260725_140639`@worker-1；冻结 dose mb=16,stall_s=0.25（INLINE 8b）；coll=**0.984** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms C1/C0=**3.760** PASS（金标≈2.06）；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；MASTER_ADDR=127.0.0.1；未重跑 P2-SW-C`135623`；未碰 master-0 |
| 2026-07-25 | **P3-SW-C SCORED D4**：`135238` C1/C0=**2.33**（收尾重验：pod-sup 准时 inject@step100；旧表 2.49 为晚注入半成品）；dose `cpu_n=nproc,cpu_load=90,mb=1,leak_every=1.0` calibrated；offline D3 + SQL PASS_D4；对照 GH/XPU 已 DONE（金标旁证≈2.5）；未改 P3-SW-B`125558` / P2-SW-C`124102` |
| 2026-07-25 | Greyhound 对照 **P2-SW-C DONE**：`contrast-p2-sw-c-20260725_135623`@worker-1；冻结 dose device_rev=1,topo_extra_ar=512,topo_ar_elems=262144；coll=**0.564** FAIL；Rbeast C1/C0 cp=**0/0** miss；dose_check **comm=15.016 PASS**（step=2.211 旁证；金标≈49.86/5.06）；detect_mode=`no_bite`；detect_ok=no；collect_seq+C0 FP；MASTER_ADDR=127.0.0.1；未重跑 P2-SW-B`134521`；未碰 master-0 |
| 2026-07-25 | Greyhound 对照 **P2-SW-B DONE**：`contrast-p2-sw-b-20260725_134521`@worker-1；冻结 dose algo=ring,stress_mb=512,buffsize=8；coll=**0.982** FAIL；Rbeast C1/C0 cp=**0/0** miss；dose_check **comm=1.862 PASS**（金标≈1.82；step=1.152 旁证不 FAIL）；detect_mode=`no_bite`；detect_ok=no；collect_seq+C0 FP；MASTER_ADDR=127.0.0.1；未重跑 P1-SW-C`132954`；未碰 master-0 |
| 2026-07-25 | Greyhound 对照 **P1-SW-C DONE**：`contrast-p1-sw-c-20260725_132954`@worker-1；冻结 dose INLINE 2c n=1024/every=1/fallback_s=0.25；coll=**1.027** FAIL；Rbeast C1/C0 cp=**0/0** miss；median step_ms=**1.024** 盲；tip max_ratio=**4.038** PASS（金标 tip max≈4.63）；detect_mode=`no_bite`；detect_ok=no；SPIKE_OK×200；MASTER_ADDR=127.0.0.1；未重跑 P1-SW-B`132011` |
| 2026-07-25 | XPUTimer 对照 **P2-SW-C DONE**：`contrast-p2-sw-c-20260725_132235`@worker-2；冻结 dose device_rev=1,topo_extra_ar=512,topo_ar_elems=262144；自主 hang/slow=**0**；跨-run coll=**0.593** FAIL；dose_check **comm=13.910 PASS**（step=2.119 旁证；金标 C1/C0_comm≈49.86/step≈5.06）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1；未重跑 P2-SW-B`131251` |
| 2026-07-25 | **P3-SW-B SCORED D4**：`125558` C1/C0=**2.06**；dose `mb=16,stall_s=0.25` calibrated；INLINE 8b；SQL PASS_D4；CONTRAST_QUEUE +GH/XPU PENDING；未改 P2-SW-C`124102` |
| 2026-07-25 | **P3-SW-B SCORED D4**：`125558` C1/C0=**2.06**；dose `mb=16,stall_s=0.25` calibrated；offline D3 + SQL PASS_D4；CONTRAST_QUEUE +GH/XPU PENDING；未改 P2-SW-C`124102` |
| 2026-07-25 | XPUTimer 对照 **P1-SW-C DONE**：`contrast-p1-sw-c-20260725_125656`@worker-2；冻结 dose INLINE 2c n=1024/every=1/fallback_s=0.25；自主 hang/slow=**0**；跨-run coll=**0.991** FAIL；median step_ms=**1.006** 盲；tip max_ratio=**4.897** PASS（金标 tip max≈4.63）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1；SPIKE_OK；未重跑 P1-SW-B`124414` |
| 2026-07-25 | Greyhound 对照 **P1-SW-B DONE**：`contrast-p1-sw-b-20260725_132011`@worker-1；冻结 dose INLINE 2b rare_seq=1536/every=1；coll=**1.020** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms C1/C0=**1.386** PASS；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；未重跑 P1-SW-A`125949` |
| 2026-07-25 | XPUTimer 对照 **P1-SW-B DONE**：`contrast-p1-sw-b-20260725_124414`@worker-2；冻结 dose INLINE 2b rare_seq=1536/every=1；dose_check step_ms C1/C0=**1.372** PASS；自主 hang/slow=**0**；跨-run coll=**0.991** FAIL（thr1.15）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1 未继承 master-0 |
| 2026-07-25 | Greyhound 对照 **P3-SW-A DONE**：`contrast-p3-sw-a-20260725_124837`@worker-1；冻结 dose inline_gc_every=1,inline_gc_stall_s=0.25；coll=**1.000** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms C1/C0=**5.667** PASS；detect_mode=`autonomous`；detect_ok=yes；MASTER_ADDR=127.0.0.1 未继承 master-0 |
| 2026-07-25 | Greyhound 对照 **P1-SW-A DONE**：`contrast-p1-sw-a-20260725_125949`@worker-1；冻结 dose INLINE 2a chunks=12/stall_mb=768/stall_s=0.25；coll=**1.009** FAIL；Rbeast C1 cp=**2**/C0=**0** hit；dose_check step_ms C1/C0=**4.283** PASS；detect_mode=`autonomous`；detect_ok=yes；collect_seq+C0 FP；未重跑 P3-SW-A`124837` |
| 2026-07-25 | XPUTimer 对照 **P1-SW-A DONE**：`contrast-p1-sw-a-20260725_123626`@worker-2；冻结 dose INLINE 2a chunks=12/stall_mb=768/stall_s=0.25；dose_check step_ms C1/C0=**4.284** PASS；自主 hang/slow=**0**；跨-run coll=**0.991** FAIL（thr1.3）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1 未继承 master-0 |
| 2026-07-25 | **P2-SW-C SCORED D3**：`124102` C1/C0_comm=**49.86**/step=**5.06**；dose `device_rev=1,topo_extra_ar=512,topo_ar_elems=262144` calibrated（移植沐曦 AR256 后抬剂）；CONTRAST_QUEUE +GH/XPU PENDING |
| 2026-07-25 | **P2-SW-B SCORED D3**：`122911` C1/C0_comm=**1.82**（step=1.13）；dose `algo=ring,stress_mb=512,buffsize=8` calibrated（ring-only@`122231` 咬空后抬 buff）；CONTRAST_QUEUE +GH/XPU PENDING |
| 2026-07-25 | **P1-SW-C SCORED D3**：`121105` tip max=4.63（med盲）；dose `n=1024,every=1,fallback_s=0.25` calibrated；CONTRAST_QUEUE +GH/XPU PENDING |
| 2026-07-25 | **双流水线 Loop**：CONTRAST_QUEUE + BASELINE_CONTRAST；LOOP/PROMPT 改为 Case 扫格 ∥ 竞品对照；第一梯队 6 格入对照队 |
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
| 2026-07-25 | Greyhound P3-EXT-A **公平性对照 DONE**：`contrast-p3-ext-a-20260725_114502`；coll C1/C0=1.048；Rbeast acf_period=8、cp=0/0；step_ms=1.922 dose_OK；detect_ok=no；旧 `yjr-as-b-gh-s4-20260725_002805` 保留 |
| 2026-07-25 | XPUTimer 对照 **P1-EXT-A DONE**：`contrast-p1-ext-a-20260725_114546`@worker-2；冻结 dose INLINE 8192×64；dose_check step_ms C1/C0=**3.955** PASS；自主 hang/slow=**0**；跨-run coll host-wall=**1.036** FAIL（thr1.5）；detect_mode=`cross_run_contrast`（未误标 autonomous）；无咬合如实记；不改 Probing 分 |
| 2026-07-25 | Greyhound 对照 **P1-EXT-A DONE**：`contrast-p1-ext-a-20260725_120526`@worker-1；冻结 dose INLINE 8192×64；coll=**1.018** FAIL；Rbeast collect_seq C1 cp=2 / C0=0 → **hit**；step_ms=**3.924** dose_OK；detect_ok=**yes**；detect_mode=`autonomous`；不改 Probing 分 |
| 2026-07-25 | XPUTimer 对照 **P1-EXT-B DONE**：`contrast-p1-ext-b-20260725_115717`@worker-2；冻结 dose INLINE HBM 512×48；dose_check step_ms C1/C0=**2.069** PASS；自主 hang/slow=**0**；跨-run coll host-wall=**0.982** FAIL（thr1.6）；detect_mode=`cross_run_contrast`；detect_ok=no；不改 Probing 分 |
| 2026-07-25 | Greyhound 对照 **P1-EXT-B DONE**：`contrast-p1-ext-b-20260725_121407`@worker-1；冻结 dose INLINE HBM 512×48；coll=**1.009** FAIL；Rbeast collect_seq C1 cp=2 / C0=0 → **hit**；step_ms=**2.070** dose_OK；detect_ok=**yes**；detect_mode=`autonomous`；不改 Probing 分 |
| 2026-07-25 | XPUTimer 对照 **P3-EXT-B DONE**：`contrast-p3-ext-b-20260725_120235`@worker-2；冻结 dose fio_nj=16+ckpt20+pread（镜像无 fio→stress-ng hdd32+iomix16）；dose_check step_ms C1/C0=**1.793** PASS；自主 hang/slow=**0**；跨-run coll=**1.048** FAIL（thr1.3）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=10.119.7.62 未继承 master-0 |
| 2026-07-25 | Greyhound 对照 **P3-EXT-B DONE**：`contrast-p3-ext-b-20260725_122204`@worker-1；冻结 dose fio_nj=16+ckpt20+pread（镜像无 fio→stress-ng hdd32+iomix16）；coll=**1.049** FAIL；Rbeast collect_seq C1/C0 cp=0/0 → miss；step_ms=**1.738** dose_OK；detect_ok=**no**；detect_mode=`no_bite`；MASTER_ADDR=127.0.0.1 未继承 master-0；能力边界如实记 |
| 2026-07-25 | XPUTimer 对照 **P3-EXT-C DONE**：`contrast-p3-ext-c-20260725_121535`@worker-2；冻结 dose vm_n=96,vm_bytes=6G；dose_check step_ms C1/C0=**1.780** PASS；自主 hang/slow=**0**；跨-run coll=**1.184** FAIL（thr1.3）；detect_mode=`cross_run_contrast`；detect_ok=no；单机 MASTER_ADDR=127.0.0.1（未继承 master-0） |
| 2026-07-25 | Greyhound 对照 **P3-EXT-C DONE**：`contrast-p3-ext-c-20260725_124257`@worker-1；冻结 dose vm_n=96,vm_bytes=6G；warmup 预热 page-in（`PAGEIN_PARTIAL +6Gi`）；coll=**1.296** FAIL（thr1.3）；Rbeast collect_seq C1/C0 cp=0/0 → miss；step_ms=**1.063** dose_WEAK；detect_ok=**no**；detect_mode=`no_bite`；MASTER_ADDR=127.0.0.1 未继承 master-0；先轮 `123310` coll=1.144 保留；能力边界如实记 |
| 2026-07-25 | XPUTimer 对照 **P3-SW-A DONE**：`contrast-p3-sw-a-20260725_122733`@worker-2；冻结 dose inline_gc_every=1,inline_gc_stall_s=0.25；dose_check step_ms C1/C0=**2.633** PASS；自主 hang/slow=**0**；跨-run coll=**0.953** FAIL（thr1.3）；detect_mode=`cross_run_contrast`；detect_ok=no；MASTER_ADDR=127.0.0.1 未继承 master-0 |
| 2026-07-25 | P1-SW-A Loud **SCORED D3**：`20260725_114556` inline_2a chunks=12/stall_mb=768/stall_s=0.25 C1/C0=**4.20** PASS；C2×16 + SQL DUMP_OK；offline D3 victim=rank_7；SQL_NO_EXT_EVIDENCE（gap flat）不升 D4；dose calibrated；CONTRAST_QUEUE 入队 GH+XPU |
| 2026-07-25 | P1-SW-B Loud **SCORED D3**：`20260725_115732` inline_2b rare_seq=1536/every=1 C1/C0=**1.36** PASS；C2×16 + SQL DUMP_OK；offline D3=`shape_seq_rare`→rank_7（1536×200）；SQL_NO_EXT_EVIDENCE 不升 D4；dose calibrated；CONTRAST_QUEUE 入队 GH+XPU；未改 P1-SW-A `114556` |
