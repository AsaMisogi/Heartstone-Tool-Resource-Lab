"""台词站点拒绝自动请求时，提供正常浏览器验证与读取入口。

远程页面使用独立 Profile，没有 WebChannel、文件访问或本地 API 权限。
用户完成站点验证后，仍按数字 ID 请求同源 API，不读取剪贴板或其他浏览器
Cookie，也不将远程 HTML 放入本地工作台。会话只保存在工作区自己的目录。
"""
import json
from urllib.parse import urlencode

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .transcripts import SOURCE, cache_quotes, parse_html_quotes


class WikiPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        # 允许验证服务自己的子框架；顶层固定为台词来源，禁用任意外站跳转。
        return not is_main_frame or (url.scheme() == 'https' and url.host() == 'hearthstone.huijiwiki.com')


class TranscriptBrowser(QDialog):
    def __init__(self, parent, workspace, dbfid, completed):
        super().__init__(parent)
        self.workspace, self.dbfid, self.completed = workspace, int(dbfid), completed
        if self.dbfid <= 0:
            raise ValueError('无效卡牌编号')
        self.setWindowTitle('公开台词 · 浏览验证与读取')
        self.resize(980, 760)
        self.message = QLabel('如果站点要求验证，请在下面完成；页面显示后点击“读取本卡台词”。')
        self.message.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.message)
        self.view = QWebEngineView(self)
        # Profile 由主窗口持有，连续查询复用会话；不与工作台本地页面共享。
        if not hasattr(parent, 'transcript_profile'):
            profile = QWebEngineProfile('transcript-source', parent)
            profile.setPersistentStoragePath(str(workspace / 'cache' / 'transcript-browser' / 'storage'))
            profile.setCachePath(str(workspace / 'cache' / 'transcript-browser' / 'http'))
            parent.transcript_profile = profile
        self.page = WikiPage(parent.transcript_profile, self.view)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        self.view.setPage(self.page)
        layout.addWidget(self.view)
        row = QHBoxLayout()
        self.read = QPushButton('读取本卡台词')
        self.read.clicked.connect(self.read_quotes)
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        row.addWidget(self.read)
        row.addWidget(close)
        layout.addLayout(row)
        self.poll = QTimer(self)
        self.poll.setInterval(250)
        self.poll.timeout.connect(self.poll_result)
        self.ticks = 0
        self.finished.connect(self.finish)
        self.view.load(QUrl(SOURCE + f'/wiki/Card/{self.dbfid}'))

    def read_quotes(self):
        if self.page.url().host() != 'hearthstone.huijiwiki.com':
            return
        self.read.setEnabled(False)
        self.message.setText('正在读取本卡展开后的台词…')
        # 有的站点页面可正常查看、API 仍被拦截。先读取已经展示的正文，并用
        # MediaWiki 页身份确认卡牌编号；不能把用户导航到其他卡牌的文字借来。
        expected = json.dumps(f'Card/{self.dbfid}')
        self.page.runJavaScript('''(() => {
          if (window.mw?.config.get('wgPageName') !== ''' + expected + ''') return '';
          const html = document.querySelector('#mw-content-text .mw-parser-output')?.outerHTML || '';
          return html.length <= 1000000 ? html : '';
        })()''', self.read_rendered)

    def read_rendered(self, html):
        if self.completed is None:
            return
        if html:
            quotes = parse_html_quotes(html)
            if quotes:
                try:
                    cache_quotes(self.workspace, self.dbfid, quotes)
                    self.accept()
                except OSError as exc:
                    self.message.setText('台词缓存未能保存：' + str(exc))
                    self.read.setEnabled(True)
                return
        query = '/api.php?' + urlencode({'action': 'parse', 'page': f'Card/{self.dbfid}',
                                         'prop': 'text', 'format': 'json', 'redirects': 1})
        # URL 全部由固定模板和数字生成。请求有超时与流式大小限制，验证页不会
        # 被错误保存成“没有台词”；只返回纯 JSON 给宿主自己的回调。
        self.page.runJavaScript('''window.__transcriptRead = null;
          (async () => {
            try {
              const response = await fetch(''' + json.dumps(query) + ''', {signal: AbortSignal.timeout(8000)});
              if (!response.ok) throw Error("站点仍拒绝读取（" + response.status + "），请先完成验证后重试。");
              const reader = response.body.getReader(), chunks = []; let size = 0;
              while (true) {
                const {done, value} = await reader.read(); if (done) break;
                size += value.length;
                if (size > 1000000) { await reader.cancel(); throw Error("来源响应超过 1 MB"); }
                chunks.push(value);
              }
              const bytes = new Uint8Array(size); let offset = 0;
              for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
              const data = JSON.parse(new TextDecoder().decode(bytes));
              if (!data.parse?.text?.["*"]) throw Error("此页面没有可读取的台词正文。");
              window.__transcriptRead = JSON.stringify({html: data.parse.text["*"]});
            } catch (error) { window.__transcriptRead = JSON.stringify({error: error.message}); }
          })();''')
        self.ticks = 0
        self.poll.start()

    def poll_result(self):
        self.ticks += 1
        if self.ticks > 40:
            self.received(json.dumps({'error': '读取超时，请确认页面验证已完成后重试。'}))
        else:
            self.page.runJavaScript('window.__transcriptRead', self.received)

    def received(self, value):
        if not self.poll.isActive() or not value:
            return
        self.poll.stop()
        self.read.setEnabled(True)
        try:
            data = json.loads(value)
            if data.get('error'):
                raise ValueError(data['error'])
            quotes = parse_html_quotes(data['html'])
            if not quotes:
                raise ValueError('此页没有可唯一匹配的简中登场、攻击或死亡台词；未覆盖已有缓存。')
            cache_quotes(self.workspace, self.dbfid, quotes)
        except (ValueError, KeyError, OSError) as exc:
            self.message.setText(str(exc))
            return
        self.accept()

    def finish(self, result):
        self.poll.stop()
        self.view.stop()
        completed, self.completed = self.completed, None
        if completed:
            completed({'saved': result == QDialog.DialogCode.Accepted})
        # Qt 页在 Profile 之前销毁；关闭窗口后不留下远程页面后台执行。
        self.view.setPage(None)
        self.page.deleteLater()
        self.deleteLater()
