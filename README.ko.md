<p align="center"><img src="rarbg_logo.png" alt="RARBG" height="70"></p>

<h1 align="center">rarbgenc</h1>

[English](README.md) | **한국어**

RARBG 블루레이 샘플에서 확인한 2-pass 설정으로
1080p 원본 영상만 H.264 또는 H.265 MP4 파일로 인코딩하는 데스크톱 앱 (PySide6).

![](screenshot.webp)

## 기능

- 파일 열기 대화상자 또는 드래그앤드랍으로 원본 동영상 입력
- 1080p 원본만 허용하며 지원하지 않는 해상도는 안내 팝업 표시 후 인코딩 차단
- H.264 / x264 (8비트, 2500 kbps, 기본값) 또는 H.265 / x265 (10비트, 2000 kbps) 선택
- 인코딩할 오디오 트랙 선택 (`ffprobe`로 전체 트랙 목록 표시)
- 오디오 언어 태그는 원본 트랙 값을 그대로 사용. 원본에 언어 정보가 없을 때만
  직접 선택/입력 가능 (ISO 639-2, 기본값 `eng`)
- MP4 `description` 메타데이터에 들어갈 짧은 디스크립션 작성
- 출력 디렉토리 (비우면 원본과 같은 폴더), 파일명 접미사 (기본값 `_ENCODED`) 지정
- 비디오 코덱, 디스크립션 이력, 대체 언어, 출력 디렉토리, 접미사를 저장해 다음 실행 시 재사용
- pass 표시, 경과 시간, 남은 시간이 포함된 진행률 표시 및 언제든 취소 가능
- ffmpeg가 없으면 설치 방법을 안내하고, 버튼 한 번으로 앱 전용 정적 빌드 다운로드 가능

## 인코딩 설정

| | |
|---|---|
| H.264 비디오 (기본값) | `libx264`, 2-pass, `-b:v 2500k`, `yuv420p`, High, Level 4.1 |
| 1pass x264 파라미터 | `b-adapt=2:rc-lookahead=50` |
| 2pass x264 파라미터 | `me=umh:subme=9:me_range=24:ref=4:trellis=2:lookahead_threads=4:direct=auto:keyint_min=25:vbv_maxrate=31250:vbv_bufsize=31250:filler=0:psy_rd=1.00,0.15:aq-mode=3:deblock=-1,-1:chroma_qp_offset=0:b-adapt=2:rc-lookahead=50` |
| H.265 비디오 | `libx265`, ABR 2-pass, `-b:v 2000k`, `-preset slow`, `yuv420p10le`, Main 10, 레벨 자동 선택 |
| x265 주요 옵션 (두 pass 공통) | `ref=4`, `bframes=4`, `b-adapt=2`, `rc-lookahead=25`, `keyint=250`, `min-keyint=23`, `open-gop=1`, `rdoq-level=2`, `limit-modes=1`, `me=3`, `subme=3`, `rd=4`, `psy-rd=2.0`, `psy-rdoq=1.0`, `aq-mode=3`, `sao=0`; 전체 값은 [encoder.py](src/rarbgenc/encoder.py)에 정의됨 |
| 오디오 (원본 6채널 이상, 5.1 / 7.1 등) | AAC 224k, 6채널 |
| 오디오 (원본 2~5채널) | AAC 128k, 2채널 (다운믹스) |
| 오디오 (원본 모노) | AAC 96k, 1채널 |
| 오디오 샘플레이트 | 두 비디오 코덱 모두 48000 Hz |
| 메타데이터 | `creation_time=now`, `title` / `comment` = 원본 파일명, `description` = 입력한 문구, 오디오 `language` = 원본(또는 선택한) 코드, 비디오 `language` 비움 |

첫 번째 비디오 스트림과 선택한 오디오 트랙만 기록하며 자막은 포함하지 않습니다.
pass 로그 파일은 임시 폴더에 만들어지고 인코딩이 끝나면 삭제됩니다.
해상도와 프레임률은 원본을 따름. SDR 블루레이용 프로파일이며 HDR 톤 매핑이나
자동 1080p 리사이즈는 수행하지 않음.

### 지원 원본 해상도

x264와 x265 프로파일 모두 1080p 원본에만 적용함.
`ffprobe`가 보고한 첫 번째 비디오 스트림을 기준으로 검사하며 표지 이미지는 제외함.

- Full HD: `1920x1080` 허용.
- 여백을 제거한 1080p: 가로와 세로가 양의 짝수이고 `1920x1080` 이내이며,
  가로가 `1920`이거나 세로가 `1080`인 경우 허용.
  `1920x804`, `1920x1040`, `1440x1080` 등이 해당함.
- 720p·4K 등 그 외 크기, 해상도 미확인, 홀수 크기는 차단함.

지원하지 않는 파일을 열면 해당 해상도가 포함된 팝업을 표시하고 **Start encoding**을
비활성화함. 인코딩 시작 직전에도 다시 검사하며, 지원하는 파일을 다시 선택하면
인코딩이 활성화됨. 자동 크기 변환은 수행하지 않음.
현재 파일의 해상도에 따른 판정이므로 크롭이나 업스케일 이전의 원본 해상도까지 입증하지는 못함.

### 샘플 검증

