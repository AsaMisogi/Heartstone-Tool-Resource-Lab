"""真实 Qt 窗口端到端检查：页面、金卡、异画、中文音频、WAV 导出及特效。

使用页面已公开的用户流程函数，不伪造后端数据。截图写入 .cache/qa。
必须在 Windows 桌面中运行；受限无桌面沙箱无法启动 Chromium 渲染器。
"""
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    app = QApplication([])
    window = Window(Path('workspace').resolve())
    window.show()
    output = Path('.cache/qa-transcripts')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('hero-transcripts', "(async()=>{await pengpeng.showCard('HERO_01');await pengpeng.cardTab('voices');const rows=[...document.querySelectorAll('.transcript')];const texts=rows.map(r=>r.textContent);if(rows.length<36||!texts.includes('嗬，你好。'))throw Error('Chinese transcripts missing');const first=document.querySelector('.voice-row .transcript');if(!first||first.getBoundingClientRect().top>innerHeight)throw Error('transcript below fold');if(speechText('<死亡>')!=='<死亡>')throw Error('stage direction stripped');return {transcripts:rows.length,first:first.textContent,greeting:texts.find(t=>t.includes('你好'))};})()"),
        ('untranscribed', "(async()=>{pengpeng.state.status.settings.online_transcripts=false;await pengpeng.showCard('EX1_116');await pengpeng.cardTab('voices');const missing=document.querySelectorAll('.transcript-missing').length;if(missing!==3)throw Error('missing transcript state unclear');return {missing};})()"),
    ]
    results = []
    current = -1
    deadline = time.monotonic() + 180
    failed = False

    def next_stage():
        global current, deadline
        current += 1
        if current >= len(stages):
            # 主动让后端进入复杂皮肤解析，再关闭窗口，验证忙碌时也没有孤儿进程。
            window.inbox.put({'id': 999999, 'method': 'save_settings', 'params': {'paired_audio': False}})
            QTimer.singleShot(200, finish)
            return
        deadline = time.monotonic() + 180
        name, source = stages[current]
        if name == 'responsive':
            window.resize(1060, 720)
        print('QA START', name, flush=True)
        window.page.runJavaScript("window.__qa=null;" + source + ".then(r=>{window.__qa={result:r};}).catch(e=>{window.__qa={error:e.message};});")

    def finish(error=None):
        global failed
        failed = bool(error)
        if error:
            results.append({'error': error})
        timer.stop()
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
        window.close()

    def received(encoded):
        if not encoded:
            return
        data = json.loads(encoded)
        if data.get('error'):
            finish(data['error'])
            return
        name = stages[current][0]
        timer.stop()
        # DOM 已更新不等于 Chromium 已绘制；等待一帧以上再截图。
        def capture():
            window.grab().save(str(output / f'{name}.png'))
            results.append({'stage': name, **data})
            print('QA PASS', name, str(data)[:250], flush=True)
            next_stage()
            if current < len(stages):
                timer.start()
        QTimer.singleShot(350, capture)

    def tick():
        if time.monotonic() > deadline:
            finish('GUI stage timeout: ' + (stages[current][0] if current >= 0 else 'initialization'))
        elif current < 0:
            window.page.runJavaScript("!!window.pengpeng?.state.status?.ready && document.querySelectorAll('.card-image img').length>2", lambda ready: next_stage() if ready and current < 0 else None)
        else:
            window.page.runJavaScript('window.__qa?JSON.stringify(window.__qa):null', received)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(500)
    app.exec()
    print('QA backend alive after close:', window.worker.is_alive(), flush=True)
    sys.exit(1 if failed or window.worker.is_alive() else 0)
