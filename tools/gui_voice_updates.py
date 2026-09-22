"""真实 Qt 回归：通用音效、设置记忆、原卡台词定位和播放竞态。

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
    output = Path('.cache/qa-voice-updates')
    output.mkdir(parents=True, exist_ok=True)
    stages = [
        ('default-and-original-source', """(async()=>{
          state.status.settings.online_transcripts=false;
          await api('save_settings',{general_audio:false,paired_audio:false});
          state.generalAudio=false;state.pairedAudio=false;
          await pengpeng.showCard('CORE_EX1_382');await pengpeng.cardTab('voices');
          if(document.querySelector('#general-audio').checked)throw Error('default enabled');
          const ids=state.voiceItems.filter(x=>x.kind==='voice').map(x=>x.transcript_dbfid);
          if(ids.some(id=>id!==1167))throw Error('original card not resolved');
          return {originalIds:ids,defaultOff:true};
        })()"""),
        ('general-playback', """(async()=>{
          await pengpeng.showCard('CORE_BOT_548');await pengpeng.cardTab('voices');
          document.querySelector('#general-audio').click();
          for(let i=0;i<50&&!state.generalAudio;i++)await new Promise(r=>setTimeout(r,50));
          const id=state.voiceItems.find(x=>x.kind==='voice'&&x.event.includes('m_PlayEffectDef')).id;
          await playAsset(id); await new Promise(r=>setTimeout(r,100));
          const names=companionTracks.map(x=>decodeURI(x.src));
          for(const name of ['FX_MinionSummon_Drop','taunt_shield_up','spell_DivineShield_target_1'])
            if(!names.some(x=>x.includes(name)))throw Error('missing '+name);
          if(names.some(x=>x.includes('break')||x.includes('Upgrade')))throw Error('wrong state audio');
          document.querySelector('#play-pause').click();await new Promise(r=>setTimeout(r,80));
          if(companionTracks.some(x=>!x.paused))throw Error('pause failed');
          document.querySelector('#volume').value='0.2';document.querySelector('#volume').dispatchEvent(new Event('input'));
          if(companionTracks.some(x=>Math.abs(x.volume-0.2)>0.01))throw Error('volume failed');
          stopPlayback();await pengpeng.cardTab('voices');
          const settings=(await api('status')).settings;
          if(!settings.general_audio||settings.paired_audio||!document.querySelector('#general-audio').checked)throw Error('memory failed');
          return {tracks:names.map(x=>x.split('/').pop()),remembered:true};
        })()"""),
        ('attack-underlay-and-dedup', """(async()=>{
          const id=state.voiceItems.find(x=>x.kind==='voice'&&x.event.includes('m_AttackEffectDef')).id;
          const shared=await api('general_audio',{cardid:state.card.id,assetid:id,locale:'zhcn'});
          if(shared.items.some(x=>/shield|Summon_Drop/i.test(x.name)))throw Error('play sound on attack');
          document.querySelector('#paired-audio').click();
          for(let i=0;i<50&&!state.pairedAudio;i++)await new Promise(r=>setTimeout(r,50));
          await playAsset(id);
          const tracks=companionTracks.map(x=>x.src);
          if(new Set(tracks).size!==tracks.length)throw Error('duplicate shared track');
          if(!tracks.length)throw Error('no paired underlay');
          stopPlayback();return {tracks:tracks.map(x=>decodeURI(x).split('/').pop()),shared:shared.items.map(x=>x.name)};
        })()"""),
        ('cancel-inflight-option', """(async()=>{
          const originalApi=api;let release,entered=false;
          const gate=new Promise(r=>release=r);
          api=async(method,params)=>{const result=await originalApi(method,params);if(method==='general_audio'){entered=true;await gate;}return result;};
          try {
            const id=state.voiceItems.find(x=>x.kind==='voice'&&x.event.includes('m_PlayEffectDef')).id;
            const playing=playAsset(id);
            for(let i=0;i<100&&!entered;i++)await new Promise(r=>setTimeout(r,40));
            if(!entered)throw Error('preparation not started');
            document.querySelector('#general-audio').click();
            for(let i=0;i<50&&state.generalAudio;i++)await new Promise(r=>setTimeout(r,50));
            release();await playing;
            if(companionTracks.length)throw Error('stale companions started');
            return {cancelled:true};
          } finally {release();api=originalApi;stopPlayback();}
        })()"""),
        ('hero-regression', """(async()=>{
          await pengpeng.showCard('HERO_01');await pengpeng.cardTab('voices');
          const lines=state.voiceItems.filter(x=>x.kind==='voice'&&x.text);
          if(lines.length<36||!lines.some(x=>speechText(x.text)==='嗬，你好。'))throw Error('hero subtitles regressed');
          const result=await api('general_audio',{cardid:'HERO_01',assetid:state.voiceItems.find(x=>x.kind==='voice').id});
          if(result.items.length)throw Error('minion sounds attached to hero');
          return {transcripts:lines.length,shared:0};
        })()"""),
        ('responsive', """(async()=>{
          await pengpeng.showCard('CORE_BOT_548');await pengpeng.cardTab('voices');
          if(document.documentElement.scrollWidth>innerWidth)throw Error('horizontal overflow');
          const box=document.querySelector('#general-audio').getBoundingClientRect();
          if(box.right>innerWidth||box.width<1)throw Error('checkbox not visible');
          return {width:innerWidth};
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
