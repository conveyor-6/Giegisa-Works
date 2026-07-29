import sys
import os
import random
import time
import re
import json
import base64
import traceback
import urllib.request
import urllib.error
import calendar as _pycalendar
from datetime import datetime, date, timedelta
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QMenu, QLineEdit, QVBoxLayout, QHBoxLayout, QDialog, QListWidget, QPushButton, QListWidgetItem, QTextEdit, QMessageBox, QFormLayout, QSpinBox, QColorDialog, QComboBox, QGroupBox, QFileDialog, QTimeEdit, QSizePolicy, QInputDialog, QSystemTrayIcon, QCheckBox, QGridLayout, QDateEdit, QScrollArea)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QPoint, QTime, QByteArray, QBuffer, QIODevice, QDate
from PyQt6.QtGui import QPixmap, QColor, QAction, QCursor, QIcon, QImage
from config import BASE_DIR, PIC_DIR, CONFIG_FILE, HISTORY_FILE, NOTES_FILE, DEFAULT_CONFIG, LOAD_WARNINGS, safe_json_save, load_config, save_config, flush_config_if_dirty
from core.utils import *
from ui import MENU_QSS, ImageBubble, ResponsiveListWidget, DraggableListWidget, ChatInputBox, FocusOverlay, InputDialog
from api import gemini_rest_generate, openai_chat
from threads import ChatThread, TriviaThread, IdleChatThread, RandomEventThread, DataRetrievalThread, ItemRetrievalThread, ImageFetchThread


# ============================================================
#  非模态弹窗封装
#  旧方案使用 exec() 模态弹窗，在 Windows 上有两个顽疾：
#  (1) 模态冻结父面板，用户无法拖动/点击（“窗口阻滞”）；
#  (2) 与置顶 Tool 父窗口的 z-order 竞争，弹窗有概率被压到父面板
#      下方，造成整个应用无法点击。
#  以下弹窗全部改为：父窗口的子窗口 + WindowStaysOnTopHint 置顶
#  （实测仅靠父子关系仍偶发被压，置顶保证新窗口恒在最上层）+
#  非模态 show()（父面板保持可拖动可交互，即使层级异常也不会
#  出现“点不动”的死锁），结果通过回调返回。回调内的异常被捕获
#  并打印，避免 PyQt6 对未处理异常直接 qFatal 导致桌宠闪退。
# ============================================================

def _safe_callback(func, *args):
    """弹窗回调统一入口：任何未处理异常都不应杀死整个桌宠进程。"""
    if func is None:
        return
    try:
        func(*args)
    except Exception:
        traceback.print_exc()


def _show_popup(dlg):
    """以非模态方式弹出子窗口。

    置顶（StayOnTop）+ 父窗口子窗口双保险：实测中仅靠父子关系，
    Windows 上仍偶发新弹窗被压在大面板下方；非模态设计保证即便
    层级异常也不会卡死，置顶则保证每个新窗口都显示在最上层。
    注意 QMessageBox 在 show() 时会重置窗口标志，置顶必须在显示
    之后设置（设置后窗口会短暂隐藏，需再次 show）。
    """
    dlg.setModal(False)
    dlg.setWindowModality(Qt.WindowModality.NonModal)
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.show()
    dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def ask_yes_no(parent, title, text, on_yes, on_no=None):
    """非模态“是/否”确认框。点“是”执行 on_yes；点“否”、关闭窗口或
    按 Esc 均执行 on_no。回调统一在窗口关闭后的 finished 阶段分发，
    保证有且只有一次。"""
    box = QMessageBox(QMessageBox.Icon.Question, title, text,
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                      parent)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    state = {"yes": False}

    def _clicked(btn):
        # buttonClicked 先于 finished 触发，只记录选择，不直接回调。
        state["yes"] = (box.standardButton(btn)
                        == QMessageBox.StandardButton.Yes)

    def _finished(_result):
        _safe_callback(on_yes if state["yes"] else on_no)

    box.buttonClicked.connect(_clicked)
    box.finished.connect(_finished)
    return _show_popup(box)


def show_info(parent, title, text, on_close=None):
    """非模态信息提示（QMessageBox.information 的替代）。"""
    box = QMessageBox(QMessageBox.Icon.Information, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    if on_close is not None:
        box.finished.connect(lambda _result: _safe_callback(on_close))
    return _show_popup(box)


def show_warning(parent, title, text, on_close=None):
    """非模态警告提示（QMessageBox.warning 的替代）。"""
    box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    if on_close is not None:
        box.finished.connect(lambda _result: _safe_callback(on_close))
    return _show_popup(box)


def show_critical(parent, title, text, on_close=None):
    """非模态错误提示（QMessageBox.critical 的替代）。"""
    box = QMessageBox(QMessageBox.Icon.Critical, title, text,
                      QMessageBox.StandardButton.Ok, parent)
    if on_close is not None:
        box.finished.connect(lambda _result: _safe_callback(on_close))
    return _show_popup(box)


class _InputPopup(QDialog):
    """通用非模态输入小窗：单行文本 / 多行文本 / 下拉选择共用骨架。"""

    def __init__(self, parent, title, label, editor, get_value, on_accept):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.editor = editor
        self._get_value = get_value
        self._on_accept = on_accept
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(label))
        lay.addWidget(editor)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _accept(self):
        value = self._get_value()
        on_accept = self._on_accept
        # 先关闭自身再回调——回调里可能刷新/重建父面板列表。
        self.accept()
        _safe_callback(on_accept, value)


def ask_text(parent, title, label, on_accept, text=''):
    """非模态单行输入。用户点“确定”后以输入文本调用 on_accept(value)。"""
    editor = QLineEdit(text or '')
    editor.selectAll()
    dlg = _InputPopup(parent, title, label, editor, editor.text, on_accept)
    return _show_popup(dlg)


def ask_multiline(parent, title, label, on_accept, text=''):
    """非模态多行输入。用户点“确定”后以输入文本调用 on_accept(value)。"""
    editor = QTextEdit()
    editor.setPlainText(text or '')
    editor.setMinimumSize(320, 140)
    dlg = _InputPopup(parent, title, label, editor, editor.toPlainText, on_accept)
    return _show_popup(dlg)


def ask_item(parent, title, label, items, on_accept, current=0):
    """非模态下拉单选。用户点“确定”后以选中项文本调用 on_accept(value)。"""
    editor = QComboBox()
    editor.addItems([str(item) for item in items])
    if 0 <= current < len(items):
        editor.setCurrentIndex(current)
    dlg = _InputPopup(parent, title, label, editor, editor.currentText, on_accept)
    return _show_popup(dlg)
