#!/usr/bin/env python3
"""解析 Megatron counterbalanced attempts → SUMMARY.md / metrics.json（n=2 preliminary）。"""

from __future__ import annotations

from strict_validate import StrictValidationError, validate_attempt_manifest

import argparse
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Any


# Frozen REPLAN: one-shot legacy proxy for sealed GROUP missing collector process_wall_ms.
LEGACY_PROXY_GROUP_IDS = frozenset({"20260809_192117-megatron-ab-formal6x20"})
ANALYZER_DERIVATION_VERSION = "20260810_steady_exclude_10_11"
STEADY_WINDOW = (5, 20)
VOLUME_RATIO_MIN = 10.0
STEADY_SLOWDOWN_MEDIAN_MAX_PCT = 5.0
STEADY_SLOWDOWN_SINGLE_MAX_PCT = 8.0
E2E_SLOWDOWN_MEDIAN_MAX_PCT = 10.0

ITER_RE = re.compile(
    r"iteration\s+(\d+)/\s*\d+.*?elapsed time per iteration \(ms\):\s*([0-9.]+)",
    re.IGNORECASE,
)
THROUGHPUT_RE = re.compile(
    r"throughput per GPU \(TFLOP/s/GPU\):\s*([0-9.]+)",
    re.IGNORECASE,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analyze Megatron AB group (strict requires local anchors)."
    )
    p.add_argument(
        "--strict",
        metavar="GROUP_DIR",
        help="Strict post-hoc on GROUP_DIR: traverse six attempts, require local anchors",
    )
    p.add_argument(
        "group_dir",
        nargs="?",
        default=None,
        help="Group directory (optional if --strict GROUP_DIR given)",
    )
    p.add_argument(
        "--capture-iter",
        type=int,
        default=None,
        help="Override capture megatron iter (default: from group_config)",
    )
    p.add_argument(
        "--allow-legacy-process-wall-proxy",
        action="store_true",
        help=(
            "Audited one-shot: allow manifest e2e proxy for process_wall_ms only on "
            "frozen legacy GROUP allowlist (192117)"
        ),
    )
    return p.parse_args(argv)


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def discover_attempts(group_dir: Path) -> list[Path]:
    attempts = sorted(
        p for p in group_dir.iterdir() if p.is_dir() and p.name.startswith("attempt_")
    )
    return attempts


