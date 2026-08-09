# PYTHONPATH 指向本目录时自动安装 MSPTI Megatron 门控 hook。
import os

if os.environ.get("MSPTI_SKELETON", "").strip().lower() in ("1", "true", "yes", "on"):
    try:
        from megatron_mspti_hook import install

        install()
    except Exception as exc:  # noqa: BLE001
        print(f"[mspti_skeleton] sitecustomize install failed: {exc!r}", flush=True)
        raise
