from pathlib import Path

import pytest

from rarbgenc.probe import parse_probe

SAMPLE = {
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 800,
         "disposition": {"attached_pic": 1}},
        {"index": 1, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "disposition": {"default": 1}},
        {"index": 2, "codec_type": "audio", "codec_name": "dts", "channels": 6,
         "channel_layout": "5.1(side)", "tags": {"language": "eng", "title": "DTS-HD MA"},
         "disposition": {"default": 1}},
        {"index": 3, "codec_type": "audio", "codec_name": "ac3", "channels": 2,
         "tags": {"LANGUAGE": "und"}, "disposition": {"default": 0}},
        {"index": 4, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"},
        {"index": 5, "codec_type": "audio", "codec_name": "aac", "channels": 1},
    ],
    "format": {"duration": "5400.123"},
}


def test_parse_probe():
    info = parse_probe(Path("x.mkv"), SAMPLE)
    assert info.has_video
    assert (info.video_codec, info.width, info.height) == ("h264", 1920, 1080)
    assert info.duration == 5400.123
    assert [t.stream_index for t in info.audio_tracks] == [2, 3, 5]
    assert [t.order for t in info.audio_tracks] == [0, 1, 2]
    first, second, third = info.audio_tracks
    assert (first.language, first.channels, first.is_default) == ("eng", 6, True)
    assert first.title == "DTS-HD MA"
    assert second.language is None
    assert third.language is None and third.channels == 1
    assert "eng" in first.label() and "5.1(side)" in first.label()
    assert "(no language)" in second.label()
    assert "1:30:00" in info.summary()


def test_parse_probe_fallbacks():
    data = {
        "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264",
                     "width": 640, "height": 360, "duration": "12.5"}],
        "format": {"duration": "N/A"},
    }
    info = parse_probe(Path("x.ts"), data)
    assert info.duration == 12.5
    assert info.audio_tracks == []


def test_parse_probe_no_video():
    info = parse_probe(Path("x.mka"), {"streams": [{"index": 0, "codec_type": "audio"}]})
    assert not info.has_video
    assert not info.is_1080p
    assert info.duration is None


@pytest.mark.parametrize(
    ("width", "height", "supported"),
    [
        (1920, 1080, True), (1920, 804, True), (1920, 1040, True),
        (1440, 1080, True), (1280, 720, False), (3840, 2160, False),
        (2048, 1080, False), (1920, 1200, False), (1080, 1920, False),
        (1280, 800, False), (1920, 803, False), (None, 1080, False),
        (1920, None, False), (1920, 0, False), (0, 1080, False),
        ("1920", 1080, False),
    ],
)
def test_1080p_resolution_policy(width, height, supported):
    info = parse_probe(Path("x.mkv"), {"streams": [
        {"codec_type": "video", "codec_name": "h264", "width": width, "height": height},
    ]})
    assert info.is_1080p is supported
