# PyInstaller 打包配置：python -m PyInstaller snaptrans.spec
# 使用 onedir（文件夹）模式：启动快，不需要每次解压到临时目录。
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

hiddenimports = collect_submodules("winrt")

# 用不到的 Qt 模块，排除以减小体积
qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtBluetooth", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSerialPort", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtXml", "PySide6.QtConcurrent",
    "PySide6.QtGraphs", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects",
]

a = Analysis(
    ["run.pyw"],
    pathex=["."],
    binaries=collect_dynamic_libs("winrt"),
    datas=[("assets/icon.ico", "assets")],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PyQt6", "matplotlib", "pytest", "IPython"] + qt_excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SnapTrans",
    icon="assets/icon.ico",
    console=False,
    upx=False,
    version=None,
)

coll = COLLECT(exe, a.binaries, a.datas, name="SnapTrans", upx=False)
