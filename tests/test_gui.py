"""offscreen 플랫폼으로 메인 창과 인코딩 실행기를 검증함."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from rarbgenc import gui
from rarbgenc.settings import Settings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def no_dialogs(monkeypatch):
    """모달 대화상자 대신 호출 기록만 남김"""
    calls = []
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, _n=name: calls.append((_n, a)) or QMessageBox.StandardButton.Ok),
        )
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a: calls.append(("question", a)) or QMessageBox.StandardButton.Yes),
    )
    return calls


def _wait_finished(window, timeout_ms: int = 120_000) -> None:
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)

    def check():
        if window.runner is None:
            loop.quit()
        else:
            QTimer.singleShot(100, check)

    QTimer.singleShot(100, check)
    loop.exec()


def test_window_language_behaviour(qapp, no_dialogs, ffmpeg_paths, sample_dir):
    settings = Settings(fallback_language="kor")
    window = gui.MainWindow(settings, str(sample_dir / "multi.mkv"))
    assert window.ffmpeg is not None and window.banner.isHidden()
    assert window.media is not None
    assert window.track_combo.count() == 2

    # 1번 트랙: 언어 없음 -> 직접 선택 가능, 저장된 대체 언어 표시
    window.track_combo.setCurrentIndex(0)
    assert window.language_combo.isEnabled()
    assert window.language_combo.currentText().startswith("kor")
    assert window.audio_out_label.text() == "AAC 224k, 6 ch"

    # 2번 트랙: 원본 언어 kor 고정
    window.track_combo.setCurrentIndex(1)
    assert not window.language_combo.isEnabled()
    assert window.language_combo.currentText().startswith("kor")
    assert window.audio_out_label.text() == "AAC 128k, 2 ch"

    window.suffix_edit.setText("_T")
    assert window.output_preview.text().endswith("multi_T.mp4")
    assert window.start_button.isEnabled()
    window.close()


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
def test_window_encodes_and_saves_settings(qapp, no_dialogs, ffmpeg_paths, sample_dir, tmp_path,
                                           isolated_settings, video_codec):
    if not gui.ffmpeg_tools.has_encoder(ffmpeg_paths.ffmpeg, f"lib{video_codec}"):
        pytest.skip(f"ffmpeg with lib{video_codec} is not available")
    window = gui.MainWindow(Settings(), str(sample_dir / "multi.mkv"))
    window.codec_combo.setCurrentIndex(window.codec_combo.findData(video_codec))
    window.track_combo.setCurrentIndex(0)
    window.language_combo.setEditText("fre")
    window.description_combo.setEditText("My test rip")
    out_dir = tmp_path / "encoded"
    window.output_dir_edit.setText(str(out_dir))

    window.start_encoding()
    assert window.runner is not None
    assert window.runner._job.video_codec == video_codec
    assert not window.codec_combo.isEnabled()
    assert not window.start_button.isEnabled()
    _wait_finished(window)

    out = out_dir / "multi_ENCODED.mp4"
    assert out.exists()
    assert window.progress_bar.value() == 1000
    assert no_dialogs[-1][0] == "information"
    assert window.codec_combo.isEnabled()

    saved = Settings.load(isolated_settings)
    assert saved.fallback_language == "fre"
    assert saved.description == "My test rip"
    assert saved.description_history[0] == "My test rip"
    assert saved.output_dir == str(out_dir)
    assert saved.video_codec == video_codec
    window.close()
    restored = gui.MainWindow(saved)
    assert restored.codec_combo.currentData() == video_codec
    restored.close()


def test_invalid_language_is_rejected(qapp, no_dialogs, ffmpeg_paths, sample_dir):
    window = gui.MainWindow(Settings(), str(sample_dir / "multi.mkv"))
    window.track_combo.setCurrentIndex(0)
    window.language_combo.setEditText("english")
    window.start_encoding()
    assert window.runner is None
    assert no_dialogs[-1][0] == "critical"
    window.close()


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
def test_cancel_removes_partial_output(qapp, no_dialogs, ffmpeg_paths, sample_dir, tmp_path,
                                      video_codec):
    if not gui.ffmpeg_tools.has_encoder(ffmpeg_paths.ffmpeg, f"lib{video_codec}"):
        pytest.skip(f"ffmpeg with lib{video_codec} is not available")
    window = gui.MainWindow(Settings(video_codec=video_codec), str(sample_dir / "multi.mkv"))
    window.output_dir_edit.setText(str(tmp_path))
    window.start_encoding()
    window.cancel_encoding()
    _wait_finished(window)
    assert window.runner is None
    assert window.status_label.text() == "Cancelled"
    assert list(tmp_path.glob("*.mp4")) == []
    window.close()


def test_missing_ffmpeg_shows_banner(qapp, no_dialogs, monkeypatch):
    monkeypatch.setattr(gui.ffmpeg_tools, "locate", lambda: None)
    window = gui.MainWindow(Settings())
    assert not window.banner.isHidden()
    assert "ffmpeg was not found" in window.banner_label.text()
    assert not window.start_button.isEnabled()
    window.close()


def test_missing_selected_encoder(qapp, no_dialogs, monkeypatch, ffmpeg_paths, sample_dir):
    monkeypatch.setattr(gui.ffmpeg_tools, "locate", lambda: ffmpeg_paths)
    monkeypatch.setattr(gui.ffmpeg_tools, "has_encoder", lambda _path, codec: codec == "libx264")
    window = gui.MainWindow(Settings(), str(sample_dir / "multi.mkv"))
    assert window.start_button.isEnabled()
    window.codec_combo.setCurrentIndex(window.codec_combo.findData("x265"))
    assert "without libx265" in window.banner_label.text()
    assert not window.start_button.isEnabled()
    window._on_download_done(ffmpeg_paths, "")
    assert not window.start_button.isEnabled()
    window.codec_combo.setCurrentIndex(window.codec_combo.findData("x264"))
    assert window.start_button.isEnabled()
    assert window.banner.isHidden()
    window.close()


def test_header_shows_logo_and_version(qapp, no_dialogs):
    from rarbgenc import __version__

    assert gui.LOGO_PATH.exists()
    window = gui.MainWindow(Settings())
    assert not window.logo_label.isHidden()
    assert window.logo_label.pixmap().height() == gui.LOGO_HEIGHT
    assert __version__ == "1.1.0"
    assert "1.1.0" in window.windowTitle()
    assert gui.ICON_PATH.exists()
    assert not window.windowIcon().isNull()
    window.close()
