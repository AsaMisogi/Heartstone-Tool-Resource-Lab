"""0.8 本机真实数据回归；仅读取游戏，结果保存在独立 QA 工作区。"""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service
from pengpeng.card_relations import supplement_relations

s = Service(Path('.cache/qa-05-workspace').resolve())
s.initialize('F:/Games/Hearthstone')
if '--rebuild' in sys.argv:
    from pengpeng.catalog import build_catalog
    build_catalog(s)
with s.store.db:
    supplement_relations(s.store.db)
out = {}
for cid in ('HERO_01aa','HERO_01','ICC_829','ICC_830','HERO_11aq','AV_337','CORE_AV_337','DINO_410','BOT_451','LOE_092','DAL_417','ONY_005'):
    c=s.card(cid)
    item={'name':c['name'], 'relations':c['related'], 'links':c['text_links']}
    if cid in ('HERO_01aa','HERO_01','ICC_829','ICC_830','HERO_11aq'):
        start=time.perf_counter(); voices=s.card_audio(cid)
        item.update(seconds=round(time.perf_counter()-start,3), voices=voices)
        print(cid, 'audio', len(voices['items']), 'errors', len(voices['errors']), flush=True)
    out[cid]=item
out['battlegrounds'] = s.list_cards(battlegrounds=True, limit=5)
out['keywords'] = s.list_cards(filters={'keyword':'嘲讽','race':'20'},limit=5)
Path('.cache/verify-08.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),'utf8')
print('BG',out['battlegrounds']['total'],'taunt beasts',out['keywords']['total'],flush=True)
s.store.close()
