#!/usr/bin/env python3
"""Structured opponent / idle checker (in-pod or local fixture).

Protocol — unique status + exit codes:
  CLEAR=0 | OPPONENT=10 | CHECK_FAILED=20

Never uses os.popen or bare ``ps | awk`` pipes that swallow rc.
Process listing is ``subprocess.run(ps, …)`` with mandatory returncode check,
or a /proc cmdline scan when ``--via-proc`` is set.

Modes:
  idle   — pre-start: any matching train process → OPPONENT
  yield  — runtime: matching train process without our ownership evidence → OPPONENT

Yield ownership (two-phase, fail-closed):
  1) Probe each train row once (environ three-state + starttime). Identify own
     rows via live seeds/PPID closure / exact RUN_MARKER / OUT_DIR; collect
     ``own_pgids`` only from live rows with marker/outdir/validated-tree evidence.
  2) Classify: STALE skip; ERROR → CHECK_FAILED; pid/ppid/marker/outdir evidence
     → own; stable readable rows without such evidence → OPPONENT. An owned PGID
     is diagnostic context only, never sufficient ownership proof by itself.

Environ three-state (never treat read errors as "no marker"):
  STALE  — /proc/<pid> gone (ENOENT/ESRCH) → ignore ps row
  ERROR  — PermissionError / EACCES / EPERM / other non-gone OSError → CHECK_FAILED
  OK     — readable; exact RUN_MARKER= / OUT_DIR= check

Stdout last line is always one of: ``CLEAR`` | ``OPPONENT|…`` | ``CHECK_FAILED|…``
Empty / unknown output must be treated as CHECK_FAILED by callers (and by this
tool itself when parse fails).

Fixture knobs (local only; never used in formal cluster path unless set):
  --fixture-ps-rc / --fixture-ps-stdout  mock the ps layer
  --fixture-environ-json  mock /proc environ+starttime probes
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

RC_CLEAR = 0
RC_OPPONENT = 10
RC_CHECK_FAILED = 20

STATUS_CLEAR = "CLEAR"
STATUS_OPPONENT = "OPPONENT"
STATUS_CHECK_FAILED = "CHECK_FAILED"

TRAIN_NEEDLES = (
    "torchrun",
    "pretrain_gpt.py",
    "pretrain",
    "megatron",
    "mspti_sync_skeleton/workload.py",
)

# Global fixture map for environ/starttime probes (tests / --fixture-environ-json).
# pid(str) -> {status, has_marker?, has_out_dir?, starttime?, errno?, errno_name?}
_ENVIRON_FIXTURE: Optional[dict[str, dict]] = None


class ProbeStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class EnvironProbe:
    status: ProbeStatus
    raw: Optional[bytes] = None
    errno_num: Optional[int] = None
    errno_name: str = ""

    @property
    def ok(self) -> bool:
        return self.status == ProbeStatus.OK


@dataclass
class RowProbe:
    pid: int
    pgid: int
    state: str
    args: str
    starttime: Optional[int]
    environ: EnvironProbe
    has_marker: Optional[bool] = None  # None if not OK
    has_out_dir: Optional[bool] = None
    in_validated_tree: bool = False
    pgid_own: bool = False
    ownership: str = "unknown"  # own | opponent | stale | error


def emit(status: str, detail: str = "") -> int:
    if status == STATUS_CLEAR:
        print(STATUS_CLEAR)
        return RC_CLEAR
    if status == STATUS_OPPONENT:
        print(f"OPPONENT|{detail}" if detail else "OPPONENT|")
        return RC_OPPONENT
    print(f"CHECK_FAILED|{detail}" if detail else "CHECK_FAILED|")
    return RC_CHECK_FAILED


def _is_zombie_state(state: str) -> bool:
    return bool(state) and state[0].upper() == "Z"


def _looks_train(args: str) -> bool:
    low = args.lower()
    if "[torchrun]" in args:
        return False
    if "awk" in low or "bash --noprofile" in low:
        return False
    if "opponent_check.py" in low:
        return False
    return any(n in low for n in TRAIN_NEEDLES)


def _errno_name(num: Optional[int]) -> str:
    if num is None:
        return ""
    try:
        return errno.errorcode.get(num, f"E{num}")
    except Exception:
        return f"E{num}"


def _is_gone_oserror(exc: BaseException) -> bool:
    if isinstance(exc, (FileNotFoundError, ProcessLookupError)):
        return True
    if isinstance(exc, OSError):
        if exc.errno in (errno.ENOENT, errno.ESRCH):
            return True
        # Some platforms surface gone as EINVAL on vanished /proc nodes.
        if exc.errno == errno.ENOENT:
            return True
    return False


def _is_perm_oserror(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in (errno.EACCES, errno.EPERM):
        return True
    return False


def set_environ_fixture(mapping: Optional[dict]) -> None:
    """Test helper: inject pid→probe map (or None to clear)."""
    global _ENVIRON_FIXTURE
    _ENVIRON_FIXTURE = mapping


def read_proc_starttime(pid: int) -> tuple[ProbeStatus, Optional[int], str]:
    """Return (status, starttime, errno_detail)."""
    if _ENVIRON_FIXTURE is not None:
        fx = _ENVIRON_FIXTURE.get(str(pid)) or _ENVIRON_FIXTURE.get(pid)  # type: ignore[index]
        if fx is None:
            return ProbeStatus.STALE, None, "fixture_missing_pid"
        st = str(fx.get("status", "OK")).upper()
        if st == "STALE":
            return ProbeStatus.STALE, None, str(fx.get("errno_name") or "ENOENT")
        if st == "ERROR":
            return ProbeStatus.ERROR, None, str(fx.get("errno_name") or "EACCES")
        raw_st = fx.get("starttime")
        return ProbeStatus.OK, (int(raw_st) if raw_st is not None else None), ""

    stat_path = Path(f"/proc/{pid}/stat")
    try:
        text = stat_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        if _is_gone_oserror(exc):
            return ProbeStatus.STALE, None, _errno_name(getattr(exc, "errno", errno.ENOENT))
        if _is_perm_oserror(exc):
            return ProbeStatus.ERROR, None, _errno_name(getattr(exc, "errno", errno.EACCES))
        return ProbeStatus.ERROR, None, _errno_name(getattr(exc, "errno", None)) or type(exc).__name__
    try:
        rparen = text.rfind(")")
        if rparen < 0:
            return ProbeStatus.ERROR, None, "stat_parse"
        fields = text[rparen + 2 :].split()
        # field 22 overall → index 19 after state
        return ProbeStatus.OK, int(fields[19]), ""
    except (IndexError, ValueError):
        return ProbeStatus.ERROR, None, "stat_parse"


def read_environ(pid: int) -> EnvironProbe:
    """Three-state environ read. Never collapses errors to 'no marker'."""
    if _ENVIRON_FIXTURE is not None:
        fx = _ENVIRON_FIXTURE.get(str(pid)) or _ENVIRON_FIXTURE.get(pid)  # type: ignore[index]
        if fx is None:
            return EnvironProbe(ProbeStatus.STALE, errno_num=errno.ENOENT, errno_name="ENOENT")
        st = str(fx.get("status", "OK")).upper()
        if st == "STALE":
            en = int(fx["errno"]) if "errno" in fx else errno.ENOENT
            return EnvironProbe(
                ProbeStatus.STALE,
                errno_num=en,
                errno_name=str(fx.get("errno_name") or _errno_name(en)),
            )
        if st == "ERROR":
            en = int(fx["errno"]) if "errno" in fx else errno.EACCES
            return EnvironProbe(
                ProbeStatus.ERROR,
                errno_num=en,
                errno_name=str(fx.get("errno_name") or _errno_name(en)),
            )
        # OK: synthesize minimal environ bytes from flags (no full env leakage).
        parts: list[bytes] = []
        if fx.get("has_marker"):
            marker = fx.get("marker_value") or fx.get("marker") or ""
            if marker:
                parts.append(f"RUN_MARKER={marker}".encode())
            else:
                parts.append(b"RUN_MARKER=__FIXTURE_MARKER__")
        if fx.get("has_out_dir"):
            od = fx.get("out_dir_value") or fx.get("out_dir") or ""
            if od:
                parts.append(f"OUT_DIR={od}".encode())
            else:
                parts.append(b"OUT_DIR=__FIXTURE_OUT__")
        # Allow explicit raw only for advanced fixtures (still not printed).
        if "raw" in fx and isinstance(fx["raw"], str):
            raw = fx["raw"].encode()
        else:
            raw = b"\0".join(parts) + (b"\0" if parts else b"")
        return EnvironProbe(ProbeStatus.OK, raw=raw)

    env_path = Path(f"/proc/{pid}/environ")
    try:
        raw = env_path.read_bytes()
    except OSError as exc:
        if _is_gone_oserror(exc):
            return EnvironProbe(
                ProbeStatus.STALE,
                errno_num=getattr(exc, "errno", errno.ENOENT),
                errno_name=_errno_name(getattr(exc, "errno", errno.ENOENT)),
            )
        if _is_perm_oserror(exc):
            return EnvironProbe(
                ProbeStatus.ERROR,
                errno_num=getattr(exc, "errno", errno.EACCES),
                errno_name=_errno_name(getattr(exc, "errno", errno.EACCES)),
            )
        return EnvironProbe(
            ProbeStatus.ERROR,
            errno_num=getattr(exc, "errno", None),
            errno_name=_errno_name(getattr(exc, "errno", None)) or type(exc).__name__,
        )
    return EnvironProbe(ProbeStatus.OK, raw=raw)


def environ_has_exact(raw: bytes, key: str, value: str) -> bool:
    needle = f"{key}={value}".encode()
    return needle in raw.split(b"\0")


def load_seed_records(out_dir: Path, node_id: int) -> Optional[list[tuple[int, Optional[int]]]]:
    """Return [(pid, expected_starttime|None), ...] or None if STARTUP (no pgid yet).

    Prefer live ownership snapshot (pid+starttime). Fall back to bare pgid/pids
    only as bootstrap seeds — callers must validate via /proc starttime / marker tree.
    """
    own_path = out_dir / f"node_{node_id}.ownership.jsonl"
    pgid_path = out_dir / f"node_{node_id}.pgid"
    if not pgid_path.exists() and not own_path.exists():
        return None

    records: list[tuple[int, Optional[int]]] = []
    seen: set[int] = set()

    if own_path.exists():
        try:
            for line in own_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    pid = int(obj["pid"])
                except (KeyError, TypeError, ValueError):
                    continue
                st = obj.get("starttime")
                starttime = int(st) if st is not None else None
                if pid not in seen:
                    records.append((pid, starttime))
                    seen.add(pid)
        except OSError:
            pass

    if pgid_path.exists():
        try:
            pid = int(pgid_path.read_text(encoding="utf-8").strip())
            if pid not in seen:
                records.append((pid, None))
                seen.add(pid)
        except (ValueError, OSError):
            if not records:
                return None

    pids_path = out_dir / f"node_{node_id}.pids"
    if pids_path.exists():
        try:
            for tok in pids_path.read_text(encoding="utf-8").split():
                try:
                    pid = int(tok)
                except ValueError:
                    continue
                if pid not in seen:
                    records.append((pid, None))
                    seen.add(pid)
        except OSError:
            pass

    if not records and not pgid_path.exists():
        return None
    if not records:
        return None
    return records


def validate_live_seeds(
    seed_records: list[tuple[int, Optional[int]]],
    *,
    marker: str = "",
    out_dir: str = "",
) -> set[int]:
    """Keep only live seeds. Prefer starttime match; bare seeds need marker/outdir.

    Bare seeds without recorded starttime are kept only when the live process still
    carries our RUN_MARKER or OUT_DIR — they bootstrap the current marker tree and
    must not claim a reused PID as own solely by number.
    """
    live: set[int] = set()
    for pid, expected_st in seed_records:
        st_status, st_val, _ = read_proc_starttime(pid)
        if st_status != ProbeStatus.OK:
            continue
        if expected_st is not None:
            if st_val is not None and int(expected_st) != int(st_val):
                # PID reuse — do not treat as own.
                continue
            live.add(pid)
            continue
        # No recorded starttime: require live marker/outdir evidence.
        env = read_environ(pid)
        if env.status != ProbeStatus.OK or env.raw is None:
            continue
        if marker and environ_has_exact(env.raw, "RUN_MARKER", marker):
            live.add(pid)
            continue
        if out_dir and environ_has_exact(env.raw, "OUT_DIR", out_dir):
            live.add(pid)
            continue
    return live


def expand_our_tree(seeds: set[int]) -> set[int]:
    """Closure over /proc ppid graph; soft-fail → return seeds only."""
    if _ENVIRON_FIXTURE is not None:
        # Fixture may provide ppid map under special key "_ppid".
        ppid_map = _ENVIRON_FIXTURE.get("_ppid") if isinstance(_ENVIRON_FIXTURE, dict) else None
        if isinstance(ppid_map, dict):
            ppid = {int(k): int(v) for k, v in ppid_map.items()}
            our = set(seeds)
            changed = True
            while changed:
                changed = False
                for pid, parent in ppid.items():
                    if parent in our and pid not in our:
                        our.add(pid)
                        changed = True
            return our
        return set(seeds)

    ppid: dict[int, int] = {}
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return set(seeds)
    try:
        for status in proc_root.glob("*/status"):
            try:
                pid = int(status.parent.name)
                text = status.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r"^PPid:\s*(\d+)", text, re.M)
                if m:
                    ppid[pid] = int(m.group(1))
            except (ValueError, OSError):
                continue
    except OSError:
        return set(seeds)
    our = set(seeds)
    changed = True
    while changed:
        changed = False
        for pid, parent in ppid.items():
            if parent in our and pid not in our:
                our.add(pid)
                changed = True
    return our


def run_ps(*, fixture_rc: Optional[int], fixture_stdout: Optional[str]) -> tuple[int, str]:
    """Run ``ps -eo pid,pgid,state,args``; always surface returncode."""
    if fixture_rc is not None or fixture_stdout is not None:
        return int(fixture_rc if fixture_rc is not None else 0), (
            fixture_stdout if fixture_stdout is not None else ""
        )
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid,pgid,state,args"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return 1, f"ps_oserror:{exc}"
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    return int(proc.returncode), out


def parse_ps_rows(stdout: str) -> list[tuple[int, int, str, str]]:
    rows: list[tuple[int, int, str, str]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("pid"):
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            pgid = int(parts[1])
        except ValueError:
            continue
        state, args = parts[2], parts[3]
        rows.append((pid, pgid, state, args))
    return rows


def scan_proc_rows() -> list[tuple[int, int, str, str]]:
    """Fallback /proc scan; OSError on /proc → raise (caller → CHECK_FAILED)."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        raise FileNotFoundError("/proc missing")
    rows: list[tuple[int, int, str, str]] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "ignore"
            )
            status = (entry / "status").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        state_m = re.search(r"^State:\s+(\S+)", status, re.M)
        pgid_m = re.search(r"^NSpgid:\s+(\d+)", status, re.M) or re.search(
            r"^PPid:\s+(\d+)", status, re.M
        )
        state = state_m.group(1) if state_m else "?"
        pgid = int(pgid_m.group(1)) if pgid_m else pid
        rows.append((pid, pgid, state, cmdline))
    return rows


