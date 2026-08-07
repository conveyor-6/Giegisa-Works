from .common import *

class UserProfileDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("👤 个人档案与关系设定")
        self.resize(450, 300)
        self.layout = QVBoxLayout(self)
        
        form = QFormLayout()
        profile = self.pet.config.get("user_profile", {})
        
        self.nickname_input = QLineEdit(profile.get("nickname", ""))
        self.nickname_input.setPlaceholderText("例如: 小明 / conveyorXX")
        
        self.birthday_layout = QHBoxLayout()
        self.b_year = QSpinBox()
        self.b_year.setRange(1900, 2100)
        self.b_year.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.b_month = QSpinBox()
        self.b_month.setRange(1, 12)
        self.b_day = QSpinBox()
        self.b_day.setRange(1, 31)
        
        old_bd = profile.get("birthday", "")
        m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', old_bd)
        if m:
            self.b_year.setValue(int(m.group(1)))
            self.b_month.setValue(int(m.group(2)))
            self.b_day.setValue(int(m.group(3)))
        else:
            self.b_year.setValue(2000)
            
        self.birthday_layout.addWidget(self.b_year)
        self.birthday_layout.addWidget(QLabel("年"))
        self.birthday_layout.addWidget(self.b_month)
        self.birthday_layout.addWidget(QLabel("月"))
        self.birthday_layout.addWidget(self.b_day)
        self.birthday_layout.addWidget(QLabel("日"))
        
        self.callme_input = QLineEdit(profile.get("call_me", "默认"))
        self.callme_input.setPlaceholderText("例如: 主人 / 笨蛋 (Giegisa对你的称呼)")
        self.relation_input = QLineEdit(profile.get("relationship", ""))
        self.relation_input.setPlaceholderText("例：普通用户/conveyor/任何、初识/好奇/任何")
        
        form.addRow("你的昵称 (我是谁):", self.nickname_input)
        form.addRow("你的生日:", self.birthday_layout)
        form.addRow("希望Giegisa称呼我为:", self.callme_input)
        form.addRow("你与Giegisa的关系:", self.relation_input)
        
        self.layout.addLayout(form)
        self.layout.addWidget(QLabel("<font color='#666'>* 填写后，Giegisa会在接下来的对话中牢牢记住你的身份。</font>"))
        
        # ===== 反臆造守则(默认注意事项，默认折叠、可修改) =====
        self.note_toggle_btn = QPushButton("🔽 默认注意事项（可修改）")
        self.note_toggle_btn.setCheckable(True)
        self.note_toggle_btn.setStyleSheet("color: gray; border: none; text-align: left;")
        self.note_toggle_btn.toggled.connect(self.toggle_note_box)
        self.layout.addWidget(self.note_toggle_btn)
        
        self.note_edit = QTextEdit()
        self.note_edit.setPlaceholderText("留空则不启用。此处内容会作为“反臆造守则”融入Giegisa的底层设定，用来减少胡编乱造。")
        self.note_edit.setStyleSheet("color: #888888; font-size: 12px;")
        self.note_edit.setPlainText(self.pet.config.get("anti_hallucination_note", ""))
        self.note_edit.setFixedHeight(120)
        self.note_edit.hide()  # 默认折叠
        self.layout.addWidget(self.note_edit)
        
        save_btn = QPushButton("💾 保存并录入潜意识")
        save_btn.setStyleSheet("background-color: #3F51B5; color: white; padding: 8px; font-weight: bold;")
        save_btn.clicked.connect(self.save_profile)
        self.layout.addWidget(save_btn)

    def toggle_note_box(self, checked):
        if checked:
            self.note_edit.show()
            self.note_toggle_btn.setText("🔼 默认注意事项（可修改）")
        else:
            self.note_edit.hide()
            self.note_toggle_btn.setText("🔽 默认注意事项（可修改）")

    def save_profile(self):
        bd_str = f"{self.b_year.value()}年{self.b_month.value()}月{self.b_day.value()}日"
        nickname = self.nickname_input.text().strip()
        self.pet.config["user_profile"] = {
            "nickname": nickname,
            "birthday": bd_str,
            "call_me": self.callme_input.text().strip() or "默认",
            "relationship": self.relation_input.text().strip()
        }
        self.pet.config["anti_hallucination_note"] = self.note_edit.toPlainText().strip()
        if hasattr(self.pet, "chat_thread"):
            self.pet.chat_thread.update_config(self.pet.config)  # 让正在运行的对话立刻用上新守则
        save_config(self.pet.config)
        
        if "conveyor" in nickname.lower():
            self.pet.inject_system_event("系统：Conveyor档案录入成功", "【dark】底层权限验证通过。欢迎回来，conveyor。")
        else:
            self.pet.inject_system_event("系统：用户档案录入成功", "【normal】用户档案已更新，我会记住你的身份。")
            
        self.accept()
        self.pet.show_bubble("【normal】档案已录入。Gisa 已记住你的设定。")

class MoodDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("💖 机体状态")
        self.resize(350, 200)
        self.layout = QVBoxLayout(self)
        
        self.info_label = QLabel()
        self.update_display()
        self.layout.addWidget(self.info_label)
        
        if self.pet.total_mood >= 76:
            self.blush_btn = QPushButton("程序竟然给 Gisa 写了害羞待机" if not self.pet.config.get("allow_blush") else "关闭害羞待机")
            self.blush_btn.setStyleSheet("background-color: #ea77b1; color: white; padding: 8px; font-weight: bold;")
            self.blush_btn.clicked.connect(self.toggle_blush)
            self.layout.addWidget(self.blush_btn)
        
        # 【双轨重置按钮】
        reset_layout = QHBoxLayout()
        self.reset_session_btn = QPushButton("🔄 重置当前情绪(归50)")
        self.reset_session_btn.setStyleSheet("background-color: #4a9fe8; color: white; font-weight: bold;")
        self.reset_session_btn.clicked.connect(self.reset_session)
        
        self.reset_total_btn = QPushButton("🔄 重置长期羁绊(归50)")
        self.reset_total_btn.setStyleSheet("background-color: #2a3dc8; color: white; font-weight: bold;")
        self.reset_total_btn.clicked.connect(self.reset_total)
        
        reset_layout.addWidget(self.reset_session_btn)
        reset_layout.addWidget(self.reset_total_btn)
        self.layout.addLayout(reset_layout)
        # 开启实时刷新面板的定时器
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.update_display)
        self.refresh_timer.start(1000) # 每1秒刷新一次界面

    def hideEvent(self, event):
        self.refresh_timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_timer.start(1000)
        self.update_display()

    def update_display(self):
        # 纯内存计算，不触发磁盘存盘，保护硬盘且丝滑
        sess_c = getattr(self.pet, "session_clicks", 0)
        sess_k = getattr(self.pet, "session_keys", 0)
        
        # 历史落盘数据 + (当前会话总数 - 已经落盘的增量) = 实时真实总数
        tot_c = self.pet.config.get("total_clicks", 0) + (sess_c - getattr(self.pet, "_flushed_clicks", 0))
        tot_k = self.pet.config.get("total_keys", 0) + (sess_k - getattr(self.pet, "_flushed_keys", 0))
        
        hook_on = getattr(self.pet, "global_input_hook_active", False)
        scope = "全局监测中" if hook_on else "仅统计点击桌宠"
        info = f"<h2>当前沟通情绪 (本次)：{int(self.pet.session_mood)} / 100</h2>" \
               f"<h2>长期羁绊积累 (总计)：{int(self.pet.total_mood)} / 100</h2>" \
               f"<p>(沟通情绪 0-30: 烦躁 | 31-75: 平常 | 总好感 >=76: 解锁彩蛋)</p>" \
               f"<hr><p><b>🖱️ 生物电脉冲监测</b>（{scope}）<br>" \
               f"本次会话：鼠标 <b>{sess_c}</b> 次 / 键盘 <b>{sess_k}</b> 次<br>" \
               f"历史累计：鼠标 <b>{tot_c}</b> 次 / 键盘 <b>{tot_k}</b> 次</p>"
        self.info_label.setText(info)

    def toggle_blush(self):
        current = self.pet.config.get("allow_blush", False)
        self.pet.config["allow_blush"] = not current
        save_config(self.pet.config)
        self.pet.update_idle_face()
        self.blush_btn.setText("程序竟然给 Gisa 写了害羞待机" if current else "关闭害羞待机")
        
    def reset_session(self):
        self.pet.session_mood = 50.0
        self.pet.update_idle_face()
        self.update_display()
        show_info(self, "已重置", "单次沟通情绪已归零。")

    def reset_total(self):
        ask_yes_no(self, '确认', '确定要将长期羁绊重置为初见状态(50)吗？',
                   self._do_reset_total)

    def _do_reset_total(self):
        self.pet.total_mood = 50.0
        self.pet.config["total_mood"] = 50.0
        save_config(self.pet.config)
        self.pet.update_idle_face()
        self.update_display()
        if hasattr(self, 'blush_btn') and self.pet.total_mood < 76:
            self.blush_btn.hide()
        show_info(self, "已重置", "长期羁绊已重置为出厂初始状态。")

