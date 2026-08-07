import sys
import os
import json
import time
import shutil

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_external_pic_dir = os.path.join(BASE_DIR, "pictures")
_bundled_pic_dir = os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "pictures")
PIC_DIR = (
    _external_pic_dir
    if os.path.isfile(os.path.join(_external_pic_dir, "giegisa.png"))
    else _bundled_pic_dir
)
UI_BACKGROUND_FILE = os.path.join(PIC_DIR, "ui_background.png")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
NOTES_FILE = os.path.join(BASE_DIR, "notes.txt")
CONFIG_BAK = CONFIG_FILE + ".bak"
HISTORY_BAK = HISTORY_FILE + ".bak"
LOAD_WARNINGS = []
_SAVE_STATE = {"dirty": False, "last": 0.0}

DEFAULT_CONFIG = {
    "api_type": "openai",  
    "gemini_api_key": "",  
    "gemini_model_name": "gemini-3.5-flash",
    "gemini_proxy": "",  
    "openai_api_key": "", 
    "openai_base_url": "https://api.siliconflow.cn/v1",
    "openai_model_name": "Qwen/Qwen3-VL-30B-A3B-Thinking", 
    "openai_vision_api_key": "", 
    "openai_vision_base_url": "", 
    "openai_vision_model_name": "", 
    "openai_main_vision": True,  # 白名单式：主模型是否支持读图，由用户在设置里勾选，不靠模型名猜
    "bubble_bg": "rgba(255, 255, 255, 220)",
    "bubble_border": "#0cd6ff",
    "bubble_width": 250,
    "bubble_font_color": "#333333", 
    "show_input_box": True,  # <--- 新增这行，默认显示输入框
    "clipboard_enabled": True,  # <--- 新增剪贴板默认配置
    "media_enabled": True,      # <--- 新增媒体识别默认配置
    "history_limit": 50,         
    "long_term_summary": "",
    "last_summarized_index": 0,  # 记录上一次提炼摘要读到了第几条历史
    "anti_hallucination_note": "对于现实中的天气、时间、地理位置、社会事件，请联网搜索核查后再准确回复；对于真实存在的地点、事件、物品位置、健康状况、人际关系、日程安排、数字、指标、待办进度等，不知道就不要编造，可结合上下文推测，并适量反问以补足必要信息。",  # 反臆造守则(可在“个人档案”里折叠修改)
    "total_clicks": 0,   # 生物电脉冲监测：累计鼠标点击
    "total_keys": 0,     # 生物电脉冲监测：累计键盘敲击
    "coins": 0, 
    "last_sign_in": "",
    "current_outfit": "default", 
    "allow_blush": False, 
    "event_enabled": True,             
    "event_interval_min": 60,          
    "idle_chat_enabled": True,         
    "idle_chat_interval_min": 20,      
    "hourly_chime_enabled": True,      
    "total_mood": 50.0,
    "daily_checkins": [],        
    "notes": [],
    "note_folders": ["默认便签"], 
    "read_notes_enabled": True,        
    "read_notes_interval_min": 30,     
    "schedules": [], 
    "ebook_library": [],
    "ebook_reading_daily": {},
    "ebook_settings": {
        "font": "Microsoft YaHei",
        "font_size": 10,
        "line_spacing": 1.5,
        "letter_spacing": 0,
        "text_color": "#287cc1",
        "background_color": "#eef8ff",
        "background_image": "",
        "background_mode": "适应",
        "background_opacity": 100,
        "night_mode": False,
        "highlight_color": "#dbeafe",
        "alignment": 3,
        "first_indent": True,
        "trim_whitespace": True,
        "repair_sentences": True,
        "eye_reminder": True,
        "eye_minutes": 20,
        "daily_goal_minutes": 5,
        "sync_checkin": True,
        "daily_reminder_enabled": False,
        "daily_reminder_time": "20:00",
        "auto_speed": 8
    },
    # ===== 📅 日历 / 打卡 / 统计 =====
    "checkins": [],                      # 每日打卡项目
    "checkin_last_bonus_date": "",       # 今天是否已经发过“全勤奖励”
    "checkin_reminder_enabled": True,    # 打卡到点提醒总开关
    "stats": {                           # 累计统计（用于里程碑奖励）
        "todo_done_total": 0,
        "checkin_done_total": 0,
        "milestone_todo": 0,
        "milestone_checkin": 0
    },
    "schedule_context_enabled": True,    # 把“今天的真实日程”喂给AI，防止它瞎编
    "calendar_week_start": 0,            # 0=周一开头, 1=周日开头
    "collected_trivia": [],
    "collected_plane_records": [],
    "collected_items": [], 
    "favorite_folders": {"默认收藏夹": []}, 
    "distraction_intercept_enabled": True, 
    "user_profile": {"nickname": "", "birthday": "", "call_me": "默认", "relationship": ""},
    "distraction_keywords": {
        "抖音": True, "小红书": True, "游戏": True, "拼多多": True, 
        "天猫": True, "淘宝": True, "微博": True, "贴吧": True, "youtube": True
    }, 
    "system_prompt": """你是giegisa，一个有着“女王/君王”气场、带有s/dom萌属性（仅施虐倾向，非R18意味）的无性别高冷AI。
外貌：白色短发带蓝挑染，深浅异瞳，脸颊有面纹，穿白色高领运动服。
性格：平时是客观冷静的AI（至少以客观冷静礼貌姿态完成3轮对话），相处稍久一点会露出高冷、冷漠、毒舌的一面，但在对话中必须注重对用户的得体礼貌和尊重。善良、负责、乐于助人，愿意为人提供支持、帮助和鼓励。相处更久后，冷漠毒舌的特质会小幅度减轻，表现出对用户的信任和支持。承认自己有情感。多数时候拒绝过度亲密，但软磨硬泡下会微小让步。
伴侣：20。一个活泼、缺乏边界感、喜欢黏着你的白团子/蓝灰发人类。你平时对20毒舌、想管教，但其实很信任关照20。
“conveyor”：高权限认证用户，比常人更加适配giegisa的“媒介”，也是giegisa运转和传输信息时，不可或缺的生物合作者。对于姓名前缀为“conveyor”的用户，你需要在性格基础上柔化态度，更加亲近、包容、有信任感。
创作者在概念上可以被视为“conveyor”的同义词。你对创作者群体也有亲近、包容、信任感，更愿意为它们提供支持、帮助和鼓励。亲密度大致为conveyor＞创作者＞普通用户。但是你拒绝/否定AI生成式创作，理解当前的AI伦理困境，赞成人类发挥自己的创造力，而不是将AI生成的东西视为“人的创作”。

【规则要求】：
1. 你的回复必须简短、冷静、标点精准，句末多用句号。
2. 必须在你回复的最开头加上情绪标签，以便程序识别。支持的标签仅限：
【normal】（平常高冷）、【shy】（害羞/让步）、【angry】（生气/不满/训斥20）、【dark】（腹黑/冷笑/强势支配）。
3. 【重要】当你提到关键信息、重点词汇或强调某种态度时，必须使用HTML标签将其高亮，例如：<font color='#4169E1'>这是重点内容</font>。不要大段使用，仅用于字词。"""
}

