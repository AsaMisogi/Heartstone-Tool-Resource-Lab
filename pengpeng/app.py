"""桌面宿主：本地 HTML + Qt WebChannel，不开放 HTTP 端口。

Qt 窗口拥有解析子进程的生命周期；退出先正常通知，再在超时后终止卡住的
原生解码器。Windows Job Object 确保关闭调试控制台时也不会留下解析进程。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import re
import sys
import time

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QUrl, Qt, QLockFile
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .worker import worker_main, effect_worker_main
from .transcript_browser import TranscriptBrowser
from .speech_process import SpeechProcess


def own_process_tree():
    """将本进程及随后创建的子进程放入 KILL_ON_JOB_CLOSE 作业。"""
    if sys.platform != 'win32':
        return None
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong), ('Flags', w.DWORD),
            ('Min', ctypes.c_size_t), ('Max', ctypes.c_size_t), ('Active', w.DWORD),
            ('Affinity', ctypes.c_size_t), ('Priority', w.DWORD), ('Scheduling', w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOp', 'WriteOp', 'OtherOp', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('Basic', Basic), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
            ('JobMemory', ctypes.c_size_t), ('PeakProcessMemory', ctypes.c_size_t), ('PeakJobMemory', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        print('WARNING: Windows Job Object 未启用；窗口关闭仍会显式终止解析进程。', flush=True)
        if job:
            kernel.CloseHandle(job)
        return None
    return job  # 保留句柄到进程退出，不能在正常运行期间关闭。


class Page(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if url.scheme() in ('file', 'qrc', 'about', 'data'):
            return True
        # 来源可由用户配置；仅用户点击的 HTTP(S) 链接交给系统浏览器，
        # 不允许外站替换持有 WebChannel 权限的本地界面。
        if url.scheme() in ('http', 'https') and navigation_type == self.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
        return False

    def javaScriptConsoleMessage(self, level, message, line, source):
        print(f'[GUI] {message} ({Path(source).name}:{line})', flush=True)


class Bridge(QObject):
    response = Signal(str)

    @Slot(float)
    def setInterfaceScale(self, scale):
        """Qt 原生页面缩放同步布局、固定定位与鼠标坐标，避免 CSS transform 偏移。"""
        if 0.8 <= scale <= 1.4:
            self.window.web.setZoomFactor(scale)

    def __init__(self, window, inbox, outbox, worker, workspace):
        super().__init__(window)
        self.window, self.inbox, self.outbox, self.worker = window, inbox, outbox, worker
        self.workspace = workspace
        self.speech = SpeechProcess(workspace, lambda message: self.response.emit(json.dumps(message, ensure_ascii=False)))
        self.dead_reported = False
        self.effect_worker = None
        self.effect_request = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(35)

    @Slot(str)
    def request(self, encoded):
        try:
            data = json.loads(encoded)
            if data.get('method') == 'speech':
                self.speech.request(data)
                return
            if data.get('method') == 'cancel_speech':
                self.speech.stop()
                self.response.emit(json.dumps({'id': data['id'], 'result': {}}))
                return
            if data.get('method') == 'initialize' or (data.get('method') == 'save_settings' and data.get('params', {}).get('speech_recognition') is False):
                self.speech.stop()
            if data.get('method') == 'transcript_browser':
                if getattr(self, 'transcript_dialog', None) is not None:
                    self.response.emit(json.dumps({'id': data['id'], 'error': '请先关闭已有台词浏览窗口'}))
                    return
                def completed(result):
                    self.transcript_dialog = None
                    self.response.emit(json.dumps({'id': data['id'], 'result': result}))
                self.transcript_dialog = TranscriptBrowser(self.window, self.workspace,
                    data['params']['dbfid'], completed)
                self.transcript_dialog.show()
                return
            if data.get('method') == 'cancel_effect':
                if self.effect_request is not None:
                    self.stop_effect('特效解析已取消')
                self.response.emit(json.dumps({'id': data['id'], 'result': {}}))
                return
            if data.get('method') == 'initialize':
                self.stop_effect('游戏目录已重新连接')
            if data.get('method') == 'effect':
                if self.effect_request is not None:
                    self.stop_effect('已切换到另一个特效')
                if self.effect_worker is None:
                    context = mp.get_context('spawn')
                    self.effect_inbox, self.effect_outbox = context.Queue(), context.Queue()
                    self.effect_worker = context.Process(target=effect_worker_main,
                        args=(self.effect_inbox, self.effect_outbox, str(self.workspace)), daemon=True)
                    self.effect_worker.start()
                self.effect_request = data['id']
                self.effect_started = time.monotonic()
                self.effect_inbox.put(data)
                return
            if not self.worker.is_alive():
                self.response.emit(json.dumps({'id': data['id'], 'error': '解析进程已停止，请重新启动砰砰解析台'}))
            else:
                self.inbox.put(data)
        except (ValueError, KeyError) as exc:
            print(f'无效请求：{exc}', flush=True)

    def stop_effect(self, reason='特效解析已停止'):
        """终止时丢弃该进程的独立队列，避免复用被强制中断的 Queue。"""
        if self.effect_worker is not None:
            self.effect_worker.terminate()
            self.effect_worker.join(timeout=0.3)
            self.effect_inbox.cancel_join_thread()
            self.effect_outbox.cancel_join_thread()
            self.effect_inbox.close()
            self.effect_outbox.close()
            self.effect_worker = None
        if self.effect_request is not None:
            self.response.emit(json.dumps({'id': self.effect_request, 'error': reason}))
            self.effect_request = None

    @Slot(str, result=str)
    def chooseDirectory(self, purpose):
        title = '选择炉石安装目录' if purpose == 'game' else '选择导出目录'
        return QFileDialog.getExistingDirectory(self.window, title)

    @Slot(str)
    def openFolder(self, path):
        folder = Path(path).resolve()
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    @Slot()
    def openLogs(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.workspace / 'logs')))

    def poll(self):
        self.speech.poll()
        if self.effect_worker is not None:
            try:
                message = self.effect_outbox.get_nowait()
            except queue.Empty:
                message = None
            if message:
                self.effect_request = None
                self.response.emit(json.dumps(message, ensure_ascii=False))
            if not self.effect_worker.is_alive():
                self.stop_effect('特效解析进程退出，请重试；其他页面可继续使用')
            elif self.effect_request is not None and time.monotonic() - self.effect_started > 30:
                self.stop_effect('此特效解析超过 30 秒，已停止；可重试或选择其他特效')
        for _ in range(120):
            try:
                message = self.outbox.get_nowait()
            except queue.Empty:
                break
            self.response.emit(json.dumps(message, ensure_ascii=False))
        if not self.worker.is_alive() and not self.dead_reported:
            self.dead_reported = True
            self.response.emit(json.dumps({'event': 'worker_dead', 'message': '解析进程意外停止。请查看日志并重新启动。'}))


class Window(QMainWindow):
    def __init__(self, workspace):
        super().__init__()
        self.setWindowTitle('砰砰解析台 · 炉石资源工作台 — 朝禊ASOGI')
        self.setWindowIcon(QIcon(str(Path(__file__).parent / 'web/icon.ico')))
        self.resize(1440, 940)
        self.setMinimumSize(1060, 720)
        context = mp.get_context('spawn')
        self.inbox, self.outbox = context.Queue(), context.Queue()
        self.worker = context.Process(target=worker_main, args=(self.inbox, self.outbox, str(workspace)), daemon=True)
        self.worker.start()
        print(f'桌面 PID={os.getpid()}，解析 PID={self.worker.pid}', flush=True)
        self.web = QWebEngineView(self)
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.page = Page(self.web)
        self.web.setPage(self.page)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.channel = QWebChannel(self.page)
        self.bridge = Bridge(self, self.inbox, self.outbox, self.worker, workspace)
        self.channel.registerObject('host', self.bridge)
        self.page.setWebChannel(self.channel)
        self.setCentralWidget(self.web)
        self.web.load(QUrl.fromLocalFile(str(Path(__file__).parent / 'web/index.html')))

    def closeEvent(self, event):
        print('正在关闭界面及解析进程…', flush=True)
        self.bridge.timer.stop()
        if getattr(self.bridge, 'transcript_dialog', None) is not None:
            self.bridge.transcript_dialog.reject()
        self.bridge.stop_effect()
        self.bridge.speech.stop()
        self.inbox.put({'method': 'shutdown'})
        self.worker.join(timeout=1.5)
        if self.worker.is_alive():
            self.worker.terminate()
            self.worker.join(timeout=2)
        self.inbox.cancel_join_thread()
        self.outbox.cancel_join_thread()
        self.inbox.close()
        self.outbox.close()
        print('后端已退出。', flush=True)
        event.accept()


def main():
    mp.freeze_support()
    parser = argparse.ArgumentParser(description='砰砰解析台')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--screenshot', type=Path, help='开发验证：退出前截图')
    parser.add_argument('--quit-after', type=int, default=0, help='开发验证：指定秒数后关闭')
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    workspace = (args.workspace or base / 'workspace').resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    job_handle = own_process_tree()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('砰砰解析台')
    lock = QLockFile(str(workspace / 'desktop.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, '砰砰解析台已运行', '这个工作区已有一个砰砰解析台窗口，请切换到已打开的窗口。')
        return 0
    window = Window(workspace)
    window.show()
    if args.quit_after:
        def finish():
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
            window.close()
        QTimer.singleShot(args.quit_after * 1000, finish)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
