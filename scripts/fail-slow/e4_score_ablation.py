#!/usr/bin/env python3
"""Pillar-C E4：朴素砍量反例 —— 相对 E3 动态臂（有 SET↑）是否归因掉级。

尺：
  - 判分 = 采集内容够不够支撑「完整动态路径」归因
    · P3-SW：cpu.utilization_rss 主证
    · 完整动态路径（对齐 E2 enough）= RSS ∧ SET↑
    · 砍量臂禁止 SET↑ → 相对 E3 正例必缺升详半截
  - 辅尺 = 总落盘字节（全表；禁止只报 cold）；torch_trace 是否空
  - 禁止：训练 step_ms；禁止只报 cold
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Optional

RSS_ABS_THR_KB = 700_000
RSS_RISE_THR_KB = 50_000
MAGIC_MEMT = 0x4D454D54


def dir_bytes(p: Path) -> int:
    if not p.is_dir():
        return 0
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def table_breakdown(pdata: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    if not pdata.is_dir():
        return out
    for f in pdata.rglob("*"):
        if not f.is_file():
            continue
        parts = f.relative_to(pdata).parts
        key = parts[1] if len(parts) >= 2 else (parts[0] if parts else "_root")
        try:
            out[key] = out.get(key, 0) + f.stat().st_size
        except OSError:
            pass
    return out


def find_one(root: Path, name: str) -> Optional[Path]:
    hits = list(root.rglob(name))
    return hits[0] if hits else None


def parse_rss_query_txt(path: Path) -> list[tuple[int, int]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[tuple[int, int]] = []
    for line in text.splitlines():
        if "│" not in line:
            continue
        cells = [c.strip() for c in line.split("│") if c.strip()]
        if len(cells) < 3:
            continue
        if cells[0] in ("ts", "─") or set(cells[0]) <= {"─", "├", "┼", "┤"}:
            continue
        try:
            ts = int(cells[0])
            rss = int(float(cells[2]))
        except ValueError:
            continue
        out.append((ts, rss))
    return out


def judge_rss(series: list[tuple[int, int]]) -> dict[str, Any]:
    if not series:
        return {"ok": False, "note": "rss_dump_missing", "n": 0, "rise": 0, "max": 0}
    vals = [r for _, r in series]
    mx, mn = max(vals), min(vals)
    rise = mx - mn
    ok = mx >= RSS_ABS_THR_KB or rise >= RSS_RISE_THR_KB
    return {
        "ok": ok,
        "note": f"rise_kb={rise}:max_kb={mx}:n={len(vals)}",
        "n": len(vals),
        "rise": rise,
        "max": mx,
    }


def set_status(path: Optional[Path], expect_absent: bool = False) -> dict[str, Any]:
    if not path or not path.is_file():
        return {
            "ok": False,
            "present": False,
            "note": "set_upgrade.log_absent" if expect_absent else "set_upgrade.log_missing",
            "absent_ok": bool(expect_absent),
        }
    text = path.read_text(encoding="utf-8", errors="replace")
    ok = "SET_OK_WORKER" in text or ("SET_OK" in text and "SET_FAIL_ALL" not in text)
    fail_all = "SET_FAIL_ALL" in text
    if fail_all:
        ok = False
    lat = None
    m = re.search(r"SET_LATENCY_MS=(\d+)", text)
    if m:
        lat = int(m.group(1))
    return {
        "ok": ok,
        "present": True,
        "note": "SET_OK" if ok else ("SET_FAIL_ALL" if fail_all else "SET_FAIL"),
        "latency_ms": lat,
        "absent_ok": False,
    }


def parse_volume(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, Any] = {}
    for key in ("total_bytes", "cold_bytes", "hot_bytes", "other_bytes", "memc_bytes"):
        m = re.search(rf"{key}=(\d+)", text)
        if m:
            out[key] = int(m.group(1))
    return out


def memt_n_rows(path: Path) -> dict[str, Any]:
    try:
        buf = path.read_bytes()
    except OSError as e:
        return {"error": str(e), "file_bytes": 0, "n_rows": 0}
    if len(buf) < 64 or struct.unpack_from("<I", buf, 0)[0] != MAGIC_MEMT:
        return {"error": "not_memt", "file_bytes": len(buf), "n_rows": 0}
    _magic, _ver, _hsz, _bom, _ts, _flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    nrows = 0
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        if cs + 40 > len(buf):
            break
        _gen, used, row_count, state, _res, _min_ts, _max_ts = struct.unpack_from("<QIIIIqq", buf, cs)
        if row_count == 0 or used == 0:
            continue
        nrows += int(row_count)
    return {"file_bytes": len(buf), "n_rows": nrows, "nchunks": nchunks}


def resolve_pdata(arm: Path) -> Path:
    pdata = arm / "probing_data"
    if pdata.is_dir():
        return pdata
    hits = [p for p in arm.rglob("probing_data") if p.is_dir()]
    return hits[0] if hits else pdata


def score_arm(arm: Path, label: str, *, expect_no_set: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {"label": label, "arm_dir": str(arm), "exists": arm.is_dir()}
    if not arm.is_dir():
        row["rss_ok"] = False
        row["path_enough"] = False
        return row

    pdata = resolve_pdata(arm)
    total = dir_bytes(pdata)
    breakdown = table_breakdown(pdata)
    vol = parse_volume(find_one(arm, "volume_final.txt"))
    cold = int(vol.get("cold_bytes") or breakdown.get("cold", 0) or 0)

    rss_path = find_one(arm, "query_p3sw_rss_window.txt")
    if rss_path is None:
        for cand in arm.rglob("query_*.txt"):
            if "rss" in cand.name.lower() or "cpu" in cand.name.lower():
                rss_path = cand
                break
    series = parse_rss_query_txt(rss_path) if rss_path else []
    rss = judge_rss(series)

    sinfo = set_status(find_one(arm, "set_upgrade.log"), expect_absent=expect_no_set)

    tt_files = list(pdata.rglob("python.torch_trace")) if pdata.is_dir() else []
    tt_bytes = sum(f.stat().st_size for f in tt_files if f.is_file())
    dense = []
    for f in tt_files:
        if not f.is_file():
            continue
        info = memt_n_rows(f)
        if int(info.get("n_rows") or 0) > 0:
            dense.append({"path": str(f), **info})
    dense.sort(key=lambda x: -int(x.get("n_rows") or 0))
    tt_rows_all = sum(int(x.get("n_rows") or 0) for x in dense)

    # 完整动态路径够归因（对齐 E2）：RSS ∧ SET↑
    # 砍量臂：禁 SET → path_enough 必假（掉级）；rss_ok 单独报告
    if expect_no_set:
        path_enough = False  # 设计上无升详 → 相对 E3 动态路径不够
        set_control_ok = (not sinfo.get("present")) or (not sinfo.get("ok"))
    else:
        path_enough = bool(rss.get("ok")) and bool(sinfo.get("ok"))
        set_control_ok = bool(sinfo.get("ok"))

    row.update(
        {
            "probing_data": str(pdata),
            "total_dump_bytes": total,
            "cold_bytes": cold,
            "breakdown": dict(sorted(breakdown.items(), key=lambda kv: -kv[1])[:15]),
            "rss": rss,
            "rss_ok": bool(rss.get("ok")),
            "rss_path": str(rss_path) if rss_path else None,
            "set": sinfo,
            "set_control_ok": set_control_ok,
            "torch_trace_files": len(tt_files),
            "torch_trace_bytes": tt_bytes,
            "torch_trace_dense_ranks": len(dense),
            "torch_trace_rows_all": tt_rows_all,
            "torch_trace_top": dense[:3],
            "path_enough": path_enough,
        }
    )
    return row


def resolve_e3_dynamic(e3_ref: Path, resident_rate: str) -> Path:
    cand = e3_ref / f"rate_{resident_rate}"
    if cand.is_dir():
        return cand
    link = e3_ref / "dynamic_link"
    if link.exists():
        return link.resolve()
    return cand


def miB(b: Optional[int]) -> str:
    if b is None:
        return "?"
    return f"{b / 1024 / 1024:.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--e3-ref", required=True)
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--resident-rate", default="0")
    ap.add_argument("--w-star", type=int, default=100)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    parent = Path(args.parent_local)
    e3_ref = Path(args.e3_ref)
    naive = parent / "naive_cut"
    # arm 脚本可能已把产物写在 naive_cut；若空则找同名
    if not (naive / "probing_data").is_dir():
        hits = [p for p in parent.rglob("probing_data") if "naive" in str(p).lower()]
        if hits:
            naive = hits[0].parent

    pos = resolve_e3_dynamic(e3_ref, args.resident_rate)
    naive_row = score_arm(naive, "naive_cut", expect_no_set=True)
    pos_row = score_arm(pos, "e3_dynamic", expect_no_set=False)

    # 掉级：正例 path_enough=Y，砍量 path_enough=N
    dropped = bool(pos_row.get("path_enough")) and (not bool(naive_row.get("path_enough")))
    # 量仍小：砍量总落盘 ≤ 正例（允许噪声）或显著小于全量锚点
    n_bytes = int(naive_row.get("total_dump_bytes") or 0)
    p_bytes = int(pos_row.get("total_dump_bytes") or 0)
    volume_still_small = (n_bytes > 0 and p_bytes > 0 and n_bytes <= int(p_bytes * 1.05)) or (
        n_bytes > 0 and n_bytes < 2_000_000_000
    )

    # 机制对照：砍量无 SET / TT 空或远稀于正例
    tt_drop = int(naive_row.get("torch_trace_rows_all") or 0) < max(
        1, int(pos_row.get("torch_trace_rows_all") or 0) // 10
    )
    set_absent_ok = bool(naive_row.get("set_control_ok"))

    verdict = "PASS" if (dropped and set_absent_ok) else "FAIL"
    if dropped and set_absent_ok and not volume_still_small:
        verdict = "PASS_WEAK"  # 掉级成立但量未明显更小

    summary = {
        "case": args.case,
        "parent": str(parent),
        "e3_ref": str(e3_ref),
        "resident_rate": args.resident_rate,
        "w_star": args.w_star,
        "naive": naive_row,
        "e3_positive": {
            k: v for k, v in pos_row.items() if k != "breakdown" or True
        },
        "dropped": dropped,
        "volume_still_small": volume_still_small,
        "tt_drop": tt_drop,
        "set_absent_ok": set_absent_ok,
        "verdict": verdict,
        "note": "path_enough=RSS∧SET（完整动态路径）；砍量禁SET→掉级；RSS单独报告",
    }
    out_json = parent / "E4_ABLATION.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    n_set = (naive_row.get("set") or {}).get("note", "?")
    p_set = (pos_row.get("set") or {}).get("note", "?")
    n_rss = (naive_row.get("rss") or {}).get("note", "?")
    p_rss = (pos_row.get("rss") or {}).get("note", "?")

    lines = [
        "# E4_ABLATION · 朴素砍量反例（证「省量必须配触发升详」）",
        "",
        f"> case=`{args.case}` loud · parent=`{parent.name}` @ grj-w0",
        f"> 砍量臂：常驻 rate=`{args.resident_rate}` · SAMPLE_MS=500 · **PILLAR_C_SET_UPGRADE=0**（无 mid SET）",
        f"> 正例：E3 动态臂 `{e3_ref.name}` / `rate_{args.resident_rate}`（rate=0→SET↑）",
        "> **判分**=采集内容够不够「完整动态路径」归因（P3：`cpu.utilization_rss` ∧ SET↑）；辅尺=总落盘；**禁止**只报 cold / **禁止**训练 step_ms。",
        "",
        f"## 结论：{verdict} —— 相对 E3 动态臂 **{'掉级' if dropped else '未掉级'}**",
        "",
        f"- 完整动态路径够归因（RSS∧SET）：正例 **{'Y' if pos_row.get('path_enough') else 'N'}** → 砍量 **{'Y' if naive_row.get('path_enough') else 'N'}**",
        f"- 砍量禁 SET 控制：**{'Y' if set_absent_ok else 'N'}**（`{n_set}`）",
        f"- 数据量仍小（辅）：**{'Y' if volume_still_small else 'N'}**（naive `{n_bytes}` B / e3 `{p_bytes}` B）",
        f"- torch_trace 升详缺失：**{'Y' if tt_drop else 'N'}**（naive rows=`{naive_row.get('torch_trace_rows_all')}` vs e3=`{pos_row.get('torch_trace_rows_all')}`）",
        f"- P3 RSS 主证单独：正例 **{'Y' if pos_row.get('rss_ok') else 'N'}**（`{p_rss}`）· 砍量 **{'Y' if naive_row.get('rss_ok') else 'N'}**（`{n_rss}`）",
        "",
        "## 分臂对照",
        "",
        "| 臂 | 配置 | total_dump_B | MiB | cold_B | RSS | SET↑ | path_enough | TT rows |",
        "|----|------|-------------:|----:|-------:|:---:|:----:|:-----------:|--------:|",
        (
            f"| E3 动态（正例） | rate={args.resident_rate}→SET1.0 | {p_bytes} | {miB(p_bytes)} | "
            f"{pos_row.get('cold_bytes') or 0} | {'Y' if pos_row.get('rss_ok') else 'N'} | {p_set} | "
            f"{'Y' if pos_row.get('path_enough') else 'N'} | {pos_row.get('torch_trace_rows_all') or 0} |"
        ),
        (
            f"| E4 砍量（naive） | rate={args.resident_rate} **禁SET** | {n_bytes} | {miB(n_bytes)} | "
            f"{naive_row.get('cold_bytes') or 0} | {'Y' if naive_row.get('rss_ok') else 'N'} | {n_set} | "
            f"{'Y' if naive_row.get('path_enough') else 'N'} | {naive_row.get('torch_trace_rows_all') or 0} |"
        ),
        "",
        "### 砍量臂分表（top）",
        "",
        "| table | bytes | MiB |",
        "|-------|------:|----:|",
    ]
    for k, v in (naive_row.get("breakdown") or {}).items():
        lines.append(f"| `{k}` | {v} | {miB(v)} |")

    lines += [
        "",
        "## 解读",
        "",
        "- **掉级定义**：完整动态路径 = `RSS ∧ SET↑`（与 E2 `enough` 对齐）。砍量臂设计去掉触发升详 → path_enough=N，相对 E3 正例掉级。",
        "- P3-SW 周期 `cpu.utilization` RSS 不依赖 torch 常驻密度；若砍量臂 RSS 仍 Y，说明「只砍 torch rate」对 **周期小表主证** 不够致命，但 **升详半截缺失** 仍证机制不可省（否则只剩粗判、无 W* 详采窗）。",
        "- 这挡「你不就是把采样率调低了吗」：同常驻稀度下，有无触发升详决定能否走完动态路径。",
        "- **禁止**用训练 step_ms 并比；全量臂本轮未重跑。",
        "",
        "## 设计回哺",
        "",
        "- 省量必须配触发升详：E4 砍量臂缺 SET↑ → 完整路径归因掉级；E3 有 SET↑ → 同 RSS 覆盖下总量 72.6%。",
        f"- 常驻 rate=`{args.resident_rate}` + SAMPLE_MS=500 可保周期小表；**不可**省略 mid SET 升详。",
        "",
        "## 产物",
        "",
        f"- `E4_ABLATION.json` · `naive_cut/` · `e3_positive/REUSE.txt`",
        f"- 本机：`{parent}`",
        f"- AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/{parent.name}/`",
        "",
    ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"[e4_score] verdict={verdict} dropped={dropped} → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
