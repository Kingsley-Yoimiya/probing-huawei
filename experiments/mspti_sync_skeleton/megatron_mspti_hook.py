"""Megatron 单 step MSPTI skeleton 门控 hook。

必须在每个 training worker 进程内、CANN/NPU 初始化之前 start_gated
（与 synthetic smoke 一致）。禁止在 torchrun agent 父进程启动 collector。

CaptureEnd 仅门控（Disable Activity，无 FlushAll）。
Finalize（FlushAll(0)+drain+Unsubscribe+DROP）必须在显式训练结束路径调用；
atexit 仅兜底，strict 拒绝 finalize_reason=atexit。
"""

from __future__ import annotations

import atexit
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_training_worker() -> bool:
    argv = " ".join(sys.argv)
    if "pretrain_gpt" not in argv:
        return False
    if os.environ.get("LOCAL_RANK") is None:
        return False
    return True


class _GatedCollector:
    def __init__(self, lib_path: str, out_dir: Path, rank: int, local_rank: int) -> None:
        cann_lib = "/usr/local/Ascend/cann-8.5.0/lib64/libmspti.so"
        if os.path.exists(cann_lib):
            ctypes.CDLL(cann_lib, mode=ctypes.RTLD_GLOBAL)
        self.lib = ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
        self.lib.mspti_skeleton_start_gated.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_uint64,
        ]
        self.lib.mspti_skeleton_start_gated.restype = ctypes.c_int
        self.lib.mspti_skeleton_capture_begin.argtypes = [ctypes.c_int64]
        self.lib.mspti_skeleton_capture_begin.restype = ctypes.c_int
        self.lib.mspti_skeleton_capture_end.argtypes = [ctypes.c_int64]
        self.lib.mspti_skeleton_capture_end.restype = ctypes.c_int
        self.lib.mspti_skeleton_finalize.argtypes = []
        self.lib.mspti_skeleton_finalize.restype = ctypes.c_int
        self.lib.mspti_skeleton_stop.argtypes = []
        self.lib.mspti_skeleton_stop.restype = ctypes.c_int
        if hasattr(self.lib, "mspti_skeleton_last_capture_stats"):
            self.lib.mspti_skeleton_last_capture_stats.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_uint64),
            ]
            self.lib.mspti_skeleton_last_capture_stats.restype = ctypes.c_int
        if hasattr(self.lib, "mspti_skeleton_last_finalize_stats"):
            self.lib.mspti_skeleton_last_finalize_stats.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_uint64),
                ctypes.POINTER(ctypes.c_uint64),
                ctypes.POINTER(ctypes.c_uint64),
                ctypes.POINTER(ctypes.c_double),
            ]
            self.lib.mspti_skeleton_last_finalize_stats.restype = ctypes.c_int
        out_dir.mkdir(parents=True, exist_ok=True)
        skeleton = out_dir / f"rank_{rank:04d}.skeleton.jsonl"
        gap_ns = int(float(os.environ.get("MSPTI_GAP_US", "50")) * 1000)
        reorder_ns = int(float(os.environ.get("MSPTI_REORDER_US", "1000")) * 1000)
        t0 = time.perf_counter_ns()
        rc = self.lib.mspti_skeleton_start_gated(
            os.fsencode(skeleton), rank, local_rank, gap_ns, reorder_ns
        )
        self.start_ms = (time.perf_counter_ns() - t0) / 1e6
        if rc != 0:
            raise RuntimeError(f"mspti_skeleton_start_gated failed: rc={rc}")
        self.alive = True
        self.finalized = False
        self.finalize_reason: Optional[str] = None
        self.capture_begin_ms: Optional[float] = None
        self.capture_end_ms: Optional[float] = None
        self.capture_end_rc: Optional[int] = None
        self.finalize_ms: Optional[float] = None
        self.finalize_rc: Optional[int] = None
        self.finalize_flush_ms: Optional[float] = None
        self.finalize_drain_ms: Optional[float] = None
        self.finalize_complete = False
        self.raw_kernel_count: Optional[int] = None
        self.raw_comm_count: Optional[int] = None
        self.incomplete = False
        self.peak_queue_bytes: Optional[int] = None
        self.armed_fail = False
        self.process_wall_ms: Optional[float] = None
        self._proc_t0 = time.perf_counter()
        self.meta_path = out_dir / f"rank_{rank:04d}.mspti_meta.json"
        self.rank = rank
        self._meta_lock = threading.Lock()

    def _pull_capture_stats(self) -> None:
        if not hasattr(self.lib, "mspti_skeleton_last_capture_stats"):
            return
        gate = ctypes.c_double(0.0)
        drain = ctypes.c_double(0.0)
        incomplete = ctypes.c_int(0)
        peak = ctypes.c_uint64(0)
        self.lib.mspti_skeleton_last_capture_stats(
            ctypes.byref(gate),
            ctypes.byref(drain),
            ctypes.byref(incomplete),
            ctypes.byref(peak),
        )
        self.incomplete = bool(incomplete.value)
        self.peak_queue_bytes = int(peak.value)

    def _pull_finalize_stats(self) -> None:
        if not hasattr(self.lib, "mspti_skeleton_last_finalize_stats"):
            return
        flush = ctypes.c_double(0.0)
        drain = ctypes.c_double(0.0)
        complete = ctypes.c_int(0)
        raw_k = ctypes.c_uint64(0)
        raw_c = ctypes.c_uint64(0)
        peak = ctypes.c_uint64(0)
        gate = ctypes.c_double(0.0)
        self.lib.mspti_skeleton_last_finalize_stats(
            ctypes.byref(flush),
            ctypes.byref(drain),
            ctypes.byref(complete),
            ctypes.byref(raw_k),
            ctypes.byref(raw_c),
            ctypes.byref(peak),
            ctypes.byref(gate),
        )
        self.finalize_flush_ms = float(flush.value)
        self.finalize_drain_ms = float(drain.value)
        self.finalize_complete = bool(complete.value)
        self.raw_kernel_count = int(raw_k.value)
        self.raw_comm_count = int(raw_c.value)
        self.peak_queue_bytes = int(peak.value)

    def begin(self, step: int) -> None:
        t0 = time.perf_counter_ns()
        rc = self.lib.mspti_skeleton_capture_begin(step)
        self.capture_begin_ms = (time.perf_counter_ns() - t0) / 1e6
        if rc != 0:
            self.armed_fail = True
            raise RuntimeError(f"mspti_skeleton_capture_begin failed: rc={rc}")

    def end(self, step: int) -> None:
        t0 = time.perf_counter_ns()
        rc = self.lib.mspti_skeleton_capture_end(step)
        self.capture_end_ms = (time.perf_counter_ns() - t0) / 1e6
        self._pull_capture_stats()
        self.capture_end_rc = int(rc)
        if rc != 0 or self.incomplete:
            self.armed_fail = True
            print(
                f"[mspti_skeleton] capture_end incomplete rc={rc} "
                f"incomplete={self.incomplete} gate_ms={self.capture_end_ms} "
                f"peak_queue_bytes={self.peak_queue_bytes}",
                flush=True,
            )

    def finalize(self, reason: str) -> int:
        """FlushAll(0)+drain+Unsubscribe+DROP+关文件。幂等；含 Stop 语义。"""
        with self._meta_lock:
            if self.finalized:
                return int(self.finalize_rc or 0)
            self.finalize_reason = reason
            t0 = time.perf_counter_ns()
            rc = int(self.lib.mspti_skeleton_finalize())
            self.finalize_ms = (time.perf_counter_ns() - t0) / 1e6
            self.finalize_rc = rc
            self.finalized = True
            self.alive = False
            self.process_wall_ms = (time.perf_counter() - self._proc_t0) * 1000.0
            self._pull_finalize_stats()
            if rc != 0 or self.incomplete or not self.finalize_complete:
                self.armed_fail = True
            self._write_meta()
            if self.armed_fail:
                print(
                    f"[mspti_skeleton] finalize fail-closed via {reason} rc={rc} "
                    f"complete={self.finalize_complete} incomplete={self.incomplete} "
                    f"flush_ms={self.finalize_flush_ms} drain_ms={self.finalize_drain_ms} "
                    f"raw_kernel={self.raw_kernel_count} raw_comm={self.raw_comm_count}",
                    flush=True,
                )
            else:
                print(
                    f"[mspti_skeleton] finalize OK via {reason} "
                    f"flush_ms={self.finalize_flush_ms} "
                    f"drain_ms={self.finalize_drain_ms} "
                    f"raw_kernel={self.raw_kernel_count} raw_comm={self.raw_comm_count}",
                    flush=True,
                )
            return rc

    def stop(self) -> None:
        self.finalize("atexit")

    def _write_meta(self) -> None:
        self.meta_path.write_text(
            json.dumps(
                {
                    "rank": self.rank,
                    "collector_start_ms": self.start_ms,
                    "capture_begin_ms": self.capture_begin_ms,
                    "capture_end_ms": self.capture_end_ms,
                    "capture_end_rc": self.capture_end_rc,
                    "finalize_ms": self.finalize_ms,
                    "finalize_rc": self.finalize_rc,
                    "finalize_flush_ms": self.finalize_flush_ms,
                    "finalize_drain_ms": self.finalize_drain_ms,
                    "finalize_total_ms": self.finalize_ms,
                    "finalize_complete": self.finalize_complete,
                    "finalize_reason": self.finalize_reason,
                    "process_wall_ms": self.process_wall_ms,
                    "raw_kernel_count": self.raw_kernel_count,
                    "raw_comm_count": self.raw_comm_count,
                    "raw_kernels": self.raw_kernel_count,
                    "raw_comms": self.raw_comm_count,
                    "peak_queue_bytes": self.peak_queue_bytes,
                    "incomplete": self.incomplete or (not self.finalize_complete),
                    "armed_fail": self.armed_fail,
                    "gate": "capture_begin_end",
                    "note": (
                        "capture_end: Disable KERNEL/COMM only (no FlushAll); "
                        "finalize: msptiActivityFlushAll(0) on dedicated thread + "
                        "real CV drain + Unsubscribe/join/DROP; "
                        "explicit reasons only for strict PASS"
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


_STATE: dict[str, Any] = {
    "installed": False,
    "collector": None,
    "target_iter": 10,
    "captured": False,
    "patch_done": False,
    "train_patch_done": False,
    "pretrain_patch_done": False,
    "train_step_calls": 0,
}


def _rank_selected() -> bool:
    ranks = os.environ.get("MSPTI_CAPTURE_RANKS", "all").strip()
    if ranks == "all":
        return True
    rank = int(os.environ.get("RANK", "-1"))
    return rank in {int(x) for x in ranks.split(",") if x.strip()}


def _read_curr_iteration() -> Optional[int]:
    try:
        from megatron.training import get_args

        args = get_args()
        for attr in ("curr_iteration", "iteration"):
            value = getattr(args, attr, None)
            if value is not None:
                return int(value)
    except Exception:
        return None
    return None


def _finalize_collector(reason: str) -> None:
    collector: Optional[_GatedCollector] = _STATE.get("collector")
    if collector is None:
        return
    print(f"[mspti_skeleton] finalize via {reason}", flush=True)
    collector.finalize(reason)


def _train_iters() -> Optional[int]:
    env = os.environ.get("TRAIN_ITERS") or os.environ.get("MSPTI_TRAIN_ITERS")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        from megatron.training import get_args

        args = get_args()
        value = getattr(args, "train_iters", None)
        return int(value) if value is not None else None
    except Exception:
        return None


def _wrap_train_step(orig: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        target = int(_STATE["target_iter"])
        curr = _read_curr_iteration()
        logged_after = None if curr is None else curr + 1
        collector: Optional[_GatedCollector] = _STATE["collector"]
        do_capture = (
            collector is not None
            and not _STATE["captured"]
            and logged_after is not None
            and logged_after == target
        )
        if do_capture:
            assert collector is not None
            collector.begin(target)
            print(
                f"[mspti_skeleton] capture_begin megatron_iter={target} "
                f"curr_iteration={curr}",
                flush=True,
            )
        try:
            return orig(*args, **kwargs)
        finally:
            _STATE["train_step_calls"] = int(_STATE.get("train_step_calls", 0)) + 1
            if do_capture and collector is not None:
                collector.end(target)
                _STATE["captured"] = True
                print(
                    f"[mspti_skeleton] capture_end megatron_iter={target} "
                    f"begin_ms={collector.capture_begin_ms} "
                    f"end_ms={collector.capture_end_ms} "
                    f"(gate only; finalize deferred)",
                    flush=True,
                )
            # 基于真实 train_iters / 调用计数：最后一个 train_step 完整返回后显式 Finalize。
            train_iters = _train_iters()
            calls = int(_STATE.get("train_step_calls", 0))
            last_by_iter = (
                logged_after is not None
                and train_iters is not None
                and logged_after >= train_iters
            )
            last_by_calls = train_iters is not None and calls >= train_iters
            if (
                collector is not None
                and not collector.finalized
                and (last_by_iter or last_by_calls)
            ):
                _finalize_collector("last_train_step")

    wrapped._mspti_skeleton_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def _wrap_train(orig: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return orig(*args, **kwargs)
        finally:
            _finalize_collector("train.finally")

    wrapped._mspti_skeleton_train_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def _wrap_pretrain(orig: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return orig(*args, **kwargs)
        finally:
            _finalize_collector("pretrain.finally")

    wrapped._mspti_skeleton_pretrain_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def _try_patch_attr(module_name: str, attr: str, wrapper: Callable, flag: str) -> bool:
    mod = sys.modules.get(module_name)
    if mod is None:
        return False
    fn = getattr(mod, attr, None)
    if fn is None or not callable(fn):
        return False
    if getattr(fn, flag, False):
        return True
    setattr(mod, attr, wrapper(fn))
    print(f"[mspti_skeleton] patched {module_name}.{attr}", flush=True)
    return True


_PATCH_TARGETS = (
    "mindspeed_llm.training.training",
    "mindspeed_llm.training",
    "megatron.training.training",
    "megatron.training",
    "pretrain_gpt",
    "__main__",
)


def _ensure_patches() -> bool:
    """原位 patch 实际 MindSpeed/Megatron 调用链（模块已在 sys.modules 时）。

    禁止把 Megatron 函数反向覆盖到 MindSpeed（已删除 _rebind_mindspeed）。
    优先 patch mindspeed_llm.training.training（真实训练入口）。
    不在 sitecustomize 阶段强行 import 重依赖。
    """
    ok = False
    for mod_name in (
        "mindspeed_llm.training.training",
        "megatron.training.training",
    ):
        if _try_patch_attr(
            mod_name, "train_step", _wrap_train_step, "_mspti_skeleton_wrapped"
        ):
            ok = True

    train_ok = False
    for mod_name in (
        "mindspeed_llm.training.training",
        "megatron.training.training",
    ):
        if _try_patch_attr(
            mod_name, "train", _wrap_train, "_mspti_skeleton_train_wrapped"
        ):
            train_ok = True

    pretrain_ok = False
    for mod_name in (
        "mindspeed_llm.training.training",
        "mindspeed_llm.training",
        "megatron.training.training",
        "megatron.training",
    ):
        if _try_patch_attr(
            mod_name, "pretrain", _wrap_pretrain, "_mspti_skeleton_pretrain_wrapped"
        ):
            pretrain_ok = True

    for mod_name, attr, wrapper, flag in (
        ("mindspeed_llm.training", "pretrain", _wrap_pretrain, "_mspti_skeleton_pretrain_wrapped"),
        ("mindspeed_llm.training", "train", _wrap_train, "_mspti_skeleton_train_wrapped"),
        ("__main__", "pretrain", _wrap_pretrain, "_mspti_skeleton_pretrain_wrapped"),
        ("__main__", "train", _wrap_train, "_mspti_skeleton_train_wrapped"),
        ("pretrain_gpt", "pretrain", _wrap_pretrain, "_mspti_skeleton_pretrain_wrapped"),
        ("pretrain_gpt", "train", _wrap_train, "_mspti_skeleton_train_wrapped"),
    ):
        if _try_patch_attr(mod_name, attr, wrapper, flag):
            if attr == "train":
                train_ok = True
            if attr == "pretrain":
                pretrain_ok = True

    if ok:
        _STATE["patch_done"] = True
    if train_ok:
        _STATE["train_patch_done"] = True
    if pretrain_ok:
        _STATE["pretrain_patch_done"] = True
    return ok and (train_ok or pretrain_ok)


def _install_import_hook() -> None:
    """模块加载后立即原位 patch，不依赖脆弱轮询，也不反向覆盖 MindSpeed。"""
    if _STATE.get("import_hook"):
        return
    import builtins

    orig_import = builtins.__import__

    def _hooked_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
        mod = orig_import(name, globals, locals, fromlist, level)
        try:
            if name in _PATCH_TARGETS or any(
                name.startswith(t + ".") for t in _PATCH_TARGETS if t != "__main__"
            ):
                _ensure_patches()
            elif fromlist:
                for leaf in fromlist:
                    full = f"{name}.{leaf}" if name else leaf
                    if full in _PATCH_TARGETS or name in _PATCH_TARGETS:
                        _ensure_patches()
                        break
        except Exception as exc:  # noqa: BLE001
            print(f"[mspti_skeleton] import-hook patch err: {exc!r}", flush=True)
        return mod

    builtins.__import__ = _hooked_import  # type: ignore[assignment]
    _STATE["import_hook"] = True


def _poll_patches() -> None:
    # Short bounded retry for race where import hook missed package export rebind.
    for _ in range(200):
        if _ensure_patches():
            return
        time.sleep(0.05)


def install() -> None:
    if _STATE["installed"] or not _env_truthy("MSPTI_SKELETON"):
        return
    if not _is_training_worker():
        print("[mspti_skeleton] skip non-training process", flush=True)
        _STATE["installed"] = True
        return
    rank = int(os.environ["RANK"])
    local_rank = int(os.environ["LOCAL_RANK"])
    _STATE["target_iter"] = int(os.environ.get("MSPTI_CAPTURE_MEGATRON_ITER", "10"))
    out_dir = Path(os.environ.get("MSPTI_OUT_DIR", "."))
    lib = os.environ.get("MSPTI_COLLECTOR_LIB", "")
    if _rank_selected():
        if not lib:
            raise RuntimeError("MSPTI_COLLECTOR_LIB is required when MSPTI_SKELETON=1")
        collector = _GatedCollector(lib, out_dir, rank, local_rank)
        _STATE["collector"] = collector
        # atexit 仅兜底；正式主路径必须显式 Finalize。
        atexit.register(_finalize_collector, "atexit")
        print(f"[mspti_skeleton] collector start_gated rank={rank}", flush=True)
    _install_import_hook()
    if not _ensure_patches():
        threading.Thread(target=_poll_patches, name="mspti-patch", daemon=True).start()
    _STATE["installed"] = True
    print(
        f"[mspti_skeleton] installed rank={rank} selected={int(_rank_selected())} "
        f"target_iter={_STATE['target_iter']}",
        flush=True,
    )
