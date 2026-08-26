"""Bounded CLI wrapper around the separately installed Seed-VC inference.py."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True, help="local source vocal WAV")
    parser.add_argument("--target", type=Path, required=True, help="local target reference WAV")
    parser.add_argument("--diffusion-steps", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.diffusion_steps <= 100:
        raise SystemExit("--diffusion-steps must be between 1 and 100")
    for item in (args.source, args.target):
        if not item.is_file():
            raise SystemExit(f"missing audio: {item}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    home = Path(config["seed_vc_home"]).resolve()
    output = Path(config["output_dir"]).resolve()
    configured_cache = Path(config["cache_dir"]).resolve()
    upstream_cache = (home / "checkpoints" / "hf_cache").resolve()
    if configured_cache != upstream_cache:
        raise SystemExit(
            "Pinned Seed-VC inference.py overrides HF_HUB_CACHE with "
            f"{upstream_cache}. Set cache_dir to that path (or make it resolve "
            "there via a local junction/symlink) before running offline."
        )
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "HF_HOME": str(upstream_cache),
        "HF_HUB_CACHE": str(upstream_cache),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    command = [
        config["python"], str(home / "inference.py"), "--source", str(args.source), "--target", str(args.target),
        "--output", str(output), "--checkpoint", config["checkpoint"], "--config", config["config"],
        "--diffusion-steps", str(args.diffusion_steps), "--f0-condition", "true", "--fp16", "true",
    ]
    return subprocess.run(command, cwd=home, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
