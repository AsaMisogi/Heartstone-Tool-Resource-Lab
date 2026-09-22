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
    output = Path('.cache/qa')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('gallery', "Promise.resolve({cards:pengpeng.state.status.cards,images:document.querySelectorAll('.card-image img').length})"),
        ('normal', "pengpeng.showCard('EX1_116').then(()=>({name:pengpeng.state.card.name,images:document.querySelectorAll('#portrait img').length}))"),
        ('golden', "(async()=>{pengpeng.state.variant=1;await pengpeng.cardTab('art');const layers=document.querySelectorAll('.layer').length;if(layers<2)throw Error('golden layers missing');return {layers};})()"),
        ('signature', "(async()=>{pengpeng.state.variant=2;await pengpeng.cardTab('art');const layers=document.querySelectorAll('.layer').length;if(layers<2)throw Error('signature layers missing');return {layers};})()"),
        ('hero-voices', "(async()=>{await pengpeng.showCard('HERO_01');await pengpeng.cardTab('voices');return {voices:document.querySelectorAll('[data-play]').length,text:document.querySelector('#detail-body').textContent.slice(0,300)};})()"),
        ('audio', "(async()=>{const id=document.querySelector('[data-play]').dataset.play;await pengpeng.playAsset(id);const a=document.querySelector('#audio');await new Promise((r,j)=>{if(a.readyState>=2)r();else{a.addEventListener('canplay',r,{once:true});setTimeout(()=>j(Error('audio timeout')),10000);}});return {duration:a.duration,ready:a.readyState,paused:a.paused};})()"),
        ('export', "(async()=>{const id=document.querySelector('[data-play]').dataset.play;const r=await pengpeng.api('export',{assetids:[id]});return {files:r.files,errors:r.errors};})()"),
        ('effect', "(async()=>{const c=await pengpeng.api('card',{cardid:'CS2_029'});await pengpeng.showEffect({reference:c.effects[0].ref});document.querySelector('#fx-play').click();await new Promise(r=>setTimeout(r,1500));return {canvas:!!document.querySelector('#fx-canvas'),text:document.querySelector('#detail-content').textContent.slice(0,300)};})()"),
        ('audio-library', "pengpeng.changeView('audio').then(()=>({rows:document.querySelectorAll('.list-row').length,total:pengpeng.state.total}))"),
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
            window.inbox.put({'id': 999999, 'method': 'card_audio', 'params': {'cardid': 'HERO_01bn'}})
            QTimer.singleShot(200, finish)
            return
        deadline = time.monotonic() + 180
        name, source = stages[current]
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
