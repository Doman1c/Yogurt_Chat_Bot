"""Run sanitized replay fixtures and emit aggregate metadata only."""

import argparse
import asyncio
from pathlib import Path
import sys

from .comparison import report_json
from .contracts import ShadowFixtureSet
from .replay import replay


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline W2 structural comparison; not semantic accuracy or promotion evidence.")
    parser.add_argument("fixtures", type=Path, help="explicit local JSON fixture file")
    args = parser.parse_args()
    try:
        # Validation errors can include raw inputs. Never print them or a traceback.
        if args.fixtures.stat().st_size > 4_000_000:
            raise ValueError("fixture too large")
        fixtures = ShadowFixtureSet.model_validate_json(args.fixtures.read_bytes())
        _, summary = asyncio.run(replay(fixtures))
    except Exception:
        print("W2_REPLAY_FAILED: invalid fixture or unavailable isolated replay", file=sys.stderr)
        return 1
    sys.stdout.write(report_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
