"""
OpenAI 兼容接口调用
===================
通过 openai 官方 SDK 调用任意兼容 OpenAI 协议的 API（硅基流动 / DeepSeek / 智谱 / 本地模型等）。

支持：
- 纯文本对话
- 多模态图片识别（自动跳过纯文本模型）
"""

import re


# ==========================================
# 🔤 已知不支持图片识别的纯文本大模型（按名称匹配，不限提供商）
# ==========================================
TEXT_ONLY_MODEL_PATTERNS = [
    # --- DeepSeek 全系列均为纯文本模型 ---
    r"deepseek",                       # deepseek-chat / deepseek-reasoner / deepseek-v4-pro 等

    # --- 智谱 ChatGLM / GLM 系列（除 GLM-4V 外均为纯文本） ---
    r"chatglm",                        # ChatGLM2 / ChatGLM3 等
    r"glm-4($|[^v])",                  # GLM-4 / GLM-4-Flash 等（排除 GLM-4V）
    r"glm-4\.\d",                      # GLM-4.5 / GLM-4.7
    r"glm-5",                          # GLM-5 / GLM-5.1 / GLM-5.2
]

# 预编译正则
_TEXT_ONLY_RE = re.compile("|".join(TEXT_ONLY_MODEL_PATTERNS), re.IGNORECASE)


def _is_text_only_model(model_name: str) -> bool:
    """根据模型名称判断是否为纯文本模型（不支持图片输入）。"""
    return bool(_TEXT_ONLY_RE.search(model_name))


def _join_url(base):
    """规范化 base_url：补全 scheme，去尾斜杠。

    注意：OpenAI SDK 的 OpenAI(base_url=...) 会在内部自行拼接
    /chat/completions，因此这里不要再追加该路径，否则会产生双段 404。
    """
    base = (base or "https://api.openai.com/v1").strip().rstrip("/")
    if not base.lower().startswith("http"):
        base = "https://" + base
    # 如果用户手误填了 /chat/completions 结尾的完整 URL，裁掉
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base


def openai_chat(config, messages, temperature=0.7, image_b64=None,
                image_mime="image/png", timeout=60):
    """调用 OpenAI 兼容 API 生成回复。

    Args:
        config: 配置字典，需包含 openai_api_key、openai_base_url、openai_model_name
        messages: 消息列表 [{"role": "user"/"assistant"/"system", "content": "..."}]
        temperature: 采样温度，默认 0.7
        image_b64: base64 编码的图片数据（可选，纯文本模型会自动跳过）
        image_mime: 图片 MIME 类型，默认 "image/png"
        timeout: 请求超时秒数，默认 60

    Returns:
        模型生成的回复文本

    Raises:
        ValueError: API Key 未填写或包含非 ASCII 字符
    """
    from openai import OpenAI

    api_key = config.get("openai_api_key", "").strip()
    if not api_key or not api_key.isascii():
        raise ValueError("OpenAI API Key 包含非英文字符或未填写。")

    model_name = config.get("openai_model_name", "").strip()
    base_url = _join_url(config.get("openai_base_url", "").strip())

    # 读图能力为“白名单式”：主模型能否读图由用户勾选 openai_main_vision 决定，不靠模型名猜。
    # - 主模型可读图（默认）→ 贴图直接用主模型；
    # - 主模型不可读图 + 已填读图模型 → 临时切换到读图模型识图
    #   （读图模型的 api key / 接口地址留空则沿用上方主模型的）；
    # - 主模型不可读图 + 未填读图模型 → 仍走主模型，图片降级为文字提示。
    # 纯文字对话永远走主模型，与读图设置无关。
    vision_model = (config.get("openai_vision_model_name") or "").strip()
    main_can_vision = bool(config.get("openai_main_vision", True))

    using_vision_model = False
    if image_b64 and vision_model and not main_can_vision:
        using_vision_model = True
        model_name = vision_model
        vision_key = (config.get("openai_vision_api_key") or "").strip()
        vision_url = (config.get("openai_vision_base_url") or "").strip()
        if vision_key:
            api_key = vision_key
        if vision_url:
            base_url = _join_url(vision_url)

    can_handle_image = main_can_vision or using_vision_model

    # 如果带了图片，将最后一条 user 消息改造为多模态格式
    if image_b64 and messages:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i]["role"] == "user":
                text_part = messages[i]["content"]
                if isinstance(text_part, list):
                    break  # 已经是多模态格式，不重复处理

                if not can_handle_image:
                    # 主模型不可读图且未配读图模型：跳过图片，在文本末尾追加简短提示
                    messages[i]["content"] = (
                        f"{text_part or ''}\n"
                        f"[用户贴了一张图，但 {model_name} 不支持图片输入，"
                        f"请据文字上下文回复。]"
                    ).strip()
                else:
                    # 可读图：正常传递 base64 图片
                    messages[i]["content"] = [
                        {"type": "text", "text": text_part or "请描述这张图片。"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_mime};base64,{image_b64}"
                            },
                        },
                    ]
                break

    # 创建客户端并发起请求
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content