def analyze_group(
    group_dir: Path,
    *,
    strict: bool,
    capture_iter: int | None = None,
    allow_legacy_process_wall_proxy: bool = False,
) -> dict[str, Any]:
    group_dir = Path(group_dir)
    group_id = _group_id_from_dir(group_dir)
    if not group_dir.is_dir():
        raise FileNotFoundError(f"group dir does not exist: {group_dir}")

    cfg_path = group_dir / "group_config.json"
    cfg: dict[str, Any] = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    model = dict(cfg.get("model") or {})
    # Fill model defaults from top-level group config keys used by launchers.
    for key, default in (
        ("gbs", 64),
        ("seq", 4096),
        ("world_size", 32),
        ("tp", 2),
        ("pp", 1),
        ("seed", 1234),
    ):
        if key not in model and key in cfg:
            model[key] = cfg[key]
        model.setdefault(key, default)

    cap = capture_iter
    if cap is None:
        cap = int(cfg.get("capture_megatron_iter") or cfg.get("capture_iter") or 10)

    attempts = discover_attempts(group_dir)
    expected_order = cfg.get("attempts_order") or cfg.get("attempts")
    if strict:
        if not attempts:
            raise RuntimeError(f"{group_dir}: no attempt_* dirs under --strict")
        plan_path = group_dir / "group_plan.json"
        design = cfg.get("design_sequence")
        if plan_path.exists():
            from ab_plan import (
                DEFAULT_DESIGN_SEQUENCE,
                load_plan,
                validate_attempts_sequence,
            )

            plan = load_plan(plan_path)
            expected_order = list(plan.get("attempts_order") or [])
            design = plan.get("design_sequence") or design or DEFAULT_DESIGN_SEQUENCE
            validate_attempts_sequence(expected_order, design_sequence=design)
            listed_hash = plan.get("plan_hash")
            cfg_hash = cfg.get("plan_hash")
            if not listed_hash:
                raise RuntimeError(f"{group_dir}: group_plan.json missing plan_hash")
            if cfg_hash and cfg_hash != listed_hash:
                raise RuntimeError(
                    f"{group_dir}: group_config plan_hash != group_plan.json"
                )
            group_plan_hash = str(listed_hash)
        else:
            from ab_plan import (
                DEFAULT_ATTEMPTS,
                DEFAULT_DESIGN_SEQUENCE,
                validate_attempts_sequence,
            )

            if expected_order is None:
                expected_order = list(DEFAULT_ATTEMPTS)
            design = design or DEFAULT_DESIGN_SEQUENCE
            validate_attempts_sequence(list(expected_order), design_sequence=design)
            group_plan_hash = str(cfg.get("plan_hash") or "")

        if expected_order is not None:
            if len(attempts) != len(expected_order):
                raise RuntimeError(
                    f"{group_dir}: attempt count {len(attempts)} != "
                    f"attempts_order {len(expected_order)}"
                )
            for idx, (attempt_dir, arm) in enumerate(zip(attempts, expected_order), 1):
                expected_id = f"attempt_{idx:02d}_{arm}"
                if attempt_dir.name != expected_id:
                    raise RuntimeError(
                        f"{group_dir}: attempt dir {attempt_dir.name} != expected {expected_id}"
                    )
                man = attempt_dir / "attempt_manifest.json"
                if man.exists():
                    mo = json.loads(man.read_text(encoding="utf-8"))
                    if str(mo.get("arm")) != str(arm):
                        raise RuntimeError(
                            f"{attempt_dir.name}: manifest arm {mo.get('arm')} != plan {arm}"
                        )
                    if int(mo.get("order_index", idx)) != idx:
                        raise RuntimeError(
                            f"{attempt_dir.name}: order_index not contiguous"
                        )
                    # Attempt must bind immutable group plan_hash (strict).
                    if group_plan_hash:
                        ah = mo.get("plan_hash")
                        if not ah:
                            raise RuntimeError(
                                f"{attempt_dir.name}: missing plan_hash "
                                f"(must equal group_plan {group_plan_hash[:16]}…)"
                            )
                        if str(ah) != group_plan_hash:
                            raise RuntimeError(
                                f"{attempt_dir.name}: plan_hash {ah} != "
                                f"group_plan {group_plan_hash}"
                            )

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for attempt_dir in attempts:
        try:
            results.append(
                analyze_attempt(
                    attempt_dir,
                    int(cap),
                    model,
                    strict,
                    group_id=group_id,
                    allow_legacy_process_wall_proxy=allow_legacy_process_wall_proxy,
                )
            )
        except Exception as exc:  # noqa: BLE001 — surface per-attempt then fail group
            errors.append(f"{attempt_dir.name}: {exc}")

    if errors:
        raise RuntimeError(
            "group analyze failures:\n" + "\n".join(errors)
        )

    by_arm: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_arm.setdefault(str(r.get("arm")), []).append(r)

    def _arm_mean(arm: str, key: str) -> float | None:
        vals = [
            float(x[key])
            for x in by_arm.get(arm, [])
            if x.get(key) is not None
        ]
        return (sum(vals) / len(vals)) if vals else None

    summary = {
        "group_dir": str(group_dir),
        "group_id": group_id,
        "strict": strict,
        "capture_megatron_iter": int(cap),
        "steady_exclude_iters": sorted(_steady_exclude_iters(int(cap))),
        "steady_window_iters": list(STEADY_WINDOW),
        "analyzer_derivation_version": ANALYZER_DERIVATION_VERSION,
        "allow_legacy_process_wall_proxy": allow_legacy_process_wall_proxy,
        "legacy_proxy_allowlist": sorted(LEGACY_PROXY_GROUP_IDS),
        "attempt_count": len(results),
        "attempts": results,
        "by_arm": {
            arm: {
                "n": len(items),
                "e2e_wall_ms_mean": _arm_mean(arm, "e2e_wall_ms"),
            }
            for arm, items in by_arm.items()
        },
        "qualifying_gates": compute_qualifying_gates(results),
        "status": "PASS" if strict else "OK",
    }

    lines = [
        f"# Megatron AB SUMMARY — {group_dir.name}",
        "",
        f"- strict: {strict}",
        f"- analyzer_derivation_version: {ANALYZER_DERIVATION_VERSION}",
        f"- steady_exclude_iters: {summary['steady_exclude_iters']}",
        f"- attempts: {len(results)}",
        f"- capture_megatron_iter: {cap}",
        "",
        "| attempt | arm | e2e_wall_ms |",
        "|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.get('attempt_id', r.get('name', '?'))} | {r.get('arm')} | {r.get('e2e_wall_ms')} |"
        )
    lines.append("")
    lines.append("## by_arm")
    for arm, info in sorted(summary["by_arm"].items()):
        lines.append(f"- {arm}: n={info['n']} e2e_mean={info['e2e_wall_ms_mean']}")
    lines.append("")

    _atomic_write_json(group_dir / "metrics.json", summary)
    _atomic_write_text(group_dir / "SUMMARY.md", "\n".join(lines) + "\n")
    return summary


