"""시스템 ffmpeg 탐색, 자동 다운로드(static-ffmpeg), OS 별 설치 안내."""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rarbgenc.settings import data_dir

_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_EXE = ".exe" if sys.platform == "win32" else ""


@dataclass(frozen=True)
class FFmpegPaths:
    ffmpeg: str
    ffprobe: str
    source: str  # "system" 또는 "bundled"


def _platform_key() -> str:
    from static_ffmpeg.run import get_platform_key

    return get_platform_key()


def bundled_dir() -> Path:
    """자동 설치한 ffmpeg 위치 (zip 내부 폴더명이 플랫폼 키이므로 마지막 경로를 맞춤)"""
    return data_dir() / "ffmpeg" / _platform_key()


def _bundled_paths() -> FFmpegPaths | None:
    try:
        directory = bundled_dir()
    except OSError:
        return None
    ffmpeg = directory / f"ffmpeg{_EXE}"
    ffprobe = directory / f"ffprobe{_EXE}"
    if (directory / "installed.crumb").exists() and ffmpeg.exists() and ffprobe.exists():
        return FFmpegPaths(str(ffmpeg), str(ffprobe), "bundled")
    return None


def locate() -> FFmpegPaths | None:
    """시스템 PATH 우선, 없으면 자동 설치본 사용"""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return FFmpegPaths(ffmpeg, ffprobe, "system")
    return _bundled_paths()


def download() -> FFmpegPaths:
    """static-ffmpeg 로 정적 빌드를 내려받음. 네트워크 오류 시 예외 발생"""
    # progress 라이브러리가 import 시점의 sys.stderr 를 사용하므로 콘솔이 없는 경우를 대비함
    sink = io.StringIO()
    stdout = sys.stdout or sink
    stderr = sys.stderr or sink
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise

        directory = bundled_dir()
        directory.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg, ffprobe = get_or_fetch_platform_executables_else_raise(
            download_dir=str(directory)
        )
    return FFmpegPaths(ffmpeg, ffprobe, "bundled")


def version(ffmpeg: str) -> str:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-version"],
            capture_output=True, timeout=20, creationflags=_CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    first = result.stdout.decode("utf-8", "replace").splitlines()
    return first[0] if first else ""


def has_encoder(ffmpeg: str, name: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, timeout=20, creationflags=_CREATION_FLAGS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def install_hint() -> str:
    """현재 OS 에 맞는 ffmpeg 설치 안내"""
    if sys.platform == "win32":
        return (
            "Install with one of the following, then restart rarbgenc:\n"
            "  winget install --id Gyan.FFmpeg -e\n"
            "  scoop install ffmpeg\n"
            "  choco install ffmpeg"
        )
    if sys.platform == "darwin":
        return "Install with Homebrew, then restart rarbgenc:\n  brew install ffmpeg"
    return (
        "Install with your package manager, then restart rarbgenc:\n"
        "  sudo apt install ffmpeg      (Debian/Ubuntu)\n"
        "  sudo dnf install ffmpeg      (Fedora, RPM Fusion)\n"
        "  sudo pacman -S ffmpeg        (Arch)"
    )
