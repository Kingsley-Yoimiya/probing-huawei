#!/usr/bin/env python3
"""In-pod high-frequency yield poller for false-positive regression."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


def main() -> int:
    out = Path(os.environ["OUT_DIR"])
    code = Path(os.environ["CODE_DIR"])
    marker = os.environ["RUN_MARKER"]
    interval = float(os.environ.get("YIELD_INTERVAL_S", "0.15"))
    log = out / "yield_poll.jsonl"
    log.write_text("", encoding="utf-8")
    deadline = time.time() + float(os.environ.get("YIELD_POLL_TIMEOUT_S", "900"))

    def once(phase: str) -> tuple[int, str]:
        p = subprocess.run(
            [
                "python3",
                str(code / "opponent_check.py"),
                "--mode",
                "yield",
                "--out-dir",
                str(out),
                "--run-marker",
                marker,
                "--node-id",
                "0",
            ],
            capture_output=True,
            text=True,
        )
        lines = (p.stdout or "").strip().splitlines()
        last = lines[-1] if lines else ""
        rec = {
            "ts": time.time(),
            "rc": int(p.returncode),
            "line": last,
            "phase": phase,
        }
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return int(p.returncode), last

    while time.time() < deadline:
        done = (out / "node_0.done").exists()
        fail = (out / "node_0.fail").exists()
        if done or fail:
            for _ in range(25):
                once("exit_burst")
                time.sleep(interval)
            break
        once("run")
        time.sleep(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
