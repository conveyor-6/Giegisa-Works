"""静默阅读舱：书架、阅读器、标记、自动阅读与阅读目标。"""

import gc
import html
import hashlib
import json
import os
import random
import re
import shutil
import stat
import time
from datetime import date, datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QPoint, QSize, QTime, QTimer, pyqtSignal, QUrl
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence,
    QPainter, QPixmap,
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextImageFormat,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox,
    QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QStackedLayout, QStackedWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
    QTimeEdit,
    QTextEdit, QToolButton, QVBoxLayout, QWidget, QInputDialog,
)

from config import BASE_DIR, LOAD_WARNINGS, save_config
from core.ebook import (
    IMAGE_OBJECT, SUPPORTED_EBOOKS, clean_text, decode_bytes, html_to_text, load_cache,
    parse_ebook, save_cache,
)
from core.utils import new_id, checkin_done_on
from .common import (ask_multiline, ask_text, ask_yes_no,
                     show_critical, show_info, show_warning)

EBOOK_DIR = os.path.join(BASE_DIR, "ebook_library")
_PENDING_CLEANUP_FILE = os.path.join(EBOOK_DIR, "_pending_cleanup.json")
HIGHLIGHT_COLORS = ["#dbeafe", "#fecaca", "#fef3c7", "#dcfce7", "#cffafe", "#ddd6fe"]
VISUAL_SETTING_KEYS = (
    "font", "font_size", "line_spacing", "letter_spacing", "text_color",
    "background_color", "background_image", "background_mode",
    "background_opacity", "alignment", "first_indent",
)


def _file_fingerprint(path):
    if not path or not os.path.isfile(path):
        return ""
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relocate_managed_book(book):
    """把旧安装目录中的绝对路径迁移到当前 EXE 的书库，保留原记录。"""
    path = os.path.abspath(book.get("path", "")) if book.get("path") else ""
    if path and os.path.isfile(path):
        return path
    book_id = str(book.get("id", ""))
    candidates = []
    relpath = book.get("library_relpath", "")
    if relpath:
        candidates.append(os.path.join(EBOOK_DIR, relpath))
    if book_id:
        folder = os.path.join(EBOOK_DIR, book_id)
        suffix = Path(path).suffix.lower() if path else ""
        if suffix:
            candidates.append(os.path.join(folder, "book" + suffix))
        if os.path.isdir(folder):
            candidates.extend(
                os.path.join(folder, name)
                for name in os.listdir(folder)
                if name.lower().startswith("book.")
                and Path(name).suffix.lower() in SUPPORTED_EBOOKS)
    for candidate in candidates:
        if os.path.isfile(candidate):
            resolved = os.path.abspath(candidate)
            book["path"] = resolved
            book["managed"] = True
            book["library_relpath"] = os.path.relpath(resolved, EBOOK_DIR)
            book["asset_dir"] = os.path.join(
                EBOOK_DIR, book_id, "assets")
            return resolved
    return path


def _format_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024


def _make_tree_writable(path):
    """去掉目录树内所有文件/子目录的只读属性（尽力而为）。

    shutil.copy2 会把源文件的只读属性一起复制进书库；Windows 上
    rmtree 无法删除只读文件（WinError 5），这正是删书后目录残留、
    且重启后重试依然失败的根因。删除前先统一去只读。
    """
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass


def _copy_book_file(src, dst):
    """复制书籍/资源文件并保证副本可写。

    shutil.copy2 会连同只读属性一起复制；只读副本日后删除时会在
    Windows 上触发 WinError 5 残留，因此复制后立刻去掉只读位。
    """
    shutil.copy2(src, dst)
    try:
        os.chmod(dst, stat.S_IWRITE)
    except OSError:
        pass


def _force_delete_dir(path, attempts=3, delay=0.05):
    """尽力删除目录。先去只读属性再 rmtree；Windows 上若文件仍被占用，
    回退到 MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)，由内核在下次启动时清理。"""
    if not os.path.isdir(path):
        return
    _make_tree_writable(path)
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            time.sleep(delay)
    # shutil.rmtree 彻底失败 → Windows 内核级兜底
    if os.name == "nt":
        try:
            _schedule_reboot_delete(path)
        except Exception:
            pass


def _schedule_reboot_delete(path):
    """调用 Win32 MoveFileExW 将目录标记为'下次启动删除'。
    此操作由 Windows Session Manager 写入 PendingFileRenameOperations
    注册表键，在内核加载用户态进程之前执行，不受文件锁影响。
    仅接受 EBOOK_DIR 子树内的路径，拒绝其他任何输入。"""
    # 安全阀：绝对拒绝 EBOOK_DIR 之外的路径
    ebook_root = os.path.abspath(EBOOK_DIR)
    target = os.path.abspath(path)
    if os.path.commonpath((ebook_root, target)) != ebook_root:
        return
    import ctypes
    from ctypes import wintypes
    MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.MoveFileExW.restype = wintypes.BOOL
    kernel32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    # 自底向上遍历：先删文件，再删子目录，最后删顶层
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            filepath = os.path.join(root, name)
            kernel32.MoveFileExW(filepath, None, MOVEFILE_DELAY_UNTIL_REBOOT)
        for name in dirs:
            dirpath = os.path.join(root, name)
            kernel32.MoveFileExW(dirpath, None, MOVEFILE_DELAY_UNTIL_REBOOT)
    kernel32.MoveFileExW(path, None, MOVEFILE_DELAY_UNTIL_REBOOT)


def _cleanup_pending_ebook_deletions(library=None):
    """启动时清理电子书书库中的历史残留。

    两阶段清理：
    1. 处理 _pending_cleanup.json 中登记的目录
    2. 扫描 EBOOK_DIR 下所有 *.deleted.* 目录（兜底，防止标记文件写入失败的残留）
    3. 若传入当前书籍列表，再清理不再被任何书籍引用的孤儿目录
       （旧版“相同书籍合并”或删除失败留下的空壳、assets 图片残余）
    """
    marker = _PENDING_CLEANUP_FILE
    targets = []  # (path, source) tuples

    # ---- 阶段 1：从标记文件读取 ----
    if os.path.isfile(marker):
        try:
            with open(marker, "r", encoding="utf-8") as f:
                entries = json.load(f)
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                folder = entry.get("folder", "") if isinstance(entry, dict) else str(entry)
                if folder:
                    targets.append((folder, entry.get("original", "") if isinstance(entry, dict) else ""))
        except Exception:
            pass
        # 无论如何先删掉旧标记；如果下面清理失败会重新写入
        try:
            os.remove(marker)
        except OSError:
            pass

    # ---- 阶段 2：扫描 EBOOK_DIR 下的 .deleted. 残留 ----
    if os.path.isdir(EBOOK_DIR):
        try:
            for name in os.listdir(EBOOK_DIR):
                if ".deleted." in name:
                    full = os.path.join(EBOOK_DIR, name)
                    if os.path.isdir(full):
                        targets.append((full, ""))
        except OSError:
            pass

    # ---- 去重后逐个清理 ----
    root = os.path.abspath(EBOOK_DIR)
    seen = set()
    remaining = []
    for target, source in targets:
        target = os.path.abspath(target) if target else ""
        if not target or target in seen:
            continue
        seen.add(target)
        if (os.path.commonpath((root, target)) != root
                or not os.path.isdir(target)):
            continue
        try:
            _force_delete_dir(target)
        except Exception:
            remaining.append({"folder": target, "original": source or target})

    # ---- 重写标记（仅当仍有残留时） ----
    if remaining:
        try:
            os.makedirs(EBOOK_DIR, exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump(remaining, f, ensure_ascii=False)
        except Exception:
            pass

    # ---- 阶段 3：孤儿目录清扫 ----
    if library is not None:
        _sweep_orphan_book_dirs(library)


def _sweep_orphan_book_dirs(library):
    """清理 EBOOK_DIR 下不再被任何书籍记录引用的目录。

    只处理“长得像书籍目录”的文件夹（含 book.* 文件、assets 子目录，
    或为空目录），避免误删用户自己放进去的其他文件。
    """
    if not os.path.isdir(EBOOK_DIR):
        return
    if LOAD_WARNINGS:
        # 配置未能干净加载（可能回退成默认空书库）。此时书库列表不可信，
        # 绝不能据此清理目录，否则会把用户真实书籍当成孤儿误删。
        return
    live_ids = {str(book.get("id")) for book in library
                if isinstance(book, dict) and book.get("id")}
    root = os.path.abspath(EBOOK_DIR)
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder) or name in live_ids or ".deleted." in name:
            continue
        try:
            entries = os.listdir(folder)
        except OSError:
            continue
        looks_like_book = (
            not entries
            or "assets" in entries
            or any(entry.lower().startswith("book.") for entry in entries))
        if looks_like_book:
            _force_delete_dir(folder)


class ReaderTextBrowser(QTextBrowser):
    image_double_clicked = pyqtSignal(str)

    def mouseDoubleClickEvent(self, a0):
        cursor = self.cursorForPosition(a0.position().toPoint())
        image_format = cursor.charFormat().toImageFormat()
        if image_format.isValid() and image_format.name():
            self.image_double_clicked.emit(image_format.name())
            return
        super().mouseDoubleClickEvent(a0)


class ImagePreviewDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(path)
        label.setPixmap(pixmap)
        label.resize(pixmap.size())
        area.setWidget(label)
        layout.addWidget(area)


class AutoReadControls(QDialog):
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()

    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self._drag_offset = QPoint()
        self.setWindowTitle("自动阅读")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setMinimumSize(190, 105)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)
        self.drag_handle = QLabel("自动阅读 · 拖动此处移动")
        self.drag_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drag_handle.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.drag_handle.setStyleSheet("font-weight:bold;color:#3979a8;")
        root.addWidget(self.drag_handle)
        row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸")
        self.pause_btn.setToolTip("暂停 / 继续")
        stop_btn = QPushButton("⏹")
        stop_btn.setToolTip("停止并返回电子书")
        self.pause_btn.clicked.connect(self.pause_requested)
        stop_btn.clicked.connect(self.stop_requested)
        row.addWidget(self.pause_btn)
        row.addWidget(stop_btn)
        root.addLayout(row)

    def place_near(self, pet):
        self.adjustSize()
        screen = pet.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        x = pet.x() - 10
        y = pet.y() - self.height() - 10
        if available is not None:
            x = max(available.left(), min(x, available.right() - self.width() + 1))
            y = max(available.top(), min(y, available.bottom() - self.height() + 1))
        self.move(x, y)

    def mousePressEvent(self, a0):
        if a0.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = a0.globalPosition().toPoint() - self.frameGeometry().topLeft()
            a0.accept()
            return
        super().mousePressEvent(a0)

    def mouseMoveEvent(self, a0):
        if a0.buttons() & Qt.MouseButton.LeftButton and not self._drag_offset.isNull():
            self.move(a0.globalPosition().toPoint() - self._drag_offset)
            a0.accept()
            return
        super().mouseMoveEvent(a0)

    def mouseReleaseEvent(self, a0):
        self._drag_offset = QPoint()
        super().mouseReleaseEvent(a0)


