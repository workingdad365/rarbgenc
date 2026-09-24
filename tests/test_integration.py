"""실제 ffmpeg 로 2-pass 인코딩 후 결과를 ffprobe 로 검증함."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rarbgenc import encoder
from rarbgenc.probe import probe


def _run_encode(ffmpeg_paths, src: Path, out: Path, track_order: int | None, language: str,
                workdir: Path) -> None:
    info = probe(ffmpeg_paths.ffprobe, src)
    track = info.audio_tracks[track_order] if track_order is not None else None
    job = encoder.EncodeJob(
        input_path=src,
        output_path=out,
        audio_stream_index=track.stream_index if track else None,
        audio_channels=track.channels if track else 0,
        language=language,
        description="Encoded using the same settings as rarbg",
    )
    passlog = workdir / "passlog"
    for build in (encoder.build_pass1, encoder.build_pass2):
        result = subprocess.run(
            build(ffmpeg_paths.ffmpeg, job, passlog), capture_output=True, cwd=workdir
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        # -progress 출력에서 진행 시각이 파싱되는지 확인
        seconds = [
            encoder.parse_progress_seconds(line)
            for line in result.stdout.decode().splitlines()
        ]
        assert any(s is not None and s > 0 for s in seconds)


def _ffprobe_json(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_probe_sample(ffmpeg_paths, sample_dir):
    info = probe(ffmpeg_paths.ffprobe, sample_dir / "multi.mkv")
    assert info.has_video and info.duration == pytest.approx(3, abs=0.2)
    assert [(t.channels, t.language) for t in info.audio_tracks] == [(6, None), (2, "kor")]


@pytest.mark.parametrize(
    ("sample", "track", "language", "channels", "bitrate_max"),
    [
        ("multi.mkv", 0, "eng", 6, 260_000),  # 5.1 언어 없음 -> 사용자 지정 eng
        ("multi.mkv", 1, "kor", 2, 160_000),  # 스테레오 원본 kor
        ("mono.mkv", 0, "jpn", 1, 120_000),
    ],
)
def test_two_pass_encode(ffmpeg_paths, sample_dir, tmp_path, sample, track, language,
                         channels, bitrate_max):
    src = sample_dir / sample
    out = tmp_path / "out" / f"{src.stem}_ENCODED.mp4"
    out.parent.mkdir()
    _run_encode(ffmpeg_paths, src, out, track, language, tmp_path)

    data = _ffprobe_json(ffmpeg_paths.ffprobe, out)
    streams = data["streams"]
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    video, audio = streams
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert audio["channels"] == channels
    assert int(audio["bit_rate"]) <= bitrate_max
    assert audio["tags"]["language"] == language

    tags = data["format"]["tags"]
    assert tags["title"] == src.stem
    assert tags["comment"] == src.stem
    assert tags["description"] == "Encoded using the same settings as rarbg"
    assert "creation_time" in tags
    # 패스 로그는 작업 폴더(임시)에만 생성되어야 함
    assert list(out.parent.iterdir()) == [out]


def test_two_pass_encode_without_audio(ffmpeg_paths, sample_dir, tmp_path):
    src = sample_dir / "silent.mkv"
    out = tmp_path / "silent_ENCODED.mp4"
    _run_encode(ffmpeg_paths, src, out, None, "eng", tmp_path)
    streams = _ffprobe_json(ffmpeg_paths.ffprobe, out)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]
