"""DeepSeek API 客户端（OpenAI 兼容接口）。

使用标准库 urllib 直接发 HTTP 请求，零第三方依赖。
- chat()：通用补全，可携带任意 tools 列表。
- generate()：叙事轮，强制携带 update_world_state 工具。
- call_tool()：携带指定工具，解析返回的工具调用参数。
- summarize()：不带工具的纯文本补全（历史压缩用）。
"""
import json
import os
import ssl
import urllib.error
import urllib.request

from config import cacert_path
from tools import UPDATE_TOOL


def _ssl_context():
    """优先用随程序打包的 CA 证书，找不到时退回系统默认。"""
    path = cacert_path()
    if os.path.exists(path):
        return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


class LLMError(RuntimeError):
    """API 调用失败。"""


class LLMClient:
    def __init__(self, api_key, model="deepseek-chat", base_url="https://api.deepseek.com"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    # ---- 底层 HTTP ----
    def _post(self, payload):
        url = self.base_url + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180, context=_ssl_context()) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise LLMError(f"DeepSeek API 返回错误 HTTP {e.code}：{detail}") from e
        except urllib.error.URLError as e:
            raise LLMError(f"网络请求失败：{e.reason}") from e

    # ---- 通用补全 ----
    def chat(self, messages, tools=None, temperature=0.8, max_tokens=2048):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        return self._post(payload)

    # ---- 叙事轮（update_world_state 工具） ----
    def generate(self, messages):
        """发起一轮叙事请求，返回 (叙事文本, 状态更新字典或 None)。"""
        resp = self.chat(messages, tools=[UPDATE_TOOL])
        return self.parse_reply(resp)

    # ---- 指定工具调用（世界观生成用） ----
    def call_tool(self, messages, tool, temperature=0.8, max_tokens=2048):
        """携带指定工具补全，返回 (文本, 工具名或None, 参数dict或None)。"""
        resp = self.chat(messages, tools=[tool], temperature=temperature, max_tokens=max_tokens)
        choice = resp["choices"][0]
        message = choice["message"]
        content = (message.get("content") or "").strip()
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            return content, fn.get("name"), args
        return content, None, None

    # ---- 不带工具的摘要补全（历史压缩用） ----
    def summarize(self, messages):
        resp = self.chat(messages, tools=None, temperature=0.4, max_tokens=800)
        choice = resp["choices"][0]
        return (choice["message"].get("content") or "").strip()

    # ---- 解析 ----
    @staticmethod
    def parse_reply(resp):
        choice = resp["choices"][0]
        message = choice["message"]
        text = (message.get("content") or "").strip()

        update = None
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            if fn.get("name") == "update_world_state":
                raw = fn.get("arguments") or "{}"
                try:
                    update = json.loads(raw)
                except json.JSONDecodeError:
                    update = None
                break
        return text, update