class DistractionSettingsDialog(QDialog):
    """【新增】精细化摸鱼拦截设置面板"""
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("⚙️ 摸鱼拦截设置")
        self.resize(380, 450)
        self.layout = QVBoxLayout(self)

        self.enable_cb = QComboBox()
        self.enable_cb.addItems(["✅ 开启全局拦截", "❌ 关闭全局拦截"])
        self.enable_cb.setCurrentIndex(0 if self.pet.config.get("distraction_intercept_enabled", True) else 1)
        self.enable_cb.currentIndexChanged.connect(self.toggle_enable)
        self.layout.addWidget(QLabel("专注期间防摸鱼系统开关:"))
        self.layout.addWidget(self.enable_cb)

        self.list_widget = QListWidget()
        self.list_widget.itemChanged.connect(self.save_toggles)
        self.layout.addWidget(QLabel("违禁词名单 (勾选生效，窗口标题包含即警告):"))
        self.layout.addWidget(self.list_widget)

        add_layout = QHBoxLayout()
        self.kw_input = QLineEdit()
        self.kw_input.setPlaceholderText("输入应用名或网站名 (如: bilibili、steam)")
        
        add_btn = QPushButton("➕添加并生效")
        add_btn.clicked.connect(self.add_kw)
        
        add_layout.addWidget(self.kw_input)
        add_layout.addWidget(add_btn)
        self.layout.addLayout(add_layout)

        del_btn = QPushButton("❌ 彻底删除选中的违禁词")
        del_btn.setStyleSheet("color: red;")
        del_btn.clicked.connect(self.del_kw)
        self.layout.addWidget(del_btn)

        self.refresh_list()

    def toggle_enable(self, idx):
        self.pet.config["distraction_intercept_enabled"] = (idx == 0)
        save_config(self.pet.config)

    def refresh_list(self):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        kws = self.pet.config.setdefault("distraction_keywords", {})
        for kw, is_enabled in kws.items():
            item = QListWidgetItem(kw)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if is_enabled else Qt.CheckState.Unchecked)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)

    def add_kw(self):
        kw = self.kw_input.text().strip()
        if kw:
            kws = self.pet.config.setdefault("distraction_keywords", {})
            if kw not in kws:
                kws[kw] = True
                save_config(self.pet.config)
                self.kw_input.clear()
                self.refresh_list()

    def del_kw(self):
        for item in self.list_widget.selectedItems():
            self.pet.config.get("distraction_keywords", {}).pop(item.text(), None)
        save_config(self.pet.config)
        self.refresh_list()

    def save_toggles(self, item):
        kws = self.pet.config.setdefault("distraction_keywords", {})
        kws[item.text()] = (item.checkState() == Qt.CheckState.Checked)
        save_config(self.pet.config)

class AutoEventSettingsDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("Giegisa - 自动化频率设置")
        self.resize(350, 250)
        self.layout = QVBoxLayout(self)
        
        form = QFormLayout()
        
        self.idle_enable_combo = QComboBox()
        self.idle_enable_combo.addItems(["开启", "关闭"])
        self.idle_enable_combo.setCurrentIndex(0 if self.pet.config.get("idle_chat_enabled", True) else 1)
        self.idle_min_spinner = QSpinBox()
        self.idle_min_spinner.setRange(1, 120)
        self.idle_min_spinner.setValue(self.pet.config.get("idle_chat_interval_min", 20))
        
        form.addRow("闲聊陪伴模式 (防冷场):", self.idle_enable_combo)
        form.addRow("无操作触发时长 (分钟):", self.idle_min_spinner)
        
        self.event_enable_combo = QComboBox()
        self.event_enable_combo.addItems(["开启", "关闭"])
        self.event_enable_combo.setCurrentIndex(0 if self.pet.config.get("event_enabled", True) else 1)
        self.event_min_spinner = QSpinBox()
        self.event_min_spinner.setRange(1, 240)
        self.event_min_spinner.setValue(self.pet.config.get("event_interval_min", 60))
        
        form.addRow("随机事件小剧场 (互动):", self.event_enable_combo)
        form.addRow("事件触发频率 (分钟):", self.event_min_spinner)
        
        self.note_enable_combo = QComboBox()
        self.note_enable_combo.addItems(["开启", "关闭"])
        self.note_enable_combo.setCurrentIndex(0 if self.pet.config.get("read_notes_enabled", True) else 1)
        self.note_min_spinner = QSpinBox()
        self.note_min_spinner.setRange(1, 240)
        self.note_min_spinner.setValue(self.pet.config.get("read_notes_interval_min", 30))
        
        form.addRow("随机复习便签 (提醒):", self.note_enable_combo)
        form.addRow("便签复习频率 (分钟):", self.note_min_spinner)
        
        # 新增朗读范围选择
        self.note_folder_combo = QComboBox()
        folders = ["所有便签"] + self.pet.config.get("note_folders", ["默认便签"])
        self.note_folder_combo.addItems(folders)
        curr_read_folder = self.pet.config.get("read_notes_folder", "所有便签")
        if curr_read_folder in folders:
            self.note_folder_combo.setCurrentText(curr_read_folder)
        else:
            self.note_folder_combo.setCurrentText("所有便签")
        form.addRow("朗读便签范围:", self.note_folder_combo)
        
        # 新增全局日程提醒控制
        self.schedule_enable_combo = QComboBox()
        self.schedule_enable_combo.addItems(["开启", "关闭"])
        self.schedule_enable_combo.setCurrentIndex(0 if self.pet.config.get("schedule_reminder_enabled", True) else 1)
        form.addRow("日程待办到点提醒 (全局):", self.schedule_enable_combo)
        
        self.chime_enable_combo = QComboBox()
        self.chime_enable_combo.addItems(["开启", "关闭"])
        self.chime_enable_combo.setCurrentIndex(0 if self.pet.config.get("hourly_chime_enabled", True) else 1)
        
        form.addRow("整点报时 (自动播报):", self.chime_enable_combo)
        self.layout.addLayout(form)
        
        save_btn = QPushButton("💾 保存自动化设置")
        save_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 8px; font-weight: bold;")
        save_btn.clicked.connect(self.save_settings)
        self.layout.addWidget(save_btn)

    def save_settings(self):
        self.pet.config["idle_chat_enabled"] = (self.idle_enable_combo.currentIndex() == 0)
        self.pet.config["idle_chat_interval_min"] = self.idle_min_spinner.value()
        self.pet.config["event_enabled"] = (self.event_enable_combo.currentIndex() == 0)
        self.pet.config["event_interval_min"] = self.event_min_spinner.value()
        self.pet.config["read_notes_enabled"] = (self.note_enable_combo.currentIndex() == 0)
        self.pet.config["read_notes_interval_min"] = self.note_min_spinner.value()
        self.pet.config["read_notes_folder"] = self.note_folder_combo.currentText()  # 新增保存文件夹
        self.pet.config["schedule_reminder_enabled"] = (self.schedule_enable_combo.currentIndex() == 0)
        self.pet.config["hourly_chime_enabled"] = (self.chime_enable_combo.currentIndex() == 0)
        
        save_config(self.pet.config)
        self.pet.idle_seconds = 0
        self.pet.event_seconds = 0
        self.pet.note_seconds = 0
        self.accept()
        self.pet.show_bubble("【normal】自动化参数已更新，所有触发时钟已重置。")

class ApiSettingsDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("Giegisa - 核心数据与接口设置 (API/人设)")
        self.resize(550, 620)
        self.layout = QVBoxLayout(self)
        
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["使用 Gemini 官方引擎", "使用 OpenAI 兼容引擎 (硅基/DeepSeek等)"])
        self.engine_combo.setCurrentIndex(0 if self.pet.config.get("api_type") == "gemini" else 1)
        self.engine_combo.currentIndexChanged.connect(self.on_engine_changed)  # 切换时联动显示
        self.layout.addWidget(QLabel("选择当前激活的AI引擎："))
        self.layout.addWidget(self.engine_combo)

        self.gemini_group = QGroupBox("Gemini 引擎设置")
        g_layout = QFormLayout()
        
        self.g_key_input = QLineEdit(self.pet.config.get("gemini_api_key", ""))
        g_layout.addRow("Gemini API Key:", self.g_key_input)
        
        self.g_proxy_input = QLineEdit(self.pet.config.get("gemini_proxy", ""))
        self.g_model_input = QLineEdit(self.pet.config.get("gemini_model_name", "gemini-3.5-flash"))
        self.g_model_input.setPlaceholderText("例如: gemini-3.5-flash（识图请用支持视觉的模型）")
        g_layout.addRow("模型名称:", self.g_model_input)
        
        self.g_proxy_input.setPlaceholderText("可选；挂梯时可填 http://127.0.0.1:7890，留空自动走系统代理")
        g_layout.addRow("强制本地代理:", self.g_proxy_input)
        
        self.gemini_group.setLayout(g_layout)
        self.layout.addWidget(self.gemini_group)

        self.openai_group = QGroupBox("OpenAI 兼容引擎设置")
        o_layout = QFormLayout()
        
        self.o_key_input = QLineEdit(self.pet.config.get("openai_api_key", ""))
        self.o_url_input = QLineEdit(self.pet.config.get("openai_base_url", ""))
        self.o_model_input = QLineEdit(self.pet.config.get("openai_model_name", ""))
        self.o_model_input.setPlaceholderText("识图请填支持视觉(VL)的模型名")
        
        self.o_main_vision_check = QCheckBox("主模型支持读图（贴图时直接用主模型识图）")
        self.o_main_vision_check.setChecked(self.pet.config.get("openai_main_vision", True))

        o_layout.addRow("API Key:", self.o_key_input)
        o_layout.addRow("接口地址:", self.o_url_input)
        o_layout.addRow("模型名称:", self.o_model_input)
        o_layout.addRow("读图能力:", self.o_main_vision_check)

        self.openai_group.setLayout(o_layout)
        self.layout.addWidget(self.openai_group)

        self.vision_group = QGroupBox("读图模型设置（可选；留空则沿用上方设置）")
        v_layout = QFormLayout()

        self.o_vision_key_input = QLineEdit(self.pet.config.get("openai_vision_api_key", ""))
        self.o_vision_url_input = QLineEdit(self.pet.config.get("openai_vision_base_url", ""))
        self.o_vision_model_input = QLineEdit(self.pet.config.get("openai_vision_model_name", ""))
        self.o_vision_model_input.setPlaceholderText("主模型不能读图时，自动改用此模型识图；例: Qwen/Qwen3-VL-30B-A3B-Thinking")

        v_layout.addRow("API Key:", self.o_vision_key_input)
        v_layout.addRow("接口地址:", self.o_vision_url_input)
        v_layout.addRow("模型名称:", self.o_vision_model_input)

        self.vision_group.setLayout(v_layout)
        self.layout.addWidget(self.vision_group)

        self.layout.addWidget(QLabel("核心人设提示词 (System Prompt):"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlainText(self.pet.config["system_prompt"])
        self.layout.addWidget(self.prompt_edit)

        save_btn = QPushButton("💾 保存接口与人设")
        save_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(self.save_all)
        self.layout.addWidget(save_btn)

        # 初始化时先按当前引擎显示对应设置块（纯界面操作，瞬间完成，不会卡顿）
        self.on_engine_changed(self.engine_combo.currentIndex())

    def on_engine_changed(self, idx):
        """切换引擎只做界面显隐，不触发任何网络请求，因此不会卡顿。"""
        is_gemini = (idx == 0)
        self.gemini_group.setVisible(is_gemini)
        self.openai_group.setVisible(not is_gemini)
        self.vision_group.setVisible(not is_gemini)

    def save_all(self):
        # 1. 引擎类型
        self.pet.config["api_type"] = "gemini" if self.engine_combo.currentIndex() == 0 else "openai"

        # 2. Gemini 相关
        self.pet.config["gemini_api_key"] = self.g_key_input.text().strip()
        self.pet.config["gemini_model_name"] = self.g_model_input.text().strip() or "gemini-3.5-flash"
        self.pet.config["gemini_proxy"] = self.g_proxy_input.text().strip()

        # 3. OpenAI 兼容相关
        self.pet.config["openai_api_key"] = self.o_key_input.text().strip()
        self.pet.config["openai_base_url"] = self.o_url_input.text().strip()
        self.pet.config["openai_model_name"] = self.o_model_input.text().strip()

        # 3.5 读图模型相关（留空则沿用上方主模型配置）
        self.pet.config["openai_vision_api_key"] = self.o_vision_key_input.text().strip()
        self.pet.config["openai_vision_base_url"] = self.o_vision_url_input.text().strip()
        self.pet.config["openai_vision_model_name"] = self.o_vision_model_input.text().strip()
        # 3.6 主模型读图能力（白名单式，用户勾选，不靠模型名猜）
        self.pet.config["openai_main_vision"] = self.o_main_vision_check.isChecked()

        # 4. 人设
        self.pet.config["system_prompt"] = self.prompt_edit.toPlainText()

        # 5. 立即应用代理设置（填了就设，清空就取消，避免残留影响 Gemini 连接）
        # Gemini ???? api.gemini ????????
        # ?????????????? OpenAI ?????

        save_config(self.pet.config)
        if hasattr(self.pet, "chat_thread"):
            self.pet.chat_thread.update_config(self.pet.config)

        self.accept()
        self.pet.show_bubble("【normal】接口与人设配置已更新。")

class AppearanceDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("Giegisa - 气泡/文字/交互") 
        self.resize(400, 250)
        self.layout = QVBoxLayout(self)
        
        appearance_group = QGroupBox("外观与字体设置")
        a_layout = QFormLayout()
        
        self.width_spinner = QSpinBox()
        self.width_spinner.setRange(150, 600)
        self.width_spinner.setValue(self.pet.config["bubble_width"])
        
        self.bg_color = self.pet.config["bubble_bg"]
        self.border_color = self.pet.config["bubble_border"]
        self.font_color = self.pet.config.get("bubble_font_color", "#333333")
        
        self.bg_btn = QPushButton("🎨 选择背景颜色")
        self.bg_btn.clicked.connect(self.choose_bg_color)
        
        self.border_btn = QPushButton("🖌️ 选择边框颜色")
        self.border_btn.clicked.connect(self.choose_border_color)
        
        self.font_btn = QPushButton("🔤 选择字体颜色")
        self.font_btn.clicked.connect(self.choose_font_color)
        
        a_layout.addRow("气泡最大宽度 (px):", self.width_spinner)
        a_layout.addRow("气泡背景色 (可透明):", self.bg_btn)
        a_layout.addRow("气泡边框色:", self.border_btn)
        a_layout.addRow("全局字体颜色:", self.font_btn)
        
        # 新增输入框显示切换
        self.input_box_combo = QComboBox()
        self.input_box_combo.addItems(["✅ 显示底部文本输入框", "❌ 隐藏底部文本输入框"])
        self.input_box_combo.setCurrentIndex(0 if self.pet.config.get("show_input_box", True) else 1)
        a_layout.addRow("快捷输入交互:", self.input_box_combo)
        
        self.media_combo = QComboBox()
        self.media_combo.addItems(["✅ 开启媒体识别", "❌ 关闭媒体识别"])
        self.media_combo.setCurrentIndex(0 if self.pet.config.get("media_enabled", True) else 1)
        a_layout.addRow("媒体评价交互:", self.media_combo)
        
        self.clip_combo = QComboBox()
        self.clip_combo.addItems(["✅ 开启剪贴板识别", "❌ 关闭剪贴板识别"])
        self.clip_combo.setCurrentIndex(0 if self.pet.config.get("clipboard_enabled", True) else 1)
        a_layout.addRow("剪贴板交互:", self.clip_combo)
        
        appearance_group.setLayout(a_layout)
        self.layout.addWidget(appearance_group)

        save_btn = QPushButton("💾 保存外观设置")
        save_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(self.save_all)
        self.layout.addWidget(save_btn)

    def choose_bg_color(self):
        color = QColorDialog.getColor(QColor(255, 255, 255, 220), self, "选择背景", QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self.bg_color = f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"
            self.bg_btn.setText("已更新背景色 ✔")

    def choose_border_color(self):
        color = QColorDialog.getColor(QColor(self.border_color) if self.border_color.startswith("#") else QColor(85, 85, 85), self, "选择边框")
        if color.isValid():
            self.border_color = color.name()
            self.border_btn.setText("已更新边框色 ✔")

    def choose_font_color(self):
        color = QColorDialog.getColor(QColor(self.font_color) if self.font_color.startswith("#") else QColor(51, 51, 51), self, "选择字体颜色")
        if color.isValid():
            self.font_color = color.name()
            self.font_btn.setText("已更新字体色 ✔")

    def save_all(self):
        self.pet.config["bubble_width"] = self.width_spinner.value()
        self.pet.config["bubble_bg"] = self.bg_color
        self.pet.config["bubble_border"] = self.border_color
        self.pet.config["bubble_font_color"] = self.font_color
        # 保存媒体开关状态
        self.pet.config["media_enabled"] = (self.media_combo.currentIndex() == 0)
        
        # 应用输入框开关
        self.pet.config["show_input_box"] = (self.input_box_combo.currentIndex() == 0)
        self.pet.input_box.setVisible(self.pet.config["show_input_box"])
        self.pet.adjustSize()
        self.pet.config["clipboard_enabled"] = (self.clip_combo.currentIndex() == 0)
        
        save_config(self.pet.config)
        self.pet.apply_bubble_style()
        self.accept()
        self.pet.show_bubble("【normal】机体外观配置已更新。")

class FocusDialog(QDialog):
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("Giegisa - 强制专注协议")
        self.resize(320, 250)
        self.layout = QVBoxLayout(self)
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["⏱️ 普通倒计时模式", "⏳ 无限正计时模式", "🍅 番茄钟循环模式"])
        self.mode_combo.currentIndexChanged.connect(self.toggle_mode)
        self.layout.addWidget(QLabel("选择协议模式："))
        self.layout.addWidget(self.mode_combo)
        
        self.normal_widget = QWidget()
        n_layout = QFormLayout(self.normal_widget)
        self.n_mins = QSpinBox()
        self.n_mins.setRange(1, 999)
        self.n_mins.setValue(25)
        n_layout.addRow("设定专注时长 (分钟):", self.n_mins)
        self.layout.addWidget(self.normal_widget)

        self.stopwatch_widget = QWidget()
        sw_layout = QVBoxLayout(self.stopwatch_widget)
        sw_layout.addWidget(QLabel("时间将持续累加。\n如果需要终止结算，请右键 Giegisa 寻找停止按钮。"))
        self.layout.addWidget(self.stopwatch_widget)
        self.stopwatch_widget.hide()
        
        self.pomo_widget = QWidget()
        p_layout = QFormLayout(self.pomo_widget)
        self.p_work = QSpinBox()
        self.p_work.setRange(1, 120)
        self.p_work.setValue(25)
        self.p_rest = QSpinBox()
        self.p_rest.setRange(1, 60)
        self.p_rest.setValue(5)
        self.p_sets = QSpinBox()
        self.p_sets.setRange(1, 10)
        self.p_sets.setValue(3)
        p_layout.addRow("单次工作时长 (分钟):", self.p_work)
        p_layout.addRow("单次休息时长 (分钟):", self.p_rest)
        p_layout.addRow("循环目标组数:", self.p_sets)
        self.layout.addWidget(self.pomo_widget)
        
        self.pomo_widget.hide() 
        
        self.layout.addWidget(QLabel("<font color='#666'>* Giegisa将在后台时刻监控你的活动窗口防摸鱼</font>"))
        
        # 👇 新增的管理拦截名单与开关按钮 👇
        btn_distract = QPushButton("⚙️ 管理拦截名单与开关")
        btn_distract.clicked.connect(lambda checked=False: self.pet.open_dialog(DistractionSettingsDialog))
        self.layout.addWidget(btn_distract)
        
        start_btn = QPushButton("🚀 签订协议并开始专注")
        start_btn.setStyleSheet("background-color: #E53935; color: white; font-weight: bold; padding: 10px;")
        start_btn.clicked.connect(self.start_focus)
        self.layout.addWidget(start_btn)

    def toggle_mode(self, idx):
        self.normal_widget.setVisible(idx == 0)
        self.stopwatch_widget.setVisible(idx == 1)
        self.pomo_widget.setVisible(idx == 2)

    def start_focus(self):
        if self.mode_combo.currentIndex() == 0:
            config = {"type": "normal", "minutes": self.n_mins.value()}
        elif self.mode_combo.currentIndex() == 1:
            config = {"type": "stopwatch"}
        else:
            config = {"type": "pomodoro", "work": self.p_work.value(), "rest": self.p_rest.value(), "sets": self.p_sets.value()}
        self.pet.start_focus_mode(config)
        self.accept()

class MemorySettingsDialog(QDialog):
    """【新增】独立的记忆上限与长期摘要配置面板"""
    def __init__(self, parent_pet):
        super().__init__(parent_pet)
        self.pet = parent_pet
        self.setWindowTitle("🧠 Giegisa - 核心记忆与潜意识摘要")
        self.resize(450, 350)
        self.layout = QVBoxLayout(self)
        
        # 轮数限制
        limit_layout = QHBoxLayout()
        limit_layout.addWidget(QLabel("近期活跃记忆轮数上限 (默认50, 0为不限):"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 9999)
        self.limit_spin.setValue(self.pet.config.get("history_limit", 50))
        limit_layout.addWidget(self.limit_spin)
        self.layout.addLayout(limit_layout)
        
        # 摘要编辑
        self.layout.addWidget(QLabel("长期记忆摘要 (随对话自动滚动更新，也可在此手动微调):"))
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlaceholderText("无需刻意填写，系统会在对话超限后自动将旧记忆浓缩生成于此...")
        self.summary_edit.setPlainText(self.pet.config.get("long_term_summary", ""))
        self.layout.addWidget(self.summary_edit)
        
        # 提示语
        notice = QLabel("<font color='#666'>💡 核心机制：当总对话轮数超过上方设定的上限时，系统会自动将最古老的对话提炼并融入这段摘要中，同时清空超出的历史，实现永久不失忆且不卡顿。</font>")
        notice.setWordWrap(True)
        self.layout.addWidget(notice)
        
        # 保存按钮
        save_btn = QPushButton("💾 保存配置并录入潜意识")
        save_btn.setStyleSheet("background-color: #3F51B5; color: white; padding: 8px; font-weight: bold;")
        save_btn.clicked.connect(self.save_settings)
        self.layout.addWidget(save_btn)
        
    def save_settings(self):
        self.pet.config["history_limit"] = self.limit_spin.value()
        self.pet.config["long_term_summary"] = self.summary_edit.toPlainText().strip()
        save_config(self.pet.config)
        if hasattr(self.pet, "chat_thread"):
            self.pet.chat_thread.update_config(self.pet.config)
        self.accept()
        self.pet.show_bubble("【normal】记忆配置已录入底层存储。")
