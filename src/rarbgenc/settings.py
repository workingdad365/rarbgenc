"""사용자 설정 (디스크립션, 언어, 출력 경로 등) 저장 및 로드."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from rarbgenc.languages import DEFAULT_LANGUAGE

APP_NAME = "rarbgenc"
DEFAULT_DESCRIPTION = "Encoded using the same settings as rarbg"
DEFAULT_SUFFIX = "_ENCODED"
HISTORY_LIMIT = 10


def config_dir() -> Path:
    """OS 별 설정 디렉토리"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME


def data_dir() -> Path:
    """OS 별 데이터 디렉토리 (자동 설치한 ffmpeg 저장 위치)"""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def default_settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    output_dir: str = ""  # 빈 값이면 원본과 같은 폴더
    suffix: str = DEFAULT_SUFFIX
    description: str = DEFAULT_DESCRIPTION
    description_history: list[str] = field(default_factory=lambda: [DEFAULT_DESCRIPTION])
    fallback_language: str = DEFAULT_LANGUAGE  # 원본에 언어 정보가 없을 때 사용
    last_open_dir: str = ""
    video_codec: str = "x264"

    def remember_description(self, text: str) -> None:
        """현재 디스크립션을 기록하고 이력 맨 앞에 추가함"""
        text = text.strip()
        self.description = text
        if not text:
            return
        history = [h for h in self.description_history if h != text]
        self.description_history = [text, *history][:HISTORY_LIMIT]

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        """설정 파일을 읽음. 없거나 손상된 경우 기본값 반환"""
        path = path or default_settings_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        defaults = cls()
        values = {}
        for f in fields(cls):
            value = raw.get(f.name, getattr(defaults, f.name))
            expected = type(getattr(defaults, f.name))
            if not isinstance(value, expected):
                value = getattr(defaults, f.name)
            values[f.name] = value
        values["description_history"] = [
            h for h in values["description_history"] if isinstance(h, str)
        ][:HISTORY_LIMIT]
        if values["video_codec"] not in ("x264", "x265"):
            values["video_codec"] = defaults.video_codec
        return cls(**values)

    def save(self, path: Path | None = None) -> None:
        path = path or default_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
