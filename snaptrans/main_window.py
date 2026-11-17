"""主窗口：工具栏 + 截图记录（列表 / 预览 / 文字）+ 设置。"""

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QFileDialog, QFrame, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from . import clipboard
from .history import History
from .settings_panel import SettingsPanel

THUMB = QSize(72, 48)


class _ImageView(QFrame):
    """截图预览：在固定区域内等比缩放显示（只缩小不放大），区域大小不随图片变化。双击用系统看图软件打开。"""
    double_clicked = Signal()

    def __init__(self):
        super().__init__()
        self._pixmap = QPixmap()
        self._scaled = QPixmap()
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setMinimumSize(120, 120)
        self.setToolTip("双击用系统看图软件打开")

    def sizeHint(self):
        return QSize(400, 260)

    def set_pixmap(self, pm: QPixmap) -> None:
        self._pixmap = pm
        self._scaled = QPixmap()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._scaled = QPixmap()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        area = self.contentsRect().adjusted(6, 6, -6, -6)
        if self._pixmap.isNull():
            p.setPen(QColor("gray"))
            p.drawText(area, Qt.AlignCenter, "没有图片")
            return
        dpr = self.devicePixelRatioF()
        if self._scaled.isNull():
            pm = self._pixmap
            avail = area.size() * dpr
            if pm.width() > avail.width() or pm.height() > avail.height():
                pm = pm.scaled(avail, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self._scaled = pm
        size = self._scaled.deviceIndependentSize().toSize()
        x = area.left() + (area.width() - size.width()) // 2
        y = area.top() + (area.height() - size.height()) // 2
        p.drawPixmap(x, y, self._scaled)

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit()


class MainWindow(QMainWindow):
    snip_capture = Signal()
    snip_translate = Signal()
    pin_record = Signal(str)         # 把记录中的截图贴到屏幕
    snip_ocr = Signal()
    clipboard_translate = Signal()
    reprocess = Signal(str)          # 重新识别并翻译某条记录
    retranslate_text = Signal(str)   # 用编辑后的原文重新翻译某条记录
    open_path = Signal(str)          # 用系统程序打开文件/文件夹
    hidden_to_tray = Signal()        # 第一次关闭到托盘
    quit_requested = Signal()

    def __init__(self, history: History, cfg: dict, icon: QIcon):
        super().__init__()
        self.history = history
        self.cfg = cfg
        self.setWindowTitle("SnapTrans 截图翻译")
        self.setWindowIcon(icon)
        self.resize(900, 600)
        self._current: str | None = None
        self._shown_image: str | None = None
        self._thumbs: dict[str, QIcon] = {}

        # ---------------- 工具栏
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.act_capture = QAction("✂ 截图", self, triggered=self.snip_capture.emit)
        self.act_translate = QAction("🌐 截图翻译", self, triggered=self.snip_translate.emit)
        self.act_ocr = QAction("🔤 截图识字", self, triggered=self.snip_ocr.emit)
        self.act_clip = QAction("📋 翻译剪贴板图片", self, triggered=self.clipboard_translate.emit)
        for a in (self.act_capture, self.act_translate, self.act_ocr, self.act_clip):
            tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.hotkey_label = QLabel()
        self.hotkey_label.setStyleSheet("color: gray; padding-right: 8px;")
        tb.addWidget(self.hotkey_label)
        self.addToolBar(tb)

        # ---------------- 标签页
        self.tabs = QTabWidget()
        self.tabs.addTab(self._history_page(), "截图记录")
        self.settings = SettingsPanel(cfg)
        settings_page = QWidget()
        sl = QVBoxLayout(settings_page)
        sl.addWidget(self.settings)
        self.tabs.addTab(settings_page, "设置")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self._refresh_usage())
        self.setCentralWidget(self.tabs)
        self.statusBar()

        history.changed.connect(self.reload)
        history.added.connect(self._select_record)
        history.updated.connect(self._on_record_updated)
        self.update_hotkey_labels(cfg)
        self.reload()

    # ------------------------------------------------------------ 布局

    def _history_page(self) -> QWidget:
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索原文 / 译文…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload)

        self.list = QListWidget()
        self.list.setIconSize(THUMB)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.currentItemChanged.connect(lambda cur, _: self._show(cur.data(Qt.UserRole) if cur else None))
        self.list.itemDoubleClicked.connect(lambda it: self._open_image(it.data(Qt.UserRole)))
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: gray;")

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.search)
        ll.addWidget(self.list, 1)
        ll.addWidget(self.count_label)

        # 右侧详情
        self.info = QLabel()
        self.info.setStyleSheet("color: gray;")
        self.info.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # 长文本不撑宽窗口
        self.image_view = _ImageView()
        self.image_view.double_clicked.connect(lambda: self._open_image(self._current))
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("识别的原文（可编辑后点“重新翻译”）")
        self.translation = QPlainTextEdit()
        self.translation.setReadOnly(True)
        self.translation.setPlaceholderText("译文")

        def btn(text, slot, tip=""):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.btn_copy_img = btn("复制图片", self._copy_image)
        self.btn_pin = btn("📌 贴图", lambda: self._current and self.pin_record.emit(self._current),
                           "钉在屏幕上；在贴图上按 , / . 可翻看其他历史截图")
        self.btn_reprocess = btn("重新识别", lambda: self._current and self.reprocess.emit(self._current),
                                 "重新 OCR 并翻译（结果在浮窗显示，同时更新本记录）")
        actions = QHBoxLayout()
        actions.addWidget(self.info, 1)
        actions.addWidget(self.btn_pin)
        actions.addWidget(self.btn_copy_img)
        actions.addWidget(btn("另存为…", self._save_image_as))
        actions.addWidget(self.btn_reprocess)
        actions.addWidget(btn("删除", self._delete_selected))

        src_bar = QHBoxLayout()
        src_bar.addWidget(QLabel("原文"))
        src_bar.addStretch()
        src_bar.addWidget(btn("复制原文", lambda: self._copy(self.source.toPlainText())))
        src_bar.addWidget(btn("重新翻译", self._retranslate))
        dst_bar = QHBoxLayout()
        dst_bar.addWidget(QLabel("译文"))
        dst_bar.addStretch()
        dst_bar.addWidget(btn("复制译文", lambda: self._copy(self.translation.toPlainText())))

        text_box = QWidget()
        tl = QVBoxLayout(text_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addLayout(src_bar)
        tl.addWidget(self.source, 1)
        tl.addLayout(dst_bar)
        tl.addWidget(self.translation, 1)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(self.image_view)
        vsplit.addWidget(text_box)
        vsplit.setSizes([260, 300])

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addLayout(actions)
        rl.addWidget(vsplit, 1)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([280, 620])

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.addWidget(split)
        self.list.addAction(QAction(self.list, shortcut=QKeySequence.Delete, triggered=self._delete_selected,
                                    shortcutContext=Qt.WidgetShortcut))
        return page

    # ------------------------------------------------------------ 数据

    def update_hotkey_labels(self, cfg: dict) -> None:
        self.cfg = cfg
        hk, hk_ocr = cfg.get("hotkey") or "无", cfg.get("hotkey_ocr") or "无"
        hk_cap = cfg.get("hotkey_capture") or "无"
        self.act_capture.setToolTip(f"截图后可标注、贴图、保存。快捷键 {hk_cap}")
        self.act_translate.setToolTip(f"快捷键 {hk}")
        self.act_ocr.setToolTip(f"快捷键 {hk_ocr}")
        self.hotkey_label.setText(f"截图 {hk_cap}　截图翻译 {hk}　截图识字 {hk_ocr}")

    def reload(self) -> None:
        keyword = self.search.text().strip().lower()
        records = self.history.records()
        if keyword:
            records = [r for r in records
                       if keyword in r.get("source", "").lower() or keyword in r.get("translation", "").lower()]
        current = self._current
        self.list.blockSignals(True)
        self.list.clear()
        select = None
        for r in records:
            item = QListWidgetItem(self._item_text(r))
            item.setData(Qt.UserRole, r["id"])
            item.setIcon(self._thumb(r["id"]))
            item.setSizeHint(QSize(0, THUMB.height() + 10))
            self.list.addItem(item)
            if r["id"] == current:
                select = item
        self.list.blockSignals(False)
        total = len(self.history.records())
        self.count_label.setText(f"共 {total} 条" + (f"，匹配 {len(records)} 条" if keyword else ""))
        if select is None and self.list.count():
            select = self.list.item(0)
        if select:
            self.list.setCurrentItem(select)
        else:
            self._show(None)

    @staticmethod
    def _item_text(r: dict) -> str:
        mode = {"capture": "截图", "translate": "翻译", "ocr": "识字", "clipboard": "剪贴板", "text": "文本"}.get(r.get("mode"), "")
        first = (r.get("source") or "（未识别）").strip().splitlines()[0][:40] if r.get("source") else "（识别中/无文字）"
        return f"{r['time'][5:16]}  {mode}\n{first}"

    def _thumb(self, rid: str) -> QIcon:
        if rid in self._thumbs:
            return self._thumbs[rid]
        img = self.history.thumbnail(rid)
        if img.isNull():
            return QIcon()
        # 画到统一尺寸的透明画布中央，避免不同比例的缩略图让文字错位
        canvas = QPixmap(THUMB * 2)
        canvas.fill(Qt.transparent)
        scaled = img.scaled(THUMB * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p = QPainter(canvas)
        p.drawImage((canvas.width() - scaled.width()) // 2, (canvas.height() - scaled.height()) // 2, scaled)
        p.end()
        icon = QIcon(canvas)
        self._thumbs[rid] = icon
        if len(self._thumbs) > 3000:
            self._thumbs.clear()
        return icon

    def _select_record(self, rid: str) -> None:
        """新截图后自动选中最新一条（搜索过滤中则先清空搜索）。"""
        if self.search.text():
            self.search.clear()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == rid:
                self.list.clearSelection()
                self.list.setCurrentRow(i)
                return

    def _on_record_updated(self, rid: str) -> None:
        self._thumbs.pop(rid, None)  # 图片可能刚写入数据库，重新生成缩略图
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.UserRole) == rid:
                r = self.history.get(rid)
                if r:
                    item.setText(self._item_text(r))
                    item.setIcon(self._thumb(rid))
                break
        if rid == self._current:
            self._show(rid)

    def _show(self, rid: str | None) -> None:
        self._current = rid
        r = self.history.get(rid) if rid else None
        has = r is not None
        for w in (self.btn_copy_img, self.btn_reprocess, self.btn_pin):
            w.setEnabled(has and self.history.has_image(rid))
        if not has:
            self._shown_image = None
            self.info.setText("")
            self.image_view.set_pixmap(QPixmap())
            self.source.setPlainText("")
            self.translation.setPlainText("")
            return
        size = f"{r['width']}×{r['height']}  " if r.get("width") else ""
        timing = []
        if r.get("ocr_ms") is not None:
            timing.append(f"识别 {r['ocr_ms']}ms")
        if r.get("tr_ms") is not None:
            timing.append(f"翻译 {r['tr_ms']}ms")
        target = f"→ {r['target']}  " if r.get("target") else ""
        self.info.setText(f"{r['time']}  {size}{target}{' · '.join(timing)}")
        self.info.setToolTip(self.info.text())
        if rid != self._shown_image:  # 文字更新时不重复解码图片
            self._shown_image = rid
            self.image_view.set_pixmap(QPixmap.fromImage(self.history.load_image(rid)))
        elif self.image_view._pixmap.isNull() and self.history.has_image(rid):
            self.image_view.set_pixmap(QPixmap.fromImage(self.history.load_image(rid)))
        if self.source.toPlainText() != r.get("source", ""):
            self.source.setPlainText(r.get("source", ""))
        self.translation.setPlainText(r.get("translation", ""))

    def _refresh_usage(self) -> None:
        self.settings.set_history_usage(len(self.history.records()), self.history.disk_usage())

    # ------------------------------------------------------------ 操作

    def _selected_ids(self) -> list[str]:
        return [it.data(Qt.UserRole) for it in self.list.selectedItems()]

    def _copy(self, text: str) -> None:
        if text:
            clipboard.set_text(text)
            self.statusBar().showMessage("已复制", 2000)

    def _copy_image(self) -> None:
        if self._current:
            img = self.history.load_image(self._current)
            if not img.isNull():
                clipboard.set_image(img)
                self.statusBar().showMessage("图片已复制", 2000)

    def _save_image_as(self) -> None:
        if not self._current:
            return
        img = self.history.load_image(self._current)
        if img.isNull():
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存为", f"{self._current}.png",
                                              "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;WebP 图片 (*.webp)")
        if path:
            img.save(path, None, 100 if path.lower().endswith(".webp") else -1)
            self.statusBar().showMessage(f"已保存到 {path}", 3000)

    def _open_image(self, rid: str | None) -> None:
        path = self.history.export_temp(rid) if rid else None
        if path:
            self.open_path.emit(str(path))

    def _retranslate(self) -> None:
        text = self.source.toPlainText().strip()
        if self._current and text:
            self.history.update(self._current, {"source": text})
            self.retranslate_text.emit(self._current)

    def _delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if len(ids) > 1 and QMessageBox.question(self, "SnapTrans", f"删除选中的 {len(ids)} 条记录？") != QMessageBox.Yes:
            return
        self.history.delete(ids)
        self.statusBar().showMessage(f"已删除 {len(ids)} 条", 2000)

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("打开图片", lambda: self._open_image(item.data(Qt.UserRole)))
        menu.addAction("贴到屏幕", lambda: self.pin_record.emit(item.data(Qt.UserRole)))
        menu.addAction("复制图片", self._copy_image)
        menu.addAction("重新识别并翻译", lambda: self.reprocess.emit(item.data(Qt.UserRole)))
        menu.addSeparator()
        menu.addAction("删除", self._delete_selected)
        menu.exec(self.list.mapToGlobal(pos))

    def show_settings(self) -> None:
        self.tabs.setCurrentIndex(1)
        self.bring_to_front()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------ 关闭

    def closeEvent(self, e):
        if self.cfg.get("close_to_tray", True):
            e.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                QTimer.singleShot(0, self.hidden_to_tray.emit)
        else:
            e.accept()
            self.quit_requested.emit()
