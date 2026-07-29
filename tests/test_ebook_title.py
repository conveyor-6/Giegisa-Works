"""书名保护回归测试：打开托管书（book.<ext>）后书名不得变成“book”。

根因：托管副本文件名恒为 book.<ext>，TXT 等按文件名推导标题的格式
会把解析标题变成“book”，_load_book 原先的回写顺序会覆盖原书名。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication, QWidget

import oc
from dialogs.ebook import EbookReaderDialog


class FakePet(QWidget):
    def __init__(self):
        super().__init__()
        self.config = json.loads(json.dumps(oc.DEFAULT_CONFIG))
        self.calendar_service = oc.CalendarService(self.config)

    def inject_system_event(self, *args):
        pass

    def show_bubble(self, *args, **kwargs):
        pass

    def send_msg(self, *args, **kwargs):
        pass

    def open_dialog(self, *args):
        pass


def _open_reader(pet, path, title, tmp):
    book = {
        "id": "t1", "title": title, "path": str(path),
        "asset_dir": str(Path(tmp) / "assets"), "managed": True,
        "category": "测试", "status": "未读", "progress": 0,
        "position": 0, "bookmarks": [], "annotations": []}
    reader = EbookReaderDialog(pet, book)
    reader.show()
    app = QApplication.instance()
    app.processEvents()
    reader.session_timer.stop()
    reader.close()
    reader.deleteLater()
    app.processEvents()
    return book


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    old_saver = oc.save_config
    import dialogs.ebook as ebook_dialog
    old_dialog_saver = ebook_dialog.save_config
    oc.save_config = lambda *args, **kwargs: True
    ebook_dialog.save_config = lambda *args, **kwargs: True
    with tempfile.TemporaryDirectory() as tmp:
        # 托管书：文件名恒为 book.txt（TXT 标题由文件名推导，即“book”）
        managed = Path(tmp) / "book.txt"
        managed.write_bytes("第一章\n正文内容。".encode("gb18030"))

        # 1. 有原书名：打开后必须保留
        pet = FakePet()
        book = _open_reader(pet, managed, "真正的书名", tmp)
        assert book["title"] == "真正的书名", book["title"]

        # 2. 原书名为空：用解析标题兜底（不产生空标题）
        book2 = _open_reader(pet, managed, "", tmp)
        assert book2["title"], "空书名未被兜底"
    oc.save_config = old_saver
    ebook_dialog.save_config = old_dialog_saver
    print("EBOOK_TITLE_OK")


if __name__ == "__main__":
    main()
