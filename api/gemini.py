"""Gemini API 调用 —— 通过 Google Gen AI SDK。"""

import re

import httpx
from google import genai


def _normalize_proxy_url(proxy):
    """把用户填写的代理字符串规整成 httpx 可用的代理 URL。

    - 空值/空白 → 返回 ""（不指定代理，由 httpx 自动走系统代理/环境变量，挂梯即可用）
    - 缺协议前缀 → 自动补 http://
    - https:// 前缀 → 改为 http://（本地代理基本都不提供 TLS，
      填 https 前缀会导致 TLS 握手失败而连不上）
    """
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", proxy):
        proxy = "http://" + proxy
    if proxy.lower().startswith("https://"):
        proxy = "http://" + proxy[len("https://"):]
    return proxy


def gemini_rest_generate(config, prompt, system_instruction=None, history=None,
                         image_b64=None, image_mime="image/png", timeout=30):
    """调用 Gemini 模型生成回复。

    Args:
        config: 配置字典，需包含 gemini_api_key、gemini_model_name
        prompt: 当前用户提示词
        system_instruction: 系统指令（可选）
        history: 历史对话列表 [{"role": "user"/"assistant", "content": "..."}]
        image_b64: base64 编码的图片数据（可选）
        image_mime: 图片 MIME 类型，默认 "image/png"
        timeout: 请求超时秒数，默认 30

    Returns:
        模型生成的回复文本

    Raises:
        ValueError: API Key 未填写或返回空内容
    """
    api_key = config.get("gemini_api_key", "").strip()
    if not api_key:
        raise ValueError("尚未填写 Gemini API Key。")

    model_name = (config.get("gemini_model_name") or "gemini-3.5-flash").strip()
    proxy = _normalize_proxy_url(config.get("gemini_proxy"))

    # ---- 构建对话内容 ----
    contents = []
    if history:
        for m in history:
            role = "user" if m.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})

    user_parts = []
    if prompt:
        user_parts.append({"text": prompt})
    if image_b64:
        user_parts.append({
            "inline_data": {"mime_type": image_mime, "data": image_b64},
        })
    if not user_parts:
        user_parts.append({"text": " "})
    contents.append({"role": "user", "parts": user_parts})

    # ---- 创建客户端（代理只影响 Gemini，不污染全局环境变量）----
    def _make_client(use_proxy):
        http_options = {"timeout": timeout * 1000}
        if use_proxy:
            http_options["transport"] = httpx.HTTPTransport(proxy=proxy)
        return genai.Client(api_key=api_key, http_options=http_options)

    client = _make_client(use_proxy=bool(proxy))

    # ---- 构建生成配置 ----
    generate_config = {}
    if system_instruction:
        generate_config["system_instruction"] = {
            "parts": [{"text": system_instruction}],
        }

    # ---- 发起请求 ----
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=generate_config,
        )
    except httpx.TransportError:
        # 手动填写的代理连不上（端口/格式不对、梯子未开等）时，
        # 自动回退到“不填代理”的默认行为（系统代理/环境变量），
        # 保证“填或不填都不影响挂梯使用”。
        if not proxy:
            raise
        client = _make_client(use_proxy=False)
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=generate_config,
        )

    # ---- 解析响应 ----
    candidates = response.candidates
    if not candidates:
        feedback = getattr(response, "prompt_feedback", None) or "Gemini 未返回任何内容"
        raise ValueError(str(feedback))

    text = "".join(
        part.text
        for candidate in candidates
        if candidate.content
        for part in candidate.content.parts
        if part.text
    )
    if not text.strip():
        raise ValueError("Gemini 返回了空内容。")

    return text.strip()
