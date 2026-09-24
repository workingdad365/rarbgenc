"""rarbg 와 동일한 설정으로 x264 mp4 를 만드는 데스크톱 인코더."""

__version__ = "1.0.0"


def main() -> int:
    # 코어 모듈 import 시 Qt 를 불러오지 않도록 지연 import
    from rarbgenc.gui import main as gui_main

    return gui_main()
