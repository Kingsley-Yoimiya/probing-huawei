# 昇腾 hold pod：Probing wheel / Rust 工具链（铁律）

> 2026-07-26 记。根因：P-FIX 在 `yysong-worker-2` 上 `rm -rf` toolchain 再 `rustup install`，卡在 USTC「downloading 6 components」——**集群 egress 拉大文件一向极慢**，本机 Clash `:7897` 接不上。同类坑以前修过一半（镜像 / egress 脚本），**华为 AIS 未固化**。

## 禁止（Agent / 人）

1. **禁止**在 Ascend hold pod（yysong / grj）里对 `RUSTUP_HOME` 做 `rm -rf …/toolchains/…` 后重装。
2. **禁止**在 pod 内 `curl static.rust-lang.org` / `rustup toolchain install` / 无代理 `cargo update` 拉整网 crates（可等几十分钟～失败）。
3. **禁止**把「修一小段 Rust」默认做成「集群内从零编 toolchain + maturin」。
4. 已有 wheel（`/data/yinjinrun.p-huawei/probing-huawei/wheels/*.whl`）仍可用时，先问是否真要重编。

## 允许的快路径（按序）

| 优先级 | 做法 | 说明 |
|--------|------|------|
| **1. 复用** | 现有 `rustc`/`cargo` + 已装 toolchain | `CARGO_HOME`/`RUSTUP_HOME`=`/data/yinjinrun.p-huawei/probing-huawei/{cargo,rustup}`；`rustc --version` 通再 `maturin build` |
| **2. 文件摆渡** | Mac Clash 下好 → 跳板 → pod | 本机 `:7897` 拉 aarch64 toolchain 包或编好 `linux_aarch64` wheel → `scp`→`ais-cf3e61a5`→`kubectl cp` 进 `/data/yinjinrun.p-huawei/probing-huawei/` → pod 只 `pip install --no-deps` 本地 whl |
| **3. 反代（可选）** | SSH `-R` 把本机 7897 转到跳板 | 思路见 `project/lab-workspace/scripts/cluster/egress_tunnel.sh`；pod/`kubectl exec` 环境设 `https_proxy`。**仍不要**删已有 toolchain 重装 |
| **4. 源码同步** | 只同步改过的 `src` + 用已有 toolchain 编 | `jsync` / tar 管道；编完装 `wheels/` + `probe-bundle/pydeps` |

落盘身份：`yinjinrun.p-huawei`。共享只读 bundle：`/afs-a3-241ceshi-shared/yinjinrun.p-huawei/probe-bundle`。

## 验收口令

- 慢且日志出现 `syncing channel updates` / `downloading N components` / `crates.io` 长时间无进度 → **立刻停**，改走摆渡，不要「再等一会」。
- `rustc` 报 `Missing manifest`：toolchain 已被删残 → **不要**在 pod 里 rustup 修；从本机摆渡完整 `toolchains/stable-aarch64-unknown-linux-gnu` 或完整 whl。

## 脚本入口

- 集群内编译（仅当 toolchain 已绿）：`scripts/fail-slow/install_probing_wheel_on_pod.sh`
- 该脚本**不得**再默认从 CDN 装 rustup；缺 toolchain 时应失败并提示本文。

## 相关

- 资源卡：[`RESOURCE.md`](RESOURCE.md)
- Param-Calib：[`PARAM_CALIB_RUNNER.md`](PARAM_CALIB_RUNNER.md) / P-FIX
- 沐曦侧 egress 参考：`project/lab-workspace/scripts/cluster/egress_tunnel.sh`
