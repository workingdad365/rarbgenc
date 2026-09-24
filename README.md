<p align="center"><img src="rarbg_logo.png" alt="RARBG" height="70"></p>

<h1 align="center">rarbgenc</h1>

**English** | [한국어](README.ko.md)

A small desktop app (PySide6) that encodes videos into x264 MP4 files using the same
2-pass settings RARBG used for its compact Blu-ray rips.

![](screenshot.webp)

![python](https://img.shields.io/badge/python-3.13-blue) ![ui](https://img.shields.io/badge/ui-PySide6-green)

## Features

- Open a source video with a file dialog or drag & drop
- Pick the audio track to encode (all tracks are listed via `ffprobe`)
- The audio language tag is copied from the source track. Only when the source has
  no language tag can you choose or type one (ISO 639-2, default `eng`).
- Short description written into the MP4 `description` metadata
- Output directory (blank = same folder as the source) and file name suffix (default `_ENCODED`)
- Description history, fallback language, output directory and suffix are remembered
- Progress bar with pass indicator, elapsed time and ETA; cancel at any time
- If ffmpeg is missing, the app shows install instructions and can download a static
  ffmpeg build for itself with one click

## Encoding settings

| | |
|---|---|
| Video | `libx264`, 2-pass, `-b:v 2500k` |
| Pass 1 x264 params | `b-adapt=2:rc-lookahead=50` |
| Pass 2 x264 params | `me=umh:subme=9:me_range=24:ref=4:trellis=2:lookahead_threads=4:direct=auto:keyint_min=25:vbv_maxrate=31250:vbv_bufsize=31250:filler=0:psy_rd=1.00,0.15:aq-mode=3:deblock=-1,-1:chroma_qp_offset=0` |
| Audio (source 6 ch or more, e.g. 5.1 / 7.1) | AAC 224k, 6 channels |
| Audio (source 2 to 5 ch) | AAC 128k, 2 channels (downmix) |
| Audio (mono source) | AAC 96k, 1 channel |
| Metadata | `creation_time=now`, `title` / `comment` = source file name, `description` = your text, audio `language` = source (or chosen) code, video `language` cleared |

Only the first video stream and the selected audio track are written; subtitles are not included.
Pass log files go to a temporary folder and are deleted after encoding.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- ffmpeg **with libx264** (see below)

## Installation

```bash
git clone <this repository> rarbgenc
cd rarbgenc
uv tool install --editable .
```

This installs a `rarbgenc` executable (e.g. `~/.local/bin/rarbgenc`). Because the install is
editable, changes to the source take effect immediately.

To uninstall: `uv tool uninstall rarbgenc`

## Installing ffmpeg

rarbgenc uses `ffmpeg` / `ffprobe` from your `PATH` first.

| OS | Command |
|---|---|
| Windows | `winget install --id Gyan.FFmpeg -e` (or `scoop install ffmpeg`, `choco install ffmpeg`) |
| macOS | `brew install ffmpeg` |
| Debian / Ubuntu | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` (RPM Fusion) |
| Arch | `sudo pacman -S ffmpeg` |

Restart rarbgenc after installing so the updated `PATH` is picked up.

**One-click alternative:** if ffmpeg is not found (or was built without libx264), click
**Download ffmpeg automatically** in the app. A static build (about 100 MB, via
[static-ffmpeg](https://github.com/zackees/static_ffmpeg)) is stored in the app data folder and
used only by rarbgenc:

- Windows: `%LOCALAPPDATA%\rarbgenc\ffmpeg`
- macOS: `~/Library/Application Support/rarbgenc/ffmpeg`
- Linux: `~/.local/share/rarbgenc/ffmpeg`

## Usage

```bash
rarbgenc                 # open the window
rarbgenc "movie.mkv"     # open the window with a file preloaded
```

1. Drop a video on the window (or click **Open...**).
2. Choose the audio track. Set the language only if the source track has none.
3. Edit the description, output directory and suffix if needed.
4. Click **Start encoding**.

Output: `<output dir>/<source name><suffix>.mp4`

## Settings file

- Windows: `%APPDATA%\rarbgenc\settings.json`
- macOS: `~/Library/Application Support/rarbgenc/settings.json`
- Linux: `~/.config/rarbgenc/settings.json`

## Development

```bash
uv sync
uv run rarbgenc
uv run pytest
```

Integration and GUI tests generate small sample clips with ffmpeg's `lavfi` source and run
real 2-pass encodes; they are skipped when ffmpeg with libx264 is not available.
GUI tests run with `QT_QPA_PLATFORM=offscreen`.

## Project layout

```
src/rarbgenc/
  encoder.py       2-pass command builder, audio profile, progress parsing
  probe.py         ffprobe wrapper
  languages.py     ISO 639-2 codes and normalization
  settings.py      persisted user settings
  ffmpeg_tools.py  ffmpeg lookup, auto download, install hints
  gui.py           PySide6 main window and QProcess runner
tests/
```
