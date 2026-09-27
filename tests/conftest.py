"""공통 fixture: ffmpeg 탐색, 테스트용 샘플 동영상 생성, 설정 파일 격리."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rarbgenc import ffmpeg_tools, settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """실제 사용자 설정 파일을 건드리지 않도록 경로를 임시 폴더로 교체"""
    path = tmp_path / "config" / "settings.json"
    monkeypatch.setattr(settings, "default_settings_path", lambda: path)
    return path


@pytest.fixture(scope="session")
def ffmpeg_paths():
    paths = ffmpeg_tools.locate()
    if paths is None or not ffmpeg_tools.has_encoder(paths.ffmpeg, "libx264"):
        pytest.skip("ffmpeg with libx264 is not available")
    return paths


def _make_sample(ffmpeg: str, out: Path, audio_specs: list[tuple[str, str | None]],
           size: str = "320x240", duration: float = 3) -> Path:
    """lavfi 샘플 생성. audio_specs: (channel_layout, language 또는 None)"""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={duration}"]
    for layout, _lang in audio_specs:
     cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration},aformat=channel_layouts={layout}"]
    cmd += ["-map", "0:v"]
    for i in range(len(audio_specs)):
        cmd += ["-map", f"{i + 1}:a"]
    cmd += ["-c:v", "mpeg4", "-c:a", "ac3" if audio_specs else "copy"]
    for i, (_layout, lang) in enumerate(audio_specs):
        # 언어 태그를 명시적으로 비워 '정보 없음' 상태를 재현
        cmd += [f"-metadata:s:a:{i}", f"language={lang or ''}"]
    cmd.append(str(out))
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory, ffmpeg_paths) -> Path:
    directory = tmp_path_factory.mktemp("samples")
    ff = ffmpeg_paths.ffmpeg
    # 1번 트랙: 5.1 언어 없음, 2번 트랙: 스테레오 kor
    _make_sample(ff, directory / "multi.mkv", [("5.1", None), ("stereo", "kor")])
    _make_sample(ff, directory / "mono.mkv", [("mono", "jpn")])
    _make_sample(ff, directory / "silent.mkv", [])
    return directory


@pytest.fixture(scope="session")
def hd_sample_dir(tmp_path_factory, ffmpeg_paths) -> Path:
    directory = tmp_path_factory.mktemp("hd_samples")
    _make_sample(
        ffmpeg_paths.ffmpeg, directory / "multi.mkv", [("5.1", None), ("stereo", "kor")],
        size="1920x1080", duration=1,
    )
    return directory
