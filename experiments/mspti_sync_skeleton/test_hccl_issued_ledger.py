#!/usr/bin/env python3
"""Local stub/overflow tests for hccl_issued_ledger.cpp (no NPU required)."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent


def compile_shared(source: Path, output: Path) -> None:
    command = [
        os.environ.get("CXX", "c++"),
        "-std=c++17",
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        "-O2",
    ]
    if platform.system() == "Darwin":
        command += ["-dynamiclib"]
    else:
        command += ["-shared", "-fPIC"]
    command += [str(source), "-o", str(output)]
    if platform.system() != "Darwin":
        command += ["-pthread", "-ldl"]
    subprocess.run(command, check=True)


def configure_functions(ledger: ctypes.CDLL) -> dict[str, object]:
    ledger.hccl_issued_ledger_begin.argtypes = [ctypes.c_int64]
    ledger.hccl_issued_ledger_begin.restype = ctypes.c_int
    ledger.hccl_issued_ledger_end.argtypes = [ctypes.c_int64]
    ledger.hccl_issued_ledger_end.restype = ctypes.c_int
    ledger.hccl_issued_ledger_finalize.argtypes = []
    ledger.hccl_issued_ledger_finalize.restype = ctypes.c_int
    ledger.hccl_issued_ledger_capacity.argtypes = []
    ledger.hccl_issued_ledger_capacity.restype = ctypes.c_uint64

    pointer = ctypes.c_void_p
    u64 = ctypes.c_uint64
    i32 = ctypes.c_int32
    u32 = ctypes.c_uint32
    functions: dict[str, object] = {}
    specifications = {
        "HcclAllReduce": [pointer, pointer, u64, i32, i32, pointer, pointer],
        "HcclAllGather": [pointer, pointer, u64, i32, pointer, pointer],
        "HcclReduceScatter": [pointer, pointer, u64, i32, i32, pointer, pointer],
        "HcclBroadcast": [pointer, u64, i32, u32, pointer, pointer],
        "HcclSend": [pointer, u64, i32, u32, pointer, pointer],
        "HcclRecv": [pointer, u64, i32, u32, pointer, pointer],
    }
    for name, argtypes in specifications.items():
        function = getattr(ledger, name)
        function.argtypes = argtypes
        function.restype = i32
        functions[name] = function
    return functions


def invoke(functions: dict[str, object], name: str, count: int = 17) -> int:
    null = ctypes.c_void_p()
    if name == "HcclAllReduce":
        return functions[name](null, null, count, 1, 2, null, null)  # type: ignore[operator]
    if name == "HcclAllGather":
        return functions[name](null, null, count, 1, null, null)  # type: ignore[operator]
    if name == "HcclReduceScatter":
        return functions[name](null, null, count, 1, 2, null, null)  # type: ignore[operator]
    if name == "HcclBroadcast":
        return functions[name](null, count, 1, 3, null, null)  # type: ignore[operator]
    if name in {"HcclSend", "HcclRecv"}:
        return functions[name](null, count, 1, 4, null, null)  # type: ignore[operator]
    raise AssertionError(name)


def read_outputs(output: Path) -> tuple[list[dict], dict]:
    rows = [
        json.loads(line)
        for line in (output / "rank_0003.hccl_issued.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    summary = json.loads(
        (output / "rank_0003.hccl_issued_summary.json").read_text(encoding="utf-8")
    )
    return rows, summary


def driver(ledger_path: Path, stub_path: Path, output: Path, case: str) -> int:
    os.environ["RANK"] = "3"
    os.environ["HCCL_ISSUED_LEDGER_OUT_DIR"] = str(output)
    os.environ["HCCL_ISSUED_LEDGER_HCCL_SO"] = str(stub_path)
    ctypes.CDLL(str(stub_path), mode=ctypes.RTLD_GLOBAL)
    ledger = ctypes.CDLL(str(ledger_path), mode=ctypes.RTLD_GLOBAL)
    functions = configure_functions(ledger)

    assert invoke(functions, "HcclAllReduce", 5) == 0  # deliberately outside gate
    assert ledger.hccl_issued_ledger_begin(2) == 0
    if case == "basic":
        names = [
            "HcclAllReduce",
            "HcclAllGather",
            "HcclReduceScatter",
            "HcclBroadcast",
            "HcclSend",
            "HcclRecv",
        ]
        for index, name in enumerate(names):
            assert invoke(functions, name, 100 + index) == 0
    elif case == "overflow":
        capacity = int(ledger.hccl_issued_ledger_capacity())
        for index in range(capacity + 1):
            assert invoke(functions, "HcclAllReduce", index + 1) == 0
    else:
        raise AssertionError(case)
    assert ledger.hccl_issued_ledger_end(2) == 0
    assert invoke(functions, "HcclAllReduce", 7) == 0  # deliberately outside gate
    rc = int(ledger.hccl_issued_ledger_finalize())
    assert int(ledger.hccl_issued_ledger_finalize()) == rc  # idempotent
    rows, summary = read_outputs(output)

    assert summary["begin_step"] == 2
    assert summary["begin_count"] == 1
    assert summary["end_count"] == 1
    assert summary["outside_gate_total"] == 2
    assert summary["all_bindings_ok"] is True
    assert all(item["resolved"] and not item["self_interpose"] for item in summary["bindings"])
    assert all(Path(item["path"]).resolve() == stub_path.resolve() for item in summary["bindings"])
    assert all(row["returned"] and row["rc"] == 0 and row["entry_ns"] <= row["return_ns"] for row in rows)

    if case == "basic":
        assert rc == 0
        assert summary["pass"] is True
        assert summary["captured_issued"] == 6
        assert summary["captured_accepted"] == 6
        assert summary["overflow_count"] == 0
        assert [row["op"] for row in rows] == names
        assert [row["seq"] for row in rows] == list(range(1, 7))
    else:
        assert rc != 0
        assert summary["pass"] is False
        assert summary["captured_issued"] == summary["capacity"] + 1
        assert summary["captured_stored"] == summary["capacity"]
        assert summary["overflow_count"] == 1
        assert len(rows) == summary["capacity"]
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", action="store_true")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--stub", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", choices=("basic", "overflow"))
    args = parser.parse_args()
    if args.driver:
        assert args.ledger and args.stub and args.output and args.case
        return driver(args.ledger, args.stub, args.output, args.case)

    with tempfile.TemporaryDirectory(prefix="hccl-issued-ledger-") as temporary:
        root = Path(temporary)
        extension = ".dylib" if platform.system() == "Darwin" else ".so"
        ledger = root / f"libhccl_issued_ledger{extension}"
        stub = root / f"libhccl_stub{extension}"
        compile_shared(HERE / "hccl_issued_ledger.cpp", ledger)
        compile_shared(HERE / "test_hccl_issued_ledger_stub.cpp", stub)
        for case in ("basic", "overflow"):
            output = root / case
            output.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--driver",
                    "--ledger",
                    str(ledger),
                    "--stub",
                    str(stub),
                    "--output",
                    str(output),
                    "--case",
                    case,
                ],
                check=True,
            )
    print("hccl issued ledger tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
