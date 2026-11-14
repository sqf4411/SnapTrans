"""程序入口：托盘图标、全局快捷键、串联截图 → OCR → 翻译。"""

import os
import sys
import winreg
from pathlib import Path

from PySide6.QtCore import QLockFile, QRect, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import config, net, ocr
from .capture import SnipController
from .hotkey import HotkeyManager
from .result_window import ResultWindow
from .settings_dialog import SettingsDialog

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(0, 120, 215))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(Qt.white)
    p.setFont(QFont("Microsoft YaHei", 30, QFont.Bold))
    p.drawText(pm.rect(), Qt.AlignCenter, "译")
    p.end()
    return QIcon(pm)


def launch_command() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller 打包后
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent.parent / "run.pyw"
    return f'"{exe}" "{script}"'


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, config.APP_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, config.APP_NAME)
            except FileNotFoundError:
                pass


class App:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        self.cfg = config.load()
        self.icon = make_icon()
        self.snip = SnipController()
        self.snip.captured.connect(self._on_captured)
        self.window = ResultWindow(lambda: self.cfg)
        self.window.setWindowIcon(self.icon)
        self._snip_translate = True
        self.hotkeys = HotkeyManager()

        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("SnapTrans 截图翻译")
        self.menu = QMenu()
        self.act_translate = QAction("截图翻译", triggered=lambda: self.start_snip(True))
        self.act_ocr = QAction("截图识字（复制文字）", triggered=lambda: self.start_snip(False))
        self.act_clip = QAction("翻译剪贴板中的图片", triggered=self.translate_clipboard)
        for act in (self.act_translate, self.act_ocr, self.act_clip):
            self.menu.addAction(act)
        self.menu.addSeparator()
        self.menu.addAction(QAction("设置…", self.menu, triggered=self.open_settings))
        self.menu.addAction(QAction("退出", self.menu, triggered=self.quit))
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(
            lambda reason: reason == QSystemTrayIcon.Trigger and self.start_snip(True))
        self.tray.show()

        self.apply_config(first=True)
        # 预热：提前创建 OCR 引擎、建立到翻译服务的连接
        QTimer.singleShot(0, lambda: ocr.warmup_windows(self.cfg["ocr"]["windows_lang"]))
        self._warmup()

        if config.is_first_run():
            config.save(self.cfg)
            self.tray.showMessage("SnapTrans 已启动",
                                  f"按 {self.cfg['hotkey']} 截图翻译。请先在托盘图标 → 设置 中填写翻译 API Key。",
                                  self.icon, 8000)
            QTimer.singleShot(500, self.open_settings)

    # ------------------------------------------------------------ 配置

    def apply_config(self, first: bool = False) -> None:
        self.hotkeys.unregister_all()
        errors = []
        for name, key, cb in (("截图翻译", "hotkey", lambda: self.start_snip(True)),
                              ("截图识字", "hotkey_ocr", lambda: self.start_snip(False))):
            text = self.cfg.get(key, "")
            if not text:
                continue
            # 快捷键回调发生在原生事件过滤器内，推迟到事件循环中执行
            try:
                self.hotkeys.register(text, lambda cb=cb: QTimer.singleShot(0, cb))
            except (ValueError, OSError) as e:
                errors.append(f"{name}：{e}")
        self.act_translate.setText(f"截图翻译\t{self.cfg['hotkey']}")
        self.act_ocr.setText(f"截图识字（复制文字）\t{self.cfg['hotkey_ocr']}")
        if errors:
            self.tray.showMessage("快捷键注册失败", "\n".join(errors), QSystemTrayIcon.Warning, 8000)
        try:
            set_autostart(self.cfg["autostart"])
        except OSError as e:
            if not first:
                QMessageBox.warning(None, "SnapTrans", f"设置开机启动失败：{e}")

    def _warmup(self) -> None:
        if self.cfg["translate"].get("enabled", True):
            net.warmup(self.cfg["translate"]["base_url"], self.cfg["translate"]["api_key"])
        if self.cfg["ocr"]["engine"] == "api":
            net.warmup(self.cfg["ocr"]["base_url"], self.cfg["ocr"]["api_key"])

    def open_settings(self) -> None:
        if getattr(self, "_settings", None) and self._settings.isVisible():
            self._settings.raise_()
            self._settings.activateWindow()
            return
        self._settings = SettingsDialog(self.cfg)
        self._settings.setWindowIcon(self.icon)
        if self._settings.exec():
            self.cfg = self._settings.cfg
            config.save(self.cfg)
            ocr.warmup_windows(self.cfg["ocr"]["windows_lang"])
            self.apply_config()
            self._warmup()
        self._settings = None

    # ------------------------------------------------------------ 动作

    def start_snip(self, translate: bool) -> None:
        if self.snip.active:
            return
        self._snip_translate = translate
        self._warmup()  # 用户框选的同时，后台把网络连接建好
        self.snip.start()

    def _on_captured(self, image: QImage, rect: QRect) -> None:
        self.window.start_image(image, rect, translate=self._snip_translate)

    def translate_clipboard(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            self.tray.showMessage("SnapTrans", "剪贴板里没有图片", self.icon, 3000)
            return
        self._warmup()
        self.window.start_image(image, None, translate=True)

    def quit(self) -> None:
        self.hotkeys.unregister_all()
        self.tray.hide()
        self.qapp.quit()


def main() -> int:
    # 高 DPI 下截图按物理像素取，避免模糊
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName(config.APP_NAME)

    lock = QLockFile(str(Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation)) / "snaptrans.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "SnapTrans", "SnapTrans 已经在运行（见系统托盘）。")
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "SnapTrans", "系统托盘不可用。")
        return 1

    app = App(qapp)  # noqa: F841  保持引用
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
