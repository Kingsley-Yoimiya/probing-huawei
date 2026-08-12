#!/usr/bin/env python3
"""MindSpeed pretrain 入口包装：在 import 树稳定后强制安装 npu_sync patch，再跑 pretrain_gpt。"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

def main() -> None:
    os.environ.setdefault("PROBING", "2")
    # 确保 probing import hook 已安装。
    import probing  # noqa: F401

    from probing.profiling.npu_sync import maybe_start

    # start_gated 必须在 CANN/NPU 初始化之前；sitecustomize 亦同序。
    collector = maybe_start()
    if collector is not None:
        print(
            "EARLY_ARM",
            "rank",
            collector.rank,
            "out",
            str(collector.skeleton_path),
            flush=True,
        )

    import torch  # noqa: F401

    # 预拉 MindSpeed 训练模块，再重试 patch（真实入口优先 mindspeed）。
    for mod in (
        "mindspeed_llm.training.training",
        "mindspeed_llm.training",
        "megatron.training.training",
        "megatron.training",
        "pretrain_gpt",
    ):
        try:
            __import__(mod)
        except Exception:
            pass

    from probing.profiling.npu_sync.hook import (
        _MINDSPEED_STEP_TARGETS,
        _patch,
        _step_patches_complete,
        _wrap_train_step,
        install_patches,
    )

    for module_name in _MINDSPEED_STEP_TARGETS:
        _patch(module_name, "train_step", _wrap_train_step)

    import time

    for _ in range(400):
        if install_patches() and _step_patches_complete():
            break
        time.sleep(0.05)
    else:
        raise SystemExit(
            "npu_sync train_step patch incomplete after bounded wait "
            "(mindspeed_llm.training.training.train_step must be wrapped)"
        )

    import mindspeed_llm.training.training as _mtt

    print(
        "PATCH_STATE",
        "train_step",
        getattr(_mtt.train_step, "_probing_npu_sync_wrapped", False),
        "train",
        getattr(_mtt.train, "_probing_npu_sync_wrapped", False),
        "pretrain",
        getattr(_mtt.pretrain, "_probing_npu_sync_wrapped", False),
        flush=True,
    )

    pretrain = Path("/MindSpeed-LLM/MindSpeed-LLM/pretrain_gpt.py")
    if not pretrain.exists():
        raise SystemExit(f"missing {pretrain}")
    sys.argv[0] = str(pretrain)
    runpy.run_path(str(pretrain), run_name="__main__")


if __name__ == "__main__":
    main()
