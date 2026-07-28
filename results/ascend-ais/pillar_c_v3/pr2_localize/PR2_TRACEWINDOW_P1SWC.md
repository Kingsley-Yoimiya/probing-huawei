# E1 · 追溯窗正式验证（P1-SW-C）

> **定位**：EVAL-GAP §2 E1。在线极稀常驻 + onset SET↑，**dump 后按步截窗**重判。
> **window_mode**：`offline_truncate`（尚无 online「只留最近 W 步」API；与 E1-off 同尺）。
> **尺**：采集归因（duration 尖刺）；**禁止**只用 cold / **禁止**训练 step_ms 假同 D。

## 配置

- parent：`20260728_211312-pillar-c-v3-pr2-exp-c-p1swc`
- arm：`dynamic`（resident_rate=?）
- case：`P1-SW-C`
- SET↑：`SET_OK`
- probing_data 总字节：`1872537584`

## 结论

- **status** = `OK`
- **W\*** = `200`（首次 enough=true；期望对照 E1-off=100）
- anchor_step=`282` inject=[100,300]
- 环内 steps=`107` (0..1042) rows_ow=`0`
- torch_trace：`/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/pillar_c_v3/pr2_localize/20260728_211312-pillar-c-v3-pr2-exp-c-p1swc/dynamic/probing_data/3564144/python.torch_trace` (pid=3564144)

## 分窗（重点 W=50 / 100 / 200）

| W | enough | n_steps | evidence |
|---:|:---:|---:|---|
| 50 | N | 6 | `no_spike:top_step=261:dur_s=0.1996:med=0.1027:n_steps=6` |
| 100 | N | 10 | `no_spike:top_step=261:dur_s=0.1996:med=0.1000:n_steps=10` |
| 200 | Y | 20 | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel` |
| full | Y | 30 | `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.0991:module=DistributedDataParallel` |

## 对照解读

- W=50：偏紧/不够 — `no_spike:top_step=261:dur_s=0.1996:med=0.1027:n_steps=6`
- W=100：不够 — `no_spike:top_step=261:dur_s=0.1996:med=0.1000:n_steps=10`
- W=200：够（对照） — `torch_trace.duration_spike:step=161:dur_s=0.5289:med=0.1011:module=DistributedDataParallel`
- 本轮 W*=200（相对 E1-off=100 的差异见 evidence）。

## 方法备注

- online：`resident rate≈0 + SET on,rate=1.0 @ inject onset`
- truncate：`anchor=inject_stop(300); keep local_step in (anchor-W, anchor]; NOT online retention API`
- forbid：`training step_ms / cold-only MiB`