def collect_rows(
    *,
    via_proc: bool,
    fixture_rc: Optional[int],
    fixture_stdout: Optional[str],
) -> tuple[str, list[tuple[int, int, str, str]]]:
    """Return (err_detail, rows). Non-empty err_detail → CHECK_FAILED."""
    if via_proc and fixture_rc is None and fixture_stdout is None:
        try:
            rows = scan_proc_rows()
            if not rows:
                return "empty_proc_rows", []
            return "", rows
        except OSError as exc:
            return f"proc_scan_failed:{exc}", []
    rc, stdout = run_ps(fixture_rc=fixture_rc, fixture_stdout=fixture_stdout)
    if rc != 0:
        return f"ps_rc={rc}", []
    rows = parse_ps_rows(stdout)
    body = [
        ln.strip()
        for ln in (stdout or "").splitlines()
        if ln.strip() and not ln.strip().lower().startswith("pid")
    ]
    if body and not rows:
        return "malformed_ps_stdout", []
    if not rows:
        return "empty_ps_rows", []
    return "", rows


def run_idle(
    rows: Iterable[tuple[int, int, str, str]],
) -> int:
    opponents: list[str] = []
    for pid, pgid, state, args in rows:
        if _is_zombie_state(state):
            continue
        if not _looks_train(args):
            continue
        opponents.append(f"pid={pid} pgid={pgid} starttime=? probe=idle {args[:120]}")
    if opponents:
        return emit(STATUS_OPPONENT, ";".join(opponents))
    return emit(STATUS_CLEAR)


