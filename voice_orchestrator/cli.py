from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Optional

from .client import VoiceRuntimeClient, VoiceRuntimeError
from .policy import VoicePlanError, plan_voice
from .schemas import VoicePlanInput


async def _run(args: argparse.Namespace) -> int:
    plan = VoicePlanInput(
        text=args.text,
        language=args.language,
        scene=args.scene,
        mood=args.mood,
    )
    request = plan_voice(plan)
    client = VoiceRuntimeClient(base_url=args.runtime_url, timeout_seconds=args.timeout)

    if not await client.reachable():
        raise VoiceRuntimeError("voice_runtime_not_ready")

    result = await client.synthesize(request)
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result.audio)

    print("VOICE ORCHESTRATOR OK")
    print(f"scene = {plan.scene}")
    print(f"emotion = {request.emotion}")
    print(f"intensity = {request.intensity:.2f}")
    print(f"speed = {result.speed or 'runtime-default'}")
    print(f"bytes = {len(result.audio)}")
    print(f"output = {output}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Yogurt P10-B local Voice Orchestrator proof")
    parser.add_argument("--text", required=True)
    parser.add_argument("--language", choices=["zh", "ja"], default="zh")
    parser.add_argument(
        "--scene",
        choices=["casual", "technical", "playful", "comfort", "celebration", "serious"],
        default="casual",
    )
    parser.add_argument(
        "--mood",
        choices=["neutral", "happy", "soft", "shy", "sad", "angry", "surprised"],
        default=None,
    )
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8770")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", default="yogurt_orchestrator_test.wav")
    args = parser.parse_args()

    try:
        raise SystemExit(asyncio.run(_run(args)))
    except (VoicePlanError, VoiceRuntimeError) as exc:
        raise SystemExit(f"Voice Orchestrator error: {exc}") from exc


if __name__ == "__main__":
    main()
