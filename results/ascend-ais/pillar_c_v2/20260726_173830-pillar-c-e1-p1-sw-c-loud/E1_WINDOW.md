# E1 · 追溯窗正式验证（P1-SW-C）

> **定位**：EVAL-GAP §2 E1。在线极稀常驻 + onset SET↑，**dump 后按步截窗**重判。
> **window_mode**：`offline_truncate`（尚无 online「只留最近 W 步」API；与 E1-off 同尺）。
> **尺**：采集归因（duration 尖刺）；**禁止**只用 cold / **禁止**训练 step_ms 假同 D。

## 配置

- parent：`20260726_173830-pillar-c-e1-p1-sw-c-loud`
- arm：`rate_0`（resident_rate=0）
- case：`P1-SW-C`
- SET↑：`SET_OK`
- probing_data 总字节：`1613283136`

## 结论

- **status** = `NO_W_STAR`
- **W\*** = `None`（首次 enough=true；期望对照 E1-off=100）
- anchor_step=`300` inject=[100,300]
- 环内 steps=`357` (189..545) rows_ow=`0`
- torch_trace：`results/ascend-ais/pillar_c_v2/20260726_173830-pillar-c-e1-p1-sw-c-loud/rate_0/probing_data/202843/python.torch_trace` (pid=202843)

## 分窗（重点 W=50 / 100 / 200）

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 50 | N | 50 | `no_spike:top_step=269:dur_s=0.3466:med=0.1973:n_steps=50` |
| 100 | N | 100 | `no_spike:top_step=269:dur_s=0.3466:med=0.1990:n_steps=100` |
| 200 | N | 112 | `no_spike:top_step=269:dur_s=0.3466:med=0.1994:n_steps=112` |
| full | N | 112 | `no_spike:top_step=269:dur_s=0.3466:med=0.1994:n_steps=112` |

## 对照解读

- W=50：偏紧/不够 — `no_spike:top_step=269:dur_s=0.3466:med=0.1973:n_steps=50`
- W=100：不够 — `no_spike:top_step=269:dur_s=0.3466:med=0.1990:n_steps=100`
- W=200：不够（对照） — `no_spike:top_step=269:dur_s=0.3466:med=0.1994:n_steps=112`
- ⚠ NO_W_STAR：全程窗仍不够 duration 尖刺；未用 cold 冒充。

## 方法备注

- online：`resident rate≈0 + SET on,rate=1.0 @ inject onset`
- truncate：`anchor=inject_stop(300); keep local_step in (anchor-W, anchor]; NOT online retention API`
- forbid：`training step_ms / cold-only MiB`


## SET / 密度核验

- set_upgrade.log：`SET_OK_WORKER pid=202843`；同时有 `Failed SET query 'set probing.torch.profiling'`（旧脚本读回命令误当 SET）。
- SET 命令当时写的是 `torch.profiling=on,rate=1.0`，**不是** C0 真相键 `probing.torch.profiling=`。
- 环内：rows=51870 steps=357 (189..545)；**最早步=189**（inject onset=100 之前无详采）。
- step≥134：steps=357，其中 dense(≥50行/步)=285 / sparse=72。
- inject 窗 top post-duration：[{'dur_s': 0.3466, 'step': 269, 'module': 'DistributedDataParallel'}, {'dur_s': 0.3445, 'step': 269, 'module': 'DistributedDataParallel.module'}, {'dur_s': 0.2311, 'step': 262, 'module': 'DistributedDataParallel'}]
- **密度结论**：相对 rate=0 空表，落盘后有详采（≠全程空）；但未呈现 C0 式「SET 后全量升密」；归因尖刺尺下 **NO_W_STAR**。

## 与 E1-off 对照

- E1-off full_fidelity：W\*=**100**（spike@238 AdamW dur≈0.71s）。
- 本轮 offline_truncate：W=50/100/200/full 均 **不够** duration 尖刺 → **未复现 W\*=100**。
- 原因归 SET 键/升详不彻底 + 尖刺未进 torch_trace（训练 tip@100 step_ms≈1163 在环外）。
- window_mode=`offline_truncate`（无 online retention API）。

## 处置

- `173220` INVALID（PATH）；本 run 可作 E1 正式一臂证据（dump 齐、截窗可判）。
- 不因 NO_W_STAR 再开无关长跑；正确键 `probing.torch.profiling=` 已写入 hold_exec，留给 E3 动态臂验证升详密度。
