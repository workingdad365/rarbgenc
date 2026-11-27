"""offscreen 플랫폼으로 메인 창과 인코딩 실행기를 검증함."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QEventLoop, QMimeData, QObject, QPoint, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from rarbgenc import gui
from rarbgenc.probe import AudioTrack, MediaInfo
from rarbgenc.settings import Settings


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def no_dialogs(qapp, monkeypatch):
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
    yield calls
    for window in qapp.topLevelWidgets():
        window.close()
        window.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _wait_finished(window, timeout_ms: int = 120_000) -> None:
    loop = QEventLoop()
    timeout = QTimer(loop)
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    poll = QTimer(loop)

    def check():
        if not window._encoding and window.loader is None:
            loop.quit()

    poll.timeout.connect(check)
    timeout.start(timeout_ms)
    poll.start(10)
    try:
        loop.exec()
    finally:
        poll.stop()
        timeout.stop()
        poll.timeout.disconnect(check)
    assert not window._encoding and window.loader is None


def test_window_language_behaviour(qapp, no_dialogs, ffmpeg_paths, hd_sample_dir):
    settings = Settings(fallback_language="kor")
    window = gui.MainWindow(settings, str(hd_sample_dir / "multi.mkv"))
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
def test_window_encodes_and_saves_settings(qapp, no_dialogs, ffmpeg_paths, hd_sample_dir, tmp_path,
                                           isolated_settings, video_codec):
    if not gui.ffmpeg_tools.has_encoder(ffmpeg_paths.ffmpeg, f"lib{video_codec}"):
        pytest.skip(f"ffmpeg with lib{video_codec} is not available")
    window = gui.MainWindow(Settings(), str(hd_sample_dir / "multi.mkv"))
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


def test_invalid_language_is_rejected(qapp, no_dialogs, ffmpeg_paths, hd_sample_dir):
    window = gui.MainWindow(Settings(), str(hd_sample_dir / "multi.mkv"))
    window.track_combo.setCurrentIndex(0)
    window.language_combo.setEditText("english")
    window.start_encoding()
    assert window.runner is None
    assert no_dialogs[-1][0] == "critical"
    window.close()


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
@pytest.mark.parametrize("action", ["cancel", "clear"])
def test_cancel_removes_partial_output(qapp, no_dialogs, ffmpeg_paths, hd_sample_dir, tmp_path,
                                      video_codec, action):
    if not gui.ffmpeg_tools.has_encoder(ffmpeg_paths.ffmpeg, f"lib{video_codec}"):
        pytest.skip(f"ffmpeg with lib{video_codec} is not available")
    window = gui.MainWindow(Settings(video_codec=video_codec), str(hd_sample_dir / "multi.mkv"))
    source = hd_sample_dir / "multi.mkv"
    original = source.read_bytes()
    window.output_dir_edit.setText(str(tmp_path))
    window.start_encoding()
    if action == "clear":
        window.clear_queue_button.click()
    else:
        window.cancel_encoding()
    _wait_finished(window)
    assert window.runner is None
    assert window.status_label.text() == ("Queue cleared" if action == "clear" else "Cancelled")
    assert list(tmp_path.glob("*.mp4")) == []
    assert source.read_bytes() == original
    if action == "clear":
        assert window.queue_view.topLevelItemCount() == 0
        assert not window.start_button.isEnabled()
    window.close()


def test_missing_ffmpeg_shows_banner(qapp, no_dialogs, monkeypatch):
    monkeypatch.setattr(gui.ffmpeg_tools, "locate", lambda: None)
    window = gui.MainWindow(Settings())
    assert not window.banner.isHidden()
    assert "ffmpeg was not found" in window.banner_label.text()
    assert not window.start_button.isEnabled()
    window.close()


def test_missing_selected_encoder(qapp, no_dialogs, monkeypatch, ffmpeg_paths, hd_sample_dir):
    monkeypatch.setattr(gui.ffmpeg_tools, "locate", lambda: ffmpeg_paths)
    monkeypatch.setattr(gui.ffmpeg_tools, "has_encoder", lambda _path, codec: codec == "libx264")
    window = gui.MainWindow(Settings(), str(hd_sample_dir / "multi.mkv"))
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


@pytest.mark.parametrize("video_codec", ["x264", "x265"])
def test_non_1080p_file_blocks_encoding(qapp, no_dialogs, ffmpeg_paths, sample_dir,
                                       hd_sample_dir, tmp_path, monkeypatch, video_codec):
    if not gui.ffmpeg_tools.has_encoder(ffmpeg_paths.ffmpeg, f"lib{video_codec}"):
        pytest.skip(f"ffmpeg with lib{video_codec} is not available")
    window = gui.MainWindow(Settings(video_codec=video_codec), str(hd_sample_dir / "multi.mkv"))
    assert window.start_button.isEnabled()
    window.output_dir_edit.setText(str(tmp_path))
    window.clear_queue_button.click()
    window.load_file(str(sample_dir / "silent.mkv"))
    assert window.media.path == sample_dir / "silent.mkv"
    assert not window.start_button.isEnabled()
    assert no_dialogs[-1][0] == "warning"
    assert "320x240" in no_dialogs[-1][1][2]
    monkeypatch.setattr(gui, "EncodeRunner", lambda *_args: pytest.fail("Encoding must not start"))
    window.suffix_edit.setText("_blocked")
    window.check_ffmpeg()
    assert not window.start_button.isEnabled()
    window.start_encoding()
    assert window.runner is None
    assert not list(tmp_path.glob("*.mp4"))
    window.load_file(str(hd_sample_dir / "multi.mkv"))
    assert window.start_button.isEnabled()
    assert window.status_label.text() == ""
    window.close()


@pytest.mark.parametrize(
    ("width", "height", "supported"),
    [(1920, 804, True), (1920, 1040, True), (1440, 1080, True),
     (1280, 720, False), (3840, 2160, False), (None, None, False)],
)
def test_window_resolution_gate(qapp, no_dialogs, ffmpeg_paths, tmp_path, monkeypatch,
                                width, height, supported):
    media = gui.MediaInfo(tmp_path / "source.mkv", 1, width, height, "h264")
    monkeypatch.setattr(gui, "probe", lambda *_args: media)
    window = gui.MainWindow(Settings(), str(media.path))
    assert window.start_button.isEnabled() is supported
    assert bool(no_dialogs) is not supported
    window.close()


def test_header_shows_logo_and_version(qapp, no_dialogs):
    from rarbgenc import __version__

    assert gui.LOGO_PATH.exists()
    window = gui.MainWindow(Settings())
    assert not window.logo_label.isHidden()
    assert window.logo_label.pixmap().height() == gui.LOGO_HEIGHT
    assert __version__ == "1.3.0"
    assert "1.3.0" in window.windowTitle()
    assert gui.ICON_PATH.exists()
    assert not window.windowIcon().isNull()
    window.close()


def test_drop_accepts_all_local_videos_and_folders(qapp, tmp_path):
    video = tmp_path / "clip.MKV"
    video.touch()
    folder = tmp_path / "folder"
    folder.mkdir()
    text = tmp_path / "readme.txt"
    text.touch()
    area = gui.DropArea()
    received = []
    area.pathsDropped.connect(received.append)
    mime = QMimeData()
    mime.setUrls([
        QUrl.fromLocalFile(str(text)), QUrl.fromLocalFile(str(video)),
        QUrl("https://example.com/movie.mp4"), QUrl.fromLocalFile(str(folder)),
    ])
    enter = QDragEnterEvent(
        QPoint(5, 5), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    area.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(
        QPointF(5, 5), Qt.DropAction.CopyAction, mime,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    area.dropEvent(drop)
    assert drop.isAccepted()
    assert [[Path(path) for path in paths] for paths in received] == [[video, folder]]
    area.setEnabled(False)
    area.dropEvent(drop)
    assert not drop.isAccepted()
    assert len(received) == 1
    area.close()


def test_folder_batch_encodes_all_supported_videos(qapp, no_dialogs, ffmpeg_paths,
                                                  hd_sample_dir, sample_dir, tmp_path):
    source = tmp_path / "sources"
    nested = source / "nested"
    nested.mkdir(parents=True)
    first = source / "first.MKV"
    second = nested / "second.mkv"
    for path in (first, second):
        shutil.copyfile(hd_sample_dir / "multi.mkv", path)
    shutil.copyfile(sample_dir / "silent.mkv", source / "low-resolution.mkv")
    (source / "broken.mp4").write_text("invalid video", encoding="utf-8")
    (source / "notes.srt").write_text("not a video", encoding="utf-8")
    window = gui.MainWindow(Settings())
    window.drop_area.pathsDropped.emit([str(first)])
    assert window.queue_view.topLevelItemCount() == 1
    window.drop_area.pathsDropped.emit([str(source), str(first)])
    assert not window.start_button.isEnabled()
    _wait_finished(window)
    assert [media.path for media in window._sources] == [first, second]
    assert len(window._source_errors) == 2
    assert no_dialogs == []
    window.track_combo.setCurrentIndex(1)
    window.start_encoding()
    assert window.runner is not None
    assert not window.output_browse.isEnabled()
    _wait_finished(window)
    for path in (source / "first_ENCODED.mp4", nested / "second_ENCODED.mp4"):
        media = gui.probe(ffmpeg_paths.ffprobe, path)
        assert media.is_1080p
        assert len(media.audio_tracks) == 1
        assert media.audio_tracks[0].language == "kor"
    assert "2 succeeded, 0 failed, 2 skipped" in window.queue_label.text()
    assert window.progress_bar.value() == 1000
    assert [kind for kind, _ in no_dialogs] == ["information"]
    assert not (source / "low-resolution_ENCODED.mp4").exists()
    assert not (source / "broken_ENCODED.mp4").exists()
    window.close()


@pytest.fixture
def batch_window(qapp, no_dialogs, monkeypatch, tmp_path):
    paths = gui.ffmpeg_tools.FFmpegPaths("ffmpeg", "ffprobe", "test")
    monkeypatch.setattr(gui.ffmpeg_tools, "locate", lambda: paths)
    monkeypatch.setattr(gui.ffmpeg_tools, "has_encoder", lambda *_args: True)
    monkeypatch.setattr(gui.ffmpeg_tools, "version", lambda *_args: "test")
    media = {}
    for name in ("a.mkv", "b.mp4", "nested/a.mkv"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        media[path] = MediaInfo(path, 1, 1920, 1080, "h264")
    monkeypatch.setattr(gui, "probe", lambda _ffprobe, path: media[path])
    runners = []

    class ManualRunner(QObject):
        progress = Signal(float, str)
        log = Signal(str)
        finished = Signal(bool, str)

        def __init__(self, ffmpeg, job, duration):
            super().__init__()
            self.job = job
            self.cancelled = False
            runners.append(self)

        def start(self):
            self.progress.emit(0, "Pass 1/2")

        def cancel(self):
            self.cancelled = True
            self.finished.emit(False, "Cancelled")

        def cancel_and_wait(self):
            self.cancel()

    monkeypatch.setattr(gui, "EncodeRunner", ManualRunner)
    window = gui.MainWindow(Settings())
    window.load_paths([str(path) for path in media])
    _wait_finished(window)
    yield window, runners, media
    window.close()


def test_batch_runs_one_at_a_time_and_continues_after_failure(batch_window, qapp, no_dialogs):
    window, runners, media = batch_window
    window.start_encoding()
    assert len(runners) == 1
    assert runners[0].job.input_path == next(iter(media))
    assert not window.start_button.isEnabled()
    runners[0].finished.emit(False, "test encoding failure")
    qapp.processEvents()
    assert len(runners) == 2
    assert no_dialogs == []
    runners[1].finished.emit(True, str(runners[1].job.output_path))
    qapp.processEvents()
    assert len(runners) == 3
    assert no_dialogs == []
    runners[2].finished.emit(True, str(runners[2].job.output_path))
    qapp.processEvents()
    assert not window._encoding
    assert window.start_button.isEnabled()
    assert "2 succeeded, 1 failed" in window.queue_label.text()
    assert [kind for kind, _ in no_dialogs] == ["information"]


@pytest.mark.parametrize("between_files", [False, True])
def test_cancelling_batch_never_starts_remaining_files(batch_window, qapp, no_dialogs, between_files):
    window, runners, _ = batch_window
    window.start_encoding()
    if between_files:
        runners[0].finished.emit(True, "first.mp4")
    window.cancel_encoding()
    qapp.processEvents()
    assert len(runners) == 1
    assert not window._encoding
    assert window.status_label.text() == "Cancelled"
    assert no_dialogs == []


def test_closing_batch_between_files_never_starts_next_file(batch_window, qapp):
    window, runners, _ = batch_window
    window.start_encoding()
    runners[0].finished.emit(True, "first.mp4")
    window.close()
    qapp.processEvents()
    assert len(runners) == 1
    assert not window._encoding


def test_immediate_restart_after_cancellation_stays_sequential(batch_window, qapp):
    window, runners, _ = batch_window
    window.start_encoding()
    runners[0].finished.emit(True, "first.mp4")
    window.cancel_encoding()
    window.start_encoding()
    qapp.processEvents()
    assert len(runners) == 2
    assert window.runner is runners[1]
    window.cancel_encoding()


def test_batch_disambiguates_output_names_and_confirms_overwrites_once(batch_window, tmp_path, no_dialogs):
    window, runners, _ = batch_window
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    for name in ("a_ENCODED.mp4", "b_ENCODED.mp4", "a_ENCODED_2.mp4"):
        (out_dir / name).write_text("existing output", encoding="utf-8")
    window.output_dir_edit.setText(str(out_dir))
    window.start_encoding()
    outputs = [runners[0].job.output_path, *(job.output_path for _, job in window._jobs)]
    assert outputs == [
        out_dir / "a_ENCODED.mp4", out_dir / "b_ENCODED.mp4", out_dir / "a_ENCODED_3.mp4",
    ]
    questions = [args for kind, args in no_dialogs if kind == "question"]
    assert len(questions) == 1
    assert "2 output file(s)" in questions[0][2]
    window.cancel_encoding()


def test_batch_cannot_overwrite_another_queued_source(batch_window, tmp_path, no_dialogs):
    window, runners, media = batch_window
    first, second, _ = media.values()
    window._sources = [first, replace(second, path=tmp_path / "a_ENCODED.mp4")]
    window.start_encoding()
    assert runners == []
    assert not window._encoding
    assert no_dialogs[-1][0] == "critical"
    assert "overwrite a source file" in no_dialogs[-1][1][2]


def test_batch_uses_each_files_audio_indices_and_languages(batch_window, no_dialogs):
    window, runners, media = batch_window
    first, second, third = media.values()
    first.audio_tracks = [
        AudioTrack(2, 0, "aac", 2, "stereo", "eng", "", True),
        AudioTrack(5, 1, "aac", 6, "5.1", None, "", False),
    ]
    second.audio_tracks = [AudioTrack(3, 0, "aac", 1, "mono", "jpn", "", True)]
    third.audio_tracks = [
        AudioTrack(1, 0, "aac", 2, "stereo", "eng", "", True),
        AudioTrack(7, 1, "aac", 2, "stereo", "kor", "", False),
    ]
    window._show_media(first)
    window.track_combo.setCurrentIndex(1)
    window.language_combo.setEditText("fre")
    window.start_encoding()
    jobs = [runners[0].job, *(job for _, job in window._jobs)]
    assert [(job.audio_stream_index, job.audio_channels, job.language) for job in jobs] == [
        (5, 6, "fre"), (3, 1, "jpn"), (7, 2, "kor"),
    ]
    window.cancel_encoding()


def test_empty_folder_preserves_previous_queue(batch_window, tmp_path, no_dialogs):
    window, runners, media = batch_window
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "audio.mp3").touch()
    window.load_paths([str(empty)])
    _wait_finished(window)
    assert [source.path for source in window._sources] == list(media)
    assert window.media.path == next(iter(media))
    assert window.queue_view.topLevelItemCount() == 3
    assert window.start_button.isEnabled()
    assert runners == []
    assert no_dialogs[-1][0] == "warning"


@pytest.mark.parametrize("action", ["cancel", "clear"])
def test_scan_cancellation_preserves_queue_unless_cleared(batch_window, monkeypatch, no_dialogs, action):
    window, runners, media = batch_window
    entered = Event()
    release = Event()
    probed = []

    def probe(_ffprobe, path):
        probed.append(path)
        entered.set()
        assert release.wait(5)
        return media[path]

    monkeypatch.setattr(gui, "probe", probe)
    window.load_paths([str(path) for path in media])
    try:
        assert entered.wait(5)
        assert window.cancel_button.isEnabled()
        assert not window.start_button.isEnabled()
        if action == "clear":
            assert window.clear_queue_button.isEnabled()
            window.clear_queue_button.click()
        else:
            window.cancel_encoding()
    finally:
        release.set()
    _wait_finished(window)
    assert len(probed) == 1
    assert window.status_label.text() == ("Queue cleared" if action == "clear" else "Cancelled")
    if action == "clear":
        assert window.media is None
        assert window.queue_view.topLevelItemCount() == 0
        assert not window.start_button.isEnabled()
    else:
        assert [source.path for source in window._sources] == list(media)
        assert window.media.path == next(iter(media))
        assert window.queue_view.topLevelItemCount() == 3
        assert window.start_button.isEnabled()
    assert runners == []
    assert no_dialogs == []


@pytest.mark.parametrize("restore", ["download", "recheck"])
def test_missing_ffmpeg_accumulates_pending_inputs_across_drops(batch_window, restore):
    window, _, media = batch_window
    ffmpeg = window.ffmpeg
    window.clear_queue_button.click()
    window.ffmpeg = None
    inputs = list(media)[:2]
    for path in inputs:
        window.drop_area.pathsDropped.emit([str(path)])
    if restore == "download":
        window._on_download_done(ffmpeg, "")
    else:
        window.check_ffmpeg()
        inputs.append(list(media)[2])
        window.drop_area.pathsDropped.emit([str(inputs[-1])])
    _wait_finished(window)
    assert [source.path for source in window._sources] == inputs


def test_queue_lists_paths_and_removes_selected_jobs_without_deleting_files(batch_window, qapp):
    window, runners, media = batch_window
    paths = list(media)
    originals = {}
    for path in paths:
        path.write_bytes(f"Source: {path}".encode())
        originals[path] = path.read_bytes()
    assert window.queue_tabs.tabText(0) == "Queue (3)"
    assert window.queue_tabs.tabText(1) == "Log"
    assert window.queue_view.topLevelItemCount() == 3
    for index, path in enumerate(paths):
        item = window.queue_view.topLevelItem(index)
        assert item.text(0) == path.name
        assert item.text(1) == "Queued"
        assert item.text(2) == str(path.parent)
        assert str(path) in item.toolTip(0)
    assert not window.remove_queue_button.isEnabled()
    window.queue_view.topLevelItem(0).setSelected(True)
    window.queue_view.topLevelItem(2).setSelected(True)
    assert window.remove_queue_button.isEnabled()
    window.remove_queue_button.click()
    assert [source.path for source in window._sources] == [paths[1]]
    assert window.media.path == paths[1]
    assert window.queue_view.topLevelItemCount() == 1
    assert window.queue_tabs.tabText(0) == "Queue (1)"
    assert window.output_preview.text().endswith("b_ENCODED.mp4")
    window.start_encoding()
    assert [runner.job.input_path for runner in runners] == [paths[1]]
    runners[0].finished.emit(True, str(runners[0].job.output_path))
    qapp.processEvents()
    assert not window._encoding
    assert {path: path.read_bytes() for path in paths} == originals


def test_removing_last_queue_items_disables_encoding(batch_window):
    window, runners, media = batch_window
    window.queue_view.selectAll()
    window.remove_queue_button.click()
    assert window.queue_view.topLevelItemCount() == 0
    assert window.media is None
    assert window.info_label.text() == ""
    assert window.output_preview.text() == ""
    assert window.drop_area._label.text() == gui.DROP_PROMPT
    assert not window.start_button.isEnabled()
    assert not window.remove_queue_button.isEnabled()
    assert not window.clear_queue_button.isEnabled()
    window.start_encoding()
    assert runners == []
    assert all(path.exists() for path in media)


def test_running_queue_removal_preserves_current_job_and_updates_progress(batch_window, qapp):
    window, runners, media = batch_window
    paths = list(media)
    window.start_encoding()
    assert window.queue_view.topLevelItem(0).text(1) == "Encoding"
    window.queue_view.topLevelItem(0).setSelected(True)
    assert not window.remove_queue_button.isEnabled()
    window.remove_selected_from_queue()
    assert len(window._sources) == 3
    runners[0].progress.emit(0.5, "Pass 2/2")
    window.queue_view.topLevelItem(1).setSelected(True)
    window.remove_queue_button.click()
    assert [source.path for source in window._sources] == [paths[0], paths[2]]
    assert window.runner is runners[0]
    assert not runners[0].cancelled
    assert window.progress_bar.value() == 250
    assert "1/2" in window.status_label.text()
    runners[0].finished.emit(False, "test failure")
    qapp.processEvents()
    assert window.queue_view.topLevelItem(0).text(1) == "Failed"
    assert window.queue_view.topLevelItem(1).text(1) == "Encoding"
    assert [runner.job.input_path for runner in runners] == [paths[0], paths[2]]
    runners[1].finished.emit(True, str(runners[1].job.output_path))
    qapp.processEvents()
    assert window.queue_view.topLevelItem(1).text(1) == "Done"
    assert "1 succeeded, 1 failed" in window.queue_label.text()
    assert all(path.exists() for path in media)


def test_removing_remaining_jobs_between_files_finishes_queue(batch_window, qapp):
    window, runners, _ = batch_window
    window.start_encoding()
    runners[0].finished.emit(True, str(runners[0].job.output_path))
    window.queue_view.selectAll()
    window.remove_queue_button.click()
    qapp.processEvents()
    assert len(runners) == 1
    assert not window._encoding
    assert window.queue_view.topLevelItemCount() == 1
    assert window.queue_view.topLevelItem(0).text(1) == "Done"
    assert window.progress_bar.value() == 1000


def test_clear_queue_resets_state_and_preserves_files_and_options(batch_window):
    window, runners, media = batch_window
    original = {path: path.read_bytes() for path in media}
    window.description_combo.setEditText("Keep this description")
    window.suffix_edit.setText("_KEEP")
    window.clear_queue_button.click()
    assert window._sources == []
    assert window._source_errors == []
    assert window._pending_paths == []
    assert not window._jobs
    assert window.media is None
    assert window.queue_view.topLevelItemCount() == 0
    assert window.queue_tabs.tabText(0) == "Queue (0)"
    assert window.progress_bar.value() == 0
    assert window.status_label.text() == "Queue cleared"
    assert not window.start_button.isEnabled()
    assert not window.clear_queue_button.isEnabled()
    assert window.description_combo.currentText() == "Keep this description"
    assert window.suffix_edit.text() == "_KEEP"
    assert {path: path.read_bytes() for path in media} == original
    window.load_file(str(next(iter(media))))
    assert window.start_button.isEnabled()
    assert window.queue_view.topLevelItemCount() == 1
    assert runners == []


def test_clear_running_queue_waits_for_stop_and_never_starts_next_job(
    batch_window, monkeypatch, qapp, no_dialogs,
):
    window, runners, media = batch_window
    window.start_encoding()
    active = runners[0]
    monkeypatch.setattr(active, "cancel", lambda: setattr(active, "cancelled", True))
    window.clear_queue_button.click()
    assert active.cancelled
    assert window.runner is active
    assert window.status_label.text() == "Clearing queue..."
    assert not window.clear_queue_button.isEnabled()
    assert not window.start_button.isEnabled()
    assert not window.open_button.isEnabled()
    assert not window._jobs
    active.finished.emit(False, "Cancelled")
    qapp.processEvents()
    assert window.runner is None
    assert not window._encoding
    assert window.queue_view.topLevelItemCount() == 0
    assert window.status_label.text() == "Queue cleared"
    assert window.open_button.isEnabled()
    assert len(runners) == 1
    assert all(path.exists() for path in media)
    assert no_dialogs == []


def test_clear_between_jobs_allows_immediate_new_queue(batch_window, qapp):
    window, runners, media = batch_window
    window.start_encoding()
    runners[0].finished.emit(True, str(runners[0].job.output_path))
    window.clear_queue_button.click()
    assert window.queue_view.topLevelItemCount() == 0
    window.load_file(str(next(iter(media))))
    window.start_encoding()
    qapp.processEvents()
    assert len(runners) == 2
    assert window.runner is runners[1]
    assert window.queue_view.topLevelItemCount() == 1
    window.cancel_encoding()


def test_clear_queue_discards_inputs_pending_ffmpeg_install(batch_window):
    window, runners, media = batch_window
    ffmpeg = window.ffmpeg
    window.ffmpeg = None
    window.load_paths([str(path) for path in media])
    assert window.clear_queue_button.isEnabled()
    window.clear_queue_button.click()
    window._on_download_done(ffmpeg, "")
    assert window.loader is None
    assert window._sources == []
    assert window._pending_paths == []
    assert window.media is None
    assert not window.start_button.isEnabled()
    assert window.queue_view.topLevelItemCount() == 0
    assert runners == []


def test_separate_file_drops_append_and_encode_in_order(batch_window, qapp):
    window, runners, media = batch_window
    first, _, second = list(media)
    window.clear_queue_button.click()
    window.drop_area.pathsDropped.emit([str(first)])
    window.drop_area.pathsDropped.emit([str(second)])
    assert first.name == second.name
    assert first.parent != second.parent
    assert [source.path for source in window._sources] == [first, second]
    assert window.queue_tabs.tabText(0) == "Queue (2)"
    assert window.media.path == first
    window.start_encoding()
    assert [runner.job.input_path for runner in runners] == [first]
    runners[0].finished.emit(True, str(runners[0].job.output_path))
    qapp.processEvents()
    assert [runner.job.input_path for runner in runners] == [first, second]
    runners[1].finished.emit(True, str(runners[1].job.output_path))
    qapp.processEvents()
    assert not window._encoding
    assert "2 succeeded" in window.queue_label.text()


def test_repeated_drops_deduplicate_resolved_paths(batch_window):
    window, _, media = batch_window
    first = next(iter(media))
    alias = first.parent / "nested" / ".." / first.name
    window.drop_area.pathsDropped.emit([str(first)])
    window.drop_area.pathsDropped.emit([str(alias)])
    window.drop_area.pathsDropped.emit([str(first.parent), str(alias)])
    _wait_finished(window)
    assert [source.path for source in window._sources] == list(media)
    assert window.queue_view.topLevelItemCount() == 3


@pytest.mark.parametrize("input_kind", ["file", "folder", "dialog"])
def test_appending_preserves_audio_selection_and_options(batch_window, monkeypatch, input_kind):
    window, _, media = batch_window
    first, second, third = media.values()
    first.audio_tracks = [
        AudioTrack(1, 0, "aac", 2, "stereo", "eng", "", True),
        AudioTrack(2, 1, "aac", 6, "5.1", None, "", False),
    ]
    window.clear_queue_button.click()
    window.drop_area.pathsDropped.emit([str(first.path)])
    window.track_combo.setCurrentIndex(1)
    window.language_combo.setEditText("fre")
    window.description_combo.setEditText("Keep description")
    window.suffix_edit.setText("_KEEP")
    if input_kind == "folder":
        window.drop_area.pathsDropped.emit([str(first.path.parent)])
    elif input_kind == "dialog":
        monkeypatch.setattr(
            gui.QFileDialog, "getOpenFileNames",
            lambda *_args: ([str(second.path), str(third.path)], ""),
        )
        window.open_button.click()
    else:
        window.drop_area.pathsDropped.emit([str(second.path)])
    _wait_finished(window)
    expected = [first.path, second.path]
    if input_kind != "file":
        expected.append(third.path)
    assert [source.path for source in window._sources] == expected
    assert window.media is first
    assert window.track_combo.currentIndex() == 1
    assert window.language_combo.currentText() == "fre"
    assert window.language_combo.isEnabled()
    assert window.description_combo.currentText() == "Keep description"
    assert window.suffix_edit.text() == "_KEEP"


@pytest.mark.parametrize("invalid_kind", ["resolution", "unreadable", "no-video"])
def test_invalid_additions_preserve_existing_queue(batch_window, monkeypatch, tmp_path, invalid_kind):
    window, _, media = batch_window
    path = tmp_path / "invalid.mkv"
    path.touch()
    if invalid_kind == "unreadable":
        def probe(*_args):
            raise gui.ProbeError("Cannot read this video")
        monkeypatch.setattr(gui, "probe", probe)
    else:
        media_info = MediaInfo(
            path, 1, 1280, 720, "h264" if invalid_kind == "resolution" else None
        )
        monkeypatch.setattr(gui, "probe", lambda *_args: media_info)
    window.drop_area.pathsDropped.emit([str(path)])
    assert [source.path for source in window._sources] == list(media)
    assert window.media.path == next(iter(media))
    assert window.queue_view.topLevelItemCount() == 3
    assert window.start_button.isEnabled()


def test_removed_video_can_be_appended_again_and_clear_starts_fresh(batch_window):
    window, _, media = batch_window
    first, second, third = list(media)
    window.queue_view.topLevelItem(1).setSelected(True)
    window.remove_queue_button.click()
    window.drop_area.pathsDropped.emit([str(second)])
    assert [source.path for source in window._sources] == [first, third, second]
    window.clear_queue_button.click()
    window.drop_area.pathsDropped.emit([str(third)])
    assert [source.path for source in window._sources] == [third]
    assert window.queue_view.topLevelItemCount() == 1
