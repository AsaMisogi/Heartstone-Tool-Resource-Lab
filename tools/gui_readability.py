"""真实 Qt 验收：独立测试工作区验证筛选记忆、相关牌、混音与缩放。

运行：.venv/Scripts/python.exe -X utf8 tools/gui_readability.py
截图与报告写入 .cache/qa-ux；不修改用户工作区设置、收藏及游戏文件。
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
    qa_workspace = Path('.cache/qa-ux/workspace').resolve()
    qa_workspace.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path('workspace/settings.json').read_text('utf-8'))
    settings.update(view_state={}, ui_scale=1, font_scale=1, online_transcripts=False, speech_recognition=False, export_path=str(qa_workspace / 'exports'))
    (qa_workspace / 'settings.json').write_text(json.dumps(settings), 'utf-8')
    window = Window(qa_workspace)
    window.show()
    output = Path('.cache/qa-ux')
    output.mkdir(parents=True, exist_ok=True)
    # 点击真实控件，并等待列表对应的异步请求完成，不用固定延时猜结果。
    prelude = """
      const p=window.pengpeng, $=s=>document.querySelector(s);
      const check=(ok,msg)=>{if(!ok)throw Error(msg)};
      const settled=async()=>{
        await new Promise(r=>setTimeout(r,20));
        while(p.state.loading) await new Promise(r=>setTimeout(r,25));
      };
      const order=async(value)=>{
        $('#sort-by').value=value; $('#sort-by').dispatchEvent(new Event('change'));
        await settled();
      };
      const tiles=()=>[...document.querySelectorAll('[data-card]')].map(n=>n.dataset.card);
    """
    stages = [
        ('remember-filters', """
          await p.changeView('cards');
          $('#search').value='火车王'; $('#search').dispatchEvent(new Event('input'));
          p.state.filters={rarity:'5'}; await p.refresh();
          await p.changeView('heroes');
          check($('#search').value==='', '英雄继承卡牌搜索');
          $('#search').value='吉安娜'; $('#search').dispatchEvent(new Event('input'));
          await p.changeView('cards');
          check($('#search').value==='火车王' && p.state.filters.rarity==='5','切页丢失筛选');
          const saved=(await p.api('status')).settings.view_state;
          check(saved.cards.query==='火车王' && saved.heroes.query==='吉安娜','筛选未保存');
          return {saved};
        """),
        ('detail', """
          await p.showCard('EX1_116');
          check(p.state.card.metadata.attack===6,'攻击属性不正确');
          check($('.card-text b'), '规则文本强调丢失');
          check($('.portrait-stage').getBoundingClientRect().height<=210,'缩略图过大');
          return {metadata:p.state.card.metadata};
        """),
        ('related', """
          await p.showCard('TOY_700');
          if (!p.state.card.related.length) {
            const candidate=await p.api('card',{cardid:'REV_018'});
            await p.showCard(candidate.id);
          }
          check(p.state.card.related.length>0,'测试卡缺少相关牌');
          window.__parent=p.state.card.id;
          $('[data-related-card]').click();
          while(!$('#back-card') || !$('#portrait img')) await new Promise(r=>setTimeout(r,50));
          check(p.state.card.id!==window.__parent,'相关牌未跳转');
          $('#back-card').click();
          while(p.state.card.id!==window.__parent || !$('#portrait img')) await new Promise(r=>setTimeout(r,50));
          return {parent:p.state.card.id};
        """),
        ('voice-export', """
          await p.showCard('EX1_116'); await p.cardTab('voices');
          check($('#mix-voice-export'),'缺少混音开关');
          for (const id of ['paired-audio','general-audio','mix-voice-export']) {
            const input=$('#'+id); if(!input.checked) input.click();
            while(input.disabled) await new Promise(r=>setTimeout(r,30));
          }
          const voice=p.state.voiceItems.find(x=>x.kind==='voice' && /Play/i.test(x.event));
          check(voice,'未找到登场语音');
          const result=await p.api('export',{assetids:[voice.id], context_cardid:p.state.card.id, locale:'zhcn',mix_voice:true,paired_audio:true,general_audio:true});
          check(result.files.some(f=>f.endsWith('_合并.wav')),'未生成混音');
          check(result.media.some(m=>m.mix?.tracks.length>1),'混音缺少配音');
          return {files:result.files,errors:result.errors,media:result.media};
        """),
        ('minimum-window', """
          await p.changeView('settings');
          check($('#settings-page').firstElementChild.classList.contains('display-settings'),'显示设置未置顶');
          $('#ui-scale').value=140; $('#ui-scale').dispatchEvent(new Event('change'));
          $('#font-scale').value=150; $('#font-scale').dispatchEvent(new Event('change'));
          await new Promise(r=>setTimeout(r,500));
          check(document.documentElement.scrollWidth<=innerWidth,'缩放导致横向溢出');
          check(getComputedStyle(document.documentElement).fontSize==='21px','字体缩放未应用');
          await p.changeView('cards');
          check(document.documentElement.scrollWidth<=innerWidth,'图鉴缩放溢出');
          await p.showCard('EX1_116');
          check($('#detail').scrollWidth<=$('#detail').clientWidth,'详情字体缩放溢出');
          $('#close-detail').click(); await p.changeView('settings');
          return {width:innerWidth,rootFont:getComputedStyle(document.documentElement).fontSize};
        """),
        ('reset', """
          $('#reset-display').click(); await new Promise(r=>setTimeout(r,300));
          await p.changeView('cards'); $('#reset-search').click(); await settled();
          check(!p.state.query && !Object.keys(p.state.filters).length,'重置未清空');
          const saved=(await p.api('status')).settings;
          check(saved.view_state.heroes.query==='吉安娜','重置误清其他页面');
          return {saved:saved.view_state};
        """),
    ]
    if '--layout-only' in sys.argv:
        stages = [stage for stage in stages if stage[0] in ('detail', 'minimum-window')]
    results, current, failed = [], -1, False
    deadline = time.monotonic() + 120

    def finish(error=None):
        global failed
        failed = bool(error)
        timer.stop()
        if error:
            results.append({'error': error})
            window.grab().save(str(output / 'failure.png'))
        (output / ('layout-results.json' if '--layout-only' in sys.argv else 'results.json')).write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
        window.page.runJavaScript("if(window.__savedOrder===null)localStorage.removeItem('pengpeng.catalogOrder');else if(window.__savedOrder!==undefined)localStorage.setItem('pengpeng.catalogOrder',window.__savedOrder);",
                                  lambda _: window.close())

    def next_stage():
        global current, deadline
        current += 1
        if current == len(stages):
            finish()
            return
        deadline = time.monotonic() + 90
        name, source = stages[current]
        if name == 'minimum-window':
            window.resize(1060, 720)
        print('QA START', name, flush=True)
        window.page.runJavaScript('window.__qa=null;(async()=>{' + prelude + source +
                                 '})().then(result=>window.__qa={result}).catch(e=>window.__qa={error:e.message});')

    def received(encoded):
        if not encoded:
            return
        data = json.loads(encoded)
        if data.get('error'):
            finish(data['error'])
            return
        timer.stop()
        def capture():
            name = stages[current][0]
            window.grab().save(str(output / f'{name}.png'))
            results.append({'stage': name, **data})
            print('QA PASS', name, data, flush=True)
            next_stage()
            if current < len(stages):
                timer.start()
        QTimer.singleShot(350, capture)

    def tick():
        if time.monotonic() > deadline:
            finish('图鉴验证超时')
        elif current < 0:
            window.page.runJavaScript('!!window.pengpeng?.state.status?.ready && !window.pengpeng.state.loading',
                                     lambda ready: next_stage() if ready and current < 0 else None)
        else:
            window.page.runJavaScript('window.__qa?JSON.stringify(window.__qa):null', received)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(300)
    app.exec()
    print('QA backend alive after close:', window.worker.is_alive(), flush=True)
    sys.exit(1 if failed or window.worker.is_alive() else 0)
