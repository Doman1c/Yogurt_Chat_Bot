import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_pipeline.py"
SPEC = importlib.util.spec_from_file_location("singing_pipeline", SCRIPT)
PIPELINE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(PIPELINE)


def config(root: Path) -> dict[str, str]:
    return {
        "runtime_root": str(root), "incoming_dir": str(root / "incoming"),
        "processing_dir": str(root / "processing"), "archive_dir": str(root / "archive"),
        "work_dir": str(root / "work"), "output_dir": str(root / "outputs"),
        "failed_dir": str(root / "failed"), "runtime_info_dir": str(root / "runtime_info"),
    }


def inbox_file(cfg: dict[str, str], name: str = "song.mp3", content: bytes = b"audio") -> Path:
    path = Path(cfg["incoming_dir"]) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_stability_and_temp_file_filter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = config(tmp_path)
    song = inbox_file(cfg)
    monkeypatch.setattr(PIPELINE.time, "sleep", lambda _: None)
    assert PIPELINE.is_candidate(song)
    assert PIPELINE.wait_until_stable(song, 2)
    assert not PIPELINE.is_candidate(inbox_file(cfg, "copy.mp3.part"))
    with pytest.raises(ValueError):
        PIPELINE.wait_until_stable(song, 1)


def test_success_archives_source_and_deduplicates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = config(tmp_path)
    with PIPELINE.JobHistory(Path(cfg["runtime_info_dir"]) / "job_history.sqlite3") as history:
        claim = PIPELINE.claim_from_inbox(cfg, history, inbox_file(cfg), force=False)
        assert claim is not None
        job_id, source, _, _ = claim
        monkeypatch.setattr(PIPELINE, "process_input", lambda *args, **kwargs: True)
        assert PIPELINE.process_claimed(cfg, history, job_id, source, resume=False, skip_mix=False,
                                        separation_only=False, keep_work=False)
        assert not source.exists()
        assert list((tmp_path / "archive").glob("song.mp3"))
        duplicate = inbox_file(cfg, "again.mp3")
        duplicate.write_bytes(b"audio")
        assert PIPELINE.claim_from_inbox(cfg, history, duplicate, force=False) is None
        assert list((tmp_path / "archive" / "duplicates").glob("again.mp3"))


def test_force_creates_new_attempt_and_failure_keeps_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = config(tmp_path)
    with PIPELINE.JobHistory(Path(cfg["runtime_info_dir"]) / "job_history.sqlite3") as history:
        first = PIPELINE.claim_from_inbox(cfg, history, inbox_file(cfg), force=True)
        assert first is not None
        monkeypatch.setattr(PIPELINE, "process_input", lambda *args, **kwargs: False)
        assert not PIPELINE.process_claimed(cfg, history, first[0], first[1], resume=False, skip_mix=False,
                                            separation_only=False, keep_work=False)
        assert (tmp_path / "failed" / first[0] / "source" / "song.mp3").is_file()
        second = PIPELINE.claim_from_inbox(cfg, history, inbox_file(cfg, "song.mp3", b"audio"), force=True)
        assert second is not None and second[0] != first[0]


def test_interrupted_state_is_recoverable_and_lock_is_exclusive(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    processing = tmp_path / "processing" / "job" / "song.mp3"
    processing.parent.mkdir(parents=True)
    processing.write_bytes(b"audio")
    with PIPELINE.JobHistory(Path(cfg["runtime_info_dir"]) / "job_history.sqlite3") as history:
        history.create(job_id="job", attempt_id="job:attempt1", sha256="a" * 64, original_filename="song.mp3",
                       processing_path=processing, force=False, pipeline_version="2", state="running", stage="MIX")
        recovered = history.recover_interrupted()
        assert recovered[0]["job_id"] == "job"
        assert "--resume" in recovered[0]["error"]
    lock = PIPELINE.PipelineLock(Path(cfg["runtime_info_dir"]) / "singing_pipeline.lock")
    lock.acquire()
    with pytest.raises(PIPELINE.PipelineAlreadyRunning, match="PIPELINE_ALREADY_RUNNING"):
        PIPELINE.PipelineLock(lock.path).acquire()
    lock.release()
