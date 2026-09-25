"""多源行为回归：失败隔离、部分补齐、禁用与缓存身份，均不依赖外网。"""
import json
from unittest.mock import patch
import pytest
from pengpeng import transcripts
from pengpeng import transcript_sources as sources
from pengpeng.service import Service


def voice(event, text=''):
    return {'id': event, 'kind': 'voice', 'locale': 'zhcn', 'text': text,
            'event': f'm_{event}EffectDef.m_SoundSpellPaths[0]', 'transcript_name': '测试卡'}


def custom(**values):
    return {'id': 'custom_test', 'name': '测试源', 'kind': 'json', 'enabled': True,
            'url': 'https://example.org/{dbfid}?lang={locale}', **values}


def test_partial_and_failure_fallback(tmp_path):
    providers = [next(s for s in sources.DEFAULT_SOURCES if s['id']=='huiji'), next(s for s in sources.DEFAULT_SOURCES if s['id']=='baidu'), custom()]
    replies = [RuntimeError('offline'), {'quotes': {'Play': '登场'}, 'source': 'https://example.org/a', 'source_name': 'A'},
               {'quotes': {'Play': '不覆盖', 'Attack': '攻击'}, 'source': 'https://example.org/b', 'source_name': 'B'}]
    with patch.object(sources, 'fetch_source', side_effect=replies) as fetch:
        result = transcripts.supplement(tmp_path, 1, [voice('Play'), voice('Attack'), voice('Death','本地')], providers=providers)
    assert fetch.call_count == 3
    assert [(x['text'], x['source_name']) for x in result['items']] == [('登场','A'), ('攻击','B')]
    assert result['sources'] == [1]


def test_disabled_sources_and_complete_stop(tmp_path):
    with patch.object(sources, 'fetch_source') as fetch:
        assert not transcripts.supplement(tmp_path, 1, [voice('Play')], providers=[])['items']
        fetch.assert_not_called()
        fetch.return_value = {'quotes': {'Play':'台词'}, 'source':'https://example.org', 'source_name':'A'}
        transcripts.supplement(tmp_path, 1, [voice('Play')], providers=[{**next(s for s in sources.DEFAULT_SOURCES if s['id'] == 'baidu'), 'enabled':False},next(s for s in sources.DEFAULT_SOURCES if s['id'] == 'huiji'),custom()])
        assert fetch.call_count == 1 and fetch.call_args.args[1]['id'] == 'huiji'


@pytest.mark.parametrize('url', ['file:///tmp/{dbfid}', 'https://user:pass@site/{dbfid}', 'https://site/{oops}',
                                 'https://{name}/quotes', 'https://site/no-identity', 'https://site/{dbfid!r}'])
def test_bad_custom_url(url):
    with pytest.raises(ValueError): sources.validate_sources([custom(url=url)])


def test_settings_restart_and_failed_save(tmp_path):
    service = Service(tmp_path)
    configured = [custom(), {**next(s for s in sources.DEFAULT_SOURCES if s['id'] == 'baidu'), 'enabled': False}, next(s for s in sources.DEFAULT_SOURCES if s['id'] == 'huiji')]
    service.save_settings(transcript_sources=configured, general_audio=True)
    before = service.settings_path.read_bytes()
    with pytest.raises(ValueError): service.save_settings(transcript_sources=[custom(url='file://bad')])
    assert service.settings_path.read_bytes() == before
    again = Service(tmp_path)
    assert again.settings['transcript_sources'] == sources.validate_sources(configured)
    assert again.settings['general_audio'] is True


class Response:
    def __init__(self, value): self.value = json.dumps(value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.value[:limit]


def test_custom_identity_and_cache_endpoint(tmp_path):
    identity = {'dbfid':1,'cardid':'X_1','name':'测试卡','locale':'zhcn'}
    with patch.object(sources, 'urlopen', return_value=Response({'dbfid':2,'locale':'zhcn','quotes':{'Play':'错卡'}})):
        with pytest.raises(ValueError): sources.fetch_source(tmp_path, custom(), identity)
    with patch.object(sources, 'urlopen', return_value=Response({'dbfid':1,'locale':'zhcn','quotes':{'Play':'正确'}})) as fetch:
        sources.fetch_source(tmp_path, custom(), identity)
        sources.fetch_source(tmp_path, custom(), identity)
        assert fetch.call_count == 1
        sources.fetch_source(tmp_path, custom(url='https://example.org/new/{dbfid}'), identity)
        assert fetch.call_count == 2


def test_baidu_section_identity_and_ambiguity():
    html = '<meta property="og:title" content="测试卡"><meta name="description" content="炉石传说卡牌">'
    html += '<h2>卡牌信息</h2><li>攻击：卡牌描述</li><h2>卡牌语音</h2><ul><li>出场：登场文字<sup>[1]</sup></li><li>攻击：一</li><li>攻击：二</li></ul><h2>故事</h2><li>死亡：故事</li>'
    assert sources.parse_baidu(html, '测试卡') == {'Play':'登场文字'}
    assert sources.parse_baidu(html, '另一张卡') == {}
