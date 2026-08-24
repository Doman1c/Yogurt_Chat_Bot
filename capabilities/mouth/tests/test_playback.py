from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from capabilities.mouth.errors import PlaybackError
from capabilities.mouth.playback import (
    AudioArtifact,
    MacOSPlaybackSink,
    MemoryPlaybackSink,
    PlaybackController,
    PlaybackState,
)


def _artifact() -> AudioArtifact:
    return AudioArtifact(audio=b"RIFFtest", media_type="audio/wav")


def test_memory_sink_normal_completion() -> None:
    async def run() -> None:
        sink = MemoryPlaybackSink()
        handle = PlaybackController().start(_artifact(), sink)
        assert await handle.wait() == PlaybackState.FINISHED
        assert sink.artifacts == [_artifact()]

    asyncio.run(run())


def test_playback_cancellation_stops_blocking_sink() -> None:
    async def run() -> None:
        sink = MemoryPlaybackSink(block=True)
        handle = PlaybackController().start(_artifact(), sink)
        await sink.started.wait()
        assert handle.cancel() is True
        assert await handle.wait() == PlaybackState.CANCELLED

    asyncio.run(run())


def test_invalid_audio_is_rejected_before_sink_use() -> None:
    unused_sink = object()
    with pytest.raises(PlaybackError, match="unsupported_media_type"):
        PlaybackController().start(AudioArtifact(b"test", "audio/mpeg"), unused_sink)
    with pytest.raises(PlaybackError, match="empty_audio"):
        PlaybackController().start(AudioArtifact(b"", "audio/wav"), unused_sink)


def test_macos_sink_uses_fixed_argv_and_cleans_temp_file(tmp_path) -> None:
    class Process:
        returncode = 0

        async def wait(self):
            return 0

    async def run() -> None:
        sink = MacOSPlaybackSink()
        cancelled = asyncio.Event()
        with patch("os.path.isfile", return_value=True), patch(
            "asyncio.create_subprocess_exec", new=AsyncMock(return_value=Process())
        ) as create_process:
            with patch("tempfile.NamedTemporaryFile") as temporary:
                handle = temporary.return_value.__enter__.return_value
                handle.name = str(tmp_path / "voice.wav")
                await sink.play(_artifact(), cancelled)
        assert create_process.await_args.args[0] == "/usr/bin/afplay"
        assert create_process.await_args.args[1] == str(tmp_path / "voice.wav")

    asyncio.run(run())
