"""百科升级的离线回归：引用、精确字幕、聚合分页、品质缓存与卡图蒙版。"""
import io
import json
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image, ImageDraw

from pengpeng.ifindhs import parse_page, save_page, fetch
from pengpeng.images import portrait_preview
from pengpeng.service import Service
from pengpeng.storage import Store
from pengpeng.transcripts import supplement
from pengpeng.transcript_sources import DEFAULT_SOURCES, migrate_sources


def wiki_html(code='HERO_11aq'):
    return f'''<table><tr><th>代码：</th><td>{code}</td></tr></table>
      <table><tr><td>卡牌触发：灵界打击</td><td>
      <audio id="VO_HERO_11aq_Trigger_DeathStrike_01"></audio><span>▶️</span>测试触发台词
      </td><td>下载</td></tr><tr><td>攻击</td><td>
      <audio id="VO_HERO_11aq_Attack_01"></audio>测试攻击
      </td></tr></table>'''


def test_ifindhs_exact_clip_and_identity():
    data = parse_page(wiki_html(), 'HERO_11aq')
    assert data['clips']['VO_HERO_11AQ_TRIGGER_DEATHSTRIKE_01'] == '测试触发台词'
    assert data['triggers']['VO_HERO_11AQ_TRIGGER_DEATHSTRIKE_01'] == '灵界打击'
    assert data['quotes'] == {'Attack': '测试攻击'}
    with pytest.raises(ValueError):
        parse_page(wiki_html(), 'TIME_618')
    with pytest.raises(ValueError):
        parse_page('<html>正在确认你是不是机器人</html>', 'HERO_11aq')


def test_ifindhs_ambiguity_and_script_are_not_transcripts():
    html = wiki_html() + '''<tr><td>触发</td><td><audio id="VO_HERO_11aq_Trigger_DeathStrike_01"></audio>
        另一个分支<script>不应执行</script></td></tr>'''
    assert 'VO_HERO_11AQ_TRIGGER_DEATHSTRIKE_01' not in parse_page(html, 'HERO_11aq')['clips']


def test_ifindhs_browser_cache_offline_and_failure_does_not_overwrite(tmp_path):
    save_page(tmp_path, 'HERO_11aq', wiki_html(), 'https://wiki.ifindhs.com/index.php?title=test')
    identity = dict(cardid='HERO_11aq', name='测试', dbfid=1)
    with patch('pengpeng.ifindhs.urlopen', side_effect=OSError('offline')):
        assert fetch(tmp_path, identity, True)['stale']
    with pytest.raises(ValueError):
        save_page(tmp_path, 'HERO_11aq', '<html>blocked</html>', 'https://wiki.ifindhs.com/')
    assert fetch(tmp_path, identity)['clips']


def test_ifindhs_supplements_special_events_without_overwriting_local(tmp_path):
    source = next(s for s in DEFAULT_SOURCES if s['id'] == 'ifindhs')
    items = [dict(id='a', name='VO_HERO_11aq_Trigger_DeathStrike_01', kind='voice', locale='zhcn',
                  event='专属触发 · 灵界打击', text='', transcript_dbfid=1, transcript_name='测试', transcript_cardid='HERO_11aq'),
             dict(id='b', name='VO_HERO_11aq_Attack_01', kind='voice', locale='zhcn', text='本地优先')]
    data = {**parse_page(wiki_html(), 'HERO_11aq'), 'source': 'https://wiki.ifindhs.com/', 'source_name': 'ifindhs'}
    with patch('pengpeng.transcript_sources.fetch_source', return_value=data):
        result = supplement(tmp_path, 1, items, providers=[source])
    assert [(r['id'], r['match_method']) for r in result['items']] == [('a', 'audio_key')]
    assert not any(s['enabled'] for s in migrate_sources([]))


def test_grouping_precedes_pagination_and_preserves_exact_id_and_favorites(tmp_path):
    s = Service(tmp_path)
    s.store = Store(tmp_path / 'test.db')
    db = s.store.db
    db.executescript('''CREATE TABLE card_groups(id TEXT PRIMARY KEY,group_key TEXT);
        CREATE TABLE card_filters(id TEXT,collectible INTEGER);
        CREATE TABLE card_editions(id TEXT,edition TEXT);''')
    for cid, name, group in [('A', '同名', 'one'), ('CORE_A', '同名', 'one'), ('B', '其他', 'two')]:
        db.execute('INSERT INTO cards VALUES (?,?,?,0,?,?,NULL)', (cid, name, name, '', '{}'))
        db.execute('INSERT INTO card_groups VALUES (?,?)', (cid, group))
        db.execute('INSERT INTO card_filters VALUES (?,1)', (cid,))
    db.execute("INSERT INTO card_editions VALUES ('A','edition:1')")
    assert s.list_cards(limit=1)['total'] == 2
    assert s.list_cards(limit=1)['items'][0]['id'] == 'CORE_A'
    assert s.list_cards(limit=1, offset=1)['items'][0]['id'] == 'B'
    assert s.list_cards(query='CORE_A')['items'][0]['id'] == 'CORE_A'
    assert s.list_cards(query='a')['items'][0]['id'] == 'A'
    assert s.list_cards(query='a')['total'] == 1
    assert s.list_cards(filters={'set': 'edition:1'})['items'][0]['id'] == 'A'
    s.favorite('card', 'A')
    assert s.list_cards(favorites=True)['items'][0]['id'] == 'A'
    assert s.list_cards()['items'][0]['version_count'] == 2
    s.store.close()


def test_portrait_alpha_is_material_data_and_angled_padding_is_removed():
    image = Image.new('RGBA', (100, 100), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.polygon([(50, 0), (99, 50), (50, 99), (0, 50)], fill=(20, 100, 200, 0))
    preview = portrait_preview(image)
    assert preview.mode == 'RGB'
    assert preview.width >= 35
    pixels = np.asarray(preview)
    assert not np.any(np.all(pixels > 245, axis=2))
    assert np.array_equal(pixels[preview.height//2, preview.width//2], [20, 100, 200])
    # 原始纹理保留 Alpha，不在预览时修改调用方对象。
    assert image.getpixel((50, 50))[3] == 0


def test_quality_render_cache_is_separate_and_rejects_html(tmp_path):
    s = Service(tmp_path)
    s.cache = tmp_path / 'cache'
    for variant, suffix in [(1, ''), (2, '_SIG'), (3, '_DIA')]:
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'\x1aE\xdf\xa3test')) as request:
            result = s.card_render('TEST_1', variant=variant)
            assert suffix + '.webm' in request.call_args.args[0].full_url
            assert result['media'] == 'video'
        with patch('urllib.request.urlopen', side_effect=AssertionError('不应再次联网')):
            assert s.card_render('TEST_1', variant=variant)['path'] == result['path']
    with patch('urllib.request.urlopen', return_value=io.BytesIO(b'<html>challenge</html>')):
        with pytest.raises(ValueError):
            s.card_render('OTHER_1', variant=1)
    assert not (s.cache / 'renders/zhcn/OTHER_1-1.webm').exists()