def derived_samples_per_s(gbs: float, elapsed_ms: float) -> float:
    """全局 samples/s = GBS / (elapsed_s) = GBS * 1000 / elapsed_ms。"""
    return gbs * 1000.0 / elapsed_ms


def derived_tokens_per_gpu_s(gbs: float, seq: float, world: float, elapsed_ms: float) -> float:
    """tokens/s/GPU = (GBS * SEQ / world_size) / elapsed_s。"""
    return ((gbs * seq) / world) / (elapsed_ms / 1000.0)


def pct(delta: float | None, base: float | None) -> float | None:
    if delta is None or base is None or base == 0:
        return None
    return 100.0 * (delta - base) / base


def pctl(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            # 跳过 preliminary 隔离目录名（不应出现在正式 attempt 内）
            if "ours_broken" in str(p) or "PRELIMINARY" in str(p):
                continue
            total += p.stat().st_size
    return total


def _manifest_max_e2e_wall_ms(manifest: dict[str, Any]) -> float | None:
    """Max node monotonic e2e from attempt manifest (same口径 as launchers)."""
    e2e = manifest.get("e2e_wall_ms")
    if e2e is not None:
        return float(e2e)
    node_e2e = [
        float(v.get("e2e_wall_ms"))
        for v in (manifest.get("node_launch") or {}).values()
        if isinstance(v, dict) and v.get("e2e_wall_ms") is not None
    ]
    return max(node_e2e) if node_e2e else None


def _group_id_from_dir(group_dir: Path) -> str:
    return group_dir.name


def _steady_exclude_iters(capture_iter: int) -> set[int]:
    """Pre-registered steady window excludes capture step and the following iter."""
    return {int(capture_iter), int(capture_iter) + 1}


def _resolve_process_wall_ms(
    meta: dict[str, Any],
    manifest: dict[str, Any],
    *,
    group_id: str,
    allow_legacy_process_wall_proxy: bool,
) -> tuple[float | None, str | None, dict[str, Any] | None]:
    """Resolve collector process wall for tail_tax.

  Default strict: missing ``meta.process_wall_ms`` fails closed.  Legacy manifest e2e
  proxy is allowed only for frozen allowlist GROUPs with an explicit audited CLI flag.
    """
    raw = meta.get("process_wall_ms")
    if raw is not None:
        val = float(raw)
        if math.isfinite(val) and val >= 0:
            return val, "meta.process_wall_ms", None
    if not allow_legacy_process_wall_proxy:
        return None, None, None
    if group_id not in LEGACY_PROXY_GROUP_IDS:
        return None, None, {
            "legacy_proxy_denied": True,
            "reason": "group_not_on_legacy_proxy_allowlist",
            "group_id": group_id,
        }
    proxy = _manifest_max_e2e_wall_ms(manifest)
    if proxy is not None and proxy > 0:
        return (
            proxy,
            "manifest_e2e_wall_ms_proxy",
            {
                "legacy_proxy_exception": True,
                "group_id": group_id,
                "note": "not_collector_measured; manifest e2e upper-bound proxy only",
            },
        )
    return None, None, {
        "legacy_proxy_denied": True,
        "reason": "manifest_e2e_wall_ms_missing",
        "group_id": group_id,
    }


def _attempt_by_name(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("attempt_id") or r.get("name")): r for r in results}


