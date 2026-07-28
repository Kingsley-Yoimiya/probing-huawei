#!/usr/bin/env python3
"""附录 A：full_fidelity 删表消融 → ABLATION_MATRIX。"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ABROOT = Path(__file__).resolve().parent
TEMPLATE = ABROOT / "_template"
FORMAL = Path(
    "/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/"
    "20260725_012957-yjr-as-c-p3-sw-a-loud"
)
FULL = Path(
    "/Users/yinjinrun/Codespace/myportal/project/probing-huawei/results/ascend-ais/"
    "pillar_c/20260725_230350-pillar-c-p3-sw-a-loud/full_fidelity"
)
SCORE = Path(
    "/Users/yinjinrun/Codespace/myportal/project/probing-test/scripts/fail-slow/"
    "score_dlevel_sql.py"
)
RECIPES = Path(
    "/Users/yinjinrun/Codespace/myportal/project/probing-huawei/scripts/fail-slow/"
    "dose_recipes.yaml"
)

ARMS: list[tuple[str, str | None, str]] = [
    ("baseline", None, "对照（完整 dump）"),
    ("drop_cpu.utilization", "cpu.utilization", "删 cpu.utilization"),
    ("drop_gpu.utilization", "gpu.utilization", "删 gpu.utilization"),
    ("drop_cpu.tasks", "cpu.tasks", "删 cpu.tasks"),
    ("drop_gpu.hccs", "gpu.hccs", "删 gpu.hccs"),
    ("drop_python.torch_trace", "python.torch_trace", "删 python.torch_trace"),
    ("drop_python.trace_event", "python.trace_event", "删 python.trace_event"),
    ("drop_python.variables", "python.variables", "删 python.variables"),
    ("drop_python.torch_step_timing", "python.torch_step_timing", "删 python.torch_step_timing"),
    ("drop_python.comm_collective", "python.comm_collective", "删 python.comm_collective"),
]

QUERY_INVALIDATE = {
    "cpu.utilization": ["query_p3sw_rss_window.txt", "query_cpu_util.txt"],
    "gpu.utilization": ["query_gpu_util.txt"],
    "cpu.tasks": ["query_cpu_tasks.txt"],
}


def dir_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def table_bytes(pdata: Path, table: str) -> int:
    total = 0
    if not pdata.is_dir():
        return 0
    for pid_dir in pdata.iterdir():
        if not pid_dir.is_dir() or pid_dir.name == "crash":
            continue
        p = pid_dir / table
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += dir_bytes(p)
    return total


def find_probing_dir(ws: Path) -> Path:
    hits = list(ws.glob("P3-SW-A/**/C2_probing/probing"))
    if not hits:
        raise FileNotFoundError(f"no probing dir under {ws}")
    return hits[0]


def ensure_template() -> None:
    if TEMPLATE.is_dir() and (TEMPLATE / "P3-SW-A").is_dir():
        return
    TEMPLATE.mkdir(parents=True, exist_ok=True)
    if (TEMPLATE / "P3-SW-A").exists():
        shutil.rmtree(TEMPLATE / "P3-SW-A")
    shutil.copytree(FORMAL / "P3-SW-A", TEMPLATE / "P3-SW-A")
    prob_ff = next(FULL.glob("P3-SW-A/**/C2_probing/probing"))
    prob_tm = find_probing_dir(TEMPLATE)
    for item in prob_tm.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in prob_ff.iterdir():
        dest = prob_tm / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
    link = TEMPLATE / "probing_data"
    if link.is_symlink() or link.exists():
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
    link.symlink_to(FULL / "probing_data")


def clone_probing_data(dst_pdata: Path, src_pdata: Path) -> None:
    if dst_pdata.exists():
        shutil.rmtree(dst_pdata)
    # APFS clone on macOS keeps disk use low for repeated ablation arms.
    cp = subprocess.run(["cp", "-cR", str(src_pdata), str(dst_pdata)], capture_output=True)
    if cp.returncode != 0:
        shutil.copytree(src_pdata, dst_pdata)


def clone_workspace(arm_id: str, pdata_master: Path) -> Path:
    dst = ABROOT / f"arm_{arm_id}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(TEMPLATE, dst, symlinks=True)
    pdata = dst / "probing_data"
    if pdata.is_symlink():
        pdata.unlink()
    elif pdata.is_dir():
        shutil.rmtree(pdata)
    clone_probing_data(pdata, pdata_master)
    return dst


def drop_table(ws: Path, table: str) -> None:
    pdata = ws / "probing_data"
    for pid_dir in pdata.iterdir():
        if not pid_dir.is_dir() or pid_dir.name == "crash":
            continue
        p = pid_dir / table
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)
    prob = find_probing_dir(ws)
    manifest_path = prob / "query_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    present = manifest.setdefault("tables_present", {})
    missing = manifest.setdefault("tables_missing", [])
    if table in present:
        present[table] = False
        if table not in missing:
            missing.append(table)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    for qf in QUERY_INVALIDATE.get(table, []):
        qp = prob / qf
        if qp.is_file():
            qp.write_text(f"-- ablation: table {table} removed\n", encoding="utf-8")


def score_workspace(ws: Path) -> dict:
    subprocess.run(
        [
            sys.executable,
            str(SCORE),
            "--result-root",
            str(ws),
            "--cases",
            "P3-SW-A",
            "--dose",
            "loud",
            "--recipes",
            str(RECIPES),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    csv = ws / "scoring_table_SQL_loud.csv"
    line = csv.read_text().strip().splitlines()[-1]
    parts = line.split(",")
    return {
        "d_level": parts[3],
        "c1_c0": parts[4],
        "tool_sql": parts[9],
        "notes": parts[12] if len(parts) > 12 else "",
    }


def main() -> int:
    ensure_template()
    pdata_ref = FULL / "probing_data"
    pdata_master = ABROOT / "_probing_data_master"
    if not pdata_master.is_dir():
        clone_probing_data(pdata_master, pdata_ref)
    total_bytes = dir_bytes(pdata_master)
    rows: list[dict] = []

    for arm_id, table, label in ARMS:
        ws = clone_workspace(arm_id, pdata_master)
        if table:
            drop_table(ws, table)
        dropped_b = table_bytes(ws / "probing_data", table) if table else 0
        remain_b = dir_bytes(ws / "probing_data")
        pct = (dropped_b / total_bytes * 100) if table and total_bytes else 0.0
        sc = score_workspace(ws)
        rows.append(
            {
                "arm_id": arm_id,
                "label": label,
                "table": table or "(none)",
                "dropped_bytes": dropped_b,
                "dropped_mib": dropped_b / 1024 / 1024,
                "pct_of_total": pct,
                "remain_bytes": remain_b,
                "d_level": sc["d_level"],
                "tool_sql": sc["tool_sql"],
                "c1_c0": sc["c1_c0"],
                "notes": sc["notes"][:240],
            }
        )
        print(f"[ablation] {arm_id}: D={sc['d_level']} sql={sc['tool_sql']}", flush=True)

    out_json = ABROOT / "ABLATION_MATRIX.json"
    out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
