"""程序入口：主窗口、托盘、全局快捷键，串联截图 → OCR → 翻译 → 记录。"""

import getpass
import sys
import winreg
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import config, net, ocr
from .capture import SnipController
from .history import History
from .hotkey import HotkeyManager
from .main_window import MainWindow
from .result_window import ResultWindow

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SERVER_NAME = f"SnapTrans-{getpass.getuser()}"


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
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent.parent / "run.pyw"
    return f'"{exe}" "{script}" --minimized'


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
    def __init__(self, qapp: QApplication, minimized: bool = False):
        self.qapp = qapp
        first_run = config.is_first_run()
        self.cfg = config.load()
        self.icon = make_icon()
        qapp.setWindowIcon(self.icon)

        self.history = History(limit=int(self.cfg["history_limit"]))
        self.snip = SnipController()
        self.snip.captured.connect(self._on_captured)
        self.snip.canceled.connect(self._restore_main)
        self._snip_mode = "translate"
        self._main_was_visible = False

        self.result = ResultWindow(lambda: self.cfg)
        self.result.setWindowIcon(self.icon)
        self.result.result.connect(self.history.update)

        self.main = MainWindow(self.history, self.cfg, self.icon)
        self.main.snip_translate.connect(lambda: self.start_snip("translate"))
        self.main.snip_ocr.connect(lambda: self.start_snip("ocr"))
        self.main.clipboard_translate.connect(self.translate_clipboard)
        self.main.reprocess.connect(self.reprocess)
        self.main.retranslate_text.connect(self.retranslate_record)
        self.main.open_path.connect(lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p)))
        self.main.hidden_to_tray.connect(lambda: self.tray.showMessage(
            "SnapTrans 仍在后台运行", "快捷键依然可用。双击托盘图标打开主窗口，右键可退出。", self.icon, 4000))
        self.main.quit_requested.connect(self.quit)
        self.main.settings.saved.connect(self.on_settings_saved)
        self.main.settings.saved.connect(lambda _: self.main.statusBar().clearMessage())
        self.main.settings.open_history_dir.connect(self.open_history_dir)
        self.main.settings.clear_history.connect(self.history.clear)

        self.hotkeys = HotkeyManager()
        self._build_tray()
        self.apply_config(first=True)

        # 预热：提前创建 OCR 引擎、建立到翻译服务的连接
        QTimer.singleShot(0, lambda: ocr.warmup_windows(self.cfg["ocr"]["windows_lang"]))
        self._warmup()

        if first_run:
            config.save(self.cfg)
            self.main.show_settings()
            self.main.settings.tabs.setCurrentIndex(2)  # 翻译页，先填 API Key
            self.main.statusBar().showMessage("欢迎使用！请先填写翻译服务的 API Key，然后点击“保存设置”。")
        elif not minimized:
            self.main.bring_to_front()

    # ------------------------------------------------------------ 托盘

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("SnapTrans 截图翻译")
        menu = QMenu()
        menu.addAction(QAction("打开主窗口", menu, triggered=self.main.bring_to_front))
        menu.addSeparator()
        self.act_translate = QAction("截图翻译", menu, triggered=lambda: self.start_snip("translate"))
        self.act_ocr = QAction("截图识字（复制文字）", menu, triggered=lambda: self.start_snip("ocr"))
        menu.addAction(self.act_translate)
        menu.addAction(self.act_ocr)
        menu.addAction(QAction("翻译剪贴板图片", menu, triggered=self.translate_clipboard))
        menu.addSeparator()
        menu.addAction(QAction("设置…", menu, triggered=self.main.show_settings))
        menu.addAction(QAction("退出", menu, triggered=self.quit))
        self._tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
            and self.main.bring_to_front())
        self.tray.show()

    # ------------------------------------------------------------ 配置

    def apply_config(self, first: bool = False) -> list[str]:
        self.hotkeys.unregister_all()
        errors = []
        for name, key, mode in (("截图翻译", "hotkey", "translate"), ("截图识字", "hotkey_ocr", "ocr")):
            text = self.cfg.get(key, "")
            if not text:
                continue
            # 快捷键回调发生在原生事件过滤器内，推迟到事件循环中执行
            try:
                self.hotkeys.register(text, lambda m=mode: QTimer.singleShot(0, lambda: self.start_snip(m)))
            except (ValueError, OSError) as e:
                errors.append(f"{name}：{e}")
        self.act_translate.setText(f"截图翻译\t{self.cfg['hotkey'] or ''}")
        self.act_ocr.setText(f"截图识字（复制文字）\t{self.cfg['hotkey_ocr'] or ''}")
        self.main.update_hotkey_labels(self.cfg)
        self.history.set_limit(int(self.cfg["history_limit"]))
        if errors:
            self.tray.showMessage("快捷键注册失败", "\n".join(errors), QSystemTrayIcon.Warning, 8000)
        try:
            set_autostart(self.cfg["autostart"])
        except OSError as e:
            errors.append(f"开机启动：{e}")
        return errors

    def on_settings_saved(self, cfg: dict) -> None:
        self.cfg = cfg
        config.save(cfg)
        ocr.warmup_windows(cfg["ocr"]["windows_lang"])
        errors = self.apply_config()
        self._warmup()
        if errors:
            QMessageBox.warning(self.main, "SnapTrans", "设置已保存，但：\n" + "\n".join(errors))

    def _warmup(self) -> None:
        if self.cfg["translate"].get("enabled", True):
            net.warmup(self.cfg["translate"]["base_url"], self.cfg["translate"]["api_key"])
        if self.cfg["ocr"]["engine"] == "api":
            net.warmup(self.cfg["ocr"]["base_url"], self.cfg["ocr"]["api_key"])

    def open_history_dir(self) -> None:
        self.history.dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.history.dir)))

    # ------------------------------------------------------------ 截图

    def start_snip(self, mode: str) -> None:
        if self.snip.active:
            return
        self._snip_mode = mode
        self._warmup()  # 用户框选的同时，后台把网络连接建好
        main_showing = self.main.isVisible() and not self.main.isMinimized()
        self._main_was_visible = main_showing and self.cfg.get("hide_on_snip", True)
        if self._main_was_visible:
            self.main.hide()
            QTimer.singleShot(200, self.snip.start)  # 等窗口隐藏动画结束，避免被截进去
        else:
            self.snip.start()

    def _restore_main(self) -> None:
        if self._main_was_visible:
            self._main_was_visible = False
            self.main.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.main.show()
            self.main.setAttribute(Qt.WA_ShowWithoutActivating, False)

    def _on_captured(self, image: QImage, rect: QRect) -> None:
        self._restore_main()
        rid = self.history.add_image(image, self._snip_mode) if self.cfg.get("history_enabled", True) else None
        self.result.start_image(image, rect, translate=self._snip_mode == "translate", record_id=rid)

    def translate_clipboard(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            self.tray.showMessage("SnapTrans", "剪贴板里没有图片", self.icon, 3000)
            return
        self._warmup()
        rid = self.history.add_image(image, "clipboard") if self.cfg.get("history_enabled", True) else None
        self.result.start_image(image, None, translate=True, record_id=rid)

    def reprocess(self, rid: str) -> None:
        image = QImage(str(self.history.image_path(rid)))
        if image.isNull():
            return
        self._warmup()
        self.result.start_image(image, None, translate=True, record_id=rid)

    def retranslate_record(self, rid: str) -> None:
        record = self.history.get(rid)
        if record and record.get("source"):
            self._warmup()
            self.result.start_text(record["source"], record_id=rid)

    def quit(self) -> None:
        self.hotkeys.unregister_all()
        self.tray.hide()
        self.qapp.quit()


def _notify_running_instance() -> bool:
    """如果已有实例在运行，通知它显示主窗口并返回 True。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def main() -> int:
    # 高 DPI 下截图按物理像素取，避免模糊
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setApplicationName(config.APP_NAME)

    if _notify_running_instance():
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "SnapTrans", "系统托盘不可用。")
        return 1

    app = App(qapp, minimized="--minimized" in sys.argv)

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.readyRead.connect(app.main.bring_to_front)
            conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_connection)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main())
