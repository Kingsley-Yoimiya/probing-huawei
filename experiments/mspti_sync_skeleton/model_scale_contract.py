#!/usr/bin/env python3
"""Megatron AB scale contracts — frozen GBS per world_size.

64 卡（NNODES=4 × NPROC=16, TP=2, PP=1）→ DP=32，强制 GBS=128。
256 卡（NNODES=16 × NPROC=16, TP=2, PP=1）→ DP=128，强制 GBS=512。
禁止在 64/256 卡规模默默落到默认 GBS=64（与冻结 DP 不整除或口径漂移）。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

# 与 32 卡 GBS=64 / DP=16 同构：grad_accum = GBS / (MBS * DP) = 4
FROZEN_GBS_64 = 128
FROZEN_GBS_256 = 512
DEFAULT_GBS_SMALL = 64


class GbsContractError(ValueError):
    """GBS / DP divisibility or frozen-scale violation."""


def data_parallel_size(
    *,
    nnodes: int,
    nproc: int,
    tp: int = 2,
    pp: int = 1,
) -> int:
    world = int(nnodes) * int(nproc)
    denom = max(1, int(tp) * int(pp))
    return max(1, world // denom)


def resolve_frozen_gbs(
    *,
    nnodes: int,
    nproc: int,
    tp: int = 2,
    pp: int = 1,
    mbs: int = 1,
    gbs: Optional[int] = None,
    seq: int = 4096,
    layers: int = 32,
    seed: int = 1234,
) -> dict[str, Any]:
    """Return model dict with frozen GBS; raise GbsContractError on violation."""
    world_size = int(nnodes) * int(nproc)
    dp = data_parallel_size(nnodes=nnodes, nproc=nproc, tp=tp, pp=pp)
    frozen_64 = world_size == 64
    frozen_256 = world_size == 256

    if frozen_256:
        required = FROZEN_GBS_256
        if gbs is None:
            gbs = required
        elif int(gbs) != required:
            raise GbsContractError(
                f"world_size=256 (NNODES={nnodes} NPROC={nproc}) requires GBS={required} "
                f"(DP={dp}); got GBS={gbs}"
            )
    elif frozen_64:
        required = FROZEN_GBS_64
        if gbs is None:
            gbs = required
        elif int(gbs) != required:
            raise GbsContractError(
                f"world_size=64 (NNODES={nnodes} NPROC={nproc}) requires GBS={required} "
                f"(DP={dp}); got GBS={gbs}"
            )
    elif gbs is None:
        gbs = DEFAULT_GBS_SMALL

    gbs = int(gbs)
    unit = int(mbs) * dp
    if gbs % unit != 0:
        raise GbsContractError(
            f"GBS={gbs} not divisible by MBS*DP={unit} "
            f"(world_size={world_size} TP={tp} PP={pp})"
        )

    grad_accum = gbs // unit
    return {
        "tp": int(tp),
        "pp": int(pp),
        "mbs": int(mbs),
        "gbs": gbs,
        "seq": int(seq),
        "layers": int(layers),
        "seed": int(seed),
        "world_size": world_size,
        "dp": dp,
        "grad_accum": grad_accum,
        "frozen_gbs_64": frozen_64,
        "frozen_gbs_256": frozen_256,
        "gbs_contract": (
            "frozen_512" if frozen_256 else ("frozen_128" if frozen_64 else "default")
        ),
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nnodes", type=int, required=True)
    ap.add_argument("--nproc", type=int, required=True)
    ap.add_argument("--tp", type=int, default=2)
    ap.add_argument("--pp", type=int, default=1)
    ap.add_argument("--mbs", type=int, default=1)
    ap.add_argument("--gbs", type=int, default=None)
    ap.add_argument("--seq", type=int, default=4096)
    ap.add_argument("--layers", type=int, default=32)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--format", choices=("json", "gbs"), default="json")
    args = ap.parse_args(argv)
    try:
        model = resolve_frozen_gbs(
            nnodes=args.nnodes,
            nproc=args.nproc,
            tp=args.tp,
            pp=args.pp,
            mbs=args.mbs,
            gbs=args.gbs,
            seq=args.seq,
            layers=args.layers,
            seed=args.seed,
        )
    except GbsContractError as exc:
        print(f"GBS_CONTRACT_FAIL: {exc}", file=sys.stderr)
        return 2
    if args.format == "gbs":
        print(model["gbs"])
        return 0
    print(json.dumps(model, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