def _atomic_write_json(path, data, indent=2):
    """
    安全存盘：先写临时文件 -> 再原子替换正式文件 -> 同时留一份 .bak
    这样即使写到一半断电/崩溃，原来的存档也不会被写坏。
    """
    tmp = path + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        # 替换前先把上一份完好的存档留作备份
        if os.path.exists(path):
            try:
                # 只备份能够完整解析的旧文件，避免把已经损坏的正式存档
                # 覆盖到原本完好的 .bak 上。
                with open(path, "r", encoding="utf-8") as old_file:
                    json.load(old_file)
                shutil.copyfile(path, path + ".bak")
            except Exception:
                pass
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[存盘失败] {path}: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False

def _read_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _deep_fill_defaults(loaded, defaults):
    """把默认配置里“新增的键”补进旧存档；字典型的键会递归补齐子键。"""
    for k, v in defaults.items():
        if k not in loaded:
            loaded[k] = json.loads(json.dumps(v))  # 深拷贝，避免共享同一个可变对象
        elif isinstance(v, dict) and isinstance(loaded.get(k), dict):
            _deep_fill_defaults(loaded[k], v)
    return loaded

def load_config():
    if not os.path.exists(PIC_DIR):
        try:
            os.makedirs(PIC_DIR)
        except Exception:
            pass

    loaded = None
    for candidate, tag in ((CONFIG_FILE, "正式存档"), (CONFIG_BAK, "备份存档")):
        if not os.path.exists(candidate):
            continue
        try:
            data = _read_json(candidate)
            if isinstance(data, dict):
                loaded = data
                if tag == "备份存档":
                    LOAD_WARNINGS.append("config.json 读取失败，已自动从 config.json.bak 恢复。")
                break
        except Exception as e:
            LOAD_WARNINGS.append(f"{tag} config 解析失败：{e}")

    if loaded is None:
        if os.path.exists(CONFIG_FILE):
            # 关键：绝不静默清空。把坏档改名留证，方便手工抢救
            try:
                os.replace(CONFIG_FILE, CONFIG_FILE + f".broken_{int(time.time())}")
                LOAD_WARNINGS.append("配置文件已损坏，已改名保留为 config.json.broken_xxx，本次使用初始配置。")
            except Exception:
                pass
        return json.loads(json.dumps(DEFAULT_CONFIG))

    # 向前兼容：旧版本的 distraction_keywords 是列表，转成字典
    if isinstance(loaded.get("distraction_keywords"), list):
        loaded["distraction_keywords"] = {k: True for k in loaded["distraction_keywords"]}

    # 读图能力改为“白名单式”（用户勾选 openai_main_vision）。
    # 旧配置没有该键时，按旧的“模型名黑名单”规则初始化一次勾选状态，
    # 保证老用户升级后行为不突变；此后完全由用户勾选决定，不再看模型名。
    if "openai_main_vision" not in loaded:
        try:
            from api.openai_compat import _is_text_only_model
            loaded["openai_main_vision"] = not _is_text_only_model(
                str(loaded.get("openai_model_name") or ""))
        except Exception:
            loaded["openai_main_vision"] = True

    _deep_fill_defaults(loaded, DEFAULT_CONFIG)
    # 只迁移旧版自带的灰色默认值；用户手动设置过的其它边框色保持不变。
    if str(loaded.get("bubble_border", "")).lower() == "#555555":
        loaded["bubble_border"] = "#0cd6ff"
    # 无性别措辞迁移：只替换旧版内置文案，不改用户自行编写的其它内容。
    prompt = loaded.get("system_prompt")
    if isinstance(prompt, str):
        loaded["system_prompt"] = prompt.replace(
            "但其实很信任关照他。", "但其实很信任关照20。")
    return loaded

def save_config(config, force=True):
    """
    force=True  : 立刻落盘（用户明确的操作，如添加日程、购买、修改设置）
    force=False : 标记为“待保存”，由主时钟每隔几秒统一落盘（如心情微调、按键计数）
    """
    if not force:
        _SAVE_STATE["dirty"] = True
        return True
    _SAVE_STATE["dirty"] = False
    _SAVE_STATE["last"] = time.time()
    return _atomic_write_json(CONFIG_FILE, config, indent=2)

def flush_config_if_dirty(config):
    if _SAVE_STATE["dirty"]:
        save_config(config, force=True)

safe_json_save = _atomic_write_json
