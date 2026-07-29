"""非模态弹窗 helper 与 bug 清单回归测试。

覆盖 260728-桌宠现有bug.txt 中已修复的问题：
- 便签“移动”选择分组闪退（input_item_box 调用了 Qt6 不存在的
  QInputDialog.setCurrentIndex，PyQt6 对未处理异常直接 qFatal）
- 阅读舱“修改分类”等输入弹窗
- 待办/打卡删除确认、记忆档案快捷清理等“窗口阻滞/被压”
  （exec() 模态弹窗冻结父面板 + 置顶竞争遮挡）
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

import oc
from dialogs.common import (ask_item, ask_multiline, ask_text, ask_yes_no,
                            show_info)


def _click_ok(dialog):
    for button in dialog.findChildren(QPushButton):
        if button.text() == "确定":
            button.click()
            return
    raise AssertionError("弹窗里没有“确定”按钮")


def assert_popup_contract(dialog, parent):
    """所有非模态弹窗的统一契约：不冻结父窗口、置顶显示在最上层。"""
    assert dialog.parent() is parent
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    # 子窗口 + 置顶双保险：实测仅靠父子关系仍偶发被大面板压住
    assert dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert parent.isEnabled()
    assert QApplication.activeModalWidget() is None


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    parent = QWidget()
    parent.show()

    # --- ask_item：便签“移动”闪退的原 crash 路径 ---
    picked = []
    dlg = ask_item(parent, "移动便签", "请选择目标分组:", ["默认便签", "工作"],
                   picked.append, current=1)
    assert_popup_contract(dlg, parent)
    assert dlg.editor.currentText() == "工作"
    dlg.editor.setCurrentIndex(0)
    _click_ok(dlg)
    assert picked == ["默认便签"], picked

    # --- ask_text：初始值 + 回调 ---
    typed = []
    dlg = ask_text(parent, "书架分类", "分类名称：", typed.append, text="默认书架")
    assert_popup_contract(dlg, parent)
    assert dlg.editor.text() == "默认书架"
    dlg.editor.setText("  新分类  ")
    _click_ok(dlg)
    assert typed == ["  新分类  "], typed

    # --- ask_multiline ---
    noted = []
    dlg = ask_multiline(parent, "添加批注", "批注内容：", noted.append, text="旧批注")
    assert_popup_contract(dlg, parent)
    dlg.editor.setPlainText("多行\n批注")
    _click_ok(dlg)
    assert noted == ["多行\n批注"], noted

    # --- ask_yes_no：是/否/关闭三条路径 ---
    calls = []
    box = ask_yes_no(parent, "确认删除", "确定删除吗？",
                     lambda: calls.append("yes"), lambda: calls.append("no"))
    assert_popup_contract(box, parent)
    box.button(QMessageBox.StandardButton.No).click()
    assert calls == ["no"], calls

    box = ask_yes_no(parent, "确认删除", "确定删除吗？",
                     lambda: calls.append("yes"), lambda: calls.append("no"))
    box.button(QMessageBox.StandardButton.Yes).click()
    assert calls == ["no", "yes"], calls

    # 右上角 X 关闭（或 Esc）同样走 on_no，有且只有一次。
    box = ask_yes_no(parent, "确认删除", "确定删除吗？",
                     lambda: calls.append("yes"), lambda: calls.append("no"))
    box.close()
    app.processEvents()
    assert calls == ["no", "yes", "no"], calls

    # 回调抛异常不得拖垮进程（PyQt6 默认 qFatal 闪退，helper 必须兜底）。
    box = ask_yes_no(parent, "确认", "会抛异常的回调",
                     lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    box.button(QMessageBox.StandardButton.Yes).click()  # 不抛出即通过

    # --- show_info：非模态 + on_close ---
    closed = []
    box = show_info(parent, "清理完成", "已成功清理 3 组历史记录。",
                    on_close=lambda: closed.append(True))
    assert_popup_contract(box, parent)
    box.button(QMessageBox.StandardButton.Ok).click()
    assert closed == [True], closed

    app.processEvents()
    print("POPUP_HELPERS_OK")


if __name__ == "__main__":
    main()
