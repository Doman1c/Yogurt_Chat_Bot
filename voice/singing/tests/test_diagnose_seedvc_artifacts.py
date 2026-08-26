from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from diagnose_seedvc_artifacts import calculate_boundaries, nearest_boundary, parse_ranges, parse_steps


def test_parsers() -> None:
    assert parse_ranges("18:26,101:109") == [(18.0, 26.0), (101.0, 109.0)]
    assert parse_steps("30,40,50") == [30, 40, 50]


def test_seedvc_boundary_math_uses_overlap_advance() -> None:
    values = calculate_boundaries(
        source_duration=20.0,
        sample_rate=44100,
        reference_duration=25.0,
        n_fft=2048,
        hop_length=512,
    )
    assert values["max_context_frames"] == 2580
    assert values["reference_mel_frames"] == 2153
    assert values["max_source_mel_frames"] == 427
    assert values["advance_frames"] == 411
    assert values["boundaries_seconds"][0] == 411 * 512 / 44100


def test_proximity_thresholds() -> None:
    assert nearest_boundary(5.5, [5.0])["proximity"] == "VERY_CLOSE"
    assert nearest_boundary(6.5, [5.0])["proximity"] == "CLOSE"
    assert nearest_boundary(7.0, [5.0])["proximity"] == "NOT_CLOSE"
