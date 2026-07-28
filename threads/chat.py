import os
import json
import re
import threading
import time
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from config import HISTORY_FILE, HISTORY_BAK, BASE_DIR, _atomic_write_json, _read_json, save_config, LOAD_WARNINGS
from api import gemini_rest_generate, openai_chat

_LOG_FILE = os.path.join(BASE_DIR, "giegisa.log")


def _log(*args):
    """写日志到文件 —— pythonw.exe 无控制台，print 不可见。"""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {' '.join(str(a) for a in args)}\n")
    except Exception:
        pass

class ChatThread(QThread):
    reply_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    api_lag_occurred = pyqtSignal(float)
    summary_updated = pyqtSignal(str)   # 后台提炼完长期记忆后，通知主线程刷新界面
    send_failed = pyqtSignal(str)       # 发送失败，参数为未能发出的用户消息原文

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.history = self.load_history()
        self.current_msg = ""
        self.mood_prompt = ""
        self.current_image_b64 = None
        self.current_image_mime = "image/png"
        # 用户消息、自动报时和日程点评可能在上一条请求尚未结束时同时到来。
        # QThread 正在运行时再次 start() 不会可靠地启动第二次请求，旧实现还会
        # 覆盖 current_msg，造成消息丢失。这里改成串行队列，逐条发送。
        self._pending_messages = []
        self.finished.connect(self._start_next_message)
        
    # 本地历史的硬上限。超过后把最旧的那批搬进 history_archive.json（不删除，只是挪走），
    # 避免用久了 history.json 变成几十MB，导致每次回复都要重写整个大文件而越用越卡。
    HISTORY_HARD_CAP = 4000

    def load_history(self):
        for candidate, is_bak in ((HISTORY_FILE, False), (HISTORY_BAK, True)):
            if not os.path.exists(candidate):
                continue
            try:
                data = _read_json(candidate)
                if isinstance(data, list):
                    if is_bak:
                        LOAD_WARNINGS.append("history.json 读取失败，已自动从 history.json.bak 恢复。")
                    return [m for m in data if isinstance(m, dict) and "role" in m]
            except Exception as e:
                LOAD_WARNINGS.append(f"历史记录解析失败：{e}")
        if os.path.exists(HISTORY_FILE):
            try:
                os.replace(HISTORY_FILE, HISTORY_FILE + f".broken_{int(time.time())}")
                LOAD_WARNINGS.append("历史记录文件已损坏，已改名保留为 history.json.broken_xxx。")
            except Exception:
                pass
        return []

    def save_history(self):
        self._archive_overflow()
        # indent=1 比 indent=4 省将近一半体积，写入也更快；仍然可读
        _atomic_write_json(HISTORY_FILE, self.history, indent=1)

    def _archive_overflow(self):
        if len(self.history) <= self.HISTORY_HARD_CAP:
            return
        cut = len(self.history) - self.HISTORY_HARD_CAP
        cut -= cut % 2  # 保证按"一问一答"成对搬走，不打乱配对关系
        if cut <= 0:
            return
        moved = self.history[:cut]
        remaining = self.history[cut:]
        archive_path = os.path.join(BASE_DIR, "history_archive.json")
        try:
            old = []
            if os.path.exists(archive_path):
                data = _read_json(archive_path)
                if isinstance(data, list):
                    old = data
            if not _atomic_write_json(archive_path, old + moved, indent=1):
                return
        except Exception as e:
            _log(f"[历史归档失败] {e}")
            return
        # 只有确认归档已经安全落盘后，才从当前历史中移走旧记录。
        # 原实现先截断内存，归档写入失败时会静默丢掉最旧的对话。
        self.history = remaining
        # 摘要书签要跟着一起前移，否则会指到错误的位置
        idx = self.config.get("last_summarized_index", 0)
        self.config["last_summarized_index"] = max(0, idx - cut)

    def clear_history(self):
        self.history = []
        self.save_history()

    def delete_history_item(self, index):
        hist_index = index * 2 
        if hist_index + 1 < len(self.history):
            del self.history[hist_index:hist_index+2]
            # 删记录后，摘要书签可能越界，必须夹回合法范围，否则下次提炼会读到错位内容
            self.config["last_summarized_index"] = max(
                0, min(self.config.get("last_summarized_index", 0), len(self.history)))
            self.save_history()

    def update_config(self, new_config):
        self.config = new_config

    def _compose_system_prompt(self):
        # 把"人设 + 反臆造守则 + 当前情绪"拼成一段系统指令。
        # 放在系统指令里(而不是每轮塞进历史)：token更省、约束更稳、也不会把历史记录越撑越大。
        parts = [self.config.get("system_prompt", "")]
        note = (self.config.get("anti_hallucination_note", "") or "").strip()
        if note:
            parts.append("【现实校准协议·反臆造守则】" + note)
        # 👇这里就是新增的长期摘要注入点👇
        summary = (self.config.get("long_term_summary", "") or "").strip()
        if summary:
            parts.append("【核心长期记忆摘要/前情提要】\n" + summary)   
        if self.mood_prompt:
            parts.append(self.mood_prompt)
        return "\n".join(p for p in parts if p).strip()

    def send_message(self, msg, mood_prompt="", image_b64=None, image_mime="image/png"):
        payload = (msg, mood_prompt, image_b64, image_mime)
        if self.isRunning():
            self._pending_messages.append(payload)
            return
        self._apply_message_payload(payload)
        self.start()

    def _apply_message_payload(self, payload):
        (self.current_msg, self.mood_prompt,
         self.current_image_b64, self.current_image_mime) = payload

    def _start_next_message(self):
        if not self._pending_messages or self.isRunning():
            return
        self._apply_message_payload(self._pending_messages.pop(0))
        self.start()

    def run(self):
        try:
            api_type = self.config.get("api_type", "gemini")
            if api_type == "gemini":
                self._run_gemini()
            else:
                self._run_openai()
        except Exception as e:
            self.error_occurred.emit(f"【normal】接口异常：{str(e)}。")
            self.send_failed.emit(self.current_msg)

    def _record_and_emit(self, reply):
        """把这一轮对话写进历史并把回复发给界面（历史里只存文字，不存庞大的图片编码）"""
        now_ts = time.time()
        user_text = self.current_msg
        if getattr(self, "current_image_b64", None):
            user_text = (user_text + " [附带图片]").strip()
        self.history.append({"role": "user", "content": user_text, "timestamp": now_ts})
        self.history.append({"role": "assistant", "content": reply, "timestamp": now_ts})
        self.save_history()
        self.reply_ready.emit(reply)
        # 在发完回复后，静默检查是否需要滚雪球式更新摘要。
        # 放在独立线程里执行，避免阻塞 run() 导致下一条消息排队。
        if self._summarize_needed():
            threading.Thread(target=self._summarize_async, daemon=True).start()

    def _summarize_needed(self):
        limit_rounds = self.config.get("history_limit", 50)
        if limit_rounds <= 0:
            return False
        limit_count = limit_rounds * 2
        last_idx = max(0, min(self.config.get("last_summarized_index", 0), len(self.history)))
        self.config["last_summarized_index"] = last_idx
        unsummarized_count = len(self.history) - limit_count - last_idx
        return unsummarized_count >= 20

    def _summarize_async(self):
        try:
            self.check_and_auto_summarize()
        except Exception as e:
            _log(f"[后台记忆提炼失败]: {e}")

    def check_and_auto_summarize(self):
        """检查历史记录是否超限，并在后台悄悄触发大模型自动融合长期摘要（无损本地历史版）"""
        limit_rounds = self.config.get("history_limit", 50)
        if limit_rounds <= 0:
            return  # 0代表用户不想限制，直接跳过
            
        limit_count = limit_rounds * 2
        # 夹紧书签：用户可能删过历史，书签越界会导致提炼到错位的内容
        last_idx = max(0, min(self.config.get("last_summarized_index", 0), len(self.history)))
        self.config["last_summarized_index"] = last_idx
        
        # 计算当前有多少条"已经溢出，但还没被提炼"的旧对话
        unsummarized_count = len(self.history) - limit_count - last_idx
        
        # 为了防止频繁调用API造成网络卡顿，积攒了超过 20 条（10轮）溢出对话时，才集中提炼一次
        if unsummarized_count >= 20:
            # 只抓取"书签"之后的这部分旧对话进行提炼
            overflow_data = self.history[last_idx : last_idx + unsummarized_count]
            
            overflow_text = ""
            for msg in overflow_data:
                role_label = "用户" if msg.get("role") == "user" else "Giegisa"
                overflow_text += f"{role_label}: {msg.get('content', '')}\n"
                
            old_summary = self.config.get("long_term_summary", "").strip()
            
            prompt = (
                f"【系统后台记忆提炼协议】\n"
                f"当前桌宠的对话历史已推进。请协助将原有的【长期记忆摘要】与最新溢出的【旧对话历史】进行高纯度融合更新。\n\n"
                f"要保留的核心指标：用户的称呼/身份设定、你们之间发生过的重大事件节点、已经达成的关系突破或重要线索。规避无关的日常废话闲聊。\n\n"
                f"原有的长期记忆摘要：\n\"\"\"\n{old_summary if old_summary else '暂无前情提要。'}\n\"\"\"\n\n"
                f"新增的旧对话内容：\n\"\"\"\n{overflow_text}\n\"\"\"\n\n"
                f"【硬性要求】：请直接输出融合更新后的全新【长期记忆摘要】正文，字数严格控制在300字以内。绝对不要包含任何诸如‘好的’等废话开场白或结尾，直接输出正文！"
            )
            
            try:
                api_type = self.config.get("api_type", "gemini")
                if api_type == "gemini":
                    new_summary = gemini_rest_generate(self.config, prompt, timeout=40)
                else:
                    new_summary = openai_chat(self.config, [{"role": "user", "content": prompt}], temperature=0.6, timeout=40)
                
                # 只清理系统注入的标签，不删合法方括号内容
                new_summary = re.sub(
                    r'\[(?:系统|后台|情绪状态|事实数据)[^\]]*\]',
                    '', new_summary,
                ).strip()
                
                if new_summary and len(new_summary) > 5:
                    # 1. 把提炼出来的成果写入 config
                    self.config["long_term_summary"] = new_summary
                    
                    # 2. 【核心修改】：往前移动阅读书签，证明这段我们已经看过了，但绝对不删除 self.history！
                    self.config["last_summarized_index"] = last_idx + unsummarized_count
                    save_config(self.config)
                    
                    # 3. 顺手刷新一下UI
                    # 原来这里写的是 for attr in dir(self.parent())，但 ChatThread 没有 parent，
                    # self.parent() 恒为 None，这段刷新代码从来没生效过。改成发信号回主线程。
                    self.summary_updated.emit(new_summary)
            except Exception as e:
                # 静默失败，不打扰玩家
                _log(f"[后台记忆提炼失败]: {str(e)}")

    def _run_gemini(self):
        try:
            system_instruction = self._compose_system_prompt()
            # 获取限制轮数（1轮=2条），并安全截断
            limit = self.config.get("history_limit", 50) * 2
            working_history = self.history[-limit:] if limit > 0 else self.history
            
            start_t = time.time()
            reply_text = gemini_rest_generate(
                self.config,
                self.current_msg,
                system_instruction=system_instruction,
                history=working_history,  # <--- 使用截断后的安全记录
                image_b64=getattr(self, "current_image_b64", None),
                image_mime=getattr(self, "current_image_mime", "image/png"),
                timeout=30,
            )
            elapsed = time.time() - start_t
            if elapsed > 10.0:
                self.api_lag_occurred.emit(elapsed)
            self._record_and_emit(reply_text)
        except Exception as e:
            self.reply_ready.emit(f"【normal】Gemini 连线失败：{str(e)}。")
            self.send_failed.emit(self.current_msg)

    def _run_openai(self):
        sys_prompt = self._compose_system_prompt()
        # 获取限制轮数（1轮=2条），并安全截断
        limit = self.config.get("history_limit", 50) * 2
        working_history = self.history[-limit:] if limit > 0 else self.history
        
        clean_history = [{"role": m["role"], "content": m["content"]} for m in working_history]
        messages = [{"role": "system", "content": sys_prompt}] + clean_history
        messages.append({"role": "user", "content": self.current_msg})

        start_t = time.time()
        reply = openai_chat(
            self.config,
            messages,
            temperature=0.7,
            image_b64=getattr(self, "current_image_b64", None),
            image_mime=getattr(self, "current_image_mime", "image/png"),
            timeout=60,
        )
        elapsed = time.time() - start_t
        if elapsed > 10.0:
            self.api_lag_occurred.emit(elapsed)

        self._record_and_emit(reply)
