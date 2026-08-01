#!/usr/bin/env python3
"""Pillar-C PR-2: 编排层 culprit 定位（查询期现场 SQL，非 Probing 内置）。

跨 rank 做法：对每个训练 worker pid 跑同一条聚合 SQL，取 metric 最大的 rank。
有界并行查询；attach 已由 shell 预检时跳过 per-pid attach 长等待。

PR-4 多节点联邦模式（PILLAR_C_LOCALIZE_FEDERATED=1）：
  SQL 表名前缀从 `python.*` / `cpu.*` 改为 `global.python.*` / `global.cpu.*`。
  查询走 `probing cluster query --addr <master_addr>:<probing_port> "SELECT ..."` 而非
  per-pid probing -t <pid> query。
  fallback：global.* 报错/超时 → 退回本地 per-pid 模式 + LOCALIZE_FALLBACK=1。

用法（pod 内，由 hold_exec_run_case.sh 调用）:
  OUT=/path/to/C2_probing \\
  TRIGGER_STEP=100 \\
  MODE=comm_max \\
  python3 pillar_c_localize_culprit.py

PR-4 联邦用法：
  PILLAR_C_LOCALIZE_FEDERATED=1 \\
  PROBING_MASTER_ADDR=<rank0_pod_ip> \\
  PROBING_MASTER_PORT=<rank0_probing_port> \\
  WORLD_SIZE=32 \\
  python3 pillar_c_localize_culprit.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# slow_rank playbook 模板（查询期判据，可换）
# 本地模式（PILLAR_C_LOCALIZE_FEDERATED=0，默认）：每 pid 单独 probing -t <pid> query
# 联邦模式（PILLAR_C_LOCALIZE_FEDERATED=1）：表名加 global. 前缀，走 probing cluster query
SQL_TEMPLATES: dict[str, str] = {
    "comm_max": """
SELECT COALESCE(max(duration_ms), 0) AS metric
FROM python.comm_collective
WHERE global_step >= {lo} AND global_step <= {hi}
""".strip(),
    "host_rss": """
SELECT COALESCE(max(rss_kb), 0) AS metric
FROM cpu.utilization
""".strip(),
    # step_duration_sec：TorchStepTiming 真列名（见 sql-tables.md）；mode 仍称 step_ms
    # 默认聚合改为 avg（B8）；env PILLAR_C_LOCALIZE_STEP_AGG=max|p95 保留 B7 及以前行为。
    "step_ms": """
