"""Trusted offline cache resolver executed by the isolated Seed-VC Python."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
        from transformers import AutoFeatureExtractor, WhisperConfig

        checkpoints_root = Path.cwd() / "checkpoints"
        hf_cache_root = checkpoints_root / "hf_cache"
        hf_hub_download(
            repo_id="lj1995/VoiceConversionWebUI",
            filename="rmvpe.pt",
            cache_dir=checkpoints_root,
            local_files_only=True,
        )
        hf_hub_download(
            repo_id="funasr/campplus",
            filename="campplus_cn_common.bin",
            cache_dir=checkpoints_root,
            local_files_only=True,
        )
        snapshot_download(
            repo_id="nvidia/bigvgan_v2_44khz_128band_512x",
            cache_dir=hf_cache_root,
            local_files_only=True,
        )
        snapshot_download(
            repo_id="openai/whisper-small",
            cache_dir=hf_cache_root,
            local_files_only=True,
        )
        WhisperConfig.from_pretrained(
            "openai/whisper-small", cache_dir=hf_cache_root, local_files_only=True
        )
        AutoFeatureExtractor.from_pretrained(
            "openai/whisper-small", cache_dir=hf_cache_root, local_files_only=True
        )
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
