"""Trusted local bridge for Vevo2 FM SVC; invoked only by ``Vevo2Adapter``.

It deliberately uses a pre-downloaded model root and does not call the
upstream demo helper that performs an implicit Hugging Face download.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--amphion-root", required=True)
    args = parser.parse_args(argv)
    amphion_root = Path(args.amphion_root)
    model_root = Path(args.model_root)
    if not amphion_root.is_dir() or not model_root.is_dir():
        return 2
    sys.path.insert(0, str(amphion_root))
    import torch
    from models.svc.vevo2.vevo2_utils import Vevo2InferencePipeline, save_audio

    pipeline = Vevo2InferencePipeline(
        content_style_tokenizer_ckpt_path=model_root / "tokenizer/contentstyle_fvq16384_12.5hz",
        fmt_cfg_path=model_root / "acoustic_modeling/fm_emilia101k_singnet7k_repa/config.json",
        fmt_ckpt_path=model_root / "acoustic_modeling/fm_emilia101k_singnet7k_repa",
        vocoder_cfg_path=model_root / "vocoder/config.json",
        vocoder_ckpt_path=model_root / "vocoder",
        device=torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu"),
    )
    audio = pipeline.inference_fm(
        src_wav_path=args.source,
        timbre_ref_wav_path=args.reference,
        use_pitch_shift=True,
        flow_matching_steps=32,
    )
    save_audio(audio, output_path=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
