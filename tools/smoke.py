"""在真实本地资源上验证关键读取链路，输出不会进入游戏目录。"""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service

if __name__ == '__main__':
    s = Service(Path('workspace'))
    print(json.dumps(s.initialize(), ensure_ascii=False))
    for cid in ['CS2_029', 'EX1_116', 'HERO_01']:
        c = s.card(cid)
        print(cid, c['name'], c['text'], len(c['effects']))
        for i in range(3):
            p = s.portrait(cid, i)
            print('PORTRAIT', i, len(p['images']), p['errors'])
    ref = s.card('HERO_01')['effects'][0]['ref']
    a = s.related_audio(ref)
    print('RELATED', a)
    if a['items']:
        print('AUDIO', s.audio(assetid=a['items'][0]['id']))
    e = s.effect(reference=s.card('CS2_029')['effects'][0]['ref'])
    print('FX', e['components'], len(e['sounds']), e['errors'])
