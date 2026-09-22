"""本地卡牌 / 皮肤关系验收：使用真实数据，不要求网络。"""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service

if __name__ == '__main__':
    service = Service(Path('workspace'))
    status = service.initialize()
    results = {'status': status, 'cards': []}
    voices = {}
    for cid in ('HERO_01', 'HERO_01a', 'HERO_01bn', 'EX1_116'):
        print('验证皮肤', cid, flush=True)
        card = service.card(cid)
        related = service.card_audio(cid)
        voices[cid] = {item['id'] for item in related['items']}
        results['cards'].append({'id': cid, 'name': card['name'],
            'variants': [v['label'] for v in card['variants'] if v['available']],
            'voices': len(related['items']), 'errors': related['errors']})
    assert voices['HERO_01'] and voices['HERO_01a']
    assert voices['HERO_01'] != voices['HERO_01a'], '不同皮肤被错误合并'
    assert len(service.card('HERO_01')['variants']) > 4, '节日外观未被保留'
    Path('.cache/local-validation.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(results['cards'], ensure_ascii=False, indent=2))
