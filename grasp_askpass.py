"""OpenSSH 调用的短生命周期输入框；回答仅交给 ssh，不保存或写日志。"""

import os
import sys
from grasp_core import T


def main():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QDialog, QInputDialog, QLabel, QLineEdit, QMessageBox,
                                   QVBoxLayout)
    import grasp_core as core

    app = QApplication.instance() or QApplication([])
    core.set_lang(core.load_config().get("lang", core.LANG))
    prompt = sys.argv[2] if len(sys.argv) > 2 else T("SSH 登录")
    hint = os.environ.get("SSH_ASKPASS_PROMPT", "").lower()
    if hint == "none":
        # OpenSSH notify_complete 会结束此进程；这里仅显示通知，不读取或回传回答。
        notice = QDialog()
        notice.setWindowTitle(T("SSH 提示"))
        notice.setAttribute(Qt.WA_ShowWithoutActivating)
        label = QLabel(prompt)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        QVBoxLayout(notice).addWidget(label)
        notice.resize(560, 160)
        notice.show()
        return app.exec()
    if hint == "confirm":
        answer = QMessageBox.question(None, T("SSH 主机确认"), prompt,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        print("yes" if answer == QMessageBox.Yes else "no")
        return 0 if answer == QMessageBox.Yes else 1
    # sshconnect.c 的主机确认使用 RP_ECHO，却不设置 confirm hint，须匹配其实际提示。
    question = "Are you sure you want to continue connecting "
    host_key = any(prompt.rstrip().endswith(question + ending)
                   for ending in ("(yes/no/[fingerprint])?", "(yes/no)?")) or prompt.strip() in (
                       "Please type 'yes' or 'no':", "Please type 'yes', 'no' or the fingerprint:")
    message = prompt
    if host_key:
        message += "\n\n" + T("请通过管理员或其他可信渠道核对主机指纹；一致后再输入 yes 或指纹，不确定请取消。")
    value, accepted = QInputDialog.getText(None, T("SSH 主机确认") if host_key else T("SSH 登录"), message,
                                          QLineEdit.Normal if host_key else QLineEdit.Password)
    if accepted:
        sys.stdout.write(value + "\n")
        sys.stdout.flush()
    return 0 if accepted else 1
