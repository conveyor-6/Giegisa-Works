from .common import *

class EditNoteDialog(QDialog):
    """便签热编辑面板 —— 非模态，保存后通过信号通知父窗口刷新。"""
    saved = pyqtSignal()

    def __init__(self, parent, note):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.parent_dialog = parent
        self.note = note
        self.setWindowTitle("✏️ 编辑便签")
        self.setWindowFlags(Qt.WindowType.Dialog)
        self.resize(400, 300)
        lay = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setPlainText(note.get("text", ""))
        lay.addWidget(self.text_edit)

        btn = QPushButton("💾 保存修改")
        btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px; font-weight: bold;")
        btn.clicked.connect(self.save_edit)
        lay.addWidget(btn)

    def save_edit(self):
        self.note["text"] = self.text_edit.toPlainText().strip()
        self.saved.emit()
        self.accept()

class QuickNoteDialog(QDialog):
    def __init__(self, parent_pet, folder="默认便签"):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.folder = folder
        title = "📝 随手记 (便签)"
        if folder != "默认便签":
            title += f" → 存入分组【{folder}】"
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.WindowType.Tool)
        self.setWindowOpacity(0.92)
        self.resize(300, 160)
        lay = QVBoxLayout(self)

        self.input_box = QTextEdit()
        self.input_box.setPlaceholderText("随时记录一闪而过的灵感或笔记...")
        lay.addWidget(self.input_box)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("💾 记录")
        save_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        save_btn.clicked.connect(self.save_note)

        mgr_btn = QPushButton("🗂️ 管理便签")
        mgr_btn.clicked.connect(lambda checked=False: self.pet.open_dialog(NotesManagerDialog))

        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(mgr_btn)
        lay.addLayout(btn_layout)

    def save_note(self):
        text = self.input_box.toPlainText().strip()
        if not text: return

        txt_path = os.path.join(BASE_DIR, "notes.txt")
        # notes.txt 只是方便人直接阅读的额外备份；即使目录只读或磁盘暂时
        # 不可写，也不能影响便签本身保存到 config.json。
        try:
            with open(txt_path, 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
        except Exception:
            pass  # notes.txt 只是额外备份，写入失败不影响主流程

        self.pet.config.setdefault("notes", []).append({
            "id": new_id(),
            "time": datetime.now().strftime('%Y-%m-%d %H:%M'),
            "text": text,
            "status": "active",
            "folder": self.folder,
            "pinned": False,
            "locked": False
        })
        self.input_box.clear()
        save_config(self.pet.config)
        self.pet.inject_system_event(
            "系统：用户记录了一条便签",
            "【normal】已将你的杂念转录至底层存储区。")
        self.pet.refresh_dialogs("dlg_NotesManagerDialog")
        self.accept()

class NotesManagerDialog(QDialog):
    """【重构】带有分组、置顶、排序的终极便签管理器"""
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("🗂️ 便签管理与归档")
        self.setMinimumSize(520, 380)
        self.resize(720, 480)
        lay = QVBoxLayout(self)
        self.current_folder = "默认便签"
        
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("📂 分组:"))
        
        self.folder_combo = QComboBox()
        self.folder_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.update_folder_combo()
        self.folder_combo.currentIndexChanged.connect(self.on_folder_change)
        folder_layout.addWidget(self.folder_combo, stretch=1)

        folder_layout.addWidget(QLabel("↕️ 排序:"))
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["最新创建优先", "最早创建优先"])
        self.sort_combo.currentIndexChanged.connect(self.refresh_list)
        folder_layout.addWidget(self.sort_combo)
        lay.addLayout(folder_layout)

        folder_actions = QHBoxLayout()

        self.btn_new_note = QPushButton("➕ 新建便签")
        self.btn_new_note.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        self.btn_new_note.setToolTip(f"快速记一条新便签，自动归入当前分组【{self.current_folder}】")
        self.btn_new_note.clicked.connect(self.new_note_in_folder)
        folder_actions.addWidget(self.btn_new_note)

        btn_new_folder = QPushButton("➕新建分组")
        btn_new_folder.clicked.connect(self.new_folder)
        folder_actions.addWidget(btn_new_folder)
        
        btn_rename_folder = QPushButton("✏️重命名")
        btn_rename_folder.clicked.connect(self.rename_folder)
        folder_actions.addWidget(btn_rename_folder)
        
        btn_del_folder = QPushButton("❌删除当前分组")
        btn_del_folder.setStyleSheet("color: red;")
        btn_del_folder.clicked.connect(self.delete_folder)
        folder_actions.addWidget(btn_del_folder)
        folder_actions.addStretch()
        lay.addLayout(folder_actions)

        # ---- 检索栏 + 便签条数总览 ----
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 搜索便签内容...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.refresh_list)
        search_row.addWidget(self.search_input, stretch=1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #6c8193; font-weight: bold;")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        search_row.addWidget(self.count_label)
        lay.addLayout(search_row)

        self.list_widget = ResponsiveListWidget()
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_item_menu)
        lay.addWidget(self.list_widget)

        # ---- 分页（条目过多时防止一次性渲染全部导致卡顿）----
        self.page_size = 20
        self.current_page = 0
        page_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀ 上一页")
        self.prev_btn.clicked.connect(self._go_prev_page)
        self.page_label = QLabel("第 1 / 1 页")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setStyleSheet("color: #6c8193; font-weight: bold;")
        self.next_btn = QPushButton("下一页 ▶")
        self.next_btn.clicked.connect(self._go_next_page)
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(self.page_label, stretch=1)
        page_row.addWidget(self.next_btn)
        lay.addLayout(page_row)

        btn_layout = QHBoxLayout()
        self.export_btn = QPushButton("💾 导出当前分组")
        self.export_btn.clicked.connect(self.export_notes)

        self.import_btn = QPushButton("📂 导入至当前分组")
        self.import_btn.clicked.connect(self.import_notes)

        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.import_btn)
        lay.addLayout(btn_layout)
        
        self.refresh_list()

    def update_folder_combo(self):
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        folders = self.pet.config.setdefault("note_folders", ["默认便签"])
        if "默认便签" not in folders:
            folders.insert(0, "默认便签")
        self.folder_combo.addItems(folders)
        self.folder_combo.setCurrentText(self.current_folder)
        self.folder_combo.blockSignals(False)

    def on_folder_change(self):
        self.current_folder = self.folder_combo.currentText()
        # 同步更新「新建便签」按钮的提示，表明新便签将归入哪个分组
        self.btn_new_note.setToolTip(f"快速记一条新便签，自动归入当前分组【{self.current_folder}】")
        self.refresh_list()

    def new_note_in_folder(self):
        """打开随手记窗口，新便签保存时自动归入当前分组。"""
        self.pet.open_dialog(QuickNoteDialog, self.current_folder)

    def new_folder(self):
        ask_text(self, "新建便签分组", "请输入分组名称:", self._add_folder)

    def _add_folder(self, text):
        text = text.strip()
        if text and text not in self.pet.config["note_folders"]:
            self.pet.config["note_folders"].append(text)
            save_config(self.pet.config)
            self.update_folder_combo()
            self.folder_combo.setCurrentText(text)

    def delete_folder(self):
        """删除便签分组并将内部便签转移至默认区域"""
        if self.current_folder == "默认便签":
            show_warning(self, "禁止操作", "【默认便签】为系统基础分组，无法被删除！")
            return
        folder = self.current_folder
        ask_yes_no(
            self, '确认删除',
            f'确定要删除分组【{folder}】吗？\n为防止数据丢失，该分组下的所有便签将被安全转移至【默认便签】！',
            lambda: self._do_delete_folder(folder))

    def _do_delete_folder(self, folder):
        # 1. 遍历并转移该分类下的便签
        for note in self.pet.config.get("notes", []):
            if note.get("folder") == folder:
                note["folder"] = "默认便签"

        # 2. 删除文件夹记录
        if folder in self.pet.config.get("note_folders", []):
            self.pet.config["note_folders"].remove(folder)

        save_config(self.pet.config)
        if self.current_folder == folder:
            self.current_folder = "默认便签"
        self.update_folder_combo()
        self.refresh_list()
        show_info(self, "成功", "分组已删除，内部便签已转移至【默认便签】。")

    def rename_folder(self):
        if self.current_folder == "默认便签":
            show_warning(self, "禁止操作", "【默认便签】为系统基础兼全局分组，无法重命名！")
            return
        old_name = self.current_folder
        ask_text(self, "重命名分组", f"将【{old_name}】重命名为:",
                 lambda new_name: self._do_rename_folder(old_name, new_name),
                 text=old_name)

    def _do_rename_folder(self, old_name, new_name):
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if new_name in self.pet.config["note_folders"]:
            show_warning(self, "错误", "该分组名称已存在！")
            return
        # 同步修改所有相关便签
        for n in self.pet.config.get("notes", []):
            if n.get("folder") == old_name:
                n["folder"] = new_name
        # 修改目录名单
        folders = self.pet.config["note_folders"]
        if old_name in folders:
            folders[folders.index(old_name)] = new_name
        save_config(self.pet.config)

        if self.current_folder == old_name:
            self.current_folder = new_name
        self.update_folder_combo()
        self.refresh_list()
        show_info(self, "成功", "重命名成功！")

    def refresh_list(self):
        self.list_widget.clear()
        all_notes = self.pet.config.get("notes", [])
        keyword = getattr(self, "search_input", None)
        keyword = keyword.text().strip().lower() if keyword else ""
        filtered_notes = []
        total_count = 0
        folder_count = 0
        
        # 兼容旧版本数据并过滤
        for n in all_notes:
            if "folder" not in n: n["folder"] = "默认便签"
            if "pinned" not in n: n["pinned"] = False
            if "locked" not in n: n["locked"] = False
            if "status" not in n: n["status"] = "active"

            # 已隐藏（归档）的便签不参与统计与展示
            if n["status"] == "hidden":
                continue
            total_count += 1

            # 当前分组内的条数（“默认便签”代表全部分组）
            if self.current_folder == "默认便签" or n["folder"] == self.current_folder:
                folder_count += 1

            # 分组 + 检索双重过滤（检索不区分大小写，匹配便签正文）
            if (self.current_folder == "默认便签" or n["folder"] == self.current_folder) \
                    and (not keyword or keyword in str(n.get("text", "")).lower()):
                filtered_notes.append(n)

        # 更新条数总览
        shown_count = len(filtered_notes)
        if hasattr(self, "count_label"):
            self.count_label.setText(
                f"📊 共 {total_count} 条便签 · 当前分组 {folder_count} 条"
                + (f" · 检索到 {shown_count} 条" if keyword else ""))

        is_desc = (self.sort_combo.currentIndex() == 0)
        filtered_notes.sort(key=lambda x: x.get("time", ""), reverse=is_desc)
        filtered_notes.sort(key=lambda x: x.get("pinned", False), reverse=True)

        # 分页：只渲染当前页的便签，防止条目过多一次性渲染造成卡顿
        total_pages = max(1, (len(filtered_notes) + self.page_size - 1) // self.page_size)
        if self.current_page >= total_pages:
            self.current_page = total_pages - 1
        if self.current_page < 0:
            self.current_page = 0
        start = self.current_page * self.page_size
        page_notes = filtered_notes[start:start + self.page_size]

        # 记录与列表项一一对应的真实便签引用（QListWidgetItem.setData 会深拷贝
        # Python 对象，右键菜单必须按行号回查此处，才能操作 config 中的原对象）。
        # 注意：分页后列表里只有当前页的条目，因此这里只保存当前页的便签。
        self._note_order = list(page_notes)
        self._update_page_controls(total_pages)

        for note in page_notes:
            item_widget = QWidget()
            item_layout = QVBoxLayout(item_widget)
            item_layout.setContentsMargins(6, 4, 6, 4)
            item_layout.setSpacing(4)
            
            pin_icon = "📌" if note["pinned"] else "📝"
            lbl = QLabel(f"[{note.get('time', '')}] {pin_icon}\n{note.get('text', '')}")
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            # 关键：不让 QLabel 拦截右键（默认会弹 Copy/Select All 文本菜单），
            # 改为把右键坐标转给 _show_item_menu 的五项功能菜单
            lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lbl.customContextMenuRequested.connect(
                lambda pos, w=lbl: self._widget_context_menu(w, pos))
            item_layout.addWidget(lbl)
            
            locked = note["locked"]
            button_grid = QGridLayout()
            button_grid.setContentsMargins(0, 0, 0, 0)
            button_grid.setHorizontalSpacing(4)
            button_grid.setVerticalSpacing(3)
            
            btn_edit = QPushButton("✏️编辑")
            btn_edit.setEnabled(not locked)
            btn_edit.clicked.connect(lambda checked=False, n=note: self.open_editor(n))
            
            btn_pin = QPushButton("❌取消置顶" if note["pinned"] else "📌置顶")
            btn_pin.clicked.connect(lambda checked=False, n=note: self.toggle_pin(n))
            
            btn_lock = QPushButton("🔓解锁" if locked else "🔒锁定")
            btn_lock.clicked.connect(lambda checked=False, n=note: self.toggle_lock(n))
            
            btn_move = QPushButton("📂移动")
            btn_move.setEnabled(not locked)
            btn_move.clicked.connect(lambda checked=False, n=note: self.move_note(n))
            
            btn_del = QPushButton("❌删除")
            btn_del.setEnabled(not locked)
            btn_del.clicked.connect(lambda checked=False, n=note: self.del_note(n))

            # 按钮也统一走自定义右键菜单（点击按钮空白处右键同样呼出五项菜单）
            for _btn in (btn_edit, btn_pin, btn_lock, btn_move, btn_del):
                _btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                _btn.customContextMenuRequested.connect(
                    lambda pos, w=_btn: self._widget_context_menu(w, pos))

            for col, button in enumerate((btn_edit, btn_pin, btn_lock)):
                button.setMinimumHeight(34)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                button_grid.addWidget(button, 0, col)
            for col, button in enumerate((btn_move, btn_del)):
                button.setMinimumHeight(34)
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
                button_grid.addWidget(button, 1, col)
            item_layout.addLayout(button_grid)
            item_widget.setMinimumHeight(max(118, item_widget.sizeHint().height()))
            # item_widget 自身也转发右键（覆盖禁用按钮等不消费鼠标事件的区域）
            item_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            item_widget.customContextMenuRequested.connect(
                lambda pos, w=item_widget: self._widget_context_menu(w, pos))
            
            item = QListWidgetItem()
            item.setSizeHint(item_widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, item_widget)

    def _go_prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_list()

    def _go_next_page(self):
        self.current_page += 1
        self.refresh_list()

    def _update_page_controls(self, total_pages):
        """刷新分页控件状态（总页数、当前页、按钮可用性）。"""
        self.prev_btn.setEnabled(self.current_page > 0)
        self.next_btn.setEnabled(self.current_page < total_pages - 1)
        self.page_label.setText(f"第 {self.current_page + 1} / {total_pages} 页")

    def _widget_context_menu(self, widget, pos):
        """item 内子控件（QLabel/按钮）的右键坐标换算成列表视口坐标后呼出菜单。

        列表项使用 setItemWidget 自定义 widget，其中的 QLabel 默认会拦截右键
        弹出系统文本菜单（Copy/Select All）。这里把 CustomContextMenu 信号
        的坐标统一换算到 viewport，复用 _show_item_menu 的五项功能菜单。
        """
        global_pos = widget.mapToGlobal(pos)
        viewport_pos = self.list_widget.viewport().mapFromGlobal(global_pos)
        self._show_item_menu(viewport_pos)

    def _show_item_menu(self, pos):
        """右键点击便签列表项时，呼出 编辑/置顶/锁定/移动/删除 菜单。"""
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        row = self.list_widget.row(item)
        note_order = getattr(self, "_note_order", [])
        if row < 0 or row >= len(note_order):
            return
        note = note_order[row]
        if note is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(MENU_QSS)
        locked = note.get("locked", False)

        act_edit = QAction("✏️ 编辑", menu)
        act_edit.setEnabled(not locked)
        act_edit.triggered.connect(lambda checked=False, n=note: self.open_editor(n))

        act_pin = QAction("❌ 取消置顶" if note.get("pinned", False) else "📌 置顶", menu)
        act_pin.triggered.connect(lambda checked=False, n=note: self.toggle_pin(n))

        act_lock = QAction("🔓 解锁" if locked else "🔒 锁定", menu)
        act_lock.triggered.connect(lambda checked=False, n=note: self.toggle_lock(n))

        act_move = QAction("📂 移动", menu)
        act_move.setEnabled(not locked)
        act_move.triggered.connect(lambda checked=False, n=note: self.move_note(n))

        act_del = QAction("❌ 删除", menu)
        act_del.setEnabled(not locked)
        act_del.triggered.connect(lambda checked=False, n=note: self.del_note(n))

        menu.addAction(act_edit)
        menu.addAction(act_pin)
        menu.addAction(act_lock)
        menu.addSeparator()
        menu.addAction(act_move)
        menu.addAction(act_del)
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def open_editor(self, note):
        dlg = EditNoteDialog(self, note)
        dlg.saved.connect(lambda: (
            save_config(self.pet.config),
            self.pet.refresh_dialogs("dlg_NotesManagerDialog"),
            self.refresh_list()))
        dlg.show()

    def toggle_pin(self, note):
        note["pinned"] = not note.get("pinned", False)
        save_config(self.pet.config)
        self.refresh_list()

    def toggle_lock(self, note):
        note["locked"] = not note.get("locked", False)
        save_config(self.pet.config)
        self.refresh_list()

    def move_note(self, note):
        folders = self.pet.config.setdefault("note_folders", ["默认便签"])
        ask_item(self, "移动便签", "请选择目标分组:", folders,
                 lambda folder_name: self._do_move_note(note, folder_name))

    def _do_move_note(self, note, folder_name):
        if not folder_name:
            return
        note["folder"] = folder_name
        save_config(self.pet.config)
        self.refresh_list()
        show_info(self, "成功", f"已移动至分组：{folder_name}")

    def del_note(self, note):
        self.pet.config["notes"].remove(note)
        save_config(self.pet.config)
        self.refresh_list()

    def export_notes(self):
        path, _ = QFileDialog.getSaveFileName(self, f"导出分组 {self.current_folder}", BASE_DIR, "JSON Files (*.json)")
        if path:
            try:
                data = [n for n in self.pet.config.get("notes", []) if n.get("folder", "默认便签") == self.current_folder]
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                show_info(self, "成功", "导出成功！")
            except Exception as e:
                show_critical(self, "失败", f"导出失败：{str(e)}")

    def import_notes(self):
        path, _ = QFileDialog.getOpenFileName(self, f"导入至 {self.current_folder}", BASE_DIR, "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    show_warning(self, "错误", "格式不正确：文件内容应该是一个列表。")
                    return
                good = []
                for d in data:
                    if not isinstance(d, dict) or "text" not in d:
                        continue
                    item = dict(d)
                    item["folder"] = self.current_folder
                    item["id"] = new_id()
                    item.setdefault("status", "active")
                    item.setdefault("pinned", False)
                    item.setdefault("locked", False)
                    good.append(item)
                if not good:
                    show_warning(self, "错误", "文件里没有可识别的便签记录。")
                    return
                self.pet.config.setdefault("notes", []).extend(good)
                save_config(self.pet.config)
                self.refresh_list()
                show_info(
                    self, "成功",
                    f"成功导入 {len(good)} 条便签；格式不正确的条目已跳过。")
            except Exception as e:
                show_critical(self, "失败", f"导入失败：{str(e)}")
