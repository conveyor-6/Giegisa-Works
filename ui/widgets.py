import os
import re
import base64
from datetime import date
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QLineEdit, QVBoxLayout, QHBoxLayout, QDialog, QListWidget, QPushButton, QTextEdit, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QSize, QRect, QByteArray, QBuffer, QIODevice, QEvent
from PyQt6.QtGui import QPixmap, QImage, QColor, QPainter, QTextCursor

class ImageBubble(QWidget):
    def __init__(self, parent_pet):
        super().__init__()
        self.pet = parent_pet
        # 设为无边框、工具窗口、悬浮置顶
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.layout = QVBoxLayout(self)
        
        self.img_label = QLabel("正在解析异世界图像数据...")
        self.img_label.setStyleSheet(f"background-color: {self.pet.config.get('bubble_bg', 'rgba(255,255,255,220)')}; border-radius: 10px; border: 2px solid {self.pet.config.get('bubble_border', '#555')}; padding: 10px; font-weight: bold;")
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.close_btn = QPushButton("✖")
        self.close_btn.setFixedSize(24, 24)
        self.close_btn.setStyleSheet("background-color: #ff4c4c; color: white; border-radius: 12px; font-weight: bold; font-family: 'Microsoft YaHei';")
        self.close_btn.clicked.connect(self.close)
        
        top_layout = QHBoxLayout()
        top_layout.addStretch()
        top_layout.addWidget(self.close_btn)
        
        self.layout.addLayout(top_layout)
        self.layout.addWidget(self.img_label)
        
        self.is_following = False
        self.mouse_drag_pos = QPoint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_following = True
            self.mouse_drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_following and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self.mouse_drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        # 松开即复位拖拽状态，避免陈旧的 mouse_drag_pos 把窗口“吸”走。
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_following = False
        super().mouseReleaseEvent(event)

    def load_image(self, data):
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if not pixmap.isNull():
            # 限制最大宽高防溢出
            if pixmap.width() > 350 or pixmap.height() > 350:
                pixmap = pixmap.scaled(350, 350, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.img_label.setPixmap(pixmap)
            self.adjustSize()
        else:
            self.img_label.setText("【图像数据损坏，渲染失败】")

class ResponsiveListWidget(QListWidget):
    """让自定义列表项先按视口宽度换行，再计算正确高度。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._relayout_pending = False

    def schedule_relayout(self):
        if self._relayout_pending:
            return
        self._relayout_pending = True
        QTimer.singleShot(0, self._relayout_items)

    def _relayout_items(self):
        self._relayout_pending = False
        width = max(120, self.viewport().width() - 10)
        for i in range(self.count()):
            item = self.item(i)
            widget = self.itemWidget(item)
            if not widget:
                continue
            widget.setMinimumWidth(width)
            widget.setMaximumWidth(width)
            layout = widget.layout()
            if layout:
                layout.invalidate()
                layout.activate()
                height = layout.heightForWidth(width)
            else:
                height = widget.sizeHint().height()
            # Qt 在自定义 QListWidgetItem 首次测量时经常漏算按钮边框与布局间距，
            # 尤其在 125%/150% DPI 下会把最下面一排按钮裁掉。保留一段明确的
            # 垂直安全区，并在设置 item 高度前重新激活布局。
            widget.adjustSize()
            height = max(
                widget.minimumSizeHint().height(), widget.sizeHint().height(),
                height if height >= 0 else 0, 38) + 14
            widget.resize(width, height)
            item.setSizeHint(QSize(width, height))
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_relayout()

    def setItemWidget(self, item, widget):
        super().setItemWidget(item, widget)
        self.schedule_relayout()

class DraggableListWidget(ResponsiveListWidget):
    order_updated = pyqtSignal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        
    def dropEvent(self, event):
        super().dropEvent(event)
        new_order = []
        for i in range(self.count()):
            item = self.item(i)
            new_order.append(item.data(Qt.ItemDataRole.UserRole))
        self.order_updated.emit(new_order)

class ChatInputBox(QTextEdit):
    returnPressed = pyqtSignal()
    image_pasted = pyqtSignal()   # 粘贴了图片时通知主界面更新提示
    historyUp = pyqtSignal()      # 光标在首行时按 ↑：翻阅更早的输入历史
    historyDown = pyqtSignal()    # 光标在末行时按 ↓：翻阅更近的输入历史

    # 占位文字（颜色固定，不随深色系统变化）。想改占位文字颜色，改 placeholder_color 即可。
    placeholder_text = "与Giegisa对话... (Ctrl+v贴图，Shift+回车换行，回车发送，↑↓显示输入记录)"
    placeholder_color = "#5EA7D8"

    def __init__(self, parent=None):
        super().__init__(parent)
        # 注意：不能调用 setPlaceholderText——原生占位文字颜色在“深色系统 +
        # QSS”下会被系统 palette 覆盖且无法自定义（setPalette 被 QStyleSheetStyle
        # 重置、QSS color 被强制 50% 淡化），故改为 paintEvent 自绘占位文字。
        # color 固定为深色：若 QSS 不写 color，深色系统下文字会跟随系统
        # palette 变成浅色，与白色半透明背景对比度不足。
        self.setStyleSheet("background-color: rgba(255, 255, 255, 180); color: #333333; border: 1px solid gray; border-radius: 5px; font-family: 'Microsoft YaHei'; font-size: 13px; padding: 4px;")
        self.setFixedHeight(40)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textChanged.connect(self.adjust_height)
        # 待发送的图片（base64 文本 + 类型），发送后会清空
        self.pending_image_b64 = None
        self.pending_image_mime = "image/png"

    def placeholderText(self):
        """兼容原 QTextEdit API：返回占位文字（实际由 paintEvent 自绘）。"""
        return self.placeholder_text

    def paintEvent(self, event):
        super().paintEvent(event)
        # 没有可见文字时自绘占位文字（允许只有空白/换行——按 Shift+回车
        # 换行后输入框变高，换行的占位文字后文随之可见）。颜色完全可控、
        # 不受深色系统 palette 影响。定位以第一行文本位置为基准（不随
        # 光标移动）：默认单行时基线定位让文字中心与光标中心重合、超宽
        # 部分由视口裁剪（只显示一行）；输入框变高（≥两行空间）且文字
        # 超宽时自动换行、顶部对齐，完整显示后文。
        if self.placeholder_text and not self.toPlainText().strip():
            painter = QPainter(self.viewport())
            painter.setPen(QColor(self.placeholder_color))
            painter.setFont(self.document().defaultFont())
            fm = painter.fontMetrics()
            cursor = self.textCursor()
            cursor.setPosition(0)          # 第一行行首，占位文字固定从顶部排起
            first = self.cursorRect(cursor)
            left = first.x() + first.width() + 2
            avail_w = self.viewport().width() - left - 2
            need_wrap = ("\n" in self.placeholder_text
                         or fm.horizontalAdvance(self.placeholder_text) > avail_w)
            if need_wrap and self.viewport().height() > 2 * fm.lineSpacing():
                # 输入框已变高（≥两行空间）：自动换行，从顶部对齐完整显示
                rect = QRect(left, first.y(), max(20, avail_w),
                             max(20, self.viewport().height() - first.y()))
                painter.drawText(rect, Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignTop
                                 | Qt.TextFlag.TextWordWrap, self.placeholder_text)
            else:
                # 单行：基线定位（文字中心与光标中心重合），超宽部分
                # 由视口裁剪——默认高度下只显示一行。
                baseline = first.y() + (first.height() - fm.height()) // 2 + fm.ascent()
                painter.drawText(left, baseline, self.placeholder_text)
            painter.end()

    def adjust_height(self):
        doc_height = int(self.document().size().height())
        # 输入框随内容一直往下拉长：不再设 120px 小上限，只有拉长到接近
        # 屏幕可用高度时才转滚动条，防止窗口整体撑出屏幕。
        max_height = 600
        screen = QApplication.primaryScreen()
        if screen is not None:
            max_height = max(400, screen.availableGeometry().height() - 250)
        new_height = max(40, min(doc_height + 12, max_height))
        self.setFixedHeight(new_height)
        if new_height >= max_height:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    # ---- 图片粘贴支持 ----
    def canInsertFromMimeData(self, source):
        if source.hasImage():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        # 优先处理图片：不把图片塞进文本框，而是暂存起来等待随文字一起发送
        image = None
        if source.hasImage():
            image = source.imageData()
        elif source.hasUrls():
            for u in source.urls():
                if u.isLocalFile() and u.toLocalFile().lower().endswith(
                        (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")):
                    image = QImage(u.toLocalFile())
                    break
        if image is not None and not QImage(image).isNull():
            self._store_image(QImage(image))
            return
        super().insertFromMimeData(source)

    def _store_image(self, qimage):
        try:
            # 太大的图先缩小，避免编码后体积爆炸、请求超时
            if qimage.width() > 1024 or qimage.height() > 1024:
                qimage = qimage.scaled(1024, 1024, Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            qimage.save(buf, "PNG")
            buf.close()
            self.pending_image_b64 = base64.b64encode(bytes(ba)).decode("ascii")
            self.image_pasted.emit()
        except Exception:
            self.pending_image_b64 = None

    def clear_pending_image(self):
        self.pending_image_b64 = None

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.returnPressed.emit()
        elif event.key() == Qt.Key.Key_Up and self.textCursor().blockNumber() == 0:
            # 仅在首行按 ↑ 才翻阅历史，不打断多行草稿内的光标移动
            self.historyUp.emit()
        elif event.key() == Qt.Key.Key_Down and self.textCursor().blockNumber() == self.document().blockCount() - 1:
            # 仅在末行按 ↓ 才翻阅历史
            self.historyDown.emit()
        else:
            super().keyPressEvent(event)

class FocusOverlay(QWidget):
    def __init__(self, pet):
        super().__init__()
        self.pet = pet
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.label = QLabel("Focus Mode")
        self.label.setStyleSheet("background-color: rgba(20, 20, 25, 220); color: #25f8ff; padding: 10px; border-radius: 8px; font-family: 'Microsoft YaHei'; font-size: 13px; font-weight: bold; border: 2px solid #25f8ff;")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.layout.addWidget(self.label)
        
        self.is_following = False
        self.mouse_drag_pos = QPoint()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_following = True
            self.mouse_drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_following and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self.mouse_drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        # 松开即复位拖拽状态，避免陈旧的 mouse_drag_pos 把窗口“吸”走。
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_following = False
        super().mouseReleaseEvent(event)

    def update_display(self):
        if not self.pet.is_focus_mode:
            self.hide()
            return
            
        rem = self.pet.focus_seconds
        m, s = divmod(rem, 60)
        h, m = divmod(m, 60)
        time_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        
        if self.pet.focus_type == "pomodoro":
            state_str = "🍅 工作中" if self.pet.focus_state == "work" else "☕ 休息中"
            color = "#FF3366" if self.pet.focus_state == "work" else "#00FF41"
            info = f"<font color='{color}'>{state_str}</font> (组: {self.pet.focus_sets_done+1}/{self.pet.focus_sets_total})<br>剩余: {time_str}"
        elif self.pet.focus_type == "stopwatch":
            info = f"⏳ 正计时中<br>已专注: {time_str}"
        else:
            info = f"⏱️ 倒计时中<br>剩余: {time_str}"
            
        st = self.pet.focus_start_dt.strftime('%H:%M:%S')
        et = self.pet.focus_end_dt.strftime('%H:%M:%S') if self.pet.focus_end_dt else "无限制"
        self.label.setText(f"{info}<br><font color='#AAAAAA' size='2'>起: {st} | 止: {et}</font>")

class InputDialog(QDialog):
    """【新增】公用单行输入弹窗"""
    def __init__(self, title, label_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(300, 100)
        self.layout = QVBoxLayout(self)
        
        self.layout.addWidget(QLabel(label_text))
        self.input = QLineEdit()
        self.layout.addWidget(self.input)
        
        btn = QPushButton("确定")
        btn.clicked.connect(self.accept)
        self.layout.addWidget(btn)
        
    def get_text(self):
        return self.input.text().strip()

class ChatBubbleWindow(QWidget):
    """独立的文字气泡子窗口。

    历史问题：气泡曾是桌宠窗口内部的顶部控件，文字增长会改变桌宠
    窗口尺寸，即使锚定回拉也会在 Windows 上分两帧绘制，图像出现
    肉眼可见的抖动。拆成独立子窗口后，桌宠本体窗口尺寸彻底恒定
    （只含图像/输入框），从架构上不可能再被气泡推动；气泡由桌宠
    定位到图像正上方，文字增长时气泡窗口自己向上扩展。
    """

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # 不用 QLayout::SetFixedSize——它会在每次布局时把窗口 min/max
        # 强制重置为 sizeHint，使“超高时给气泡设高度上限”无法实现。
        # 尺寸统一由 setText 后的 adjustSize() 驱动（adjustSize 遵守
        # 窗口 maximumHeight 上限）。
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setMinimumHeight(30)
        self.label.setSizePolicy(QSizePolicy.Policy.MinimumExpanding,
                                 QSizePolicy.Policy.MinimumExpanding)
        self.label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lay.addWidget(self.label)

    # ---- 兼容原 QLabel 调用面（oc.py 各处以 self.chat_bubble.xxx 调用） ----
    def setText(self, text):
        self.label.setText(text)
        # 没有 SetFixedSize 约束，内容变化后需显式自适应尺寸。
        self.adjustSize()

    def text(self):
        return self.label.text()

    def setFixedWidth(self, width):
        # 窗口与内部标签同时定宽：未显示时窗口宽度也恒定。
        super().setFixedWidth(width)
        self.label.setFixedWidth(width)

    def setStyleSheet(self, qss):
        self.label.setStyleSheet(qss)

    # ---- 事件转发：点击/拖动/右键气泡，等效于操作桌宠本体 ----
    def mousePressEvent(self, event):
        self.pet.mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.pet.mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.pet.mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.pet.contextMenuEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        # 显示前立即就位，避免在旧坐标闪一帧
        self.pet._sync_bubble_position()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 文字增长后让桌宠重新定位（保持底边贴在图像顶，向上扩展）。
        # 推迟到同一轮事件循环执行，与缩放同一帧生效。
        self.pet._schedule_bubble_sync()


class DailySigninWindow(QWidget):
    """每日上线“数据碎片”领取窗（非常驻按钮窗）。

    每天首次上线或跨 0 点仍在运行时由桌宠自动弹出；点击“领取”按钮
    才入账，也可点 ✖ 直接关闭（本日不再弹出）。领取动作放在
    DesktopPet.claim_daily_signin，点击时会再次核对今日是否已领，防重复。
    """

    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # 右上角 ✖：非常驻，不想要时可以随手关掉（本日不再弹出）
        top = QHBoxLayout()
        top.addStretch()
        self.close_btn = QPushButton("✖")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet(
            "QPushButton { background-color: rgba(255, 76, 76, 200); color: white;"
            " border: none; border-radius: 10px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #ff4c4c; }")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.hide)
        top.addWidget(self.close_btn)
        lay.addLayout(top)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.label.setStyleSheet(
            "background-color: rgba(24, 28, 46, 235); color: #EAF6FF;"
            "border: 2px solid #4FC3F7; border-radius: 10px;"
            "padding: 12px 16px; font-family: 'Microsoft YaHei UI', 'Microsoft YaHei';"
            "font-size: 13px;")
        lay.addWidget(self.label)

        self.claim_btn = QPushButton("点击领取")
        self.claim_btn.setStyleSheet(
            "QPushButton { background-color: #2D9BEA; color: white;"
            " border: none; border-radius: 10px; padding: 9px;"
            " font-family: 'Microsoft YaHei UI', 'Microsoft YaHei';"
            " font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #4FC3F7; }"
            "QPushButton:pressed { background-color: #1E88E5; }"
            "QPushButton:disabled { background-color: rgba(96, 128, 150, 120);"
            " color: #CFE6F5; }")
        self.claim_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.claim_btn.clicked.connect(self._on_claim_clicked)
        lay.addWidget(self.claim_btn)

        self.is_following = False
        self.mouse_drag_pos = QPoint()
        # 文字区域鼠标穿透：按住提示框文字也能拖动窗口（样式照常显示）
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # 按钮区域同样支持拖动（点击功能不受影响），用事件过滤器统一处理
        self._btn_drag_started = False
        self.installEventFilter(self)

    def refresh_text(self):
        """按今日是否已领刷新文案（启动与跨 0 点可能各触发一次，幂等）。"""
        claimed = self.pet.config.get("last_sign_in") == str(date.today())
        if claimed:
            self.label.setText(
                "✅ <b>今日数据碎片已领取</b>")
            self.claim_btn.setText("已领取")
            self.claim_btn.setEnabled(False)
        else:
            self.label.setText(
                "📰 <b>每日上线奖励</b><br>"
                " <font color='#7FD6FF'><b>50</b> 数据碎片</font>")
            self.claim_btn.setText("点击领取")
            self.claim_btn.setEnabled(True)
        self.adjustSize()

    def _on_claim_clicked(self):
        # 领取逻辑在桌宠侧，点击时会再核对一次“今日是否已领”。
        self.pet.claim_daily_signin()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_text()
        self._place_near_pet()

    def _place_near_pet(self):
        """贴在桌宠本体左侧（气泡在上方，避免重叠）；左边放不下则贴右侧。"""
        pet = self.pet
        self.adjustSize()
        w, h = self.width(), self.height()
        sg = (QApplication.screenAt(pet.geometry().center())
              or QApplication.primaryScreen()).availableGeometry()
        x = pet.x() - w - 10
        if x < sg.left():
            x = pet.x() + pet.width() + 10
        x = max(sg.left(), min(x, sg.right() - w + 1))
        y = max(sg.top(), pet.y())
        y = min(y, sg.bottom() - h + 1)
        self.move(x, y)

    # ---- 可拖动（与 FocusOverlay/ImageBubble 一致） ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_following = True
            self.mouse_drag_pos = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.is_following and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self.mouse_drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_following = False
        super().mouseReleaseEvent(event)

    # ---- 按钮区域也可拖动（按住移动即拖动窗口，原地松开仍是点击） ----
    def eventFilter(self, obj, event):
        if isinstance(obj, QPushButton):
            t = event.type()
            if (t == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                self._btn_drag_started = False
                self.is_following = True
                self.mouse_drag_pos = (event.globalPosition().toPoint()
                                       - self.pos())
                return False  # 让按钮正常收到按下，准备点击
            if t == QEvent.Type.MouseMove:
                if self.is_following and (event.buttons()
                                          & Qt.MouseButton.LeftButton):
                    delta = (event.globalPosition().toPoint() - self.pos()
                             - self.mouse_drag_pos)
                    if delta.manhattanLength() > 3:
                        self._btn_drag_started = True
                        obj.setDown(False)  # 取消按钮的按下高亮
                    if self._btn_drag_started:
                        self.move(event.globalPosition().toPoint()
                                  - self.mouse_drag_pos)
                        return True  # 吞掉移动，避免按钮误响应
            if (t == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton):
                was_drag = self._btn_drag_started
                self.is_following = False
                self._btn_drag_started = False
                if was_drag:
                    obj.setDown(False)
                    return True  # 拖动结束：吞掉松开，防止误触发点击
        return super().eventFilter(obj, event)