def _fmt_opponent_row(rp: RowProbe) -> str:
    st = rp.starttime if rp.starttime is not None else "?"
    marker = (
        "marker=yes"
        if rp.has_marker is True
        else ("marker=no" if rp.has_marker is False else f"marker={rp.environ.status.value.lower()}")
    )
    outd = (
        "outdir=yes"
        if rp.has_out_dir is True
        else (
            "outdir=no"
            if rp.has_out_dir is False
            else f"outdir={rp.environ.status.value.lower()}"
        )
    )
    return (
        f"pid={rp.pid} pgid={rp.pgid} starttime={st} "
        f"{marker} {outd} tree={int(rp.in_validated_tree)} "
        f"pgid_own={int(rp.pgid_own)} {rp.args[:100]}"
    )


def _fmt_check_failed(rp: RowProbe) -> str:
    en = rp.environ.errno_name or _errno_name(rp.environ.errno_num) or "EUNKNOWN"
    eno = rp.environ.errno_num if rp.environ.errno_num is not None else "?"
    return f"pid={rp.pid} errno={eno} errno_name={en}"


def probe_train_rows(
    rows: Iterable[tuple[int, int, str, str]],
    *,
    marker: str,
    out_s: str,
) -> list[RowProbe]:
    """Single-pass environ/starttime cache for all train rows (no re-race reads)."""
    probes: list[RowProbe] = []
    for pid, pgid, state, args in rows:
        if _is_zombie_state(state):
            continue
        if not _looks_train(args):
            continue
        st_status, st_val, _ = read_proc_starttime(pid)
        env = read_environ(pid)
        # If starttime says STALE, treat row as stale even if environ raced.
        if st_status == ProbeStatus.STALE and env.status == ProbeStatus.OK:
            env = EnvironProbe(ProbeStatus.STALE, errno_num=errno.ENOENT, errno_name="ENOENT")
        if st_status == ProbeStatus.ERROR and env.status == ProbeStatus.OK:
            # Permission on stat but environ readable — keep environ; starttime unknown.
            st_val = None
        has_marker: Optional[bool] = None
        has_out: Optional[bool] = None
        if env.status == ProbeStatus.OK and env.raw is not None:
            has_marker = environ_has_exact(env.raw, "RUN_MARKER", marker)
            has_out = environ_has_exact(env.raw, "OUT_DIR", out_s)
        probes.append(
            RowProbe(
                pid=pid,
                pgid=pgid,
                state=state,
                args=args,
                starttime=st_val if st_status == ProbeStatus.OK else None,
                environ=env,
                has_marker=has_marker,
                has_out_dir=has_out,
            )
        )
    return probes


