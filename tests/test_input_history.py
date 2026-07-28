"""终端式输入历史（↑/↓翻阅）与发送失败回填的回归测试。

对应需求：
1. 方向键上/下切换历史输入，指令序列独立维护、不混入历史档案
2. 发送失败时未成功发送的内容重新出现在输入框（等同按一下↑）
"""
import json
import os
import sys
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

import oc
import core.calendar_service as calendar_service_module
import dialogs.ebook as ebook_dialog_module
from ui.widgets import ChatInputBox


class _ChatThreadStub:
    """保留真实 ChatThread 的信号，打桩掉线程与落盘行为。"""

    @staticmethod
    def install(pet):
        thread = pet.chat_thread
        thread.history = []
        thread.sent = []
        thread.isRunning = lambda: False
        thread.save_history = lambda: None
        thread.send_message = lambda *args, **kwargs: thread.sent.append(args)
        return thread


def _key(key):
    return QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    # ---- 1. 控件级：↑/↓ 仅在首行/末行触发信号 ----
    box = ChatInputBox()
    fired = []
    box.historyUp.connect(lambda: fired.append("up"))
    box.historyDown.connect(lambda: fired.append("down"))
    box.setPlainText("单行")
    box.keyPressEvent(_key(Qt.Key.Key_Up))
    box.keyPressEvent(_key(Qt.Key.Key_Down))
    assert fired == ["up", "down"], fired
    fired.clear()
    box.setPlainText("第一行\n第二行\n第三行")
    cursor = box.textCursor()
    cursor.setPosition(8)  # 第二行内
    box.setTextCursor(cursor)
    box.keyPressEvent(_key(Qt.Key.Key_Up))
    box.keyPressEvent(_key(Qt.Key.Key_Down))
    assert fired == [], f"多行中间位置不应触发翻阅: {fired}"
    cursor.movePosition(cursor.MoveOperation.Start)
    box.setTextCursor(cursor)
    box.keyPressEvent(_key(Qt.Key.Key_Up))
    cursor.movePosition(cursor.MoveOperation.End)
    box.setTextCursor(cursor)
    box.keyPressEvent(_key(Qt.Key.Key_Down))
    assert fired == ["up", "down"], fired

    # ---- 2. 桌宠级：输入历史记录与翻阅 ----
    old_loader = oc.load_config
    old_saver = oc.save_config
    old_service_saver = calendar_service_module.save_config
    old_ebook_cleanup = ebook_dialog_module._cleanup_pending_ebook_deletions
    fake_config = json.loads(json.dumps(oc.DEFAULT_CONFIG))
    fake_config["last_sign_in"] = date.today().strftime("%Y-%m-%d")
    oc.load_config = lambda: fake_config
    oc.save_config = lambda *args, **kwargs: True
    calendar_service_module.save_config = lambda *args, **kwargs: True
    ebook_dialog_module._cleanup_pending_ebook_deletions = lambda *args, **kwargs: None
    try:
        pet = oc.DesktopPet()
        pet.show()
        app.processEvents()
        _ChatThreadStub.install(pet)

        def send(text):
            pet.input_box.setPlainText(text)
            pet.send_msg()
            pet.type_timer.stop()
            pet.is_typing = False
            pet._bubble_queue.clear()
            pet.chat_bubble.hide()

        send("第一条指令")
        send("第二条指令")
        send("第二条指令")  # 连续重复不重复记录
        history = fake_config["input_history"]
        assert history == ["第一条指令", "第二条指令"], history
        # 指令序列独立维护，不混入聊天历史档案
        assert pet.chat_thread.history == []

        # 翻阅：↑ 回溯、↓ 前进、翻过最新恢复草稿
        pet.input_box.setPlainText("正在输入的草稿")
        pet._navigate_input_history(-1)
        assert pet.input_box.toPlainText() == "第二条指令"
        pet._navigate_input_history(-1)
        assert pet.input_box.toPlainText() == "第一条指令"
        pet._navigate_input_history(-1)  # 到顶不再往前
        assert pet.input_box.toPlainText() == "第一条指令"
        pet._navigate_input_history(1)
        assert pet.input_box.toPlainText() == "第二条指令"
        pet._navigate_input_history(1)
        assert pet.input_box.toPlainText() == "正在输入的草稿", "草稿未恢复"

        # 发送后翻阅状态复位
        send("第三条")
        assert pet._input_nav_index is None
        pet._navigate_input_history(-1)
        assert pet.input_box.toPlainText() == "第三条"

        # 容量上限 50
        fake_config["input_history"] = [f"旧指令{i}" for i in range(50)]
        send("新指令")
        assert len(fake_config["input_history"]) == 50
        assert fake_config["input_history"][-1] == "新指令"
        assert fake_config["input_history"][0] == "旧指令1"

        # ---- 3. 发送失败回填（等同按一下↑）----
        pet.input_box.setPlainText("会失败的消息")
        pet.input_box.pending_image_b64 = "aGVsbG8="
        pet.input_box.pending_image_mime = "image/png"
        pet.on_image_pasted()
        pet.send_msg()
        pet.type_timer.stop()
        pet.is_typing = False
        assert pet.input_box.toPlainText() == ""  # 发送时清空
        pet.chat_thread.send_failed.emit("会失败的消息")
        assert pet.input_box.toPlainText() == "会失败的消息", "失败消息未回填"
        assert pet.input_box.pending_image_b64 == "aGVsbG8=", "图片附件未回填"
        assert pet.image_hint.isVisible()

        # 后台隐藏指令失败不回填、不清草稿
        pet.input_box.setPlainText("用户草稿")
        pet.chat_thread.send_failed.emit("【系统后台强制指令：用户正在播放：xxx】")
        assert pet.input_box.toPlainText() == "用户草稿"

        pet.close()
        pet.deleteLater()
        app.processEvents()
    finally:
        oc.load_config = old_loader
        oc.save_config = old_saver
        calendar_service_module.save_config = old_service_saver
        ebook_dialog_module._cleanup_pending_ebook_deletions = old_ebook_cleanup
    print("INPUT_HISTORY_OK")


if __name__ == "__main__":
    main()
