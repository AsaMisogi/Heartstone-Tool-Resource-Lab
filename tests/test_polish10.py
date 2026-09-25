"""0.10 回归：卡面词条、四大声音分类与未知音轨的叠放约定。"""
import json

import pytest

from pengpeng.audio_library import labels
from pengpeng.catalog import has_immune_keyword, update_immune_keywords
from pengpeng.common import audio_category
from pengpeng.service import Service
from pengpeng.storage import Store


def card_text(text, flavor=''):
    return {'m_textInHand': {'m_locValues': [''] * 12 + [text]},
            'm_flavorText': {'m_locValues': [''] * 12 + [flavor]}}


@pytest.mark.parametrize('text', [
    '<b>免疫</b>', '你的英雄在攻击时<b>免疫</b>。',
    '使一个随从获得<b>免疫</b>。', '<b>快枪：</b>在本回合中获得<b>免疫</b>。',
])
def test_immune_includes_conditional_and_granted_keyword(text):
    assert has_immune_keyword(card_text(text))


def test_immune_migration_removes_hidden_tag_and_ignores_flavor(tmp_path):
    store = Store(tmp_path / 'index.db')
    store.db.execute('CREATE TABLE card_keywords(id TEXT,keyword TEXT,PRIMARY KEY(id,keyword))')
    for cid, data in [('hidden', card_text('无法攻击。', '我对吐槽免疫')),
                      ('grant', card_text('你的英雄在攻击时<b>免疫</b>。'))]:
        store.db.execute('INSERT INTO cards VALUES (?,?,?,0,?,?,NULL)',
                         (cid, cid, cid, '', json.dumps(data)))
    store.db.execute("INSERT INTO card_keywords VALUES ('hidden','免疫')")
    store.db.execute("INSERT INTO card_keywords VALUES ('hidden','嘲讽')")
    update_immune_keywords(store)
    update_immune_keywords(store)
    assert [tuple(r) for r in store.db.execute('SELECT * FROM card_keywords ORDER BY id')] == [
        ('grant', '免疫'), ('hidden', '嘲讽')]
    store.close()


@pytest.mark.parametrize('name,bundle,duration,category', [
    ('MusicBox_Impact', 'musicexpansion_global', 2, '音效'),
    ('UI_Button', 'heromusic_base_global-audio', 1, '音效'),
    ('Forest_AMB_Loop', 'musicexpansion_base_global', 120, '音效'),
    ('Mushroom_poke_1', 'initial', 2, '音效'),
    ('VO_HERO_Attack', 'heromusic_base_global', 2, '角色语音'),
    ('Unknown_Long_Loop', 'initial', 240, '音效'),
    ('Main_Title', 'essential', 136, '背景音乐'),
    ('Duel', 'essential', 191, '背景音乐'),
    ('Better Hand', 'essential', 234, '背景音乐'),
    ('Collection Manager', 'initial', 191, '背景音乐'),
    ('HeroMusic_Arthas', 'initial', 96, '背景音乐'),
    ('HS_LegendaryStinger_Antonidas', 'essential', 8, '短音乐 / 登场曲'),
    ('ETC_Menu_Music_Intro', 'initial', 12, '短音乐 / 登场曲'),
])
def test_audio_classification_uses_clip_evidence(name, bundle, duration, category):
    assert audio_category(name, bundle, duration) == category


def test_classic_music_has_discoverable_group():
    assert labels('Duel', 'essential', '背景音乐')['subgroup'] == '经典默认音乐'


def test_audio_plan_preserves_known_delay_without_known_main_and_deduplicates(tmp_path):
    service = Service(tmp_path)
    known = {'resolved': True, 'anchor': 'card_event', 'seconds': .25}
    sound = {'id': 's', 'name': 'sound', 'kind': 'sound', 'group': 'play', 'timing': known}
    unknown = {'id': 'u', 'name': 'unknown', 'kind': 'sound', 'group': 'play'}
    service.card_audio = lambda *a: {'items': [
        {'id': 'v', 'kind': 'voice', 'group': 'play'}, sound, unknown]}
    service.general_audio = lambda *a: {'items': [{**sound, 'timing': {'resolved': False}}, unknown], 'errors': []}
    plan = service.audio_plan('card', 'v', paired_audio=True, general_audio=True)
    assert [(t['id'], t['delay']) for t in plan['tracks']] == [('s', .25), ('u', 0)]
    assert plan['main_delay'] == 0 and plan['skipped'] == []
    assert service.audio_plan('card', 'v')['tracks'] == []
