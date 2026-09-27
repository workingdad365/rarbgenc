"""ffprobe 로 원본 동영상 정보를 읽음."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rarbgenc import languages

# Windows 에서 콘솔 창 생성 방지
_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class ProbeError(RuntimeError):
    pass


@dataclass
class AudioTrack:
    stream_index: int  # 파일 전체 기준 스트림 인덱스 (-map 0:N)
    order: int  # 오디오 스트림 중 순번 (0부터)
    codec: str
    channels: int
    channel_layout: str
    language: str | None  # 정규화된 ISO 639-2 코드, 없으면 None
    title: str
    is_default: bool

    def label(self) -> str:
        parts = [f"#{self.order + 1}", self.language or "(no language)", self.codec]
        parts.append(self.channel_layout or f"{self.channels}ch")
        if self.title:
            parts.append(f'"{self.title}"')
        if self.is_default:
            parts.append("[default]")
        return "  ".join(parts)


@dataclass
class MediaInfo:
    path: Path
    duration: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_tracks: list[AudioTrack] = field(default_factory=list)

    @property
    def has_video(self) -> bool:
        return self.video_codec is not None

    @property
    def is_1080p(self) -> bool:
        width, height = self.width, self.height
        if not self.has_video or not isinstance(width, int) or not isinstance(height, int):
            return False
        return (
            0 < width <= 1920 and 0 < height <= 1080
            and width % 2 == 0 and height % 2 == 0
            and (width == 1920 or height == 1080)
        )

    def summary(self) -> str:
        parts = []
        if self.video_codec:
            parts.append(f"{self.video_codec} {self.width}x{self.height}")
        if self.duration:
            total = int(self.duration)
            parts.append(f"{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}")
        parts.append(f"{len(self.audio_tracks)} audio track(s)")
        return "  |  ".join(parts)


def _to_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def parse_probe(path: Path, data: dict) -> MediaInfo:
    """ffprobe JSON 출력을 MediaInfo 로 변환함"""
    streams = data.get("streams") or []
    fmt = data.get("format") or {}

    video = next(
        (
            s for s in streams
            if s.get("codec_type") == "video"
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )

    duration = _to_float(fmt.get("duration"))
    if duration is None and video is not None:
        duration = _to_float(video.get("duration"))

    tracks: list[AudioTrack] = []
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        tracks.append(
            AudioTrack(
                stream_index=int(s.get("index", 0)),
                order=len(tracks),
                codec=s.get("codec_name") or "unknown",
                channels=int(s.get("channels") or 0),
                channel_layout=s.get("channel_layout") or "",
                language=languages.normalize(tags.get("language")),
                title=tags.get("title") or "",
                is_default=bool((s.get("disposition") or {}).get("default")),
            )
        )

    return MediaInfo(
        path=path,
        duration=duration,
        width=video.get("width") if video else None,
        height=video.get("height") if video else None,
        video_codec=video.get("codec_name") if video else None,
        audio_tracks=tracks,
    )


def probe(ffprobe: str, path: Path, timeout: float = 60) -> MediaInfo:
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout, creationflags=_CREATION_FLAGS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"ffprobe execution failed: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ProbeError(message or f"ffprobe exited with code {result.returncode}")
    try:
        data = json.loads(result.stdout.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ProbeError(f"Invalid ffprobe output: {exc}") from exc
    return parse_probe(path, data)
