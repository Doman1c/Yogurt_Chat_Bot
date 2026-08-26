"""Run a non-destructive TTS sampling A/B capture; it never picks a winner."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:17861")
    parser.add_argument("--text", required=True)
    parser.add_argument("--language", choices=["auto", "zh", "ja", "en"], default="auto")
    parser.add_argument("--emotion", default="neutral")
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument("--variants", type=Path, default=Path(__file__).parents[1] / "configs" / "sampling.example.json")
    parser.add_argument("--output-root", type=Path, default=Path(r"E:\Yogurt_TTS_Train\runtime_info\mumble_ab"))
    args = parser.parse_args()
    variants = json.loads(args.variants.read_text(encoding="utf-8"))
    if set(variants) != {"baseline", "stable", "conservative"}:
        raise ValueError("variants config must define baseline, stable, and conservative")
    run = args.output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run.mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": datetime.now().isoformat(), "normalized_text": args.text, "segments": [args.text],
                "emotion": args.emotion, "language": args.language, "seed": args.seed, "variants": [],
                "decision": "No automatic winner selection."}
    for name, settings in variants.items():
        # The service may intentionally ignore unsupported sampling controls;
        # capture its response rather than assuming a change took effect.
        payload = {"text": args.text, "language": args.language, "emotion": args.emotion, "seed": args.seed, **settings}
        request = Request(f"{args.base_url.rstrip('/')}/v1/tts", data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        with urlopen(request, timeout=240) as response:
            audio = response.read()
            headers = dict(response.headers.items())
            normalized_headers = {key.lower(): value for key, value in headers.items()}
        target = run / f"{name}.wav"
        target.write_bytes(audio)
        manifest["variants"].append({"name": name, "sampling": settings, "audio": target.name,
                                     "bytes": len(audio), "elapsed_seconds": round(time.perf_counter() - started, 3),
                                     "reference_id": normalized_headers.get("x-yogurt-reference-id"),
                                     "response_headers": {key: value for key, value in headers.items() if key.lower().startswith("x-yogurt-")}})
    (run / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
