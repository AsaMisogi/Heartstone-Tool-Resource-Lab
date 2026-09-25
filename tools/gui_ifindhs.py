"""验证项目内 ifindhs 浏览读取流程；仅正常加载页面，不处理交互验证码。"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from pengpeng.transcript_browser import TranscriptBrowser
from pengpeng.ifindhs import cache_path

if __name__ == '__main__':
    app = QApplication([])
    parent = QMainWindow()
    workspace = Path('.cache/qa-05-workspace').resolve()
    started = time.monotonic()
    result = {}
    def completed(value):
        result.update(value)
        result['message'] = dialog.message.text()
        app.quit()
    dialog = TranscriptBrowser(parent, workspace, 123702, completed,
        cardid='HERO_11aq', name='永时收割者哈斯克', source='ifindhs')
    dialog.show()
    def received(html):
        if '<audio' in html and 'HERO_11aq' in html and not result:
            timer.stop()
            dialog.read_quotes()
            QTimer.singleShot(2000, lambda: dialog.reject() if not result else None)
    def tick():
        if time.monotonic() - started > 90:
            timer.stop()
            dialog.reject()
        else:
            dialog.page.toHtml(received)
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(1000)
    app.exec()
    if result.get('saved'):
        data = json.loads(cache_path(workspace, 'HERO_11aq').read_text('utf8'))
        print(json.dumps({'saved': True, 'clips': len(data['clips']), 'triggers': data['triggers']}, ensure_ascii=False))
    else:
        print('浏览读取未成功：' + result.get('message', '未完成'))
    sys.exit(0 if result.get('saved') else 1)
