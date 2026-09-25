"""0.8 关键回归：语音条件方向、对象图隔离、战棋与版本差异发布。"""
import json
from pathlib import Path
from types import SimpleNamespace

from pengpeng.storage import Store
from pengpeng.service import Service
from pengpeng.unity import UnityReader
from pengpeng.game_updates import analyze, compare_assets, probe
from pengpeng.voice_conditions import matches_owner, event_group, audible_effect, describe_condition
from pengpeng.card_relations import text_links


def test_voice_owner_is_source_hero_not_target_hero():
    condition = {'m_CardId':'HERO_01','m_SideToSearch':4}
    assert not matches_owner(condition, 'HERO_01', {})
    condition['m_SideToSearch'] = 3
    assert matches_owner(condition, 'HERO_01', {})
    assert not matches_owner(condition, 'HERO_01aa', {})
    assert matches_owner({'m_RequireTag':2839,'m_TagValue':56,'m_SideToSearch':3}, 'HERO_01aa', {'2839':56})


def test_hero_music_group_excludes_collection_and_phone_templates():
    assert event_group('m_CustomSummonSpellPath') == event_group('m_PlayEffectDef.m_SoundSpellPaths[0]')
    assert not audible_effect({'field':'m_StoreItemDisplayPath'})
    assert not audible_effect({'field':'m_CollectionHeroDefPath'})
    assert not audible_effect({'field':'m_SocketInEffectFriendlyPhone'})
    assert audible_effect({'field':'m_EmoteDefs[0].m_emoteSoundSpellPath'})


def test_sound_graph_follows_sounddef_but_not_other_condition(tmp_path):
    """模拟客户端 AudioSource + 同宿主 SoundDef，默认/条件共享父 Transform。"""
    objects = {}
    def obj(identity, kind, tree):
        value = SimpleNamespace(path_id=identity, assets_file=SimpleNamespace(name='cab'),
                                type=SimpleNamespace(name=kind), read_typetree=lambda:tree)
        objects[identity] = value
        return value
    ptr = lambda n:{'m_PathID':n,'m_FileID':0}
    obj(1,'GameObject',{'m_Component':[{'component':ptr(2)},{'component':ptr(3)}]})
    obj(2,'Transform',{'m_GameObject':ptr(1),'m_Children':[ptr(10),ptr(20)]})
    branch = {'m_CardId':'HERO_A','m_SideToSearch':3,'m_AudioSource':ptr(21)}
    obj(3,'MonoBehaviour',{'m_GameObject':ptr(1),'m_CardSoundData':{'m_AudioSource':ptr(11)},'m_CardSpecificVoDataList':[branch]})
    for n in (10,20):
        obj(n,'GameObject',{'m_Component':[{'component':ptr(n+1)},{'component':ptr(n+2)}]})
        obj(n+1,'AudioSource',{'m_GameObject':ptr(n)})
        obj(n+2,'MonoBehaviour',{'m_GameObject':ptr(n),'m_AudioClip':ptr(n+3)})
        obj(n+3,'AudioClip',{})
    reader = UnityReader.__new__(UnityReader)
    reader.pointer = lambda parent,p:objects.get(p.get('m_PathID'))
    audio = [(o.path_id,c) for o,t,c in reader.walk(objects[1],with_conditions=True) if o.type.name=='AudioClip']
    assert audio == [(13,None),(23,branch)]
    assert [o.path_id for o,t,c in reader.walk(objects[1],with_conditions=True,condition_match=lambda c:False)
            if o.type.name=='AudioClip'] == [13]


def test_voice_zone_is_hero_not_deck(tmp_path):
    store=Store(tmp_path/'db')
    store.db.execute("INSERT INTO cards VALUES ('H','英雄','',1,'','{}',NULL)")
    text, targets = describe_condition(store,{'m_CardId':'H','m_SideToSearch':3,'m_ZonesToSearch':[2]})
    assert '英雄' in text and '牌库' not in text and targets[0]['id']=='H'
    store.close()


