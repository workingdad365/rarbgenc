"""재귀 동영상 탐색의 필터링과 중복 제거를 검증함."""

import os

import pytest

from rarbgenc.sources import iter_video_files


def test_collects_videos_recursively_in_order_without_duplicates(tmp_path):
    nested = tmp_path / "nested" / "deeper"
    nested.mkdir(parents=True)
    names = ["b.MKV", "A.mp4", "notes.srt", "audio.mp3", "picture.jpg", "nested/deeper/c.mov"]
    for name in names:
        (tmp_path / name).touch()
    errors = []
    paths = list(iter_video_files(
        [tmp_path / "b.MKV", tmp_path, nested, tmp_path / "A.mp4", tmp_path / "missing.mkv"],
        errors.append,
    ))
    assert paths == [tmp_path / "b.MKV", tmp_path / "A.mp4", nested / "c.mov"]
    assert errors == []


def test_directory_links_do_not_create_recursive_loops(tmp_path):
    video = tmp_path / "video.mkv"
    video.touch()
    link = tmp_path / "loop"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available")
    assert list(iter_video_files([tmp_path], pytest.fail)) == [video]


def test_scan_reports_unreadable_directories_and_continues(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.touch()

    def walk(path, onerror, followlinks):
        onerror(PermissionError("Cannot read private directory"))
        yield str(path), [], [video.name]

    monkeypatch.setattr(os, "walk", walk)
    errors = []
    assert list(iter_video_files([tmp_path], errors.append)) == [video]
    assert len(errors) == 1
    assert isinstance(errors[0], PermissionError)


def test_scan_can_be_cancelled_before_visiting_next_file(tmp_path):
    for name in ("a.mkv", "b.mp4"):
        (tmp_path / name).touch()
    cancelled = False
    files = iter_video_files([tmp_path], pytest.fail, lambda: cancelled)
    assert next(files) == tmp_path / "a.mkv"
    cancelled = True
    assert list(files) == []
