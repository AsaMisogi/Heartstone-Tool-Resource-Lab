"""验证图鉴边界、全量排序与缺失日期，防止页面看似正确但跨页重复。"""
import json

import pytest

from pengpeng.releases import release_reference
from pengpeng.service import Service
from pengpeng.storage import Store


@pytest.fixture
def catalog(tmp_path):
    service = Service(tmp_path)
    service.store = Store(tmp_path / 'index.db')
    db = service.store.db
    for cid, name, ename, hero, release in [
        ('CORE_Z', '丙', 'Zulu', 0, '2020-01-01'),
        ('CARD_A', '甲', 'Alpha', 0, '2021-01-01'),
        ('CARD_B', '甲', 'Beta', 0, '2021-01-01'),
        ('CARD_UNKNOWN', '乙', 'Unknown', 0, None),
        ('HERO_01', '甲', 'Hero', 1, '2014-03-11'),
        ('HERO_SKIN', '甲', 'Skin', 1, None),
    ]:
        data = {'m_name': {'m_locValues': [ename] + [''] * 11 + [name]},
                'm_textInHand': {'m_locValues': ['共同文本']}, 'artist': '仅元数据'}
        db.execute('INSERT INTO cards VALUES (?,?,?,?,?,?,NULL)',
                   (cid, name, ename, hero, '', json.dumps(data, ensure_ascii=False)))
        db.execute('INSERT INTO card_releases VALUES (?,?,?)', (cid, release, '测试日期'))
    yield service
    service.store.close()


def ids(result):
    return [item['id'] for item in result['items']]


def test_search_and_favorites_never_cross_catalog(catalog):
    catalog.favorite('card', 'CARD_A')
    catalog.favorite('card', 'HERO_01')
    assert ids(catalog.list_cards(query='甲')) == ['CARD_A', 'CARD_B']
    assert ids(catalog.list_cards(query='甲', hero=True)) == ['HERO_01', 'HERO_SKIN']
    assert ids(catalog.list_cards(query='甲', favorites=True)) == ['CARD_A']
    assert ids(catalog.list_cards(query='甲', hero=True, favorites=True)) == ['HERO_01']
    assert catalog.list_cards(query='共同文本')['total'] == 4
    assert catalog.list_cards(query='仅元数据')['total'] == 0
    assert catalog.list_cards(query='%')['total'] == 0
    assert catalog.list_cards(query='CARD_')['total'] == 3


@pytest.mark.parametrize('hero', [False, True])
@pytest.mark.parametrize('sort', ['default', 'id', 'name', 'release'])
@pytest.mark.parametrize('descending', [False, True])
def test_sort_before_pagination_is_stable(catalog, hero, sort, descending):
    options = dict(hero=hero, sort=sort, descending=descending)
    whole = catalog.list_cards(**options)
    pages = [catalog.list_cards(offset=i, limit=1, **options)['items'][0]['id']
             for i in range(whole['total'])]
    assert pages == ids(whole)
    assert len(set(pages)) == len(pages)
    if sort != 'release':
        assert pages == ids(catalog.list_cards(**{**options, 'descending': not descending}))[::-1]


def test_name_uses_display_language_and_release_unknown_stays_last(catalog):
    assert ids(catalog.list_cards(sort='name', locale='enus')) == ['CARD_A', 'CARD_B', 'CARD_UNKNOWN', 'CORE_Z']
    assert ids(catalog.list_cards(sort='name', locale='zhcn')) == ['CORE_Z', 'CARD_UNKNOWN', 'CARD_A', 'CARD_B']
    for descending in (False, True):
        items = catalog.list_cards(sort='release', descending=descending)['items']
        assert items[-1]['id'] == 'CARD_UNKNOWN'
        dates = [x['release_date'] for x in items[:-1]]
        assert dates == sorted(dates, reverse=descending)
        assert ids(catalog.list_cards(hero=True, sort='release', descending=descending)) == ['HERO_01', 'HERO_SKIN']


def test_invalid_order_is_rejected(catalog):
    with pytest.raises(ValueError, match='排序'):
        catalog.list_cards(sort='id; DROP TABLE cards')
    with pytest.raises(ValueError, match='排序'):
        catalog.list_cards(descending='false')


def test_dates_do_not_invent_skin_or_core_release():
    assert release_reference('HERO_01abc', [1001], True) == (None, '日期未知')
    assert release_reference('CORE_X', [1637], False) == (None, '日期未知')
    assert release_reference('ICC_X', [1001], False) == ('2017-08-10', '系列上线日期（参考）')
    assert release_reference('HERO_01', [2], True)[0] == '2014-03-11'
