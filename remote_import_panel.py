"""从现有配置直接新建服务器计算；沿用上传预览、确认和结果核对。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

import grasp_config as config
from calc_panel import ConfigDialog
from remote_edit_panel import UploadDialog, supported


def create(parent, source, directory, opened):
    current = getattr(parent, "import_dialog", None)
    if current is not None:
        current.show()
        current.raise_()
        return
    if not supported(source, parent):
        return
    entries = [dict(cfg=cfg, warnings=[], path=None) for cfg in config.list_demos()]
    chooser = ConfigDialog(parent, entries, "", remote_directory=directory)
    if chooser.exec() != QDialog.Accepted:
        chooser.deleteLater()
        return
    dialog = UploadDialog(parent, source, chooser.target(), chooser.entry()["cfg"], None,
                          chooser.run_after.isChecked(), remote_only=True, own_source=False)
    chooser.deleteLater()
    parent.import_dialog = dialog
    dialog.setWindowModality(Qt.WindowModal)
    dialog.remoteCreated.connect(opened)
    def finished(result):
        parent.import_dialog = None
        dialog.deleteLater()
    dialog.finished.connect(finished)
    dialog.show()
