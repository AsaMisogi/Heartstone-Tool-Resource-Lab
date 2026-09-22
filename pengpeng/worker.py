"""隔离 Unity / 原生音频库；消息循环在每个资源包之后让出执行权。"""
import logging
from logging.handlers import RotatingFileHandler
import queue
from pathlib import Path
import traceback
from concurrent.futures import ThreadPoolExecutor

from .service import Service
from .transcripts import supplement


def effect_worker_main(inbox, outbox, workspace):
    """独立解析特效，Unity 对象和 SQLite 连接绝不跨线程/进程共享。

    宿主负责超时和取消，卡在原生解压时仍能终止，不占普通查询队列。
    进程存活期间复用资源包及最近十二个预览；只响应特效请求。
    """
    service = Service(Path(workspace))
    try:
        while True:
            request = inbox.get()
            if request.get('method') == 'shutdown':
                break
            try:
                if service.reader is None:
                    service.initialize(effect_only=True)
                result = service.effect(**request.get('params', {}))
                outbox.put({'id': request['id'], 'result': result})
            except Exception as exc:
                outbox.put({'id': request['id'], 'error': str(exc)})
    finally:
        if service.store:
            service.store.close()


class EventLog(logging.Handler):
    def __init__(self, outbox):
        super().__init__(logging.INFO)
        self.outbox = outbox

    def emit(self, record):
        self.outbox.put({'event': 'log', 'level': record.levelname, 'message': self.format(record)})


def worker_main(inbox, outbox, workspace):
    workspace = Path(workspace)
    logs = workspace / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', '%H:%M:%S')
    handlers = [logging.StreamHandler(), RotatingFileHandler(logs / 'pengpeng.log', maxBytes=4_000_000,
                  backupCount=3, encoding='utf-8'), EventLog(outbox)]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.captureWarnings(True)
    log = logging.getLogger(__name__)
    log.info('砰砰解析台解析进程已启动；游戏资源只读')
    service = Service(workspace, outbox.put)
    # 网络台词请求不使用 Unity/SQLite 对象，在独立线程等待，不能堵住本地试听。
    transcripts = ThreadPoolExecutor(max_workers=1, thread_name_prefix='transcripts')
    transcript_job = None
    allowed = {'card_render', 'initialize', 'status', 'list_cards', 'card', 'thumbnail', 'portrait', 'list_assets',
               'audio', 'related_audio', 'card_audio', 'general_audio', 'effect', 'favorite', 'export', 'diagnostics', 'save_settings'}
    scan = None
    try:
        while True:
            try:
                request = inbox.get(timeout=0.001 if scan else 0.25)
            except queue.Empty:
                request = None
            if request:
                identity = request.get('id')
                method = request.get('method')
                if method == 'shutdown':
                    break
                try:
                    if method == 'transcripts':
                        if transcript_job and transcript_job.cancel():
                            outbox.put({'id': transcript_job.request_id, 'error': '已切换台词查询'})
                        params = {**request.get('params', {}), 'providers': service.settings['transcript_sources'] if service.settings.get('online_transcripts', True) else []}
                        job = transcripts.submit(supplement, workspace, **params)
                        job.request_id = identity
                        def deliver(future):
                            if future.cancelled():
                                return
                            try:
                                outbox.put({'id': future.request_id, 'result': future.result()})
                            except Exception as exc:
                                outbox.put({'id': future.request_id, 'error': '公开台词查询失败：' + str(exc)})
                        job.add_done_callback(deliver)
                        transcript_job = job
                        continue
                    if method == 'scan':
                        if not service.reader:
                            raise ValueError('请先连接游戏目录')
                        if scan is None:
                            scan = service.scan()
                        result = {'scanning': True}
                    elif method == 'cancel_scan':
                        if scan:
                            scan.close()
                        scan = None
                        result = {'scanning': False}
                        outbox.put({'event': 'scan_finished', 'cancelled': True, 'status': service.status()})
                    elif method in allowed:
                        if method == 'initialize' and scan:
                            scan.close()
                            scan = None
                        result = getattr(service, method)(**request.get('params', {}))
                    else:
                        raise ValueError('未知操作')
                    outbox.put({'id': identity, 'result': result})
                except Exception as exc:
                    log.exception('操作 %s 失败', method)
                    outbox.put({'id': identity, 'error': str(exc)})
            if scan:
                try:
                    progress = next(scan)
                    outbox.put({'event': 'progress', **progress})
                except StopIteration:
                    scan = None
                    outbox.put({'event': 'scan_finished', 'cancelled': False, 'status': service.status()})
                except Exception as exc:
                    scan = None
                    log.exception('扫描中断')
                    outbox.put({'event': 'scan_finished', 'error': str(exc), 'status': service.status()})
    finally:
        transcripts.shutdown(wait=False, cancel_futures=True)
        if service.store:
            service.store.close()
        log.info('解析进程已关闭')
