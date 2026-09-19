"""ctypes bindings for LD_PRELOAD interpose library (single instance)."""
from __future__ import annotations

import ctypes
import os


def _ld_preload_active() -> bool:
    needle = "libacl_event_trace_v2.so"
    return needle in os.environ.get("LD_PRELOAD", "")


def load_preload_lib(preload_lib: str) -> ctypes.CDLL:
    if _ld_preload_active():
        return ctypes.CDLL(None)
    return ctypes.CDLL(preload_lib)


def bind_work_api(lib: ctypes.CDLL) -> ctypes.CDLL:
    lib.acl_event_trace_finalize.restype = ctypes.c_int
    lib.acl_event_work_prepare.restype = None
    lib.acl_event_work_cleanup.restype = None
    lib.acl_event_delay_arm.restype = None
    lib.acl_event_delay_disarm.restype = None
    return lib
