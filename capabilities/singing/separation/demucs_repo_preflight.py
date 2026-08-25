"""Trusted LocalRepo completeness check executed by the Demucs Python runtime."""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--model-name", required=True)
    args = parser.parse_args(argv)
    try:
        import yaml
        from demucs.repo import LocalRepo

        if not args.repo.is_dir():
            return 2
        bag_path = args.repo / f"{args.model_name}.yaml"
        if not bag_path.is_file():
            return 2
        bag = yaml.safe_load(bag_path.read_text())
        signatures = bag.get("models") if isinstance(bag, dict) else None
        if not isinstance(signatures, list) or not signatures or not all(
            isinstance(signature, str) and signature for signature in signatures
        ):
            return 2
        model_repo = LocalRepo(args.repo)
        if not all(model_repo.has_model(signature) for signature in signatures):
            return 2
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
