"""
Profiling Tools

Spec
----
This package provides specialized profiling tools for AI workloads.

Responsibilities:
1.  Collective communication profiling (NCCL/distributed ops).
2.  Framework-specific profiling (e.g., PyTorch Module/Optimizer steps).
3.  Accelerator timing skeletons for synchronization analysis.

Submodules:
- `collective`: Distributed communication analysis.
- `torch`: PyTorch specific profiling logic.
- `npu_sync`: Ascend/CANN MSPTI sync skeleton (opt-in).
"""
