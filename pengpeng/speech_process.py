"""桌面端的语音进程生命周期：单任务、可取消、硬超时、闲置后释放内存。"""
import multiprocessing as mp
import queue
import time
from .speech import speech_worker


class SpeechProcess:
    def __init__(self, workspace, emit):
        self.workspace, self.emit = workspace, emit
        self.process = None
        self.request_id = None
        self.activity = time.monotonic()

    def request(self, data):
        if self.request_id is not None:
            self.emit({'id': data['id'], 'error': '已有语音正在识别，请稍后重试'})
            return
        if self.process is None:
            context = mp.get_context('spawn')
            self.inbox, self.outbox = context.Queue(), context.Queue()
            self.process = context.Process(target=speech_worker,
                args=(self.inbox, self.outbox, str(self.workspace)), daemon=True)
            self.process.start()
        self.request_id = data['id']
        self.activity = time.monotonic()
        self.deadline = self.activity + 125  # 首次模型下载的总等待上限。
        self.inbox.put(data)

    def stop(self, reason='语音识别已取消'):
        if self.process is not None:
            self.process.terminate()
            self.process.join(timeout=0.3)
            for channel in (self.inbox, self.outbox):
                channel.cancel_join_thread()
                channel.close()
            self.process = None
        if self.request_id is not None:
            self.emit({'id': self.request_id, 'error': reason})
            self.request_id = None

    def poll(self):
        if self.process is None:
            return
        for _ in range(8):
            try:
                message = self.outbox.get_nowait()
            except queue.Empty:
                break
            if message.get('event') == 'speech_progress':
                if message['message'].startswith('正在离线识别'):
                    self.deadline = time.monotonic() + 20
            else:
                self.request_id = None
                self.activity = time.monotonic()
            self.emit(message)
        if not self.process.is_alive():
            self.stop('语音识别进程退出，可重试；试听不受影响')
        elif self.request_id is not None and time.monotonic() > self.deadline:
            self.stop('语音识别超时，已释放资源；可重试')
        elif self.request_id is None and time.monotonic() - self.activity > 15:
            self.stop()  # 通过退出进程归还原生内存，而不依赖 Python GC。
