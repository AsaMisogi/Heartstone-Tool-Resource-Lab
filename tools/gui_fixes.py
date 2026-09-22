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
    settings_path = Path('workspace/settings.json')
    original_settings = settings_path.read_bytes()
    window = Window(Path('workspace').resolve())
    window.show()
    output = Path('.cache/qa-fixes')
    output.mkdir(parents=True, exist_ok=True)
    # 20 秒静音仅用于验证配套音轨的 15 秒上限，不覆盖游戏媒体。
    import wave
    with wave.open(str(output / 'cap-test.wav'), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
        wav.writeframes(b'\x00\x00' * 8000 * 20)
    cap_url = (output / 'cap-test.wav').resolve().as_uri()
    stages = [
        ('pagination', "(async()=>{await api('save_settings',{export_path:'E:/Code-Project-Doing/Heartstone-Tool/.cache/qa-fixes/exports'});await pengpeng.changeView('cards'); state.limit=24; state.infiniteScroll=false; await refresh(); if(document.querySelectorAll('[data-page]').length!==10)throw Error('ten shortcuts missing'); window.scrollTo(0,600); await new Promise(r=>setTimeout(r,150));const before=scrollY; await goPage(2); if(Math.abs(scrollY-before)>1)throw Error('pagination moved scroll '+before+' -> '+scrollY); return {before,after:scrollY,page:state.offset/24+1};})()"),
        ('jump-and-size', "(async()=>{await goPage(10);if(state.offset!==216)throw Error('jump failed'); const size=document.querySelector('#page-size');size.value='48';size.dispatchEvent(new Event('change'));await new Promise(r=>setTimeout(r,600));if(state.limit!==48||document.querySelectorAll('[data-card]').length!==48)throw Error('page size failed');return {size:state.limit};})()"),
        ('scroll-containment', "(async()=>{document.querySelector('[data-filter]').nextElementSibling.click();const popup=document.querySelector('.select-popup');if(getComputedStyle(popup).overscrollBehaviorY!=='contain'||getComputedStyle(document.querySelector('#detail')).overscrollBehaviorY!=='contain')throw Error('scroll chaining not contained');SelectUI.close();return {contained:true};})()"),
        ('infinite', "(async()=>{await pengpeng.changeView('settings');document.querySelector('#infinite-scroll').click();await new Promise(r=>setTimeout(r,250));await pengpeng.changeView('cards');if(!document.querySelector('#prev').hidden)throw Error('pagination visible');const count=document.querySelectorAll('[data-card]').length;await loadMore();const ids=[...document.querySelectorAll('[data-card]')].map(n=>n.dataset.card);if(ids.length!==count*2||new Set(ids).size!==ids.length)throw Error('append duplicated/replaced');await pengpeng.changeView('audio');if(document.querySelectorAll('[data-asset]').length!==48)throw Error('view reset failed');return {appended:ids.length};})()"),
        ('voice-source', "(async()=>{await pengpeng.showCard('EX1_116');await pengpeng.cardTab('voices');for(let i=0;i<100&&state.transcriptNote.includes('正在');i++)await new Promise(r=>setTimeout(r,100));if(document.querySelectorAll('.transcript-source').length!==3)throw Error('source transcripts missing: '+state.transcriptNote);return {source:document.querySelector('.transcript-source').textContent,voice:state.voiceItems.filter(x=>x.kind==='voice').length,sounds:state.voiceItems.filter(x=>x.kind==='sound').length};})()"),
        ('per-voice-export', "(async()=>{const ids=state.voiceItems.filter(a=>a.kind==='voice').slice(0,2).map(a=>a.id);for(const id of ids)await exportFiles({assetids:[id],context_cardid:state.card.id,locale:'zhcn'});if(document.querySelectorAll('.open-audio-export').length!==2)throw Error('export A overwritten by B');renderVoiceList();if(document.querySelectorAll('.open-audio-export').length!==2)throw Error('export lost on rerender');return {folders:ids.map(id=>state.audioExports.get(id))};})()"),
        ('companion-tail', "(async()=>{state.pairedAudio=true;const id=state.voiceItems.find(a=>a.kind==='voice'&&a.event.includes('m_PlayEffectDef')).id;await playAsset(id);audio.playbackRate=4;const duration=currentAudio.samples[0].duration;for(let i=0;i<150&&!audio.ended;i++)await new Promise(r=>setTimeout(r,100));if(!audio.ended)throw Error('voice did not finish');if(!companionTracks.some(t=>!t.paused&&!t.ended))throw Error('tail cut at voice end');const count=companionTracks.length;document.querySelector('#play-pause').click();await new Promise(r=>setTimeout(r,80));if(companionTracks.some(t=>!t.paused))throw Error('tail pause failed');stopPlayback();audio.playbackRate=1;return {voiceDuration:duration,tails:count};})()"),
        ('companion-cap', "(async()=>{state.pairedAudio=true;const id=state.voiceItems.find(a=>a.kind==='voice'&&a.event.includes('m_PlayEffectDef')).id;await playAsset(id);const track=companionTracks[0];track.src=CAP_URL;await track.play();track.currentTime=14.7;await new Promise(r=>setTimeout(r,650));if(!track.capped||!track.paused||track.currentTime>15.15)throw Error('15 second cap failed '+track.currentTime);const position=track.currentTime;stopPlayback();return {position,capped:true};})()".replace('CAP_URL', json.dumps(cap_url))),
        ('effect-concurrency', "(async()=>{const ref=state.card.effects.find(e=>!e.speech).ref;const start=performance.now();const effect=showEffect({reference:ref});const queryStart=performance.now();await api('list_cards',{limit:24});const latency=performance.now()-queryStart;await effect;await new Promise(r=>setTimeout(r,150));if(!fx?.systems.length||!fx.active)throw Error('effect missing');document.querySelector('#fx-time').value='0.12';document.querySelector('#fx-time').dispatchEvent(new Event('input'));if(fx.active)throw Error('scrub did not pause');const pixels=fx.ctx.getImageData(360,270,80,80).data;const colors=new Set();for(let i=0;i<pixels.length;i+=4)colors.add(pixels[i]+','+pixels[i+1]+','+pixels[i+2]);if(colors.size<4)throw Error('no particles drawn');if(latency>2500)throw Error('query blocked by effect');return {systems:fx.systems.length,queryMs:latency,totalMs:performance.now()-start,colors:colors.size};})()"),
        ('effect-cancel', "(async()=>{const ref='Bacon_Impact_Physical_Shout_Super.prefab:b86c16d6cdd6eab479f56188d66a6e18';const request=showEffect({reference:ref});await pengpeng.changeView('cards');await request;if(!document.querySelector('#detail').hidden)throw Error('stale effect reopened');return {cancelled:true};})()"),
        ('responsive', "(async()=>{await pengpeng.changeView('settings');if(state.infiniteScroll){document.querySelector('#infinite-scroll').click();await new Promise(r=>setTimeout(r,200));}await pengpeng.changeView('cards');if(document.documentElement.scrollWidth>innerWidth)throw Error('horizontal overflow');return {width:innerWidth};})()"),
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
            print('QA FAIL', error, flush=True)
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
    settings_path.write_bytes(original_settings)
    print('QA backend alive after close:', window.worker.is_alive(), flush=True)
    sys.exit(1 if failed or window.worker.is_alive() else 0)