def run_yield(
    rows: Iterable[tuple[int, int, str, str]],
    *,
    out_dir: Path,
    marker: str,
    node_id: int,
) -> int:
    if not marker or len(marker) < 8:
        return emit(STATUS_CHECK_FAILED, "marker_empty_or_short")
    seed_records = load_seed_records(out_dir, node_id)
    if seed_records is None:
        # Startup race: pgid not ready — not CLEAR of opponent absence.
        print("STARTUP")
        return RC_CLEAR

    out_s = str(out_dir)
    # Phase 0: validate live seeds (starttime / marker) → PPID closure.
    live_seeds = validate_live_seeds(seed_records, marker=marker, out_dir=out_s)
    validated_tree = expand_our_tree(live_seeds)

    # Phase 1: probe all train rows once; identify own + collect own_pgids.
    probes = probe_train_rows(rows, marker=marker, out_s=out_s)
    own_pgids: set[int] = set()

    for rp in probes:
        if rp.environ.status == ProbeStatus.STALE:
            rp.ownership = "stale"
            continue
        if rp.environ.status == ProbeStatus.ERROR:
            rp.ownership = "error"
            continue
        in_tree = rp.pid in validated_tree
        rp.in_validated_tree = in_tree
        marker_own = bool(rp.has_marker)
        out_own = bool(rp.has_out_dir)
        if in_tree or marker_own or out_own:
            rp.ownership = "own"
            # PGID ownership only from live row with marker/outdir/validated-tree evidence.
            own_pgids.add(rp.pgid)

    # Phase 2: classify with own_pgids; never re-read environ.
    opponents: list[str] = []
    errors: list[str] = []
    for rp in probes:
        if rp.ownership == "stale":
            continue
        if rp.ownership == "error":
            errors.append(_fmt_check_failed(rp))
            continue
        if rp.ownership == "own":
            rp.pgid_own = rp.pgid in own_pgids
            continue
        # A stable, readable row without marker/out-dir/tree evidence is foreign,
        # even if it shares an owned PGID. PGID alone is not an ownership proof.
        if rp.pgid in own_pgids:
            rp.pgid_own = True
        rp.ownership = "opponent"
        opponents.append(_fmt_opponent_row(rp))

    if errors:
        return emit(STATUS_CHECK_FAILED, ";".join(errors))
    if opponents:
        return emit(STATUS_OPPONENT, ";".join(opponents))
    return emit(STATUS_CLEAR)


