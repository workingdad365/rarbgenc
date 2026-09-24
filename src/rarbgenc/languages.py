"""오디오 언어 코드 (ISO 639-2) 목록 및 정규화 유틸리티.

ffmpeg 의 language 메타데이터는 ISO 639-2 세 글자 코드를 사용함 (ffmpeg-formats 문서 Metadata 항목).
mp4(mov) muxer 는 세 글자 소문자 코드만 기록하며 그 외 값은 무시함.
"""

from __future__ import annotations

import re

DEFAULT_LANGUAGE = "eng"

# (코드, 영문 이름) - mkvtoolnix 등에서 주로 쓰이는 ISO 639-2/B 코드 기준
LANGUAGES: list[tuple[str, str]] = [
    ("eng", "English"),
    ("kor", "Korean"),
    ("jpn", "Japanese"),
    ("chi", "Chinese"),
    ("spa", "Spanish"),
    ("fre", "French"),
    ("ger", "German"),
    ("ita", "Italian"),
    ("por", "Portuguese"),
    ("rus", "Russian"),
    ("ara", "Arabic"),
    ("hin", "Hindi"),
    ("tha", "Thai"),
    ("vie", "Vietnamese"),
    ("ind", "Indonesian"),
    ("may", "Malay"),
    ("fil", "Filipino"),
    ("tur", "Turkish"),
    ("pol", "Polish"),
    ("dut", "Dutch"),
    ("swe", "Swedish"),
    ("nor", "Norwegian"),
    ("dan", "Danish"),
    ("fin", "Finnish"),
    ("ice", "Icelandic"),
    ("cze", "Czech"),
    ("slo", "Slovak"),
    ("hun", "Hungarian"),
    ("rum", "Romanian"),
    ("bul", "Bulgarian"),
    ("gre", "Greek"),
    ("ukr", "Ukrainian"),
    ("scc", "Serbian"),
    ("hrv", "Croatian"),
    ("slv", "Slovenian"),
    ("heb", "Hebrew"),
    ("per", "Persian"),
    ("tam", "Tamil"),
    ("tel", "Telugu"),
    ("cat", "Catalan"),
    ("baq", "Basque"),
    ("lat", "Latin"),
]

# 언어 정보 없음으로 간주하는 코드
UNDEFINED_CODES = {"", "und", "unk", "mis", "mul", "zxx", "qaa", "n/a", "none"}

# ISO 639-1 (두 글자) -> ISO 639-2 변환 (주요 언어만)
_ISO639_1_TO_2: dict[str, str] = {
    "en": "eng", "ko": "kor", "ja": "jpn", "zh": "chi", "es": "spa", "fr": "fre",
    "de": "ger", "it": "ita", "pt": "por", "ru": "rus", "ar": "ara", "hi": "hin",
    "th": "tha", "vi": "vie", "id": "ind", "ms": "may", "tr": "tur", "pl": "pol",
    "nl": "dut", "sv": "swe", "no": "nor", "nb": "nor", "da": "dan", "fi": "fin",
    "is": "ice", "cs": "cze", "sk": "slo", "hu": "hun", "ro": "rum", "bg": "bul",
    "el": "gre", "uk": "ukr", "sr": "scc", "hr": "hrv", "sl": "slv", "he": "heb",
    "fa": "per", "ta": "tam", "te": "tel", "ca": "cat", "eu": "baq", "la": "lat",
}

_CODE_RE = re.compile(r"^[a-z]{3}$")


def is_valid_code(code: str) -> bool:
    """mp4 에 기록 가능한 세 글자 코드 여부"""
    return bool(_CODE_RE.match(code)) and code not in UNDEFINED_CODES


def normalize(code: str | None) -> str | None:
    """원본 스트림의 language 태그를 ISO 639-2 코드로 정규화함. 정보가 없으면 None.

    'en-US' 같은 BCP 47 태그는 주 언어 부분만 사용함.
    """
    if not code:
        return None
    value = code.strip().lower().replace("_", "-").split("-")[0]
    if len(value) == 2:
        value = _ISO639_1_TO_2.get(value, "")
    return value if is_valid_code(value) else None


def display_name(code: str) -> str:
    """콤보박스 표시용 문자열"""
    for c, name in LANGUAGES:
        if c == code:
            return f"{c} - {name}"
    return code


def parse_display(text: str) -> str:
    """콤보박스 입력값('eng - English' 또는 'eng')에서 코드만 추출함"""
    return text.strip().split(" ")[0].lower()
