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
    qa_workspace = Path('.cache/qa-recovery/workspace').resolve()
    qa_workspace.mkdir(parents=True, exist_ok=True)
    (qa_workspace / 'settings.json').write_text(json.dumps({'game_path': 'F:/Games/Hearthstone', 'online_transcripts': False, 'speech_recognition': False}), encoding='utf8')
    window = Window(qa_workspace)
    window.show()
    output = Path('.cache/qa-recovery')
    output.mkdir(parents=True, exist_ok=True)
    # 固定失败/恢复响应，仅拦截台词接口；卡牌与界面仍由真实 Qt/本地资源加载。
    stages = [
        ('retry-ready', """(async()=>{
          await pengpeng.showCard('EX1_116');await pengpeng.cardTab('voices');
          state.status.settings.online_transcripts=true;renderVoiceList();
          if(!document.querySelector('#retry-transcripts'))throw Error('retry absent');
          window.savedApi=api;window.retryCalls=[];
          api=(method,params)=>{if(method!=='transcripts')return savedApi(method,params);
            retryCalls.push(params);return new Promise((resolve,reject)=>{window.resolveTranscript=resolve;window.rejectTranscript=reject;});};
          document.querySelector('#retry-transcripts').click();await new Promise(r=>setTimeout(r,40));
          document.querySelector('#retry-transcripts').click();
          if(!document.querySelector('#retry-transcripts').disabled||retryCalls.length!==1||!retryCalls[0].force)throw Error('retry not guarded');
          rejectTranscript(Error('测试网络断开'));await new Promise(r=>setTimeout(r,40));
          if(document.querySelector('#retry-transcripts').disabled)throw Error('retry stuck');
          return {calls:retryCalls.length,enabled:true};
        })()"""),
        ('retry-success', """(async()=>{
          document.querySelector('#retry-transcripts').click();await new Promise(r=>setTimeout(r,40));
          const item=state.voiceItems.find(x=>x.kind==='voice'&&!x.text);
          resolveTranscript({items:[{id:item.id,text:'重试成功的测试台词',source:'https://example.org',source_name:'测试响应',match_method:'audio_key'}],sources:[],note:'恢复成功'});
          await new Promise(r=>setTimeout(r,40));
          if(!document.querySelector('#voice-list').textContent.includes('按音频键精确匹配'))throw Error('match label absent');
          if(document.querySelector('#retry-transcripts').disabled)throw Error('partial retry disabled');
          return {calls:retryCalls.length,recovered:true};
        })()"""),
        ('retry-stale', """(async()=>{
          document.querySelector('#retry-transcripts').click();await new Promise(r=>setTimeout(r,40));
          const old=resolveTranscript;state.status.settings.online_transcripts=false;
          await pengpeng.showCard('HERO_01');await pengpeng.cardTab('voices');
          old({items:[],sources:[],note:'不应出现的旧响应'});await new Promise(r=>setTimeout(r,40));
          if(state.transcriptNote.includes('不应出现'))throw Error('stale response applied');
          if(document.querySelector('#retry-transcripts'))throw Error('disabled online still has retry');
          api=savedApi;return {staleIgnored:true,heroTranscripts:document.querySelectorAll('.transcript').length};
        })()"""),
        ('source-settings', """(async()=>{
          await pengpeng.changeView('settings');
          const labels=[...document.querySelectorAll('[data-provider]')].map(x=>x.closest('label').textContent.trim());
          if(labels.length!==4||!labels[0].includes('百度')||!labels[3].includes('Wiki.gg'))throw Error('source order');
          if(document.querySelectorAll('[data-source-edit]').length)throw Error('builtins editable');
          return {labels};
        })()"""),
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