**Ready or Not (2019), H264.AAC-RARBG**와 **21 Grams (2003), x265-RARBG**의
`ffprobe` 스트림 정보 및 첫 비디오 패킷에 포함된 인코더 설정 SEI를 분석함.
먹서에 따라 SEI가 스트림 extradata에 기록되기도 함
(`-show_streams -show_packets -show_data -read_intervals '%+#1'`).

- H.264: High 4.1, 8비트, 목표 2500 kbps, 실제 약 2499 kbps로 확인됨.
  기존 핵심 옵션은 일치하며 픽셀 형식·프로파일·레벨을 명시적으로 고정함.
  두 pass 모두 B-adapt 2와 lookahead 50을 명시함.
  `chroma_qp_offset=0`은 x264의 psy 보정 후 샘플과 동일한 `-3`으로 기록됨.
- H.265: Main 10, 10비트, 목표 2000 kbps, 실제 약 1999 kbps로 확인됨.
  `rc=abr`, `stats-read=2`, `aq-mode=3`, SAO 비활성화를 포함한 프로파일 재현.
- 두 샘플 모두 AAC-LC 5.1, 224 kbps, 48 kHz로 확인됨.
  다른 채널 수의 오디오 프리셋은 샘플에서 추론한 값이 아닌 앱 정책임.

SEI만으로 원래 명령 전체나 전처리 과정을 복원할 수는 없음.
샘플은 x264 core 152 및 RARBG 전용 x265 3.3+4 빌드로 제작되어 현재 라이브러리로
비트 단위 동일 출력은 보장할 수 없음. 스레드 수와 lookahead 분할은 입력과 시스템에
따라 자동 조정될 수 있음. AAC 구현체 역시 해당 스트림 속성만으로 특정할 수 없음.

## 요구 사항

- Python 3.13, [uv](https://docs.astral.sh/uv/)
- 선택할 코덱에 맞게 **libx264 또는 10비트를 지원하는 libx265가 포함된** ffmpeg (아래 참고)

## 설치

```bash
git clone <this repository> rarbgenc
cd rarbgenc
uv tool install --editable .
```

`rarbgenc` 실행 파일이 설치됩니다 (예: `~/.local/bin/rarbgenc`). editable 설치이므로
소스를 수정하면 바로 반영됩니다.

제거: `uv tool uninstall rarbgenc`

## ffmpeg 설치

rarbgenc는 `PATH`에 있는 `ffmpeg` / `ffprobe`를 우선 사용합니다.

| OS | 명령 |
|---|---|
| Windows | `winget install --id Gyan.FFmpeg -e` (또는 `scoop install ffmpeg`, `choco install ffmpeg`) |
| macOS | `brew install ffmpeg` |
| Debian / Ubuntu | `sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` (RPM Fusion) |
| Arch | `sudo pacman -S ffmpeg` |

설치 후에는 바뀐 `PATH`가 적용되도록 rarbgenc를 다시 실행하세요.

**원클릭 설치:** ffmpeg가 없거나 선택한 인코더 없이 빌드된 경우, 앱 상단의
**Download ffmpeg automatically** 버튼을 누르면 정적 빌드(약 100 MB,
[static-ffmpeg](https://github.com/zackees/static_ffmpeg) 사용)를 앱 데이터 폴더에 내려받아
rarbgenc 전용으로 사용합니다.

- Windows: `%LOCALAPPDATA%\rarbgenc\ffmpeg`
- macOS: `~/Library/Application Support/rarbgenc/ffmpeg`
- Linux: `~/.local/share/rarbgenc/ffmpeg`

## 사용법

```bash
rarbgenc                 # 창 열기
rarbgenc "movie.mkv"     # 파일을 미리 불러온 상태로 창 열기
```

1. 지원하는 1080p 동영상을 창에 드래그앤드랍 (또는 **Open...** 클릭)
2. 오디오 트랙 선택. 원본 트랙에 언어 정보가 없을 때만 언어 지정
3. **Output > Video**에서 코덱 선택 후 필요하면 디스크립션, 출력 디렉토리, 접미사 수정
4. **Start encoding** 클릭

출력 파일: `<출력 디렉토리>/<원본 파일명><접미사>.mp4`

## 설정 파일

- Windows: `%APPDATA%\rarbgenc\settings.json`
- macOS: `~/Library/Application Support/rarbgenc/settings.json`
- Linux: `~/.config/rarbgenc/settings.json`

## 개발

```bash
uv sync
uv run rarbgenc
uv run pytest
```

통합 테스트와 GUI 테스트는 ffmpeg의 `lavfi` 소스로 짧은 샘플 영상을 만들어 실제 2-pass
인코딩과 SEI 설정 검사를 수행함. libx264가 포함된 ffmpeg가 없으면 건너뛰며,
x265 인코딩 테스트에는 libx265도 필요함.
GUI 테스트는 `QT_QPA_PLATFORM=offscreen`으로 실행됩니다.

## 프로젝트 구조

```
src/rarbgenc/
  encoder.py       2-pass 명령 생성, 오디오 프로파일, 진행률 파싱
  probe.py         ffprobe 래퍼
  languages.py     ISO 639-2 코드 및 정규화
  settings.py      사용자 설정 저장
  ffmpeg_tools.py  ffmpeg 탐색, 자동 다운로드, 설치 안내
  gui.py           PySide6 메인 창, QProcess 실행기
tests/
```
