"""파일과 폴더에서 중복 없는 동영상 경로를 수집함."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

VIDEO_EXTENSIONS = (
    ".mkv", ".mp4", ".m4v", ".m2ts", ".mts", ".ts", ".avi", ".mov", ".wmv",
    ".webm", ".mpg", ".mpeg", ".vob", ".flv", ".3gp", ".3g2", ".asf", ".mxf",
    ".ogv", ".m2v", ".divx", ".rm", ".rmvb",
)


def iter_video_files(
    paths: Iterable[str | Path],
    on_error: Callable[[OSError], None],
    cancelled: Callable[[], bool] = lambda: False,
) -> Iterator[Path]:
    """입력 순서와 폴더 내 이름순으로 순회하며 디렉토리 링크는 재귀 탐색하지 않음"""
    seen_files: set[Path] = set()
    seen_dirs: set[Path] = set()

    def accept(path: Path) -> bool:
        if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
            return False
        key = path.resolve()
        if key in seen_files:
            return False
        seen_files.add(key)
        return True

    for value in paths:
        if cancelled():
            return
        path = Path(value)
        try:
            if not path.is_dir():
                if accept(path):
                    yield path
                continue
            for root, dirs, files in os.walk(path, onerror=on_error, followlinks=False):
                if cancelled():
                    return
                directory = Path(root)
                key = directory.resolve()
                if key in seen_dirs:
                    dirs.clear()
                    continue
                seen_dirs.add(key)
                dirs[:] = sorted(
                    (name for name in dirs
                     if not (directory / name).is_symlink()
                     and not (directory / name).is_junction()),
                    key=str.casefold,
                )
                for name in sorted(files, key=str.casefold):
                    if cancelled():
                        return
                    file = directory / name
                    try:
                        if accept(file):
                            yield file
                    except OSError as exc:
                        on_error(exc)
        except OSError as exc:
            on_error(exc)
