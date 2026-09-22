"""真实 Qt 验收：独立测试工作区验证图鉴摘要、酒馆标记、详情顺序与缩放。

运行：.venv/Scripts/python.exe -X utf8 tools/gui_catalog_cards.py
截图与报告写入 .cache/qa-catalog-cards；不修改用户工作区设置、收藏及游戏文件。
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
    qa_workspace = Path('.cache/qa-catalog-cards/workspace').resolve()
    qa_workspace.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path('workspace/settings.json').read_text('utf-8'))
    settings.update(view_state={}, ui_scale=1, font_scale=1, online_transcripts=False, speech_recognition=False, export_path=str(qa_workspace / 'exports'))
    (qa_workspace / 'settings.json').write_text(json.dumps(settings), 'utf-8')
    window = Window(qa_workspace)
    window.show()
    output = Path('.cache/qa-catalog-cards')
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
        ('cards', """
          await p.changeView('cards');
          check($('.tile-stats') && $('.tile-class') && $('.tile-set'), '图鉴缺少摘要');
          const data=await p.api('list_cards',{query:'火车王'});
          check(data.items.some(c=>c.summary.attack===6),'真实卡牌摘要错误');
          $('#results').scrollIntoView({block:'start'});
          return {count:document.querySelectorAll('.card-tile').length, sample:data.items[0]};
        """),
        ('list', """
          const first=$('[data-card]'), ids=tiles(), generation=p.state.generation;
          $('[data-layout="list"]').click();
          check($('#results').classList.contains('catalog-list'), '没有切换列表');
          check(first===$('[data-card]') && ids.join()===tiles().join(), '切换重建卡片');
          check(generation===p.state.generation, '切换触发查询');
          check($('#catalog-size').disabled, '列表未禁用尺寸');
          return {count:ids.length};
        """),
        ('layout-memory', """
          await p.changeView('heroes');
          check(!$('#results').classList.contains('catalog-list'), '两个图鉴布局串扰');
          $('[data-layout="list"]').click();
          await p.changeView('cards');
          check($('#results').classList.contains('catalog-list'), '布局未恢复');
          $('#reset-layout').click();
          $('#catalog-size').value=180; $('#catalog-size').dispatchEvent(new Event('input'));
          $('#catalog-size').dispatchEvent(new Event('change'));
          await p.changeView('heroes'); await p.changeView('cards');
          check($('#catalog-size').value==='180', '尺寸未记忆');
          const saved=await p.api('status');
          check(saved.settings.view_state.cards.display.size===180, '尺寸未持久化');
          $('#reset-layout').click();
          check($('#catalog-size').value==='220', '恢复默认失败');
          check(getComputedStyle($('.card-image img')).objectFit==='contain', '原画仍在裁切');
          return {display:p.state.catalogDisplay};
        """),
        ('heroes', """
          await p.changeView('heroes');
          p.state.filters={battlegrounds:'only'}; await p.refresh();
          check($('.is-battlegrounds'), '酒馆英雄缺少标记');
          check($('.tile-class'), '英雄缺少职业');
          $('#results').scrollIntoView({block:'start'});
          return {label:$('.tile-kind').textContent};
        """),
        ('heroes-list', """
          $('[data-layout="list"]').click();
          check($('#results').classList.contains('catalog-list'), '英雄列表未切换');
          check($('.tile-kind').textContent.includes('酒馆'), '列表酒馆标记丢失');
          return {label:$('#crumb').textContent};
        """),
        ('detail', """
          await p.showCard('EX1_116');
          check($('.detail-facts').previousElementSibling.id==='detail-body','基础信息未置底');
          check(!$('.detail-facts').textContent.includes('打造') && !$('.detail-facts').textContent.includes('赛制'),'详情保留已删除字段');
          check($('.card-text b'), '规则强调丢失');
          check($('.card-text').nextElementSibling.classList.contains('flavor'), '趣闻不在描述下方');
          check($('.flavor').nextElementSibling.classList.contains('card-artist'), '画师不在趣闻下方');
          check($('.card-artist').nextElementSibling.id==='portrait', '原画顺序错误');
          await p.cardTab('raw');
          check($('.detail-facts').previousElementSibling.id==='detail-body','切换标签改变顺序');
          await p.cardTab('art');
          return {facts:$('.card-facts').textContent};
        """),
        ('detail-bottom', """
          $('#detail').scrollTop=$('#detail').scrollHeight;
          return {scroll:$('#detail').scrollTop};
        """),
        ('minimum-window', """
          $('#close-detail').click(); await p.changeView('settings');
          $('#ui-scale').value=140; $('#ui-scale').dispatchEvent(new Event('change'));
          $('#font-scale').value=150; $('#font-scale').dispatchEvent(new Event('change'));
          await new Promise(r=>setTimeout(r,500));
          await p.changeView('cards');
          check(document.documentElement.scrollWidth<=innerWidth,'页面横向溢出');
          check([...document.querySelectorAll('.tile-info')].every(n=>n.scrollWidth<=n.clientWidth),'卡片文字溢出');
          $('#results').scrollIntoView({block:'start'});
          return {width:innerWidth};
        """),
        ('minimum-list', """
          $('[data-layout="list"]').click();
          check(document.documentElement.scrollWidth<=innerWidth,'列表页面横向溢出');
          check([...document.querySelectorAll('.tile-info')].every(n=>n.scrollWidth<=n.clientWidth),'列表文字溢出');
          return {width:innerWidth};
        """),
    ]
    if '--layout-only'  in sys.argv:
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
