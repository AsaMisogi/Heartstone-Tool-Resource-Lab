"""离线验证独立台词浏览窗口：真实 Qt 页面、身份校验、缓存与关闭回调。

来源正文用测试页面代替，禁止网络加载；该脚本不证明远端 Cloudflare
验证一定通过。所有测试台词仅保存到 QA 工作区，不污染用户台词缓存。
"""
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView
from pengpeng.transcript_browser import TranscriptBrowser, WikiPage
from pengpeng.transcripts import SOURCE


if __name__ == '__main__':
    app = QApplication([])
    parent = QMainWindow()
    workspace = Path('.cache/qa-transcript-browser').resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    outcome = {'passed': False}

    def finish(result):
        try:
            assert result['saved'], result
            saved = json.loads((workspace / 'cache/transcripts-v2/123.json').read_text('utf-8'))
            assert saved['quotes'] == {'Play': '测试登场'}
            cancelled = []
            with patch.object(QWebEngineView, 'load'):
                second = TranscriptBrowser(parent, workspace, 456, cancelled.append)
            second.reject()
            assert cancelled == [{'saved': False}]
            outcome.update(passed=True, cached=saved['quotes'], cancel_callback=True)
        except Exception as exc:
            outcome['error'] = str(exc)
        QTimer.singleShot(250, app.quit)

    with patch.object(QWebEngineView, 'load'):
        dialog = TranscriptBrowser(parent, workspace, 123, finish)
    assert dialog.page.webChannel() is None
    assert dialog.page.profile() is parent.transcript_profile
    dialog.show()
    fixture = '''<html><meta charset="utf-8"><script>
      window.mw={config:{get:()=>"Card/123"}};
      </script><div id="mw-content-text"><div class="mw-parser-output">
      <h2>台词</h2><ul><li>登场</li></ul><blockquote>测试登场<br>Test</blockquote>
      </div></div></html>'''

    def loaded(ok):
        if ok:
            dialog.grab().save(str(workspace / 'browser.png'))
            dialog.read_quotes()

    dialog.page.loadFinished.connect(loaded)
    # 仅测试中的本地 fixture 允许 data: 导航；生产页面仍固定 HTTPS 来源。
    with patch.object(WikiPage, 'acceptNavigationRequest', return_value=True):
        dialog.page.setHtml(fixture, QUrl(SOURCE + '/wiki/Card/123'))
        QTimer.singleShot(15000, app.quit)
        app.exec()
    (workspace / 'results.json').write_text(json.dumps(outcome, ensure_ascii=False, indent=2), 'utf-8')
    print(outcome)
    sys.exit(0 if outcome['passed'] else 1)
