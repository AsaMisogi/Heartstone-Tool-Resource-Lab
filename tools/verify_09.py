"""本机资源验收：音乐定向索引、真实声音延迟与统一试听时间轴。"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service

if __name__ == '__main__':
    s = Service(Path('.cache/qa-05-workspace'))
    s.initialize('F:/Games/Hearthstone')
    start = time.perf_counter()
    for progress in s.scan(scope='music'):
        if progress['done'] % 30 == 0:
            print(progress, flush=True)
    music=s.list_assets(category='音乐 / 登场曲', limit=100)
    assert music['total'] > 1000 and len(music['subgroups']) >= 7
    result={'music_total':music['total'],'subgroups':music['subgroups'],
            'index_seconds':time.perf_counter()-start,'cards':{}}
    for cid in ('EX1_116','BOT_021','ICC_829','ICC_830','ONY_005','BOT_548'):
        items=s.card_audio(cid)['items']
        selected=next(x for x in items if x.get('group')=='play' and x['kind']=='voice')
        plan=s.audio_plan(cid,selected['id'],paired_audio=True,general_audio=True)
        result['cards'][cid]=plan
        if cid=='BOT_021':
            assert plan['main_delay']==.1 and plan['tracks']
            decoded=s.playback_audio(selected['id'],cid,paired_audio=True,general_audio=True)
            assert 'audio-timelines' in decoded['samples'][0]['path']
            result['preview']=decoded['samples'][0]
        print(cid,len(plan['tracks']),len(plan['skipped']),flush=True)
    Path('.cache/verify09.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    s.store.close()
