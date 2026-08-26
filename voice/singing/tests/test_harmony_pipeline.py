import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_pipeline.py"
SPEC = importlib.util.spec_from_file_location("harmony_pipeline", SCRIPT)
PIPELINE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(PIPELINE)


def enabled_config(mode: str = "preserve") -> dict:
    return {"harmony": {"enabled": True, "mode": mode,
                         "separator_model": "UVR-BVE-4B_SN-44100-1",
                         "backing_gain_offset_db": -3.0}}


def test_legacy_config_preserves_v2_behavior() -> None:
    assert PIPELINE.normalize_harmony_config({}) == {
        "enabled": False, "mode": "legacy", "separator_model": None,
        "backing_gain_offset_db": 0.0,
    }


@pytest.mark.parametrize("mode", ["preserve", "remove"])
def test_harmony_modes_are_explicit_and_bve_mapping_is_known(mode: str) -> None:
    harmony = PIPELINE.normalize_harmony_config(enabled_config(mode))
    assert harmony["enabled"] is True
    assert harmony["mode"] == mode
    assert PIPELINE.harmony_stem_tokens(harmony) == (("instrument",), ("vocal",))


def test_convert_mode_is_reserved() -> None:
    with pytest.raises(ValueError, match="convert is reserved"):
        PIPELINE.normalize_harmony_config(enabled_config("convert"))


def test_custom_model_requires_explicit_stem_tokens() -> None:
    config = {"harmony": {"enabled": True, "mode": "preserve", "separator_model": "custom-model"}}
    harmony = PIPELINE.normalize_harmony_config(config)
    with pytest.raises(ValueError, match="requires lead_stem_tokens"):
        PIPELINE.harmony_stem_tokens(harmony)
    config["harmony"].update({"lead_stem_tokens": ["lead"], "backing_stem_tokens": ["backing"]})
    harmony = PIPELINE.normalize_harmony_config(config)
    assert PIPELINE.harmony_stem_tokens(harmony) == (("lead",), ("backing",))


def test_backing_validation_reports_a_silent_arrangement_without_rejecting_it(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    backing = tmp_path / "backing.wav"
    backing.write_bytes(b"wav")
    monkeypatch.setattr(PIPELINE, "audio_metrics", lambda *_: {
        "finite": True, "duration": 10.0, "sample_rate": 44100, "peak": 0.0, "rms": 0.0,
    })
    _, no_backing = PIPELINE.validate_backing_audio(Path("python"), backing, expected_duration=10.0)
    assert no_backing is True


@pytest.mark.parametrize(
    ("mode", "no_backing", "expected_inputs"),
    [("preserve", False, "inputs=3"), ("remove", False, "inputs=2"), ("preserve", True, "inputs=2")],
)
def test_mix_only_includes_backing_for_preserve_with_detected_backing(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, no_backing: bool, expected_inputs: str) -> None:
    paths = {key: tmp_path / key for key in ("mix", "stems", "seed_output", "final")}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    for path in (paths["mix"] / "instrumental.wav", paths["stems"] / "lead_vocals.wav",
                 paths["stems"] / "backing_vocals.wav", paths["seed_output"] / "yogurt_lead.wav"):
        path.write_bytes(b"wav")
    commands: list[list[str]] = []
    monkeypatch.setattr(PIPELINE, "ffmpeg_path", lambda *_: Path("ffmpeg"))
    monkeypatch.setattr(PIPELINE, "audio_metrics", lambda *_: {"rms": 0.2})
    monkeypatch.setattr(PIPELINE, "validate_audio", lambda *_args, **_kwargs: {
        "finite": True, "duration": 10.0, "sample_rate": 44100, "peak": 0.5, "rms": 0.2,
    })

    def fake_run(command: list[str], **_kwargs: object) -> None:
        commands.append(command)
        if str(command[-1]).endswith(".mp3"):
            Path(command[-1]).write_bytes(b"x" * 1025)

    monkeypatch.setattr(PIPELINE, "run", fake_run)
    metrics = PIPELINE.mix_stage(
        {"separation": {"python": "python"}, "mix": {"auto_gain": False, "limiter_ceiling_db": -1}},
        paths, 10.0, tmp_path / "pipeline.log",
        harmony=PIPELINE.normalize_harmony_config(enabled_config(mode)), no_backing_detected=no_backing,
    )
    assert expected_inputs in commands[0][commands[0].index("-filter_complex") + 1]
    assert metrics["backing_included"] is (mode == "preserve" and not no_backing)


def test_skip_harmony_fails_before_seed_vc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "song.mp3"
    source.write_bytes(b"audio")
    root = tmp_path / "runtime"
    cfg = {
        "runtime_root": str(root), "work_dir": str(root / "work"), "output_dir": str(root / "outputs"),
        "failed_dir": str(root / "failed"), "separation": {"python": str(source), "vocal_models": [],
        "instrumental_models": []}, "seed_vc": {}, "mix": {}, **enabled_config(),
    }
    metrics = {"finite": True, "duration": 10.0, "sample_rate": 44100, "peak": 0.5, "rms": 0.2}
    monkeypatch.setattr(PIPELINE.subprocess, "check_output", lambda *_args, **_kwargs: "2.1.3\nMock GPU\n")
    monkeypatch.setattr(PIPELINE, "ffmpeg_path", lambda *_: Path("ffmpeg"))
    monkeypatch.setattr(PIPELINE, "validate_audio", lambda *_args, **_kwargs: metrics)

    def fake_run(command: list[str], **_kwargs: object) -> None:
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"wav")

    def fake_separation(_cfg: dict, paths: dict, _duration: float, _log: Path, _warnings: list) -> dict:
        for name in ("vocals_for_seedvc.wav", "instrumental_clean.wav"):
            (paths["stems"] / name).write_bytes(b"wav")
        return {}

    seed_calls: list[bool] = []
    monkeypatch.setattr(PIPELINE, "run", fake_run)
    monkeypatch.setattr(PIPELINE, "separation_stage", fake_separation)
    monkeypatch.setattr(PIPELINE, "seed_vc_stage", lambda *_args, **_kwargs: seed_calls.append(True) or metrics)
    assert not PIPELINE.process_input(cfg, source, resume=False, skip_mix=False, separation_only=False,
                                      keep_work=True, skip_harmony_separation=True)
    assert seed_calls == []


