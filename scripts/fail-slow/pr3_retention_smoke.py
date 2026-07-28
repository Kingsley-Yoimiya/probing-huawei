#!/usr/bin/env python3
"""PR-3 retention smoke test — run inside the target pod after installing
the freshly built probing wheel. Does not launch training; only exercises
the ExternalTable retention surface.

Emits `PASS: <check>` / `FAIL: <check>` lines the runner grep-parses.
"""

from __future__ import annotations

import os
import sys
import traceback


def _print_ok(check: str, extra: str = "") -> None:
    tail = f" — {extra}" if extra else ""
    print(f"PASS: {check}{tail}", flush=True)


def _print_fail(check: str, extra: str = "") -> None:
    tail = f" — {extra}" if extra else ""
    print(f"FAIL: {check}{tail}", flush=True)


def _get_config_cls():
    """Locate PyExternalTableConfig. It lives on `probing._core`; the
    top-level `probing` module does not re-export it in this build."""
    import probing._core as core
    return core.PyExternalTableConfig


def check_import_fields() -> bool:
    """PyExternalTableConfig exposes retain_steps / retain_secs on the tiered defaults."""
    try:
        Cfg = _get_config_cls()
        cfg_tt = Cfg.for_table("python.torch_trace")
        d = cfg_tt.into_py()
        if d.get("retain_steps") != 500:
            _print_fail(
                "import_fields.torch_trace.retain_steps",
                f"expected 500 got {d.get('retain_steps')}",
            )
            return False
        cfg_cpu = Cfg.for_table("cpu.utilization")
        d2 = cfg_cpu.into_py()
        # env override in this smoke may pin it to 1800; only require Some(_)
        got_cpu = d2.get("retain_secs")
        if got_cpu is None:
            _print_fail("import_fields.cpu.utilization.retain_secs",
                        f"expected non-None got {got_cpu}")
            return False
        _print_ok(
            "import_fields",
            f"torch_trace.retain_steps={d.get('retain_steps')} "
            f"cpu.utilization.retain_secs={got_cpu}",
        )
        return True
    except Exception as e:  # pragma: no cover
        _print_fail("import_fields", f"exception: {e!r}")
        traceback.print_exc()
        return False


def check_torch_trace_retain_steps() -> bool:
    """Create python.torch_trace, retain_steps=100, write enough rows to
    force multiple chunk recycles, then confirm retention counter fires
    when we overwrite still-recent step ranges.

    The retention *observation* is what we verify — the write path is
    single-writer append-only, so MEMT recycles regardless; the counter
    increments each time recycle would truncate the window."""
    try:
        import probing

        name = f"torch_trace_smoke_{os.getpid()}"
        # Ensure a fresh table (drop if pre-existing).
        try:
            probing.ExternalTable.drop(name)
        except Exception:
            pass
        # Tiny ring — 32 KiB total, 4 KiB × 8 chunks — so 4000 rows will
        # force many recycles. discard_threshold is the total budget.
        t = probing.ExternalTable(
            name,
            ["step", "dur_ms"],
            10000,           # chunk_size (byte-based; ignored, kept for API compat)
            32 * 1024,       # discard_threshold (32 KiB total ring)
            "BaseMemorySize",
            None,            # table_doc
            None,            # column_docs
            100,             # retain_steps
            None,            # retain_secs
        )
        # 4000 rows will definitely trigger recycles.
        n_rows = 4000
        for step in range(n_rows):
            t.append([step, 0.5])
        rows = t.take(None)
        if not rows:
            _print_fail("retain_steps.take_nonempty")
            return False
        # rows[i] = (timestamp_ele, [step_ele, dur_ele]) — the first user
        # column is "step" so vals[0].
        steps = [int(vals[0]) for _, vals in rows]
        min_step = min(steps)
        max_step = max(steps)
        snapshot = t.retention()
        extras = (
            f"rows={len(rows)} min_step={min_step} max_step={max_step} "
            f"retain_steps={snapshot.get('retain_steps')} "
            f"violations_step={snapshot.get('violations_step')}"
        )
        # PASS criteria (handbook §3.2):
        # - violations_step > 0 → retention truncation was observed and
        #   accounted for (the whole point of PR-3)
        # - retain_steps snapshot == 100 (what we configured)
        if (
            snapshot.get("retain_steps") == 100
            and snapshot.get("violations_step", 0) > 0
        ):
            _print_ok("retain_steps.violations_counted", extras)
            return True
        _print_fail("retain_steps.violations_counted", extras)
        return False
    except Exception as e:
        _print_fail("retain_steps", f"exception: {e!r}")
        traceback.print_exc()
        return False


def check_set_retention_runtime() -> bool:
    """Runtime override via `ExternalTable.set_retention(...)`. Stand-in for
    `SET probing.exttbl.<t>.retain_steps=200` — the full SET route hook
    is left for a follow-up PR; this test still verifies the underlying
    knob is reachable end-to-end."""
    try:
        import probing

        name = f"torch_trace_set_{os.getpid()}"
        try:
            probing.ExternalTable.drop(name)
        except Exception:
            pass
        t = probing.ExternalTable(
            name,
            ["step", "dur_ms"],
            10000,
            256 * 1024,
            "BaseMemorySize",
            None,
            None,
        )
        prev = t.set_retention(retain_steps=200, retain_secs=None)
        snap = t.retention()
        if snap.get("retain_steps") != 200:
            _print_fail(
                "set_retention.applied",
                f"prev={prev} snap={snap}",
            )
            return False
        # A few writes past the window to check counter plumbing (not
        # asserting counter > 0 because with this size we may not recycle).
        for step in range(50):
            t.append([step, 0.5])
        snap2 = t.retention()
        _print_ok("set_retention.applied", f"prev={prev} snap={snap} snap2={snap2}")
        return True
    except Exception as e:
        _print_fail("set_retention", f"exception: {e!r}")
        traceback.print_exc()
        return False


def check_env_override_retain_secs() -> bool:
    """Env override `PROBING_EXTTBL_CPU_UTILIZATION_RETAIN_SECS=1800` must
    surface on the config helper. The env is expected to be set by the
    driver *before* python starts."""
    try:
        Cfg = _get_config_cls()
        env_val = os.environ.get("PROBING_EXTTBL_CPU_UTILIZATION_RETAIN_SECS")
        if env_val is None:
            _print_fail(
                "env_override.retain_secs",
                "PROBING_EXTTBL_CPU_UTILIZATION_RETAIN_SECS not in env",
            )
            return False
        cfg = Cfg.for_table("cpu.utilization")
        d = cfg.into_py()
        want = int(env_val)
        got = d.get("retain_secs")
        if got == want:
            _print_ok("env_override.retain_secs", f"env={env_val} got={got}")
            return True
        _print_fail(
            "env_override.retain_secs",
            f"env={env_val} expected={want} got={got}",
        )
        return False
    except Exception as e:
        _print_fail("env_override.retain_secs", f"exception: {e!r}")
        traceback.print_exc()
        return False


def main() -> int:
    print("== PR-3 retention smoke ==", flush=True)
    ok = True
    ok &= check_import_fields()
    ok &= check_torch_trace_retain_steps()
    ok &= check_set_retention_runtime()
    ok &= check_env_override_retain_secs()
    print("== SUMMARY:", "PASS" if ok else "FAIL", "==", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
