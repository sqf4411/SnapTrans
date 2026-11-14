"""后台任务：OCR + 流式翻译，在独立线程中运行，通过 Qt 信号把结果送回界面。"""

import threading
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from . import ocr, translator


class Job(QObject):
    ocr_done = Signal(str, int)        # 识别文字, 耗时 ms
    target_chosen = Signal(str)        # 实际使用的目标语言
    chunk = Signal(str)                # 翻译片段
    finished = Signal(int)             # 翻译总耗时 ms（从开始翻译算）
    failed = Signal(str)

    def __init__(self, cfg: dict, image: QImage | None = None, text: str | None = None,
                 translate: bool = True, target: str | None = None):
        super().__init__()
        self.cfg = cfg
        self.image = image
        self.text = text
        self.translate = translate
        self.target = target
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            text = self.text
            if text is None:
                t0 = time.perf_counter()
                text = ocr.recognize(self.image, self.cfg["ocr"])
                if self.cancel_event.is_set():
                    return
                self.ocr_done.emit(text, round((time.perf_counter() - t0) * 1000))
            if not self.translate or not self.cfg["translate"].get("enabled", True):
                return
            if not text.strip():
                self.failed.emit("没有识别到文字")
                return
            target = self.target or translator.pick_target(text, self.cfg)
            self.target_chosen.emit(target)
            t0 = time.perf_counter()
            for piece in translator.translate_stream(text, target, self.cfg["translate"], self.cancel_event):
                if self.cancel_event.is_set():
                    return
                self.chunk.emit(piece)
            if not self.cancel_event.is_set():
                self.finished.emit(round((time.perf_counter() - t0) * 1000))
        except Exception as e:  # 把错误显示到界面上，而不是让线程静默崩溃
            if not self.cancel_event.is_set():
                self.failed.emit(str(e))
