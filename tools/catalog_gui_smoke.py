"""真实 Qt 图鉴验收：分类、排序按钮、分页、输入法与最小窗口布局。

运行：.venv/Scripts/python.exe -X utf8 tools/catalog_gui_smoke.py
不联网获取卡面，不修改游戏目录；截图和结果写入 .cache/qa-catalog。
测试结束恢复排序记忆，不覆盖用户的收藏、语音或页面容量设置。
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
    output = Path('.cache/qa-catalog')
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
          window.__savedOrder=localStorage.getItem('pengpeng.catalogOrder');
          await p.changeView('cards');
          check(!document.querySelector('.tile-tag'),'英雄混入卡牌');
          return {total:p.state.total,tiles:tiles().length};
        """),
        ('sort-name', """
          await order('name');
          const expected=await p.api('list_cards',{sort:'name',locale:p.state.locale,
            descending:p.state.catalogOrder.cards.descending,limit:p.state.limit});
          check(JSON.stringify(tiles())===JSON.stringify(expected.items.map(x=>x.id)),'名称排序不一致');
          return {first:tiles()[0],hint:$('#sort-hint').textContent};
        """),
        ('sort-id-reverse', """
          await order('id'); const before=p.state.catalogOrder.cards.descending;
          $('#sort-direction').click(); await settled();
          check(p.state.catalogOrder.cards.descending!==before,'倒序按钮无效');
          const expected=await p.api('list_cards',{sort:'id',descending:!before,limit:p.state.limit});
          check(JSON.stringify(tiles())===JSON.stringify(expected.items.map(x=>x.id)),'ID排序不一致');
          $('#next').click(); await settled(); check(p.state.offset===p.state.limit,'下一页无效');
          await order('release'); check(p.state.offset===0,'排序未重置分页');
          return {first:tiles()[0],date:$('.release-date').textContent};
        """),
        ('heroes', """
          await p.changeView('heroes'); await order('release');
          check(document.querySelectorAll('.tile-tag').length===tiles().length,'英雄分类错误');
          const expected=await p.api('list_cards',{hero:true,sort:'release',
            descending:p.state.catalogOrder.heroes.descending,limit:p.state.limit});
          check(JSON.stringify(tiles())===JSON.stringify(expected.items.map(x=>x.id)),'皮肤日期排序不一致');
          return {total:p.state.total,first:tiles()[0]};
        """),
        ('search-ime', """
          await p.changeView('cards');
          const search=$('#search'), generation=p.state.generation;
          search.dispatchEvent(new CompositionEvent('compositionstart'));
          search.value='ji'; search.dispatchEvent(new Event('input'));
          await new Promise(r=>setTimeout(r,350));
          check(p.state.query==='','输入法组词时发起查询');
          search.value='吉安娜'; search.dispatchEvent(new CompositionEvent('compositionend'));
          await new Promise(r=>setTimeout(r,350)); await settled();
          check(p.state.query==='吉安娜','输入法完成后未查询');
          check(!document.querySelector('.tile-tag'),'搜索混入英雄');
          return {query:p.state.query,total:p.state.total};
        """),
        ('minimum-window', """
          await p.changeView('cards');
          check(document.documentElement.scrollWidth<=innerWidth,'最小窗口出现横向溢出');
          check(!$('#catalog-order').hidden,'排序栏不可见');
          await p.changeView('audio'); check($('#catalog-order').hidden,'音频页出现图鉴排序');
          await p.changeView('cards');
          return {width:innerWidth,height:innerHeight};
        """),
    ]
    results, current, failed = [], -1, False
    deadline = time.monotonic() + 120

    def finish(error=None):
        global failed
        failed = bool(error)
        timer.stop()
        if error:
            results.append({'error': error})
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
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
