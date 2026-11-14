"""OpenAI 兼容接口的 HTTP 客户端。

为了速度：全局复用一个长连接池（省掉每次的 TCP + TLS 握手），按下快捷键时就预热连接，
翻译使用流式输出，第一个字返回就显示。
"""

import json
import threading

import httpx

_client = httpx.Client(
    timeout=httpx.Timeout(30.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120),
    http2=False,
    trust_env=True,
)


class ApiError(Exception):
    pass


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path


def _headers(api_key: str) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        resp.read()
        body = resp.json()
        msg = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        msg = msg or body.get("message") or resp.text
    except Exception:
        msg = resp.text
    hint = {401: "API Key 无效", 402: "账户余额不足", 404: "接口地址或模型名错误", 429: "请求过于频繁或额度用尽"}.get(resp.status_code, "")
    raise ApiError(f"HTTP {resp.status_code} {hint} {str(msg)[:300]}".strip())


def warmup(base_url: str, api_key: str) -> None:
    """后台建立到服务商的连接，后续请求直接复用。"""
    if not base_url:
        return

    def run():
        try:
            _client.get(_url(base_url, "/models"), headers=_headers(api_key), timeout=5)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def chat(base_url: str, api_key: str, model: str, messages: list, temperature: float = 0.3) -> str:
    try:
        resp = _client.post(
            _url(base_url, "/chat/completions"),
            headers=_headers(api_key),
            json={"model": model, "messages": messages, "temperature": temperature, "stream": False},
            timeout=60,
        )
    except httpx.HTTPError as e:
        raise ApiError(f"网络错误：{e}") from e
    _raise_for(resp)
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as e:
        raise ApiError(f"无法解析返回内容：{resp.text[:300]}") from e


def chat_stream(base_url: str, api_key: str, model: str, messages: list, temperature: float = 0.3,
                cancel: threading.Event | None = None):
    """流式对话，逐段 yield 文本。"""
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
    try:
        with _client.stream("POST", _url(base_url, "/chat/completions"),
                            headers=_headers(api_key), json=payload) as resp:
            _raise_for(resp)
            for line in resp.iter_lines():
                if cancel is not None and cancel.is_set():
                    return
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue
                if delta.get("content"):
                    yield delta["content"]
    except httpx.HTTPError as e:
        raise ApiError(f"网络错误：{e}") from e
