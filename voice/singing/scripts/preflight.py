"""Configuration-only preflight for a local Seed-VC installation."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


PINNED_COMMIT = "51383efd921027683c89e5348211d93ff12ac2a8"


def check(label: str, ok: bool, detail: str, failures: list[str]) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    if not ok:
        failures.append(label)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True, help="local JSON derived from configs/runtime.example.json")
    args = parser.parse_args()
    data = json.loads(args.config.read_text(encoding="utf-8"))
    root = Path(data["seed_vc_home"]).resolve()
    python = Path(data["python"]).resolve()
    failures: list[str] = []
    check("Python", python.is_file(), str(python), failures)
    check("Seed-VC checkout", (root / "inference.py").is_file(), str(root), failures)
    for key in ("checkpoint", "config"):
        check(key, Path(data[key]).is_file(), data[key], failures)
    expected_cache = (root / "checkpoints" / "hf_cache").resolve()
    configured_cache = Path(data["cache_dir"]).resolve()
    check(
        "Seed-VC cache path",
        configured_cache == expected_cache,
        f"configured={configured_cache} / pinned-upstream={expected_cache}",
        failures,
    )
    for key in ("cache_dir", "output_dir"):
        path = Path(data[key])
        try:
            path.mkdir(parents=True, exist_ok=True)
            check(f"{key} writable", path.is_dir(), str(path), failures)
        except OSError as exc:
            check(f"{key} writable", False, str(exc), failures)
    try:
        head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        check("Seed-VC commit", head == PINNED_COMMIT, head, failures)
    except Exception as exc:
        check("Seed-VC commit", False, str(exc), failures)
    try:
        result = subprocess.check_output([str(python), "-c", "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"], text=True).splitlines()
        check("CUDA", result and result[0] == "True", " / ".join(result), failures)
    except Exception as exc:
        check("CUDA", False, str(exc), failures)
    print(f"STATUS: {'READY' if not failures else 'NOT READY'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
