"""截图历史：保存在本地 SQLite 小型数据库 history.db 中。

- 截图以无损 WebP 压缩后存入数据库（同样清晰，体积约为 PNG 的 1/3），另存一张小缩略图供列表快速显示；
- 识别原文、译文、耗时等信息以 JSON 保存在 records 表；
- 旧版本的 history/ 文件夹（index.json + PNG）会在后台自动导入数据库，导入成功后删除旧文件。
"""

import json
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QImage, QImageWriter

from .config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "history.db"
LEGACY_DIR = CONFIG_DIR / "history"
THUMB_SIZE = (160, 108)

_WEBP = b"webp" in [bytes(f) for f in QImageWriter.supportedImageFormats()]


def _encode(image: QImage, fmt: str, quality: int = -1) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    ok = image.save(buf, fmt, quality)
    return bytes(data) if ok else b""


def compress(image: QImage) -> tuple[str, bytes, bytes]:
    """返回 (格式, 原图数据, 缩略图数据)。原图无损。"""
    image = image.convertToFormat(QImage.Format_RGB32 if not image.hasAlphaChannel() else QImage.Format_ARGB32)
    data, fmt = b"", "webp"
    if _WEBP and max(image.width(), image.height()) <= 16383:
        data = _encode(image, "WEBP", 100)  # quality=100 即无损 WebP
    if not data:
        fmt, data = "png", _encode(image, "PNG")
    thumb = image.scaled(*THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return fmt, data, _encode(thumb.convertToFormat(QImage.Format_RGB32), "JPG", 85)


class History(QObject):
    changed = Signal()
    added = Signal(str)    # 新增记录
    updated = Signal(str)  # 某条记录内容更新（含图片写入完成）

    def __init__(self, path: Path = DB_PATH, limit: int = 200):
        super().__init__()
        self.path = Path(path)
        self.dir = self.path.parent   # “打开记录文件夹”
        self.limit = limit
        self._lock = threading.RLock()
        self._records: list[dict] = []
        self._has_img: set[str] = set()
        self._pending: dict[str, QImage] = {}   # 正在后台压缩写入的图片
        self._legacy: dict[str, Path] = {}      # 尚未导入的旧版 PNG
        self.dir.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._init_db()
        self._load()
        self._import_legacy()

    # ------------------------------------------------------------ 数据库

    def _init_db(self) -> None:
        db = self._db
        db.execute("PRAGMA auto_vacuum=INCREMENTAL")  # 仅对新建的数据库生效：删除后可回收空间
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("CREATE TABLE IF NOT EXISTS records ("
                   "seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS images ("
                   "id TEXT PRIMARY KEY, fmt TEXT NOT NULL, data BLOB NOT NULL, thumb BLOB)")

    def _load(self) -> None:
        with self._lock:
            rows = self._db.execute("SELECT data FROM records ORDER BY seq").fetchall()
            self._records = []
            for (data,) in rows:
                try:
                    r = json.loads(data)
                except ValueError:
                    continue
                if isinstance(r, dict) and "id" in r:
                    self._records.append(r)
            self._has_img = {rid for (rid,) in self._db.execute("SELECT id FROM images")}

    def _insert_record(self, record: dict) -> None:
        self._db.execute("INSERT OR IGNORE INTO records (id, data) VALUES (?, ?)",
                         (record["id"], json.dumps(record, ensure_ascii=False)))

    def _write_image(self, rid: str, image: QImage) -> bool:
        """压缩并写入（在后台线程调用）。记录已被删除时放弃。"""
        fmt, data, thumb = compress(image)
        if not data:
            return False
        with self._lock:
            if self.get(rid) is None:
                return False
            self._db.execute("INSERT OR REPLACE INTO images (id, fmt, data, thumb) VALUES (?, ?, ?, ?)",
                             (rid, fmt, sqlite3.Binary(data), sqlite3.Binary(thumb)))
            self._has_img.add(rid)
        return True

    def _vacuum(self) -> None:
        try:
            with self._lock:
                self._db.execute("PRAGMA incremental_vacuum").fetchall()
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------ 旧版数据导入

    def _import_legacy(self) -> None:
        index = LEGACY_DIR / "index.json"
        if not LEGACY_DIR.is_dir():
            return
        try:
            with open(index, encoding="utf-8") as f:
                old = [r for r in json.load(f) if isinstance(r, dict) and "id" in r]
        except (OSError, ValueError):
            old = []
        with self._lock:
            known = {r["id"] for r in self._records}
            new = [r for r in old if r["id"] not in known]
            if new:
                self._db.execute("BEGIN")
                for r in new:
                    self._insert_record(r)
                self._db.execute("COMMIT")
        if new:
            self._load()
        if index.exists():
            index.replace(index.with_suffix(".imported.json"))
        # 以文件为准（即使上次导入到一半退出，剩下的 PNG 下次也会继续导入）
        with self._lock:
            known = {r["id"] for r in self._records}
            for p in LEGACY_DIR.glob("*.png"):
                if p.stem in known and p.stem not in self._has_img:
                    self._legacy[p.stem] = p
                else:
                    p.unlink(missing_ok=True)  # 已导入或已删除的记录
        if not self._legacy:
            shutil.rmtree(LEGACY_DIR, ignore_errors=True)
            return

        def run():
            for rid, p in list(self._legacy.items()):
                img = QImage(str(p))
                ok = not img.isNull() and self._write_image(rid, img)
                with self._lock:
                    self._legacy.pop(rid, None)
                if ok or self.get(rid) is None:
                    p.unlink(missing_ok=True)
                if ok:
                    self.updated.emit(rid)
            # 全部导入后删除旧文件夹
            if not any(LEGACY_DIR.glob("*.png")):
                shutil.rmtree(LEGACY_DIR, ignore_errors=True)
            self._vacuum()

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ 查询

    def records(self) -> list[dict]:
        with self._lock:
            return list(reversed(self._records))  # 最新的在前

    def get(self, rid: str) -> dict | None:
        with self._lock:
            return next((r for r in self._records if r["id"] == rid), None)

    def has_image(self, rid: str | None) -> bool:
        if not rid:
            return False
        with self._lock:
            return rid in self._has_img or rid in self._pending or rid in self._legacy

    def load_image(self, rid: str | None) -> QImage:
        """读取原图；没有时返回空 QImage。"""
        if not rid:
            return QImage()
        with self._lock:
            if rid in self._pending:
                return self._pending[rid].copy()
            legacy = self._legacy.get(rid)
            row = None if legacy else self._db.execute("SELECT data FROM images WHERE id=?", (rid,)).fetchone()
        if legacy:
            return QImage(str(legacy))
        return QImage.fromData(bytes(row[0])) if row else QImage()

    def thumbnail(self, rid: str) -> QImage:
        with self._lock:
            pending = self._pending.get(rid)
            legacy = self._legacy.get(rid)
            row = None
            if pending is None and legacy is None:
                row = self._db.execute("SELECT thumb FROM images WHERE id=?", (rid,)).fetchone()
        if row and row[0]:
            return QImage.fromData(bytes(row[0]))
        img = pending if pending is not None else (QImage(str(legacy)) if legacy else QImage())
        return img.scaled(*THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not img.isNull() else img

    def export_temp(self, rid: str) -> Path | None:
        """导出为临时 PNG（用系统看图软件打开时使用）。"""
        img = self.load_image(rid)
        if img.isNull():
            return None
        folder = Path(tempfile.gettempdir()) / "SnapTrans"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{rid}.png"
        return path if path.exists() or img.save(str(path), "PNG") else None

    # ------------------------------------------------------------ 修改

    @staticmethod
    def _new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]

    def add_image(self, image: QImage, mode: str) -> str:
        rid = self._new_id()
        record = {"id": rid, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
                  "width": image.width(), "height": image.height(),
                  "source": "", "translation": "", "target": ""}
        image = image.copy()
        with self._lock:
            self._records.append(record)
            self._pending[rid] = image
            self._insert_record(record)

        # 压缩编码放后台，不拖慢识别和复制
        def write():
            try:
                self._write_image(rid, image)
            finally:
                with self._lock:
                    self._pending.pop(rid, None)
            self.updated.emit(rid)

        threading.Thread(target=write, daemon=True).start()
        self._trim()
        self.changed.emit()
        self.added.emit(rid)
        return rid

    def add_text(self, text: str) -> str:
        rid = self._new_id()
        record = {"id": rid, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "mode": "text",
                  "source": text, "translation": "", "target": ""}
        with self._lock:
            self._records.append(record)
            self._insert_record(record)
        self._trim()
        self.changed.emit()
        self.added.emit(rid)
        return rid

    def update(self, rid: str, fields: dict) -> None:
        with self._lock:
            rec = next((r for r in self._records if r["id"] == rid), None)
            if rec is None:
                return
            rec.update(fields)
            self._db.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(rec, ensure_ascii=False), rid))
        self.updated.emit(rid)

    def _remove(self, ids: set[str]) -> None:
        with self._lock:
            self._records = [r for r in self._records if r["id"] not in ids]
            self._db.execute("BEGIN")
            for rid in ids:
                self._db.execute("DELETE FROM records WHERE id=?", (rid,))
                self._db.execute("DELETE FROM images WHERE id=?", (rid,))
            self._db.execute("COMMIT")
            self._has_img -= ids
            for rid in ids:
                p = self._legacy.pop(rid, None)
                if p:
                    p.unlink(missing_ok=True)

    def delete(self, rids: list[str]) -> None:
        self._remove(set(rids))
        threading.Thread(target=self._vacuum, daemon=True).start()
        self.changed.emit()

    def clear(self) -> None:
        with self._lock:
            ids = {r["id"] for r in self._records}
        self._remove(ids)
        with self._lock:
            self._db.execute("DELETE FROM images")
            self._db.execute("DELETE FROM records")
        try:
            with self._lock:
                self._db.execute("VACUUM")
        except sqlite3.Error:
            pass
        self.changed.emit()

    def set_limit(self, limit: int) -> None:
        self.limit = limit
        if self._trim():
            self.changed.emit()

    def _trim(self) -> bool:
        with self._lock:
            extra = len(self._records) - self.limit
            if extra <= 0:
                return False
            ids = {r["id"] for r in self._records[:extra]}
        self._remove(ids)
        threading.Thread(target=self._vacuum, daemon=True).start()
        return True

    # ------------------------------------------------------------ 统计 / 关闭

    def disk_usage(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        if LEGACY_DIR.is_dir():
            total += sum(p.stat().st_size for p in LEGACY_DIR.iterdir() if p.is_file())
        return total

    def close(self) -> None:
        try:
            with self._lock:
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._db.close()
        except sqlite3.Error:
            pass
