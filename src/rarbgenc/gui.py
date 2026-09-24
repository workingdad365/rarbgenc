"""PySide6 기반 메인 창."""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Qt, QThread, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QPixmap
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

VIDEO_FILTER = (
    "Video files (*.mkv *.mp4 *.m4v *.m2ts *.mts *.ts *.avi *.mov *.wmv *.webm "
    "*.mpg *.mpeg *.vob *.flv);;All files (*)"
)
LOG_TAIL_LINES = 200
LOGO_PATH = Path(__file__).parent / "assets" / "rarbg_logo.png"
LOGO_HEIGHT = 36


def _elastic(label: QLabel) -> QLabel:
    """긴 경로 문자열이 창 최소 폭을 늘리지 않도록 가로 크기 힌트를 무시함"""
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def _format_seconds(seconds: float) -> str:
    total = int(max(seconds, 0))
    return f"{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}"


class DropArea(QFrame):
    """파일 드래그앤드랍 영역"""

    fileDropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(90)
        self._label = QLabel("Drop a video file here", self)
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
    def _local_file(event) -> str | None:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            path = url.toLocalFile()
            if path and Path(path).is_file():
                return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.isEnabled() and self._local_file(event):
            event.acceptProposedAction()
            self._set_highlight(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_highlight(False)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_highlight(False)
        path = self._local_file(event)
        if path:
            event.acceptProposedAction()
            self.fileDropped.emit(path)


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
        self._started_at = 0.0

        self.setWindowTitle(f"rarbgenc {__version__} - RARBG-style x264 encoder")
        self.resize(760, 720)
        self._build_ui()
        self._load_settings()
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
        title = QLabel(f"<b>x264 2-pass encoder</b><br>v{__version__}", self)
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
        self.drop_area.fileDropped.connect(self.load_file)
        open_row = QHBoxLayout()
        self.open_button = QPushButton("Open...", source_box)
        self.open_button.clicked.connect(self.open_file_dialog)
        self.info_label = _elastic(QLabel("", source_box))
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_row.addWidget(self.open_button)
        open_row.addWidget(self.info_label, 1)
        source_layout.addWidget(self.drop_area)
        source_layout.addLayout(open_row)
        root.addWidget(source_box)

        # 오디오
        audio_box = QGroupBox("Audio", self)
        audio_form = QFormLayout(audio_box)
        self.track_combo = QComboBox(audio_box)
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
        self.description_combo = QComboBox(output_box)
        self.description_combo.setEditable(True)
        self.description_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.description_combo.lineEdit().setMaxLength(255)
        dir_row = QHBoxLayout()
        self.output_dir_edit = QLineEdit(output_box)
        self.output_dir_edit.setPlaceholderText("Same folder as source")
        self.output_dir_edit.textChanged.connect(self._update_output_preview)
        browse = QPushButton("Browse...", output_box)
        browse.clicked.connect(self.choose_output_dir)
        dir_row.addWidget(self.output_dir_edit, 1)
        dir_row.addWidget(browse)
        self.suffix_edit = QLineEdit(output_box)
        self.suffix_edit.textChanged.connect(self._update_output_preview)
        self.output_preview = QLabel("", output_box)
        self.output_preview.setWordWrap(True)
        _elastic(self.output_preview)
        self.output_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
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

    def _store_settings(self) -> None:
        s = self.settings
        s.remember_description(self.description_combo.currentText())
        s.output_dir = self.output_dir_edit.text().strip()
        s.suffix = self.suffix_edit.text()
        try:
            s.save()
        except OSError as exc:
            self.log_view.appendPlainText(f"Failed to save settings: {exc}")

    # ---- ffmpeg ----

    def check_ffmpeg(self) -> None:
        self.ffmpeg = ffmpeg_tools.locate()
        if self.ffmpeg is None:
            self._show_banner(
                "ffmpeg was not found.\n\n" + ffmpeg_tools.install_hint()
                + "\n\nOr click the button below to download a static ffmpeg build "
                "for rarbgenc only."
            )
            self.statusBar().showMessage("ffmpeg not found")
        elif not ffmpeg_tools.has_encoder(self.ffmpeg.ffmpeg, "libx264"):
            self._show_banner(
                f"The ffmpeg at {self.ffmpeg.ffmpeg} was built without libx264.\n\n"
                + ffmpeg_tools.install_hint()
                + "\n\nOr click the button below to download a static ffmpeg build "
                "that includes libx264."
            )
            self.statusBar().showMessage(f"ffmpeg without libx264: {self.ffmpeg.ffmpeg}")
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
        self.banner.hide()
        self.statusBar().showMessage(
            f"{ffmpeg_tools.version(paths.ffmpeg)}  (bundled: {paths.ffmpeg})"
        )
        self._update_start_enabled()
        if self.media is None and self.drop_area.property("pending"):
            self.load_file(self.drop_area.property("pending"))

    # ---- 원본 파일 ----

    def open_file_dialog(self) -> None:
        start = self.settings.last_open_dir or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open video", start, VIDEO_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str) -> None:
        if self.runner is not None:
            return
        file = Path(path)
        self.settings.last_open_dir = str(file.parent)
        if self.ffmpeg is None:
            self.drop_area.setProperty("pending", path)
            QMessageBox.warning(self, "ffmpeg required", "Install ffmpeg first (see the notice above).")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            media = probe(self.ffmpeg.ffprobe, file)
        except ProbeError as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Cannot read file", f"{file.name}\n\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        if not media.has_video:
            QMessageBox.critical(self, "No video", f"{file.name} has no video stream.")
            return
        self.media = media
        self.drop_area.setText(f"{file.name}\n{file.parent}")
        self.info_label.setText(media.summary())
        self.progress_bar.setValue(0)
        self.status_label.setText("")

        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        for track in media.audio_tracks:
            self.track_combo.addItem(track.label(), track.order)
        default = next((t.order for t in media.audio_tracks if t.is_default), 0)
        if media.audio_tracks:
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
            self.runner is None and self.ffmpeg is not None and self.media is not None
        )

    # ---- 인코딩 ----

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.drop_area, self.open_button, self.description_combo,
            self.output_dir_edit, self.suffix_edit,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self._update_audio_ui()
        else:
            self.track_combo.setEnabled(False)
            self.language_combo.setEnabled(False)

    def start_encoding(self) -> None:
        if self.media is None or self.ffmpeg is None or self.runner is not None:
            return
        out = self._output_path()
        assert out is not None
        try:
            same = out.resolve() == self.media.path.resolve()
        except OSError:
            same = False
        if same:
            QMessageBox.critical(self, "Invalid output", "Output file would overwrite the source file.\nSet a suffix or another directory.")
            return

        track = self._current_track()
        language = languages.DEFAULT_LANGUAGE
        if track is not None:
            if track.language:
                language = track.language
            else:
                language = languages.parse_display(self.language_combo.currentText())
                if not languages.is_valid_code(language):
                    QMessageBox.critical(self, "Invalid language", "Enter a three-letter ISO 639-2 code (e.g. eng, kor, jpn).")
                    return
                self.settings.fallback_language = language

        if out.exists():
            answer = QMessageBox.question(self, "Overwrite?", f"{out}\nalready exists. Overwrite it?")
            if answer != QMessageBox.StandardButton.Yes:
                return

        description = self.description_combo.currentText().strip()
        self._store_settings()
        self.description_combo.clear()
        self.description_combo.addItems(self.settings.description_history)
        self.description_combo.setEditText(description)

        job = encoder.EncodeJob(
            input_path=self.media.path,
            output_path=out,
            audio_stream_index=track.stream_index if track else None,
            audio_channels=track.channels if track else 0,
            language=language,
            description=description,
        )
        self.log_view.clear()
        self.runner = EncodeRunner(self.ffmpeg.ffmpeg, job, self.media.duration)
        self.runner.progress.connect(self._on_progress)
        self.runner.log.connect(self.log_view.appendPlainText)
        self.runner.finished.connect(self._on_finished)
        self._set_inputs_enabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        if not self.media.duration:
            self.progress_bar.setRange(0, 0)  # 길이를 알 수 없으면 불확정 표시
        self._started_at = time.monotonic()
        self.runner.start()

    def cancel_encoding(self) -> None:
        if self.runner is not None:
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Cancelling...")
            self.runner.cancel()

    def _on_progress(self, fraction: float, text: str) -> None:
        self.progress_bar.setValue(int(fraction * 1000))
        elapsed = time.monotonic() - self._started_at
        info = f"{text}   elapsed {_format_seconds(elapsed)}"
        if 0.01 < fraction < 1.0:
            info += f"   ETA {_format_seconds(elapsed * (1 - fraction) / fraction)}"
        self.status_label.setText(info)

    def _on_finished(self, ok: bool, message: str) -> None:
        self.runner = None
        self.progress_bar.setRange(0, 1000)
        self.cancel_button.setEnabled(False)
        self._set_inputs_enabled(True)
        self._update_start_enabled()
        elapsed = _format_seconds(time.monotonic() - self._started_at)
        if ok:
            self.progress_bar.setValue(1000)
            self.status_label.setText(f"Done in {elapsed}")
            QMessageBox.information(self, "Encoding finished", f"Saved to:\n{message}")
        else:
            self.progress_bar.setValue(0)
            self.status_label.setText(message)
            if message != "Cancelled":
                QMessageBox.critical(self, "Encoding failed", f"{message}\n\nSee the log for details.")

    def closeEvent(self, event) -> None:
        if self.downloader is not None:
            QMessageBox.information(self, "Download in progress", "Wait until the ffmpeg download finishes.")
            event.ignore()
            return
        if self.runner is not None:
            answer = QMessageBox.question(self, "Encoding in progress", "Cancel encoding and quit?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.cancel_and_wait()
        self._store_settings()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("rarbgenc")
    initial = argv[1] if len(argv) > 1 else None
    window = MainWindow(Settings.load(), initial)
    window.show()
    return app.exec()