class EbookReaderDialog(QDialog):
    PANEL_TITLES = ["目录", "夜间", "视觉", "搜索", "标记", "自动", "其它"]
    PANEL_WIDTH = 250
    READING_MIN_WIDTH = 350

    def __init__(self, parent_pet, book):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.book = book
        self.settings = self.pet.config.setdefault("ebook_settings", {})
        self._initialize_visual_profiles()
        if not self.pet.config.get("ebook_reader_ui_v2_migrated"):
            # 旧版默认 18pt 且横向扁平。只迁移一次，不覆盖用户之后的选择。
            if int(self.settings.get("font_size", 18)) == 18:
                self.settings["font_size"] = 10
            self.pet.config["ebook_reader_ui_v2_migrated"] = True
        self.parsed = None
        self.pages = []
        self.current_page = 0
        self.current_chapter = 0
        self.session_seconds = 0
        self.session_chars = 0
        self._daily_recorded_chars = 0
        self._last_page_end = int(book.get("position", 0) or 0)
        self._resize_pending = False
        self._auto_position = 0
        self._auto_running = False
        self._auto_paused = False
        self._auto_controls = None
        self._auto_generation = 0
        self._closed_reported = False

        self.setWindowTitle(f"◇ 静默阅读舱 ◇  {book.get('title', '')}")
        self.setMinimumSize(380, 480)
        self.resize(520, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._build_ui()
        self._load_book()
        QTimer.singleShot(0, self._apply_window_theme)

        self.session_timer = QTimer(self)
        self.session_timer.timeout.connect(self._reading_second)
        self.session_timer.start(1000)
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._auto_tick)
        self.eye_timer = QTimer(self)
        self.eye_timer.timeout.connect(self._eye_reminder)
        self._update_eye_timer()

        QTimer.singleShot(1200, self._show_open_notice)

    def _initialize_visual_profiles(self):
        default_day = {
            "font": self.settings.get("font", "Microsoft YaHei"),
            "font_size": int(self.settings.get("font_size", 10)),
            "line_spacing": float(self.settings.get("line_spacing", 1.5)),
            "letter_spacing": float(self.settings.get("letter_spacing", 0)),
            "text_color": self.settings.get("text_color", "#287cc1"),
            "background_color": self.settings.get("background_color", "#eef8ff"),
            "background_image": self.settings.get("background_image", ""),
            "background_mode": self.settings.get("background_mode", "适应"),
            "background_opacity": int(self.settings.get("background_opacity", 100)),
            "alignment": int(self.settings.get("alignment", 3)),
            "first_indent": bool(self.settings.get("first_indent", True)),
        }
        default_night = dict(default_day)
        default_night.update({
            "text_color": "#e7f2ff",
            "background_color": "#18222d",
            "background_opacity": min(
                42, int(default_day.get("background_opacity", 100))),
        })
        self.settings.setdefault("day_profile", default_day)
        self.settings.setdefault("night_profile", default_night)
        active = (
            self.settings["night_profile"]
            if self.settings.get("night_mode", False)
            else self.settings["day_profile"])
        for key in VISUAL_SETTING_KEYS:
            if key in active:
                self.settings[key] = active[key]

    def _show_open_notice(self):
        if self._closed_reported or not self.parsed:
            return
        today = str(date.today())
        daily = self.pet.config.get("ebook_reading_daily", {}).get(today, {})
        goal_seconds = int(self.settings.get("daily_goal_minutes", 5)) * 60
        remaining = goal_seconds - int(daily.get("seconds", 0))
        # 即将触发达标提示时不再插入“阅读舱已展开”，避免两条气泡抢占。
        if not daily.get("goal_awarded") and remaining <= 30:
            return
        self.pet.show_bubble(
            f"【normal】阅读舱已展开。《{self.book.get('title', '')}》会从上次位置继续。")

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        self.panel = QWidget()
        self.panel.setFixedWidth(self.PANEL_WIDTH)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(4, 4, 4, 4)
        self.panel_title = QLabel("目录")
        self.panel_title.setStyleSheet("font-weight:bold;font-size:14px;color:#287cc1;")
        panel_layout.addWidget(self.panel_title)
        self.stack = QStackedWidget()
        panel_layout.addWidget(self.stack, stretch=1)
        root.addWidget(self.panel)
        self.panel.hide()

        tools = QVBoxLayout()
        tools.setSpacing(2)
        self.tool_buttons = []
        for index, (text, tip) in enumerate(zip(
                ("☷", "◐", "◉", "⌕", "★", "A", "⋯"), self.PANEL_TITLES)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setFixedSize(30, 30)
            button.clicked.connect(lambda checked=False, i=index: self.toggle_panel(i))
            tools.addWidget(button)
            self.tool_buttons.append(button)
        tools.addStretch()
        root.addLayout(tools)

        reading = QVBoxLayout()
        reading.setSpacing(2)
        header = QHBoxLayout()
        self.shelf_btn = QToolButton()
        self.shelf_btn.setText("☰")
        self.shelf_btn.setToolTip("返回书架")
        self.shelf_btn.clicked.connect(self.back_to_shelf)
        self.header_info = QLabel("")
        self.header_info.setStyleSheet("color:#6c8193;font-size:11px;font-weight:bold;")
        self.header_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.shelf_btn)
        header.addWidget(self.header_info, stretch=1)
        reading.addLayout(header)

        self.text = ReaderTextBrowser()
        self.text.setOpenLinks(False)
        self.text.setReadOnly(True)
        self.text.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text.customContextMenuRequested.connect(self._reader_menu)
        self.text.image_double_clicked.connect(self._open_image_preview)
        self.text.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if self.text.viewport() is not None:
            self.text.viewport().setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.text.viewport().setAutoFillBackground(False)
        self.reading_surface = QWidget()
        surface_stack = QStackedLayout(self.reading_surface)
        surface_stack.setContentsMargins(0, 0, 0, 0)
        surface_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.background_label = QLabel()
        self.background_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.background_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        surface_stack.addWidget(self.background_label)
        surface_stack.addWidget(self.text)
        # StackAll 会把“当前控件”提升到最上层；默认索引为 0 时底图会遮住正文。
        # 明确让可交互的正文层处于最上方，底图仍在其下方绘制。
        surface_stack.setCurrentWidget(self.text)
        reading.addWidget(self.reading_surface, stretch=1)

        nav = QHBoxLayout()
        self.prev_btn = QToolButton()
        self.prev_btn.setText("◀")
        self.prev_btn.clicked.connect(self.prev_page)
        self.chapter_label = QLabel("")
        self.chapter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chapter_label.setStyleSheet("font-weight:bold;color:#647b8f;")
        self.next_btn = QToolButton()
        self.next_btn.setText("▶")
        self.next_btn.clicked.connect(self.next_page)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.chapter_label, stretch=1)
        nav.addWidget(self.next_btn)
        reading.addLayout(nav)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(7)
        self.progress.setTextVisible(False)
        reading.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setStyleSheet("font-size:10px;color:#6c8193;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reading.addWidget(self.status)
        root.addLayout(reading, stretch=1)

        self._make_toc_panel()
        self._make_night_panel()
        self._make_visual_panel()
        self._make_search_panel()
        self._make_marks_panel()
        self._make_auto_panel()
        self._make_other_panel()
        self._sync_tool_button_colors()

    def _sync_tool_button_colors(self):
        """左侧工具列及翻页按钮的图标颜色：日间固定为黑色，不随操作系统
        明暗主题变化；夜间模式由阅读器主题样式表统一接管（置空回退）。"""
        night = bool(self.settings.get("night_mode", False))
        qss = "" if night else "color:#1a1a1a;"
        for button in self.tool_buttons + [self.shelf_btn, self.prev_btn, self.next_btn]:
            button.setStyleSheet(qss)

    def _panel_scroll(self):
        content = QWidget()
        content.setMinimumWidth(0)
        content.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(3, 3, 3, 3)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        area.setWidget(content)
        self.stack.addWidget(area)
        return layout

    def _make_toc_panel(self):
        layout = self._panel_scroll()
        self.toc_list = QListWidget()
        self.toc_list.itemActivated.connect(
            lambda item: self.goto_chapter(self.toc_list.row(item)))
        layout.addWidget(self.toc_list)

    def _make_night_panel(self):
        layout = self._panel_scroll()
        day = QPushButton("☀ 日间模式")
        night = QPushButton("🌙 夜间模式")
        day.clicked.connect(lambda: self._apply_preset(False))
        night.clicked.connect(lambda: self._apply_preset(True))
        layout.addWidget(day)
        layout.addWidget(night)
        self.night_status = QLabel("")
        self.night_status.setWordWrap(True)
        layout.addWidget(self.night_status)
        layout.addWidget(QLabel(
            "日间与夜间会分别保存字体、字号、文字颜色、背景和排版。"
            "夜间模式也会压暗整个阅读窗口。"))
        layout.addStretch()

    def _make_visual_panel(self):
        layout = self._panel_scroll()
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.font_combo = QFontComboBox()
        self.font_combo.setMinimumWidth(0)
        self.font_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.font_combo.setCurrentFont(QFont(self.settings.get("font", "Microsoft YaHei")))
        self.font_combo.currentFontChanged.connect(
            lambda font: self._set_setting("font", font.family()))
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 48)
        self.font_size.setValue(int(self.settings.get("font_size", 10)))
        self.font_size.valueChanged.connect(lambda value: self._set_setting("font_size", value, True))
        self.line_spacing = QDoubleSpinBox()
        self.line_spacing.setRange(0.8, 3.0)
        self.line_spacing.setSingleStep(0.1)
        self.line_spacing.setValue(float(self.settings.get("line_spacing", 1.5)))
        self.line_spacing.valueChanged.connect(lambda value: self._set_setting("line_spacing", value, True))
        self.letter_spacing = QDoubleSpinBox()
        self.letter_spacing.setRange(-2, 12)
        self.letter_spacing.setValue(float(self.settings.get("letter_spacing", 0)))
        self.letter_spacing.valueChanged.connect(lambda value: self._set_setting("letter_spacing", value, True))
        self.align_combo = QComboBox()
        self.align_combo.addItems(["左对齐", "居中", "右对齐", "两端对齐"])
        self.align_combo.setCurrentIndex(int(self.settings.get("alignment", 3)))
        self.align_combo.currentIndexChanged.connect(lambda value: self._set_setting("alignment", value))
        form.addRow("字体", self.font_combo)
        form.addRow("字号", self.font_size)
        form.addRow("行距", self.line_spacing)
        form.addRow("字距", self.letter_spacing)
        form.addRow("对齐", self.align_combo)
        layout.addLayout(form)
        text_color = QPushButton("文字颜色")
        text_color.clicked.connect(lambda: self._choose_color("text_color"))
        bg_color = QPushButton("背景颜色")
        bg_color.clicked.connect(lambda: self._choose_color("background_color"))
        bg_image = QPushButton("导入背景底图")
        bg_image.clicked.connect(self._choose_background)
        clear_bg = QPushButton("清除背景底图")
        clear_bg.clicked.connect(lambda: self._set_setting("background_image", ""))
        self.bg_mode = QComboBox()
        self.bg_mode.addItems(["平铺", "拉伸", "适应"])
        self.bg_mode.setCurrentText(self.settings.get("background_mode", "适应"))
        self.bg_mode.currentTextChanged.connect(lambda value: self._set_setting("background_mode", value))
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix("%")
        self.opacity.setValue(int(self.settings.get("background_opacity", 100)))
        self.opacity.valueChanged.connect(lambda value: self._set_setting("background_opacity", value))
        layout.addWidget(text_color)
        layout.addWidget(bg_color)
        layout.addWidget(bg_image)
        layout.addWidget(clear_bg)
        layout.addWidget(QLabel("底图显示"))
        layout.addWidget(self.bg_mode)
        layout.addWidget(QLabel("背景透明度"))
        layout.addWidget(self.opacity)
        layout.addStretch()

    def _make_search_panel(self):
        layout = self._panel_scroll()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索当前书籍…")
        self.search_input.returnPressed.connect(self.search_text)
        search_btn = QPushButton("搜索")
        search_btn.clicked.connect(self.search_text)
        self.search_results = QListWidget()
        self.search_results.setWordWrap(True)
        self.search_results.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.search_results.itemActivated.connect(self._goto_search_result)
        layout.addWidget(self.search_input)
        layout.addWidget(search_btn)
        layout.addWidget(self.search_results)

    def _make_marks_panel(self):
        layout = self._panel_scroll()
        bookmark_title = QLabel("—— 书签 ——")
        bookmark_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add_mark = QPushButton("★ 当前页添加书签")
        add_mark.clicked.connect(self.add_bookmark)

        highlight_title = QLabel("—— 高亮 ——")
        highlight_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.highlight_color_combo = QComboBox()
        for name, color in zip(
                ("浅蓝", "浅红", "浅黄", "浅绿", "浅青", "浅紫"),
                HIGHLIGHT_COLORS):
            self.highlight_color_combo.addItem(name, color)
        saved_color = self.settings.setdefault(
            "highlight_color", HIGHLIGHT_COLORS[0])
        saved_index = max(0, HIGHLIGHT_COLORS.index(saved_color)) if saved_color in HIGHLIGHT_COLORS else 0
        self.highlight_color_combo.setCurrentIndex(saved_index)
        self.highlight_color_combo.currentIndexChanged.connect(
            lambda: self._set_setting(
                "highlight_color", self.highlight_color_combo.currentData()))
        highlight = QPushButton("高亮选中文字")
        highlight.clicked.connect(self.highlight_selection)

        note_title = QLabel("—— 批注 ——")
        note_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        note = QPushButton("为选中文字添加批注")
        note.clicked.connect(self.annotate_selection)
        edit = QPushButton("修改选中书签 / 批注文字")
        edit.clicked.connect(self.edit_mark)
        recolor = QPushButton("修改选中高亮 / 批注颜色")
        recolor.clicked.connect(self.change_mark_color)
        self.mark_filter = QComboBox()
        self.mark_filter.addItems(["全部标记", "仅书签", "仅高亮", "仅批注"])
        self.mark_filter.currentIndexChanged.connect(self._refresh_marks)
        self.mark_list = QListWidget()
        self.mark_list.itemActivated.connect(self._goto_mark)
        delete = QPushButton("删除选中标记")
        delete.clicked.connect(self.delete_mark)
        layout.addWidget(bookmark_title)
        layout.addWidget(add_mark)
        layout.addWidget(highlight_title)
        layout.addWidget(QLabel("新高亮颜色"))
        layout.addWidget(self.highlight_color_combo)
        layout.addWidget(highlight)
        layout.addWidget(note_title)
        layout.addWidget(note)
        layout.addWidget(edit)
        layout.addWidget(recolor)
        layout.addWidget(self.mark_filter)
        layout.addWidget(self.mark_list)
        layout.addWidget(delete)

    def _make_auto_panel(self):
        layout = self._panel_scroll()
        self.auto_mode = QComboBox()
        self.auto_mode.addItems(["电子书卡拉 OK 模式", "Gisa 气泡朗读模式"])
        self.auto_speed = QSpinBox()
        self.auto_speed.setRange(1, 30)
        self.auto_speed.setValue(int(self.settings.get("auto_speed", 8)))
        self.auto_speed.setSuffix(" 字/秒")
        start = QPushButton("▶ 开始自动阅读")
        start.clicked.connect(self.start_auto_read)
        layout.addWidget(QLabel("阅读模式"))
        layout.addWidget(self.auto_mode)
        layout.addWidget(QLabel("速度"))
        layout.addWidget(self.auto_speed)
        layout.addWidget(start)
        layout.addStretch()

    def _make_other_panel(self):
        layout = self._panel_scroll()
        self.stats_label = QLabel("")
        self.stats_label.setWordWrap(True)
        self.trim_check = QCheckBox("裁剪多余空行和空格")
        self.trim_check.setChecked(bool(self.settings.get("trim_whitespace", True)))
        self.repair_check = QCheckBox("智能恢复硬换行句子")
        self.repair_check.setChecked(bool(self.settings.get("repair_sentences", True)))
        self.indent_check = QCheckBox("段落首行缩进")
        self.indent_check.setChecked(bool(self.settings.get("first_indent", True)))
        self.indent_check.toggled.connect(lambda value: self._set_setting("first_indent", value))
        self.eye_check = QCheckBox("启用视力保护提醒")
        self.eye_check.setChecked(bool(self.settings.get("eye_reminder", True)))
        self.eye_check.toggled.connect(lambda value: (self._set_setting("eye_reminder", value), self._update_eye_timer()))
        self.eye_minutes = QSpinBox()
        self.eye_minutes.setRange(5, 120)
        self.eye_minutes.setValue(int(self.settings.get("eye_minutes", 20)))
        self.eye_minutes.setSuffix(" 分钟")
        self.eye_minutes.valueChanged.connect(lambda value: (self._set_setting("eye_minutes", value), self._update_eye_timer()))
        self.goal_minutes = QSpinBox()
        self.goal_minutes.setRange(1, 240)
        self.goal_minutes.setValue(int(self.settings.get("daily_goal_minutes", 5)))
        self.goal_minutes.setSuffix(" 分钟")
        self.goal_minutes.valueChanged.connect(self._goal_changed)
        self.sync_check = QCheckBox("与日历每日打卡同步")
        self.sync_check.setChecked(bool(self.settings.get("sync_checkin", True)))
        self.sync_check.toggled.connect(self._sync_setting_changed)
        self.daily_reminder = QCheckBox("每日阅读提醒")
        self.daily_reminder.setChecked(bool(self.settings.get("daily_reminder_enabled", False)))
        self.daily_reminder.toggled.connect(
            lambda value: self._set_setting("daily_reminder_enabled", value))
        self.reminder_time = QTimeEdit(
            QTime.fromString(self.settings.get("daily_reminder_time", "20:00"), "HH:mm"))
        self.reminder_time.setDisplayFormat("HH:mm")
        self.reminder_time.timeChanged.connect(
            lambda value: self._set_setting("daily_reminder_time", value.toString("HH:mm")))
        reload_btn = QPushButton("按清理选项重新排版")
        reload_btn.clicked.connect(self.reload_cleaning)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.trim_check)
        layout.addWidget(self.repair_check)
        layout.addWidget(self.indent_check)
        layout.addWidget(self.eye_check)
        layout.addWidget(self.eye_minutes)
        layout.addWidget(QLabel("每日阅读目标"))
        layout.addWidget(self.goal_minutes)
        layout.addWidget(self.sync_check)
        layout.addWidget(self.daily_reminder)
        layout.addWidget(self.reminder_time)
        reward_rule = QLabel(
            "奖励规则：阅读窗口可见或气泡朗读进行时才累计时长；"
            "达到上方每日目标后，当天首次奖励 5 数据碎片。"
            "暂停、关闭及重复打开不会重复计奖。")
        reward_rule.setWordWrap(True)
        layout.addWidget(reward_rule)
        self.ai_status_label = QLabel(self._ai_status_text())
        self.ai_status_label.setWordWrap(True)
        layout.addWidget(self.ai_status_label)
        layout.addWidget(reload_btn)
        layout.addStretch()

    def _load_book(self):
        path = _relocate_managed_book(self.book)
        if not os.path.isfile(path):
            show_warning(
                self, "需要重新定位",
                "原书库路径已经失效。请选择同一本电子书重新关联；"
                "原有阅读进度、书签和批注都会保留。")
            replacement, _ = QFileDialog.getOpenFileName(
                self, "重新定位电子书", BASE_DIR,
                "电子书 (" + " ".join(
                    f"*{ext}" for ext in sorted(SUPPORTED_EBOOKS)) + ")")
            if not replacement:
                QTimer.singleShot(0, self.close)
                return
            expected = self.book.get("source_hash", "")
            actual = _file_fingerprint(replacement)
            if expected and actual != expected:
                show_critical(
                    self, "文件不一致",
                    "所选文件与原书籍内容不一致，已取消关联，旧笔记和进度没有改动。")
                QTimer.singleShot(0, self.close)
                return
            path = os.path.abspath(replacement)
            self.book["path"] = path
            self.book["source_hash"] = actual
            self.book["managed"] = False
        asset_dir = (
            os.path.join(EBOOK_DIR, str(self.book["id"]), "assets")
            if self.book.get("managed")
            else self.book.get("asset_dir") or os.path.join(
                EBOOK_DIR, str(self.book["id"]), "assets"))
        self.book["asset_dir"] = asset_dir
        self.book["source_hash"] = (
            self.book.get("source_hash") or _file_fingerprint(path))
        trim = bool(self.settings.get("trim_whitespace", True))
        repair = bool(self.settings.get("repair_sentences", True))
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            cached = load_cache(asset_dir, path)
            if (cached and cached.get("trim_whitespace") == trim
                    and cached.get("repair_sentences") == repair):
                self.parsed = cached
            else:
                self.parsed = parse_ebook(path, asset_dir, trim, repair)
                self.parsed["source_mtime"] = os.path.getmtime(path)
                self.parsed["trim_whitespace"] = trim
                self.parsed["repair_sentences"] = repair
                save_cache(self.parsed, asset_dir)
        except Exception as exc:
            show_critical(self, "解析失败", str(exc))
            QTimer.singleShot(0, self.close)
            return
        finally:
            QApplication.restoreOverrideCursor()
        if not self.parsed:
            return
        # 已有书名一律保留：导入到书库的托管副本文件名恒为 book.<ext>，
        # 按文件名推导标题的格式（如 TXT）会把解析标题变成“book”，
        # 绝不允许它覆盖书架上的原书名；仅在原书名为空时用解析标题补。
        self.book["title"] = self.book.get("title") or self.parsed["title"]
        self.book["size"] = self.parsed["size"]
        self.book["total_chars"] = self.parsed["total_chars"]
        self.book["chapter_count"] = len(self.parsed["chapters"])
        self.book["last_opened"] = datetime.now().isoformat(timespec="seconds")
        self._ensure_reading_checkin()
        self._rebuild_text()
        self.toc_list.clear()
        self.toc_list.addItems([c["title"] for c in self.parsed["chapters"]])
        self._refresh_marks()
        self._refresh_stats()
        self._apply_window_theme()
        save_config(self.pet.config)

    def _rebuild_text(self):
        if not self.parsed:
            return
        self.chapter_starts = []
        self.inline_images = {}
        self.block_image_positions = set()
        pieces = []
        cursor = 0
        for chapter in self.parsed["chapters"]:
            if pieces:
                pieces.append("\n\n")
                cursor += 2
            self.chapter_starts.append(cursor)
            text = chapter.get("text", "")
            images = list(chapter.get("images", []))
            if images and IMAGE_OBJECT not in text:
                # PDF 等格式没有 HTML 内联位置时，放到该页/章节正文之后。
                text = text + ("\n" if text else "") + IMAGE_OBJECT * len(images)
            image_index = 0
            for offset, char in enumerate(text):
                if char != IMAGE_OBJECT:
                    continue
                image_path = images[image_index] if image_index < len(images) else ""
                if image_path and os.path.isfile(image_path):
                    absolute_position = cursor + offset
                    self.inline_images[absolute_position] = image_path
                    pixmap = QPixmap(image_path)
                    if (not pixmap.isNull()
                            and (pixmap.width() >= 120 or pixmap.height() >= 120)):
                        self.block_image_positions.add(absolute_position)
                image_index += 1
            pieces.append(text)
            cursor += len(text)
        self.full_text = "".join(pieces)
        self.repaginate(int(self.book.get("position", 0) or 0))

    def _page_capacity(self):
        viewport = self.text.viewport()
        if viewport is None:
            return 400
        size = max(10, int(self.settings.get("font_size", 10)))
        spacing = max(0.8, float(self.settings.get("line_spacing", 1.5)))
        width = max(240, viewport.width() - 30)
        height = max(150, viewport.height() - 20)
        # point 字号在 Windows 高 DPI 下会换算成更大的像素；额外预留段间距，
        # 保证默认页面无需再拖动正文内部滚动条。
        cols = max(10, int(width / (size * 1.32)))
        rows = max(4, int(height / (size * spacing * 1.25)))
        return max(55, cols * rows)

    def repaginate(self, absolute_position=None):
        if not self.parsed:
            return
        viewport = self.text.viewport()
        # 首次调用时 widget 可能尚未 layout，viewport 返回默认尺寸（~100×30）或 None。
        # 此时跳过，由后续 resizeEvent → _resize_repaginate 在正确尺寸下补上。
        if viewport is None or viewport.width() < 100 or viewport.height() < 100:
            return
        position = self._page_start() if absolute_position is None and self.pages else int(absolute_position or 0)
        capacity = self._page_capacity()
        sorted_images = sorted(self.block_image_positions)
        pages = []
        start = 0
        while start < len(self.full_text):
            if start in self.block_image_positions:
                chapter = max(
                    0, sum(1 for value in self.chapter_starts if value <= start) - 1)
                pages.append((start, start + 1, chapter))
                start += 1
                continue
            target = min(len(self.full_text), start + capacity)
            next_image = next(
                (pos for pos in sorted_images if start < pos < target),
                None)
            if next_image is not None:
                target = next_image
            if target < len(self.full_text):
                search_start = start + int(capacity * 0.72)
                candidates = [
                    self.full_text.rfind(mark, search_start, target)
                    for mark in ("\n", "。", "！", "？", ".", "!", "?")
                ]
                best = max(candidates)
                if best > start:
                    target = best + 1
            chapter = max(0, sum(1 for value in self.chapter_starts if value <= start) - 1)
            pages.append((start, max(start + 1, target), chapter))
            start = max(start + 1, target)
        self.pages = pages or [(0, 0, 0)]
        self.current_page = next(
            (i for i, (begin, end, _) in enumerate(self.pages) if begin <= position < end),
            len(self.pages) - 1)
        self.show_page()

    def _page_start(self):
        return self.pages[self.current_page][0] if self.pages else 0

    def show_page(self):
        if not self.pages or not self.parsed:
            return
        start, end, chapter = self.pages[self.current_page]
        self.text.clear()
        self.text.setPlainText(self.full_text[start:end])
        self._insert_inline_images(start, end)
        self._apply_visual_format()
        self._apply_annotations(start, end)
        self._update_page_chrome()

    def _update_page_chrome(self):
        """页眉信息、章节名、进度条与阅读位置保存（两种模式共用）。"""
        start, end, chapter = self.pages[self.current_page]
        self.current_chapter = chapter
        self.toc_list.setCurrentRow(chapter)
        chapter_title = self.parsed["chapters"][chapter]["title"]
        percent = (end / max(1, len(self.full_text))) * 100
        chapter_pages = [i for i, page in enumerate(self.pages) if page[2] == chapter]
        chapter_page = chapter_pages.index(self.current_page) + 1
        self.header_info.setText(
            f"({chapter_page}/{len(chapter_pages)})　章节 {chapter + 1}/{len(self.parsed['chapters'])}　全书 {percent:.1f}%")
        self.chapter_label.setText(f"{self.book.get('title', '')} ◆ {chapter_title}")
        self.progress.setRange(0, max(1, len(self.full_text)))
        self.progress.setValue(end)
        self.book["position"] = start
        self.book["progress"] = percent
        self.book["status"] = "已读" if percent >= 99.5 else "阅读中"
        newly_read = max(0, end - self._last_page_end)
        self.session_chars += newly_read
        self._last_page_end = max(self._last_page_end, end)
        self._update_status()

    def _insert_inline_images(self, page_start, page_end):
        # 倒序替换对象占位符，确保前面的文本位置不会被后续插入扰动。
        for absolute_pos, path in sorted(self.inline_images.items(), reverse=True):
            if not (page_start <= absolute_pos < page_end):
                continue
            cursor = self.text.textCursor()
            local = absolute_pos - page_start
            cursor.setPosition(local)
            cursor.setPosition(local + 1, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextImageFormat()
            fmt.setName(os.path.abspath(path))
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                viewport = self.text.viewport()
                if viewport is None:
                    available_width = 500
                    available_height = 500
                else:
                    available_width = max(80, viewport.width() - 36)
                    available_height = max(100, viewport.height() - 36)
                scale = min(
                    1.0,
                    available_width / max(1, pixmap.width()),
                    available_height / max(1, pixmap.height()))
                fmt.setWidth(max(1, pixmap.width() * scale))
                fmt.setHeight(max(1, pixmap.height() * scale))
            cursor.insertImage(fmt)

    def _apply_visual_format(self):
        font = QFont(self.settings.get("font", "Microsoft YaHei"))
        font.setPointSize(int(self.settings.get("font_size", 10)))
        font.setLetterSpacing(
            QFont.SpacingType.AbsoluteSpacing,
            float(self.settings.get("letter_spacing", 0)))
        cursor = self.text.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        char_format.setForeground(QColor(self.settings.get("text_color", "#287cc1")))
        # 每次重绘先清掉旧批注背景，否则重新批注或换背景时旧格式会扩散。
        char_format.setBackground(QColor(0, 0, 0, 0))
        cursor.mergeCharFormat(char_format)
        block = QTextBlockFormat()
        block.setLineHeight(float(self.settings.get("line_spacing", 1.5)) * 100, 1)
        alignments = [
            Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignCenter,
            Qt.AlignmentFlag.AlignRight, Qt.AlignmentFlag.AlignJustify]
        block.setAlignment(alignments[int(self.settings.get("alignment", 3))])
        if self.settings.get("first_indent", True):
            block.setTextIndent(font.pointSizeF() * 2)
        cursor.mergeBlockFormat(block)
        image = self.settings.get("background_image", "")
        rendered = self._render_background_image(
            image if image and os.path.isfile(image) else "")
        if rendered:
            self.background_label.setPixmap(QPixmap(rendered))
        self.text.setStyleSheet(
            "QTextBrowser{background:transparent;"
            "border:1px solid #87cfff;border-radius:10px;}"
            "QTextBrowser QWidget{background:transparent;}")

    def _render_background_image(self, image_path=""):
        """先画纯色底，再把底图叠在其上，保证图片位于文字下方。"""
        width = max(1, self.text.width())
        height = max(1, self.text.height())
        source = QPixmap(image_path)
        canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor(
            self.settings.get("background_color", "#eef8ff")))
        painter = QPainter(canvas)
        if not source.isNull():
            painter.setOpacity(max(0, min(100, int(
                self.settings.get("background_opacity", 100)))) / 100.0)
            mode = self.settings.get("background_mode", "适应")
            if mode == "平铺":
                tile_w = max(64, width // 3)
                tile_h = max(48, int(tile_w * source.height() / max(1, source.width())))
                if tile_h > height // 2:
                    tile_h = max(48, height // 2)
                    tile_w = max(64, int(tile_h * source.width() / max(1, source.height())))
                tiled = source.scaled(
                    tile_w, tile_h,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                painter.drawTiledPixmap(canvas.rect(), tiled)
            elif mode == "拉伸":
                painter.drawPixmap(canvas.rect(), source)
            else:
                scaled = source.scaled(
                    width, height, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                left = (width - scaled.width()) // 2
                top = (height - scaled.height()) // 2
                painter.drawPixmap(left, top, scaled)
        painter.end()
        out = os.path.join(self.book.get("asset_dir", EBOOK_DIR), "reader_background.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        return out if canvas.save(out, "PNG") else ""

    def _apply_annotations(self, page_start, page_end):
        for mark in self.book.setdefault("annotations", []):
            begin, end = int(mark.get("start", 0)), int(mark.get("end", 0))
            if end <= page_start or begin >= page_end:
                continue
            cursor = self.text.textCursor()
            cursor.setPosition(max(0, begin - page_start))
            cursor.setPosition(min(page_end, end) - page_start, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(mark.get("color", HIGHLIGHT_COLORS[0])))
            cursor.mergeCharFormat(fmt)
        visible_cursor = self.text.textCursor()
        visible_cursor.clearSelection()
        self.text.setTextCursor(visible_cursor)

    def toggle_panel(self, index):
        if self.panel.isVisible() and self.stack.currentIndex() == index:
            self.panel.hide()
            self.setMinimumSize(380, 480)
            self.resize(max(520, self.width() - self.PANEL_WIDTH), self.height())
            self._keep_inside_screen()
            return
        was_hidden = not self.panel.isVisible()
        self.stack.setCurrentIndex(index)
        self.panel_title.setText(self.PANEL_TITLES[index])
        self.panel.show()
        if was_hidden:
            self.setMinimumWidth(
                self.PANEL_WIDTH + self.READING_MIN_WIDTH + 70)
            self.resize(
                max(self.minimumWidth(), self.width() + self.PANEL_WIDTH),
                self.height())
            self._keep_inside_screen()
        if index == 4:
            self._refresh_marks()
        elif index == 6:
            self._refresh_stats()
            if hasattr(self, "ai_status_label"):
                self.ai_status_label.setText(self._ai_status_text())

    def _keep_inside_screen(self):
        """侧栏展开或收起后，确保标题栏、侧栏和正文都留在当前屏幕内。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(self.width(), available.width())
        height = min(self.height(), available.height())
        if width != self.width() or height != self.height():
            self.resize(width, height)
        rightmost_x = available.right() - self.width() + 1
        bottommost_y = available.bottom() - self.height() + 1
        self.move(
            max(available.left(), min(self.x(), rightmost_x)),
            max(available.top(), min(self.y(), bottommost_y)))

    def _set_setting(self, key, value, repaginate=False):
        self.settings[key] = value
        if key in VISUAL_SETTING_KEYS:
            profile_name = (
                "night_profile" if self.settings.get("night_mode", False)
                else "day_profile")
            self.settings.setdefault(profile_name, {})[key] = value
        save_config(self.pet.config, force=False)
        if repaginate:
            self.repaginate()
        else:
            self._apply_visual_format()
            self._apply_annotations(self._page_start(), self.pages[self.current_page][1])
        self._apply_window_theme()

    def _choose_color(self, key):
        color = QColorDialog.getColor(QColor(self.settings.get(key, "#287cc1")), self)
        if color.isValid():
            self._set_setting(key, color.name())

    def _choose_background(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择阅读背景", BASE_DIR, "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._set_setting("background_image", path)

    def _open_image_preview(self, path):
        self._image_preview = ImagePreviewDialog(path, self)
        self._image_preview.show()

    def _apply_preset(self, night):
        current_profile = (
            "night_profile" if self.settings.get("night_mode", False)
            else "day_profile")
        self.settings.setdefault(current_profile, {}).update({
            key: self.settings.get(key)
            for key in VISUAL_SETTING_KEYS
            if key in self.settings})
        self.settings["night_mode"] = bool(night)
        target_name = "night_profile" if night else "day_profile"
        profile = self.settings.setdefault(target_name, {})
        for key in VISUAL_SETTING_KEYS:
            if key in profile:
                self.settings[key] = profile[key]
        self._sync_visual_controls()
        self._apply_visual_format()
        self._apply_annotations(self._page_start(), self.pages[self.current_page][1])
        self._apply_window_theme()
        save_config(self.pet.config, force=False)

    def _sync_visual_controls(self):
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(self.settings.get("font", "Microsoft YaHei")))
        self.font_combo.blockSignals(False)

        self.font_size.blockSignals(True)
        self.font_size.setValue(int(self.settings.get("font_size", 10)))
        self.font_size.blockSignals(False)

        self.line_spacing.blockSignals(True)
        self.line_spacing.setValue(float(self.settings.get("line_spacing", 1.5)))
        self.line_spacing.blockSignals(False)

        self.letter_spacing.blockSignals(True)
        self.letter_spacing.setValue(float(self.settings.get("letter_spacing", 0)))
        self.letter_spacing.blockSignals(False)

        self.align_combo.blockSignals(True)
        self.align_combo.setCurrentIndex(int(self.settings.get("alignment", 3)))
        self.align_combo.blockSignals(False)

        self.bg_mode.blockSignals(True)
        self.bg_mode.setCurrentText(self.settings.get("background_mode", "适应"))
        self.bg_mode.blockSignals(False)

        self.opacity.blockSignals(True)
        self.opacity.setValue(int(self.settings.get("background_opacity", 100)))
        self.opacity.blockSignals(False)

    def _apply_window_theme(self):
        night = bool(self.settings.get("night_mode", False))
        self.setProperty("ebookNightMode", night)
        background = getattr(self, "_ice_background_widget", None)
        if background is not None:
            background.setProperty("darkOverlayOpacity", 145 if night else 0)
            background.update()
        if night:
            self.setStyleSheet("""
                EbookReaderDialog, EbookReaderDialog QWidget {
                    color: #e6f1fb;
                }
                EbookReaderDialog QLabel { color: #dcebf7; }
                EbookReaderDialog QPushButton,
                EbookReaderDialog QToolButton,
                EbookReaderDialog QComboBox,
                EbookReaderDialog QSpinBox,
                EbookReaderDialog QDoubleSpinBox,
                EbookReaderDialog QTimeEdit,
                EbookReaderDialog QLineEdit {
                    color: #eaf4ff;
                    background-color: rgba(35, 45, 55, 220);
                    border: 1px solid rgba(115, 151, 178, 190);
                }
                EbookReaderDialog QListWidget,
                EbookReaderDialog QScrollArea {
                    color: #eaf4ff;
                    background-color: rgba(24, 32, 40, 205);
                    border-color: rgba(105, 142, 170, 180);
                }
                EbookReaderDialog QListWidget::item {
                    color: #eaf4ff;
                }
                EbookReaderDialog QListWidget::item:selected {
                    color: white;
                    background-color: rgba(55, 109, 150, 225);
                }
            """)
        else:
            self.setStyleSheet("")
        if hasattr(self, "night_status"):
            self.night_status.setText(
                "当前：夜间配置（本模式内的修改会单独保存）"
                if night else "当前：日间配置（本模式内的修改会单独保存）")
        self._sync_tool_button_colors()

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.show_page()

    def next_page(self):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.show_page()
        else:
            self.book["status"] = "已读"
            self.pet.show_bubble("【normal】已经读到末页。至少这一本没有半途而废。")

    def goto_chapter(self, index):
        if 0 <= index < len(self.chapter_starts):
            position = self.chapter_starts[index]
            self.current_page = next(
                (i for i, page in enumerate(self.pages) if page[0] <= position < page[1]), 0)
            self.show_page()

    def search_text(self):
        query = self.search_input.text().strip()
        self.search_results.clear()
        if not query:
            return
        matches = list(re.finditer(re.escape(query), self.full_text, re.IGNORECASE))[:200]
        for result_index, match in enumerate(matches, 1):
            begin = max(0, match.start() - 24)
            end = min(len(self.full_text), match.end() + 36)
            chapter = max(
                0, sum(1 for value in self.chapter_starts if value <= match.start()) - 1)
            page = next(
                (i for i, value in enumerate(self.pages)
                 if value[0] <= match.start() < value[1]), 0)
            snippet = self.full_text[begin:end].replace(IMAGE_OBJECT, "【图片】")
            snippet = re.sub(r"\s+", " ", snippet).strip()
            item = QListWidgetItem(
                f"{result_index}. 第 {chapter + 1} 章 · 第 {page + 1} 页\n{snippet}")
            item.setData(Qt.ItemDataRole.UserRole, match.start())
            self.search_results.addItem(item)
        if not matches:
            empty = QListWidgetItem("没有找到匹配内容。")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.search_results.addItem(empty)

    def _goto_search_result(self, item):
        self.goto_position(int(item.data(Qt.ItemDataRole.UserRole)))

    def goto_position(self, position):
        self.current_page = next(
            (i for i, page in enumerate(self.pages) if page[0] <= position < page[1]),
            len(self.pages) - 1)
        self.show_page()
        local = max(0, position - self._page_start())
        cursor = self.text.textCursor()
        cursor.setPosition(min(local, len(self.text.toPlainText())))
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()

    def _selection_range(self):
        cursor = self.text.textCursor()
        if not cursor.hasSelection():
            return None
        start = self._page_start() + cursor.selectionStart()
        end = self._page_start() + cursor.selectionEnd()
        return start, end, cursor.selectedText()

    def highlight_selection(self):
        selected = self._selection_range()
        if not selected:
            self.pet.show_bubble("【normal】先选中要标记的文字。")
            return
        start, end, text = selected
        color = self.settings.get("highlight_color", HIGHLIGHT_COLORS[0])
        existing = next((
            mark for mark in self.book.setdefault("annotations", [])
            if mark.get("type") == "highlight"
            and int(mark.get("start", -1)) == start
            and int(mark.get("end", -1)) == end
        ), None)
        if existing:
            # 同一范围再次高亮视为修改颜色，不重复生成列表项。
            existing["color"] = color
            existing["text"] = text[:160]
        else:
            self.book["annotations"].append({
                "id": new_id(), "type": "highlight", "start": start, "end": end,
                "text": text[:160], "color": color, "note": "",
                "created": datetime.now().isoformat(timespec="minutes")})
        self.show_page()
        self._refresh_marks()
        save_config(self.pet.config)

    def annotate_selection(self):
        selected = self._selection_range()
        if not selected:
            self.pet.show_bubble("【normal】先选中要批注的文字。")
            return
        ask_multiline(self, "添加批注", "批注内容：",
                      lambda note: self._save_annotation(selected, note))

    def _save_annotation(self, selected, note):
        start, end, text = selected
        self.book.setdefault("annotations", []).append({
            "id": new_id(), "type": "note", "start": start, "end": end,
            "text": text[:160],
            "color": self.settings.get("highlight_color", HIGHLIGHT_COLORS[0]),
            "note": note.strip(),
            "created": datetime.now().isoformat(timespec="minutes")})
        self.show_page()
        self._refresh_marks()
        save_config(self.pet.config)

    def add_bookmark(self):
        if not self.pages or not self.parsed:
            return
        start, _, chapter = self.pages[self.current_page]
        self.book.setdefault("bookmarks", []).append({
            "id": new_id(), "position": start,
            "title": f"{self.parsed['chapters'][chapter]['title']} · 第 {self.current_page + 1} 页",
            "created": datetime.now().isoformat(timespec="minutes")})
        self._refresh_marks()
        save_config(self.pet.config)

    def _refresh_marks(self):
        if not hasattr(self, "mark_list"):
            return
        if self._merge_duplicate_highlights():
            save_config(self.pet.config, force=False)
        self.mark_list.clear()
        filter_index = self.mark_filter.currentIndex() if hasattr(self, "mark_filter") else 0
        for mark in self.book.setdefault("bookmarks", []):
            if filter_index not in (0, 1):
                continue
            item = QListWidgetItem("★ " + mark.get("title", "书签"))
            item.setData(Qt.ItemDataRole.UserRole, ("bookmark", mark.get("id"), mark.get("position", 0)))
            self.mark_list.addItem(item)
        for mark in self.book.setdefault("annotations", []):
            is_note = bool(mark.get("note")) or mark.get("type") == "note"
            if filter_index == 1 or filter_index == 2 and is_note or filter_index == 3 and not is_note:
                continue
            prefix = "✎" if is_note else "▰"
            item = QListWidgetItem(f"{prefix} {mark.get('text', '')[:45]}\n{mark.get('note', '')}")
            item.setBackground(QColor(mark.get("color", HIGHLIGHT_COLORS[0])))
            item.setData(Qt.ItemDataRole.UserRole, ("annotation", mark.get("id"), mark.get("start", 0)))
            self.mark_list.addItem(item)

    def _merge_duplicate_highlights(self):
        annotations = self.book.setdefault("annotations", [])
        seen = {}
        merged = []
        changed = False
        for mark in annotations:
            if mark.get("type") != "highlight":
                merged.append(mark)
                continue
            key = (
                int(mark.get("start", -1)), int(mark.get("end", -1)),
                re.sub(r"\s+", "", str(mark.get("text", ""))))
            if key in seen:
                # 保留首次创建的 ID 与顺序，采用最后一次选择的颜色。
                seen[key]["color"] = mark.get(
                    "color", seen[key].get("color", HIGHLIGHT_COLORS[0]))
                changed = True
                continue
            seen[key] = mark
            merged.append(mark)
        if changed:
            self.book["annotations"] = merged
        return changed

    def _goto_mark(self, item):
        _, _, position = item.data(Qt.ItemDataRole.UserRole)
        self.goto_position(int(position))

    def delete_mark(self):
        item = self.mark_list.currentItem()
        if not item:
            return
        kind, mark_id, _ = item.data(Qt.ItemDataRole.UserRole)
        key = "bookmarks" if kind == "bookmark" else "annotations"
        self.book[key] = [mark for mark in self.book.get(key, []) if mark.get("id") != mark_id]
        self._refresh_marks()
        self.show_page()
        save_config(self.pet.config)

    def edit_mark(self):
        item = self.mark_list.currentItem()
        if not item:
            return
        kind, mark_id, _ = item.data(Qt.ItemDataRole.UserRole)
        if kind == "bookmark":
            mark = next((m for m in self.book.get("bookmarks", []) if m.get("id") == mark_id), None)
            if mark:
                ask_text(self, "修改书签", "书签名称：",
                         lambda title: self._save_mark_title(mark, title),
                         text=mark.get("title", "书签"))
        else:
            mark = next((m for m in self.book.get("annotations", []) if m.get("id") == mark_id), None)
            if mark:
                if mark.get("type") == "highlight" and not mark.get("note"):
                    self.pet.show_bubble(
                        "【normal】这是纯高亮，没有批注文字。"
                        "如需改色请使用“修改选中高亮 / 批注颜色”。")
                    return
                ask_multiline(self, "修改批注", "批注内容：",
                              lambda note: self._save_mark_note(mark, note),
                              text=mark.get("note", ""))

    def _save_mark_title(self, mark, title):
        if title.strip():
            mark["title"] = title.strip()
            self._refresh_marks()
            self.show_page()
            save_config(self.pet.config)

    def _save_mark_note(self, mark, note):
        mark["note"] = note.strip()
        self._refresh_marks()
        self.show_page()
        save_config(self.pet.config)

    def change_mark_color(self):
        item = self.mark_list.currentItem()
        if not item:
            self.pet.show_bubble("【normal】请先在标记列表中选择一条高亮或批注。")
            return
        kind, mark_id, _ = item.data(Qt.ItemDataRole.UserRole)
        if kind == "bookmark":
            self.pet.show_bubble("【normal】书签没有高亮颜色。请选择高亮或批注。")
            return
        mark = next(
            (value for value in self.book.get("annotations", [])
             if value.get("id") == mark_id),
            None)
        if not mark:
            return
        color = QColorDialog.getColor(
            QColor(mark.get("color", HIGHLIGHT_COLORS[0])),
            self, "选择标记颜色")
        if not color.isValid():
            return
        mark["color"] = color.name()
        self._refresh_marks()
        self.show_page()
        save_config(self.pet.config)

    def _reader_menu(self, pos):
        menu = self.text.createStandardContextMenu()
        menu.addSeparator()
        highlight = QAction("高亮选中文字", self)
        highlight.triggered.connect(self.highlight_selection)
        note = QAction("添加批注", self)
        note.triggered.connect(self.annotate_selection)
        remove_highlight = QAction("删除此处 / 选中范围的高亮", self)
        remove_highlight.setEnabled(bool(self._highlights_at(pos)))
        remove_highlight.triggered.connect(
            lambda checked=False, point=pos: self.remove_highlight_at(point))
        talk = QAction("让 Gisa 谈谈选中文字", self)
        talk.triggered.connect(self.talk_about_selection)
        menu.addAction(highlight)
        menu.addAction(note)
        menu.addAction(remove_highlight)
        menu.addAction(talk)
        menu.exec(self.text.mapToGlobal(pos))

    def _highlights_at(self, pos):
        selected = self._selection_range()
        if selected:
            start, end, _ = selected
        else:
            cursor = self.text.cursorForPosition(pos)
            start = self._page_start() + cursor.position()
            end = start + 1
        return [
            mark for mark in self.book.get("annotations", [])
            if mark.get("type") == "highlight"
            and int(mark.get("end", 0)) > start
            and int(mark.get("start", 0)) < end]

    def remove_highlight_at(self, pos):
        targets = self._highlights_at(pos)
        if not targets:
            return
        target_ids = {mark.get("id") for mark in targets}
        self.book["annotations"] = [
            mark for mark in self.book.get("annotations", [])
            if mark.get("id") not in target_ids]
        self._refresh_marks()
        self.show_page()
        save_config(self.pet.config)

    def talk_about_selection(self):
        if not self.parsed:
            return
        if not self._llm_configured():
            show_info(
                self, "尚未配置大模型",
                "“让 Gisa 谈谈选中文字”会调用当前设置的大模型接口。"
                "请先在“核心数据与接口”中填写 API Key 和模型名称。")
            return
        selected = self._selection_range()
        quote = selected[2] if selected else self.text.toPlainText()[:400]
        chapter = self.parsed["chapters"][self.current_chapter]["title"]
        self.pet.send_msg(
            f"我正在读《{self.book.get('title','')}》的“{chapter}”。"
            f"请结合上下文谈谈下面这段文字；若涉及真实作品背景，可适度联网核查，不要编造：\n{quote[:1200]}")

    def _llm_configured(self):
        if self.pet.config.get("api_type", "openai") == "gemini":
            return bool(
                self.pet.config.get("gemini_api_key", "").strip()
                and self.pet.config.get("gemini_model_name", "").strip())
        return bool(
            self.pet.config.get("openai_api_key", "").strip()
            and self.pet.config.get("openai_model_name", "").strip())

    def _ai_status_text(self):
        api_type = self.pet.config.get("api_type", "openai")
        if api_type == "gemini":
            model = self.pet.config.get("gemini_model_name", "") or "未填写模型"
            provider = "Gemini"
        else:
            model = self.pet.config.get("openai_model_name", "") or "未填写模型"
            provider = "OpenAI 兼容接口"
        state = "已配置，会真实调用接口" if self._llm_configured() else "未配置，暂时不会发起请求"
        return f"与 Gisa 交谈：{provider} / {model}\n状态：{state}。"

    def reload_cleaning(self):
        if not self.parsed:
            return
        self.settings["trim_whitespace"] = self.trim_check.isChecked()
        self.settings["repair_sentences"] = self.repair_check.isChecked()
        position = self._page_start()
        for chapter in self.parsed["chapters"]:
            chapter["text"] = clean_text(
                chapter.get("text", ""),
                self.trim_check.isChecked(), self.repair_check.isChecked())
        self.parsed["trim_whitespace"] = self.trim_check.isChecked()
        self.parsed["repair_sentences"] = self.repair_check.isChecked()
        self.parsed["source_mtime"] = os.path.getmtime(self.book.get("path", ""))
        save_cache(self.parsed, self.book["asset_dir"])
        self._rebuild_text()
        self.goto_position(position)
        save_config(self.pet.config)

    def start_auto_read(self):
        self.settings["auto_speed"] = self.auto_speed.value()
        self._auto_position = self._page_start()
        self._auto_running = True
        self._auto_paused = False
        self._auto_generation += 1
        if self._auto_controls is None:
            self._auto_controls = AutoReadControls(self.pet)
            self._auto_controls.pause_requested.connect(self.toggle_auto_pause)
            self._auto_controls.stop_requested.connect(self.stop_auto_read)
        self._auto_controls.pause_btn.setText("⏸")
        self._auto_controls.show()
        self._auto_controls.place_near(self.pet)
        if self.auto_mode.currentIndex() == 1:
            self.hide()
            self._speech_bubble(self._auto_generation)
        else:
            self.panel.hide()
            self.auto_timer.start(100)

    def _auto_tick(self):
        if not self._auto_running or self._auto_paused or self.auto_mode.currentIndex() == 1:
            return
        advance = max(1, int(self.auto_speed.value() / 10))
        self._auto_position = min(len(self.full_text), self._auto_position + advance)
        newly_read = max(0, self._auto_position - self._last_page_end)
        self.session_chars += newly_read
        self._last_page_end = max(self._last_page_end, self._auto_position)
        if self._auto_position >= self.pages[self.current_page][1] and self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.show_page()
        cursor = self.text.textCursor()
        local = max(0, self._auto_position - self._page_start())
        cursor.setPosition(min(local, len(self.text.toPlainText())))
        cursor.setPosition(min(local + advance, len(self.text.toPlainText())), QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#1976d2"))
        fmt.setBackground(QColor("#dbeafe"))
        cursor.mergeCharFormat(fmt)
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()
        if self._auto_position >= len(self.full_text):
            self.stop_auto_read()

    def _speech_bubble(self, generation=None):
        if (generation is not None and generation != self._auto_generation
                or not self._auto_running or self._auto_paused):
            return
        if self._auto_position >= len(self.full_text):
            self.stop_auto_read()
            return
        # 上一个气泡尚在打字、保留展示或队列中时不提前推进位置；
        # 否则长文本会连续塞入有限队列，造成跳段和切换卡顿。
        if (getattr(self.pet, "is_typing", False)
                or getattr(self.pet, "_bubble_queue", [])
                or getattr(self.pet, "_bubble_hold_until", 0.0) > datetime.now().timestamp()):
            active_generation = self._auto_generation
            QTimer.singleShot(
                180, lambda: self._speech_bubble(active_generation))
            return
        end = self._next_speech_end(self._auto_position)
        chunk = self.full_text[self._auto_position:end].replace(IMAGE_OBJECT, "").strip()
        self._auto_position = max(end, self._auto_position + 1)
        newly_read = max(0, self._auto_position - self._last_page_end)
        self.session_chars += newly_read
        self._last_page_end = max(self._last_page_end, self._auto_position)
        if not chunk:
            QTimer.singleShot(
                0, lambda: self._speech_bubble(self._auto_generation))
            return
        safe = html.escape(chunk).replace("\n", "<br>")
        first_break = next((safe.find(mark) + 1 for mark in ("。", "！", "？", "<br>") if safe.find(mark) >= 0), 0)
        if first_break:
            safe = f"<font color='#4169E1'>{safe[:first_break]}</font>{safe[first_break:]}"
        self.pet.show_bubble(
            f"【normal】{safe}",
            type_interval=max(15, int(1000 / max(1, self.auto_speed.value()))))
        delay = max(
            1500,
            int(len(chunk) / max(1, self.auto_speed.value()) * 1000) + 1350)
        active_generation = self._auto_generation
        QTimer.singleShot(
            delay, lambda: self._speech_bubble(active_generation))

    def _next_speech_end(self, start):
        minimum = min(len(self.full_text), start + 180)
        maximum = min(len(self.full_text), start + 330)
        if minimum >= len(self.full_text):
            return len(self.full_text)
        segment = self.full_text[start:maximum]
        for match in re.finditer(r"[。！？!?\n]", segment):
            absolute = start + match.end()
            if absolute >= minimum:
                return absolute
        # 330 字内没有标点时，尽量在较近的空白处分段，不丢任何字符。
        space = max(segment.rfind(" "), segment.rfind("\n"))
        return start + space + 1 if space >= 120 else maximum

    def toggle_auto_pause(self):
        self._auto_paused = not self._auto_paused
        if self._auto_controls:
            self._auto_controls.pause_btn.setText("▶" if self._auto_paused else "⏸")
        if not self._auto_paused and self.auto_mode.currentIndex() == 1:
            self._auto_generation += 1
            self._speech_bubble(self._auto_generation)

    def stop_auto_read(self):
        self._auto_running = False
        self._auto_paused = False
        self._auto_generation += 1
        self.auto_timer.stop()
        if self._auto_controls:
            self._auto_controls.hide()
        self.goto_position(self._auto_position)
        self.show()
        self.raise_()
        self.activateWindow()
        save_config(self.pet.config)

    def _reading_second(self):
        if not self.isVisible() and not self._auto_running:
            return
        self.session_seconds += 1
        today = str(date.today())
        daily = self.pet.config.setdefault("ebook_reading_daily", {}).setdefault(
            today, {"seconds": 0, "chars": 0, "reward_units": 0, "goal_awarded": False})
        daily["seconds"] = int(daily.get("seconds", 0)) + 1
        char_delta = max(0, self.session_chars - self._daily_recorded_chars)
        daily["chars"] = int(daily.get("chars", 0)) + char_delta
        self._daily_recorded_chars = self.session_chars
        goal_seconds = int(self.settings.get("daily_goal_minutes", 5)) * 60
        if daily["seconds"] >= goal_seconds and not daily.get("goal_awarded"):
            daily["goal_awarded"] = True
            self._complete_reading_goal()
        if self.session_seconds % 5 == 0:
            save_config(self.pet.config, force=False)
        self._update_status()

    def _complete_reading_goal(self):
        awarded = False
        if self.settings.get("sync_checkin", True):
            checkins = self.pet.config.setdefault("checkins", [])
            item = next((c for c in checkins if c.get("id") == "ebook_daily_reading"), None)
            if item is None:
                item = {
                    "id": "ebook_daily_reading", "name": "每日阅读",
                    "note": "由静默阅读舱自动同步", "enabled": True,
                    "archived": False, "remind_times": [], "created": str(date.today()),
                    "done_dates": []}
                checkins.append(item)
            if not checkin_done_on(item, date.today()):
                self.pet.calendar_service.do_checkin(item, date.today(), True, quiet=True)
                awarded = True
        else:
            self.pet.config["coins"] = int(self.pet.config.get("coins", 0)) + 5
            awarded = True
        save_config(self.pet.config)
        goal_minutes = int(self.settings.get("daily_goal_minutes", 5))
        reward_text = (
            "每天仅首次完成该目标时计奖；这是今天首次，奖励 "
            "<font color='#FFD700'>5 数据碎片</font>。"
            if awarded else
            "今天的“每日阅读”打卡此前已经完成，因此不重复奖励。")
        self.pet.inject_system_event(
            "系统：用户完成今日电子书阅读目标",
            f"【normal】已累计阅读 {goal_minutes} 分钟，达到今日阅读目标。"
            f"{reward_text}计时只包含阅读窗口可见或气泡朗读进行中的时间。")

    def _ensure_reading_checkin(self):
        if not self.settings.get("sync_checkin", True):
            return None
        checkins = self.pet.config.setdefault("checkins", [])
        item = next((c for c in checkins if c.get("id") == "ebook_daily_reading"), None)
        if item is None:
            item = {
                "id": "ebook_daily_reading", "name": "每日阅读",
                "enabled": True, "archived": False, "remind_times": [],
                "created": str(date.today()), "done_dates": []}
            checkins.append(item)
        item["note"] = f"静默阅读舱目标：每天阅读 {int(self.settings.get('daily_goal_minutes', 5))} 分钟"
        if hasattr(self.pet, "refresh_dialogs"):
            self.pet.refresh_dialogs("dlg_CheckinDialog", "dlg_MiniCalendarDialog")
        return item

    def _sync_setting_changed(self, value):
        self._set_setting("sync_checkin", value)
        if value:
            self._ensure_reading_checkin()
            save_config(self.pet.config)

    def _goal_changed(self, value):
        self._set_setting("daily_goal_minutes", value)
        self._ensure_reading_checkin()

    def _update_status(self):
        elapsed = f"{self.session_seconds // 60:02d}:{self.session_seconds % 60:02d}"
        self.status.setText(
            f"{datetime.now().strftime('%H:%M')}　阅读 {elapsed}　"
            f"{self.session_chars} 字　书签 {len(self.book.get('bookmarks', []))}　"
            f"第 {self.current_page + 1}/{max(1, len(self.pages))} 页")

    def _refresh_stats(self):
        if not self.parsed:
            return
        summary = "、".join(c["title"] for c in self.parsed["chapters"][:8])
        if len(self.parsed["chapters"]) > 8:
            summary += "…"
        self.stats_label.setText(
            f"《{self.book.get('title','')}》\n"
            f"格式/编码：{self.parsed['extension'][1:].upper()} / {self.parsed['encoding']}\n"
            f"文件大小：{_format_size(self.parsed['size'])}\n"
            f"总字数：{self.parsed['total_chars']:,}\n"
            f"章节：{len(self.parsed['chapters'])}　页数：{len(self.pages)}\n"
            f"当前页：{self.current_page + 1}　书签：{len(self.book.get('bookmarks', []))}\n"
            f"章节概要：{summary or '正文'}")

    def _update_eye_timer(self):
        if not hasattr(self, "eye_timer"):
            return
        if self.settings.get("eye_reminder", True):
            self.eye_timer.start(max(5, int(self.settings.get("eye_minutes", 20))) * 60 * 1000)
        else:
            self.eye_timer.stop()

    def _eye_reminder(self):
        self.pet.show_bubble("【normal】视力保护提醒。抬头看远处二十秒，别把眼睛固定在屏幕上。")

    def back_to_shelf(self):
        self.close()
        self.pet.open_dialog(EbookShelfDialog)

    def resizeEvent(self, a0):
        super().resizeEvent(a0)
        if self.parsed and not self._resize_pending:
            self._resize_pending = True
            QTimer.singleShot(180, self._resize_repaginate)

    def _resize_repaginate(self):
        self._resize_pending = False
        if self.parsed:
            self.repaginate()

    def keyPressEvent(self, a0):
        if a0.key() == Qt.Key.Key_Left:
            self.prev_page()
        elif a0.key() == Qt.Key.Key_Right:
            self.next_page()
        elif a0.matches(QKeySequence.StandardKey.Find):
            self.toggle_panel(3)
            self.search_input.setFocus()
        elif a0.modifiers() & Qt.KeyboardModifier.ControlModifier and a0.key() == Qt.Key.Key_B:
            self.add_bookmark()
        elif a0.key() == Qt.Key.Key_Space and self._auto_running:
            self.toggle_auto_pause()
        else:
            super().keyPressEvent(a0)

    def closeEvent(self, a0):
        self.stop_auto_read() if self._auto_running else None
        self.session_timer.stop()
        self.eye_timer.stop()
        if not self.parsed:
            super().closeEvent(a0)
            return
        self.book["position"] = self._page_start()
        self.book["last_read_seconds"] = self.session_seconds
        save_config(self.pet.config)
        if not self._closed_reported:
            self._closed_reported = True
            self.pet.show_bubble(
                f"【normal】阅读进度已封存。下次会从《{self.book.get('title','')}》当前位置继续。")
        super().closeEvent(a0)


class BookCardButton(QToolButton):
    open_requested = pyqtSignal(object)

    def __init__(self, book, cover, parent=None, compact=False):
        super().__init__(parent)
        self.book = book
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIcon(QIcon(cover))
        self.setIconSize(QSize(88 if compact else 108, 116 if compact else 144))
        title = book.get("title", Path(book.get("path", "")).stem)
        if len(title) > 12:
            title = title[:11] + "…"
        self.setText(
            f"{title}\n{book.get('status', '未读')} · "
            f"{float(book.get('progress', 0)):.0f}%")
        self.setToolTip(book.get("title", title))
        self.setMinimumHeight(164 if compact else 194)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QToolButton{padding:6px;border:1px solid #a8d8ff;border-radius:10px;"
            "background:rgba(245,251,255,205);color:#356b94;}"
            "QToolButton:hover{background:#e1f3ff;border-color:#62b9f4;}"
            "QToolButton:checked{background:#ccecff;border:2px solid #3aa6e8;}")

    def mouseDoubleClickEvent(self, a0):
        self.open_requested.emit(self.book)
        a0.accept()


class EbookShelfDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self._selected_book_id = None
        self._card_buttons = []
        self._library_migrated = False
        self.setWindowTitle("◇ 静默阅读舱 · 书架 ◇")
        self.setMinimumSize(420, 520)
        self.resize(520, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        import_btn = QPushButton("导入")
        import_btn.clicked.connect(self.import_books)
        folder_btn = QPushButton("目录文件夹")
        folder_btn.clicked.connect(self.link_folder)
        random_btn = QPushButton("随机打开")
        random_btn.clicked.connect(self.open_random)
        top.addWidget(import_btn)
        top.addWidget(folder_btn)
        top.addWidget(random_btn)
        layout.addLayout(top)

        recent_title = QLabel("最近阅读")
        recent_title.setStyleSheet("font-size:14px;font-weight:bold;color:#287cc1;")
        layout.addWidget(recent_title)
        self.recent_host = QWidget()
        self.recent_layout = QHBoxLayout(self.recent_host)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_layout.setSpacing(7)
        layout.addWidget(self.recent_host)

        filters = QGridLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("检索书名…")
        self.search.textChanged.connect(self.refresh_table)
        self.category = QComboBox()
        self.category.currentTextChanged.connect(self.refresh_table)
        category_label = QLabel("书架分类")
        category_label.setStyleSheet("font-weight:bold;color:#287cc1;")
        filters.addWidget(category_label, 0, 0)
        filters.addWidget(self.category, 0, 1)
        filters.addWidget(self.search, 1, 0, 1, 2)
        filters.setColumnStretch(1, 1)
        layout.addLayout(filters)

        self.book_scroll = QScrollArea()
        self.book_scroll.setWidgetResizable(True)
        self.book_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.book_host = QWidget()
        self.book_grid = QGridLayout(self.book_host)
        self.book_grid.setContentsMargins(2, 2, 2, 2)
        self.book_grid.setHorizontalSpacing(8)
        self.book_grid.setVerticalSpacing(8)
        self.book_scroll.setWidget(self.book_host)
        layout.addWidget(self.book_scroll, stretch=1)

        actions = QGridLayout()
        open_btn = QPushButton("打开选中")
        open_btn.clicked.connect(self.open_selected)
        category_btn = QPushButton("修改分类")
        category_btn.clicked.connect(self.change_category)
        status_btn = QPushButton("切换已读/未读")
        status_btn.clicked.connect(self.toggle_status)
        up_btn = QPushButton("上移")
        up_btn.clicked.connect(lambda: self.move_book(-1))
        down_btn = QPushButton("下移")
        down_btn.clicked.connect(lambda: self.move_book(1))
        delete_btn = QPushButton("删除")
        delete_btn.setStyleSheet("color:#c62828;")
        delete_btn.clicked.connect(self.delete_book)
        for index, button in enumerate(
                (open_btn, category_btn, status_btn, up_btn, down_btn, delete_btn)):
            actions.addWidget(button, index // 3, index % 3)
        layout.addLayout(actions)
        self.refresh_table()

    def books(self):
        library = self.pet.config.setdefault("ebook_library", [])
        if not self._library_migrated:
            self._library_migrated = True
            if self._repair_and_merge_library(library):
                save_config(self.pet.config)
        return library

    def _repair_and_merge_library(self, library):
        changed = False
        by_hash = {}
        duplicates = []
        for book in library:
            before = book.get("path", "")
            path = _relocate_managed_book(book)
            changed = changed or path != before
            if os.path.isfile(path):
                fingerprint = book.get("source_hash") or _file_fingerprint(path)
                if fingerprint and book.get("source_hash") != fingerprint:
                    book["source_hash"] = fingerprint
                    changed = True
                if fingerprint in by_hash:
                    self._merge_book_records(by_hash[fingerprint], book)
                    duplicates.append(book)
                    changed = True
                elif fingerprint:
                    by_hash[fingerprint] = book
        for duplicate in duplicates:
            if duplicate in library:
                library.remove(duplicate)
            # 清理被合并的重复书籍的托管目录，避免残留的 assets 和封面图片堆积
            dup_id = duplicate.get("id") if isinstance(duplicate, dict) else None
            if dup_id and duplicate.get("managed"):
                dup_folder = os.path.abspath(os.path.join(EBOOK_DIR, str(dup_id)))
                root = os.path.abspath(EBOOK_DIR)
                if (os.path.commonpath((root, dup_folder)) == root
                        and os.path.isdir(dup_folder)):
                    _force_delete_dir(dup_folder)
        return changed

    @staticmethod
    def _merge_book_records(primary, duplicate):
        for key in ("bookmarks", "annotations"):
            target = primary.setdefault(key, [])
            known = {item.get("id") for item in target}
            target.extend(
                item for item in duplicate.get(key, [])
                if item.get("id") not in known)
        if float(duplicate.get("progress", 0)) > float(primary.get("progress", 0)):
            for key in ("progress", "position", "status", "last_opened"):
                if key in duplicate:
                    primary[key] = duplicate[key]
        primary_path = _relocate_managed_book(primary)
        duplicate_path = _relocate_managed_book(duplicate)
        if not os.path.isfile(primary_path) and os.path.isfile(duplicate_path):
            for key in (
                    "path", "asset_dir", "managed", "library_relpath",
                    "source_hash", "size"):
                if key in duplicate:
                    primary[key] = duplicate[key]
        if (primary.get("category", "默认书架") == "默认书架"
                and duplicate.get("category", "默认书架") != "默认书架"):
            primary["category"] = duplicate["category"]

    def refresh_table(self):
        categories = ["全部分类"] + sorted({
            book.get("category", "默认书架") for book in self.books()})
        current = self.category.currentText() or "全部分类"
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(categories)
        self.category.setCurrentText(current if current in categories else "全部分类")
        self.category.blockSignals(False)
        query = self.search.text().strip().lower()
        category = self.category.currentText()
        visible = [
            book for book in self.books()
            if (not query or query in book.get("title", "").lower())
            and (category == "全部分类" or book.get("category", "默认书架") == category)]
        self.visible_books = visible
        self._clear_layout(self.book_grid)
        self._clear_layout(self.recent_layout)
        self._card_buttons = []

        recent = sorted(
            self.books(),
            key=lambda book: book.get("last_opened", book.get("added", "")),
            reverse=True)[:3]
        for book in recent:
            self.recent_layout.addWidget(
                self._make_card(book, compact=True), stretch=1)
        for _ in range(max(0, 3 - len(recent))):
            placeholder = QLabel("尚无记录")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setMinimumHeight(164)
            placeholder.setStyleSheet(
                "color:#92a8ba;border:1px dashed #b9d8ee;border-radius:10px;")
            self.recent_layout.addWidget(placeholder, stretch=1)

        for index, book in enumerate(visible):
            self.book_grid.addWidget(self._make_card(book), index // 3, index % 3)
        for column in range(3):
            self.book_grid.setColumnStretch(column, 1)
        self.book_grid.setRowStretch((len(visible) + 2) // 3, 1)
        if not visible:
            empty = QLabel("这里还没有符合条件的书\n可从上方导入电子书或选择目录文件夹")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#7894aa;padding:24px;")
            self.book_grid.addWidget(empty, 0, 0, 1, 3)
        if not any(book.get("id") == self._selected_book_id for book in visible):
            self._selected_book_id = visible[0].get("id") if visible else None
        self._sync_card_selection()

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _cover_pixmap(self, book):
        path = book.get("path", "")
        asset_dir = book.get("asset_dir", "")
        parsed = load_cache(asset_dir, path) if path and asset_dir else None
        if parsed:
            for chapter in parsed.get("chapters", []):
                for image_path in chapter.get("images", []):
                    if os.path.isfile(image_path):
                        pixmap = QPixmap(image_path)
                        if not pixmap.isNull():
                            return pixmap.scaled(
                                108, 144, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
        fallback = QPixmap(108, 144)
        fallback.fill(QColor("#91a8bd"))
        return fallback

    def _make_card(self, book, compact=False):
        card = BookCardButton(
            book, self._cover_pixmap(book), self.book_host, compact=compact)
        card.clicked.connect(
            lambda checked=False, book_id=book.get("id"): self._select_book(book_id))
        card.open_requested.connect(self._open_book)
        self._card_buttons.append(card)
        return card

    def _select_book(self, book_id):
        self._selected_book_id = book_id
        self._sync_card_selection()

    def _sync_card_selection(self):
        for card in self._card_buttons:
            card.setChecked(card.book.get("id") == self._selected_book_id)

    def refresh_list(self):
        self.refresh_table()

    def selected_book(self):
        return next(
            (book for book in self.books()
             if book.get("id") == self._selected_book_id),
            None)

    def _register(self, path, managed=False):
        absolute = os.path.abspath(path)
        fingerprint = _file_fingerprint(absolute)
        source_size = os.path.getsize(absolute)
        source_title = Path(absolute).stem.casefold().strip()
        existing = next((
            book for book in self.books()
            if (os.path.abspath(book.get("path", "")) == absolute
                or (fingerprint and book.get("source_hash") == fingerprint)
                or (
                    not book.get("source_hash")  # 旧记录缺 hash 时才能用弱匹配兜底
                    and not os.path.isfile(_relocate_managed_book(book))
                    and Path(book.get("title", "")).stem.casefold().strip() == source_title
                    and int(book.get("size", -1)) == source_size))
        ), None)
        if existing:
            if managed:
                folder = os.path.join(EBOOK_DIR, str(existing.get("id")))
                os.makedirs(folder, exist_ok=True)
                final_path = os.path.join(
                    folder, "book" + Path(absolute).suffix.lower())
                if os.path.abspath(final_path) != absolute:
                    temporary = final_path + ".importing"
                    _copy_book_file(absolute, temporary)
                    os.replace(temporary, final_path)
                self._copy_html_resources(absolute, folder)
                existing["path"] = final_path
                existing["asset_dir"] = os.path.join(folder, "assets")
                existing["managed"] = True
                existing["library_relpath"] = os.path.relpath(final_path, EBOOK_DIR)
            elif not os.path.isfile(existing.get("path", "")):
                existing["path"] = absolute
                existing["managed"] = False
            existing["source_hash"] = fingerprint
            existing["size"] = source_size
            existing["title"] = existing.get("title") or Path(absolute).stem
            save_config(self.pet.config)
            return existing
        book_id = new_id()
        final_path = absolute
        asset_dir = os.path.join(EBOOK_DIR, str(book_id), "assets")
        if managed:
            folder = os.path.join(EBOOK_DIR, str(book_id))
            os.makedirs(folder, exist_ok=True)
            final_path = os.path.join(folder, "book" + Path(path).suffix.lower())
            _copy_book_file(path, final_path)
            self._copy_html_resources(path, folder)
        book = {
            "id": book_id, "title": Path(path).stem, "path": final_path,
            "asset_dir": asset_dir, "managed": managed, "category": "默认书架",
            "status": "未读", "progress": 0.0, "position": 0,
            "size": os.path.getsize(path), "bookmarks": [], "annotations": [],
            "source_hash": fingerprint,
            "library_relpath": (
                os.path.relpath(final_path, EBOOK_DIR) if managed else ""),
            "added": datetime.now().isoformat(timespec="seconds")}
        self.books().append(book)
        return book

    @staticmethod
    def _copy_html_resources(path, folder):
        if Path(path).suffix.lower() not in (".html", ".htm"):
            return
        try:
            source, _ = decode_bytes(Path(path).read_bytes())
            _, _, refs = html_to_text(source)
            for ref in refs:
                relative = Path(ref)
                source_image = (Path(path).parent / relative).resolve()
                if ".." not in relative.parts and source_image.is_file():
                    target_image = Path(folder, relative)
                    target_image.parent.mkdir(parents=True, exist_ok=True)
                    _copy_book_file(source_image, target_image)
        except Exception:
            pass

    def import_books(self):
        patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EBOOKS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入电子书", BASE_DIR, f"电子书 ({patterns});;所有文件 (*)")
        for path in paths:
            if Path(path).suffix.lower() in SUPPORTED_EBOOKS:
                self._register(path, managed=True)
        if paths:
            save_config(self.pet.config)
            self.refresh_table()

    def link_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择现有电子书文件夹", BASE_DIR)
        if not folder:
            return
        count = 0
        for root, _, files in os.walk(folder):
            for name in files:
                path = os.path.join(root, name)
                if Path(name).suffix.lower() in SUPPORTED_EBOOKS:
                    self._register(path, managed=False)
                    count += 1
                    if count >= 500:
                        break
            if count >= 500:
                break
        save_config(self.pet.config)
        self.refresh_table()
        self.pet.show_bubble(f"【normal】已关联 {count} 本电子书。原文件仍留在原位置。")

    def open_selected(self):
        book = self.selected_book()
        if not book:
            return
        self._open_book(book)

    def _open_book(self, book):
        current = getattr(self.pet, "_ebook_reader", None)
        if current is not None:
            try:
                current.show()
                current.raise_()
                current.activateWindow()
                return
            except RuntimeError:
                self.pet._ebook_reader = None
        self.hide()
        reader = EbookReaderDialog(self.pet, book)
        self.pet._ebook_reader = reader
        self.pet.dlg_EbookReaderDialog = reader
        reader.destroyed.connect(lambda: (
            setattr(self.pet, "_ebook_reader", None),
            setattr(self.pet, "dlg_EbookReaderDialog", None)))
        reader.show()

    def open_random(self):
        if not self.books():
            self.pet.show_bubble("【normal】书架是空的。先导入电子书。")
            return
        book = random.choice(self.books())
        self._selected_book_id = book.get("id")
        self._open_book(book)

    def change_category(self):
        book = self.selected_book()
        if not book:
            return
        ask_text(self, "书架分类", "分类名称：",
                 lambda category: self._apply_category(book, category),
                 text=book.get("category") or "默认书架")

    def _apply_category(self, book, category):
        if category.strip():
            book["category"] = category.strip()
            save_config(self.pet.config)
            self.refresh_table()

    def toggle_status(self):
        book = self.selected_book()
        if book:
            book["status"] = "未读" if book.get("status") == "已读" else "已读"
            save_config(self.pet.config)
            self.refresh_table()

    def move_book(self, delta):
        book = self.selected_book()
        if not book:
            return
        books = self.books()
        index = books.index(book)
        target = max(0, min(len(books) - 1, index + delta))
        if target != index:
            books.insert(target, books.pop(index))
            save_config(self.pet.config)
            self.refresh_table()

    def delete_book(self):
        book = self.selected_book()
        if not book:
            return
        book_id = book.get("id")
        if not book_id:
            self.pet.show_bubble("【normal】这本书的记录不完整，缺少标识，无法安全删除。")
            return
        message = "从书架删除这本书？"
        if book.get("managed"):
            message += "\n这本书是导入到桌宠书库的副本，副本文件也会删除。"
        else:
            message += "\n原文件夹中的电子书不会被删除。"
        ask_yes_no(self, "确认删除", message,
                   lambda: self._do_delete_book(book_id, bool(book.get("managed"))))

    def _do_delete_book(self, book_id, managed):
        # 1. 如果正在阅读这本书，先关闭阅读器，等待 Qt 释放资源
        reader = getattr(self.pet, "_ebook_reader", None)
        if reader is not None:
            try:
                reader_book_id = getattr(reader, "book", {}).get("id")
            except RuntimeError:
                reader_book_id = None
                reader = None
            if reader is not None and reader_book_id == book_id:
                try:
                    reader.destroyed.disconnect()
                except (TypeError, RuntimeError):
                    pass
                self.pet._ebook_reader = None
                self.pet.dlg_EbookReaderDialog = None
                try:
                    reader.close()
                except RuntimeError:
                    pass
                QApplication.processEvents()
                gc.collect()

        # 2. 用 id 从列表中查找并移除（比依赖对象引用更安全，
        #    避免 _repair_and_merge_library 中间态导致的不匹配）
        books = self.books()
        target = next((b for b in books if b.get("id") == book_id), None)
        if target is not None:
            books.remove(target)

        # 3. 删除托管的文件副本（带 GC + 重试 + 重命名降级）
        cleanup_error = None
        folder = ""
        if managed:
            folder = os.path.abspath(os.path.join(EBOOK_DIR, str(book_id)))
            root = os.path.abspath(EBOOK_DIR)
            if os.path.commonpath((root, folder)) == root and os.path.isdir(folder):
                _force_delete_dir(folder)
                if os.path.isdir(folder):
                    # 目录仍存在（非 Windows 或无 reboot delete 兜底）
                    # → 重命名释放原始路径，再试 _force_delete_dir
                    temp_dir = folder + ".deleted." + str(int(time.time()))
                    try:
                        os.rename(folder, temp_dir)
                        _force_delete_dir(temp_dir)
                        if not os.path.isdir(temp_dir):
                            cleanup_error = None
                    except OSError as exc:
                        cleanup_error = str(exc)
                else:
                    cleanup_error = None

        # 4. 保存并刷新
        self._selected_book_id = None
        save_config(self.pet.config)
        self.refresh_table()

        if cleanup_error:
            self.pet.show_bubble(
                f"【normal】书籍已从书架移除。副本文件暂时被系统占用，"
                f"已安排下次启动时自动清理。（{cleanup_error}）")
