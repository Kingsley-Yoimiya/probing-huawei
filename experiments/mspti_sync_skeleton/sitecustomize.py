# PYTHONPATH 指向本目录时自动安装 hook。
# - MSPTI_SKELETON=1：实验 megatron_mspti_hook（与 formal smoke 兼容）
# - PROBING_NPU_SYNC_SKELETON=1：Probing 主路径早启动（互斥，勿双开）
from __future__ import annotations

import os
import sys

_mspti = os.environ.get("MSPTI_SKELETON", "").strip().lower() in ("1", "true", "yes", "on")
_probing_main = os.environ.get("PROBING_NPU_SYNC_SKELETON", "").strip() == "1"

if _mspti and _probing_main:
    raise RuntimeError("MSPTI_SKELETON and PROBING_NPU_SYNC_SKELETON are mutually exclusive")

if _mspti:
    try:
        from megatron_mspti_hook import install

        install()
    except Exception as exc:  # noqa: BLE001
        print(f"[mspti_skeleton] sitecustomize install failed: {exc!r}", flush=True)
        raise

if _probing_main and os.environ.get("LOCAL_RANK") is not None:
    os.environ.setdefault("PROBING", "2")
    try:
        import probing  # noqa: F401

        # start_gated must run before CANN/NPU init — never after ``import torch``.
        from probing.profiling.npu_sync import maybe_start

        collector = maybe_start()
        if collector is not None:
            print(
                f"[probing_main_sitecustomize] armed before torch: "
                f"rank={collector.rank} out={collector.skeleton_path}",
                flush=True,
            )

        import torch  # noqa: F401
    except Exception as exc:
        print(
            f"[probing_main_sitecustomize] early bootstrap failed: {exc!r}",
            file=sys.stderr,
        )
