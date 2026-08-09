"""严格校验 skeleton JSONL / meta / counters / attempt lifecycle；fail-closed。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Optional


class StrictValidationError(RuntimeError):
    pass


REQUIRED_DROP_KEYS = (
    "allocation",
    "queue",
    "parse",
    "io",
    "callback",
    "mspti",
)

EXPLICIT_FINALIZE_REASONS = frozenset(
    {"last_train_step", "train.finally", "pretrain.finally"}
)

POST_TRAIN_DONE_MARKER = "after training is done"

SIGSEGV_PATTERNS = (
    re.compile(r"fatal signal SIGSEGV", re.IGNORECASE),
    re.compile(r"Segmentation fault", re.IGNORECASE),
)


def read_jsonl_strict(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise StrictValidationError(f"missing {path}")
    if path.stat().st_size <= 0:
        raise StrictValidationError(f"empty {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        if not line.startswith("{"):
            raise StrictValidationError(f"{path.name}:{line_number} non-json line")
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise StrictValidationError(
                f"{path.name}:{line_number} JSONDecodeError: {exc}"
            ) from exc
    if not rows:
        raise StrictValidationError(f"{path.name} has no records")
    return rows


def _read_meta(run_dir: Path, rank: int) -> dict[str, Any]:
    path = run_dir / f"rank_{rank:04d}.mspti_meta.json"
    if not path.exists():
        raise StrictValidationError(f"missing meta {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_drop_flags(flags: str) -> dict[str, int]:
    """Strictly parse DROP flags; every required component must be present."""
    parts = [p for p in str(flags).split(";") if p]
    parsed: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            raise StrictValidationError(f"DROP flags malformed token: {part!r}")
        key, value = part.split("=", 1)
        parsed[key] = value
    missing = [k for k in REQUIRED_DROP_KEYS if k not in parsed]
    if missing:
        raise StrictValidationError(f"DROP flags missing components: {missing}")
    if "incomplete" not in parsed:
        raise StrictValidationError("DROP flags missing incomplete=")
    if "finalize" not in parsed:
        raise StrictValidationError("DROP flags missing finalize=")
    out: dict[str, int] = {}
    for key in REQUIRED_DROP_KEYS:
        try:
            out[key] = int(parsed[key])
        except ValueError as exc:
            raise StrictValidationError(f"DROP {key} not int: {parsed[key]!r}") from exc
        if out[key] < 0:
            raise StrictValidationError(f"DROP {key} negative: {out[key]}")
    out["incomplete"] = int(parsed["incomplete"])
    out["finalize"] = int(parsed["finalize"])
    return out


def _robust_center(values: list[int]) -> float:
    """Median as robust center across ranks."""
    if not values:
        raise StrictValidationError("empty values for robust center")
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2])


def validate_rank_rows(
    rows: list[dict[str, Any]],
    *,
    expected_rank: int,
    capture_step: int,
    meta: Optional[dict[str, Any]] = None,
    min_raw_kernels: Optional[int] = None,
    min_comm: Optional[int] = None,
    min_kseg: int = 1,
    require_explicit_finalize: bool = True,
) -> dict[str, Any]:
    kinds = Counter(str(r["kind"]) for r in rows)
    begins = [
        r
        for r in rows
        if r["kind"] == "STEP" and "phase=begin" in str(r.get("flags", ""))
    ]
    ends = [
        r
        for r in rows
        if r["kind"] == "STEP" and "phase=end" in str(r.get("flags", ""))
    ]
    drops = [r for r in rows if r["kind"] == "DROP"]
    if len(begins) != 1 or len(ends) != 1:
        raise StrictValidationError(
            f"rank{expected_rank}: begin/end STEP must be exactly once "
            f"(begin={len(begins)} end={len(ends)})"
        )
    if int(begins[0]["step"]) != capture_step or int(ends[0]["step"]) != capture_step:
        raise StrictValidationError(
            f"rank{expected_rank}: capture step mismatch "
            f"begin={begins[0]['step']} end={ends[0]['step']} want={capture_step}"
        )
    if len(drops) != 1:
        raise StrictValidationError(f"rank{expected_rank}: need exactly one DROP row")

    # DROP must be the final row AND carry the maximum seq for this rank.
    drop_row = drops[0]
    if rows[-1] is not drop_row and rows[-1].get("kind") != "DROP":
        raise StrictValidationError(
            f"rank{expected_rank}: DROP is not the last event "
            f"(last_kind={rows[-1].get('kind')})"
        )
    if rows[-1].get("kind") != "DROP":
        raise StrictValidationError(
            f"rank{expected_rank}: last row kind={rows[-1].get('kind')} != DROP"
        )
    drop_seq = int(drop_row.get("seq", -1))
    max_seq = max(int(r.get("seq", -1)) for r in rows)
    if drop_seq != max_seq:
        raise StrictValidationError(
            f"rank{expected_rank}: DROP.seq={drop_seq} != max seq={max_seq}"
        )
    # Any event after DROP is forbidden (already implied by last-row check;
    # keep explicit scan for clearer negatives).
    drop_index = next(i for i, r in enumerate(rows) if r.get("kind") == "DROP")
    if drop_index != len(rows) - 1:
        trailing = [str(r.get("kind")) for r in rows[drop_index + 1 :]]
        raise StrictValidationError(
            f"rank{expected_rank}: events after DROP: {trailing}"
        )

    drop_flags = parse_drop_flags(str(drop_row.get("flags", "")))
    drop_count = int(drop_row.get("count", -1))
    component_sum = sum(drop_flags[k] for k in REQUIRED_DROP_KEYS)
    if drop_count != component_sum:
        raise StrictValidationError(
            f"rank{expected_rank}: DROP.count={drop_count} != sum(components)={component_sum} "
            f"flags={drop_row.get('flags')}"
        )
    if drop_count != 0:
        raise StrictValidationError(
            f"rank{expected_rank}: drop_count={drop_count} != 0 flags={drop_row.get('flags')}"
        )
    for key in REQUIRED_DROP_KEYS:
        if drop_flags[key] != 0:
            raise StrictValidationError(
                f"rank{expected_rank}: DROP.{key}={drop_flags[key]} != 0"
            )
    if drop_flags["incomplete"] != 0:
        raise StrictValidationError(f"rank{expected_rank}: incomplete=1 in DROP flags")
    if drop_flags["finalize"] != 1:
        raise StrictValidationError(f"rank{expected_rank}: DROP missing finalize=1 flag")

    active_gt_span = 0
    gap_neg = 0
    raw_from_kseg = 0
    for r in rows:
        if r["kind"] != "KSEG":
            continue
        active = int(r.get("active_ns", 0))
        span = int(r.get("span_ns", 0))
        gap = int(r.get("gap_ns", 0))
        raw_from_kseg += int(r.get("count", 0))
        if active > span:
            active_gt_span += 1
        if gap < 0:
            gap_neg += 1
    if active_gt_span:
        raise StrictValidationError(
            f"rank{expected_rank}: {active_gt_span} KSEG with active_ns>span_ns"
        )
    if gap_neg:
        raise StrictValidationError(f"rank{expected_rank}: {gap_neg} KSEG with gap_ns<0")
    for r in rows:
        if int(r["rank"]) != expected_rank:
            raise StrictValidationError(
                f"rank{expected_rank}: row rank={r['rank']} mismatch"
            )

    kseg = kinds.get("KSEG", 0)
    comm = kinds.get("COMM", 0) + kinds.get("P2P", 0)
    if kseg < min_kseg:
        raise StrictValidationError(
            f"rank{expected_rank}: KSEG={kseg} < min_kseg={min_kseg}"
        )
    if min_comm is not None and comm < min_comm:
        raise StrictValidationError(
            f"rank{expected_rank}: COMM+P2P={comm} < min_comm={min_comm}"
        )

    if meta is None:
        raise StrictValidationError(f"rank{expected_rank}: meta required")
    if not meta.get("finalize_complete", False):
        raise StrictValidationError(
            f"rank{expected_rank}: meta.finalize_complete is not true"
        )
    if int(meta.get("finalize_rc", -1)) != 0:
        raise StrictValidationError(
            f"rank{expected_rank}: meta.finalize_rc={meta.get('finalize_rc')}"
        )
    if meta.get("incomplete"):
        raise StrictValidationError(f"rank{expected_rank}: meta.incomplete=true")
    if meta.get("armed_fail"):
        raise StrictValidationError(f"rank{expected_rank}: meta.armed_fail=true")
    if meta.get("capture_end_rc") not in (None, 0):
        raise StrictValidationError(
            f"rank{expected_rank}: capture_end_rc={meta.get('capture_end_rc')}"
        )

    meta_raw = meta.get("raw_kernels")
    if meta_raw is None:
        raise StrictValidationError(f"rank{expected_rank}: meta.raw_kernels missing")
    meta_raw_i = int(meta_raw)
    if meta_raw_i != raw_from_kseg:
        raise StrictValidationError(
            f"rank{expected_rank}: meta.raw_kernels={meta_raw_i} != "
            f"sum(KSEG.count)={raw_from_kseg}"
        )

    if require_explicit_finalize:
        reason = str(meta.get("finalize_reason") or "")
        if reason not in EXPLICIT_FINALIZE_REASONS:
            raise StrictValidationError(
                f"rank{expected_rank}: finalize_reason={reason!r} not explicit "
                f"(need one of {sorted(EXPLICIT_FINALIZE_REASONS)}; atexit rejected)"
            )

    # Performance fields must be present and non-negative (smoke: no perf conclusion).
    for key in (
        "finalize_ms",
        "finalize_flush_ms",
        "finalize_drain_ms",
        "capture_begin_ms",
        "capture_end_ms",
        "collector_start_ms",
    ):
        if key not in meta or meta[key] is None:
            raise StrictValidationError(f"rank{expected_rank}: meta.{key} null/missing")
        if float(meta[key]) < 0:
            raise StrictValidationError(
                f"rank{expected_rank}: meta.{key}={meta[key]} negative"
            )

    if min_raw_kernels is not None and meta_raw_i < min_raw_kernels:
        raise StrictValidationError(
            f"rank{expected_rank}: raw_kernels={meta_raw_i} < min={min_raw_kernels}"
        )

    return {
        "rank": expected_rank,
        "events": len(rows),
        "kinds": dict(kinds),
        "kseg": kseg,
        "comm": comm,
        "raw_kernels": meta_raw_i,
        "raw_from_kseg_count": raw_from_kseg,
        "finalize_reason": meta.get("finalize_reason"),
        "drop_flags": drop_flags,
    }


def validate_ours_dir(
    run_dir: Path,
    *,
    expected_ranks: int = 32,
    capture_step: int = 10,
    require_meta: bool = True,
    min_raw_kernels: Optional[int] = None,
    min_comm: Optional[int] = None,
    min_kseg: int = 1,
    relative_raw_floor: float = 0.8,
    relative_comm_floor: float = 0.8,
    require_explicit_finalize: bool = True,
    frozen_thresholds: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Absolute floors come from frozen pre-run config only (never self-reference alone).

    relative_*_floor: each rank >= floor * robust_center(cross-rank).
    """
    if not require_meta:
        raise StrictValidationError("strict path requires meta")

    # Prefer frozen thresholds from config/group_config if present.
    frozen = dict(frozen_thresholds or {})
    cfg_path = run_dir / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        ft = cfg.get("frozen_thresholds") or cfg.get("strict_thresholds") or {}
        if isinstance(ft, dict):
            frozen = {**ft, **frozen}
    if min_raw_kernels is None and frozen.get("min_raw_kernels_per_rank") is not None:
        min_raw_kernels = int(frozen["min_raw_kernels_per_rank"])
    if min_comm is None and frozen.get("min_comm_per_rank") is not None:
        min_comm = int(frozen["min_comm_per_rank"])
    if frozen.get("relative_raw_floor") is not None:
        relative_raw_floor = float(frozen["relative_raw_floor"])
    if frozen.get("relative_comm_floor") is not None:
        relative_comm_floor = float(frozen["relative_comm_floor"])

    if min_raw_kernels is None:
        raise StrictValidationError(
            "strict requires frozen min_raw_kernels_per_rank (CLI/config); "
            "self-referenced median alone is forbidden"
        )
    if min_comm is None:
        raise StrictValidationError(
            "strict requires frozen min_comm_per_rank (CLI/config)"
        )

    per_rank = []
    for rank in range(expected_ranks):
        path = run_dir / f"rank_{rank:04d}.skeleton.jsonl"
        rows = read_jsonl_strict(path)
        meta = _read_meta(run_dir, rank)
        per_rank.append(
            validate_rank_rows(
                rows,
                expected_rank=rank,
                capture_step=capture_step,
                meta=meta,
                min_raw_kernels=min_raw_kernels,
                min_comm=min_comm,
                min_kseg=min_kseg,
                require_explicit_finalize=require_explicit_finalize,
            )
        )
    extras = sorted(run_dir.glob("rank_*.skeleton.jsonl"))
    if len(extras) != expected_ranks:
        raise StrictValidationError(
            f"expected {expected_ranks} skeleton files, found {len(extras)}"
        )

    raws = [int(p["raw_kernels"]) for p in per_rank]
    comms = [int(p["comm"]) for p in per_rank]
    if not raws or min(raws) <= 0:
        raise StrictValidationError(f"raw_kernels must be >0 on every rank: {raws}")
    if not comms or min(comms) < 0:
        raise StrictValidationError(f"comm invalid: {comms}")

    raw_center = _robust_center(raws)
    comm_center = _robust_center(comms)
    raw_rel = max(1, int(raw_center * relative_raw_floor))
    comm_rel = max(0, int(comm_center * relative_comm_floor))
    raw_floor = max(int(min_raw_kernels), raw_rel)
    comm_floor = max(int(min_comm), comm_rel)

    for p in per_rank:
        if int(p["raw_kernels"]) < raw_floor:
            raise StrictValidationError(
                f"rank{p['rank']}: raw_kernels={p['raw_kernels']} < floor={raw_floor} "
                f"(center={raw_center}, relative={relative_raw_floor}, "
                f"min_raw_kernels={min_raw_kernels})"
            )
        if int(p["comm"]) < comm_floor:
            raise StrictValidationError(
                f"rank{p['rank']}: COMM+P2P={p['comm']} < floor={comm_floor} "
                f"(center={comm_center}, relative={relative_comm_floor}, "
                f"min_comm={min_comm})"
            )

    return {
        "expected_ranks": expected_ranks,
        "capture_step": capture_step,
        "per_rank": per_rank,
        "raw_kernel_center": raw_center,
        "raw_kernel_floor": raw_floor,
        "raw_kernel_min": min(raws),
        "raw_kernel_max": max(raws),
        "comm_center": comm_center,
        "comm_floor": comm_floor,
        "comm_min": min(comms),
        "comm_max": max(comms),
        "frozen_min_raw_kernels": min_raw_kernels,
        "frozen_min_comm": min_comm,
        "ok": True,
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_safe_rel_path(rel: str) -> bool:
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        return False
    if Path(rel).is_absolute():
        return False
    if ".." in Path(rel).parts:
        return False
    return True


def _canonical_aggregate(files: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for entry in sorted(files, key=lambda e: str(e["path"])):
        h.update(str(entry["path"]).encode())
        h.update(b"\0")
        h.update(str(entry["sha256"]).encode())
        h.update(b"\n")
    return h.hexdigest()


REQUIRED_ARTIFACT_BASENAMES = (
    "counters.json",
    "config.json",
    "run.log",
)

REQUIRED_ARTIFACT_GLOBS = (
    "rank_*.skeleton.jsonl",
    "rank_*.mspti_meta.json",
    "rank_*.trace.json",
    "node_*.done",
    "node_*.launch.json",
    "node_*.log",
    "node_*.exit",
    "launch_*.log",
    "cluster.trace.json",
)


def _is_symlink_escape(path: Path, root: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        path.resolve().relative_to(root.resolve())
    except Exception:
        return True
    return False


def _file_mode_octal(path: Path) -> int:
    return int(path.stat().st_mode) & 0o777


def verify_sealed_collector_so(
    attempt_dir: Path,
    *,
    manifest: Optional[dict[str, Any]] = None,
    require_readonly: bool = True,
) -> dict[str, Any]:
    """Resolve collector_so_sealed_relpath safely and match actual sha256/size/mode."""
    attempt_dir = Path(attempt_dir)
    if manifest is None:
        man_path = attempt_dir / "attempt_manifest.json"
        if not man_path.exists():
            raise StrictValidationError("missing attempt_manifest.json for SO verify")
        manifest = json.loads(man_path.read_text(encoding="utf-8"))

    arm = str(manifest.get("arm") or "").lower()
    build_path = attempt_dir / "provenance_build.json"
    build: dict[str, Any] = {}
    if build_path.exists():
        build = json.loads(build_path.read_text(encoding="utf-8"))

    claimed = (
        (manifest.get("provenance") or {}).get("collector_so_sha256")
        or manifest.get("collector_so_sha256")
        or build.get("collector_so_sha256")
    )
    rel = build.get("collector_so_sealed_relpath") or manifest.get(
        "collector_so_sealed_relpath"
    )
    if arm == "ours" or rel or claimed:
        if not claimed:
            raise StrictValidationError("missing claimed collector_so_sha256")
        if not rel:
            cands = sorted(attempt_dir.glob("sealed_bins/libmspti_sync_skeleton.so.*"))
            cands = [p for p in cands if p.is_file() and not p.is_symlink()]
            if len(cands) != 1:
                raise StrictValidationError(
                    f"sealed SO relpath missing and candidates={len(cands)}"
                )
            rel = str(cands[0].relative_to(attempt_dir))
        if not _is_safe_rel_path(str(rel)):
            raise StrictValidationError(f"unsafe sealed SO relpath: {rel}")
        so_path = attempt_dir / str(rel)
        if so_path.is_symlink() or _is_symlink_escape(so_path, attempt_dir):
            raise StrictValidationError(f"sealed SO symlink/escape: {rel}")
        try:
            so_path.resolve().relative_to(attempt_dir.resolve())
        except Exception as exc:
            raise StrictValidationError(f"sealed SO escapes attempt dir: {rel}") from exc
        if not so_path.is_file():
            raise StrictValidationError(f"missing sealed SO file: {rel}")
        name = so_path.name
        if not name.startswith("libmspti_sync_skeleton.so."):
            raise StrictValidationError(f"sealed SO name not hash-addressed: {name}")
        suffix = name.split("libmspti_sync_skeleton.so.", 1)[-1]
        if len(suffix) != 64 or any(c not in "0123456789abcdef" for c in suffix):
            raise StrictValidationError(f"sealed SO name hash suffix invalid: {name}")

        actual = _sha256_file(so_path)
        size = so_path.stat().st_size
        mode = _file_mode_octal(so_path)
        if actual != str(claimed):
            raise StrictValidationError(
                f"sealed SO actual sha256 {actual} != claimed {claimed}"
            )
        if suffix != actual:
            raise StrictValidationError(
                f"sealed SO filename hash {suffix} != actual {actual}"
            )
        claimed_size = build.get("collector_so_size")
        if claimed_size is not None and int(claimed_size) != size:
            raise StrictValidationError(
                f"sealed SO size mismatch listed={claimed_size} actual={size}"
            )
        if require_readonly and (mode & 0o222) != 0:
            raise StrictValidationError(
                f"sealed SO not readonly (mode={oct(mode)}; want write bits clear)"
            )
        if build.get("collector_so_sha256") and str(build["collector_so_sha256"]) != actual:
            raise StrictValidationError("provenance_build collector_so_sha256 mismatch")

        # ours: every expected node MUST record non-empty loaded hash + path.
        # Missing fields → FAIL immediately (not optional). normal/torch skip.
        arm_l = str(manifest.get("arm") or "").lower()
        require_loaded = arm_l == "ours"
        expected_nodes = int(
            manifest.get("expected_nodes")
            or manifest.get("nnodes")
            or 0
        )
        node_launch = manifest.get("node_launch") or {}

        def _check_loaded_rec(label: str, rec: dict) -> None:
            loaded = rec.get("collector_so_sha256_loaded")
            path = rec.get("collector_so_loaded_path") or rec.get("collector_so_path")
            if require_loaded:
                if not loaded:
                    raise StrictValidationError(
                        f"{label}: missing required collector_so_sha256_loaded for ours"
                    )
                if not path:
                    raise StrictValidationError(
                        f"{label}: missing required collector_so_loaded_path for ours"
                    )
                if str(loaded) != actual:
                    raise StrictValidationError(
                        f"{label}: loaded_so_sha256 {loaded} != actual sealed {actual}"
                    )
                path_s = str(path)
                base = Path(path_s).name
                # must be hash-named: libmspti_sync_skeleton.so.<sha256>
                if not (
                    base.startswith("libmspti_sync_skeleton.so.")
                    and actual in base
                    and base != "libmspti_sync_skeleton.so"
                ):
                    raise StrictValidationError(
                        f"{label}: collector_so_loaded_path not hash-named sealed SO: {path_s}"
                    )
            elif loaded is not None and str(loaded) != actual:
                raise StrictValidationError(
                    f"{label}: loaded_so_sha256 {loaded} != actual sealed {actual}"
                )

        if require_loaded and expected_nodes > 0:
            for node_id in range(expected_nodes):
                key_candidates = (
                    f"node_{node_id}",
                    f"node_{node_id}.launch.json",
                    str(node_id),
                )
                rec = None
                for key in key_candidates:
                    if key in node_launch and isinstance(node_launch[key], dict):
                        rec = node_launch[key]
                        break
                if rec is None:
                    lp = attempt_dir / f"node_{node_id}.launch.json"
                    if lp.exists():
                        rec = json.loads(lp.read_text(encoding="utf-8"))
                if rec is None:
                    raise StrictValidationError(
                        f"node_{node_id}: missing launch/provenance for required loaded SO"
                    )
                _check_loaded_rec(f"node_{node_id}", rec)
        else:
            for key, rec in sorted(node_launch.items()):
                if isinstance(rec, dict):
                    _check_loaded_rec(key, rec)
            for lp in sorted(attempt_dir.glob("node_*.launch.json")):
                rec = json.loads(lp.read_text(encoding="utf-8"))
                _check_loaded_rec(lp.name, rec)

        art_path = attempt_dir / "artifact_digest.json"
        if art_path.exists():
            art = json.loads(art_path.read_text(encoding="utf-8"))
            files = art.get("files") or []
            hits = [e for e in files if str(e.get("path")) == str(rel)]
            if not hits:
                raise StrictValidationError(
                    f"required sealed SO {rel} missing from artifact_digest.files"
                )
            entry = hits[0]
            if str(entry.get("sha256")) != actual or int(entry.get("size", -1)) != size:
                raise StrictValidationError(
                    f"artifact entry for sealed SO does not match actual bytes: {rel}"
                )

        return {
            "relpath": str(rel),
            "sha256": actual,
            "size": size,
            "mode": mode,
            "ok": True,
        }
    return {"ok": True, "skipped": True}


def collect_required_artifact_names(
    attempt_dir: Path,
    *,
    arm: Optional[str] = None,
    expected_ranks: Optional[int] = None,
    expected_nodes: Optional[int] = None,
) -> set[str]:
    """Strict required set for the current arm.

    Every required name must appear in artifact files[] with matching hash/size.
    Paths may be nested (e.g. torch_prof_node0/.../trace_view.json).
    """
    required: set[str] = set()
    arm_l = (arm or "").lower()

    required.add("config.json")
    required.add("run.log")
    if arm_l == "ours" or (attempt_dir / "counters.json").exists():
        if arm_l == "ours":
            required.add("counters.json")
        elif (attempt_dir / "counters.json").exists():
            required.add("counters.json")

    for pattern in REQUIRED_ARTIFACT_GLOBS:
        for path in attempt_dir.glob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            name = path.name
            if arm_l in ("normal", "torch"):
                if name.endswith((".skeleton.jsonl", ".mspti_meta.json", ".trace.json")):
                    continue
                if name == "cluster.trace.json":
                    continue
            required.add(name)

    nodes = expected_nodes
    if nodes is None:
        nodes = 0
        for p in attempt_dir.glob("node_*.done"):
            try:
                nodes = max(nodes, int(p.stem.split("_")[1]) + 1)
            except Exception:
                pass
    if nodes:
        for n in range(int(nodes)):
            for name in (f"node_{n}.done", f"node_{n}.launch.json", f"node_{n}.log"):
                required.add(name)

    if arm_l == "ours" and expected_ranks:
        for r in range(int(expected_ranks)):
            required.add(f"rank_{r:04d}.skeleton.jsonl")
            required.add(f"rank_{r:04d}.mspti_meta.json")
        if (attempt_dir / "cluster.trace.json").exists() or arm_l == "ours":
            required.add("cluster.trace.json")

    # Torch: recursive primary traces must be sealed (non-empty).
    if arm_l == "torch":
        traces = sorted(
            p
            for p in attempt_dir.glob("torch_prof_node*/**/trace_view.json")
            if p.is_file() and not p.is_symlink()
        )
        if not traces:
            # Also accept flat ASCEND_PROFILER_OUTPUT layout under torch_prof_node*
            traces = sorted(
                p
                for p in attempt_dir.rglob("trace_view.json")
                if p.is_file()
                and not p.is_symlink()
                and any(part.startswith("torch_prof_node") for part in p.parts)
            )
        for p in traces:
            try:
                rel = str(p.relative_to(attempt_dir))
            except ValueError:
                continue
            if not _is_safe_rel_path(rel):
                raise StrictValidationError(f"unsafe torch trace path: {rel}")
            if p.stat().st_size <= 0:
                raise StrictValidationError(f"empty torch primary trace: {rel}")
            required.add(rel)

    # Sealed collector SO copy (content-addressed) when present / ours arm.
    so_dir = attempt_dir / "sealed_bins"
    if so_dir.is_dir() or arm_l == "ours":
        for p in sorted(attempt_dir.glob("sealed_bins/libmspti_sync_skeleton.so.*")):
            if p.is_file() and not p.is_symlink():
                required.add(str(p.relative_to(attempt_dir)))
        # Prefer explicit sealed path from provenance_build if available.
        build = attempt_dir / "provenance_build.json"
        if build.exists():
            try:
                bo = json.loads(build.read_text(encoding="utf-8"))
                sealed = bo.get("collector_so_sealed_relpath")
                if sealed:
                    if not _is_safe_rel_path(str(sealed)):
                        raise StrictValidationError(f"unsafe sealed SO path: {sealed}")
                    required.add(str(sealed))
            except StrictValidationError:
                raise
            except Exception:
                pass

    return required


def verify_artifact_digest(
    attempt_dir: Path,
    *,
    expected_aggregate: Optional[str] = None,
    require_required_globs: bool = True,
    arm: Optional[str] = None,
    expected_ranks: Optional[int] = None,
    expected_nodes: Optional[int] = None,
) -> dict[str, Any]:
    """Strict per-entry re-read + bidirectional required coverage."""
    art_path = attempt_dir / "artifact_digest.json"
    if not art_path.exists():
        raise StrictValidationError("missing artifact_digest.json")
    art = json.loads(art_path.read_text(encoding="utf-8"))
    files = art.get("files")
    if not isinstance(files, list) or not files:
        raise StrictValidationError("artifact_digest.files empty or missing")
    seen: set[str] = set()
    recomputed: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise StrictValidationError("artifact entry not object")
        rel = str(entry.get("path", ""))
        if not _is_safe_rel_path(rel):
            raise StrictValidationError(f"unsafe artifact path: {rel!r}")
        if rel in seen:
            raise StrictValidationError(f"duplicate artifact path: {rel}")
        seen.add(rel)
        path = attempt_dir / rel
        if path.is_symlink() or _is_symlink_escape(path, attempt_dir):
            raise StrictValidationError(f"symlink or escape artifact path: {rel}")
        try:
            path.resolve().relative_to(attempt_dir.resolve())
        except Exception as exc:
            raise StrictValidationError(f"path escapes attempt dir: {rel}") from exc
        if not path.is_file():
            raise StrictValidationError(f"missing artifact file: {rel}")
        size = path.stat().st_size
        digest = _sha256_file(path)
        if int(entry.get("size", -1)) != size:
            raise StrictValidationError(
                f"artifact size mismatch {rel}: listed={entry.get('size')} actual={size}"
            )
        if str(entry.get("sha256")) != digest:
            raise StrictValidationError(f"artifact hash mismatch: {rel}")
        recomputed.append({"path": rel, "sha256": digest, "size": size})

    aggregate = _canonical_aggregate(recomputed)
    listed = art.get("aggregate_sha256") or art.get("artifact_digest_sha256")
    if not listed:
        raise StrictValidationError("artifact_digest missing aggregate_sha256")
    if listed != aggregate:
        raise StrictValidationError(
            f"artifact aggregate mismatch: listed={listed} recomputed={aggregate}"
        )
    file_hash = _sha256_file(art_path)
    if expected_aggregate is not None and expected_aggregate == file_hash and file_hash != aggregate:
        raise StrictValidationError(
            "manifest artifact_digest_sha256 equals artifact_digest.json file hash "
            "but not aggregate — wrong semantics"
        )
    if expected_aggregate is not None and expected_aggregate != aggregate:
        raise StrictValidationError(
            "manifest provenance.artifact_digest_sha256 != recomputed aggregate"
        )

    if require_required_globs:
        arm_use = arm
        ranks_use = expected_ranks
        nodes_use = expected_nodes
        man_path = attempt_dir / "attempt_manifest.json"
        if man_path.exists():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
                arm_use = arm_use or man.get("arm")
                ranks_use = ranks_use if ranks_use is not None else man.get("expected_ranks")
                nodes_use = nodes_use if nodes_use is not None else (
                    man.get("expected_nodes") or man.get("nnodes")
                )
            except Exception:
                pass
        required = collect_required_artifact_names(
            attempt_dir,
            arm=arm_use,
            expected_ranks=ranks_use,
            expected_nodes=nodes_use,
        )
        missing_on_disk = [n for n in sorted(required) if not (attempt_dir / n).is_file()]
        if missing_on_disk:
            raise StrictValidationError(
                f"required artifact missing on disk: {missing_on_disk[:8]}"
            )
        missing_in_digest = [n for n in sorted(required) if n not in seen]
        if missing_in_digest:
            raise StrictValidationError(
                f"required artifact not in digest files[]: {missing_in_digest[:8]}"
            )
        # Torch: every non-empty primary trace on disk must be covered by digest.
        if (arm_use or "").lower() == "torch":
            disk_traces = {
                str(p.relative_to(attempt_dir))
                for p in attempt_dir.rglob("trace_view.json")
                if p.is_file()
                and not p.is_symlink()
                and any(part.startswith("torch_prof_node") for part in p.parts)
                and p.stat().st_size > 0
            }
            if not disk_traces:
                raise StrictValidationError(
                    "torch arm requires at least one non-empty trace_view.json under torch_prof_node*"
                )
            uncovered = sorted(disk_traces - seen)
            if uncovered:
                raise StrictValidationError(
                    f"torch primary traces not in digest: {uncovered[:8]}"
                )

    return {
        "aggregate_sha256": aggregate,
        "file_count": len(recomputed),
        "artifact_digest_file_sha256": file_hash,
        "ok": True,
    }



def verify_local_anchor(
    attempt_dir: Path,
    *,
    require: bool = True,
) -> Optional[dict[str, Any]]:
    """Formal accept gate: LOCAL_VERIFIED_SEAL.json written by parent after pullback."""
    path = attempt_dir / "LOCAL_VERIFIED_SEAL.json"
    if not path.exists():
        if require:
            raise StrictValidationError(
                "missing LOCAL_VERIFIED_SEAL.json (formal mode requires parent local anchor)"
            )
        return None
    seal = json.loads(path.read_text(encoding="utf-8"))
    man = attempt_dir / "attempt_manifest.json"
    if not man.exists():
        raise StrictValidationError("LOCAL_VERIFIED_SEAL present but no attempt_manifest.json")
    man_hash = _sha256_file(man)
    if seal.get("local_manifest_sha256") != man_hash:
        raise StrictValidationError("LOCAL_VERIFIED_SEAL local_manifest_sha256 mismatch")
    # Re-verify aggregate still matches
    art = verify_artifact_digest(
        attempt_dir,
        expected_aggregate=seal.get("artifact_aggregate_sha256"),
        require_required_globs=False,
    )
    if art["aggregate_sha256"] != seal.get("artifact_aggregate_sha256"):
        raise StrictValidationError("LOCAL_VERIFIED_SEAL artifact_aggregate_sha256 mismatch")
    return seal


def audit_fatal_signals_in_node_logs(attempt_dir: Path) -> dict[str, Any]:
    """Scan node_*.log for SIGSEGV; fail-closed unless only post-training teardown."""
    attempt_dir = Path(attempt_dir)
    audit: dict[str, Any] = {
        "files": [],
        "post_train_fatal_signal_noted": False,
    }
    post_train_hits: list[dict[str, Any]] = []
    for log_path in sorted(attempt_dir.glob("node_*.log")):
        text = log_path.read_text(encoding="utf-8", errors="replace")
        marker_pos = text.find(POST_TRAIN_DONE_MARKER)
        for pattern in SIGSEGV_PATTERNS:
            for match in pattern.finditer(text):
                offset = match.start()
                is_post_train = marker_pos >= 0 and offset > marker_pos
                entry = {
                    "file": log_path.name,
                    "pattern": pattern.pattern,
                    "offset": offset,
                    "post_train": is_post_train,
                    "matched": match.group(0),
                }
                audit["files"].append(entry)
                if not is_post_train:
                    raise StrictValidationError(
                        f"{log_path.name}: fatal signal before training done "
                        f"(offset={offset}, marker_at={marker_pos})"
                    )
                post_train_hits.append(entry)
    if post_train_hits:
        audit["post_train_fatal_signal_noted"] = True
        audit_path = attempt_dir / "FATAL_SIGNAL_AUDIT.json"
        audit_path.write_text(
            json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8"
        )
    return audit


def validate_attempt_manifest(
    attempt_dir: Path,
    *,
    expected_ranks: int,
    capture_step: int,
    expected_nodes: Optional[int] = None,
    require_seal: bool = True,
    require_provenance: bool = True,
    require_local_anchor: bool = False,
) -> dict[str, Any]:
    manifest_path = attempt_dir / "attempt_manifest.json"
    if not manifest_path.exists():
        raise StrictValidationError("missing attempt_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if int(manifest.get("exit_code", -1)) != 0:
        raise StrictValidationError(f"manifest.exit_code={manifest.get('exit_code')}")
    if int(manifest.get("convert_rc", -1)) != 0:
        raise StrictValidationError(f"manifest.convert_rc={manifest.get('convert_rc')}")
    if not manifest.get("finalize_complete", False):
        raise StrictValidationError("manifest.finalize_complete is not true")

    nodes = expected_nodes
    if nodes is None:
        nodes = int(manifest.get("nnodes") or manifest.get("expected_nodes") or 0)
        if nodes <= 0:
            # derive from world/expected_ranks if nproc known
            world = int(manifest.get("expected_ranks") or expected_ranks)
            nproc = int(manifest.get("nproc_per_node") or 0)
            if nproc > 0:
                nodes = max(1, world // nproc)
            else:
                nodes = 1 if expected_ranks <= 16 else 2

    # Parent launcher evidence: node_*.done present, node_*.fail absent.
    for node_id in range(nodes):
        done = attempt_dir / f"node_{node_id}.done"
        fail = attempt_dir / f"node_{node_id}.fail"
        if not done.exists():
            raise StrictValidationError(f"missing {done.name}")
        if fail.exists():
            raise StrictValidationError(f"unexpected {fail.name}")

    # Nested node launch raw exit codes from trusted parent launcher records.
    node_launch = manifest.get("node_launch") or {}
    if not node_launch:
        # also accept files on disk
        for p in sorted(attempt_dir.glob("node_*.launch.json")):
            node_launch[p.name] = json.loads(p.read_text(encoding="utf-8"))
    if len(node_launch) < nodes:
        raise StrictValidationError(
            f"node_launch records {len(node_launch)} < expected_nodes={nodes}"
        )
    for name, rec in sorted(node_launch.items()):
        if not isinstance(rec, dict):
            raise StrictValidationError(f"{name}: not an object")
        if int(rec.get("raw_exit_code", -1)) != 0:
            raise StrictValidationError(
                f"{name}: raw_exit_code={rec.get('raw_exit_code')} != 0"
            )
        if rec.get("raw_exit_code_pending", False):
            raise StrictValidationError(f"{name}: raw_exit_code_pending=true")

    # Every expected node must have positive e2e (monotonic wall); attempt e2e = max.
    # e2e is independent of UTC timestamps used for provenance seals.
    node_e2e_vals: list[float] = []
    for node_id in range(int(nodes)):
        key_candidates = (
            f"node_{node_id}",
            f"node_{node_id}.launch.json",
            str(node_id),
        )
        rec = None
        for key in key_candidates:
            if key in node_launch and isinstance(node_launch[key], dict):
                rec = node_launch[key]
                break
        if rec is None:
            # Fall back to on-disk launch file
            lp = attempt_dir / f"node_{node_id}.launch.json"
            if lp.exists():
                rec = json.loads(lp.read_text(encoding="utf-8"))
        if rec is None:
            raise StrictValidationError(
                f"missing node_{node_id} launch record for e2e check"
            )
        e2e = rec.get("e2e_wall_ms")
        if e2e is None or float(e2e) <= 0:
            raise StrictValidationError(
                f"node_{node_id}: missing/non-positive e2e_wall_ms={e2e}"
            )
        node_e2e_vals.append(float(e2e))
        # ours: loaded SO hash/path required and must match expected/provenance.
        # normal/torch: do not force collector loaded fields.
        expected_so = (
            (manifest.get("provenance") or {}).get("collector_so_sha256")
            or manifest.get("collector_so_sha256")
        )
        arm_l = str(manifest.get("arm") or "").lower()
        loaded = rec.get("collector_so_sha256_loaded")
        sealed_path = rec.get("collector_so_loaded_path") or rec.get("collector_so_path")
        if arm_l == "ours":
            if not loaded:
                raise StrictValidationError(
                    f"node_{node_id}: missing required collector_so_sha256_loaded"
                )
            if not sealed_path:
                raise StrictValidationError(
                    f"node_{node_id}: missing required collector_so_loaded_path"
                )
            if expected_so and str(loaded) != str(expected_so):
                raise StrictValidationError(
                    f"node_{node_id}: loaded SO hash {loaded} != expected {expected_so}"
                )
            base = Path(str(sealed_path)).name
            if expected_so and expected_so not in base:
                raise StrictValidationError(
                    f"node_{node_id}: loaded path {sealed_path} must be hash-named sealed SO"
                )
            cand = Path(str(sealed_path))
            if not cand.is_absolute():
                cand = attempt_dir / cand
            if cand.is_file():
                got = _sha256_file(cand)
                if expected_so and got != str(expected_so):
                    raise StrictValidationError(
                        f"node_{node_id}: sealed SO file hash {got} != expected {expected_so}"
                    )
        elif loaded and expected_so and str(loaded) != str(expected_so):
            raise StrictValidationError(
                f"node_{node_id}: loaded SO hash {loaded} != expected {expected_so}"
            )

    attempt_e2e = max(node_e2e_vals) if node_e2e_vals else None
    man_e2e = manifest.get("e2e_wall_ms")
    if man_e2e is not None and attempt_e2e is not None:
        if abs(float(man_e2e) - float(attempt_e2e)) > 1.0:
            # Allow minor float formatting drift; large mismatch is a seal error.
            raise StrictValidationError(
                f"manifest e2e_wall_ms={man_e2e} != max(node e2e)={attempt_e2e}"
            )

    if int(manifest.get("capture_megatron_iter", -1)) != capture_step and int(
        manifest.get("capture_step", -1)
    ) != capture_step:
        if "capture_megatron_iter" in manifest or "capture_step" in manifest:
            got = manifest.get("capture_megatron_iter", manifest.get("capture_step"))
            if int(got) != capture_step:
                raise StrictValidationError(
                    f"manifest capture step {got} != {capture_step}"
                )

    if require_provenance:
        prov = manifest.get("provenance") or {}
        for key in (
            "source_tree_sha256",
            "collector_so_sha256",
            "artifact_digest_sha256",
        ):
            if not prov.get(key) and not manifest.get(key):
                raise StrictValidationError(f"missing provenance.{key}")
        snap = attempt_dir / "provenance_source_snapshot.sha256"
        tree = attempt_dir / "provenance_source_tree.json"
        art = attempt_dir / "artifact_digest.json"
        if not snap.exists() and not tree.exists():
            raise StrictValidationError("missing provenance source snapshot/tree")
        if not art.exists():
            raise StrictValidationError("missing artifact_digest.json")
        listed_agg = prov.get("artifact_digest_sha256") or manifest.get(
            "artifact_digest_sha256"
        )
        verify_artifact_digest(
            attempt_dir,
            expected_aggregate=listed_agg,
            require_required_globs=True,
            arm=manifest.get("arm"),
            expected_ranks=int(manifest.get("expected_ranks") or expected_ranks),
            expected_nodes=nodes,
        )

        # Force non-null provenance hashes that match on-disk files when present.
        src = prov.get("source_tree_sha256") or manifest.get("source_tree_sha256")
        so = prov.get("collector_so_sha256") or manifest.get("collector_so_sha256")
        if not src or src in ("null", "None"):
            raise StrictValidationError("provenance.source_tree_sha256 is null")
        if not so or so in ("null", "None"):
            raise StrictValidationError("provenance.collector_so_sha256 is null")
        if not listed_agg:
            raise StrictValidationError("provenance.artifact_digest_sha256 is null")
        tree = attempt_dir / "provenance_source_tree.json"
        if tree.exists():
            tree_obj = json.loads(tree.read_text(encoding="utf-8"))
            if tree_obj.get("source_tree_sha256") != src:
                raise StrictValidationError(
                    "manifest source_tree_sha256 != provenance_source_tree.json"
                )
        snap = attempt_dir / "provenance_source_snapshot.sha256"
        if not snap.exists():
            raise StrictValidationError("missing provenance_source_snapshot.sha256")
        build = attempt_dir / "provenance_build.json"
        if build.exists():
            build_obj = json.loads(build.read_text(encoding="utf-8"))
            if build_obj.get("collector_so_sha256") and build_obj.get(
                "collector_so_sha256"
            ) != so:
                raise StrictValidationError(
                    "manifest collector_so_sha256 != provenance_build.json"
                )
        verify_sealed_collector_so(attempt_dir, manifest=manifest, require_readonly=True)

    if require_seal:
        seal_path = attempt_dir / "attempt_manifest.sha256"
        if not seal_path.exists():
            raise StrictValidationError("missing attempt_manifest.sha256")
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest().strip()
        seal = seal_path.read_text(encoding="utf-8").strip()
        if digest != seal:
            raise StrictValidationError("attempt_manifest.sha256 mismatch")
        # Re-seal-only bypass: changing manifest then rewriting .sha256 must still
        # fail if artifact entries no longer match (verify_artifact_digest above)
        # or if local anchor is required.

    if require_local_anchor:
        verify_local_anchor(attempt_dir, require=True)

    fatal_audit = audit_fatal_signals_in_node_logs(attempt_dir)

    return {
        "manifest": manifest,
        "ok": True,
        "expected_nodes": nodes,
        "post_train_fatal_signal_noted": fatal_audit.get(
            "post_train_fatal_signal_noted", False
        ),
    }
