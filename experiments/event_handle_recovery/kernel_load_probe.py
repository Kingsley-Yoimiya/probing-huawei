#!/usr/bin/env python3
"""Independent load probe for FINAL vs TMP negative."""
import ctypes
import json
import sys


def probe(path: str) -> dict:
    acl = ctypes.CDLL("libascendcl.so")
    acl.aclInit(None)
    acl.aclrtSetDevice(0)
    ctx = ctypes.c_void_p()
    acl.aclrtCreateContext(ctypes.byref(ctx), 0)
    handle = ctypes.c_void_p()
    load_rc = acl.aclrtBinaryLoadFromFile(path.encode(), None, ctypes.byref(handle))
    func = ctypes.c_void_p()
    get_rc = -1
    if load_rc == 0:
        get_rc = acl.aclrtBinaryGetFunction(
            handle, b"d51_compute_delay_kernel", ctypes.byref(func)
        )
    return {"path": path, "load_rc": int(load_rc), "get_function_rc": int(get_rc)}


if __name__ == "__main__":
    out = {"FINAL": probe(sys.argv[1]), "TMP_negative": probe(sys.argv[2])}
    print(json.dumps(out, indent=2))
