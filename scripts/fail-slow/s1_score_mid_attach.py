#!/usr/bin/env python3
"""Pillar-C S1：中途接入回溯 —— 时间覆盖证据（非 cold MiB、非训练 step_ms）。

尺：
  - attach 是否成功（marker / 日志）
  - cpu.utilization RSS 时间覆盖：是否覆盖 onset 前 / attach 前 / 仅 attach 后
  - 对照叙述：对手触发后才采或需重启 → 丢 onset 前；Probing 热接入代价=0
  - 环形标定引用 E1-off：20MB≈546 步（只能覆盖「已在采」的历史）
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

RSS_ABS_THR_KB = 700_000
RSS_RISE_THR_KB = 50_000


def find_one(root: Path, name: str) -> Optional[Path]:
    hits = list(root.rglob(name))
    return hits[0] if hits else None


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


def cold_hot_bytes(pdata: Path) -> dict[str, int]:
    hot_b = cold_b = 0
    cold_segs = hot_files = 0
    if not pdata.is_dir():
        return {"hot_bytes": 0, "cold_bytes": 0, "cold_segs": 0, "hot_files": 0}
    for f in pdata.rglob("*"):
        if not f.is_file():
            continue
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        if f.suffix == ".memc" or f.name.endswith(".memc"):
            cold_b += sz
            cold_segs += 1
        else:
            hot_b += sz
            hot_files += 1
    return {
        "hot_bytes": hot_b,
        "cold_bytes": cold_b,
        "cold_segs": cold_segs,
        "hot_files": hot_files,
    }


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


def rss_ts_to_sec(ts: int) -> float:
    # probing cpu.utilization.ts ≈ 微秒 epoch
    if ts > 10_000_000_000_000:  # > ~year 2286 in ms → µs
        return ts / 1e6
    if ts > 10_000_000_000:  # ms
        return ts / 1e3
    return float(ts)


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


def load_jsonl_steps(path: Path) -> dict[int, float]:
    out: dict[int, float] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "step" in rec and "ts" in rec:
            out[int(rec["step"])] = float(rec["ts"])
    return out


def parse_attach_marker(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {"ok": False, "step": None, "ts": None, "note": "marker_absent"}
    text = path.read_text(encoding="utf-8", errors="replace")
    step = ts = None
    m = re.search(r"step=(\d+)", text)
    if m:
        step = int(m.group(1))
    m = re.search(r"ts=([0-9.]+)", text)
    if m:
        ts = float(m.group(1))
    return {"ok": True, "step": step, "ts": ts, "note": "marker_ok", "raw": text.strip()}


def set_status(path: Optional[Path]) -> dict[str, Any]:
    if not path or not path.is_file():
        return {"ok": False, "note": "set_upgrade.log_absent"}
    text = path.read_text(encoding="utf-8", errors="replace")
    if "SET_OK_WORKER" in text:
        return {"ok": True, "note": "SET_OK"}
    if "SET_FAIL_ALL" in text:
        return {"ok": False, "note": "SET_FAIL_ALL"}
    return {"ok": False, "note": "set_log_no_ok"}


def miB(b: int) -> float:
    return round(b / (1024 * 1024), 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parent-local", required=True)
    ap.add_argument("--case", default="P3-SW-A")
    ap.add_argument("--attach-at", type=int, default=150)
    ap.add_argument("--inject-start", type=int, default=100)
    ap.add_argument("--inject-stop", type=int, default=300)
    ap.add_argument("--ring-steps", type=int, default=546)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    parent = Path(args.parent_local)
    arm = parent / "mid_attach"
    out_md = Path(args.out)

    attach_marker = find_one(arm, "probing_mid_attach.marker")
    attach_fail = find_one(arm, "probing_mid_attach.fail")
    attach_info = parse_attach_marker(attach_marker)
    if not attach_info["ok"] and attach_fail and attach_fail.is_file():
        attach_info = {
            "ok": False,
            "step": None,
            "ts": None,
            "note": "attach_fail_marker",
            "raw": attach_fail.read_text(encoding="utf-8", errors="replace")[:500],
        }
    # 日志兜底
    if not attach_info["ok"]:
        log = parent / "logs" / "arm_mid_attach.log"
        node = find_one(arm, "node_0.log")
        for cand in (log, node):
            if cand and cand.is_file():
                txt = cand.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"PROBING_MID_ATTACH_OK step=(\d+) ts=([0-9.]+)", txt)
                if m:
                    attach_info = {
                        "ok": True,
                        "step": int(m.group(1)),
                        "ts": float(m.group(2)),
                        "note": f"log_ok:{cand.name}",
                    }
                    break
                if "PROBING_MID_ATTACH_FAIL" in txt:
                    attach_info = {
                        "ok": False,
                        "step": None,
                        "ts": None,
                        "note": f"log_fail:{cand.name}",
                    }

    rank0 = find_one(arm, "rank_0000.jsonl")
    steps = load_jsonl_steps(rank0) if rank0 else {}
    onset_ts = steps.get(args.inject_start)
    attach_step = attach_info.get("step") if attach_info.get("step") is not None else args.attach_at
    attach_ts = attach_info.get("ts")
    if attach_ts is None and attach_step is not None:
        attach_ts = steps.get(int(attach_step))

    rss_path = find_one(arm, "query_p3sw_rss_window.txt")
    if rss_path is None:
        for cand in arm.rglob("query_*.txt"):
            if "rss" in cand.name.lower() or "cpu" in cand.name.lower():
                rss_path = cand
                break
    series = parse_rss_query_txt(rss_path) if rss_path else []
    rss = judge_rss(series)

    # 时间覆盖
    rss_secs = [rss_ts_to_sec(ts) for ts, _ in series]
    pre_onset_n = 0
    pre_attach_n = 0
    post_attach_n = 0
    if rss_secs and onset_ts is not None:
        pre_onset_n = sum(1 for t in rss_secs if t < float(onset_ts) - 0.05)
    if rss_secs and attach_ts is not None:
        pre_attach_n = sum(1 for t in rss_secs if t < float(attach_ts) - 0.05)
        post_attach_n = sum(1 for t in rss_secs if t >= float(attach_ts) - 0.05)
    elif rss_secs:
        post_attach_n = len(rss_secs)

    span_s = (max(rss_secs) - min(rss_secs)) if len(rss_secs) >= 2 else 0.0
    # 用 jsonl 估 attach 后覆盖了多少训练步
    steps_covered_post = None
    if attach_ts is not None and steps:
        post_steps = [s for s, t in steps.items() if t >= float(attach_ts) - 0.05]
        if post_steps:
            steps_covered_post = max(post_steps) - min(post_steps) + 1

    pre_onset_ok = pre_onset_n > 0
    # S1 主判：晚 attach 后仍见 onset 前？期望按 OUTLINE 为 Y，物理上冷启动应为 N
    lookback_claim = pre_onset_ok
    attach_after_onset = (
        attach_step is not None and int(attach_step) > int(args.inject_start)
    )

    pdata = None
    for cand in arm.rglob("probing_data"):
        if cand.is_dir():
            pdata = cand
            break
    vol = cold_hot_bytes(pdata) if pdata else {
        "hot_bytes": 0,
        "cold_bytes": 0,
        "cold_segs": 0,
        "hot_files": 0,
    }
    total_dump = dir_bytes(pdata) if pdata else 0
    # 选择性回拉时可能只有 volume_meta.txt
    vmeta = find_one(arm, "volume_meta.txt") or find_one(parent, "volume.txt")
    if (total_dump == 0) and vmeta and vmeta.is_file():
        text = vmeta.read_text(encoding="utf-8", errors="replace")
        m = re.search(
            r"hot_files=(\d+)\s+hot_bytes=(\d+)\s+cold_segs=(\d+)\s+cold_bytes=(\d+)\s+total=(\d+)",
            text,
        )
        if m:
            vol = {
                "hot_files": int(m.group(1)),
                "hot_bytes": int(m.group(2)),
                "cold_segs": int(m.group(3)),
                "cold_bytes": int(m.group(4)),
            }
            total_dump = int(m.group(5))
            vol["source"] = str(vmeta)

    set_log = find_one(arm, "set_upgrade.log")
    sinfo = set_status(set_log)

    # 对照半定量：对手若必须重启重跑到 attach 点，代价≈ attach_at 步（或 onset→attach）
    opponent_restart_steps = int(attach_step) if attach_step is not None else args.attach_at
    probing_attach_restart_steps = 0  # 热接入

    summary = {
        "experiment": "S1",
        "case": args.case,
        "attach_at_requested": args.attach_at,
        "inject_window": [args.inject_start, args.inject_stop],
        "ring_calibrated_steps_e1off": args.ring_steps,
        "attach": attach_info,
        "attach_after_onset": attach_after_onset,
        "onset_ts_s": onset_ts,
        "attach_ts_s": attach_ts,
        "rss": rss,
        "rss_path": str(rss_path) if rss_path else None,
        "time_cover": {
            "n_samples": len(series),
            "span_s": round(span_s, 3),
            "pre_onset_n": pre_onset_n,
            "pre_attach_n": pre_attach_n,
            "post_attach_n": post_attach_n,
            "pre_onset_visible": pre_onset_ok,
            "steps_covered_post_attach": steps_covered_post,
        },
        "lookback_pre_onset": lookback_claim,
        "set_upgrade": sinfo,
        "volume": {**vol, "total_dump_bytes": total_dump},
        "contrast": {
            "probing_restart_gpu_steps": probing_attach_restart_steps,
            "opponent_restart_gpu_steps_est": opponent_restart_steps,
            "note": (
                "对手若只触发后采/需重启才能挂采集，则丢 onset 前基线；"
                "Probing 热接入 restart=0 可开始采。"
                "环形 20MB≈546 步只保留「接入后已写入」的历史，不能发明 attach 前未采样本。"
            ),
        },
        "verdict": (
            "PASS_LOOKBACK"
            if lookback_claim and attach_info.get("ok")
            else (
                "PASS_ATTACH_NO_PRE_ONSET"
                if attach_info.get("ok") and rss.get("ok") and not lookback_claim
                else (
                    "ATTACH_OK_RSS_WEAK"
                    if attach_info.get("ok")
                    else "FAIL_ATTACH"
                )
            )
        ),
    }

    (parent / "S1_MID_ATTACH.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# S1_MID_ATTACH · 中途接入回溯（outline 场景一）",
        "",
        f"> case=`{args.case}` loud · parent=`{parent.name}`",
        f"> 注入窗=[{args.inject_start},{args.inject_stop}] · attach_at={args.attach_at} "
        f"（onset 后）· 环标定 20MB≈{args.ring_steps} 步（E1-off）",
        "> **尺**=RSS/冷段**时间覆盖**；禁止只报 cold MiB；禁止训练 step_ms 假同 D。",
        "> 接入实现：Ascend hold **无 libprobing.so** → 用 `PROBING_ATTACH_AT_STEP` "
        "延迟 `site_hook`（中途 import）；非 CLI ptrace。",
        "",
        f"## 结论：{summary['verdict']}",
        "",
        f"- 中途接入成功：**{'Y' if attach_info.get('ok') else 'N'}**"
        f"（{attach_info.get('note')}；step={attach_step}）",
        f"- attach 在 onset 之后：**{'Y' if attach_after_onset else 'N'}**",
        f"- RSS 主证（抬升/高位）：**{'Y' if rss.get('ok') else 'N'}**（`{rss.get('note')}`）",
        f"- **onset 前 RSS 可见**：**{'Y' if pre_onset_ok else 'N'}**"
        f"（pre_onset_n={pre_onset_n} / n={len(series)}）",
        f"- attach 前样本数={pre_attach_n} · attach 后={post_attach_n} · 窗跨度≈{span_s:.1f}s",
        f"- SET↑：{sinfo.get('note')}",
        "",
        "## 时间覆盖（主证据）",
        "",
        "| 锚点 | wall_ts (s) | 说明 |",
        "|------|------------:|------|",
        f"| inject onset (step {args.inject_start}) | {onset_ts if onset_ts is not None else '—'} | jsonl |",
        f"| mid-attach (step {attach_step}) | {attach_ts if attach_ts is not None else '—'} | marker/log |",
        f"| RSS 最早 | {min(rss_secs) if rss_secs else '—'} | cpu.utilization |",
        f"| RSS 最晚 | {max(rss_secs) if rss_secs else '—'} | cpu.utilization |",
        "",
        "## 对照：回溯窗 vs 对手重启代价（半定量，喂 Eval-A）",
        "",
        "| 工具 | 中途接入 | onset 前证据 | 代价（估） |",
        "|------|----------|:------------:|------------|",
        f"| **Probing（本跑）** | 热接入（延迟 site_hook）"
        f" | {'有' if pre_onset_ok else '无（冷启动无史）'} "
        f"| restart GPU-steps = **{probing_attach_restart_steps}** |",
        f"| 对手（触发后才采 / 需重启挂采集） | 需重启重跑到现场 "
        f"| 丢（未挂则零数据） "
        f"| restart GPU-steps ≈ **{opponent_restart_steps}**（跑到 attach 点） |",
        "",
        f"- 环形容量标定（E1-off）：20MB ≈ **{args.ring_steps}** 步 — "
        "只约束「已写入 ring/cold 的保留长度」，**不能**回填 attach 前未采集时段。",
        "- 若要验证「接入后仍见 onset 前」，需 **attach ≤ onset** 或训起即常驻采集；"
        "本格按大纲选 **attach>onset**，用于标定冷启动晚接入的时间边界。",
        "",
        "## 落盘（辅尺，非主结论）",
        "",
        f"- total_dump ≈ {total_dump} B（{miB(total_dump)} MiB）",
        f"- cold ≈ {vol.get('cold_bytes', 0)} B（{miB(int(vol.get('cold_bytes') or 0))} MiB；"
        f"segs={vol.get('cold_segs', 0)}）— **不作主结论**",
        "",
        "## 解读",
        "",
    ]
    if summary["verdict"] == "PASS_LOOKBACK":
        lines += [
            "- 晚接入后仍见 onset 前 RSS → 支持 outline「中途接入回溯」强表述（需核对是否误采）。",
        ]
    elif summary["verdict"] == "PASS_ATTACH_NO_PRE_ONSET":
        lines += [
            "- **热接入成功**，周期小表在 attach 后采到 RSS 主证；"
            "**未见 onset 前样本** —— 与「环/冷只能保留已采集历史」一致。",
            "- 喂 Eval-A：中途接入代价=0（相对对手重启 ≈"
            f"{opponent_restart_steps} 步）；但 **onset 前基线** 要求接入不晚于 onset 或训起常驻。",
            "- OUTLINE「attach@300 仍查 150–300 冷段」在冷启动语义下不成立；"
            "可成立的表述改为：常驻极稀采集 + 环形保留窗（E1 W*/546 步）+ 热 SET 升详。",
        ]
    elif summary["verdict"] == "ATTACH_OK_RSS_WEAK":
        lines += [
            "- 接入成功但 RSS 主证弱/缺 — 检查 SAMPLE_MS、dump 窗、victim rank。",
        ]
    else:
        lines += [
            "- 中途接入失败 — 查 `probing_mid_attach.fail` / node_0.log；"
            "确认 `PROBING_ATTACH_AT_STEP` 已同步进 pod 训练脚本。",
        ]

    lines += [
        "",
        "## 产物",
        "",
        f"- `S1_MID_ATTACH.json` · `mid_attach/`",
        f"- 本机：`{parent}`",
        f"- AFS：`/afs-a3-weight-share/yinjinrun.p-huawei/results/ascend-ais/pillar_c_v2/{parent.name}/`",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[s1-score] verdict={summary['verdict']} → {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