def test_resume_reuses_validated_harmony_stems(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "song.mp3"
    source.write_bytes(b"audio")
    root = tmp_path / "runtime"
    job_id = "resume-job"
    work = root / "work" / job_id
    stems = root / "outputs" / job_id / "stems"
    work.mkdir(parents=True)
    (work / "decoded.wav").write_bytes(b"wav")
    stems.mkdir(parents=True)
    for name in ("vocals_for_seedvc.wav", "instrumental_clean.wav", "lead_vocals.wav", "backing_vocals.wav"):
        (stems / name).write_bytes(b"wav")
    cfg = {
        "runtime_root": str(root), "work_dir": str(root / "work"), "output_dir": str(root / "outputs"),
        "failed_dir": str(root / "failed"), "separation": {"python": str(source), "vocal_models": [],
        "instrumental_models": []}, "seed_vc": {}, "mix": {}, **enabled_config(),
    }
    metrics = {"finite": True, "duration": 10.0, "sample_rate": 44100, "peak": 0.5, "rms": 0.2}
    monkeypatch.setattr(PIPELINE.subprocess, "check_output", lambda *_args, **_kwargs: "2.1.3\nMock GPU\n")
    monkeypatch.setattr(PIPELINE, "ffmpeg_path", lambda *_: Path("ffmpeg"))
    monkeypatch.setattr(PIPELINE, "validate_audio", lambda *_args, **_kwargs: metrics)
    monkeypatch.setattr(PIPELINE, "validate_backing_audio", lambda *_args, **_kwargs: (metrics, False))
    monkeypatch.setattr(PIPELINE, "harmony_separation_stage", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("resume must not re-run harmony separation")))
    seed_inputs: list[Path] = []

    def fake_seed(_cfg: dict, paths: dict, _duration: float, _log: Path, *, input_stem: Path, output_name: str) -> dict:
        seed_inputs.append(input_stem)
        output = paths["seed_output"] / output_name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"wav")
        return metrics

    monkeypatch.setattr(PIPELINE, "seed_vc_stage", fake_seed)
    monkeypatch.setattr(PIPELINE, "mix_stage", lambda *_args, **_kwargs: {"backing_included": True})
    assert PIPELINE.process_input(cfg, source, resume=True, skip_mix=False, separation_only=False,
                                  keep_work=True, job_id=job_id, output_override=root / "outputs" / job_id)
    assert seed_inputs == [stems / "lead_vocals.wav"]