def classify_transport_stdout(stdout: str, check_rc: int) -> tuple[str, int, str]:
    """Map kubectl/ssh/checker output → (status, rc, detail). Fail-closed."""
    if check_rc not in (RC_CLEAR, RC_OPPONENT, RC_CHECK_FAILED):
        return STATUS_CHECK_FAILED, RC_CHECK_FAILED, f"transport_rc={check_rc}"
    text = (stdout or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if not last:
        return STATUS_CHECK_FAILED, RC_CHECK_FAILED, "empty_stdout"
    if check_rc == RC_CHECK_FAILED:
        return STATUS_CHECK_FAILED, RC_CHECK_FAILED, last[:500]
    if check_rc == RC_OPPONENT:
        if last.startswith("OPPONENT") or last.startswith("OPP|") or last.startswith("OPP"):
            return STATUS_OPPONENT, RC_OPPONENT, last[:500]
        return STATUS_CHECK_FAILED, RC_CHECK_FAILED, f"opponent_rc_mismatch={last[:200]}"
    if last == "STARTUP" or last == STATUS_CLEAR or last == "OK":
        return STATUS_CLEAR, RC_CLEAR, last
    if last.startswith("OPPONENT") or last.startswith("OPP|") or last.startswith("OPP"):
        return STATUS_OPPONENT, RC_OPPONENT, last[:500]
    if last.startswith("CHECK_FAILED"):
        return STATUS_CHECK_FAILED, RC_CHECK_FAILED, last[:500]
    return STATUS_CHECK_FAILED, RC_CHECK_FAILED, f"unexpected_stdout={last[:200]}"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=("idle", "yield"), required=True)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--run-marker", default="")
    ap.add_argument("--node-id", type=int, default=0)
    ap.add_argument("--via-proc", action="store_true")
    ap.add_argument("--fixture-ps-rc", type=int, default=None)
    ap.add_argument("--fixture-ps-stdout", default=None)
    ap.add_argument(
        "--fixture-environ-json",
        default=None,
        help="JSON map pid→{status,has_marker,has_out_dir,starttime,errno_name} "
        "plus optional _ppid map for tree expansion",
    )
    ap.add_argument(
        "--classify-only",
        action="store_true",
        help="Classify provided --stdout/--rc (transport mock); do not run ps",
    )
    ap.add_argument("--stdout", default="")
    ap.add_argument("--rc", type=int, default=0)
    args = ap.parse_args(argv)

    if args.classify_only:
        status, rc, detail = classify_transport_stdout(args.stdout, args.rc)
        print(status)
        print(rc)
        print(detail)
        return rc

    if args.fixture_environ_json:
        try:
            mapping = json.loads(args.fixture_environ_json)
            if not isinstance(mapping, dict):
                return emit(STATUS_CHECK_FAILED, "fixture_environ_not_object")
            set_environ_fixture(mapping)
        except json.JSONDecodeError as exc:
            return emit(STATUS_CHECK_FAILED, f"fixture_environ_json:{exc}")

    err, rows = collect_rows(
        via_proc=args.via_proc,
        fixture_rc=args.fixture_ps_rc,
        fixture_stdout=args.fixture_ps_stdout,
    )
    if err:
        return emit(STATUS_CHECK_FAILED, err)

    try:
        if args.mode == "idle":
            return run_idle(rows)
        if not args.out_dir or not args.run_marker:
            return emit(STATUS_CHECK_FAILED, "yield_requires_out_dir_and_marker")
        return run_yield(
            rows,
            out_dir=Path(args.out_dir),
            marker=args.run_marker,
            node_id=args.node_id,
        )
    finally:
        if args.fixture_environ_json:
            set_environ_fixture(None)


if __name__ == "__main__":
    raise SystemExit(main())
