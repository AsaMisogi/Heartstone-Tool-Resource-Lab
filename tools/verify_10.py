"""本机资源验收：经典音乐、免疫、索引范围、叠放试听与合并导出一致性。"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service


if __name__ == '__main__':
    service = Service(Path('.cache/qa-05-workspace'))
    service.initialize('F:/Games/Hearthstone')
    output = Path('.cache/qa-10')
    output.mkdir(exist_ok=True)
    result = {}
    try:
        # 定向扫描不能刷新“完整索引”时间；已有资源包应复用而非重新解压。
        before = service.store.get_meta('last_scan')
        start = time.perf_counter()
        for progress in service.scan(scope='music'):
            if progress['done'] % 100 == 0:
                print(progress, flush=True)
        assert service.store.get_meta('last_scan') == before
        result['page_index_seconds'] = time.perf_counter() - start
        result['classic'] = [x['name'] for x in service.list_assets(
            category='背景音乐', subgroup='经典默认音乐')['items']]
        assert {'Main_Title', 'Duel', 'Better Hand', 'Collection Manager'} <= set(result['classic'])
        immune = {r[0] for r in service.store.db.execute("SELECT id FROM card_keywords WHERE keyword='免疫'")}
        assert {'KAR_712', 'WW_808', 'WW_815', 'EX1_549'} <= immune
        assert not {'ICCA08_020', 'BOM_03_Vapos_002hb'} & immune
        result['immune_count'] = len(immune)
        result['categories'] = {category: service.list_assets(category=category)['total']
            for category in ('角色语音', '音效', '短音乐 / 登场曲', '背景音乐')}
        result['cards'] = {}
        for cid in ('BOT_021', 'ICC_829', 'ICC_830'):
            items = service.card_audio(cid)['items']
            selected = next(x for x in items if x.get('group') == 'play' and x['kind'] == 'voice')
            plan = service.audio_plan(cid, selected['id'], paired_audio=True, general_audio=True)
            assert plan['tracks'] and not plan['skipped']
            assert len({t['id'] for t in plan['tracks']}) == len(plan['tracks'])
            assert any(t['placement'] == 'overlay' for t in plan['tracks'])
            preview = service.playback_audio(selected['id'], cid, paired_audio=True, general_audio=True)
            assert not preview['timeline']['errors']
            assert 'audio-timelines' in preview['samples'][0]['path']
            if cid == 'BOT_021':
                assert plan['main_delay'] == .1
                # 实际 WAV 样本比较，验证两条调用路径的播放起点、响度与尾声一致。
                exported = service.export(assetids=[selected['id']], context_cardid=cid,
                    mix_voice=True, paired_audio=True, general_audio=True)
                assert not exported['errors']
                a, rate_a = sf.read(preview['samples'][0]['path'])
                b, rate_b = sf.read(exported['files'][0])
                assert rate_a == rate_b and np.array_equal(a, b)
                result['preview_matches_export'] = True
            result['cards'][cid] = plan
            print(cid, len(plan['tracks']), 'tracks', flush=True)
        (output / 'resources.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
        print('PASS', result['categories'], flush=True)
    finally:
        service.store.close()
