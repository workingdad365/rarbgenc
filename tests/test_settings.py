import json

from rarbgenc.settings import DEFAULT_DESCRIPTION, HISTORY_LIMIT, Settings


def test_defaults_when_missing(tmp_path):
    s = Settings.load(tmp_path / "none.json")
    assert s == Settings()
    assert s.suffix == "_ENCODED"
    assert s.fallback_language == "eng"
    assert s.description == DEFAULT_DESCRIPTION


def test_roundtrip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    s = Settings(output_dir="D:/out", suffix="_x264", fallback_language="kor")
    s.remember_description("My rip")
    s.save(path)
    loaded = Settings.load(path)
    assert loaded == s
    assert loaded.description_history[0] == "My rip"


def test_default_path_is_used(isolated_settings):
    Settings(suffix="_A").save()
    assert isolated_settings.exists()
    assert Settings.load().suffix == "_A"


def test_corrupt_file_falls_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert Settings.load(path) == Settings()
    path.write_text("[1, 2]", encoding="utf-8")
    assert Settings.load(path) == Settings()


def test_wrong_types_fall_back_per_field(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"suffix": 3, "output_dir": "X", "description_history": ["a", 1, "b"]}),
        encoding="utf-8",
    )
    s = Settings.load(path)
    assert s.suffix == "_ENCODED"
    assert s.output_dir == "X"
    assert s.description_history == ["a", "b"]


def test_description_history():
    s = Settings()
    for i in range(HISTORY_LIMIT + 5):
        s.remember_description(f"d{i}")
    assert len(s.description_history) == HISTORY_LIMIT
    assert s.description_history[0] == f"d{HISTORY_LIMIT + 4}"
    s.remember_description("d5")
    assert s.description_history[0] == "d5"
    assert s.description_history.count("d5") == 1
    s.remember_description("   ")
    assert s.description == ""
    assert "" not in s.description_history
