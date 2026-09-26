"""0.14 本机桌面回归：新工作区无完整索引启动、中英切换、对照试听和导出。

只操作自建窗口中的 DOM，隔离设置/缓存/导出至 .cache，不修改用户工作区。
PENGPENG_RENDERER=qt 可重复验证兼容引擎。每步结果和截图保存在 .cache/qa-14。
"""
import json
import multiprocessing as mp
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.app import Window, own_process_tree
from pengpeng.service import Service
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


STEPS = [
    ('fresh-connect', """async()=>{
      if(state.status.string_warnings.length)throw Error(JSON.stringify(state.status.string_warnings));
      if(!document.querySelector('#app-version').textContent.includes('0.14.0'))throw Error('版本缺失');
      document.querySelector('#locale').value='enus';
      await document.querySelector('#locale').onchange();
      await showCard('HERO_01'); await cardTab('voices');
      if(state.voiceLocale!=='enus'||!state.voiceItems.some(x=>x.text))throw Error('英语未同步或无字幕');
      return {indexed:state.status.indexed,bundles:state.status.bundles,voices:state.voiceItems.length};
    }"""),
    ('independent-memory', """async()=>{
      closeDetail(); await changeView('settings');
      const toggle=document.querySelector('#sync-voice-locale'); toggle.checked=false;
      await toggle.onchange({target:toggle});
      await changeView('cards'); document.querySelector('#locale').value='zhcn';
      await document.querySelector('#locale').onchange();
      await showCard('HERO_01');await cardTab('voices');
      document.querySelector('#voice-locale').value='enus';
      await document.querySelector('#voice-locale').onchange();
      closeDetail(); await showCard('EX1_001');await cardTab('voices');
      if(state.locale!=='zhcn'||state.voiceLocale!=='enus')throw Error('独立语言记忆丢失');
      const saved=await api('status');
      if(saved.settings.voice_locale!=='enus'||saved.settings.sync_voice_locale)throw Error('设置未落盘');
      return {filter:state.locale,resource:state.voiceLocale};
    }"""),
    ('multi-language', """async()=>{
      await showCard('HERO_01'); await cardTab('voices');
      const box=document.querySelector('#other-voice-locales');box.checked=true;
      await box.onchange({target:box});
      const primary=document.querySelector('#voice-list');
      const deadline=Date.now()+90000;
      while(state.otherVoices.some(g=>g.status!=='ready')&&Date.now()<deadline)await new Promise(r=>setTimeout(r,200));
      const group=state.otherVoices.find(g=>g.locale==='zhcn');
      if(!group?.items.some(x=>x.text)||group.status!=='ready')throw Error('中文对照缺失');
      if(primary!==document.querySelector('#voice-list'))throw Error('后台重建了主列表');
      const row=document.querySelector('[data-other-language="zhcn"] [data-play]');
      if(!row?.closest('.voice-row'))throw Error('对照未嵌入主行');
      const slot=row.closest('[data-voice-match]');
      const matched=group.index.get(slot.dataset.voiceMatch);
      if(matched.id!==row.dataset.play)throw Error('语音对应错误');
      const toggle=document.querySelector('#toggle-other-voices');
      if(toggle.getAttribute('aria-expanded')!=='true')throw Error('默认应展开');
      toggle.click();
      if([...document.querySelectorAll('[data-voice-match]')].some(n=>!n.hidden))throw Error('统一折叠失败');
      document.querySelector('#voice-next').click();
      if([...document.querySelectorAll('[data-voice-match]')].some(n=>!n.hidden))throw Error('翻页丢失折叠状态');
      document.querySelector('#voice-prev').click();
      toggle.click();
      if([...document.querySelectorAll('[data-voice-match]')].some(n=>n.hidden))throw Error('统一展开失败');
      const playable=document.querySelector('[data-other-language="zhcn"] [data-play]');
      await playable.onclick(); await new Promise(r=>setTimeout(r,300));
      if(currentAudio.locale!=='zhcn'||audio.paused||!audio.duration)throw Error('对照播放语言错误');
      const duration=audio.duration;stopPlayback();
      const button=playable.closest('.voice-translation').querySelector('[data-export]');await button.onclick();
      if(!state.exports.at(-1)?.folder.includes('zhcn'))throw Error('导出语言错误');
      return {languages:state.otherVoices.map(g=>g.locale),duration,export:state.exports.at(-1).folder};
    }"""),
    ('linked-resource-and-cancel', """async()=>{
      closeDetail();await changeView('settings');
      const toggle=document.querySelector('#sync-voice-locale');toggle.checked=true;await toggle.onchange({target:toggle});
      await changeView('cards');await showCard('HERO_01');await cardTab('voices');
      document.querySelector('#voice-locale').value='enus';await document.querySelector('#voice-locale').onchange();
      if(state.locale!=='enus'||document.querySelector('#locale').value!=='enus')throw Error('反向同步失败');
      await showCard('HERO_07d');await cardTab('voices');
      await new Promise(r=>setTimeout(r,220));
      closeDetail();await showCard('EX1_001');await cardTab('voices');
      const box=document.querySelector('#other-voice-locales');box.checked=false;await box.onchange({target:box});
      await new Promise(r=>setTimeout(r,500));
      if(state.card.id!=='EX1_001'||document.querySelector('.voice-translation'))throw Error('旧请求覆盖新卡');
      return {card:state.card.id,locale:state.voiceLocale};
    }"""),
]


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    output = Path('.cache/qa-14').resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = output / 'workspace'
    service = Service(work)
    service.save_settings(game_path=r'F:\Games\Hearthstone', online_transcripts=False,
                          speech_recognition=False, auto_check_updates=False,
                          index_guide_seen=True, sync_voice_locale=True,
                          show_other_voice_locales=False)
    app = QApplication([])
    window = Window(work)
    window.show()
    engine = 'native' if window.native_browser else 'qt'
    index = -1
    results = []
    failed = False
    busy = False
    deadline = time.monotonic() + 180

    def finish(error=None):
        global failed
        failed = bool(error)
        timer.stop()
        if error:
            results.append({'error': error})
            print('FAIL', error, flush=True)
        (output / f'{engine}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf8')
        window.close()

    def advance():
        global index, deadline
        index += 1
        deadline = time.monotonic() + 150
        if index == len(STEPS):
            finish()
            return
        print('START', engine, STEPS[index][0], flush=True)
        window.page.runJavaScript('window.__qa14=null;(' + STEPS[index][1] +
            ')().then(result=>window.__qa14={result}).catch(e=>window.__qa14={error:e.stack})')

    def receive(encoded):
        global busy
        busy = False
        if not encoded:
            return
        data = json.loads(encoded)
        if data.get('error'):
            finish(data['error'])
            return
        window.page.runJavaScript('window.__qa14=null')
        results.append({'stage':STEPS[index][0], **data})
        print('PASS', STEPS[index][0], data['result'], flush=True)
        if STEPS[index][0] == 'multi-language':
            window.grab().save(str(output / f'{engine}.png'))
            window.page.runJavaScript("document.querySelector('.voice-translation .transcript').closest('.voice-row').scrollIntoView()")
            QTimer.singleShot(200, lambda: window.grab().save(str(output / f'{engine}-other.png')))
            QTimer.singleShot(350, advance)
        else:
            advance()

    def poll():
        global busy
        if time.monotonic() > deadline:
            finish('等待界面超时')
        elif busy:
            return
        elif index < 0:
            window.page.runJavaScript('!!window.pengpeng?.state.status?.ready', lambda ready: advance() if ready and index < 0 else None)
        else:
            busy = True
            window.page.runJavaScript('window.__qa14?JSON.stringify(__qa14):null', receive)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(250)
    app.exec()
    sys.exit(1 if failed else 0)
