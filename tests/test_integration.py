"""실제 ffmpeg 로 2-pass 인코딩 후 결과를 ffprobe 로 검증함."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from rarbgenc import encoder, ffmpeg_tools
from rarbgenc.probe import probe


def _run_encode(ffmpeg_paths, src: Path, out: Path, track_order: int | None, language: str,
                workdir: Path, video_codec: str = "x264") -> None:
    if not ffmpeg_tools.has_encoder(ffmpeg_paths.ffmpeg, f"lib{video_codec}"):
        pytest.skip(f"ffmpeg with lib{video_codec} is not available")
    info = probe(ffmpeg_paths.ffprobe, src)
    track = info.audio_tracks[track_order] if track_order is not None else None
    job = encoder.EncodeJob(
        input_path=src,
        output_path=out,
        audio_stream_index=track.stream_index if track else None,
        audio_channels=track.channels if track else 0,
        language=language,
        description="Encoded using the same settings as rarbg",
        video_codec=video_codec,
    )
    passlog = workdir / "passlog"
    for build in (encoder.build_pass1, encoder.build_pass2):
        result = subprocess.run(
            build(ffmpeg_paths.ffmpeg, job, passlog), capture_output=True, cwd=workdir
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        errors = result.stderr.decode("utf-8", "replace").lower()
        assert "unknown option" not in errors, errors
        assert "error parsing option" not in errors, errors
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


def _encoder_settings(ffprobe: str, path: Path) -> str:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#1",
         "-show_packets", "-show_streams", "-show_data", "-of", "json", str(path)],
        capture_output=True, check=True,
    )
    data = json.loads(result.stdout)
    for dump in (data["packets"][0]["data"], data["streams"][0].get("extradata", "")):
        payload = bytes.fromhex("".join(line[10:49] for line in dump.splitlines() if line))
        match = re.search(rb"options: ([\x20-\x7e]+)", payload)
        if match:
            return match[1].decode("ascii")
    raise AssertionError("Encoder settings SEI not found")


def test_probe_sample(ffmpeg_paths, sample_dir):
    info = probe(ffmpeg_paths.ffprobe, sample_dir / "multi.mkv")
    assert info.has_video and info.duration == pytest.approx(3, abs=0.2)
    assert [(t.channels, t.language) for t in info.audio_tracks] == [(6, None), (2, "kor")]


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
@pytest.mark.parametrize(
    ("sample", "track", "language", "channels", "bitrate_max"),
    [
        ("multi.mkv", 0, "eng", 6, 260_000),  # 5.1 언어 없음 -> 사용자 지정 eng
        ("multi.mkv", 1, "kor", 2, 160_000),  # 스테레오 원본 kor
        ("mono.mkv", 0, "jpn", 1, 120_000),
    ],
)
def test_two_pass_encode(ffmpeg_paths, sample_dir, tmp_path, sample, track, language,
                         channels, bitrate_max, video_codec):
    src = sample_dir / sample
    out = tmp_path / "out" / f"{src.stem}_ENCODED.mp4"
    out.parent.mkdir()
    _run_encode(ffmpeg_paths, src, out, track, language, tmp_path, video_codec)

    data = _ffprobe_json(ffmpeg_paths.ffprobe, out)
    streams = data["streams"]
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    video, audio = streams
    assert video["codec_name"] == ("hevc" if video_codec == "x265" else "h264")
    assert audio["codec_name"] == "aac"
    assert audio["channels"] == channels
    assert int(audio["bit_rate"]) <= bitrate_max
    assert audio["sample_rate"] == "48000"
    assert audio["tags"]["language"] == language

    tags = data["format"]["tags"]
    assert tags["title"] == src.stem
    assert tags["comment"] == src.stem
    assert tags["description"] == "Encoded using the same settings as rarbg"
    assert "creation_time" in tags
    # 패스 로그는 작업 폴더(임시)에만 생성되어야 함
    assert list(out.parent.iterdir()) == [out]


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
def test_two_pass_encode_without_audio(ffmpeg_paths, sample_dir, tmp_path, video_codec):
    src = sample_dir / "silent.mkv"
    out = tmp_path / "silent_ENCODED.mp4"
    _run_encode(ffmpeg_paths, src, out, None, "eng", tmp_path, video_codec)
    streams = _ffprobe_json(ffmpeg_paths.ffprobe, out)["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]
    assert streams[0]["codec_name"] == ("hevc" if video_codec == "x265" else "h264")
    assert streams[0]["pix_fmt"] == ("yuv420p10le" if video_codec == "x265" else "yuv420p")
    assert streams[0]["profile"] == ("Main 10" if video_codec == "x265" else "High")
    settings = _encoder_settings(ffmpeg_paths.ffprobe, out).split()
    if video_codec == "x265":
        expected = (
            "ref=4", "bframes=4", "b-adapt=2", "rc-lookahead=25", "min-keyint=23",
            "keyint=250", "open-gop", "rect", "no-amp", "rdoq-level=2", "max-merge=3",
            "limit-refs=3", "limit-modes", "me=3", "subme=3", "merange=57",
            "deblock=0:0", "no-sao", "rd=4", "psy-rd=2.00", "psy-rdoq=1.00",
            "rc=abr", "bitrate=2000", "stats-write=0", "stats-read=2",
            "aq-mode=3", "aq-strength=1.00", "cutree", "qg-size=32",
        )
    else:
        assert streams[0]["level"] == 41
        expected = (
            "ref=4", "deblock=1:-1:-1", "me=umh", "subme=9", "psy_rd=1.00:0.15",
            "me_range=24", "trellis=2", "chroma_qp_offset=-3",
            "b_adapt=2", "direct=3", "keyint=250", "keyint_min=25", "rc_lookahead=50",
            "rc=2pass", "bitrate=2500", "vbv_maxrate=31250", "vbv_bufsize=31250",
            "aq=3:1.00",
        )
        thread_option = next(value for value in settings if value.startswith("lookahead_threads="))
        assert 1 <= int(thread_option.partition("=")[2]) <= 4
    assert set(expected) <= set(settings), settings


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
def test_ten_bit_input_output_format(ffmpeg_paths, sample_dir, tmp_path, video_codec):
    src = tmp_path / "tenbit.mkv"
    subprocess.run(
        [ffmpeg_paths.ffmpeg, "-y", "-v", "error", "-i", str(sample_dir / "silent.mkv"),
         "-c:v", "ffv1", "-pix_fmt", "yuv444p10le", str(src)],
        check=True, capture_output=True,
    )
    out = tmp_path / "converted.mp4"
    _run_encode(ffmpeg_paths, src, out, None, "eng", tmp_path, video_codec)
    video = _ffprobe_json(ffmpeg_paths.ffprobe, out)["streams"][0]
    assert video["pix_fmt"] == ("yuv420p10le" if video_codec == "x265" else "yuv420p")