def test_collection_links_do_not_pick_one_treasure():
    related=[{'id':f'LOEA16_{i}','name':name} for i,name in [(3,'能量之光'),(4,'恐怖丧钟'),(5,'末日镜像')]]
    links=text_links('LOE_092','发现一张强大的神器牌',related)
    assert links[0]['label']=='神器' and len(links[0]['cards'])==3
    assert text_links('X','召唤山熊宝宝',[{'id':'Y','name':'山熊宝宝'}])[0]['cards'][0]['id']=='Y'


def card(store, cid):
    store.db.execute('INSERT INTO cards VALUES (?,?,?,0,?,?,NULL)',(cid,cid,cid,'','{}'))


def asset(store, cid, name):
    store.db.execute("INSERT INTO assets VALUES (?, 'bundle','1','',?,'AudioClip','音乐','global',1)",(cid,name))


def snapshots(tmp_path, complete=True):
    old=Store(tmp_path/'cache/aaaa/index.sqlite3');card(old,'OLD');asset(old,'old-path-id','same')
    if complete:old.set_meta('last_scan','2026-09-24')
    old.db.commit();old.close()
    service=Service(tmp_path);service.store=Store(tmp_path/'cache/bbbb/index.sqlite3')
    card(service.store,'OLD');card(service.store,'NEW');asset(service.store,'new-path-id','same');asset(service.store,'added','added')
    service.store.db.commit()
    analyze(service,{'changed':True,'previous':'aaaa','root':str(tmp_path),'fingerprint':'bbbb','game_version':'2'})
    return service


def test_new_content_survives_restart_and_replaces_previous_update(tmp_path):
    s=snapshots(tmp_path)
    assert [r['id'] for r in s.list_cards(filters={'new':'1'})['items']]==['NEW']
    compare_assets(s)
    assert [r['id'] for r in s.list_assets(new=True)['items']]==['added']
    assert not s.store.get_meta('update_index_pending')
    analyze(s,{'changed':False,'root':str(tmp_path),'fingerprint':'bbbb','game_version':'2'})
    assert s.list_cards(filters={'new':'1'})['total']==1
    s.store.close()
    current=Store(tmp_path/'cache/cccc/index.sqlite3');card(current,'OLD');card(current,'NEW');card(current,'LATEST');current.db.commit()
    s.store=current
    analyze(s,{'changed':True,'previous':'bbbb','root':str(tmp_path),'fingerprint':'cccc','game_version':'3'})
    assert [r['id'] for r in s.list_cards(filters={'new':'1'})['items']]==['LATEST']
    current.close()


def test_incomplete_old_index_does_not_claim_old_assets_are_new(tmp_path):
    s=snapshots(tmp_path,complete=False);compare_assets(s)
    assert not s.list_assets(new=True)['items']
    assert s.store.get_meta('asset_comparison_available') is False
    s.store.close()


def test_failed_scan_keeps_comparison_pending(tmp_path):
    s=snapshots(tmp_path)
    s.store.db.execute("INSERT INTO bundles VALUES ('broken','1','读取失败')");s.store.db.commit()
    compare_assets(s)
    assert s.store.get_meta('update_index_pending')
    assert not s.list_assets(new=True)['items']
    s.store.close()


def test_initial_install_and_changed_manifest(tmp_path):
    root=tmp_path/'game';(root/'Data/Win').mkdir(parents=True)
    for name in ('asset_manifest','dbf'):(root/'Data/Win'/f'{name}.unity3d').write_bytes(b'initial')
    workspace=tmp_path/'workspace';workspace.mkdir()
    report=probe(workspace,root);assert not report['changed']
    (workspace/'game-snapshot.json').write_text(json.dumps(report),'utf8')
    (root/'Data/Win/dbf.unity3d').write_bytes(b'updated client')
    assert probe(workspace,root)['changed']
