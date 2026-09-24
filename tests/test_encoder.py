from pathlib import Path

import pytest

from rarbgenc import encoder
from rarbgenc.encoder import AudioProfile, EncodeJob


def _job(**overrides) -> EncodeJob:
    values = dict(
        input_path=Path("C:/in/Movie.2010.1080p.mkv"),
        output_path=Path("D:/out/Movie.2010.1080p_ENCODED.mp4"),
        audio_stream_index=2,
        audio_channels=6,
        language="eng",
        description="Encoded using the same settings as rarbg",
    )
    values.update(overrides)
    return EncodeJob(**values)


def _value(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def _metadata(cmd: list[str], flag: str = "-metadata") -> list[str]:
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == flag]


@pytest.mark.parametrize(
    ("channels", "expected"),
    [
        (8, AudioProfile("224k", 6)),
        (6, AudioProfile("224k", 6)),
        (5, AudioProfile("128k", 2)),
        (2, AudioProfile("128k", 2)),
        (1, AudioProfile("96k", 1)),
        (0, AudioProfile("128k", 2)),
    ],
)
def test_audio_profile(channels, expected):
    assert encoder.audio_profile(channels) == expected


def test_pass1_matches_rarbg():
    cmd = encoder.build_pass1("ffmpeg", _job(), Path("T/passlog"))
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert _value(cmd, "-i") == str(Path("C:/in/Movie.2010.1080p.mkv"))
    assert _value(cmd, "-c:v") == "libx264"
    assert _value(cmd, "-pass") == "1"
    assert _value(cmd, "-b:v") == "2500k"
    assert _value(cmd, "-x264-params") == "b-adapt=2:rc-lookahead=50"
    assert _value(cmd, "-passlogfile") == str(Path("T/passlog"))
    assert "-an" in cmd
    assert cmd[-3:] == ["-f", "null", "-"]
    assert _metadata(cmd, "-map") == ["0:V:0"]


def test_pass2_matches_rarbg():
    cmd = encoder.build_pass2("ffmpeg", _job(), Path("T/passlog"))
    assert _value(cmd, "-pass") == "2"
    assert _value(cmd, "-b:v") == "2500k"
    assert _value(cmd, "-x264-params") == (
        "me=umh:subme=9:me_range=24:ref=4:trellis=2:lookahead_threads=4:direct=auto"
        ":keyint_min=25:vbv_maxrate=31250:vbv_bufsize=31250:filler=0:psy_rd=1.00,0.15"
        ":aq-mode=3:deblock=-1,-1:chroma_qp_offset=0"
    )
    assert _metadata(cmd, "-map") == ["0:V:0", "0:2"]
    assert _value(cmd, "-c:a") == "aac"
    assert _value(cmd, "-b:a") == "224k"
    assert _value(cmd, "-ac") == "6"
    assert _metadata(cmd) == [
        "creation_time=now",
        "title=Movie.2010.1080p",
        "comment=Movie.2010.1080p",
        "description=Encoded using the same settings as rarbg",
    ]
    assert _metadata(cmd, "-metadata:s:a:0") == ["language=eng"]
    assert _metadata(cmd, "-metadata:s:v") == ["language="]
    assert cmd[-1] == str(Path("D:/out/Movie.2010.1080p_ENCODED.mp4"))


@pytest.mark.parametrize(("channels", "bitrate", "ac"), [(2, "128k", "2"), (1, "96k", "1")])
def test_pass2_audio_channels(channels, bitrate, ac):
    cmd = encoder.build_pass2("ffmpeg", _job(audio_channels=channels, language="kor"), Path("p"))
    assert _value(cmd, "-b:a") == bitrate
    assert _value(cmd, "-ac") == ac
    assert _metadata(cmd, "-metadata:s:a:0") == ["language=kor"]


def test_pass2_without_audio():
    cmd = encoder.build_pass2("ffmpeg", _job(audio_stream_index=None, audio_channels=0), Path("p"))
    assert "-an" in cmd
    assert "-c:a" not in cmd
    assert "-metadata:s:a:0" not in cmd
    assert _metadata(cmd, "-map") == ["0:V:0"]


def test_pass2_description_with_special_characters():
    desc = 'He said "hi" & left; 한글'
    cmd = encoder.build_pass2("ffmpeg", _job(description=desc), Path("p"))
    assert f"description={desc}" in cmd


def test_output_path_for():
    src = Path("C:/videos/Some.Movie.mkv")
    assert encoder.output_path_for(src, "", "_ENCODED") == Path("C:/videos/Some.Movie_ENCODED.mp4")
    assert encoder.output_path_for(src, "  ", "_x") == Path("C:/videos/Some.Movie_x.mp4")
    assert encoder.output_path_for(src, "D:/out", "") == Path("D:/out/Some.Movie.mp4")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("out_time_us=1500000", 1.5),
        ("out_time_ms=2000000\n", 2.0),
        ("out_time_us=N/A", None),
        ("out_time_us=-9223372036854775807", None),
        ("out_time=00:00:01.500000", None),
        ("progress=continue", None),
        ("garbage", None),
    ],
)
def test_parse_progress_seconds(line, expected):
    assert encoder.parse_progress_seconds(line) == expected


def test_overall_progress():
    assert encoder.overall_progress(1, 0) == 0
    assert encoder.overall_progress(1, 1) == pytest.approx(encoder.PASS1_WEIGHT)
    assert encoder.overall_progress(2, 0) == pytest.approx(encoder.PASS1_WEIGHT)
    assert encoder.overall_progress(2, 1) == pytest.approx(1.0)
    assert encoder.overall_progress(2, 1.7) == pytest.approx(1.0)
    assert encoder.overall_progress(1, -1) == 0
