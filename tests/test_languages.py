import pytest

from rarbgenc import languages


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eng", "eng"),
        ("ENG", "eng"),
        (" kor ", "kor"),
        ("en", "eng"),
        ("en-US", "eng"),
        ("pt_BR", "por"),
        ("und", None),
        ("", None),
        (None, None),
        ("xx", None),
        ("english", None),
        ("e1g", None),
    ],
)
def test_normalize(raw, expected):
    assert languages.normalize(raw) == expected


def test_is_valid_code():
    assert languages.is_valid_code("jpn")
    assert not languages.is_valid_code("und")
    assert not languages.is_valid_code("JPN")
    assert not languages.is_valid_code("jp")


def test_display_roundtrip():
    for code, _name in languages.LANGUAGES:
        assert languages.parse_display(languages.display_name(code)) == code
    assert languages.parse_display("  XYZ ") == "xyz"
    assert languages.display_name("xyz") == "xyz"


def test_language_list_codes_are_valid_and_unique():
    codes = [c for c, _ in languages.LANGUAGES]
    assert len(codes) == len(set(codes))
    assert all(languages.is_valid_code(c) for c in codes)
    assert languages.DEFAULT_LANGUAGE == codes[0] == "eng"
