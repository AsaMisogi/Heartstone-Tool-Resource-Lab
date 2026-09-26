"""隔离 Unity / 原生音频库；消息循环在每个资源包之后让出执行权。"""
import logging
from logging.handlers import RotatingFileHandler
import queue
from pathlib import Path
import traceback
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from types import SimpleNamespace

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
    updates = ThreadPoolExecutor(max_workers=1, thread_name_prefix='updates')
    renders = ThreadPoolExecutor(max_workers=1, thread_name_prefix='card-render')
    render_job = None
    queued = deque()
    update_job = None
    transcript_job = None
    allowed = {'card_render', 'initialize', 'game_status', 'status', 'list_cards', 'card', 'thumbnail', 'portrait', 'list_assets',
               'audio', 'playback_audio', 'related_audio', 'card_audio', 'general_audio', 'effect', 'favorite', 'export', 'diagnostics', 'save_settings'}
    scan = None
    scan_scope = 'all'
    def detail_superseded(identity, background=False):
        """资源图在两个预制体之间让出取消点；消息仍留在队列供主循环响应。"""
        while True:
            try:
                queued.append(inbox.get_nowait())
            except queue.Empty:
                break
        return any(r['id'] > identity and (r.get('scope') == 'detail' or
                   (background and r.get('method') in ('audio', 'playback_audio', 'export', 'initialize', 'save_settings', 'list_cards', 'list_assets')))
                   for r in queued)
    try:
        while True:
            try:
                if not queued:
                    queued.append(inbox.get(timeout=0.001 if scan else 0.25))
                # 慢读取结束后合并积压的只读导航。每个被替代请求仍返回取消结果，
                # WebChannel Promise 可以正常释放，后台不会重做已离开的页面。
                while True:
                    try:
                        queued.append(inbox.get_nowait())
                    except queue.Empty:
                        break
                latest = {r['scope']: r['id'] for r in queued if r.get('scope')}
                kept = deque()
                for r in queued:
                    if (r.get('scope') and latest[r['scope']] != r['id']) or (r.get('background') and latest.get('detail', 0) > r['id']):
                        outbox.put({'id': r['id'], 'error': '请求已被新的选择替代', 'cancelled': True})
                    else:
                        kept.append(r)
                # 后台语言只占一个队列项，前台试听、导出和导航先执行。
                queued = deque(r for r in kept if not r.get('background'))
                queued.extend(r for r in kept if r.get('background'))
                request = queued.popleft()
            except queue.Empty:
                request = None
            if request:
                identity = request.get('id')
                method = request.get('method')
                if method == 'shutdown':
                    break
                try:
                    if method == 'card_render':
                        if render_job and render_job.cancel():
                            outbox.put({'id': render_job.request_id, 'error': '已切换卡面', 'cancelled': True})
                        # 下载只依赖快照路径，不共享 Unity 对象或 SQLite 连接。
                        render_job = renders.submit(Service.card_render, SimpleNamespace(cache=service.cache),
                                                    **request.get('params', {}))
                        render_job.request_id = identity
                        def render_done(future):
                            if future.cancelled():
                                return
                            try:
                                outbox.put({'id': future.request_id, 'result': future.result()})
                            except Exception as exc:
                                outbox.put({'id': future.request_id, 'error': str(exc)})
                        render_job.add_done_callback(render_done)
                        continue
                    if method == 'check_updates':
                        if update_job and not update_job.done():
                            raise ValueError('正在检查更新，请稍候')
                        from .updates import check_update
                        update_job = updates.submit(check_update)
                        def update_done(future, request_id=identity):
                            try:
                                outbox.put({'id': request_id, 'result': future.result()})
                            except Exception as exc:
                                outbox.put({'id': request_id, 'error': '检查更新失败：' + str(exc)})
                        update_job.add_done_callback(update_done)
                        continue
                    if method == 'transcripts':
                        if transcript_job and transcript_job.cancel():
                            outbox.put({'id': transcript_job.request_id, 'error': '已切换台词查询'})
                        params = {**request.get('params', {}), 'providers': service.settings['transcript_sources'] if service.settings.get('online_transcripts', True) else []}
                        def transcript_progress(message, done, total, request_id=identity):
                            outbox.put({'event': 'transcript_progress', 'request_id': request_id,
                                        'message': message, 'done': done, 'total': total})
                        job = transcripts.submit(supplement, workspace, **params, progress=transcript_progress)
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
                            scan_scope = request.get('params', {}).get('scope', 'all')
                            scan = service.scan(scope=scan_scope)
                        result = {'scanning': True}
                    elif method == 'invalidate_detail':
                        result = {}
                    elif method == 'cancel_scan':
                        if scan:
                            scan.close()
                        scan = None
                        result = {'scanning': False}
                        outbox.put({'event': 'scan_finished', 'scope': scan_scope, 'cancelled': True, 'status': service.status()})
                    elif method in allowed:
                        if method == 'initialize' and scan:
                            scan.close()
                            scan = None
                        service.cancel_detail = (lambda: detail_superseded(identity, request.get('background', False))) if method == 'card_audio' else lambda: False
                        try:
                            result = getattr(service, method)(**request.get('params', {}))
                        finally:
                            service.cancel_detail = lambda: False
                    else:
                        raise ValueError('未知操作')
                    outbox.put({'id': identity, 'result': result})
                except InterruptedError:
                    outbox.put({'id': identity, 'error': '请求已被新的选择替代', 'cancelled': True})
                except Exception as exc:
                    log.exception('操作 %s 失败', method)
                    outbox.put({'id': identity, 'error': str(exc)})
            if scan:
                try:
                    progress = next(scan)
                    outbox.put({'event': 'progress', **progress})
                except StopIteration:
                    scan = None
                    outbox.put({'event': 'scan_finished', 'scope': scan_scope, 'cancelled': False, 'status': service.status()})
                except Exception as exc:
                    scan = None
                    log.exception('扫描中断')
                    outbox.put({'event': 'scan_finished', 'scope': scan_scope, 'error': str(exc), 'status': service.status()})
    finally:
        transcripts.shutdown(wait=False, cancel_futures=True)
        updates.shutdown(wait=False, cancel_futures=True)
        renders.shutdown(wait=False, cancel_futures=True)
        if service.store:
            service.store.close()
        log.info('解析进程已关闭')
