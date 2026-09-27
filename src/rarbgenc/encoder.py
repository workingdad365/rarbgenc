"""RARBG 샘플 기반 x264/x265 2-pass 인코딩 명령 생성."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VIDEO_BITRATE = "2500k"
PASS1_X264_PARAMS = "b-adapt=2:rc-lookahead=50"
PASS2_X264_PARAMS = (
    "me=umh:subme=9:me_range=24:ref=4:trellis=2:lookahead_threads=4:direct=auto"
    ":keyint_min=25:vbv_maxrate=31250:vbv_bufsize=31250:filler=0:psy_rd=1.00,0.15"
    ":aq-mode=3:deblock=-1,-1:chroma_qp_offset=0:b-adapt=2:rc-lookahead=50"
)
X265_PARAMS = (
    "ref=4:bframes=4:b-adapt=2:rc-lookahead=25:lookahead-slices=4"
    ":keyint=250:min-keyint=23:open-gop=1:rect=1:amp=0:rdoq-level=2"
    ":max-merge=3:limit-refs=3:limit-modes=1:me=3:subme=3:merange=57"
    ":weightp=1:weightb=0:deblock=0,0:sao=0:rd=4:rskip=1"
    ":psy-rd=2.0:psy-rdoq=1.0:aq-mode=3:aq-strength=1.0:cutree=1"
    ":qcomp=0.6:qpstep=4:ipratio=1.4:pbratio=1.3:qg-size=32"
    ":frame-threads=4"
)

# 전체 진행률 계산 시 각 pass 가중치 (1pass 는 fastfirstpass 로 상대적으로 빠름)
PASS1_WEIGHT = 0.3
PASS2_WEIGHT = 1.0 - PASS1_WEIGHT


@dataclass(frozen=True)
class AudioProfile:
    bitrate: str
    channels: int


def audio_profile(source_channels: int) -> AudioProfile:
    """원본 채널 수에 따른 AAC 출력 설정.

    5.1 이상(6ch 이상) -> 224k 6ch, 2~5ch -> 128k 2ch (다운믹스), 모노 -> 96k 1ch.
    채널 정보를 알 수 없으면 스테레오로 처리함.
    """
    if source_channels >= 6:
        return AudioProfile("224k", 6)
    if source_channels == 1:
        return AudioProfile("96k", 1)
    return AudioProfile("128k", 2)


@dataclass
class EncodeJob:
    input_path: Path
    output_path: Path
    audio_stream_index: int | None  # None 이면 오디오 없음
    audio_channels: int
    language: str
    description: str
    video_codec: str = "x264"

    def __post_init__(self) -> None:
        if self.video_codec not in ("x264", "x265"):
            raise ValueError(f"Unsupported video codec: {self.video_codec}")

    @property
    def title(self) -> str:
        return self.input_path.stem


def output_path_for(input_path: Path, output_dir: str, suffix: str) -> Path:
    """출력 파일 경로 생성. output_dir 가 비어 있으면 원본과 같은 폴더"""
    directory = Path(output_dir).expanduser() if output_dir.strip() else input_path.parent
    return directory / f"{input_path.stem}{suffix}.mp4"


def _common_prefix(ffmpeg: str, job: EncodeJob) -> list[str]:
    return [
        ffmpeg, "-y", "-hide_banner", "-nostdin", "-nostats",
        "-loglevel", "error", "-progress", "pipe:1",
        "-i", str(job.input_path),
        "-map", "0:V:0",
    ]


def _video_options(job: EncodeJob, passlog: Path, pass_no: int) -> list[str]:
    if job.video_codec == "x265":
        stats = passlog.as_posix().replace(":", "\\:")
        return [
            "-c:v", "libx265", "-preset", "slow",
            "-pix_fmt", "yuv420p10le", "-profile:v", "main10",
            "-b:v", "2000k",
            "-x265-params", f"{X265_PARAMS}:pass={pass_no}:stats={stats}",
        ]
    return [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-level:v", "4.1", "-pass", str(pass_no), "-passlogfile", str(passlog),
        "-b:v", VIDEO_BITRATE,
        "-x264-params", PASS1_X264_PARAMS if pass_no == 1 else PASS2_X264_PARAMS,
    ]


def build_pass1(ffmpeg: str, job: EncodeJob, passlog: Path) -> list[str]:
    return [
        *_common_prefix(ffmpeg, job),
        *_video_options(job, passlog, 1),
        "-an",
        "-f", "null", "-",
    ]


def build_pass2(ffmpeg: str, job: EncodeJob, passlog: Path) -> list[str]:
    cmd = _common_prefix(ffmpeg, job)
    if job.audio_stream_index is not None:
        cmd += ["-map", f"0:{job.audio_stream_index}"]
    cmd += _video_options(job, passlog, 2)
    if job.audio_stream_index is not None:
        profile = audio_profile(job.audio_channels)
        cmd += [
            "-c:a", "aac", "-b:a", profile.bitrate, "-ac", str(profile.channels),
            "-ar", "48000",
        ]
    else:
        cmd += ["-an"]
    cmd += [
        "-metadata", "creation_time=now",
        "-metadata", f"title={job.title}",
        "-metadata", f"comment={job.title}",
        "-metadata", f"description={job.description}",
    ]
    if job.audio_stream_index is not None:
        cmd += ["-metadata:s:a:0", f"language={job.language}"]
    cmd += ["-metadata:s:v", "language=", str(job.output_path)]
    return cmd


def parse_progress_seconds(line: str) -> float | None:
    """ffmpeg -progress 출력 한 줄에서 현재 처리 시각(초)을 추출함"""
    key, sep, value = line.strip().partition("=")
    if not sep or key not in ("out_time_us", "out_time_ms"):
        return None
    # out_time_ms 도 실제로는 마이크로초 단위임 (ffmpeg 의 오래된 명명 오류)
    try:
        micro = int(value)
    except ValueError:
        return None
    return micro / 1_000_000 if micro >= 0 else None


def overall_progress(pass_no: int, fraction: float) -> float:
    """pass 번호와 해당 pass 진행률(0~1)로 전체 진행률(0~1) 계산"""
    fraction = min(max(fraction, 0.0), 1.0)
    if pass_no == 1:
        return PASS1_WEIGHT * fraction
    return PASS1_WEIGHT + PASS2_WEIGHT * fraction
