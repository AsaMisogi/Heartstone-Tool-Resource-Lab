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
    output = Path('.cache/qa-workbench')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('gallery', "(async()=>{await new Promise(r=>setTimeout(r,1800));const f=document.querySelectorAll('[data-filter]');if(f.length!==7)throw Error('missing filters');return {filters:f.length,images:document.querySelectorAll('.card-image img').length};})()"),
        ('filters', "(async()=>{const s=pengpeng.state;s.filters={class:'4',cost:'0',type:'5'};await pengpeng.refresh();if(!s.total)throw Error('empty zero-cost mage spells');return {total:s.total};})()"),
        ('hero-filters', "(async()=>{await pengpeng.changeView('heroes');const s=document.querySelector('[data-filter=hero_group]');if(s.options[1].value!=='12'||s.options[s.options.length-1].value!=='enemy')throw Error('hero order');pengpeng.state.filters={hero_group:'enemy',battlegrounds:'exclude'};await pengpeng.refresh();if(!pengpeng.state.total)throw Error('no enemies');return {enemies:pengpeng.state.total};})()"),
        ('image-viewer', "(async()=>{await pengpeng.showCard('EX1_116');document.querySelector('#large-portrait').click();if(!document.querySelector('#image-viewer').open)throw Error('viewer did not open');return {dimensions:document.querySelector('#viewer-title').textContent};})()"),
        ('native-export', "(async()=>{document.querySelector('#viewer-close').click();document.querySelector('#export-image').click();while(!pengpeng.state.exports.length)await new Promise(r=>setTimeout(r,100));const e=pengpeng.state.exports[0];if(!e.folder.includes('火车王里诺艾')||!document.querySelector('.open-export-inline'))throw Error('named export/open missing');return {folder:e.folder,media:e.media};})()"),
        ('full-card', "(async()=>{await pengpeng.cardTab('render');const im=document.querySelector('#full-card');if(!im)throw Error(document.querySelector('#render-stage').textContent);await im.decode();return {width:im.naturalWidth,height:im.naturalHeight};})()"),
        ('dismiss-detail', "(async()=>{document.querySelector('#detail-backdrop').click();if(!document.querySelector('#detail').hidden||document.querySelector('main').inert)throw Error('backdrop close failed');return {closed:true};})()"),
        ('voice-tabs', "(async()=>{await pengpeng.showCard('EX1_116');await pengpeng.cardTab('voices');const voices=document.querySelectorAll('[data-play]').length;document.querySelector('[data-voice-kind=sound]').click();const sounds=document.querySelectorAll('[data-play]').length;document.querySelector('[data-voice-kind=voice]').click();if(!voices||!sounds)throw Error('voice/sound tabs empty');return {voices,sounds};})()"),
        ('pair-setting', "(async()=>{const c=document.querySelector('#paired-audio');c.click();for(let i=0;i<20;i++){await new Promise(r=>setTimeout(r,50));const s=await pengpeng.api('status');if(s.settings.paired_audio===c.checked)return {saved:s.settings.paired_audio};}throw Error('pair preference not saved');})()"),
        ('paired-playback', "(async()=>{const b=document.querySelector('[data-play]');await pengpeng.playAsset(b.dataset.play);await new Promise(r=>setTimeout(r,400));if(!companionTracks.length||companionTracks.some(t=>t.paused))throw Error('companion audio not playing');const tracks=companionTracks.length;document.querySelector('#play-pause').click();await new Promise(r=>setTimeout(r,80));if(companionTracks.some(t=>!t.paused))throw Error('companion pause failed');document.querySelector('#stop-player').click();return {tracks,pause:true};})()"),
        ('missing-language', "(async()=>{const select=document.querySelector('#voice-locale');select.value='enus';select.dispatchEvent(new Event('change'));await new Promise(r=>setTimeout(r,600));if(document.querySelector('#voice-warning').hidden)throw Error('language warning missing');return {warning:document.querySelector('#voice-warning').textContent};})()"),
        ('audio-select', "(async()=>{await pengpeng.changeView('audio');document.querySelector('#category').nextElementSibling.click();const p=document.querySelector('.select-popup'),r=p.getBoundingClientRect();if(r.left<0||r.right>innerWidth||r.bottom>innerHeight)throw Error('popup outside viewport');return {popup:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},viewport:[innerWidth,innerHeight]};})()"),
        ('responsive', "(async()=>{document.body.click();await new Promise(r=>setTimeout(r,400));if(innerWidth!==1060||document.documentElement.scrollWidth>innerWidth)throw Error('responsive overflow');return {width:innerWidth,overflow:false};})()"),
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
