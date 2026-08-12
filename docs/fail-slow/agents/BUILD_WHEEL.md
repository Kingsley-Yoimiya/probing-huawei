# 昇腾 hold pod：Probing wheel / Rust 工具链（铁律）

> 2026-07-26 记；**2026-08-05 handoff 分支编译教训已并入**（Case B / `agent/complete-capability-handoff`）。

根因（历史）：P-FIX 在 pod 内 `rm -rf` toolchain 再 `rustup install`，卡在 USTC「downloading 6 components」。
根因（2026-08-05）：Mac 交叉编译、跳板 `/tmp` 编 target 磁盘满、cargo 误走跳板反代、wheel 文件名不合法。

## Case B 固定 wheel（找这个，别找错）

| 项 | 值 |
|---|---|
| 文件 | `/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei/wheels/probing-0.2.6+handoff.bed9ee1.2-cp38-abi3-linux_aarch64.whl` |
| 历史对照 | `.../probing-0.2.6+handoff.bed9ee1.1-cp38-abi3-linux_aarch64.whl`（修复前；勿当固定版） |
| 分支 / commit | `agent/complete-capability-handoff` / `bed9ee1` |
| 文档 | `plans/case-b-512card/PROBING-VERSION.md` |
| Cursor 规则 | `.cursor/rules/probing-wheel-build.mdc` |

## 禁止（Agent / 人）

1. **禁止**在 Ascend hold pod 里对 AFS `toolchains/rust/` 做 `rm -rf …/toolchains/…` 后重装 rustup。
2. **禁止**在 pod 内从零 `curl static.rust-lang.org` / `rustup toolchain install`（极慢或失败）；缺 toolchain → 本机 Clash 摆渡或 `install_rust_afs.sh` 写 AFS。
3. **禁止**在本机 Mac `maturin --zig` 交叉编 wheel 当昇腾**正式**产物（无 CANN；HCCL shim 不在 Ascend 环境编）。
4. **禁止**在跳板 `ais-cf3e61a5` 的 `/tmp` 当 `CARGO_TARGET_DIR`（overlay ~48G，易满）。
5. **禁止**在跳板 `git clone` GitHub；本机 clone → tar → SSH 管道 → AFS `handoff-src-<commit>/`。
6. **禁止**在 pod 内 `export http_proxy=http://127.0.0.1:18080`（反代在跳板，pod 连不上）。
7. **禁止** wheel 名 `...-pod-...` 等 PEP 427 非法形式；用 `+handoff.<commit>.<n>` local version。
8. 已有固定 wheel 仍可用时，先问是否真要重编。

## 允许的快路径（按序）

| 优先级 | 做法 | 说明 |
|--------|------|------|
| **1. 复用** | AFS 固定 whl + `pip install --no-deps` | Case B probe 格默认路径 |
| **2. pod 内编** | `build_handoff_wheel_pod.sh` | hold pod + AFS target + tuna 直连；**正解** |
| **3. 源码摆渡** | 本机 tar → 跳板 → `kubectl exec -i` 灌 AFS | 不占跳板带宽 clone |
| **4. 反代（仅跳板）** | `egress_tunnel.sh` + `install_rust_afs.sh` | 只给**跳板本机**装 rustup；**不给 pod cargo 用** |
| **5. 两集群同步** | grj 与 pjlab-new 各写 AFS 或 pipe wheel | PVC 不共享 |

### pod 内编译检查表

```bash
source /afs-a3-weight-share/yinjinrun.p-huawei/toolchains/rust-env.sh
export CARGO_HOME=/afs-a3-weight-share/yinjinrun.p-huawei/toolchains/rust/cargo
export CARGO_TARGET_DIR=/afs-a3-weight-share/yinjinrun.p-huawei/probing-huawei/build-handoff-bed9ee1/target
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
# cargo 用 tuna（pod 可直连），见 build_handoff_wheel_pod.sh
```

HCCL shim 复制路径：`$CARGO_TARGET_DIR/release/libprofapi.so` → `python/probing/shim/hccl/`（**不是** `$SRC/target/`）。

## 跳板 /tmp 保洁

编译前在 `ais-cf3e61a5`：`df -h /`；可删 `/tmp/rust-jump`、`*probing*/target`、旧实验 tar。目标：保留 ≥10G 空闲。

## 验收口令

- `No space left on device` on 跳板 → 清 `/tmp`，target 改 AFS，**不要**继续在本机/Mac 编。
- cargo `Could not connect to server … via 127.0.0.1:18080` → pod 内 `unset` 全部 proxy，改 tuna 直连。
- `Invalid wheel filename` → 改名 `+handoff.bed9ee1.1`，勿 `-pod-`。
- `syncing channel updates` / rustup CDN 长时间无进度 → 停，改摆渡或跳板反代装 rust，勿 pod 内硬等。

## 脚本入口

- **handoff / Case B**：`scripts/fail-slow/build_handoff_wheel_pod.sh`
- 通用（legacy）：`scripts/fail-slow/install_probing_wheel_on_pod.sh`（缺 toolchain 应失败并提示本文）

## 相关

- 资源卡：[`RESOURCE.md`](RESOURCE.md)
- Case B 版本：[`plans/case-b-512card/PROBING-VERSION.md`](../../../../plans/case-b-512card/PROBING-VERSION.md)
- 沐曦反代参考：`project/lab-workspace/scripts/cluster/egress_tunnel.sh`
