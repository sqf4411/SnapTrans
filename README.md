# SnapTrans 截图翻译

Windows 上的截图 OCR 实时翻译工具，用法类似 macOS 上的 Bob，可以和 Snipaste 同时使用。

按快捷键 → 框选区域 → 立即识别文字 → 流式显示译文。

## 功能

- **截图翻译**（默认 `Alt+Q`）：框选后识别并翻译，结果显示在选区旁边的浮窗里
- **截图识字**（默认 `Alt+W`）：只识别文字，并复制到剪贴板
- **翻译剪贴板图片**：从托盘菜单打开，例如用 Snipaste 截图后直接翻译
- 支持多显示器和高 DPI；在选区内右键或按 `Esc` 取消
- 结果浮窗：原文可以修改，`Ctrl+Enter` 重新翻译，`Ctrl+Shift+C` 复制译文，可以临时切换目标语言，📌 固定窗口
- 中英自动互译：原文已经是目标语言（比如中文）时，自动译成另一种语言（默认英文）

## 为什么快

| 环节 | 做法 | 典型耗时 |
| --- | --- | --- |
| OCR | Windows 系统自带 OCR，离线运行，按文字行高自动放大并补边距来提高准确率 | 50–150 ms |
| 网络 | 复用长连接；按下快捷键时就开始预热 TLS 连接，框选的同时握手已经完成 | 省掉约 200–500 ms |
| 翻译 | 流式输出，首字到达就显示 | DeepSeek 首字约 0.5–1 s |

## 服务商

翻译和在线 OCR 都使用 **OpenAI 兼容接口**，在设置里选一个预设，再填 API Key 就能用：

- 翻译：DeepSeek（默认）、硅基流动、通义千问、OpenAI、Ollama 本地，也可以自定义
- 在线 OCR（可选）：硅基流动 · DeepSeek-OCR、通义 qwen-vl-ocr、OpenAI，也可以自定义

> DeepSeek-OCR 是开源模型，DeepSeek 官方 API 没有提供它。可以通过硅基流动等平台调用，
> 也可以用 vLLM 自己部署后填写本地地址。在线 OCR 要上传图片，速度比本地 OCR 慢，
> 适合排版复杂的图片，日常翻译建议用 Windows OCR。

## 安装和运行

需要 Windows 10/11 和 Python 3.10 以上版本。

```bat
setup.bat      :: 创建 .venv 并安装依赖（只需运行一次）
start.bat      :: 启动，程序常驻在系统托盘
```

首次启动会自动打开设置窗口，填好翻译 API Key 后就可以用了。配置保存在 `%APPDATA%\SnapTrans\config.json`。

### OCR 语言包

Windows OCR 依赖系统语言包。中文语言包可以同时识别中英文；如果经常翻译英文，建议在
「设置 → 时间和语言 → 语言和区域」中添加 English (United States)。装好后，程序遇到英文为主的内容会自动改用英文引擎，准确率更高。
日文、韩文等其他语言也一样，需要先安装对应的语言包。

## 项目结构

```
snaptrans/
  app.py              托盘、快捷键、整体流程
  capture.py          多屏截图选区遮罩
  ocr.py              Windows OCR / 在线视觉模型 OCR
  net.py              HTTP 长连接、预热、流式请求
  translator.py       翻译提示词、目标语言判断
  worker.py           后台线程任务
  result_window.py    结果浮窗
  settings_dialog.py  设置窗口
  hotkey.py           全局快捷键（RegisterHotKey）
```
