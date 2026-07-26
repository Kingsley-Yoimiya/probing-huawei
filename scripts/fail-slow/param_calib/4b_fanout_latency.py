#!/usr/bin/env python3
"""④-B 聚合延迟 vs 基数 / FanoutScope 切换点。

对齐 federation 默认：并发=128、超时=30s、FanoutScope∈{Local,Flat,Node,Coordinator}。
控制：同注入形态、同 CRITERIA 两阶段过滤（④-A）、只变基数 N（或 scope）。

模式：
  - simulated_network（默认）：按 cluster_fanout / FanoutScope 跳数建模 + 本机 HTTP 微基准校准
    进程/并发/合并开销；跨机 RTT 用可配置参数（诚实标注，非伪造 64 卡 live 作业）。
  - local_http_microbench：本机起 N 个 SUMMARY 端点，实测 Flat 并发扇出墙钟（校准用）。

用法:
  python3 4b_fanout_latency.py \\
    --results-root .../results/ascend-ais \\
    --out .../param_calib/4B_fanout_latency
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

# --- 锁定控制（与 ④-A / CRITERIA 一致，禁止改扫）---
WINDOW_LO, WINDOW_HI = 100, 300
VICTIM = 7
DOSE = "loud"
DOSE_THETA = 1.16
CROSS_RANK_THETA = 1.2
WORST_FRACTION_PHI = 0.4
W_STAR = 100
BYTES_PER_TT_STEP = 24.6 * 1024
INCLUDE_TT = True

# federation 默认（cluster_executor.rs）
FANOUT_CONCURRENCY = 128
REMOTE_TIMEOUT_S = 30.0

# 拓扑：昇腾 A3 常见 16 卡/机
GPUS_PER_NODE = 16
N_SWEEP = (8, 16, 32, 64)
SCOPES = ("Local", "Flat", "Node", "Coordinator")

# ④-A 实测摘要量（B/rank）；DETAIL TT 估计
SUMMARY_BYTES_PER_RANK = 208.2
DETAIL_PHASE_BYTES = 10100.0
DETAIL_TT_BYTES = W_STAR * BYTES_PER_TT_STEP
DETAIL_BYTES_PER_SUSPECT = DETAIL_PHASE_BYTES + (DETAIL_TT_BYTES if INCLUDE_TT else 0)

# 主对照 case（与 ④-A primary 一致）
PRIMARY_RUN = "20260725_012957-yjr-as-c-p3-sw-a-loud"
PRIMARY_CASE = "P3-SW-A"

# 网络默认（DC 量级；可用 --rtt-* / 微基准覆盖）
DEFAULT_RTT_INTRA_MS = 0.35   # 同机 loopback/IPC 量级偏乐观；微基准会校准
DEFAULT_RTT_INTER_MS = 0.80   # 同机房跨 pod
DEFAULT_BW_INTRA_GBPS = 25.0
DEFAULT_BW_INTER_GBPS = 10.0
DEFAULT_PROC_MS = 0.15        # 单端本地摘要查询处理
DEFAULT_MERGE_US_PER_PEER = 8.0
COORD_CPU_MS = 0.50           # ④-A federated_coord 中位量级
STRAGGLER_SIGMA = 0.25        # lognormal 相对抖动（straggler 尾）
N_TRIALS = 21
MICROBENCH_PAYLOAD = b'{"rank":0,"status":"healthy","step_ms_med":1.0}'  # ~50B；扩到 ~208B
RNG_SEED = 42


@dataclass
class NetParams:
    rtt_intra_ms: float
    rtt_inter_ms: float
    bw_intra_gbps: float
    bw_inter_gbps: float
    proc_ms: float
    merge_us_per_peer: float
    concurrency: int = FANOUT_CONCURRENCY
    timeout_s: float = REMOTE_TIMEOUT_S


def utf8_len(obj: object) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def transfer_ms(nbytes: float, bw_gbps: float) -> float:
    if bw_gbps <= 0:
        return 0.0
    return (nbytes * 8.0) / (bw_gbps * 1e9) * 1000.0


def peer_service_ms(
    rng: random.Random,
    rtt_ms: float,
    nbytes: float,
    bw_gbps: float,
    proc_ms: float,
    sigma: float = STRAGGLER_SIGMA,
) -> float:
    """单 peer 服务时间 ≈ Lognormal 抖动 × (RTT + 传 + 处理)。"""
    base = rtt_ms + transfer_ms(nbytes, bw_gbps) + proc_ms
    # lognormal 均值≈1：mu = -0.5 sigma^2
    jitter = math.exp(rng.gauss(-0.5 * sigma * sigma, sigma))
    return base * jitter


def concurrent_fanout_ms(
    rng: random.Random,
    n_peers: int,
    rtt_ms: float,
    nbytes: float,
    bw_gbps: float,
    proc_ms: float,
    concurrency: int,
    merge_us_per_peer: float,
) -> float:
    """对齐 cluster_executor：按 concurrency 分波，每波取 max(peer)。"""
    if n_peers <= 0:
        return 0.0
    samples = [
        peer_service_ms(rng, rtt_ms, nbytes, bw_gbps, proc_ms) for _ in range(n_peers)
    ]
    c = max(1, concurrency)
    total = 0.0
    for i in range(0, n_peers, c):
        wave = samples[i : i + c]
        total += max(wave)
    total += (n_peers * merge_us_per_peer) / 1000.0
    return total


def n_nodes(n_ranks: int, gpus_per_node: int = GPUS_PER_NODE) -> int:
    return max(1, math.ceil(n_ranks / gpus_per_node))


def ranks_on_coord_node(n_ranks: int, gpus_per_node: int = GPUS_PER_NODE) -> int:
    """协调所在机上的 rank 数（不满机时取 min）。"""
    return min(gpus_per_node, n_ranks)


def simulate_scope_phase1_ms(
    rng: random.Random,
    scope: str,
    n_ranks: int,
    net: NetParams,
    summary_bytes: float,
) -> dict:
    """Phase-1 全 rank SUMMARY 扇出墙钟（ms）按 FanoutScope。"""
    if scope == "Local":
        # 仅本进程：不能聚齐跨 rank；记本地摘要 + 标记 incomplete
        return {
            "latency_ms": net.proc_ms,
            "remote_peers": 0,
            "complete": n_ranks <= 1,
            "hops": "local_only",
        }

    if scope == "Flat":
        # 协调直连其余 N-1 peer（跨机用 inter RTT；同机混部时仍按 inter 上界偏保守）
        peers = max(0, n_ranks - 1)
        lat = concurrent_fanout_ms(
            rng,
            peers,
            net.rtt_inter_ms,
            summary_bytes,
            net.bw_inter_gbps,
            net.proc_ms,
            net.concurrency,
            net.merge_us_per_peer,
        )
        return {
            "latency_ms": lat + net.proc_ms,  # 含本机
            "remote_peers": peers,
            "complete": True,
            "hops": "coord→all_peers",
        }

    if scope == "Node":
        # 单机 local0 → 本机 leaves；跨机 rank 不可见
        local_n = ranks_on_coord_node(n_ranks)
        leaves = max(0, local_n - 1)
        lat = concurrent_fanout_ms(
            rng,
            leaves,
            net.rtt_intra_ms,
            summary_bytes,
            net.bw_intra_gbps,
            net.proc_ms,
            net.concurrency,
            net.merge_us_per_peer,
        )
        return {
            "latency_ms": lat + net.proc_ms,
            "remote_peers": leaves,
            "complete": n_ranks <= GPUS_PER_NODE,
            "hops": "local0→on_node_leaves",
        }

    if scope == "Coordinator":
        # hierarchical：本机 Node 并行 + 远程 node aggregators（每机一次，体内含 Node）
        nn = n_nodes(n_ranks)
        local_n = ranks_on_coord_node(n_ranks)
        local_leaves = max(0, local_n - 1)
        t_local_node = concurrent_fanout_ms(
            rng,
            local_leaves,
            net.rtt_intra_ms,
            summary_bytes,
            net.bw_intra_gbps,
            net.proc_ms,
            net.concurrency,
            net.merge_us_per_peer,
        ) + net.proc_ms

        # 远程每机：RTT_inter + 该机 Node 聚合（满机 16，末机可能不满）
        remote_node_lats = []
        remaining = n_ranks - local_n
        while remaining > 0:
            on_node = min(GPUS_PER_NODE, remaining)
            leaves = max(0, on_node - 1)
            t_node = concurrent_fanout_ms(
                rng,
                leaves,
                net.rtt_intra_ms,
                summary_bytes,
                net.bw_intra_gbps,
                net.proc_ms,
                net.concurrency,
                net.merge_us_per_peer,
            ) + net.proc_ms
            # 远程调用：inter RTT + 传回节点聚合摘要（≈ on_node * summary）
            t_remote = peer_service_ms(
                rng,
                net.rtt_inter_ms,
                on_node * summary_bytes,
                net.bw_inter_gbps,
                t_node,  # 远端处理≈整机 Node 时间
                sigma=STRAGGLER_SIGMA,
            )
            remote_node_lats.append(t_remote)
            remaining -= on_node

        # 协调并发扇出到远程 aggregators（与本机 Node 并行）
        if remote_node_lats:
            c = max(1, net.concurrency)
            remote_wave = 0.0
            for i in range(0, len(remote_node_lats), c):
                remote_wave += max(remote_node_lats[i : i + c])
            remote_wave += (len(remote_node_lats) * net.merge_us_per_peer) / 1000.0
            lat = max(t_local_node, remote_wave)
        else:
            lat = t_local_node

        return {
            "latency_ms": lat,
            "remote_peers": max(0, nn - 1),
            "complete": True,
            "hops": "coord→node_aggs→leaves",
            "n_nodes": nn,
        }

    raise ValueError(scope)


def simulate_phase2_detail_ms(
    rng: random.Random,
    n_suspects: int,
    net: NetParams,
    detail_bytes: float,
    same_node_as_coord: bool = True,
) -> float:
    """Phase-2 仅 suspects DETAIL；通常 n=1，直连目标 rank。"""
    if n_suspects <= 0:
        return 0.0
    rtt = net.rtt_intra_ms if same_node_as_coord else net.rtt_inter_ms
    bw = net.bw_intra_gbps if same_node_as_coord else net.bw_inter_gbps
    # DETAIL 含 TT 时传占据主导；proc 略大
    return concurrent_fanout_ms(
        rng,
        n_suspects,
        rtt,
        detail_bytes,
        bw,
        net.proc_ms * 2.0,
        net.concurrency,
        net.merge_us_per_peer,
    )


def simulate_federated_ms(
    rng: random.Random,
    scope: str,
    n_ranks: int,
    net: NetParams,
    n_suspects: int = 1,
    summary_bytes: float = SUMMARY_BYTES_PER_RANK,
    detail_bytes: float = DETAIL_BYTES_PER_SUSPECT,
) -> dict:
    p1 = simulate_scope_phase1_ms(rng, scope, n_ranks, net, summary_bytes)
    p2 = simulate_phase2_detail_ms(
        rng,
        n_suspects if p1["complete"] else 0,
        net,
        detail_bytes,
        same_node_as_coord=(VICTIM < ranks_on_coord_node(n_ranks)),
    )
    total = p1["latency_ms"] + COORD_CPU_MS + p2
    return {
        "phase1_ms": p1["latency_ms"],
        "coord_ms": COORD_CPU_MS,
        "phase2_ms": p2,
        "total_ms": total,
        "remote_peers_phase1": p1["remote_peers"],
        "complete": p1["complete"],
        "hops": p1["hops"],
        **{k: v for k, v in p1.items() if k == "n_nodes"},
    }


def median_iqr(xs: list[float]) -> dict:
    if not xs:
        return {"median": None, "p25": None, "p75": None, "mean": None}
    xs = sorted(xs)
    return {
        "median": statistics.median(xs),
        "p25": xs[max(0, len(xs) // 4)],
        "p75": xs[min(len(xs) - 1, (3 * len(xs)) // 4)],
        "mean": statistics.mean(xs),
        "min": xs[0],
        "max": xs[-1],
    }


# ---------- 本机 HTTP 微基准（校准 Flat 并发）----------
class _Handler(BaseHTTPRequestHandler):
    payload = MICROBENCH_PAYLOAD

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", "0"))
        if n:
            self.rfile.read(n)
        body = self.payload
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        return


def _pad_summary_payload(nbytes: int) -> bytes:
    base = {"rank": 0, "status": "healthy", "step_ms_med": 12.3, "pad": ""}
    raw = json.dumps(base, separators=(",", ":")).encode()
    if len(raw) >= nbytes:
        return raw[:nbytes]
    base["pad"] = "x" * (nbytes - len(raw) - 8)
    out = json.dumps(base, separators=(",", ":")).encode()
    if len(out) < nbytes:
        out = out + b"x" * (nbytes - len(out))
    return out[:nbytes]


def run_local_http_microbench(
    n_peers: int,
    concurrency: int,
    payload_bytes: int,
    trials: int,
) -> dict:
    """本机 ThreadingHTTPServer × n_peers，并发 POST 测墙钟（校准用）。"""
    payload = _pad_summary_payload(payload_bytes)
    _Handler.payload = payload
    servers: list[ThreadingHTTPServer] = []
    threads: list[threading.Thread] = []
    ports: list[int] = []
    for _ in range(n_peers):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        ports.append(port)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
        threads.append(t)

    def one_fanout() -> float:
        urls = [f"http://127.0.0.1:{p}/query" for p in ports]
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futs = [
                ex.submit(
                    lambda u: urlopen(u, data=b"{}", timeout=REMOTE_TIMEOUT_S).read(),
                    u,
                )
                for u in urls
            ]
            for f in as_completed(futs):
                f.result()
        return (time.perf_counter() - t0) * 1000.0

    # warmup
    one_fanout()
    samples = [one_fanout() for _ in range(trials)]
    for srv in servers:
        srv.shutdown()
    return {
        "n_peers": n_peers,
        "concurrency": concurrency,
        "payload_bytes": payload_bytes,
        "trials": trials,
        "latency_ms": median_iqr(samples),
        "samples_ms": samples,
    }


def calibrate_from_microbench(mb: dict, net: NetParams) -> NetParams:
    """用本机 Flat 微基准反推有效 RTT_intra（含栈开销）。"""
    med = mb["latency_ms"]["median"]
    if med is None or mb["n_peers"] <= 0:
        return net
    # 单波（peers≤concurrency）：med ≈ rtt_eff + transfer + proc
    xfer = transfer_ms(mb["payload_bytes"], net.bw_intra_gbps)
    rtt_eff = max(0.05, med - xfer - net.proc_ms)
    return NetParams(
        rtt_intra_ms=rtt_eff,
        rtt_inter_ms=max(net.rtt_inter_ms, rtt_eff * 1.8),
        bw_intra_gbps=net.bw_intra_gbps,
        bw_inter_gbps=net.bw_inter_gbps,
        proc_ms=net.proc_ms,
        merge_us_per_peer=net.merge_us_per_peer,
        concurrency=net.concurrency,
        timeout_s=net.timeout_s,
    )


def design_scope_for_n(n: int) -> str:
    """连接数/完整性优先的设计默认（论文可写死的切换表）。"""
    if n <= 1:
        return "Local"
    if n <= GPUS_PER_NODE:
        return "Node"
    return "Coordinator"


def choose_switch_points(curve: list[dict]) -> dict:
    """设计推荐 + 延迟最优对照；跨机不以 Flat 短暂毫秒优势覆盖 O(N) 连接代价。"""
    by_n: dict[int, list[dict]] = {}
    for row in curve:
        if not row.get("complete"):
            continue
        by_n.setdefault(row["n_ranks"], []).append(row)

    per_n = {}
    for n, rows in sorted(by_n.items()):
        latency_best = min(
            rows,
            key=lambda r: (
                r["total_ms_median"],
                r["remote_peers_phase1"],
            ),
        )
        design = design_scope_for_n(n)
        design_row = next((r for r in rows if r["scope"] == design), latency_best)
        per_n[n] = {
            "recommended_scope": design,
            "recommended_ms_median": design_row["total_ms_median"],
            "recommended_remote_peers": design_row["remote_peers_phase1"],
            "latency_optimal_scope": latency_best["scope"],
            "latency_optimal_ms_median": latency_best["total_ms_median"],
            "alternatives": [
                {
                    "scope": r["scope"],
                    "total_ms_median": r["total_ms_median"],
                    "remote_peers_phase1": r["remote_peers_phase1"],
                }
                for r in sorted(rows, key=lambda x: x["total_ms_median"])
            ],
        }

    flat_vs_coord = []
    for n in sorted(by_n):
        rows = {r["scope"]: r for r in by_n[n]}
        if "Flat" in rows and "Coordinator" in rows:
            f, c = rows["Flat"], rows["Coordinator"]
            flat_vs_coord.append(
                {
                    "n_ranks": n,
                    "flat_ms": f["total_ms_median"],
                    "coord_ms": c["total_ms_median"],
                    "coord_wins_latency": c["total_ms_median"] < f["total_ms_median"],
                    "coord_peer_saving": f["remote_peers_phase1"] - c["remote_peers_phase1"],
                    "flat_peers": f["remote_peers_phase1"],
                    "coord_peers": c["remote_peers_phase1"],
                }
            )

    # 延迟交叉：首次 Coordinator 墙钟严格优于 Flat
    latency_cross = None
    for item in flat_vs_coord:
        if item["coord_wins_latency"]:
            latency_cross = item["n_ranks"]
            break

    # 设计切换：进入跨机即 Coordinator（N>gpus_per_node）
    design_switch = GPUS_PER_NODE + 1

    return {
        "per_n": {str(k): v for k, v in per_n.items()},
        "switch_flat_to_coordinator_at_n": design_switch,
        "latency_cross_flat_to_coordinator_at_n": latency_cross,
        "node_scope_max_n": GPUS_PER_NODE,
        "local_scope_max_n": 1,
        "rule": (
            "设计默认：N=1→Local；单机 N≤16→Node；跨机 N≥17→Coordinator。"
            "延迟交叉（coord_ms<flat_ms 的最小 N）仅作对照；"
            "N≤64 且并发=128 时 Flat 常仍单波、墙钟可略优，"
            "但连接数 O(N) vs Coordinator O(nodes)，跨机/万卡必须 Coordinator。"
        ),
        "flat_vs_coordinator": flat_vs_coord,
    }


def run_simulation(net: NetParams, n_list: list[int], trials: int) -> list[dict]:
    curve = []
    for n in n_list:
        for scope in SCOPES:
            samples = []
            meta = None
            for t in range(trials):
                scope_id = {"Local": 1, "Flat": 2, "Node": 3, "Coordinator": 4}[scope]
                rng = random.Random(RNG_SEED + 10007 * n + 97 * scope_id + t)
                r = simulate_federated_ms(rng, scope, n, net, n_suspects=1)
                samples.append(r["total_ms"])
                meta = r
            assert meta is not None
            stats = median_iqr(samples)
            curve.append(
                {
                    "n_ranks": n,
                    "scope": scope,
                    "n_nodes": n_nodes(n),
                    "complete": meta["complete"],
                    "hops": meta["hops"],
                    "remote_peers_phase1": meta["remote_peers_phase1"],
                    "total_ms_median": stats["median"],
                    "total_ms_p25": stats["p25"],
                    "total_ms_p75": stats["p75"],
                    "total_ms_mean": stats["mean"],
                    "phase1_ms_example": meta["phase1_ms"],
                    "phase2_ms_example": meta["phase2_ms"],
                    "coord_ms": meta["coord_ms"],
                    "n_trials": trials,
                }
            )
    return curve


def write_plot(curve: list[dict], out_dir: Path) -> str | None:
    """延迟-基数曲线 SVG（优先 lab plot_style，失败则纯 matplotlib）。"""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    try:
        import sys

        lw = Path(__file__).resolve().parents[5] / "lab-workspace" / "reports"
        # myportal/project/lab-workspace
        candidates = [
            Path("/Users/yinjinrun/Codespace/myportal/project/lab-workspace/reports"),
            Path.home() / "Codespace/myportal/project/lab-workspace/reports",
            lw,
        ]
        for c in candidates:
            if (c / "plot_style.py").is_file():
                sys.path.insert(0, str(c))
                import plot_style  # noqa: F401

                plot_style.apply()
                break
    except Exception:
        pass

    fig, ax = plt.subplots(figsize=(8.5, 5.2))
    markers = {"Local": "o", "Flat": "s", "Node": "^", "Coordinator": "D"}
    for scope in SCOPES:
        xs, ys = [], []
        for row in curve:
            if row["scope"] != scope or not row["complete"]:
                continue
            xs.append(row["n_ranks"])
            ys.append(row["total_ms_median"])
        if xs:
            ax.plot(xs, ys, marker=markers.get(scope, "o"), label=scope, linewidth=2)

    ax.set_xlabel("Rank cardinality N")
    ax.set_ylabel("Federated two-phase latency median (ms)")
    ax.set_title("4B fanout latency vs N (FanoutScope)")
    ax.set_xticks(list(N_SWEEP))
    ax.axvline(GPUS_PER_NODE + 0.5, color="0.4", linestyle="--", linewidth=1.2, label="multi-node →")
    ax.grid(True, axis="y", linestyle=":", alpha=0.35)
    ax.legend()
    fig.tight_layout()
    svg = out_dir / "fig_latency_vs_n.svg"
    fig.savefig(svg)
    plt.close(fig)
    return str(svg)


def write_outputs(
    out_dir: Path,
    curve: list[dict],
    switch: dict,
    net: NetParams,
    microbench: dict | None,
    mode: str,
    gaps: list[str],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    scored_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    fig = write_plot(curve, out_dir)

    # chosen：设计默认切换表
    chosen = {
        "fanout_scope_policy": {
            "N_eq_1": "Local",
            "N_le_16_single_node": "Node",  # 本机聚合；入口 Auto→local0 用 Coordinator≡Node
            "N_gt_16_multi_node": "Coordinator",
            "flat_allowed_when": "N≤16 或显式 --flat / HIERARCHICAL=0；跨机不推荐",
            "switch_flat_to_coordinator_at_n": switch["switch_flat_to_coordinator_at_n"],
            "latency_cross_flat_to_coordinator_at_n": switch.get(
                "latency_cross_flat_to_coordinator_at_n"
            ),
            "gpus_per_node_assumed": GPUS_PER_NODE,
        },
        "federation_defaults_confirmed": {
            "PROBING_FANOUT_CONCURRENCY": FANOUT_CONCURRENCY,
            "PROBING_REMOTE_QUERY_TIMEOUT_SECS": int(REMOTE_TIMEOUT_S),
            "PROBING_CLUSTER_FANOUT_HIERARCHICAL": 1,
        },
    }

    # 曲线要点
    highlights = []
    for n in N_SWEEP:
        rows = [r for r in curve if r["n_ranks"] == n and r["complete"]]
        if not rows:
            continue
        design = design_scope_for_n(n)
        design_row = next(r for r in rows if r["scope"] == design)
        flat = next((r for r in rows if r["scope"] == "Flat"), None)
        coord = next((r for r in rows if r["scope"] == "Coordinator"), None)
        highlights.append(
            {
                "n_ranks": n,
                "best_scope": design,
                "best_ms": design_row["total_ms_median"],
                "flat_ms": flat["total_ms_median"] if flat else None,
                "coord_ms": coord["total_ms_median"] if coord else None,
                "flat_peers": flat["remote_peers_phase1"] if flat else None,
                "coord_peers": coord["remote_peers_phase1"] if coord else None,
            }
        )

    param = {
        "param": "fanout_scope_switch_points",
        "exp_id": "4B_fanout_latency",
        "status": "DONE",
        "mode": mode,
        "harness": "scripts/fail-slow/param_calib/4b_fanout_latency.py",
        "criteria": "param_calib/4_health_summary_criteria/{CRITERIA.json,CRITERIA.md}",
        "upstream": "param_calib/4A_federated_denoise/",
        "swept_range": {
            "n_ranks": list(N_SWEEP),
            "fanout_scope": list(SCOPES),
        },
        "chosen_value": chosen,
        "choose_rule": switch["rule"],
        "controls": {
            "dose": DOSE,
            "dose_theta": DOSE_THETA,
            "cross_rank_theta": CROSS_RANK_THETA,
            "worst_fraction_phi": WORST_FRACTION_PHI,
            "W_star": W_STAR,
            "victim": VICTIM,
            "inject_window": [WINDOW_LO, WINDOW_HI],
            "aggregation": "federated_SUMMARY_then_suspect_DETAIL",
            "n_suspects": 1,
            "fanout_concurrency": FANOUT_CONCURRENCY,
            "remote_timeout_s": REMOTE_TIMEOUT_S,
            "gpus_per_node": GPUS_PER_NODE,
            "summary_bytes_per_rank": SUMMARY_BYTES_PER_RANK,
            "detail_bytes_per_suspect": DETAIL_BYTES_PER_SUSPECT,
            "forbid": [
                "fabricate live 64-rank cluster query",
                "open batch4 2C/3C",
                "touch yysong-master / a3 / song AFS",
            ],
        },
        "network_params": {
            "rtt_intra_ms": net.rtt_intra_ms,
            "rtt_inter_ms": net.rtt_inter_ms,
            "bw_intra_gbps": net.bw_intra_gbps,
            "bw_inter_gbps": net.bw_inter_gbps,
            "proc_ms": net.proc_ms,
            "merge_us_per_peer": net.merge_us_per_peer,
            "straggler_sigma": STRAGGLER_SIGMA,
            "calibrated_from_local_http_microbench": microbench is not None,
        },
        "ground_truth_source": {
            "criteria_locked": True,
            "volume_ratio_4A": 0.0626,
            "primary_case": PRIMARY_CASE,
            "primary_run": PRIMARY_RUN,
            "federation_code": [
                "probing/core/src/core/federation/fanout_scope.rs",
                "probing/core/src/core/federation/cluster_executor.rs",
                "probing/server/src/server/cluster_fanout.rs",
            ],
        },
        "measurements": {
            "curve": curve,
            "highlights": highlights,
            "switch_points": switch,
            "local_http_microbench": microbench,
            "figure": fig,
        },
        "supports_design": supports_sentence(switch, highlights),
        "gaps": gaps,
        "scored_at": scored_at,
        "blocked": False,
    }

    (out_dir / "PARAM.json").write_text(
        json.dumps(param, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "PARAM.md").write_text(
        render_md(param, fig), encoding="utf-8"
    )
    (out_dir / "DONE.md").write_text(
        render_done(param), encoding="utf-8"
    )


def supports_sentence(switch: dict, highlights: list[dict]) -> str:
    design_sw = switch.get("switch_flat_to_coordinator_at_n")
    lat_sw = switch.get("latency_cross_flat_to_coordinator_at_n")
    bits = []
    for h in highlights:
        bits.append(
            f"N={h['n_ranks']} design={h['best_scope']}@{h['best_ms']:.2f}ms "
            f"(flat={h['flat_ms']:.2f}, coord={h['coord_ms']:.2f}, "
            f"peers {h['flat_peers']}→{h['coord_peers']})"
        )
    return (
        "延迟-基数曲线："
        + "；".join(bits)
        + f"。设计切换 Flat/跨机→Coordinator @ N≥{design_sw}；"
        + (
            f"延迟交叉 coord<flat @ N={lat_sw}；"
            if lat_sw is not None
            else "本扫程 N≤64 延迟上 Flat 可略优于 Coordinator（并发128单波）；"
        )
        + "跨机以连接数 O(nodes) 为准选 Coordinator，勿因 N≤64 毫秒差退回 Flat。"
    )


def render_md(param: dict, fig: str | None) -> str:
    sw = param["measurements"]["switch_points"]
    lines = [
        "# ④-B 聚合延迟 vs 基数 / FanoutScope · DONE",
        "",
        f"> 状态：**DONE** · `4B_fanout_latency` · mode=`{param['mode']}` · {param['scored_at']}",
        f"> harness：`{param['harness']}`",
        "",
        "## 一句话",
        "",
        param["supports_design"],
        "",
        "## 自变量 / 控制",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 自变量 | N∈{param['swept_range']['n_ranks']}；FanoutScope∈{param['swept_range']['fanout_scope']} |",
        "| 聚合路径 | ④-A 联邦两阶段 SUMMARY→suspect→DETAIL |",
        f"| 并发 / 超时 | {FANOUT_CONCURRENCY} / {int(REMOTE_TIMEOUT_S)}s |",
        f"| 卡/机假设 | {GPUS_PER_NODE} |",
        f"| CRITERIA | loud θ*={DOSE_THETA}；①-B {CROSS_RANK_THETA}/{WORST_FRACTION_PHI} |",
        f"| n_suspects | 1（单 victim） |",
        "",
        "## 推荐 FanoutScope 切换点",
        "",
        "| 条件 | 推荐 scope |",
        "|---|---|",
        "| N = 1 | **Local** |",
        "| 单机 N ≤ 16 | **Node**（入口 Auto 在 local0 ≡ Coordinator 单机退化） |",
        "| 跨机 N > 16 | **Coordinator**（hierarchical） |",
        "| 显式扁平 | Flat 仅 N≤16 或调试；跨机不推荐 |",
        "",
        f"- **设计切换** 跨机→Coordinator：**N ≥ {sw.get('switch_flat_to_coordinator_at_n')}**",
        f"- 延迟交叉 coord_ms<flat_ms：**N = {sw.get('latency_cross_flat_to_coordinator_at_n')}**（对照；N≤64 可能 Flat 墙钟略优）",
        f"- 规则：{sw.get('rule')}",
        "",
        "## 延迟-基数曲线（中位 ms）",
        "",
    ]
    # table
    lines.append("| N | Local | Flat | Node | Coordinator | 设计推荐 | 延迟最优 | peers Flat→Coord |")
    lines.append("|---:|---:|---:|---:|---:|---|---|---:|")
    by = {}
    for row in param["measurements"]["curve"]:
        by[(row["n_ranks"], row["scope"])] = row
    for n in N_SWEEP:
        cells = []
        for scope in SCOPES:
            r = by.get((n, scope))
            if not r:
                cells.append("—")
            elif not r["complete"]:
                cells.append(f"({r['total_ms_median']:.2f}*)")
            else:
                cells.append(f"{r['total_ms_median']:.2f}")
        info = sw["per_n"].get(str(n), {})
        rec = info.get("recommended_scope", "—")
        lat_opt = info.get("latency_optimal_scope", "—")
        fp = by.get((n, "Flat"), {}).get("remote_peers_phase1", "—")
        cp = by.get((n, "Coordinator"), {}).get("remote_peers_phase1", "—")
        lines.append(
            f"| {n} | "
            + " | ".join(cells)
            + f" | **{rec}** | {lat_opt} | {fp}→{cp} |"
        )
    lines.append("")
    lines.append("> `*` = 该 scope 无法完整聚合全部 rank（Local 跨进程 / Node 跨机）。")
    lines.append("")
    if fig:
        lines += ["## 图", "", f"![latency vs N]({Path(fig).name})", ""]
    np_ = param["network_params"]
    lines += [
        "## 网络参数（诚实）",
        "",
        f"- mode=`{param['mode']}`",
        f"- RTT_intra≈**{np_['rtt_intra_ms']:.3f} ms**；RTT_inter≈**{np_['rtt_inter_ms']:.3f} ms**",
        f"- BW_intra={np_['bw_intra_gbps']} Gbps；BW_inter={np_['bw_inter_gbps']} Gbps",
        f"- 本机 HTTP 微基准校准：{np_['calibrated_from_local_http_microbench']}（含 Python HTTP 栈，偏高估真 RTT）",
        "",
        "## Flat vs Coordinator（连接数）",
        "",
        "| N | flat_ms | coord_ms | flat_peers | coord_peers | 省连接 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for item in sw.get("flat_vs_coordinator") or []:
        save = item["flat_peers"] - item["coord_peers"]
        lines.append(
            f"| {item['n_ranks']} | {item['flat_ms']:.2f} | {item['coord_ms']:.2f} | "
            f"{item['flat_peers']} | {item['coord_peers']} | {save} |"
        )
    lines += [
        "",
        "## 缺口",
        "",
    ]
    for g in param.get("gaps") or []:
        lines.append(f"- {g}")
    if not param.get("gaps"):
        lines.append("- （无）")
    lines += [
        "",
        "## 支撑设计决策",
        "",
        "扇出范围随规模切换：单机 Node、跨机 Coordinator；Flat 连接数 O(N) 在万卡不可扩展。",
        "本曲线在固定 CRITERIA 两阶段与并发128/30s 下给出延迟-基数证据；",
        "未伪造 live 64 卡 probing cluster query。",
        "",
    ]
    return "\n".join(lines)


def render_done(param: dict) -> str:
    sw = param["chosen_value"]["fanout_scope_policy"]
    return "\n".join(
        [
            "# DONE · 4B_fanout_latency",
            "",
            f"- status: {param['status']}",
            f"- mode: {param['mode']}",
            f"- design switch → Coordinator @ N≥{sw.get('switch_flat_to_coordinator_at_n')}",
            f"- latency cross Flat→Coordinator @ N={sw.get('latency_cross_flat_to_coordinator_at_n')}",
            f"- policy: N=1 Local；≤16 Node；≥17 Coordinator",
            f"- scored_at: {param['scored_at']}",
            "- 未开批次4（②-C/③-C）",
            "",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--results-root",
        type=Path,
        default=Path(__file__).resolve().parents[3]
        / "results"
        / "ascend-ais",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[3]
        / "results"
        / "ascend-ais"
        / "param_calib"
        / "4B_fanout_latency",
    )
    ap.add_argument("--trials", type=int, default=N_TRIALS)
    ap.add_argument("--skip-microbench", action="store_true")
    ap.add_argument("--rtt-intra-ms", type=float, default=DEFAULT_RTT_INTRA_MS)
    ap.add_argument("--rtt-inter-ms", type=float, default=DEFAULT_RTT_INTER_MS)
    ap.add_argument("--bw-intra-gbps", type=float, default=DEFAULT_BW_INTRA_GBPS)
    ap.add_argument("--bw-inter-gbps", type=float, default=DEFAULT_BW_INTER_GBPS)
    args = ap.parse_args()

    net = NetParams(
        rtt_intra_ms=args.rtt_intra_ms,
        rtt_inter_ms=args.rtt_inter_ms,
        bw_intra_gbps=args.bw_intra_gbps,
        bw_inter_gbps=args.bw_inter_gbps,
        proc_ms=DEFAULT_PROC_MS,
        merge_us_per_peer=DEFAULT_MERGE_US_PER_PEER,
    )

    microbench = None
    if not args.skip_microbench:
        # 校准：15 peer ≈ 单机 Flat（N=16）
        microbench = run_local_http_microbench(
            n_peers=15,
            concurrency=FANOUT_CONCURRENCY,
            payload_bytes=int(SUMMARY_BYTES_PER_RANK),
            trials=11,
        )
        net = calibrate_from_microbench(microbench, net)
        # 附加多点微基准曲线（本机 Flat）
        mb_curve = []
        for n in N_SWEEP:
            peers = max(0, n - 1)
            if peers == 0:
                continue
            mb_curve.append(
                run_local_http_microbench(
                    n_peers=min(peers, 64),
                    concurrency=FANOUT_CONCURRENCY,
                    payload_bytes=int(SUMMARY_BYTES_PER_RANK),
                    trials=7,
                )
            )
        microbench["flat_localhost_curve"] = [
            {
                "n_peers": x["n_peers"],
                "latency_ms_median": x["latency_ms"]["median"],
            }
            for x in mb_curve
        ]

    curve = run_simulation(net, list(N_SWEEP), args.trials)
    switch = choose_switch_points(curve)

    gaps = [
        "未跑 live 多机 probing-injected cluster query（grj 仅 2×16 空闲壳，无现成 64 卡注入作业；禁碰 yysong-master）",
        "N=32/64 延迟为 simulated_network（跳数+并发波+straggler），非伪造的 64 卡实测 jsonl",
        "本机 HTTP 微基准只校准 loopback 栈/并发，不替代跨 pod RTT",
    ]
    mode = "simulated_network+local_http_calib"

    write_outputs(args.out, curve, switch, net, microbench, mode, gaps)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "mode": mode,
                "design_switch_n": switch.get("switch_flat_to_coordinator_at_n"),
                "latency_cross_n": switch.get("latency_cross_flat_to_coordinator_at_n"),
                "highlights": [
                    {
                        "n_ranks": h["n_ranks"],
                        "design_scope": h["best_scope"],
                        "design_ms": round(h["best_ms"], 3),
                        "flat_ms": round(h["flat_ms"], 3) if h["flat_ms"] is not None else None,
                        "coord_ms": round(h["coord_ms"], 3) if h["coord_ms"] is not None else None,
                        "peers": f"{h['flat_peers']}→{h['coord_peers']}",
                    }
                    for h in [
                        {
                            "n_ranks": n,
                            "best_scope": design_scope_for_n(n),
                            "best_ms": next(
                                x["total_ms_median"]
                                for x in curve
                                if x["n_ranks"] == n and x["scope"] == design_scope_for_n(n)
                            ),
                            "flat_ms": next(
                                x["total_ms_median"]
                                for x in curve
                                if x["n_ranks"] == n and x["scope"] == "Flat"
                            ),
                            "coord_ms": next(
                                x["total_ms_median"]
                                for x in curve
                                if x["n_ranks"] == n and x["scope"] == "Coordinator"
                            ),
                            "flat_peers": next(
                                x["remote_peers_phase1"]
                                for x in curve
                                if x["n_ranks"] == n and x["scope"] == "Flat"
                            ),
                            "coord_peers": next(
                                x["remote_peers_phase1"]
                                for x in curve
                                if x["n_ranks"] == n and x["scope"] == "Coordinator"
                            ),
                        }
                        for n in N_SWEEP
                    ]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
