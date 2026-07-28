"""气泡布局与系统提示回归测试。

覆盖网友反馈的三点：
1. 气泡文字增长时桌宠图像位置骤然偏移 → 锚定后图像屏幕坐标恒定
2. 文字过长显示不全 → 窗口夹回屏幕内 + 超长回复拆成连续气泡
3. API 卡顿提示显性气泡挤掉正式回答 → 改回静默（写历史不弹气泡）
"""
import json
import os
import sys
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

import oc
import core.calendar_service as calendar_service_module
import dialogs.ebook as ebook_dialog_module


class _FakeChatThread:
    def __init__(self):
        self.history = []
        self.sent = []

    def save_history(self):
        pass

    def isRunning(self):
        return False

    def send_message(self, *args, **kwargs):
        self.sent.append(args)


def _image_anchor(pet):
    """桌宠图像在屏幕上的“底边中点”坐标。"""
    label = pet.pet_label
    center = label.mapToGlobal(label.rect().center())
    bottom = label.mapToGlobal(label.rect().bottomLeft()).y()
    return center.x(), bottom


def main():
    app = QApplication.instance() or QApplication(sys.argv)

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

        # ---- 1. 锚定+定宽：气泡从无到有、从短到长，图像坐标不动、宽度恒定 ----
        pet.move(500, 300)
        assert pet.chat_bubble.width() == fake_config["bubble_width"], "气泡未按配置定宽"
        before = _image_anchor(pet)
        win_w0 = pet.width()
        pet.chat_bubble.setText("短气泡")
        pet.chat_bubble.show()
        pet.adjustSize()
        app.processEvents()
        mid = _image_anchor(pet)
        pet.chat_bubble.setText("很长的气泡内容，" * 30)
        pet.adjustSize()
        app.processEvents()
        after = _image_anchor(pet)
        assert pet.width() == win_w0, (win_w0, pet.width())
        for a, b in ((before, mid), (mid, after)):
            assert abs(a[0] - b[0]) <= 2, (a, b)
            assert abs(a[1] - b[1]) <= 2, (a, b)

        # ---- 1b. 延迟锚定：resize 不同步移动窗口，锚点在事件循环中统一生效 ----
        pet.chat_bubble.hide()
        pet.adjustSize()
        app.processEvents()
        pet.move(500, 300)
        app.processEvents()
        base = _image_anchor(pet)
        y_before = pet.y()
        pet.chat_bubble.setText("延迟锚定测试，" * 10)
        pet.chat_bubble.show()
        pet.adjustSize()
        assert pet.y() == y_before, "resizeEvent 里不应再同步移动窗口（跳帧根因）"
        app.processEvents()
        now = _image_anchor(pet)
        assert abs(base[0] - now[0]) <= 2 and abs(base[1] - now[1]) <= 2, (base, now)
        # 隐藏→再增长循环后锚点依旧稳定
        pet.chat_bubble.hide()
        pet.adjustSize()
        app.processEvents()
        pet.chat_bubble.setText("第二轮增长，" * 14)
        pet.chat_bubble.show()
        pet.adjustSize()
        app.processEvents()
        now2 = _image_anchor(pet)
        assert abs(base[0] - now2[0]) <= 2 and abs(base[1] - now2[1]) <= 2, (base, now2)

        # ---- 2. 上方空间不足时：气泡按可用空间截顶，不顶出屏幕 ----
        pet.move(500, 200)
        app.processEvents()
        pet.chat_bubble.setText("顶部长文本，" * 60)
        pet.chat_bubble.show()
        pet.chat_bubble.adjustSize()
        app.processEvents()
        avail = app.primaryScreen().availableGeometry()
        space = (pet.y() - 6) - avail.top()
        assert pet.chat_bubble.height() <= space + 2, \
            (pet.chat_bubble.height(), space)
        assert pet.chat_bubble.y() >= avail.top() - 2, \
            (pet.chat_bubble.y(), avail.top())

        # ---- 2b. 桌宠停在屏幕底边（底部超出屏幕）时不得被夹持上拉 ----
        pet.chat_bubble.hide()
        pet.adjustSize()
        app.processEvents()
        park_y = avail.bottom() - pet.height() + 40  # 底部探出屏幕 40px
        pet.move(500, park_y)
        app.processEvents()
        parked = _image_anchor(pet)
        pet.chat_bubble.setText("底部长气泡，" * 20)
        pet.chat_bubble.show()
        pet.adjustSize()
        app.processEvents()
        grown = _image_anchor(pet)
        assert abs(parked[0] - grown[0]) <= 2, (parked, grown)
        assert abs(parked[1] - grown[1]) <= 2, (parked, grown)

        pet.chat_bubble.hide()
        pet.adjustSize()
        app.processEvents()

        # ---- 3. 超长回复拆分为连续气泡 ----
        pet.chat_thread = _FakeChatThread()
        long_reply = "【normal】" + "这是一段很长的回答。" * 120
        pet.handle_api_reply(long_reply)
        assert pet.is_typing
        assert len(pet.full_text) <= oc._BUBBLE_SPLIT_LEN, len(pet.full_text)
        assert len(pet._bubble_queue) >= 1, "超长回复没有拆出后续气泡"
        # 拼接还原：所有文字都在（忽略切分处的空白差异）
        joined = "".join([pet.full_text] + [t for t, _ in pet._bubble_queue])
        assert "这是一段很长的回答。" * 120 == joined.replace(" ", ""), len(joined)
        pet.type_timer.stop()
        pet.is_typing = False
        pet._bubble_queue.clear()
        pet.chat_bubble.hide()

        # ---- 4. API 卡顿提示静默：写历史、不弹气泡、不挤掉回答 ----
        pet.handle_api_lag(12.34)
        assert len(pet.chat_thread.history) == 2
        assert "卡顿" in pet.chat_thread.history[0]["content"]
        assert "12.3" in pet.chat_thread.history[1]["content"]
        assert not pet.chat_bubble.isVisible(), "卡顿提示不应弹出气泡"
        assert not pet.is_typing, "卡顿提示不应打断打字状态"
        assert not pet._bubble_queue, "卡顿提示不应进入气泡队列"

        # 普通系统事件仍应弹气泡（行为不回归）
        pet.inject_system_event("系统：测试事件", "【normal】正常提示")
        assert pet.is_typing or pet.chat_bubble.isVisible()
        pet.type_timer.stop()
        pet.is_typing = False
        pet._bubble_queue.clear()

        # ---- 5. 发送新消息时不得闪现/续播上一轮回复 ----
        pet.input_box.setPlainText("新消息")
        pet.handle_api_reply("【normal】上一轮回复的内容", 60000)  # 极慢打字，模拟正在播放
        assert pet.is_typing
        pet.send_msg()
        # 旧一轮立即终结：旧文本不进新气泡、队列清空、只播放占位符“...”
        assert pet.full_text == "...", pet.full_text[:40]
        assert "上一轮回复的内容" not in pet.chat_bubble.text()
        assert not any("上一轮" in t for t, _ in pet._bubble_queue)
        assert pet.chat_thread.sent, "新消息未发送到聊天线程"
        pet.type_timer.stop()
        pet.is_typing = False
        pet._bubble_queue.clear()
        pet.chat_bubble.hide()

        # ---- 6. 独立气泡子窗口的行为契约 ----
        from PyQt6.QtCore import QPoint, QPointF, Qt as _Qt
        from PyQt6.QtGui import QMouseEvent as _QME

        def _ev(etype, local, button, buttons):
            local = QPointF(local)
            return _QME(etype, local, QPointF(1000, 1000) + local,
                        button, buttons, _Qt.KeyboardModifier.NoModifier)

        pet.chat_bubble.setText("跟随测试")
        pet.chat_bubble.show()
        app.processEvents()
        # 6.1 气泡贴在图像正上方：水平中心对齐、底边贴近本体顶边
        assert abs(pet.chat_bubble.x() + pet.chat_bubble.width() // 2
                   - (pet.x() + pet.width() // 2)) <= 2
        assert abs(pet.chat_bubble.y() + pet.chat_bubble.height()
                   - (pet.y() - 6)) <= 2
        # 6.2 拖动本体（经气泡转发），气泡实时跟随
        pre_pos = pet.pos()
        pet.chat_bubble.mousePressEvent(_ev(_QME.Type.MouseButtonPress,
                                            QPoint(10, 10), _Qt.MouseButton.LeftButton,
                                            _Qt.MouseButton.LeftButton))
        pet.chat_bubble.mouseMoveEvent(_ev(_QME.Type.MouseMove,
                                           QPoint(60, 70), _Qt.MouseButton.NoButton,
                                           _Qt.MouseButton.LeftButton))
        app.processEvents()
        assert pet.pos() == pre_pos + QPoint(50, 60), (pre_pos, pet.pos())
        assert abs(pet.chat_bubble.x() + pet.chat_bubble.width() // 2
                   - (pet.x() + pet.width() // 2)) <= 2, "气泡未跟随本体"
        pet.chat_bubble.mouseReleaseEvent(_ev(_QME.Type.MouseButtonRelease,
                                              QPoint(60, 70), _Qt.MouseButton.LeftButton,
                                              _Qt.MouseButton.NoButton))
        assert not pet.is_following
        # 6.3 气泡上的左键松开等效点击本体：可见气泡被收起
        assert not pet.chat_bubble.isVisible(), "点击气泡未触发本体的收起逻辑"
        # 6.4 本体隐藏时气泡跟随隐藏
        pet.chat_bubble.setText("再显示")
        pet.chat_bubble.show()
        app.processEvents()
        pet.hide()
        app.processEvents()
        assert not pet.chat_bubble.isVisible(), "本体隐藏后气泡仍可见"
        pet.show()
        app.processEvents()
        pet.chat_bubble.hide()

        pet.close()
        pet.deleteLater()
        app.processEvents()
    finally:
        oc.load_config = old_loader
        oc.save_config = old_saver
        calendar_service_module.save_config = old_service_saver
        ebook_dialog_module._cleanup_pending_ebook_deletions = old_ebook_cleanup

    # ---- 5. 拆分函数单元行为 ----
    assert oc._split_bubble_text("") == []
    assert oc._split_bubble_text("短") == ["短"]
    text = "第一句。" * 200
    chunks = oc._split_bubble_text(text)
    assert all(len(c) <= oc._BUBBLE_SPLIT_LEN for c in chunks)
    assert "".join(chunks) == text
    assert all(c.endswith("。") for c in chunks[:-1]), "应优先在句末断开"
    hard = "无标点" * 300
    hard_chunks = oc._split_bubble_text(hard)
    assert all(len(c) <= oc._BUBBLE_SPLIT_LEN for c in hard_chunks)
    assert "".join(hard_chunks) == hard

    print("BUBBLE_LAYOUT_OK")


if __name__ == "__main__":
    main()
