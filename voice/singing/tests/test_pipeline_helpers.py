import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_safe_name_and_stable_job_id(tmp_path: Path) -> None:
    source = tmp_path / "A song (demo).mp3"
    source.write_bytes(b"audio")
    assert MODULE.safe_name(source.stem) == "A_song_demo"
    assert MODULE.job_id_for(source) == MODULE.job_id_for(source)


def test_supported_input_extensions() -> None:
    assert {".mp3", ".wav", ".flac", ".m4a"} == MODULE.SUPPORTED_INPUTS
