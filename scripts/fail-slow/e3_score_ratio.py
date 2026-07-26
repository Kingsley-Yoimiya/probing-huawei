#!/usr/bin/env python3
"""Pillar-C E3：动态臂 vs 全量臂 —— 同覆盖下总落盘比（头条）。

尺：
  - 主尺 = 总落盘字节（probing_data 全树；禁止只报 cold）
  - 判分 = 采集内容够不够归因（P3-SW：cpu.utilization_rss）
  - 禁止：训练 step_ms 并比
  - W* 截窗：无 online retention 时 dump 后按步估算 torch_trace 截窗字节并写明
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Optional

# 复用 E2 RSS 判据
RSS_ABS_THR_KB = 700_000
RSS_RISE_THR_KB = 50_000
INJECT_STOP = 300
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
    """按表名汇总（跨 pid）；目录名即表名。"""
    out: dict[str, int] = {}
    if not pdata.is_dir():
        return out
    for f in pdata.rglob("*"):
        if not f.is_file():
            continue
        # probing_data/<pid>/<table>/... 或 probing_data/<pid>/<table>
        parts = f.relative_to(pdata).parts
        if len(parts) < 2:
            key = parts[0] if parts else "_root"
        else:
            key = parts[1]
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


def set_ok(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {"ok": False, "note": "set_upgrade.log_missing"}
    text = path.read_text(encoding="utf-8", errors="replace")
    ok = "SET_OK_WORKER" in text or ("SET_OK" in text and "SET_FAIL_ALL" not in text)
    fail_all = "SET_FAIL_ALL" in text
    if fail_all:
        ok = False
    lat = None
    m = re.search(r"SET_LATENCY_MS=(\d+)", text)
    if m:
        lat = int(m.group(1))
    wrong_key = "set probing.torch.profiling" in text and "Failed SET" in text
    # 正确键成功痕迹
    has_correct = "probing.torch.profiling=on,rate=1.0" in text or "SET_OK_WORKER" in text
    return {
        "ok": ok,
        "note": "SET_OK" if ok else ("SET_FAIL_ALL" if fail_all else "SET_FAIL"),
        "latency_ms": lat,
        "has_correct_key": has_correct,
        "wrong_key_noise": wrong_key,
    }


def parse_volume(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    out: dict[str, Any] = {"raw": text.strip()[:500]}
    for key in ("total_bytes", "cold_bytes", "hot_bytes", "other_bytes", "memc_bytes"):
        m = re.search(rf"{key}=(\d+)", text)
        if m:
            out[key] = int(m.group(1))
    return out


def memt_step_info(path: Path) -> dict[str, Any]:
    """轻量读 MEMT：步集合 + 行数（不全量解析字符串列）。"""
    try:
        buf = path.read_bytes()
    except OSError as e:
        return {"error": str(e)}
    if len(buf) < 64 or struct.unpack_from("<I", buf, 0)[0] != MAGIC_MEMT:
        return {"error": "not_memt", "file_bytes": len(buf)}
    _magic, _ver, _hsz, _bom, _ts, _flags, ncols, nchunks, chunk_size, data_off = struct.unpack_from(
        "<IHHHHIIIII", buf, 0
    )
    _wc, _rc, _pid, _pad = struct.unpack_from("<IIII", buf, 32)
    _start, chunks_recycled, rows_overwritten = struct.unpack_from("<QII", buf, 48)
    # find local_step col
    ls_idx = None
    cols = []
    for i in range(ncols):
        off = 64 + i * 64
        namelen = struct.unpack_from("<H", buf, off)[0]
        name = buf[off + 2 : off + 2 + namelen].decode("utf-8", "replace")
        dtype, esz = struct.unpack_from("<II", buf, off + 56)
        cols.append((name, dtype, esz))
        if name == "local_step":
            ls_idx = i
    steps: set[int] = set()
    nrows = 0
    for c in range(nchunks):
        cs = data_off + c * chunk_size
        if cs + 40 > len(buf):
            break
        _gen, used, row_count, state, _res, _min_ts, _max_ts = struct.unpack_from("<QIIIIqq", buf, cs)
        if row_count == 0 or used == 0:
            continue
        pos = cs + 40
        end = cs + 40 + used
        for _ in range(row_count):
            if pos + 4 > end:
                break
            row_len = struct.unpack_from("<I", buf, pos)[0]
            rstart = pos + 4
            rend = rstart + row_len
            if rend > end or row_len <= 0:
                break
            nrows += 1
            if ls_idx is not None:
                # walk columns to local_step
                p = rstart
                ok_row = True
                for ci, (name, dtype, esz) in enumerate(cols):
                    if p >= rend:
                        ok_row = False
                        break
                    if dtype in (8, 9):  # str/bytes length-prefixed
                        if p + 4 > rend:
                            ok_row = False
                            break
                        raw = struct.unpack_from("<i", buf, p)[0]
                        p += 4
                        if raw >= 0:
                            p += raw
                    else:
                        if ci == ls_idx:
                            if dtype == 3:  # i64
                                if p + 8 <= rend:
                                    steps.add(int(struct.unpack_from("<q", buf, p)[0]))
                            elif dtype == 7:  # u32
                                if p + 4 <= rend:
                                    steps.add(int(struct.unpack_from("<I", buf, p)[0]))
                            elif dtype == 2:  # i32
                                if p + 4 <= rend:
                                    steps.add(int(struct.unpack_from("<i", buf, p)[0]))
                        p += esz
                if not ok_row:
                    break
            pos = rend
    info = {
        "file_bytes": len(buf),
        "n_rows": nrows,
        "n_steps": len(steps),
        "step_min": min(steps) if steps else None,
        "step_max": max(steps) if steps else None,
        "rows_overwritten": rows_overwritten,
        "chunks_recycled": chunks_recycled,
    }
    if steps:
        # W 窗：锚 inject_stop，保留 (anchor-W, anchor]
        info["steps"] = sorted(steps)
    return info


def estimate_w_truncate_tt_bytes(tt_info: dict[str, Any], w: int, anchor: int = INJECT_STOP) -> dict[str, Any]:
    """按步比例估算截窗后 torch_trace 字节（MEMT 环文件本身不缩；此为内容量近似）。"""
    fb = int(tt_info.get("file_bytes") or 0)
    steps = tt_info.get("steps") or []
    if not steps or fb <= 0:
        return {
            "mode": "offline_truncate_estimate",
            "w": w,
            "anchor": anchor,
            "raw_tt_bytes": fb,
            "est_tt_bytes_w": fb,
            "n_steps_raw": tt_info.get("n_steps", 0),
            "n_steps_w": 0,
            "note": "no_steps_or_empty",
        }
    keep = [s for s in steps if (anchor - w) < s <= anchor]
    if not keep:
        # 环内无 inject 窗步：退化为最近 W 个步
        keep = steps[-w:] if len(steps) > w else list(steps)
        note = "fallback_last_W_steps"
    else:
        note = "anchor_inject_stop"
    ratio = len(keep) / max(len(steps), 1)
    # 行级更准：若有 n_rows，按步均匀近似
    est = int(fb * ratio)
    return {
        "mode": "offline_truncate_estimate",
        "w": w,
        "anchor": anchor,
        "raw_tt_bytes": fb,
        "est_tt_bytes_w": est,
        "n_steps_raw": len(steps),
        "n_steps_w": len(keep),
        "ratio": round(ratio, 4),
        "note": note,
    }


def resolve_dynamic_dir(parent: Path, resident_rate: str) -> Path:
    cand = parent / f"rate_{resident_rate}"
    if cand.is_dir():
        return cand
    link = parent / "dynamic_link"
    if link.is_symlink() or link.is_dir():
        return link.resolve()
    return cand


def score_arm(arm: Path, label: str) -> dict[str, Any]:
    row: dict[str, Any] = {"label": label, "arm_dir": str(arm), "exists": arm.is_dir()}
    if not arm.is_dir():
        row["enough"] = False
        return row

    pdata = arm / "probing_data"
    # 有时 probing_data 在 by_pod 下未拉回顶层；优先顶层
    if not pdata.is_dir():
        # 搜一层
        hits = [p for p in arm.rglob("probing_data") if p.is_dir()]
        pdata = hits[0] if hits else pdata

    total = dir_bytes(pdata)
    breakdown = table_breakdown(pdata)
    vol = parse_volume(find_one(arm, "volume_final.txt"))
    cold = int(vol.get("cold_bytes") or 0)
    if cold == 0:
        cold = breakdown.get("cold", 0)

    rss_path = find_one(arm, "query_p3sw_rss_window.txt")
    if rss_path is None:
        for cand in arm.rglob("query_*.txt"):
            if "rss" in cand.name.lower() or "cpu" in cand.name.lower():
                rss_path = cand
                break
    series = parse_rss_query_txt(rss_path) if rss_path else []
    rss = judge_rss(series)

    sinfo = set_ok(find_one(arm, "set_upgrade.log"))

    tt_files = list(pdata.rglob("python.torch_trace")) if pdata.is_dir() else []
    # victim 通常最大
    tt_files_sorted = sorted(tt_files, key=lambda p: p.stat().st_size if p.is_file() else 0, reverse=True)
    tt_victim = tt_files_sorted[0] if tt_files_sorted else None
    tt_info = memt_step_info(tt_victim) if tt_victim else {}
    tt_bytes_all = sum(f.stat().st_size for f in tt_files if f.is_file())

    row.update(
        {
            "probing_data": str(pdata),
            "total_dump_bytes": total,
            "cold_bytes": cold,
            "breakdown": dict(sorted(breakdown.items(), key=lambda kv: -kv[1])[:20]),
            "rss": rss,
            "rss_path": str(rss_path) if rss_path else None,
            "set": sinfo,
            "torch_trace_files": len(tt_files),
            "torch_trace_bytes_all_ranks": tt_bytes_all,
            "torch_trace_victim": str(tt_victim) if tt_victim else None,
            "torch_trace_info": {k: v for k, v in tt_info.items() if k != "steps"},
            "torch_trace_steps": tt_info.get("steps"),
            "enough": bool(rss.get("ok")),
        }
    )
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--w-star", type=int, default=100)
    ap.add_argument("--resident-rate", default="0")
    ap.add_argument("--full-ref", default="")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    parent = Path(args.parent_local)
    dyn = resolve_dynamic_dir(parent, args.resident_rate)
    full_local = parent / "full_fidelity"
    # 复用：本机可能无 probing_data；用 total_dump_bytes.txt + 可选远端已缓存
    full_bytes_file = full_local / "total_dump_bytes.txt"
    reuse_txt = full_local / "REUSE.txt"

    dyn_row = score_arm(dyn, "dynamic")
    full_row: dict[str, Any] = {"label": "full_fidelity", "reuse": reuse_txt.is_file()}

    if (full_local / "probing_data").is_dir():
        full_row = score_arm(full_local, "full_fidelity")
        full_row["reuse"] = False
    elif full_bytes_file.is_file():
        fb = int(full_bytes_file.read_text().strip() or "0")
        full_row.update(
            {
                "exists": True,
                "total_dump_bytes": fb,
                "cold_bytes": None,
                "enough": True,  # 复用 B Loud 金标覆盖；不重判训练
                "evidence_note": "reuse_full_fidelity_upper_bound; cover=D4_reuse_B_loud",
                "full_ref": args.full_ref,
                "breakdown": {},
            }
        )
        if reuse_txt.is_file():
            full_row["reuse_meta"] = reuse_txt.read_text(encoding="utf-8", errors="replace").strip()
    else:
        full_row.update({"exists": False, "total_dump_bytes": 0, "enough": False})

    # W* 截窗估算（动态臂）
    w_est = None
    dyn_adj = None
    tt_info = {
        **(dyn_row.get("torch_trace_info") or {}),
        "steps": dyn_row.get("torch_trace_steps"),
        "file_bytes": (dyn_row.get("torch_trace_info") or {}).get("file_bytes")
        or dyn_row.get("torch_trace_bytes_all_ranks"),
    }
    # 用全 rank torch_trace 总量按 victim 步比例缩放
    if dyn_row.get("torch_trace_steps") or (dyn_row.get("torch_trace_info") or {}).get("n_steps"):
        victim_est = estimate_w_truncate_tt_bytes(
            {
                "file_bytes": dyn_row.get("torch_trace_bytes_all_ranks") or 0,
                "steps": dyn_row.get("torch_trace_steps") or [],
                "n_steps": (dyn_row.get("torch_trace_info") or {}).get("n_steps"),
            },
            args.w_star,
        )
        w_est = victim_est
        raw_total = int(dyn_row.get("total_dump_bytes") or 0)
        raw_tt = int(dyn_row.get("torch_trace_bytes_all_ranks") or 0)
        est_tt = int(victim_est.get("est_tt_bytes_w") or raw_tt)
        dyn_adj = raw_total - raw_tt + est_tt
        dyn_row["total_dump_bytes_w_star_est"] = dyn_adj
        dyn_row["w_truncate"] = w_est
    else:
        dyn_row["w_truncate"] = {
            "mode": "offline_truncate_estimate",
            "note": "torch_trace_missing_or_empty_steps; report raw dump only",
            "w": args.w_star,
        }

    d_bytes = int(dyn_row.get("total_dump_bytes") or 0)
    f_bytes = int(full_row.get("total_dump_bytes") or 0)
    ratio = (100.0 * d_bytes / f_bytes) if f_bytes > 0 else None
    ratio_w = (100.0 * dyn_adj / f_bytes) if (f_bytes > 0 and dyn_adj is not None) else None

    # 头条优先用 W* 估算（设计意图）；同时报 raw
    headline_pct = ratio_w if ratio_w is not None else ratio
    headline_note = "W*_est" if ratio_w is not None else "raw_dump"

    summary = {
        "case": args.case,
        "parent": str(parent),
        "resident_rate": args.resident_rate,
        "w_star": args.w_star,
        "dynamic": {k: v for k, v in dyn_row.items() if k != "torch_trace_steps"},
        "full": full_row,
        "ratio_raw_pct": None if ratio is None else round(ratio, 2),
        "ratio_w_star_pct": None if ratio_w is None else round(ratio_w, 2),
        "headline_pct": None if headline_pct is None else round(headline_pct, 2),
        "headline_note": headline_note,
        "same_cover": bool(dyn_row.get("enough")) and bool(full_row.get("enough")),
    }

    out_json = parent / "E3_RATIO.json"
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def miB(b: Optional[int]) -> str:
        if b is None:
            return "?"
        return f"{b / 1024 / 1024:.2f}"

    lines = [
        "# E3_RATIO · 同覆盖下总落盘比（头条）",
        "",
        f"> case=`{args.case}` loud · parent=`{parent.name}`",
        f"> 常驻 rate=`{args.resident_rate}`（E2 BOUNDARY）· 设计窗 W\\*=`{args.w_star}`（E1-off；正式 E1 NO_W_STAR 不推翻）",
        "> **主尺**=总落盘字节（全表）动态/全量；**禁止**只报 cold；**禁止**训练 step_ms 并比。",
        "> 判分=采集内容够归因（P3-SW：`cpu.utilization_rss`）；覆盖复用 B Loud D4。",
        "",
        f"## 结论：动态/全量 = **{summary['headline_pct']}%**（{headline_note}）",
        "",
        f"- 同覆盖（采集够归因）：**{'Y' if summary['same_cover'] else 'N'}**",
        f"- raw 总落盘比：`{summary['ratio_raw_pct']}%`",
        f"- W\\*={args.w_star} 截窗估算比：`{summary['ratio_w_star_pct']}%`（无 online retention → `offline_truncate_estimate`）",
        "",
        "## 分臂字节表",
        "",
        "| 臂 | 配置 | total_dump_B | total MiB | cold_B | RSS enough | SET↑ | 备注 |",
        "|----|------|-------------:|----------:|-------:|:----------:|:----:|------|",
    ]

    d_set = (dyn_row.get("set") or {}).get("note", "—")
    d_rss = (dyn_row.get("rss") or {}).get("note", "?")
    lines.append(
        f"| 动态 | rate={args.resident_rate}→SET 1.0 SAMPLE_MS=500 | "
        f"{d_bytes} | {miB(d_bytes)} | {dyn_row.get('cold_bytes') or 0} | "
        f"{'Y' if dyn_row.get('enough') else 'N'} | {d_set} | `{d_rss}` |"
    )
    f_note = "reuse_upper_bound" if full_row.get("reuse") else "fresh"
    lines.append(
        f"| 全量 | rate=1.0 SAMPLE_MS=50 | "
        f"{f_bytes} | {miB(f_bytes)} | {full_row.get('cold_bytes') if full_row.get('cold_bytes') is not None else '—'} | "
        f"{'Y' if full_row.get('enough') else 'N'} | n/a | {f_note} |"
    )
    if dyn_adj is not None:
        lines.append(
            f"| 动态·W\\*估 | 上表 − torch_trace + 截窗估 | "
            f"{dyn_adj} | {miB(dyn_adj)} | — | {'Y' if dyn_row.get('enough') else 'N'} | {d_set} | "
            f"W={args.w_star} {w_est.get('note') if w_est else ''} |"
        )

    lines += [
        "",
        "### 动态臂分表（top）",
        "",
        "| table | bytes | MiB |",
        "|-------|------:|----:|",
    ]
    for k, v in (dyn_row.get("breakdown") or {}).items():
        lines.append(f"| `{k}` | {v} | {miB(v)} |")

    if w_est:
        lines += [
            "",
            "## W\\* 截窗说明",
            "",
            f"- window_mode = `{w_est.get('mode')}`（**非** online「只留最近 W 步」API）",
            f"- victim/汇总 torch_trace：raw=`{w_est.get('raw_tt_bytes')}` → W={args.w_star} est=`{w_est.get('est_tt_bytes_w')}` "
            f"（steps {w_est.get('n_steps_raw')}→{w_est.get('n_steps_w')}；{w_est.get('note')}）",
            f"- 调整后动态总落盘 = `{dyn_adj}`",
        ]

    lines += [
        "",
        "## 设计回哺",
        "",
        f"- 头条数字：**同 D4/同归因下 动态/全量 ≈ {summary['headline_pct']}%**（{headline_note}）。",
        "- 全量臂只作数据量上界；profiling 拖慢训练，**step_ms 不与动态臂并比**。",
        "- P3-SW 主证在周期 `cpu.utilization`；torch_trace W\\* 截窗省的是详采环，不改变本 case 归因尺。",
        "",
        "## 产物",
        "",
        f"- `E3_RATIO.json` · `rate_{args.resident_rate}/` · `full_fidelity/`",
        f"- 本机：`{parent}`",
        "",
    ]

    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"[e3_score] headline={summary['headline_pct']}% ({headline_note}) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
