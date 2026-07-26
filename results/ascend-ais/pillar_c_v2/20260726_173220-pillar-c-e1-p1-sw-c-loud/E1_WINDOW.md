# E1 · 追溯窗正式验证（P1-SW-C）

> **定位**：EVAL-GAP §2 E1。在线极稀常驻 + onset SET↑，**dump 后按步截窗**重判。
> **window_mode**：`offline_truncate`（尚无 online「只留最近 W 步」API；与 E1-off 同尺）。
> **尺**：采集归因（duration 尖刺）；**禁止**只用 cold / **禁止**训练 step_ms 假同 D。

## 配置

- parent：`20260726_173220-pillar-c-e1-p1-sw-c-loud`
- arm：`rate_0`（resident_rate=0）
- case：`None`
- SET↑：`None`
- probing_data 总字节：`None`

## 结论

**BLOCKED**：torch_trace MEMT empty

