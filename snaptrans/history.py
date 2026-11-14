"""截图历史：图片保存为 PNG，记录保存在 index.json。"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .config import CONFIG_DIR

DEFAULT_DIR = CONFIG_DIR / "history"


class History(QObject):
    changed = Signal()
    added = Signal(str)    # 新增记录
    updated = Signal(str)  # 某条记录内容更新

    def __init__(self, directory: Path = DEFAULT_DIR, limit: int = 200):
        super().__init__()
        self.dir = Path(directory)
        self.limit = limit
        self._lock = threading.Lock()
        self._records: list[dict] = []
        self._load()

    # ------------------------------------------------------------ 读写

    @property
    def _index(self) -> Path:
        return self.dir / "index.json"

    def _load(self) -> None:
        try:
            with open(self._index, encoding="utf-8") as f:
                self._records = [r for r in json.load(f) if isinstance(r, dict) and "id" in r]
        except (OSError, ValueError):
            self._records = []

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._index.with_suffix(".tmp")
        with self._lock:
            data = list(self._records)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._index)

    def image_path(self, rid: str) -> Path:
        return self.dir / f"{rid}.png"

    # ------------------------------------------------------------ 操作

    def records(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._records))  # 最新的在前

    def get(self, rid: str) -> dict | None:
        with self._lock:
            return next((r for r in self._records if r["id"] == rid), None)

    def add_image(self, image: QImage, mode: str) -> str:
        rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        record = {"id": rid, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
                  "width": image.width(), "height": image.height(),
                  "source": "", "translation": "", "target": ""}
        with self._lock:
            self._records.append(record)
        self.dir.mkdir(parents=True, exist_ok=True)
        image = image.copy()
        path = self.image_path(rid)

        # PNG 编码放后台，不拖慢识别
        def write():
            image.save(str(path), "PNG")
            self.updated.emit(rid)

        threading.Thread(target=write, daemon=True).start()
        self._trim()
        self._save()
        self.changed.emit()
        self.added.emit(rid)
        return rid

    def add_text(self, text: str) -> str:
        rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        with self._lock:
            self._records.append({"id": rid, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "text",
                                  "source": text, "translation": "", "target": ""})
        self._trim()
        self._save()
        self.changed.emit()
        self.added.emit(rid)
        return rid

    def update(self, rid: str, fields: dict) -> None:
        with self._lock:
            rec = next((r for r in self._records if r["id"] == rid), None)
            if rec is None:
                return
            rec.update(fields)
        self._save()
        self.updated.emit(rid)

    def delete(self, rids: list[str]) -> None:
        ids = set(rids)
        with self._lock:
            self._records = [r for r in self._records if r["id"] not in ids]
        for rid in ids:
            self.image_path(rid).unlink(missing_ok=True)
        self._save()
        self.changed.emit()

    def clear(self) -> None:
        with self._lock:
            self._records = []
        if self.dir.exists():
            for p in self.dir.glob("*.png"):
                p.unlink(missing_ok=True)
        self._save()
        self.changed.emit()

    def set_limit(self, limit: int) -> None:
        self.limit = limit
        if self._trim():
            self._save()
            self.changed.emit()

    def _trim(self) -> bool:
        with self._lock:
            extra = len(self._records) - self.limit
            if extra <= 0:
                return False
            removed, self._records = self._records[:extra], self._records[extra:]
        for r in removed:
            self.image_path(r["id"]).unlink(missing_ok=True)
        return True

    def disk_usage(self) -> int:
        if not self.dir.exists():
            return 0
        return sum(p.stat().st_size for p in self.dir.iterdir() if p.is_file())