def compute_qualifying_gates(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Pre-registered volume/steady/e2e gates from attempt-level metrics."""
    by_name = _attempt_by_name(results)
    volume_pairs: list[dict[str, Any]] = []
    for torch_id, ours_id in (("attempt_03_torch", "attempt_02_ours"), ("attempt_04_torch", "attempt_05_ours")):
        torch_a = by_name.get(torch_id) or {}
        ours_a = by_name.get(ours_id) or {}
        torch_bytes = int((torch_a.get("trace") or {}).get("profiler_dir_bytes_sum") or torch_a.get("raw_bytes") or 0)
        ours_bytes = int(ours_a.get("raw_bytes") or 0)
        ratio = (torch_bytes / ours_bytes) if ours_bytes > 0 else None
        volume_pairs.append(
            {
                "torch_attempt": torch_id,
                "ours_attempt": ours_id,
                "torch_bytes": torch_bytes,
                "ours_bytes": ours_bytes,
                "ratio": ratio,
                "pass": ratio is not None and ratio >= VOLUME_RATIO_MIN,
            }
        )

    normal_steady = [
        float(x["steady_p50_ms"])
        for x in results
        if x.get("arm") == "normal" and x.get("steady_p50_ms") is not None
    ]
    ours_steady = [
        float(x["steady_p50_ms"])
        for x in results
        if x.get("arm") == "ours" and x.get("steady_p50_ms") is not None
    ]
    normal_e2e = [
        float(x["e2e_wall_ms"])
        for x in results
        if x.get("arm") == "normal" and x.get("e2e_wall_ms") is not None
    ]
    ours_e2e = [
        float(x["e2e_wall_ms"])
        for x in results
        if x.get("arm") == "ours" and x.get("e2e_wall_ms") is not None
    ]
    normal_steady_med = statistics.median(normal_steady) if normal_steady else None
    ours_steady_med = statistics.median(ours_steady) if ours_steady else None
    normal_e2e_med = statistics.median(normal_e2e) if normal_e2e else None
    ours_e2e_med = statistics.median(ours_e2e) if ours_e2e else None

    steady_slowdowns = [
        pct(ours_val, normal_steady_med)
        for ours_val in ours_steady
        if normal_steady_med is not None
    ]
    steady_slowdowns = [v for v in steady_slowdowns if v is not None]
    e2e_slowdowns = [
        pct(ours_val, normal_e2e_med) for ours_val in ours_e2e if normal_e2e_med is not None
    ]
    e2e_slowdowns = [v for v in e2e_slowdowns if v is not None]
    steady_med = statistics.median(steady_slowdowns) if steady_slowdowns else None
    steady_max = max(steady_slowdowns) if steady_slowdowns else None
    e2e_med = statistics.median(e2e_slowdowns) if e2e_slowdowns else None

    volume_pass = all(p["pass"] for p in volume_pairs) if volume_pairs else False
    steady_pass = (
        steady_med is not None
        and steady_max is not None
        and steady_med <= STEADY_SLOWDOWN_MEDIAN_MAX_PCT
        and steady_max <= STEADY_SLOWDOWN_SINGLE_MAX_PCT
    )
    e2e_pass = e2e_med is not None and e2e_med <= E2E_SLOWDOWN_MEDIAN_MAX_PCT

    return {
        "volume_pairs": volume_pairs,
        "volume_pass": volume_pass,
        "steady_slowdown_pct_median": steady_med,
        "steady_slowdown_pct_max_single": steady_max,
        "steady_pass": steady_pass,
        "e2e_slowdown_pct_median": e2e_med,
        "e2e_pass": e2e_pass,
        "gate_512_pass": volume_pass and steady_pass and e2e_pass,
        "thresholds": {
            "volume_ratio_min": VOLUME_RATIO_MIN,
            "steady_slowdown_median_max_pct": STEADY_SLOWDOWN_MEDIAN_MAX_PCT,
            "steady_slowdown_single_max_pct": STEADY_SLOWDOWN_SINGLE_MAX_PCT,
            "e2e_slowdown_median_max_pct": E2E_SLOWDOWN_MEDIAN_MAX_PCT,
        },
        "reference_normal_steady_p50_ms": normal_steady_med,
        "reference_normal_e2e_ms_median": normal_e2e_med,
    }


def parse_megatron_log(path: Path) -> dict[str, Any]:
    text = path.read_text(errors="replace") if path.exists() else ""
    steps: dict[int, float] = {}
    tflops: dict[int, float] = {}
    for line in text.splitlines():
        m = ITER_RE.search(line)
        if not m:
            continue
        it = int(m.group(1))
        steps[it] = float(m.group(2))
        tm = THROUGHPUT_RE.search(line)
        if tm:
            tflops[it] = float(tm.group(1))
    return {
        "log": str(path),
        "steps_ms": steps,
        "tflops": tflops,
        "has_loss_line": "lm loss" in text.lower(),
        "is_megatron": "pretrain_gpt.py" in text or bool(steps),
    }


def analyze_attempt(
    attempt_dir: Path,
    capture_iter: int,
    model: dict[str, Any],
    strict: bool,
    *,
    group_id: str = "",
    allow_legacy_process_wall_proxy: bool = False,
) -> dict[str, Any]:
    manifest_path = attempt_dir / "attempt_manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    arm = manifest.get("arm") or attempt_dir.name.rsplit("_", 1)[-1]
    candidates = sorted(attempt_dir.glob("node_*.log"), key=lambda p: p.stat().st_size, reverse=True)
    if not candidates:
        candidates = [attempt_dir / "node_0.log"]
    parsed = {"steps_ms": {}, "tflops": {}, "has_loss_line": False, "log": ""}
    for log in candidates:
        if not log.is_file():
            continue
        cur = parse_megatron_log(log)
        if len(cur["steps_ms"]) > len(parsed["steps_ms"]):
            parsed = cur
    steps = parsed["steps_ms"]
    ordered = sorted(steps)
    gbs = float(model.get("gbs", 64))
    seq = float(model.get("seq", 4096))
    world = float(model.get("world_size", 32))
    # Pre-registered steady window: iterations 5–20 excluding capture and capture+1.
    exclude = _steady_exclude_iters(capture_iter)
    wall_ms = sum(steps[i] for i in ordered) if ordered else None
    steady_ids = [i for i in ordered if 5 <= i <= 20 and i not in exclude]
    steady_all_ids = [i for i in ordered if 5 <= i <= 20]
    steady = [steps[i] for i in steady_ids]
    steady_all = [steps[i] for i in steady_all_ids]
    near = [steps[i] for i in ordered if i not in exclude and abs(i - capture_iter) <= 2]
    near_med = statistics.median(near) if near else None
    capture_ms = steps.get(capture_iter)
    samples = {i: derived_samples_per_s(gbs, ms) for i, ms in steps.items() if ms > 0}
    tokens = {
        i: derived_tokens_per_gpu_s(gbs, seq, world, ms) for i, ms in steps.items() if ms > 0
    }

    if strict:
        train_iters = int(manifest.get("train_iters", 20))
        if len(steps) != train_iters:
            raise RuntimeError(
                f"{attempt_dir.name}: expected {train_iters} iters, got {len(steps)}"
            )
        if not parsed["has_loss_line"]:
            raise RuntimeError(f"{attempt_dir.name}: missing lm loss evidence")
        # 交叉核 manifest vs 日志配置痕迹
        m_model = manifest.get("model") or {}
        for key in ("gbs", "seq", "seed", "tp", "pp"):
            if key in m_model and key in model and m_model[key] != model[key]:
                raise RuntimeError(
                    f"{attempt_dir.name}: model.{key} mismatch manifest={m_model[key]} group={model[key]}"
                )

    out: dict[str, Any] = {
        "attempt_id": attempt_dir.name,
        "arm": arm,
        "manifest": {
            "path": str(manifest_path) if manifest_path.exists() else None,
            "started_at_utc": manifest.get("started_at_utc"),
            "ended_at_utc": manifest.get("ended_at_utc"),
            "master_port": manifest.get("master_port"),
            "exit_code": manifest.get("exit_code"),
            "run_marker": manifest.get("run_marker"),
        },
        "megatron_log_evidence": {
            "n_steps_logged": len(steps),
            "step_ids": ordered,
            "has_loss_or_lm_loss": parsed["has_loss_line"],
            "log_path": parsed.get("log"),
        },
        "steps_ms": steps,
        "train_wall_ms_sum_logged_iters": wall_ms,
        "steady_exclude_iters": sorted(exclude),
        "steady_step_ids": steady_ids,
        "steady_p50_ms": statistics.median(steady) if steady else None,
        "steady_p95_ms": pctl(steady, 0.95),
        "steady_all_p50_ms": statistics.median(steady_all) if steady_all else None,
        "steady_all_p95_ms": pctl(steady_all, 0.95),
        "capture_iter": capture_iter,
        "capture_step_ms": capture_ms,
        "capture_vs_near_median_ms": (
            None if capture_ms is None or near_med is None else capture_ms - near_med
        ),
        "note": "CaptureEnd does not Flush; finalize_* from mspti meta; no fake flush_slot",
        "tflops_p50": statistics.median(list(parsed["tflops"].values()))
        if parsed["tflops"]
        else None,
        "samples_per_s_p50": statistics.median(list(samples.values())) if samples else None,
        "tokens_per_gpu_s_p50": statistics.median(list(tokens.values())) if tokens else None,
        "throughput_source": "derived_gbs_seq",
        "throughput_formula": {
            "samples_per_s": "GBS * 1000 / elapsed_ms",
            "tokens_per_gpu_s": "(GBS * SEQ / world_size) / (elapsed_ms/1000)",
            "gbs": gbs,
            "seq": seq,
            "world_size": world,
        },
        "raw_bytes": dir_size(attempt_dir),
    }

    if arm == "ours":
        counters_path = attempt_dir / "counters.json"
        if not counters_path.exists():
            if strict:
                raise RuntimeError(f"{attempt_dir.name}: missing counters.json")
            counters = {}
        else:
            counters = json.loads(counters_path.read_text(encoding="utf-8"))
        if strict and not counters.get("pass", False):
            raise RuntimeError(f"{attempt_dir.name}: counters.pass is false")
        cluster = attempt_dir / "cluster.trace.json"
        skeletons = sorted(attempt_dir.glob("rank_*.skeleton.jsonl"))
        # 拒绝误扫 broken late gate
        skeletons = [p for p in skeletons if "broken" not in str(p)]
        out["trace"] = {
            "cluster_trace_bytes": cluster.stat().st_size if cluster.exists() else 0,
            "cluster_trace_mib": (cluster.stat().st_size / (1024 * 1024)) if cluster.exists() else 0,
            "skeleton_jsonl_bytes_sum": sum(p.stat().st_size for p in skeletons),
            "skeleton_jsonl_mib": sum(p.stat().st_size for p in skeletons) / (1024 * 1024),
            "skeleton_rank_count": len(skeletons),
            "rank0_skeleton_bytes": (
                (attempt_dir / "rank_0000.skeleton.jsonl").stat().st_size
                if (attempt_dir / "rank_0000.skeleton.jsonl").exists()
                else 0
            ),
            "rank0_trace_bytes": (
                (attempt_dir / "rank_0000.trace.json").stat().st_size
                if (attempt_dir / "rank_0000.trace.json").exists()
                else 0
            ),
            "event_counts": counters.get("event_counts"),
            "drop_count": counters.get("drop_count"),
            "pass": counters.get("pass"),
        }
        meta = attempt_dir / "rank_0000.npu_sync_meta.json"
        if not meta.exists():
            meta = attempt_dir / "rank_0000.mspti_meta.json"
        if meta.exists():
            m = json.loads(meta.read_text(encoding="utf-8"))
            out["npu_sync_meta_rank0"] = m
            out["mspti_meta_rank0"] = m
            proc_wall, proc_src, proxy_disclosure = _resolve_process_wall_ms(
                m,
                manifest,
                group_id=group_id or _group_id_from_dir(attempt_dir.parent),
                allow_legacy_process_wall_proxy=allow_legacy_process_wall_proxy,
            )
            out["tail_tax"] = {
                "finalize_total_ms": m.get("finalize_total_ms", m.get("finalize_ms")),
                "finalize_flush_ms": m.get("finalize_flush_ms"),
                "finalize_drain_ms": m.get("finalize_drain_ms"),
                "process_wall_ms": proc_wall,
                "process_wall_ms_source": proc_src,
                "capture_begin_ms": m.get("capture_begin_ms"),
                "capture_end_ms": m.get("capture_end_ms"),
                "convert_trace_write_ms": counters.get("convert_trace_write_ms") or counters.get("convert_export_wall_ms"),
                "convert_command_wall_ms": counters.get("convert_command_wall_ms"),
                "convert_export_wall_ms": counters.get("convert_trace_write_ms") or counters.get("convert_export_wall_ms"),
                "finalize_reason": m.get("finalize_reason"),
            }
            if proxy_disclosure:
                out["tail_tax"]["legacy_proxy_disclosure"] = proxy_disclosure
            # smoke: fields present and non-negative
            for k, v in out["tail_tax"].items():
                if k in ("finalize_reason", "process_wall_ms_source", "legacy_proxy_disclosure"):
                    continue
                if v is None or float(v) < 0:
                    if strict:
                        raise RuntimeError(f"{attempt_dir.name}: tail_tax.{k} null/neg: {v}")
            wall = out.get("train_wall_ms_sum_logged_iters")
            fin = out["tail_tax"].get("finalize_total_ms") or 0.0
            exp = (
                out["tail_tax"].get("convert_trace_write_ms")
                or out["tail_tax"].get("convert_export_wall_ms")
                or 0.0
            )
            # Prefer node-runner monotonic e2e (argv start → process exit).
            e2e = manifest.get("e2e_wall_ms")
            if e2e is None:
                node_e2e = [
                    float(v.get("e2e_wall_ms"))
                    for v in (manifest.get("node_launch") or {}).values()
                    if isinstance(v, dict) and v.get("e2e_wall_ms") is not None
                ]
                e2e = max(node_e2e) if node_e2e else None
            if e2e is not None:
                out["e2e_wall_ms"] = float(e2e)
                out["e2e_wall_source"] = "manifest_max_node_monotonic"
            elif wall is not None:
                # Fallback only when node e2e absent (legacy); do not claim export covered.
                out["e2e_wall_ms"] = float(wall) + float(fin) + float(exp)
                out["e2e_wall_source"] = "legacy_iter_sum_plus_tail"
            else:
                out["e2e_wall_ms"] = None
                out["e2e_wall_source"] = "unavailable"

    if arm == "normal":
        wall = out.get("train_wall_ms_sum_logged_iters")
        e2e = manifest.get("e2e_wall_ms")
        if e2e is None:
            node_e2e = [
                float(v.get("e2e_wall_ms"))
                for v in (manifest.get("node_launch") or {}).values()
                if isinstance(v, dict) and v.get("e2e_wall_ms") is not None
            ]
            e2e = max(node_e2e) if node_e2e else None
        out["e2e_wall_ms"] = float(e2e) if e2e is not None else None
        out["e2e_wall_source"] = (
            "manifest_max_node_monotonic" if e2e is not None else "unavailable"
        )
        out["tail_tax"] = {"note": "normal arm: no collector finalize/export tail"}

    if arm == "torch":
        prof_dirs = sorted(attempt_dir.glob("torch_prof_node*"))
        view_traces = sorted({p for d in prof_dirs for p in d.rglob("trace_view.json")})
        rank0 = [p for p in view_traces if "torch_prof_node0" in str(p)][:1]
        out["trace"] = {
            "profiler_dir_bytes_sum": sum(dir_size(d) for d in prof_dirs),
            "profiler_dir_mib": sum(dir_size(d) for d in prof_dirs) / (1024 * 1024),
            "final_trace_json_bytes_sum": sum(p.stat().st_size for p in view_traces),
            "final_trace_json_mib": sum(p.stat().st_size for p in view_traces) / (1024 * 1024),
            "final_trace_file_count": len(view_traces),
            "trace_view_count": len(view_traces),
            "trace_view_bytes_sum": sum(p.stat().st_size for p in view_traces),
            "rank0_trace_bytes": rank0[0].stat().st_size if rank0 else 0,
            "rank0_trace_mib": (rank0[0].stat().st_size / (1024 * 1024)) if rank0 else 0,
            "rank0_trace_files": [str(p.relative_to(attempt_dir)) for p in rank0],
        }
        wall = out.get("train_wall_ms_sum_logged_iters")
        # torch export tail approximated by profiler dir presence; no fake flush_slot
        out["tail_tax"] = {
            "profiler_export_note": "torch profiler write included in e2e when wall known",
            "convert_export_wall_ms": None,
        }
        e2e = manifest.get("e2e_wall_ms")
        if e2e is None:
            node_e2e = [
                float(v.get("e2e_wall_ms"))
                for v in (manifest.get("node_launch") or {}).values()
                if isinstance(v, dict) and v.get("e2e_wall_ms") is not None
            ]
            e2e = max(node_e2e) if node_e2e else None
        out["e2e_wall_ms"] = float(e2e) if e2e is not None else None
        out["e2e_wall_source"] = (
            "manifest_max_node_monotonic" if e2e is not None else "unavailable"
        )

    if strict:
        # Full post-hoc: manifest/artifact/local-anchor; missing any → fail.
        try:
            validate_attempt_manifest(
                attempt_dir,
                expected_ranks=int(manifest.get("expected_ranks") or model.get("world_size") or 0),
                capture_step=int(manifest.get("capture_megatron_iter") or capture_iter),
                expected_nodes=int(manifest.get("expected_nodes") or manifest.get("nnodes") or 0) or None,
                require_seal=True,
                require_provenance=True,
                require_local_anchor=True,
            )
        except StrictValidationError as exc:
            raise RuntimeError(f"{attempt_dir.name}: strict manifest/anchor fail: {exc}") from exc
        if out.get("e2e_wall_ms") is None:
            raise RuntimeError(f"{attempt_dir.name}: e2e_wall_ms missing under --strict")
        if float(out["e2e_wall_ms"]) <= 0:
            raise RuntimeError(
                f"{attempt_dir.name}: e2e_wall_ms must be >0 under --strict, got {out['e2e_wall_ms']}"
            )
        if arm == "torch":
            tr = out.get("trace") or {}
            nfiles = int(tr.get("trace_view_count") or 0)
            nbytes = int(tr.get("profiler_dir_bytes_sum") or 0)
            if nfiles <= 0 or nbytes <= 0:
                raise RuntimeError(
                    f"{attempt_dir.name}: torch arm requires non-empty profiler traces "
                    f"(files={nfiles}, bytes={nbytes})"
                )
        if arm == "ours":
            tr = out.get("trace") or {}
            if int(tr.get("cluster_trace_bytes") or 0) <= 0:
                raise RuntimeError(f"{attempt_dir.name}: ours requires cluster.trace.json")
        if arm == "normal":
            out.setdefault("trace", {"arm_kind": "normal", "trace_required": False})

    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    group = args.strict or args.group_dir
    if not group:
        print(
            "usage: analyze_megatron_ab.py --strict GROUP_DIR | GROUP_DIR",
            file=sys.stderr,
        )
        return 2
    strict = False
    if args.strict:
        strict = True
        group = args.strict
    try:
        summary = analyze_group(
            Path(group),
            strict=strict,
            capture_iter=args.capture_iter,
            allow_legacy_process_wall_proxy=args.allow_legacy_process_wall_proxy,
        )
    except FileNotFoundError as exc:
        print(f"ANALYZE_FAIL: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"ANALYZE_FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "ANALYZE_OK",
        summary.get("status"),
        f"attempts={summary.get('attempt_count')}",
        f"strict={strict}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
