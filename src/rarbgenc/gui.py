"""PySide6 기반 메인 창."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from rarbgenc import __version__, encoder, ffmpeg_tools, languages
from rarbgenc.probe import MediaInfo, ProbeError, probe
from rarbgenc.settings import Settings
from rarbgenc.sources import VIDEO_EXTENSIONS, iter_video_files

VIDEO_FILTER = "Video files (" + " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS) + ");;All files (*)"
DROP_PROMPT = "Drop video files or folders here"
LOG_TAIL_LINES = 200
LOGO_PATH = Path(__file__).parent / "assets" / "rarbg_logo.png"
LOGO_HEIGHT = 36
ICON_PATH = Path(__file__).parent / "assets" / "icon.png"
APP_ID = "drasys.rarbgenc"


def _elastic(label: QLabel) -> QLabel:
    """긴 경로 문자열이 창 최소 폭을 늘리지 않도록 가로 크기 힌트를 무시함"""
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _format_seconds(seconds: float) -> str:
    total = int(max(seconds, 0))
    return f"{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _set_windows_app_id(app_id: str) -> None:
    """Windows 작업 표시줄이 python 대신 이 앱의 아이콘을 쓰도록 AppUserModelID 를 지정함"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass



class DropArea(QFrame):
    """파일과 폴더 드래그앤드랍 영역"""

    pathsDropped = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(90)
        self._label = QLabel(DROP_PROMPT, self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        _elastic(self._label)
        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        self._set_highlight(False)

    def setText(self, text: str) -> None:
        self._label.setText(text)

    def _set_highlight(self, on: bool) -> None:
        border = "palette(highlight)" if on else "palette(mid)"
        self.setStyleSheet(f"DropArea {{ border: 2px dashed {border}; border-radius: 8px; }}")

    @staticmethod
    def _local_paths(event) -> list[str]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        paths = []
        for url in mime.urls():
            path = url.toLocalFile()
            if not path:
                continue
            file = Path(path)
            if file.is_dir() or (file.is_file() and file.suffix.lower() in VIDEO_EXTENSIONS):
                paths.append(path)
        return paths

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.isEnabled() and self._local_paths(event):
            event.acceptProposedAction()
            self._set_highlight(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_highlight(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_highlight(False)
        paths = self._local_paths(event) if self.isEnabled() else []
        if paths:
            event.acceptProposedAction()
            self.pathsDropped.emit(paths)
        else:
            event.ignore()


class SourceLoader(QThread):
    """폴더 탐색과 동영상 검사를 백그라운드에서 수행함"""

    progress = Signal(str)

    def __init__(self, ffprobe: str, paths: list[str], parent: QObject) -> None:
        super().__init__(parent)
        self._ffprobe = ffprobe
        self._paths = paths
        self.sources: list[MediaInfo] = []
        self.errors: list[str] = []

    def run(self) -> None:
        for path in iter_video_files(
            self._paths, lambda exc: self.errors.append(str(exc)), self.isInterruptionRequested
        ):
            self.progress.emit(f"Scanning: {path}")
            try:
                media = probe(self._ffprobe, path)
            except ProbeError as exc:
                self.errors.append(f"{path}: {exc}")
                continue
            if not media.has_video:
                self.errors.append(f"{path}: No video stream")
            elif not media.is_1080p:
                self.errors.append(
                    f"{path}: Unsupported source resolution: {media.width or '?'}x{media.height or '?'}"
                )
            else:
                self.sources.append(media)


class EncodeRunner(QObject):
    """QProcess 로 1pass -> 2pass 를 순차 실행함"""

    progress = Signal(float, str)  # 전체 진행률(0~1), 상태 문자열
    log = Signal(str)
    finished = Signal(bool, str)  # 성공 여부, 메시지

    def __init__(self, ffmpeg: str, job: encoder.EncodeJob, duration: float | None) -> None:
        super().__init__()
        self._ffmpeg = ffmpeg
        self._job = job
        self._duration = duration
        self._process: QProcess | None = None
        self._pass = 0
        self._buffer = b""
        self._cancelled = False
        self._done = False
        self._tmpdir = Path(tempfile.mkdtemp(prefix="rarbgenc-"))
        self._passlog = self._tmpdir / "passlog"

    def start(self) -> None:
        try:
            self._job.output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._finish(False, f"Cannot create output directory: {exc}")
            return
        self._run_pass(1)

    def cancel(self) -> None:
        self._cancelled = True
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
        else:
            self._finish(False, "Cancelled")

    def cancel_and_wait(self, msecs: int = 5000) -> None:
        """취소 후 프로세스 종료와 정리 작업까지 동기적으로 대기함 (앱 종료 시 사용)"""
        self.cancel()
        if self._process is not None:
            self._process.waitForFinished(msecs)

    def _run_pass(self, pass_no: int) -> None:
        self._pass = pass_no
        self._buffer = b""
        build = encoder.build_pass1 if pass_no == 1 else encoder.build_pass2
        cmd = build(self._ffmpeg, self._job, self._passlog)
        self.log.emit(f"[pass {pass_no}] " + " ".join(cmd))
        self.progress.emit(encoder.overall_progress(pass_no, 0.0), f"Pass {pass_no}/2")

        process = QProcess(self)
        process.setWorkingDirectory(str(self._tmpdir))
        process.readyReadStandardOutput.connect(self._on_stdout)
        process.readyReadStandardError.connect(self._on_stderr)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start(cmd[0], cmd[1:])

    def _on_stdout(self) -> None:
        if self._process is None:
            return
        self._buffer += bytes(self._process.readAllStandardOutput().data())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            seconds = encoder.parse_progress_seconds(raw.decode("utf-8", "replace"))
            if seconds is None or not self._duration:
                continue
            fraction = seconds / self._duration
            self.progress.emit(
                encoder.overall_progress(self._pass, fraction),
                f"Pass {self._pass}/2  {min(fraction, 1.0) * 100:5.1f}%",
            )

    def _on_stderr(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardError().data()).decode("utf-8", "replace")
        for line in text.splitlines():
            if line.strip():
                self.log.emit(line.rstrip())

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._finish(False, f"Failed to start ffmpeg: {self._ffmpeg}")

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        if self._cancelled:
            self._finish(False, "Cancelled")
        elif status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self._finish(False, f"ffmpeg pass {self._pass} failed (exit code {exit_code})")
        elif self._pass == 1:
            self._run_pass(2)
        else:
            self.progress.emit(1.0, "Done")
            self._finish(True, str(self._job.output_path))

    def _finish(self, ok: bool, message: str) -> None:
        if self._done:
            return
        self._done = True
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        if not ok and self._pass == 2:
            # 실패/취소 시 불완전한 출력 파일 삭제
            try:
                self._job.output_path.unlink(missing_ok=True)
            except OSError:
                pass
        self.finished.emit(ok, message)


class DownloadWorker(QThread):
    """ffmpeg 자동 다운로드 백그라운드 작업"""

    done = Signal(object, str)  # FFmpegPaths 또는 None, 오류 메시지

    def run(self) -> None:
        try:
            paths = ffmpeg_tools.download()
        except Exception as exc:  # 네트워크/압축 해제 오류 등 모두 사용자에게 표시
            self.done.emit(None, str(exc))
            return
        self.done.emit(paths, "")


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, initial_file: str | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.ffmpeg: ffmpeg_tools.FFmpegPaths | None = None
        self.media: MediaInfo | None = None
        self.runner: EncodeRunner | None = None
        self.downloader: DownloadWorker | None = None
        self.loader: SourceLoader | None = None
        self._sources: list[MediaInfo] = []
        self._source_errors: list[str] = []
        self._pending_paths: list[str] = []
        self._jobs: deque[tuple[MediaInfo, encoder.EncodeJob]] = deque()
        self._encoding = False
        self._cancelled = False
        self._total = 0
        self._completed = 0
        self._failed = 0
        self._track_order = -1
        self._started_at = 0.0
        self._batch_started_at = 0.0
        self._last_result = (False, "")

        self.setWindowTitle(f"rarbgenc {__version__} - RARBG-style x264/x265 encoder")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(760, 720)
        self._build_ui()
        self._load_settings()
        self.codec_combo.currentIndexChanged.connect(self.check_ffmpeg)
        self.check_ffmpeg()
        if initial_file:
            self.load_file(initial_file)

    # ---- UI 구성 ----

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)

        # 헤더 (로고, 버전)
        header = QHBoxLayout()
        self.logo_label = QLabel(self)
        logo = QPixmap(str(LOGO_PATH))
        if logo.isNull():
            self.logo_label.hide()
        else:
            self.logo_label.setPixmap(
                logo.scaledToHeight(LOGO_HEIGHT, Qt.TransformationMode.SmoothTransformation)
            )
        title = QLabel(f"<b>x264 / x265 2-pass encoder</b><br>v{__version__}", self)
        header.addWidget(self.logo_label)
        header.addSpacing(8)
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # ffmpeg 미설치 안내
        self.banner = QFrame(self)
        self.banner.setStyleSheet(
            "QFrame#banner { background: #fff4ce; border: 1px solid #e0b000; border-radius: 6px; }"
            "QFrame#banner QLabel { color: #3a2e00; }"
        )
        self.banner.setObjectName("banner")
        banner_layout = QVBoxLayout(self.banner)
        self.banner_label = QLabel(self.banner)
        self.banner_label.setWordWrap(True)
        self.banner_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        banner_buttons = QHBoxLayout()
        self.download_button = QPushButton("Download ffmpeg automatically", self.banner)
        self.download_button.clicked.connect(self.download_ffmpeg)
        self.recheck_button = QPushButton("Check again", self.banner)
        self.recheck_button.clicked.connect(self.check_ffmpeg)
        banner_buttons.addWidget(self.download_button)
        banner_buttons.addWidget(self.recheck_button)
        banner_buttons.addStretch()
        banner_layout.addWidget(self.banner_label)
        banner_layout.addLayout(banner_buttons)
        root.addWidget(self.banner)

        # 원본
        source_box = QGroupBox("Source", self)
        source_layout = QVBoxLayout(source_box)
        self.drop_area = DropArea(source_box)
        self.drop_area.pathsDropped.connect(self.load_paths)
        open_row = QHBoxLayout()
        self.open_button = QPushButton("Open...", source_box)
        self.open_button.clicked.connect(self.open_file_dialog)
        self.info_label = _elastic(QLabel("", source_box))
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_row.addWidget(self.open_button)
        open_row.addWidget(self.info_label, 1)
        source_layout.addWidget(self.drop_area)
        source_layout.addLayout(open_row)
        self.queue_label = _elastic(QLabel("", source_box))
        source_layout.addWidget(self.queue_label)
        root.addWidget(source_box)

        # 오디오
        audio_box = QGroupBox("Audio", self)
        audio_form = QFormLayout(audio_box)
        self.track_combo = QComboBox(audio_box)
        self.track_combo.setToolTip(
            "For a batch, use this audio track number in every file. "
            "If absent, use that file's default or first audio track."
        )
        self.track_combo.currentIndexChanged.connect(self._on_track_changed)
        self.language_combo = QComboBox(audio_box)
        self.language_combo.setEditable(True)
        self.language_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for code, _name in languages.LANGUAGES:
            self.language_combo.addItem(languages.display_name(code), code)
        self.language_note = _elastic(QLabel("", audio_box))
        self.audio_out_label = QLabel("", audio_box)
        audio_form.addRow("Track:", self.track_combo)
        audio_form.addRow("Language:", self.language_combo)
        audio_form.addRow("", self.language_note)
        audio_form.addRow("Output:", self.audio_out_label)
        root.addWidget(audio_box)

        # 출력
        output_box = QGroupBox("Output", self)
        output_form = QFormLayout(output_box)
        self.codec_combo = QComboBox(output_box)
        self.codec_combo.addItem("H.264 / x264 - 8-bit, 2500 kbps", "x264")
        self.codec_combo.addItem("H.265 / x265 - 10-bit, 2000 kbps", "x265")
        self.description_combo = QComboBox(output_box)
        self.description_combo.setEditable(True)
        self.description_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.description_combo.lineEdit().setMaxLength(255)
        dir_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit(output_box)
        self.output_dir_edit.setPlaceholderText("Same folder as source")
        self.output_dir_edit.textChanged.connect(self._update_output_preview)
        self.output_browse = QPushButton("Browse...", output_box)
        self.output_browse.clicked.connect(self.choose_output_dir)
        dir_row.addWidget(self.output_dir_edit, 1)
        dir_row.addWidget(self.output_browse)
        self.suffix_edit = QLineEdit(output_box)
        self.suffix_edit.textChanged.connect(self._update_output_preview)
        self.output_preview = QLabel("", output_box)
        self.output_preview.setWordWrap(True)
        _elastic(self.output_preview)
        self.output_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        output_form.addRow("Video:", self.codec_combo)
        output_form.addRow("Description:", self.description_combo)
        output_form.addRow("Directory:", dir_row)
        output_form.addRow("Suffix:", self.suffix_edit)
        output_form.addRow("File:", self.output_preview)
        root.addWidget(output_box)

        # 실행
        action_row = QHBoxLayout()
        self.start_button = QPushButton("Start encoding", self)
        self.start_button.clicked.connect(self.start_encoding)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self.cancel_encoding)
        self.cancel_button.setEnabled(False)
        self.status_label = _elastic(QLabel("", self))
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.status_label, 1)
        root.addLayout(action_row)

        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setTextVisible(False)
        root.addWidget(self.progress_bar)

        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(LOG_TAIL_LINES)
        mono = QFont("Consolas" if sys.platform == "win32" else "Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.log_view.setFont(mono)
        root.addWidget(self.log_view, 1)

        self.setCentralWidget(central)
        self._update_audio_ui()

    def _load_settings(self) -> None:
        s = self.settings
        self.description_combo.addItems(s.description_history)
        self.description_combo.setEditText(s.description)
        self.output_dir_edit.setText(s.output_dir)
        self.suffix_edit.setText(s.suffix)
        self.codec_combo.setCurrentIndex(max(0, self.codec_combo.findData(s.video_codec)))

    def _store_settings(self) -> None:
        s = self.settings
        s.remember_description(self.description_combo.currentText())
        s.output_dir = self.output_dir_edit.text().strip()
        s.suffix = self.suffix_edit.text()
        s.video_codec = self.codec_combo.currentData()
        try:
            s.save()
        except OSError as exc:
            self.log_view.appendPlainText(f"Failed to save settings: {exc}")

    # ---- ffmpeg ----

    def check_ffmpeg(self) -> None:
        self.ffmpeg = ffmpeg_tools.locate()
        self._check_selected_encoder()

    def _check_selected_encoder(self) -> None:
        codec = f"lib{self.codec_combo.currentData()}"
        if self.ffmpeg is None:
            self._show_banner(
                "ffmpeg was not found.\n\n" + ffmpeg_tools.install_hint()
                + "\n\nOr click the button below to download a static ffmpeg build "
                "for rarbgenc only."
            )
            self.statusBar().showMessage("ffmpeg not found")
        elif not ffmpeg_tools.has_encoder(self.ffmpeg.ffmpeg, codec):
            self._show_banner(
            f"The ffmpeg at {self.ffmpeg.ffmpeg} was built without {codec}.\n\n"
                + ffmpeg_tools.install_hint()
                + "\n\nOr click the button below to download a static ffmpeg build "
                f"that includes {codec}."
            )
            self.statusBar().showMessage(f"ffmpeg without {codec}: {self.ffmpeg.ffmpeg}")
            self.ffmpeg = None
        else:
            self.banner.hide()
            ver = ffmpeg_tools.version(self.ffmpeg.ffmpeg)
            self.statusBar().showMessage(f"{ver}  ({self.ffmpeg.source}: {self.ffmpeg.ffmpeg})")
        self._update_start_enabled()

    def _show_banner(self, text: str) -> None:
        self.banner_label.setText(text)
        self.banner.show()

    def download_ffmpeg(self) -> None:
        if self.downloader is not None:
            return
        self.download_button.setEnabled(False)
        self.recheck_button.setEnabled(False)
        self.download_button.setText("Downloading ffmpeg... (about 100 MB)")
        self.downloader = DownloadWorker(self)
        self.downloader.done.connect(self._on_download_done)
        self.downloader.start()

    def _on_download_done(self, paths, error: str) -> None:
        self.downloader = None
        self.download_button.setEnabled(True)
        self.recheck_button.setEnabled(True)
        self.download_button.setText("Download ffmpeg automatically")
        if paths is None:
            QMessageBox.critical(self, "Download failed", f"Could not download ffmpeg:\n{error}")
            return
        self.ffmpeg = paths
        self._check_selected_encoder()
        if self.ffmpeg is not None and self._pending_paths:
            self.load_paths(self._pending_paths)

    # ---- 원본 파일 ----

    def open_file_dialog(self) -> None:
        start = self.settings.last_open_dir or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Open videos", start, VIDEO_FILTER)
        if paths:
            self.load_paths(paths)

    def load_paths(self, paths: list[str]) -> None:
        if not paths or self._encoding or self.loader is not None:
            return
        if len(paths) == 1 and not Path(paths[0]).is_dir():
            self.load_file(paths[0])
            return
        if self.ffmpeg is None:
            self._pending_paths = list(paths)
            QMessageBox.warning(self, "ffmpeg required", "Install ffmpeg first (see the notice above).")
            return
        first = Path(paths[0])
        self.settings.last_open_dir = str(first if first.is_dir() else first.parent)
        self._pending_paths = []
        self._sources = []
        self._source_errors = []
        self._cancelled = False
        self._show_media(None)
        self.log_view.clear()
        self.queue_label.setText("Scanning video files...")
        self.loader = SourceLoader(self.ffmpeg.ffprobe, list(paths), self)
        self.loader.progress.connect(self.status_label.setText)
        self.loader.finished.connect(self._on_sources_loaded)
        self._set_inputs_enabled(False)
        self._update_start_enabled()
        self.cancel_button.setEnabled(True)
        self.loader.start()

    def _on_sources_loaded(self) -> None:
        loader = self.loader
        assert loader is not None
        cancelled = self._cancelled
        self._sources = [] if cancelled else loader.sources
        self._source_errors = loader.errors
        self.loader = None
        loader.deleteLater()
        self.cancel_button.setEnabled(False)
        self._show_media(self._sources[0] if self._sources else None)
        self._set_inputs_enabled(True)
        for error in self._source_errors:
            self.log_view.appendPlainText(f"[skipped] {error}")
        self.queue_label.setText(
            f"{len(self._sources)} video(s) queued, {len(self._source_errors)} skipped"
        )
        if cancelled:
            self.status_label.setText("Cancelled")
        elif not self._sources:
            self.status_label.setText("No supported video files found")
            QMessageBox.warning(
                self, "No videos to encode",
                "No supported 1080p video files were found.\nSee the log for skipped files.",
            )
        else:
            self.status_label.setText("Ready to encode the queue")
        self._update_start_enabled()

    def load_file(self, path: str) -> None:
        if self._encoding or self.loader is not None:
            return
        file = Path(path)
        if file.is_dir():
            self.load_paths([path])
            return
        if file.suffix.lower() not in VIDEO_EXTENSIONS:
            QMessageBox.warning(self, "Not a video file", "Choose a video file or a folder.")
            return
        self.settings.last_open_dir = str(file.parent)
        if self.ffmpeg is None:
            self._pending_paths = [path]
            QMessageBox.warning(self, "ffmpeg required", "Install ffmpeg first (see the notice above).")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            media = probe(self.ffmpeg.ffprobe, file)
        except ProbeError as exc:
            QMessageBox.critical(self, "Cannot read file", f"{file.name}\n\n{exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not media.has_video:
            QMessageBox.critical(self, "No video", f"{file.name} has no video stream.")
            return
        self._pending_paths = []
        self._sources = [media]
        self._source_errors = []
        self._show_media(media)
        self.queue_label.setText("1 video queued" if media.is_1080p else "No supported video queued")
        if not media.is_1080p:
            self._show_resolution_warning()

    def _show_media(self, media: MediaInfo | None) -> None:
        self.media = media
        self.drop_area.setText(f"{media.path.name}\n{media.path.parent}" if media else DROP_PROMPT)
        self.info_label.setText(media.summary() if media else "")
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.status_label.setText("")

        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        tracks = media.audio_tracks if media else []
        for track in tracks:
            self.track_combo.addItem(track.label(), track.order)
        default = next((t.order for t in tracks if t.is_default), 0)
        if tracks:
            self.track_combo.setCurrentIndex(default)
        self.track_combo.blockSignals(False)
        self._update_audio_ui()
        self._update_output_preview()

    # ---- 오디오 ----

    def _current_track(self):
        if self.media is None or not self.media.audio_tracks:
            return None
        index = self.track_combo.currentIndex()
        return self.media.audio_tracks[index] if index >= 0 else None

    def _on_track_changed(self, _index: int) -> None:
        self._update_audio_ui()

    def _update_audio_ui(self) -> None:
        track = self._current_track()
        if track is None:
            self.track_combo.setEnabled(False)
            self.language_combo.setEnabled(False)
            self.language_note.setText("No audio track" if self.media else "")
            self.audio_out_label.setText("")
            return
        self.track_combo.setEnabled(True)
        if track.language:
            self.language_combo.setEditText(languages.display_name(track.language))
            self.language_combo.setEnabled(False)
            self.language_note.setText("Taken from the source track")
        else:
            self.language_combo.setEditText(
                languages.display_name(self.settings.fallback_language)
            )
            self.language_combo.setEnabled(True)
            self.language_note.setText("Source track has no language tag - choose or type an ISO 639-2 code")
        profile = encoder.audio_profile(track.channels)
        self.audio_out_label.setText(f"AAC {profile.bitrate}, {profile.channels} ch")

    # ---- 출력 ----

    def choose_output_dir(self) -> None:
        start = self.output_dir_edit.text() or self.settings.last_open_dir or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Output directory", start)
        if path:
            self.output_dir_edit.setText(path)

    def _output_path(self) -> Path | None:
        if self.media is None:
            return None
        return encoder.output_path_for(
            self.media.path, self.output_dir_edit.text(), self.suffix_edit.text()
        )

    def _update_output_preview(self) -> None:
        out = self._output_path()
        self.output_preview.setText(str(out) if out else "")
        self._update_start_enabled()

    def _update_start_enabled(self) -> None:
        self.start_button.setEnabled(
            not self._encoding and self.loader is None
            and self.ffmpeg is not None and self.media is not None
            and self.media.is_1080p
        )

    # ---- 인코딩 ----

    def _show_resolution_warning(self) -> None:
        assert self.media is not None
        resolution = f"{self.media.width or '?'}x{self.media.height or '?'}"
        self.status_label.setText(f"Unsupported source resolution: {resolution}")
        QMessageBox.warning(
            self, "1080p source required",
            f"{self.media.path.name}\nSource resolution: {resolution}\n\n"
            "These encoding profiles support only 1080p sources.\n"
            "1920x1080 and cropped 1080p (e.g. 1920x804 or 1920x1040) are supported.\n"
            "Dimensions must be positive even numbers, no larger than 1920x1080, "
            "with width 1920 or height 1080.\n"
            "Choose a supported source; automatic resizing is not performed.",
        )

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.drop_area, self.open_button, self.description_combo,
            self.output_dir_edit, self.output_browse, self.suffix_edit, self.codec_combo,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self._update_audio_ui()
        else:
            self.track_combo.setEnabled(False)
            self.language_combo.setEnabled(False)

    def start_encoding(self) -> None:
        if self.media is None or self.ffmpeg is None or self._encoding or self.loader is not None:
            return
        if not self.media.is_1080p:
            self._show_resolution_warning()
            return
        track = self._current_track()
        fallback = self.settings.fallback_language
        if track is not None and not track.language:
            fallback = languages.parse_display(self.language_combo.currentText())
        if not languages.is_valid_code(fallback):
            QMessageBox.critical(self, "Invalid language", "Enter a three-letter ISO 639-2 code (e.g. eng, kor, jpn).")
            return
        self._track_order = self.track_combo.currentIndex()
        description = self.description_combo.currentText().strip()
        try:
            jobs = self._prepare_jobs(fallback, description)
        except OSError as exc:
            QMessageBox.critical(self, "Invalid output", str(exc))
            return
        if not jobs:
            return

        existing = [job.output_path for _, job in jobs if job.output_path.exists()]
        if existing:
            listing = "\n".join(str(path) for path in existing[:10])
            if len(existing) > 10:
                listing += f"\n... and {len(existing) - 10} more"
            answer = QMessageBox.question(
                self, "Overwrite?",
                f"{len(existing)} output file(s) already exist:\n{listing}\n\nOverwrite them?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.settings.fallback_language = fallback
        self._store_settings()
        self.description_combo.clear()
        self.description_combo.addItems(self.settings.description_history)
        self.description_combo.setEditText(description)

        self.log_view.clear()
        for error in self._source_errors:
            self.log_view.appendPlainText(f"[skipped] {error}")
        self._jobs = deque(jobs)
        self._total = len(jobs)
        self._completed = self._failed = 0
        self._cancelled = False
        self._encoding = True
        self._batch_started_at = time.monotonic()
        self._set_inputs_enabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._start_next_job()

    def _prepare_jobs(self, fallback: str, description: str) -> list[tuple[MediaInfo, encoder.EncodeJob]]:
        sources = self._sources
        source_paths = {media.path.resolve() for media in sources}
        outputs: set[Path] = set()
        jobs = []
        for media in sources:
            if not media.is_1080p:
                QMessageBox.warning(self, "1080p source required", f"{media.path}\nUnsupported source resolution.")
                return []
            out = encoder.output_path_for(
                media.path, self.output_dir_edit.text(), self.suffix_edit.text()
            )
            if out.resolve() in source_paths:
                QMessageBox.critical(
                    self, "Invalid output",
                    f"Output file would overwrite a source file:\n{out}\nSet a suffix or another directory.",
                )
                return []
            if out.resolve() in outputs:
                base = out
                number = 2
                while out.resolve() in outputs or out.resolve() in source_paths or out.exists():
                    out = base.with_name(f"{base.stem}_{number}{base.suffix}")
                    number += 1
            outputs.add(out.resolve())
            tracks = media.audio_tracks
            track = (
                tracks[self._track_order] if 0 <= self._track_order < len(tracks)
                else next((t for t in tracks if t.is_default), tracks[0] if tracks else None)
            )
            job = encoder.EncodeJob(
                input_path=media.path,
                output_path=out,
                audio_stream_index=track.stream_index if track else None,
                audio_channels=track.channels if track else 0,
                language=track.language if track and track.language else fallback,
                description=description,
                video_codec=self.codec_combo.currentData(),
            )
            jobs.append((media, job))
        return jobs

    def _start_next_job(self) -> None:
        if not self._encoding or self.runner is not None:
            return
        if self._cancelled or not self._jobs:
            self._finish_queue()
            return
        assert self.ffmpeg is not None
        media, job = self._jobs.popleft()
        self._show_media(media)
        for index, track in enumerate(media.audio_tracks):
            if track.stream_index == job.audio_stream_index:
                self.track_combo.setCurrentIndex(index)
                break
        self.output_preview.setText(str(job.output_path))
        self._set_inputs_enabled(False)
        self.queue_label.setText(f"Encoding {self._completed + 1}/{self._total}: {media.path.name}")
        self.log_view.appendPlainText(f"[{self._completed + 1}/{self._total}] {media.path}")
        if not media.duration:
            self.progress_bar.setRange(0, 0)  # 길이를 알 수 없으면 불확정 표시
        self._started_at = time.monotonic()
        try:
            self.runner = EncodeRunner(self.ffmpeg.ffmpeg, job, media.duration)
        except OSError as exc:
            self._on_finished(False, str(exc))
            return
        self.runner.progress.connect(self._on_progress)
        self.runner.log.connect(self.log_view.appendPlainText)
        self.runner.finished.connect(self._on_finished)
        self.runner.start()

    def cancel_encoding(self) -> None:
        if self.loader is not None:
            self._cancelled = True
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling scan...")
            self.loader.requestInterruption()
        elif self._encoding:
            self._cancelled = True
            self._jobs.clear()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling...")
            if self.runner is not None:
                self.runner.cancel()
            else:
                self._finish_queue()

    def _on_progress(self, fraction: float, text: str) -> None:
        self.progress_bar.setValue(int((self._completed + fraction) / self._total * 1000))
        elapsed = time.monotonic() - self._started_at
        info = f"{text}   elapsed {_format_seconds(elapsed)}"
        if 0.01 < fraction < 1.0:
            info += f"   ETA {_format_seconds(elapsed * (1 - fraction) / fraction)}"
        if self._total > 1:
            info = f"File {self._completed + 1}/{self._total}   {info}"
        self.status_label.setText(info)

    def _on_finished(self, ok: bool, message: str) -> None:
        if self.runner is not None:
            self.runner.deleteLater()
        self.runner = None
        self._last_result = (ok, message)
        self.log_view.appendPlainText(f"[{'saved' if ok else 'failed'}] {message}")
        if self._cancelled:
            self._finish_queue()
            return
        self._completed += 1
        if not ok:
            self._failed += 1
        self.progress_bar.setValue(int(self._completed / self._total * 1000))
        QTimer.singleShot(0, self._start_next_job)

    def _finish_queue(self) -> None:
        self._encoding = False
        self._jobs.clear()
        self._show_media(self._sources[0])
        if self._track_order >= 0:
            self.track_combo.setCurrentIndex(self._track_order)
        self.cancel_button.setEnabled(False)
        self._set_inputs_enabled(True)
        self._update_start_enabled()
        elapsed = _format_seconds(time.monotonic() - self._batch_started_at)
        summary = (
            f"{self._completed - self._failed} succeeded, {self._failed} failed, "
            f"{len(self._source_errors)} skipped"
        )
        self.queue_label.setText(summary)
        if self._cancelled:
            self.status_label.setText("Cancelled")
            return
        ok, message = self._last_result
        if self._total > 1 or self._source_errors:
            self.progress_bar.setValue(1000)
            self.status_label.setText(f"Queue finished in {elapsed}")
            QMessageBox.information(
                self, "Encoding finished", f"{summary}\n\nSee the log for file details."
            )
        elif ok:
            self.progress_bar.setValue(1000)
            self.status_label.setText(f"Done in {elapsed}")
            QMessageBox.information(self, "Encoding finished", f"Saved to:\n{message}")
        else:
            self.status_label.setText(message)
            QMessageBox.critical(self, "Encoding failed", f"{message}\n\nSee the log for details.")

    def closeEvent(self, event) -> None:
        if self.downloader is not None:
            QMessageBox.information(self, "Download in progress", "Wait until the ffmpeg download finishes.")
            event.ignore()
            return
        if self.loader is not None:
            answer = QMessageBox.question(self, "Scan in progress", "Cancel scanning and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancelled = True
            self.loader.requestInterruption()
            self.loader.wait()
        if self._encoding:
            answer = QMessageBox.question(self, "Encoding in progress", "Cancel encoding and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancelled = True
            self._jobs.clear()
            if self.runner is not None:
                self.runner.cancel_and_wait()
            else:
                self._finish_queue()
        self._store_settings()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    _set_windows_app_id(APP_ID)
    app = QApplication(argv)
    app.setApplicationName("rarbgenc")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    initial = argv[1] if len(argv) > 1 else None
    window = MainWindow(Settings.load(), initial)
    window.show()
    return app.exec()
