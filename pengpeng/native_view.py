"""Windows 原生浏览器承载层，避开 QQuickWidget 的离屏合成与 60Hz 帧源。

Qt 官方 WebView2 插件负责 HWND、DPI、输入与 Windows 显示同步。本模块只适配
现有本地页面的异步请求，不转发滚轮、不接管动画时钟，也不开放 HTTP/调试端口。
插件没有公开 WebMessageReceived，因此用现有 35ms 后端轮询批量交换业务消息；
滚动和音频时钟完全在浏览器内执行，不依赖这个轮询。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Signal, QUrl, QMetaObject, Q_ARG
from PySide6.QtQuick import QQuickView
from PySide6.QtWidgets import QWidget


def runtime_version():
    """只读检测 Evergreen Runtime；未安装时保留现有 Qt 引擎兼容路径。"""
    if sys.platform != 'win32':
        return ''
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\EdgeUpdate\Clients',
                                    0, winreg.KEY_READ | view) as clients:
                    for i in range(winreg.QueryInfoKey(clients)[0]):
                        with winreg.OpenKey(clients, winreg.EnumKey(clients, i)) as item:
                            try:
                                name = winreg.QueryValueEx(item, 'name')[0]
                                version = winreg.QueryValueEx(item, 'pv')[0]
                                if 'WebView2' in name and version != '0.0.0.0':
                                    return version
                            except OSError:
                                pass
            except OSError:
                pass
    return ''


def initialize_native_view():
    """必须在 QApplication 创建前初始化；不更改任何系统或浏览器全局设置。"""
    if not runtime_version():
        return False
    os.environ['QT_WEBVIEW_PLUGIN'] = 'webview2'
    from PySide6.QtWebView import QtWebView
    QtWebView.initialize()
    return True


class NativeView(QObject):
    loadFinished = Signal(bool)

    def __init__(self, parent, workspace):
        super().__init__(parent)
        # WebView2 缓存和子进程只属于这个工作区，不与系统 Edge 或其他应用共享。
        os.environ['WEBVIEW2_USER_DATA_FOLDER'] = str(workspace / 'browser')
        self.window = QQuickView()
        self.window.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / 'web/NativeView.qml')))
        self.root = self.window.rootObject()
        if self.root is None:
            raise RuntimeError('原生浏览器承载层加载失败：' + '; '.join(e.toString() for e in self.window.errors()))
        self.widget = QWidget.createWindowContainer(self.window, parent)
        self.home = QUrl.fromLocalFile(str(Path(__file__).parent / 'web/index.html'))
        self.ready = False
        self.generation = 0
        self.serial = 0
        self.callbacks = {}
        self.outgoing = []
        self.exchange_pending = False
        self.scale = 1.0
        self.root.completed.connect(self._completed)
        self.root.loaded.connect(self._loaded)
        self.root.navigated.connect(self._navigated)

    def _invoke(self, method, *args):
        QMetaObject.invokeMethod(self.root, method, *[Q_ARG('QVariant', arg) for arg in args])

    def load(self, url=None):
        self._invoke('navigate', (url or self.home).toString())

    def _navigated(self, address):
        # 外部链接由页面显式请求系统浏览器。其他导航不能获得本地业务桥。
        self.ready = False
        self.generation += 1
        if QUrl(address) != self.home and address != 'about:blank':
            self.ready = False
            self._invoke('stopNavigation')
            self.load()

    def _loaded(self, ok):
        self.ready = False
        if not ok or QUrl(self.root.property('address')) != self.home:
            self.loadFinished.emit(False)
            return
        # Edge 将 file: 样式表视为不透明来源，不能读写其中的媒体查询规则。
        # 将同一份随包 CSS 转为文档内样式（相对资源仍以页面为基准），再开放
        # 业务桥。这样页面缩放可调整响应式断点，不放宽浏览器的文件安全策略，
        # 也不维护第二份样式；整个转换只发生在页面加载时。
        css = (Path(__file__).parent / 'web/style.css').read_text('utf-8')
        generation = self.generation

        def styled(success):
            if generation != self.generation:
                return
            self.ready = bool(success)
            self.loadFinished.emit(self.ready)

        self.runJavaScript("(()=>{const style=document.createElement('style');"
                           "style.textContent=" + json.dumps(css, ensure_ascii=False) + ";"
                           "document.querySelector('link[rel=stylesheet]').replaceWith(style);"
                           "return true;})()", styled)

    def _completed(self, identity, encoded):
        callback = self.callbacks.pop(identity, None)
        if callback:
            callback(json.loads(encoded))

    def runJavaScript(self, script, callback=None):
        self.serial += 1
        if callback:
            self.callbacks[self.serial] = callback
        self._invoke('evaluate', script, self.serial)

    def setZoomFactor(self, scale):
        self.scale = scale
        # CSS zoom 是浏览器原生布局缩放，输入命中和滚动由同一引擎处理。
        # 不使用 transform，也不在 Python 中再按比例换算鼠标坐标。
        self.runJavaScript(f'window.NativeHost?.setScale({scale!r})')

    def zoomFactor(self):
        return self.scale

    def queue_response(self, encoded):
        self.outgoing.append({'kind': 'response', 'value': encoded})

    def exchange(self, bridge):
        """每轮最多一个异步交换；批量发送结果，避免每张缩略图单独跨进程调用。"""
        if not self.ready or self.exchange_pending:
            return
        self.exchange_pending = True
        generation = self.generation
        batch, self.outgoing = self.outgoing, []

        def received(requests):
            self.exchange_pending = False
            if not self.ready or generation != self.generation:
                return
            for request in requests or []:
                method, args = request.get('method'), request.get('args', [])
                # 仅暴露界面已有的五类业务操作；绝不按任意字符串反射调用对象。
                if method == 'request':
                    bridge.request(args[0])
                elif method == 'chooseDirectory':
                    path = bridge.chooseDirectory(args[0])
                    self.outgoing.append({'kind': 'callback', 'id': request['id'], 'value': path})
                elif method == 'openFolder':
                    bridge.openFolder(args[0])
                elif method == 'openLogs':
                    bridge.openLogs()
                elif method == 'openExternal':
                    from PySide6.QtGui import QDesktopServices
                    url = QUrl(args[0])
                    if url.scheme() in ('https', 'http'):
                        QDesktopServices.openUrl(url)

        self.runJavaScript('window.NativeHost?.exchange(' + json.dumps(batch, ensure_ascii=False) + ') || []', received)