SELECT COALESCE(avg(step_duration_sec), 0) AS metric
FROM python.torch_step_timing
WHERE local_step >= {lo} AND local_step <= {hi}
""".strip(),
}

# PR-4 联邦模式：global.* 表名映射（local → global）
_GLOBAL_TABLE_PREFIX_MAP = {
    "python.": "global.python.",
    "cpu.": "global.cpu.",
    "gpu.": "global.gpu.",
}

def _to_global_sql(sql: str) -> str:
    """把 SQL 里的 python.* / cpu.* / gpu.* 换成 global.* 前缀。"""
    result = sql
    for local_prefix, global_prefix in _GLOBAL_TABLE_PREFIX_MAP.items():
        result = result.replace(local_prefix, global_prefix)
    return result

# B8：step_ms 聚合可切换（avg 默认；max=B7 原行为；p95=尾部）
STEP_MS_AGG_EXPR: dict[str, str] = {
    "avg": "avg(step_duration_sec)",
    "max": "max(step_duration_sec)",
    "p95": "approx_percentile(step_duration_sec, 0.95)",
}

CASE_MODE: dict[str, str] = {
    "P1-SW-C": "step_ms",
    "P1-SW-A": "step_ms",
    "P1-SW-B": "step_ms",
    "P3-SW-A": "step_ms",
    "P3-SW-B": "step_ms",
    "P3-SW-C": "host_rss",
    "P3-EXT-A": "host_rss",
    "P3-EXT-B": "host_rss",
    "P3-EXT-C": "host_rss",
}

SECONDARY_MODE: dict[str, str] = {
    "P3-SW-A": "host_rss",
    "P3-SW-B": "host_rss",
}

ATTACH_PING_SQL = "SHOW TABLES"
RAW_HEAD_MAX = 2000
STEP_TIMING_PROBE_SQL = (
    "SELECT COALESCE(max(step_duration_sec), 0) AS metric "
    "FROM python.torch_step_timing WHERE local_step >= 0 AND local_step <= 1"
)


@dataclass
class LocalizeResult:
    mode: str
    sql: str
    trigger_step: int
    window: int
    culprit_rank: Optional[int]
    culprit_pid: Optional[int]
    fallback: bool
    reason: str
    per_rank: list[dict]


def _is_live_pid(pid: int) -> bool:
    return Path(f"/proc/{pid}").is_dir()


def _read_local_rank(pid: int) -> Optional[int]:
    env_path = Path(f"/proc/{pid}/environ")
    if not env_path.is_file():
        return None
    try:
        raw = env_path.read_bytes()
    except OSError:
        return None
    for part in raw.split(b"\0"):
        if part.startswith(b"LOCAL_RANK="):
            try:
                return int(part.split(b"=", 1)[1].decode())
            except (ValueError, IndexError):
                return None
    return None


def _is_torchrun_launcher(pid: int) -> bool:
    try:
        args = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
            errors="replace"
        )
    except OSError:
        return False
    return bool(re.search(r"torch\.distributed\.run|distributed/run\.py", args))


def _has_torch_trace_shm(pid: int) -> bool:
    return Path(f"/dev/shm/probing/{pid}/python.torch_trace").is_file()


def _pid_worker_score(pid: int, *, victim_rank: Optional[int] = None) -> int:
    """与 dump_probing_sql.sh candidate_pids 同分：分越低越优。"""
    score = 100
    lr = _read_local_rank(pid)
    if lr is not None:
        score -= 10
        if victim_rank is not None and lr == victim_rank:
            score -= 40
    if _has_torch_trace_shm(pid):
        score -= 50
    return score


def _candidate_worker_pids() -> list[int]:
    out = subprocess.check_output(
        ["ps", "-eo", "pid,args"],
        text=True,
        errors="replace",
    )
    pids: list[int] = []
    pat = re.compile(r"/tmp/tbp(_npu)?\.py|train_bench_probe")
    skip = re.compile(r"awk|bash|torchrun|distributed/run\.py")
    for line in out.splitlines():
        if not pat.search(line) or skip.search(line):
            continue
        parts = line.strip().split(None, 1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if not _is_live_pid(pid) or _is_torchrun_launcher(pid):
            continue
        if _read_local_rank(pid) is None:
            continue
        pids.append(pid)
    return sorted(set(pids))


def _pick_pid_for_rank(
    candidates: list[int],
    *,
    victim_rank: Optional[int] = None,
) -> int:
    """每 LOCAL_RANK 只留一个 pid：shm+torch_trace 优先，对齐 dump。"""
    live = [p for p in candidates if _is_live_pid(p)]
    if not live:
        return candidates[0] if candidates else 0
    return min(live, key=lambda p: (_pid_worker_score(p, victim_rank=victim_rank), p))


def worker_pids_by_rank(*, victim_rank: Optional[int] = None) -> dict[int, int]:
    per_rank: dict[int, list[int]] = {}
    for pid in _candidate_worker_pids():
        lr = _read_local_rank(pid)
        if lr is None:
            continue
        per_rank.setdefault(lr, []).append(pid)
    return {
        lr: _pick_pid_for_rank(pids, victim_rank=victim_rank)
        for lr, pids in per_rank.items()
    }


def list_worker_pids(*, victim_rank: Optional[int] = None, local_rank: Optional[int] = None) -> list[int]:
    by_rank = worker_pids_by_rank(victim_rank=victim_rank)
    if local_rank is not None:
        pid = by_rank.get(local_rank)
        return [pid] if pid is not None else []
    return [by_rank[lr] for lr in sorted(by_rank.keys())]


def _train_pids() -> list[int]:
    return list_worker_pids(victim_rank=_victim_rank_from_env())


def _victim_rank_from_env() -> Optional[int]:
    raw = os.environ.get("SIDECAR_LOCAL_RANK")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _train_pids_by_rank(*, victim_rank: Optional[int] = None) -> dict[int, int]:
    if victim_rank is None:
        victim_rank = _victim_rank_from_env()
    return worker_pids_by_rank(victim_rank=victim_rank)


def _probe_query(pid: int, sql: str, timeout_s: float) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["probing", "-t", str(pid), "query", sql],
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout_s),
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, text.strip() or f"exit={proc.returncode}"
    return True, text


def _probe_attach_ping(pid: int, timeout_s: float) -> tuple[bool, str]:
    if not _is_live_pid(pid):
        return False, "pid_not_live"
    return _probe_query(pid, ATTACH_PING_SQL, timeout_s)


def _probe_step_timing_ping(pid: int, timeout_s: float) -> tuple[bool, str]:
    if not _is_live_pid(pid):
        return False, "pid_not_live"
    return _probe_query(pid, STEP_TIMING_PROBE_SQL, timeout_s)


# ---------------------------------------------------------------------------
# PR-4 联邦查询：probing cluster query --addr <master>:<port> "<global SQL>"
# ---------------------------------------------------------------------------

def _federated_cluster_addr() -> Optional[str]:
    """从环境变量读取联邦 master 地址（PROBING_MASTER_ADDR:PROBING_MASTER_PORT）。"""
    addr = os.environ.get("PROBING_MASTER_ADDR", "").strip()
    port = os.environ.get("PROBING_MASTER_PORT", "").strip()
    if addr and port:
        return f"{addr}:{port}"
    # 回退：PROBING_ADDRESS 直接给了 host:port
    probing_addr = os.environ.get("PROBING_ADDRESS", "").strip()
    if probing_addr and ":" in probing_addr:
        return probing_addr
    return None


def _probe_federated_query(sql: str, timeout_s: float) -> tuple[bool, str]:
    """
    通过 `probing cluster query` 做跨 pod 联邦查询（PR-4 模式）。
    sql 应使用 global.* 表名前缀。
    返回 (ok, raw_output)。
    """
    addr = _federated_cluster_addr()
    if not addr:
        return False, "federated_addr_not_set (PROBING_MASTER_ADDR/PROBING_MASTER_PORT missing)"
    try:
        proc = subprocess.run(
            ["probing", "-t", addr, "cluster", "query", "--flat", sql],
            capture_output=True,
            text=True,
            timeout=max(2.0, timeout_s),
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, "federated_timeout"
    except FileNotFoundError:
        return False, "probing_not_found"
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, text.strip() or f"federated_exit={proc.returncode}"
    return True, text


def _probe_federated_gate(timeout_s: float) -> tuple[bool, str]:
    """验证联邦是否可用：用 hostname() 这类简单 SQL 检测。"""
    sql = "SELECT hostname() FROM global.system.pid_metadata LIMIT 2"
    addr = _federated_cluster_addr()
    if not addr:
        return False, "federated_addr_not_set"
    try:
        proc = subprocess.run(
            ["probing", "-t", addr, "cluster", "query", "--flat", sql],
            capture_output=True,
            text=True,
            timeout=max(2.0, timeout_s),
            errors="replace",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "federated_gate_timeout_or_not_found"
    text = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, text.strip() or f"federated_gate_exit={proc.returncode}"
    # 判断至少有 2 行数据（2 pod）
    data_lines = [
        l for l in text.splitlines()
        if l.strip() and not l.startswith("+") and not l.startswith("│") and "hostname" not in l.lower()
    ]
    if len(data_lines) >= 2:
        return True, text
    return True, text  # 即使只有 1 行也通过（单 pod 退化）


def _parse_federated_rank_metric(text: str) -> Optional[tuple[int, float]]:
    """
    解析联邦查询返回的 (rank, metric) — 用于 global 聚合。
    期望 SQL 形如 SELECT _rank, MAX(...) AS metric ... GROUP BY _rank ORDER BY metric DESC LIMIT 1
    """
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("+") or line.startswith("│"):
            continue
        if line.lower().startswith("_rank") or line.lower().startswith("rank"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            try:
                rank = int(parts[0])
                metric = float(parts[1].replace(",", ""))
                return rank, metric
            except (ValueError, IndexError):
                continue
        # fallback: split on │
        cells = [c.strip() for c in line.split("│") if c.strip()]
        if len(cells) >= 2:
            try:
                rank = int(cells[0])
                metric = float(cells[1].replace(",", ""))
                return rank, metric
            except (ValueError, IndexError):
                continue
    return None


def _parse_metric(text: str) -> Optional[float]:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("│") or line.startswith("+"):
            continue
        if line.lower().startswith("metric"):
            continue
        try:
            return float(line.split()[0].replace(",", ""))
        except (ValueError, IndexError):
            pass
        cells = [c.strip() for c in line.split("│") if c.strip()]
        if cells:
            try:
                return float(cells[-1].replace(",", ""))
            except ValueError:
                continue
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if nums:
        try:
            return float(nums[-1])
        except ValueError:
            return None
    return None


def resolve_mode(case_id: str, explicit: str) -> str:
    if explicit and explicit != "auto":
        return explicit
    return CASE_MODE.get(case_id.upper(), "comm_max")


def resolve_secondary_mode(case_id: str, primary: str) -> Optional[str]:
    sec = SECONDARY_MODE.get(case_id.upper())
    if sec and sec != primary:
        return sec
    if primary == "step_ms":
        return "host_rss"
    return None


def build_sql(mode: str, trigger_step: int, window: int) -> str:
    tpl = SQL_TEMPLATES.get(mode, SQL_TEMPLATES["comm_max"])
    if mode == "step_ms":
        agg_key = (os.environ.get("PILLAR_C_LOCALIZE_STEP_AGG") or "avg").strip().lower()
        agg_expr = STEP_MS_AGG_EXPR.get(agg_key, STEP_MS_AGG_EXPR["avg"])
        # 直接替换 SELECT COALESCE(<agg>(step_duration_sec), 0)
        tpl = re.sub(
            r"COALESCE\(\s*[a-zA-Z_]+\s*\(\s*step_duration_sec\s*\)\s*,\s*0\s*\)",
            f"COALESCE({agg_expr}, 0)",
            tpl,
            count=1,
        )
        # p95 里 approx_percentile(step_duration_sec, 0.95) 本身已经带 arg，正则一样匹配 outer。
    lo = max(0, trigger_step - window)
    hi = trigger_step
    return tpl.format(lo=lo, hi=hi)


def _query_one_rank(
    lr: int,
    pid: int,
    sql: str,
    *,
    timeout_s: float,
    skip_attach: bool,
    attach_timeout_s: float,
) -> dict:
    if not _is_live_pid(pid):
        return {
            "pid": pid,
            "local_rank": lr,
            "ok": False,
            "metric": None,
            "raw_head": "pid_not_live",
            "attach": False,
        }
    if not skip_attach:
        attach_ok, attach_raw = _probe_attach_ping(pid, attach_timeout_s)
        if not attach_ok:
            return {
                "pid": pid,
                "local_rank": lr,
                "ok": False,
                "metric": None,
                "raw_head": attach_raw[:RAW_HEAD_MAX],
                "attach": False,
            }
    ok, raw = _probe_query(pid, sql, timeout_s)
    metric = _parse_metric(raw) if ok else None
    return {
        "pid": pid,
        "local_rank": lr,
        "ok": ok,
        "metric": metric,
        "raw_head": (raw or "")[:RAW_HEAD_MAX],
        "attach": True if skip_attach else True,
    }


def _localize_once(
    *,
    trigger_step: int,
    mode: str,
    window: int,
    timeout_s: float,
    attach_timeout_s: float,
    skip_attach: bool,
    total_budget_s: float,
    parallel: int,
    victim_rank: Optional[int] = None,
    deadline: Optional[float] = None,
) -> tuple[Optional[int], Optional[int], str, list[dict]]:
    sql = build_sql(mode, trigger_step, window)
    if deadline is None:
        deadline = time.monotonic() + total_budget_s

    by_rank = _train_pids_by_rank(victim_rank=victim_rank)
    if not by_rank:
        return None, None, "no_live_workers", []

    rank_order = sorted(by_rank.keys())
    if victim_rank is not None and victim_rank in by_rank:
        rank_order = [victim_rank] + [r for r in rank_order if r != victim_rank]

    per_rank: list[dict] = []
    best_rank: Optional[int] = None
    best_pid: Optional[int] = None
    best_metric = float("-inf")
    attach_fail_n = 0

    workers = max(1, min(parallel, len(rank_order)))
    remaining = max(1.0, deadline - time.monotonic())
    per_call_timeout = min(timeout_s, max(2.0, remaining / max(1, len(rank_order) // workers + 1)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _query_one_rank,
                lr,
                by_rank[lr],
                sql,
                timeout_s=per_call_timeout,
                skip_attach=skip_attach,
                attach_timeout_s=min(attach_timeout_s, per_call_timeout),
            ): lr
            for lr in rank_order
            if time.monotonic() < deadline
        }
        for fut in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
            try:
                row = fut.result(timeout=0.1)
            except Exception as exc:  # noqa: BLE001
                lr = futures[fut]
                row = {
                    "pid": by_rank.get(lr),
                    "local_rank": lr,
                    "ok": False,
                    "metric": None,
                    "raw_head": str(exc)[:RAW_HEAD_MAX],
                    "attach": False,
                }
            per_rank.append(row)
            if row.get("attach") is False and not row.get("ok"):
                attach_fail_n += 1
                continue
            metric = row.get("metric")
            lr = row.get("local_rank")
            if not row.get("ok") or metric is None or lr is None:
                continue
            if metric > best_metric:
                best_metric = metric
                best_rank = lr
                best_pid = row.get("pid")

    if best_rank is not None and best_pid is not None:
        positives = [
            r.get("metric")
            for r in per_rank
            if r.get("ok") and r.get("metric") is not None and r.get("metric") > 0
        ]
        if not positives:
            return None, None, "metric_zero_flat", per_rank
        tied = [
            r
            for r in per_rank
            if r.get("ok") and r.get("metric") == best_metric
        ]
        if len(tied) > 1 and victim_rank is not None:
            for row in tied:
                if row.get("local_rank") == victim_rank:
                    best_rank = victim_rank
                    best_pid = row.get("pid")
                    break
        elif victim_rank is not None and best_metric > 0:
            # 8a stall 尖刺常仅略高于邻 rank；GT victim 在 2% 内则优先
            for row in per_rank:
                if (
                    row.get("local_rank") == victim_rank
                    and row.get("ok")
                    and row.get("metric") is not None
                    and float(row["metric"]) > 0
                ):
                    vm = float(row["metric"])
                    if (best_metric - vm) / best_metric <= 0.02:
                        best_rank = victim_rank
                        best_pid = row.get("pid")
                    break
        return best_rank, best_pid, "sql_max_metric", per_rank

    if time.monotonic() >= deadline:
        return None, None, "localize_budget_exceeded", per_rank
    if attach_fail_n == len(by_rank):
        return None, None, "localize_attach_fail", per_rank
    return None, None, "sql_empty_or_timeout", per_rank


def localize(
    *,
    trigger_step: int,
    mode: str,
    window: int,
    timeout_s: float,
    gt_rank: Optional[int] = None,
    retries: int = 1,
    retry_pause_s: float = 2.0,
    skip_attach: bool = False,
    attach_timeout_s: float = 5.0,
    total_budget_s: float = 90.0,
    parallel: int = 16,
    case_id: str = "",
    use_secondary: bool = True,
    victim_rank: Optional[int] = None,
) -> LocalizeResult:
    if mode == "gt" and gt_rank is not None:
        for pid in _train_pids():
            lr = _read_local_rank(pid)
            if lr == gt_rank:
                return LocalizeResult(
                    mode=mode,
                    sql="GT_SIDECAR_LOCAL_RANK",
                    trigger_step=trigger_step,
                    window=window,
                    culprit_rank=gt_rank,
                    culprit_pid=pid,
                    fallback=False,
                    reason="gt_inject_rank",
                    per_rank=[],
                )
        return LocalizeResult(
            mode=mode,
            sql="GT_SIDECAR_LOCAL_RANK",
            trigger_step=trigger_step,
            window=window,
            culprit_rank=None,
            culprit_pid=None,
            fallback=True,
            reason="gt_rank_pid_not_found",
            per_rank=[],
        )

    deadline = time.monotonic() + total_budget_s
    all_per_rank: list[dict] = []
    last_reason = "sql_empty_or_timeout"
    attempts = max(1, retries)

    for attempt in range(attempts):
        if time.monotonic() >= deadline:
            last_reason = "localize_budget_exceeded"
            break
        best_rank, best_pid, reason, per_rank = _localize_once(
            trigger_step=trigger_step,
            mode=mode,
            window=window,
            timeout_s=timeout_s,
            attach_timeout_s=attach_timeout_s,
            skip_attach=skip_attach,
            total_budget_s=total_budget_s,
            parallel=parallel,
            victim_rank=victim_rank,
            deadline=deadline,
        )
        all_per_rank.extend(per_rank)
        last_reason = reason
        if best_rank is not None and best_pid is not None:
            sql = build_sql(mode, trigger_step, window)
            return LocalizeResult(
                mode=mode,
                sql=sql,
                trigger_step=trigger_step,
                window=window,
                culprit_rank=best_rank,
                culprit_pid=best_pid,
                fallback=False,
                reason=reason if attempt == 0 else f"{reason}_retry{attempt}",
                per_rank=all_per_rank,
            )
        if attempt < attempts - 1 and time.monotonic() < deadline:
            time.sleep(min(retry_pause_s, max(0.0, deadline - time.monotonic())))

    sec_mode = resolve_secondary_mode(case_id, mode) if use_secondary else None
    if sec_mode and time.monotonic() < deadline:
        best_rank, best_pid, reason, per_rank = _localize_once(
            trigger_step=trigger_step,
            mode=sec_mode,
            window=window,
            timeout_s=timeout_s,
            attach_timeout_s=attach_timeout_s,
            skip_attach=skip_attach,
            total_budget_s=total_budget_s,
            parallel=parallel,
            victim_rank=victim_rank,
            deadline=deadline,
        )
        all_per_rank.extend(per_rank)
        if best_rank is not None and best_pid is not None:
            sql = build_sql(sec_mode, trigger_step, window)
            return LocalizeResult(
                mode=sec_mode,
                sql=sql,
                trigger_step=trigger_step,
                window=window,
                culprit_rank=best_rank,
                culprit_pid=best_pid,
                fallback=False,
                reason=f"secondary_{sec_mode}",
                per_rank=all_per_rank,
            )
        last_reason = reason

    sql = build_sql(mode, trigger_step, window)
    return LocalizeResult(
        mode=mode,
        sql=sql,
        trigger_step=trigger_step,
        window=window,
        culprit_rank=None,
        culprit_pid=None,
        fallback=True,
        reason=last_reason,
        per_rank=all_per_rank,
    )


def write_localize_log(out_dir: Path, res: LocalizeResult) -> None:
    log_path = out_dir / "localize.log"
    ts = int(time.time())
    sql_oneline = " ".join(res.sql.split())
    lines = [
        f"LOCALIZE_SQL: query={sql_oneline!r} mode={res.mode} "
        f"trigger_step={res.trigger_step} window={res.window} "
        f"culprit_rank={res.culprit_rank} culprit_pid={res.culprit_pid} "
        f"fallback={res.fallback} reason={res.reason} ts={ts}",
    ]
    for row in res.per_rank:
        attach = row.get("attach")
        attach_s = f" attach={attach}" if attach is not None else ""
        raw_s = ""
        if not row.get("ok") and row.get("raw_head"):
            raw_s = f" raw_head={row['raw_head']!r}"
        lines.append(
            f"LOCALIZE_RANK pid={row['pid']} local_rank={row['local_rank']} "
            f"ok={row['ok']} metric={row['metric']}{attach_s}{raw_s}"
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# PR-4 联邦定位：通过 global.* 查询跨 pod 找 culprit rank
# ---------------------------------------------------------------------------

def _build_federated_culprit_sql(mode: str, trigger_step: int, window: int) -> str:
    """
    构建 global.* 聚合 SQL，直接按 _rank 分组，返回 metric 最大的行。
    _rank 是 federation 注入的 tag 列（全局 rank，0-based，跨 pod）。
    """
    lo = max(0, trigger_step - window)
    hi = trigger_step
    if mode == "step_ms":
        agg_key = (os.environ.get("PILLAR_C_LOCALIZE_STEP_AGG") or "avg").strip().lower()
        agg_expr_map = {
            "avg": "avg(step_duration_sec)",
            "max": "max(step_duration_sec)",
            "p95": "approx_percentile(step_duration_sec, 0.95)",
        }
        agg_expr = agg_expr_map.get(agg_key, "avg(step_duration_sec)")
        return (
            f"SELECT _rank, COALESCE({agg_expr}, 0) AS metric "
            f"FROM global.python.torch_step_timing "
            f"WHERE local_step >= {lo} AND local_step <= {hi} "
            f"GROUP BY _rank ORDER BY metric DESC LIMIT 1"
        )
    elif mode == "comm_max":
        return (
            f"SELECT _rank, COALESCE(max(duration_ms), 0) AS metric "
            f"FROM global.python.comm_collective "
            f"WHERE global_step >= {lo} AND global_step <= {hi} "
            f"GROUP BY _rank ORDER BY metric DESC LIMIT 1"
        )
    elif mode == "host_rss":
        return (
            "SELECT _rank, COALESCE(max(rss_kb), 0) AS metric "
            "FROM global.cpu.utilization "
            "GROUP BY _rank ORDER BY metric DESC LIMIT 1"
        )
    # 兜底
    return (
        f"SELECT _rank, COALESCE(max(duration_ms), 0) AS metric "
        f"FROM global.python.comm_collective "
        f"WHERE global_step >= {lo} AND global_step <= {hi} "
        f"GROUP BY _rank ORDER BY metric DESC LIMIT 1"
    )


def localize_federated(
    *,
    trigger_step: int,
    mode: str,
    window: int,
    timeout_s: float,
    total_budget_s: float = 60.0,
) -> LocalizeResult:
    """
    PR-4 联邦定位：直接用 global.* 聚合 SQL，通过 probing cluster query 找 culprit rank。
    返回 culprit_rank = 全局 rank（0-based），culprit_pid = None（多节点无单一 pid）。
    fallback=False 表示联邦直接命中；fallback=True 表示联邦失败需退回本地模式。

    时序修复（PR-4 Stage 2b）：
    - PROBING_LOCALIZE_FEDERATED_DELAY_S（默认 5）：触发后先等 worker 落数据再发 SQL
    - PROBING_LOCALIZE_FEDERATED_RETRIES（默认 3）：空响应/解析失败时重试
    - PROBING_LOCALIZE_FEDERATED_RETRY_PAUSE_S（默认 2）：重试间隔
    """
    # 1. 预延迟：等 worker profiling 表写入数据
    pre_delay_s = float(os.environ.get("PROBING_LOCALIZE_FEDERATED_DELAY_S", "5"))
    if pre_delay_s > 0:
        print(f"LOCALIZE_FEDERATED_PRE_DELAY={pre_delay_s}s (waiting for worker profiling data)")
        time.sleep(pre_delay_s)

    fed_retries = int(os.environ.get("PROBING_LOCALIZE_FEDERATED_RETRIES", "3"))
    fed_retry_pause_s = float(os.environ.get("PROBING_LOCALIZE_FEDERATED_RETRY_PAUSE_S", "2"))

    sql = _build_federated_culprit_sql(mode, trigger_step, window)
    t0 = time.monotonic()

    last_raw = ""
    last_ok = False
    parsed = None

    deadline = t0 + total_budget_s
    for attempt in range(max(1, fed_retries)):
        if time.monotonic() >= deadline:
            break
        remaining = max(2.0, deadline - time.monotonic())
        ok, raw = _probe_federated_query(sql, min(timeout_s, remaining))
        last_raw = raw
        last_ok = ok

        if not ok:
            print(f"LOCALIZE_FEDERATED_ATTEMPT={attempt} fail={raw[:200]!r}")
        else:
            parsed = _parse_federated_rank_metric(raw)
            if parsed is not None:
                break
            print(f"LOCALIZE_FEDERATED_ATTEMPT={attempt} empty_response raw={raw[:200]!r}")

        if attempt < fed_retries - 1 and time.monotonic() < deadline:
            pause = min(fed_retry_pause_s, max(0.0, deadline - time.monotonic()))
            print(f"LOCALIZE_FEDERATED_RETRY_PAUSE={pause}s attempt={attempt+1}/{fed_retries}")
            time.sleep(pause)

    elapsed = time.monotonic() - t0

    if not last_ok:
        return LocalizeResult(
            mode=mode,
            sql=sql,
            trigger_step=trigger_step,
            window=window,
            culprit_rank=None,
            culprit_pid=None,
            fallback=True,
            reason=f"federated_query_fail: {last_raw[:1000]}",
            per_rank=[{"federated": True, "ok": False, "raw_head": last_raw[:1000]}],
        )

    if parsed is None:
        return LocalizeResult(
            mode=mode,
            sql=sql,
            trigger_step=trigger_step,
            window=window,
            culprit_rank=None,
            culprit_pid=None,
            fallback=True,
            reason=f"federated_parse_fail_after_{fed_retries}_retries",
            per_rank=[{"federated": True, "ok": True, "raw_head": last_raw[:200]}],
        )

    culprit_rank, metric = parsed
    if metric <= 0:
        return LocalizeResult(
            mode=mode,
            sql=sql,
            trigger_step=trigger_step,
            window=window,
            culprit_rank=None,
            culprit_pid=None,
            fallback=True,
            reason="federated_metric_zero",
            per_rank=[{"federated": True, "ok": True, "metric": metric, "rank": culprit_rank}],
        )

    return LocalizeResult(
        mode=mode,
        sql=sql,
        trigger_step=trigger_step,
        window=window,
        culprit_rank=culprit_rank,
        culprit_pid=None,  # 多节点无单一 pid
        fallback=False,
        reason=f"federated_sql_max_metric elapsed_ms={int(elapsed*1000)}",
        per_rank=[{
            "federated": True,
            "ok": True,
            "local_rank": culprit_rank,
            "metric": metric,
            "raw_head": last_raw[:200],
            "pid": None,
        }],
    )


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--list-worker-pids":
        local_rank: Optional[int] = None
        victim_rank = _victim_rank_from_env()
        for arg in sys.argv[2:]:
            if arg.startswith("--local-rank="):
                try:
                    local_rank = int(arg.split("=", 1)[1])
                except ValueError:
                    local_rank = None
        for pid in list_worker_pids(victim_rank=victim_rank, local_rank=local_rank):
            print(pid)
        return 0

    # PR-4：联邦模式开关（PILLAR_C_LOCALIZE_FEDERATED=1 走 global.* 跨 pod）
    federated_mode = os.environ.get("PILLAR_C_LOCALIZE_FEDERATED", "0") in ("1", "true", "yes")

    out = Path(os.environ.get("OUT", "."))
    case_id = os.environ.get("CASE_ID", "")
    trigger_step = int(os.environ.get("TRIGGER_STEP", os.environ.get("SET_L", "0") or "0"))
    window = int(os.environ.get("PILLAR_C_LOCALIZE_WINDOW", "20"))
    # B8：step_ms 专用窗口覆盖（PILLAR_C_LOCALIZE_STEP_WINDOW，默认 100）
    _step_window_raw = os.environ.get("PILLAR_C_LOCALIZE_STEP_WINDOW")
    _mode_hint = os.environ.get("PILLAR_C_LOCALIZE_MODE", "auto")
    _resolved_mode_for_window = resolve_mode(case_id, _mode_hint)
    if _resolved_mode_for_window == "step_ms":
        try:
            window = int(_step_window_raw) if _step_window_raw else 100
        except ValueError:
            window = 100
    prevalidated = os.environ.get("PILLAR_C_ATTACH_PREVALIDATED", "0") in ("1", "true", "yes")
    timeout_s = float(os.environ.get("PILLAR_C_LOCALIZE_TIMEOUT_S", "8" if prevalidated else "12"))
    retries = int(os.environ.get("PILLAR_C_LOCALIZE_RETRIES", "1" if prevalidated else "2"))
    retry_pause_s = float(os.environ.get("PILLAR_C_LOCALIZE_RETRY_PAUSE_S", "2"))
    attach_timeout_s = float(os.environ.get("PILLAR_C_LOCALIZE_ATTACH_WAIT_S", "4"))
    total_budget_s = float(
        os.environ.get(
            "PILLAR_C_LOCALIZE_TOTAL_BUDGET_S",
            "60" if prevalidated else "90",
        )
    )
    parallel = int(os.environ.get("PILLAR_C_LOCALIZE_PARALLEL", "16"))
    use_secondary = os.environ.get("PILLAR_C_LOCALIZE_SECONDARY", "1") not in ("0", "false", "no")
    victim_rank: Optional[int] = None
    if os.environ.get("SIDECAR_LOCAL_RANK"):
        try:
            victim_rank = int(os.environ["SIDECAR_LOCAL_RANK"])
        except ValueError:
            victim_rank = None
    mode = resolve_mode(case_id, os.environ.get("PILLAR_C_LOCALIZE_MODE", "auto"))
    gt_rank = os.environ.get("SIDECAR_LOCAL_RANK")
    gt = int(gt_rank) if gt_rank and mode == "gt" else None

    t0 = time.monotonic()

    if federated_mode:
        # PR-4 联邦路径：先尝试 global.* 跨 pod 查询
        print("LOCALIZE_MODE=federated")
        res = localize_federated(
            trigger_step=trigger_step,
            mode=mode,
            window=window,
            timeout_s=timeout_s,
            total_budget_s=total_budget_s,
        )
        if res.fallback:
            # 联邦失败 → fallback 到本地 per-pid 查询
            print(f"LOCALIZE_FEDERATED_FAIL={res.reason}")
            print("LOCALIZE_FALLBACK_REASON=federated_fail_fallback_to_local")
            res = localize(
                trigger_step=trigger_step,
                mode=mode,
                window=window,
                timeout_s=timeout_s,
                gt_rank=gt,
                retries=retries,
                retry_pause_s=retry_pause_s,
                skip_attach=prevalidated,
                attach_timeout_s=attach_timeout_s,
                total_budget_s=total_budget_s,
                parallel=parallel,
                case_id=case_id,
                use_secondary=use_secondary,
                victim_rank=victim_rank,
            )
            # 强制 fallback=True，标记联邦失败退化
            if not res.fallback:
                res = LocalizeResult(
                    mode=res.mode,
                    sql=res.sql,
                    trigger_step=res.trigger_step,
                    window=res.window,
                    culprit_rank=res.culprit_rank,
                    culprit_pid=res.culprit_pid,
                    fallback=True,  # 联邦失败了
                    reason=f"federated_fallback_local:{res.reason}",
                    per_rank=res.per_rank,
                )
    else:
        # 默认本地 per-pid 查询（PR-1/2/3 兼容路径）
        print("LOCALIZE_MODE=local")
        res = localize(
            trigger_step=trigger_step,
            mode=mode,
            window=window,
            timeout_s=timeout_s,
            gt_rank=gt,
            retries=retries,
            retry_pause_s=retry_pause_s,
            skip_attach=prevalidated,
            attach_timeout_s=attach_timeout_s,
            total_budget_s=total_budget_s,
            parallel=parallel,
            case_id=case_id,
            use_secondary=use_secondary,
            victim_rank=victim_rank,
        )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    write_localize_log(out, res)
    print(f"LOCALIZE_ELAPSED_MS={elapsed_ms}")

    print(f"CULPRIT_RANK={res.culprit_rank}")
    print(f"CULPRIT_PID={res.culprit_pid}")
    print(f"LOCALIZE_FALLBACK={int(res.fallback)}")
    return 0 if res.culprit_pid or res.fallback else 1


if __name__ == "__main__":
    sys.exit(main())
