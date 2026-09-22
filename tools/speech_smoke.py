"""真实游戏语音与独立进程性能抽样；需先安装中文小模型，不评价全库准确率。"""
import ctypes
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service
from pengpeng.speech_process import SpeechProcess
from pengpeng.audio_strings import audio_key


def peak_memory(process):
    from ctypes import wintypes as w
    class Counters(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('faults', w.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'paged_peak', 'paged', 'nonpaged_peak', 'nonpaged', 'pagefile', 'pagefile_peak')]
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = w.HANDLE
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x410, False, process.pid)
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    api = ctypes.windll.psapi.GetProcessMemoryInfo
    api.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
    try:
        if not api(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return round(counters.peak / 1024 / 1024, 1)
    finally:
        kernel.CloseHandle(handle)


if __name__ == '__main__':
    mp.freeze_support()
    service = Service(Path('workspace'))
    service.initialize()
    rows = service.store.db.execute("SELECT id,name FROM assets WHERE category='角色语音' AND locale='zhcn' AND duration BETWEEN 2 AND 8 ORDER BY name LIMIT 4").fetchall()
    messages, results = [], []
    controller = SpeechProcess(service.workspace, messages.append)
    try:
        for index, row in enumerate(rows):
            samples = service.audio(assetid=row['id'])['samples']
            start = time.monotonic()
            controller.request({'id': index + 1, 'params': {'paths': [s['path'] for s in samples], 'locale': 'zhcn', 'force': True}})
            while controller.request_id is not None:
                controller.poll()
                time.sleep(0.025)
            reply = next(m for m in messages if m.get('id') == index + 1)
            if 'error' in reply:
                raise RuntimeError(reply['error'])
            results.append({'name': row['name'], 'audio_seconds': sum(s['duration'] for s in samples),
                'total_seconds': round(time.monotonic() - start, 3), 'peak_memory_mib': peak_memory(controller.process),
                'client_text': service.strings['zhcn'].get(audio_key(row['name']), ''), **reply['result']})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        # 同一真实音频二次请求必须走缓存，且闲置后进程必须被回收。
        controller.request({'id': 100, 'params': {'paths': [s['path'] for s in samples], 'locale': 'zhcn'}})
        while controller.request_id is not None:
            controller.poll()
            time.sleep(0.025)
        assert next(m for m in messages if m.get('id') == 100)['result']['cached']
        idle = time.monotonic()
        while controller.process is not None and time.monotonic() - idle < 18:
            controller.poll()
            time.sleep(0.05)
        assert controller.process is None
        output = Path('.cache/qa-speech')
        output.mkdir(parents=True, exist_ok=True)
        (output / 'benchmark.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf8')
        print('PASS: real audio, cache and idle process release')
    finally:
        controller.stop()
        service.store.close()
