from .common import *
from .settings import MemorySettingsDialog

class CollectionManagerDialog(QDialog):
    """【统一管理的图鉴基类】"""
    def __init__(self, parent_pet, collection_key, title_name):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.collection_key = collection_key
        self.setWindowTitle(f"Giegisa - {title_name}")
        self.resize(550, 600)
        self.layout = QVBoxLayout(self)
        
        # --- 新增：搜索与排序 UI ---
        top_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字检索...")
        self.search_input.textChanged.connect(self.refresh_list)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["最新记录优先", "最早记录优先"])
        self.sort_combo.currentIndexChanged.connect(self.refresh_list)
        
        top_layout.addWidget(self.search_input)
        top_layout.addWidget(self.sort_combo)
        self.layout.addLayout(top_layout)
        # ----------------------
        
        self.list_widget = ResponsiveListWidget()
        self.list_widget.setSpacing(5)
        self.layout.addWidget(self.list_widget)
        
        # --- 分页（条目过多时防止一次性渲染全部导致卡顿）---
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
        self.layout.addLayout(page_row)
        
        btn_layout = QHBoxLayout()
        btn_export = QPushButton("💾 导出记录")
        btn_export.clicked.connect(self.export_data)
        
        btn_import = QPushButton("📂 导入记录")
        btn_import.clicked.connect(self.import_data)
        
        btn_layout.addWidget(btn_export)
        btn_layout.addWidget(btn_import)
        self.layout.addLayout(btn_layout)
        
        self.refresh_list()

    # 将整个 refresh_list 方法替换为以下代码：
    def refresh_list(self):
        self.list_widget.clear()
        items = self.pet.config.get(self.collection_key, [])
        
        # --- 新增：检索与排序逻辑 ---
        keyword = self.search_input.text().strip().lower()
        filtered_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if (keyword in str(item.get('content', '')).lower()
                    or keyword in str(item.get('date', '')).lower()):
                filtered_items.append(item)
                
        if self.sort_combo.currentIndex() == 0:
            filtered_items.reverse() # 倒序
            
        # 分页：只渲染当前页的条目，防止条目过多一次性渲染造成卡顿
        total_pages = max(1, (len(filtered_items) + self.page_size - 1) // self.page_size)
        if self.current_page >= total_pages:
            self.current_page = total_pages - 1
        if self.current_page < 0:
            self.current_page = 0
        start = self.current_page * self.page_size
        page_items = filtered_items[start:start + self.page_size]
        self._update_page_controls(total_pages)
            
        if not filtered_items:
            self.list_widget.addItem("📭 暂无符合条件的记录！")
        else:
            for i, t in enumerate(page_items):
                item_widget = QWidget()
                h_layout = QHBoxLayout(item_widget)
                h_layout.setContentsMargins(5,2,5,2)
                
                lbl = QLabel(f"[{t.get('date', '未知时间')}]\n{t.get('content', '')}")
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
                
                is_locked = t.get("locked", False)
                btn_v_layout = QVBoxLayout()
                
                btn_replay = QPushButton("🔁播放")
                btn_replay.setFixedWidth(65)
                btn_replay.clicked.connect(lambda checked=False, text=t.get('content', ''): self.pet.handle_api_reply(f"【normal】{text}"))
                
                btn_lock = QPushButton("🔓解锁" if is_locked else "🔒锁定")
                btn_lock.setFixedWidth(65)
                btn_lock.clicked.connect(lambda checked=False, item=t: self.toggle_lock(item))
                
                btn_del = QPushButton("❌删除")
                btn_del.setFixedWidth(65)
                btn_del.setEnabled(not is_locked)
                btn_del.clicked.connect(lambda checked=False, item=t: self.delete_item(item))
                
                btn_v_layout.addWidget(btn_replay)
                btn_v_layout.addWidget(btn_lock)
                btn_v_layout.addWidget(btn_del)
                
                h_layout.addWidget(lbl, stretch=1)
                h_layout.addLayout(btn_v_layout)
                
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

    def toggle_lock(self, item):
        item["locked"] = not item.get("locked", False)
        save_config(self.pet.config)
        self.refresh_list()
        
    def delete_item(self, item):
        self.pet.config[self.collection_key].remove(item)
        save_config(self.pet.config)
        self.refresh_list()
        
    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出数据", BASE_DIR, "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(self.pet.config.get(self.collection_key, []), f, ensure_ascii=False, indent=4)
                show_info(self, "成功", "数据已成功导出！")
            except Exception as e:
                show_critical(self, "失败", f"导出失败：{str(e)}")

    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入数据", BASE_DIR, "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    show_warning(self, "错误", "格式不正确：文件内容应该是一个列表。")
                    return
                good = [d for d in data if isinstance(d, dict) and "content" in d]
                bad = len(data) - len(good)
                if not good:
                    show_warning(self, "错误", "文件里没有含 content 字段的有效记录。")
                    return
                self.pet.config.setdefault(self.collection_key, []).extend(good)
                save_config(self.pet.config)
                self.refresh_list()
                show_info(
                    self, "成功",
                    f"成功导入 {len(good)} 条记录。"
                    + (f"\n另有 {bad} 条格式不正确，已跳过。" if bad else ""))
            except Exception as e:
                show_critical(self, "失败", f"导入失败：{str(e)}")

class StoreDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self._workers = []
        self.setWindowTitle("Giegisa - 数据交换商城 (Economy)")
        self.resize(420, 400)
        self.layout = QVBoxLayout(self)
        
        self.coin_label = QLabel(f"<h2>💰 当前资产：{self.pet.config['coins']} 数据碎片</h2>")
        self.layout.addWidget(self.coin_label)
        
        items = [
            ("⚙️ 异世界Token (50碎片) - 维持gisa的跨位面稳定性", 50, self.buy_cake),
            ("🧠 随机小知识盲盒 (100碎片) - 拓展Giegisa的数据库", 100, self.buy_trivia),
            ("🌌 Giegisa的数据调取 (200碎片) - 收集万千位面的见闻", 200, self.buy_data_retrieval)
        ]
        
        for name, price, func in items:
            btn = QPushButton(name)
            btn.setStyleSheet("padding: 10px; font-weight: bold;")
            btn.clicked.connect(lambda checked=False, p=price, f=func: self.attempt_buy(p, f))
            self.layout.addWidget(btn)
            
        self.layout.addSpacing(10)
        
        btn_layout1 = QHBoxLayout()
        box_btn = QPushButton("📦 查看储物盒子")
        box_btn.setStyleSheet("padding: 10px; background-color: #00dbde; color: white; font-weight: bold;")
        box_btn.clicked.connect(lambda checked=False: self.pet.open_dialog(CollectionManagerDialog, "collected_items", "储物盒子"))
        
        encyclopedia_btn = QPushButton("📚 查看小知识图鉴")
        encyclopedia_btn.setStyleSheet("padding: 10px; background-color: #da73eb; color: white; font-weight: bold;")
        encyclopedia_btn.clicked.connect(lambda checked=False: self.pet.open_dialog(CollectionManagerDialog, "collected_trivia", "小知识图鉴"))
        
        btn_layout1.addWidget(box_btn)
        btn_layout1.addWidget(encyclopedia_btn)
        
        btn_layout2 = QHBoxLayout()
        records_btn = QPushButton("📜 查看位面见闻录")
        records_btn.setStyleSheet("padding: 10px; background-color: #009de5; color: white; font-weight: bold;")
        records_btn.clicked.connect(lambda checked=False: self.pet.open_dialog(CollectionManagerDialog, "collected_plane_records", "位面见闻录"))
        btn_layout2.addWidget(records_btn)
        
        self.layout.addLayout(btn_layout1)
        self.layout.addLayout(btn_layout2)

    def _start_worker(self, worker, success_slot, error_slot):
        """保住所有仍在运行的线程，避免连续点击时旧线程被回收导致闪退。"""
        self._workers.append(worker)
        worker.result_ready.connect(success_slot)
        worker.error_occurred.connect(error_slot)
        worker.finished.connect(lambda w=worker: self._release_worker(w))
        worker.start()

    def _release_worker(self, worker):
        self._workers = [w for w in self._workers if w is not worker]
    
    def showEvent(self, event):
        super().showEvent(event)
        self.coin_label.setText(f"<h2>💰 当前资产：{self.pet.config['coins']} 数据碎片</h2>")
            
    def attempt_buy(self, price, action_func):
        if self.pet.config["coins"] >= price:
            self.pet.config["coins"] -= price
            save_config(self.pet.config)
            self.coin_label.setText(f"<h2>💰 当前资产：{self.pet.config['coins']} 数据碎片</h2>")
            action_func()
        else:
            show_warning(self, "余额不足", "你的数据碎片不够！快去专注工作或者签到赚取吧。")
            
    def buy_cake(self):
        self.pet.change_mood(30)
        show_info(self, "购买成功", "Giegisa的心情似乎变好了。")
        
        if random.random() < 0.6:
            self.pet.inject_system_event("系统：用户购买了异世界Token", "【shy】...谢谢，这个有用。但不要以为拿这种资源就能讨好我。\n【normal】利用这些token，我在其他世界拾取了一些东西。")
            self._start_worker(
                ItemRetrievalThread(self.pet.config),
                self.on_item_fetched,
                self.on_item_error)
        else:
            self.pet.inject_system_event("系统：用户购买了异世界Token", "【shy】...谢谢，这个有用。但不要以为拿这种资源就能讨好我。")
            
    def on_item_fetched(self, text):
        self.pet.config.setdefault("collected_items", []).append({"date": str(date.today()), "content": text})
        save_config(self.pet.config)
        self.pet.inject_system_event("系统：Giegisa拾取了一个新物品", f"【normal】空间置换完成。放入储物盒子：{text}")
        
        for attr_name in dir(self.pet):
            if attr_name.startswith("dlg_CollectionManagerDialog"):
                dlg = getattr(self.pet, attr_name)
                if dlg and hasattr(dlg, "refresh_list"):
                    try: dlg.refresh_list()
                    except: pass
        show_info(self, "拾取成功", "新物品已成功收取！快去储物盒子查看吧。")

    def on_item_error(self, err_msg):
        self.pet.config["coins"] += 50
        save_config(self.pet.config)
        self.coin_label.setText(f"<h2>💰 当前资产：{self.pet.config['coins']} 数据碎片</h2>")
        show_critical(self, "拾取失败", f"接口请求失败，已退还50数据碎片。\n错误信息：{err_msg}")
        self.pet.inject_system_event("系统：拾取物品失败", "【angry】跨位面通道不稳定，物品掉落到了虚空。")
        
    def buy_trivia(self):
        self.pet.inject_system_event("系统：用户触发了小知识检索", "【normal】正在连接外部知识库进行数据检索...")
        show_info(self, "检索中", "已扣除100数据碎片，Giegisa正在请求互联网知识库，请稍候...")
        self._start_worker(
            TriviaThread(self.pet.config),
            self.on_trivia_fetched,
            self.on_trivia_error)

    def on_trivia_fetched(self, text):
        self.pet.config.setdefault("collected_trivia", []).append({"date": str(date.today()), "content": text})
        save_config(self.pet.config)
        self.pet.inject_system_event("系统：Giegisa发现了一个新知识", f"【normal】数据库更新。录入新知识条目：{text}")
        
        for attr_name in dir(self.pet):
            if attr_name.startswith("dlg_CollectionManagerDialog"):
                dlg = getattr(self.pet, attr_name)
                if dlg and hasattr(dlg, "refresh_list"):
                    try: dlg.refresh_list()
                    except: pass
        show_info(self, "检索成功", "新知识已成功录入！快去小知识图鉴查看吧。")

    def on_trivia_error(self, err_msg):
        self.pet.config["coins"] += 100
        save_config(self.pet.config)
        self.coin_label.setText(f"<h2>💰 当前资产：{self.pet.config['coins']} 数据碎片</h2>")
        show_critical(self, "检索失败", f"接口请求失败，已退还100数据碎片。\n错误信息：{err_msg}")
        self.pet.inject_system_event("系统：检索小知识失败", "【angry】网络连接不稳定，检索任务被强制中断。")
        
    def buy_data_retrieval(self):
        self.pet.inject_system_event("系统：用户触发位面数据调取", "【normal】我在各个平行世界都有可用的接口，在不同的场所会收集到不同的讯息。接下来你所看到的内容，是万千位面中的某一段见闻。")
        show_info(self, "调取中", "已扣除200数据碎片，Giegisa正在调取平行世界的数据，请稍候...")
        self._start_worker(
            DataRetrievalThread(self.pet.config),
            self.on_data_fetched,
            self.on_data_error)

    def on_data_fetched(self, text):
        self.pet.config.setdefault("collected_plane_records", []).append({"date": str(date.today()), "content": text})
        save_config(self.pet.config)
        self.pet.inject_system_event("系统：Giegisa完成了位面数据调取", f"【normal】数据解析完毕，开始展示提取的记录档案：\n{text}")
        
        for attr_name in dir(self.pet):
            if attr_name.startswith("dlg_CollectionManagerDialog"):
                dlg = getattr(self.pet, attr_name)
                if dlg and hasattr(dlg, "refresh_list"):
                    try: dlg.refresh_list()
                    except: pass
        show_info(self, "调取成功", "新见闻已成功解析并录入！快去位面见闻录查看吧。")

    def on_data_error(self, err_msg):
        self.pet.config["coins"] += 200
        save_config(self.pet.config)
        self.coin_label.setText(f"<h2>💰 当前资产：{self.pet.config['coins']} 数据碎片</h2>")
        show_critical(self, "调取失败", f"接口请求失败，已退还200数据碎片。\n错误信息：{err_msg}")
        self.pet.inject_system_event("系统：调取位面数据失败", "【angry】位面锚点连接丢失，调取任务被强制中断。")

class HistoryDialog(QDialog):
    """【重构】带有档案室/收藏夹分类管理机制的高级记忆档案面板"""
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.chat_thread = parent_pet.chat_thread
        self.setWindowTitle("Giegisa - 记忆档案 (历史监控与回溯)")
        self.resize(500, 600)
        self.layout = QVBoxLayout(self)
        self.current_folder = "当前记忆"
        
        # --- 布局 1：收藏夹管理 ---
        control_layout1 = QHBoxLayout()
        control_layout1.addWidget(QLabel("📂 归档位置:"))
        self.folder_combo = QComboBox()
        self.update_folder_combo()
        self.folder_combo.currentIndexChanged.connect(self.on_folder_change)
        control_layout1.addWidget(self.folder_combo)
        
        btn_new_folder = QPushButton("➕新建收藏夹")
        btn_new_folder.clicked.connect(self.new_folder)
        control_layout1.addWidget(btn_new_folder)
        
        btn_rename_folder = QPushButton("✏️重命名")
        btn_rename_folder.clicked.connect(self.rename_folder)
        control_layout1.addWidget(btn_rename_folder)
        
        btn_del_folder = QPushButton("❌删除")
        btn_del_folder.setStyleSheet("color: red;")
        btn_del_folder.clicked.connect(self.delete_folder)
        control_layout1.addWidget(btn_del_folder)
        self.layout.addLayout(control_layout1)

        # --- 布局 2：多重筛选过滤 ---
        control_layout2 = QHBoxLayout()
        self.time_combo = QComboBox()
        self.time_combo.addItems(["🕒 显示最近 1 天", "🕒 显示最近 3 天", "🕒 显示最近 7 天", "📜 显示全部时间"])
        self.time_combo.setCurrentIndex(0) # 默认显示1天之内
        
        self.type_combo = QComboBox()
        self.type_combo.addItems(["显示全部记录", "💬 仅显示聊天对话", "⚙️ 仅显示系统/提示"])
        self.type_combo.currentIndexChanged.connect(self.refresh_list)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["最新记录优先 (倒序)", "最早记录优先 (正序)"])
        self.sort_combo.setCurrentIndex(0) # 默认倒序
        self.sort_combo.currentIndexChanged.connect(self.refresh_list)
        
        control_layout2.addWidget(self.time_combo)
        control_layout2.addWidget(self.type_combo)
        control_layout2.addWidget(self.sort_combo)
        self.layout.addLayout(control_layout2)
        
        # --- 布局 3：检索与一键清理 ---
        control_layout3 = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字检索对话内容...")
        self.search_input.textChanged.connect(self.refresh_list)
        
        self.quick_del_btn = QPushButton("🗑️ 快捷清理...")
        self.quick_del_btn.setStyleSheet("background-color: #ff4c4c; color: white;")
        self.setup_quick_delete_menu() 
        
        self.mem_mgr_btn = QPushButton("🧠 记忆摘要...")
        self.mem_mgr_btn.setStyleSheet("background-color: #3F51B5; color: white;")
        self.mem_mgr_btn.clicked.connect(lambda checked=False: self.pet.open_dialog(MemorySettingsDialog))
        
        control_layout3.addWidget(self.search_input)
        control_layout3.addWidget(self.quick_del_btn)
        control_layout3.addWidget(self.mem_mgr_btn)  # 塞进布局
        self.layout.addLayout(control_layout3)

        # ==========================================
        # 🚨 修复关键：补回丢失的列表控件和底部按钮
        # ==========================================
        self.list_widget = ResponsiveListWidget()
        self.layout.addWidget(self.list_widget)
        
        # --- 分页（条目过多时防止一次性渲染全部导致卡顿）---
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
        self.layout.addLayout(page_row)
        
        btn_layout = QHBoxLayout()
        self.export_btn = QPushButton("💾 导出当前分类")
        self.export_btn.clicked.connect(self.export_history)
        self.import_btn = QPushButton("📂 导入至当前分类")
        self.import_btn.clicked.connect(self.import_history)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addWidget(self.import_btn)
        self.layout.addLayout(btn_layout)

        # 必须在 list_widget 初始化完成后，再绑定时间筛选的信号，防止提前触发报错
        self.time_combo.currentIndexChanged.connect(self.refresh_list)
        
        # 首次加载刷新
        self.refresh_list()

    def save_mem_settings(self):
        self.pet.config["history_limit"] = self.limit_spin.value()
        self.pet.config["long_term_summary"] = self.summary_edit.toPlainText().strip()
        save_config(self.pet.config)
        # 实时同步给正在运行的聊天线程，打完字立刻生效
        if hasattr(self.pet, "chat_thread"):
            self.pet.chat_thread.update_config(self.pet.config)

    def update_folder_combo(self):
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem("当前记忆")
        folders = self.pet.config.setdefault("favorite_folders", {"默认收藏夹": []})
        self.folder_combo.addItems(folders.keys())
        self.folder_combo.setCurrentText(self.current_folder)
        self.folder_combo.blockSignals(False)

    def on_folder_change(self):
        self.current_folder = self.folder_combo.currentText()
        self.refresh_list()
        
    def new_folder(self):
        ask_text(self, "新建收藏夹", "请输入收藏夹名称:", self._add_folder)

    def _add_folder(self, text):
        text = text.strip()
        if text:
            self.pet.config.setdefault("favorite_folders", {})[text] = []
            save_config(self.pet.config)
            self.update_folder_combo()
            self.folder_combo.setCurrentText(text)

    def delete_folder(self):
        """删除当前记忆收藏夹"""
        if self.current_folder in ["当前记忆", "默认收藏夹"]:
            show_warning(self, "禁止操作", "系统基础分组无法被删除！")
            return
        folder = self.current_folder
        ask_yes_no(
            self, '确认删除',
            f'确定要彻底删除收藏夹【{folder}】吗？\n警告：内部的所有聊天记录将被永久清空！',
            lambda: self._do_delete_folder(folder))

    def _do_delete_folder(self, folder):
        self.pet.config.get("favorite_folders", {}).pop(folder, None)
        save_config(self.pet.config)
        if self.current_folder == folder:
            self.current_folder = "当前记忆"
        self.update_folder_combo()
        self.refresh_list()
        show_info(self, "成功", "该收藏夹已被彻底抹除。")

    def rename_folder(self):
        if self.current_folder in ["当前记忆", "默认收藏夹"]:
            show_warning(self, "禁止操作", "系统基础分组无法重命名！")
            return
        old_name = self.current_folder
        ask_text(self, "重命名收藏夹", f"将【{old_name}】重命名为:",
                 lambda new_name: self._do_rename_folder(old_name, new_name),
                 text=old_name)

    def _do_rename_folder(self, old_name, new_name):
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        if new_name in self.pet.config.get("favorite_folders", {}):
            show_warning(self, "错误", "该收藏夹名称已存在！")
            return
        # 变更字典键名
        self.pet.config["favorite_folders"][new_name] = self.pet.config["favorite_folders"].pop(old_name)
        save_config(self.pet.config)

        if self.current_folder == old_name:
            self.current_folder = new_name
        self.update_folder_combo()
        self.refresh_list()
        show_info(self, "成功", "重命名成功！")

    def refresh_list(self):
        self.list_widget.clear()
        if self.current_folder == "当前记忆":
            data_source = self.chat_thread.history
            is_fav_mode = False
        else:
            data_source = self.pet.config["favorite_folders"].get(self.current_folder, [])
            is_fav_mode = True
            
        keyword = self.search_input.text().strip().lower()
        time_idx = self.time_combo.currentIndex()
        filter_idx = self.type_combo.currentIndex()
        
        # 计算时间截断点
        current_ts = time.time()
        if time_idx == 0: cutoff_ts = current_ts - 86400 * 1
        elif time_idx == 1: cutoff_ts = current_ts - 86400 * 3
        elif time_idx == 2: cutoff_ts = current_ts - 86400 * 7
        else: cutoff_ts = 0
        
        # 组装成对数据，以便整段排序
        paired_data = []
        for i in range(0, len(data_source), 2):
            if i + 1 < len(data_source):
                paired_data.append((i, data_source[i], data_source[i+1]))
                
        # 排序控制 (如果选了最新优先，则翻转列表)
        if self.sort_combo.currentIndex() == 0:
            paired_data.reverse()
            
        # 第一遍：先过滤，收集符合条件的数据（渲染阶段只处理当前页）
        page_candidates = []  # (original_idx, user_data, ai_data)
        for original_idx, user_data, ai_data in paired_data:
            user_msg = user_data.get("content", "")
            ai_msg = ai_data.get("content", "")
            locked = user_data.get("locked", False)
            msg_ts = user_data.get("timestamp", 0)
            
            # --- 拦截特定功能后台提示词 ---
            if "识别图片" in user_msg or "识图" in user_msg:
                continue

            # --- 时间范围过滤 ---
            if time_idx != 3: # 只要不是选“全部时间”
                # 没有时间戳的旧数据(ts=0) 或 早于截断时间的数据直接跳过
                if msg_ts == 0 or msg_ts < cutoff_ts:
                    continue
            
            # --- 系统记录与对话分类过滤 ---
            is_system = user_msg.startswith("（系统：")
            if filter_idx == 1 and is_system: continue
            if filter_idx == 2 and not is_system: continue
            
            # --- 关键词检索过滤 ---
            if keyword and keyword not in user_msg.lower() and keyword not in ai_msg.lower():
                continue
            
            page_candidates.append((original_idx, user_data, ai_data))
            
        # 分页：只渲染当前页的条目，防止条目过多一次性渲染造成卡顿
        total_pages = max(1, (len(page_candidates) + self.page_size - 1) // self.page_size)
        if self.current_page >= total_pages:
            self.current_page = total_pages - 1
        if self.current_page < 0:
            self.current_page = 0
        start = self.current_page * self.page_size
        page_candidates = page_candidates[start:start + self.page_size]
        self._update_page_controls(total_pages)
        
        for original_idx, user_data, ai_data in page_candidates:
            user_msg = user_data.get("content", "")
            ai_msg = ai_data.get("content", "")
            locked = user_data.get("locked", False)
            msg_ts = user_data.get("timestamp", 0)
            is_system = user_msg.startswith("（系统：")
                
            item_widget = QWidget()
            h_layout = QHBoxLayout(item_widget)
            
            # 视觉区分：系统记录与普通对话
            if is_system:
                text_label = QLabel(f"<b style='color:#888;'>[系统机制]</b> <span style='color:#666;'>{user_msg}</span><br><b style='color:#888;'>Giegisa:</b> <span style='color:#666;'>{ai_msg}</span>")
            else:
                text_label = QLabel(f"<b>You:</b> {user_msg}<br><b>Giegisa:</b> {ai_msg}")
                
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            
            btn_v_layout = QVBoxLayout()
            btn_replay = QPushButton("🔁播放")
            btn_replay.setFixedWidth(65)
            btn_replay.clicked.connect(lambda checked=False, text=ai_msg: self.pet.handle_api_reply(text))
            btn_v_layout.addWidget(btn_replay)
            
            if not is_fav_mode:
                btn_fav = QPushButton("⭐收藏")
                btn_fav.setFixedWidth(65)
                btn_fav.clicked.connect(lambda checked=False, idx=original_idx: self.favorite_record(idx))
                btn_v_layout.addWidget(btn_fav)
                
            btn_lock = QPushButton("🔓解锁" if locked else "🔒锁定")
            btn_lock.setFixedWidth(65)
            btn_lock.clicked.connect(lambda checked=False, idx=original_idx, fav=is_fav_mode: self.toggle_lock(idx, fav))
            btn_v_layout.addWidget(btn_lock)
            
            btn_del = QPushButton("❌删除")
            btn_del.setFixedWidth(65)
            btn_del.setEnabled(not locked)
            btn_del.clicked.connect(lambda checked=False, idx=original_idx, fav=is_fav_mode: self.delete_record(idx, fav))
            btn_v_layout.addWidget(btn_del)
            
            h_layout.addWidget(text_label, stretch=1)
            h_layout.addLayout(btn_v_layout)
            
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

    def setup_quick_delete_menu(self):
        menu = QMenu(self)
        actions = [
            ("清空全部 (保留已锁定记录)", 0),
            ("删除 1 天前", 1),
            ("删除 1 周前", 7),
            ("自定义天数...", -1)
        ]
        for text, days in actions:
            act = QAction(text, self)
            act.triggered.connect(lambda checked=False, d=days: self.execute_quick_delete(d))
            menu.addAction(act)
        self.quick_del_btn.setMenu(menu)

    def execute_quick_delete(self, days):
        if self.current_folder != "当前记忆":
            show_warning(self, "禁止操作", "快捷清理仅针对【当前记忆】生效。")
            return

        if days == -1:
            ask_text(self, "自定义清理", "请输入要删除多少天前的记录(纯数字，默认30):",
                     self._on_custom_delete_days)
            return
        self._run_quick_delete(days)

    def _on_custom_delete_days(self, val):
        val = val.strip()
        if not val.isdigit():
            show_warning(self, "错误", "请输入有效的数字！")
            return
        if self.current_folder != "当前记忆":
            return
        self._run_quick_delete(int(val))

    def _run_quick_delete(self, days):
        cutoff_time = time.time() - (days * 86400) if days > 0 else float('inf')
        history = self.chat_thread.history
        new_history = []
        deleted_count = 0

        for i in range(0, len(history), 2):
            if i + 1 < len(history):
                pair_locked = history[i].get("locked", False)
                msg_time = history[i].get("timestamp", 0)

                # 如果没有上锁，并且生成时间早于截断时间（没时间戳的旧数据默认视为 0，会被顺理成章地当旧数据清掉）
                if not pair_locked and msg_time <= cutoff_time:
                    deleted_count += 1
                    continue

                new_history.extend([history[i], history[i+1]])

        if deleted_count > 0:
            self.chat_thread.history = new_history
            self.chat_thread.save_history()
            self.refresh_list()
            show_info(self, "清理完成", f"已成功清理 {deleted_count} 组历史记录。")
        else:
            show_info(self, "清理完成", "没有找到符合条件（或未上锁）的记录。")

    def toggle_lock(self, idx, is_fav_mode):
        if is_fav_mode:
            data = self.pet.config["favorite_folders"][self.current_folder]
            state = not data[idx].get("locked", False)
            data[idx]["locked"] = state
            data[idx+1]["locked"] = state
            save_config(self.pet.config)
        else:
            state = not self.chat_thread.history[idx].get("locked", False)
            self.chat_thread.history[idx]["locked"] = state
            self.chat_thread.history[idx+1]["locked"] = state
            self.chat_thread.save_history()
        self.refresh_list()

    def favorite_record(self, idx):
        folders = list(self.pet.config.setdefault("favorite_folders", {"默认收藏夹": []}).keys())
        history = self.chat_thread.history
        if idx < 0 or idx + 1 >= len(history):
            return
        # 先抓取记录副本：弹窗是非模态的，确认期间聊天历史可能追加新消息。
        record_pair = [dict(history[idx]), dict(history[idx + 1])]
        ask_item(self, "选择收藏夹", "请选择目标收藏夹:", folders,
                 lambda folder_name: self._do_favorite_record(record_pair, folder_name))

    def _do_favorite_record(self, record_pair, folder_name):
        if not folder_name:
            return
        self.pet.config["favorite_folders"][folder_name].extend(record_pair)
        save_config(self.pet.config)
        self.update_folder_combo()
        show_info(self, "成功", f"已收藏至【{folder_name}】")

    def delete_record(self, idx, is_fav_mode):
        if is_fav_mode:
            folder = self.pet.config["favorite_folders"][self.current_folder]
            del folder[idx:idx+2]
            save_config(self.pet.config)
        else: 
            self.chat_thread.delete_history_item(idx // 2)
        self.refresh_list()

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, f"导出 {self.current_folder}", BASE_DIR, "JSON Files (*.json)")
        if path:
            try:
                data = self.chat_thread.history if self.current_folder == "当前记忆" else self.pet.config["favorite_folders"][self.current_folder]
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                show_info(self, "成功", "导出成功！")
            except Exception as e:
                show_critical(self, "失败", f"导出失败：{str(e)}")

    def import_history(self):
        path, _ = QFileDialog.getOpenFileName(self, f"导入至 {self.current_folder}", BASE_DIR, "JSON Files (*.json)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    show_warning(self, "错误", "格式不正确：历史文件应该是一个列表。")
                    return
                # 历史界面按“一问一答”成对处理，只接受完整的 user/assistant 对。
                good = []
                for i in range(0, len(data) - 1, 2):
                    user_msg, ai_msg = data[i], data[i + 1]
                    if (isinstance(user_msg, dict)
                            and isinstance(ai_msg, dict)
                            and user_msg.get("role") == "user"
                            and ai_msg.get("role") == "assistant"
                            and isinstance(user_msg.get("content"), str)
                            and isinstance(ai_msg.get("content"), str)):
                        good.extend((dict(user_msg), dict(ai_msg)))
                if not good:
                    show_warning(self, "错误", "没有找到完整的“用户—Giegisa”对话记录。")
                    return
                if self.current_folder == "当前记忆":
                    self.chat_thread.history.extend(good)
                    self.chat_thread.save_history()
                else:
                    self.pet.config["favorite_folders"][self.current_folder].extend(good)
                    save_config(self.pet.config)
                self.refresh_list()
                show_info(self, "成功", f"成功导入 {len(good) // 2} 组对话。")
            except Exception as e:
                show_critical(self, "失败", f"导入失败：{str(e)}")
