"""真实 Qt 回归：轻量语音识别自动回退、重试、准确台词优先、关闭和取消。

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
    output = Path('.cache/qa-speech-gui')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('speech-settings', """(async()=>{
          await changeView('settings');
          const sources=(await api('status')).settings.transcript_sources;
          if(sources.slice(-2).map(x=>x.id).join()!=='hsdata,wikigg'||sources.slice(-2).some(x=>x.enabled))throw Error('default sources');
          if(!document.querySelector('#speech-recognition').checked)throw Error('default ASR disabled');
          state.status.settings=await api('save_settings',{online_transcripts:false,speech_recognition:true});
          return {sources};
        })()"""),
        ('speech-source-priority', """(async()=>{
          state.status.settings=await api('save_settings',{online_transcripts:true});
          const originalApi=api;let resolveSource, speechCalls=0;
          api=(method,params)=>{
            if(method==='transcripts')return new Promise(resolve=>{resolveSource=resolve;});
            if(method==='speech')speechCalls++;
            return originalApi(method,params);
          };
          try {
            await showCard('EX1_116');await cardTab('voices');
            if(!resolveSource||speechCalls||state.speechRun)throw Error('ASR ran before sources finished');
            const item=state.voiceItems.find(a=>a.kind==='voice'&&!a.text);
            resolveSource({items:[{id:item.id,text:'测试来源台词',source:'https://example.org',source_name:'测试来源'}],sources:[],note:'来源查询完成'});
            for(let i=0;i<400&&(state.transcriptPending||state.speechRun);i++)await new Promise(r=>setTimeout(r,50));
            const protectedItem=state.voiceItems.find(a=>a.id===item.id);
            if(!speechCalls||protectedItem.speech_done||protectedItem.text!=='测试来源台词')throw Error('fallback priority incorrect');
            return {waitedForSources:true,sourceTextPreserved:true,speechCalls};
          } finally {
            api=originalApi;
            state.status.settings=await api('save_settings',{online_transcripts:false});
          }
        })()"""),
        ('speech-auto', """(async()=>{
          await showCard('EX1_116');await cardTab('voices');
          for(let i=0;i<400&&state.speechRun;i++)await new Promise(r=>setTimeout(r,50));
          if(state.speechRun)throw Error('ASR stalled');
          const recognized=state.voiceItems.filter(a=>a.speech_done);
          if(!recognized.length)throw Error('no automatic recognition: '+state.speechNote);
          if(!document.querySelector('.transcript-summary').textContent.includes('条语音识别'))throw Error('recognition count absent');
          if(!document.querySelector('.speech-label'))throw Error('disclaimer absent: '+state.speechNote);
          if(recognized.some(a=>a.text))throw Error('ASR overwrote transcript');
          window.speechTestId=recognized[0].id;
          return {recognized:recognized.map(a=>({name:a.name,text:a.speech_text})),note:state.speechNote};
        })()"""),
        ('speech-retry', """(async()=>{
          const button=[...document.querySelectorAll('[data-speech]')].find(b=>b.dataset.speech===speechTestId);
          if(!button||button.disabled||button.textContent!=='重试语音识别')throw Error('retry absent');
          button.click();await new Promise(r=>setTimeout(r,50));
          if(!state.speechRun||![...document.querySelectorAll('[data-speech]')].every(b=>b.disabled))throw Error('duplicate requests allowed');
          for(let i=0;i<400&&state.speechRun;i++)await new Promise(r=>setTimeout(r,50));
          if(state.speechRun)throw Error('retry stalled');
          const item=state.voiceItems.find(a=>a.id===speechTestId);
          if(item.speech_error||item.speech_cached)throw Error('retry failed or reused cache');
          // 模拟后续公开来源补齐准确字幕，只替换其数据；真实识别链路已在前面验证。
          item.text='测试：后来取得的准确台词';renderVoiceList();
          const row=[...document.querySelectorAll('[data-play]')].find(b=>b.dataset.play===speechTestId).closest('.voice-row');
          if(row.querySelector('.speech-label')||!row.textContent.includes(item.text))throw Error('official text priority');
          return {retry:true,accurateTextWins:true};
        })()"""),
        ('speech-off', """(async()=>{
          await changeView('settings');document.querySelector('#speech-recognition').click();
          for(let i=0;i<100&&state.status.settings.speech_recognition;i++)await new Promise(r=>setTimeout(r,30));
          if((await api('status')).settings.speech_recognition)throw Error('switch not persisted');
          await showCard('EX1_116');await cardTab('voices');await new Promise(r=>setTimeout(r,150));
          if(state.speechRun||document.querySelector('[data-speech]'))throw Error('ASR continued while off');
          return {off:true};
        })()"""),
        ('speech-cancel', """(async()=>{
          state.status.settings=await api('save_settings',{speech_recognition:true});
          await cardTab('voices');
          for(let i=0;i<200&&state.speechRun;i++)await new Promise(r=>setTimeout(r,30));
          const item=state.voiceItems.find(a=>a.speech_done);
          const running=recognizeMissing(state.detailGeneration,item.id);
          await new Promise(r=>setTimeout(r,80));
          closeDetail();await running;
          if(state.speechRun||state.voiceItems.length)throw Error('stale result after close');
          await changeView('settings');
          return {cancelled:true};
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
            window.page.runJavaScript("!!window.pengpeng?.state.status?.ready", lambda ready: next_stage() if ready and current < 0 else None)
        else:
            window.page.runJavaScript('window.__qa?JSON.stringify(window.__qa):null', received)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(500)
    app.exec()
    settings_path.write_bytes(original_settings)
    print('QA backend alive after close:', window.worker.is_alive(), flush=True)
    assert window.bridge.speech.process is None
    sys.exit(1 if failed or window.worker.is_alive() else 0)
