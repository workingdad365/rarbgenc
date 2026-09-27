<p align="center"><img src="rarbg_logo.png" alt="RARBG" height="70"></p>

<h1 align="center">rarbgenc</h1>

**English** | [한국어](README.ko.md)

A small desktop app (PySide6) that encodes videos into H.264 or H.265 MP4 files using
2-pass profiles reconstructed from RARBG Blu-ray samples.

![](screenshot.webp)

![python](https://img.shields.io/badge/python-3.13-blue) ![ui](https://img.shields.io/badge/ui-PySide6-green)

## Features

- Open a source video with a file dialog or drag & drop
- Choose H.264 / x264 (8-bit, 2500 kbps, default) or H.265 / x265 (10-bit, 2000 kbps)
- Pick the audio track to encode (all tracks are listed via `ffprobe`)
- The audio language tag is copied from the source track. Only when the source has
  no language tag can you choose or type one (ISO 639-2, default `eng`).
- Short description written into the MP4 `description` metadata
- Output directory (blank = same folder as the source) and file name suffix (default `_ENCODED`)
- Video codec, description history, fallback language, output directory and suffix are remembered
- Progress bar with pass indicator, elapsed time and ETA; cancel at any time
- If ffmpeg is missing, the app shows install instructions and can download a static
  ffmpeg build for itself with one click

## Encoding settings

| | |
|---|---|
| H.264 video (default) | `libx264`, 2-pass, `-b:v 2500k`, `yuv420p`, High, Level 4.1 |
| Pass 1 x264 params | `b-adapt=2:rc-lookahead=50` |
| Pass 2 x264 params | `me=umh:subme=9:me_range=24:ref=4:trellis=2:lookahead_threads=4:direct=auto:keyint_min=25:vbv_maxrate=31250:vbv_bufsize=31250:filler=0:psy_rd=1.00,0.15:aq-mode=3:deblock=-1,-1:chroma_qp_offset=0:b-adapt=2:rc-lookahead=50` |
| H.265 video | `libx265`, ABR 2-pass, `-b:v 2000k`, `-preset slow`, `yuv420p10le`, Main 10, automatic level |
| x265 overrides (both passes) | `ref=4`, `bframes=4`, `b-adapt=2`, `rc-lookahead=25`, `keyint=250`, `min-keyint=23`, `open-gop=1`, `rdoq-level=2`, `limit-modes=1`, `me=3`, `subme=3`, `rd=4`, `psy-rd=2.0`, `psy-rdoq=1.0`, `aq-mode=3`, `sao=0`; full options in [encoder.py](src/rarbgenc/encoder.py) |
| Audio (source 6 ch or more, e.g. 5.1 / 7.1) | AAC 224k, 6 channels |
| Audio (source 2 to 5 ch) | AAC 128k, 2 channels (downmix) |
| Audio (mono source) | AAC 96k, 1 channel |
| Audio sample rate | 48000 Hz for both video codecs |
| Metadata | `creation_time=now`, `title` / `comment` = source file name, `description` = your text, audio `language` = source (or chosen) code, video `language` cleared |

Only the first video stream and the selected audio track are written; subtitles are not included.
Pass log files go to a temporary folder and are deleted after encoding.
Resolution and frame rate follow the source; these profiles target SDR Blu-ray material,
not HDR tone mapping or automatic 1080p resizing.

### Sample verification

The profiles were checked against **Ready or Not (2019), H264.AAC-RARBG** and
**21 Grams (2003), x265-RARBG** using `ffprobe` stream information and the encoder
settings SEI in the first video packet. Depending on the muxer, SEI can also be in
stream extradata (`-show_streams -show_packets -show_data -read_intervals '%+#1'`).

- H.264: High 4.1, 8-bit, target 2500 kbps, actual approximately 2499 kbps.
  The existing core options matched; pixel format/profile/level are now explicit,
  and both passes explicitly request B-adapt 2 and lookahead 50.
  `chroma_qp_offset=0` becomes `-3` after x264's psy adjustment, matching the sample.
- H.265: Main 10, 10-bit, target 2000 kbps, actual approximately 1999 kbps.
  `rc=abr`, `stats-read=2`, `aq-mode=3`, and disabled SAO identify the reproduced profile.
- Both samples contain AAC-LC 5.1, 224 kbps, 48 kHz. Other channel-count presets are
  app policy, not inferred from these two samples.

SEI does not reveal the complete original command or preprocessing. The samples used
x264 core 152 and a RARBG-specific x265 3.3+4 build; current libraries cannot guarantee
bit-identical output. Thread counts and lookahead slicing can adjust to the input and
machine. AAC implementation identity cannot be recovered from these stream properties.

## Requirements

- Python 3.13 and [uv](https://docs.astral.sh/uv/)
- ffmpeg **with libx264 and/or 10-bit-capable libx265** for the selected codec (see below)

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

**One-click alternative:** if ffmpeg is not found (or lacks the selected encoder), click
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
3. Select **Output > Video**, then edit the description, output directory and suffix if needed.
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
real 2-pass encodes, including SEI settings checks; they are skipped when ffmpeg with
libx264 is not available, and x265 encoding cases also require libx265.
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
