"""在独立测试窗口中比较原生/旧自绘滚动条的真实 Chromium Paint 开销。

先以 QTWEBENGINE_REMOTE_DEBUGGING=127.0.0.1:9227 启动测试工作区，再运行本脚本。
只连接本机调试端口。正式应用不启用调试端口；轨迹只写入 .cache。
加载相同的 96 张卡片后预热图片，再以相同速度连续滚动，避免把图片缓存差异
误认为绘制优化。rAF 是主线程采样，不能等同于显示器最终呈现或主观流畅度。
"""
import base64
from collections import Counter
import json
from pathlib import Path
import time
import urllib.request

from websockets.sync.client import connect

OUTPUT = Path('.cache/qa-11-1-paint')
# 仅测试时恢复 0.11.0 的滚动条规则。正式页面不含这些伪元素样式。
LEGACY = '''html {scrollbar-color:auto}
::-webkit-scrollbar {width:14px;height:14px}
::-webkit-scrollbar-track {background:#101923;border-radius:12px}
::-webkit-scrollbar-thumb {background:#52667b;border:4px solid #101923;border-radius:12px;min-height:44px}
::-webkit-scrollbar-thumb:hover {background:#89a2ba;border-width:3px}
::-webkit-scrollbar-thumb:active {background:var(--accent);border-width:3px}
::-webkit-scrollbar-corner {background:#101923}'''
SCROLL = '''new Promise(resolve=>{
  let start=performance.now(),last=start,frames=[];
  function step(){const now=performance.now();frames.push(now-last);last=now;
    scrollTo(0,Math.min(6000,(now-start)*1.5));
    if(now-start<4000)requestAnimationFrame(step);
    else resolve({frames,images:document.images.length,height:document.scrollingElement.scrollHeight});
  }requestAnimationFrame(step);
})'''


class Protocol:
    def __init__(self, socket):
        self.socket, self.serial, self.events = socket, 0, []

    def receive(self):
        return json.loads(self.socket.recv(timeout=60))

    def call(self, method, params=None):
        self.serial += 1
        identity = self.serial
        self.socket.send(json.dumps(dict(id=identity, method=method, params=params or {})))
        while True:
            message = self.receive()
            if message.get('id') == identity:
                if 'error' in message:
                    raise RuntimeError(message['error'])
                return message.get('result', {})
            self.events.append(message)

    def js(self, script):
        result = self.call('Runtime.evaluate', dict(expression=script, awaitPromise=True, returnByValue=True))
        if 'exceptionDetails' in result:
            raise RuntimeError(result['exceptionDetails'])
        return result.get('result', {}).get('value')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen('http://127.0.0.1:9227/json', timeout=5) as response:
        pages = json.load(response)
    target = next(p for p in pages if p['type'] == 'page' and p['url'].endswith('/web/index.html'))
    summaries = []
    with connect(target['webSocketDebuggerUrl'], origin='http://localhost', max_size=None) as socket:
        protocol = Protocol(socket)
        protocol.js("""(async()=>{closeDetail();await pengpeng.changeView('cards');
          state.filters={};state.query='';state.favorites=false;state.limit=96;state.offset=0;
          await refresh();WheelScroll.stop();scrollTo(0,0);})()""")
        protocol.js(SCROLL)
        # 等当前预加载队列排空再开始对照；不要求缺失图片一定成功。
        deadline = time.monotonic() + 30
        while protocol.js('thumbnailWorkers>0 || thumbnailQueue.length>0'):
            if time.monotonic() > deadline:
                raise TimeoutError('图片队列未排空，不能进行已加载图片的对照')
            time.sleep(.2)
        try:
            for name, css in [('legacy', LEGACY), ('native', '')]:
                protocol.js("document.querySelector('#paint-comparison')?.remove();"
                            "var style=document.createElement('style');style.id='paint-comparison';"
                            f"style.textContent={json.dumps(css)};document.head.append(style);scrollTo(0,0)")
                time.sleep(.4)
                protocol.events.clear()
                protocol.call('Tracing.start', dict(categories='devtools.timeline,blink,cc,gpu,benchmark', transferMode='ReportEvents'))
                result = protocol.js(SCROLL)
                protocol.call('Tracing.end')
                while not any(e.get('method') == 'Tracing.tracingComplete' for e in protocol.events):
                    protocol.events.append(protocol.receive())
                trace = [item for event in protocol.events if event.get('method') == 'Tracing.dataCollected'
                         for item in event['params']['value']]
                (OUTPUT / f'{name}-trace.json').write_text(json.dumps({'traceEvents': trace}), 'utf-8')
                durations, counts = Counter(), Counter()
                for event in trace:
                    if event.get('ph') == 'X':
                        durations[event['name']] += event.get('dur', 0) / 1000
                        counts[event['name']] += 1
                frames = sorted(result.pop('frames'))
                summary = dict(mode=name, **result, frames=len(frames), p95FrameMs=frames[int(len(frames)*.95)],
                               maxFrameMs=max(frames), over25ms=sum(f>25 for f in frames),
                               work={key:dict(ms=round(durations[key], 2), count=counts[key])
                                     for key in ('Paint', 'Layout', 'UpdateLayoutTree', 'RasterTask', 'ProxyMain::BeginMainFrame')})
                summaries.append(summary)
                print(json.dumps(summary, ensure_ascii=False), flush=True)
            protocol.js('scrollTo(0,1200)')
            time.sleep(.2)
            shot = protocol.call('Page.captureScreenshot', dict(format='png'))
            (OUTPUT/'native.png').write_bytes(base64.b64decode(shot['data']))
        finally:
            protocol.js("document.querySelector('#paint-comparison')?.remove()")
    (OUTPUT/'summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2), 'utf-8')


if __name__ == '__main__':
    main()
