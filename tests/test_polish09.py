"""0.9：时间轴采样位置、未知事件隔离、音乐查询与滚轮有限动量。"""
import json
import numpy as np
import pytest
import soundfile as sf
from pengpeng.audio_mix import mix_samples
from pengpeng.audio_timing import card_timing, fsm_audio_actions
from pengpeng.audio_library import labels, update_labels
from pengpeng.service import Service
from pengpeng.storage import Store


def test_mixer_event_offsets_and_leading_track(tmp_path):
    main, tail, out = (tmp_path / n for n in ('voice.wav','music.wav','mix.wav'))
    sf.write(main, np.ones(8000)*.1, 8000)
    sf.write(tail, np.ones(16000)*.2, 16000)
    report = mix_samples(main,[{'path':tail,'delay':0}],out,main_delay=.75)
    data, rate = sf.read(out)
    assert rate == 16000 and len(data) == 28000
    assert data[4000] == pytest.approx(.2, abs=.001)
    assert data[13000] == pytest.approx(.3, abs=.001)
    assert data[20000] == pytest.approx(.1, abs=.001)
    assert report['start_seconds'] == [.75,0]
    with pytest.raises(ValueError):
        mix_samples(main, [], out, main_delay=float('nan'))


def test_timing_does_not_fabricate_missing_delay():
    assert card_timing({}) is None
    assert card_timing({'m_DelaySec':-.1}) is None
    assert card_timing({'m_DelaySec':.75})['seconds'] == .75
    import struct
    tree={'fsm':{'states':[{'name':'Shield', 'actionData':{
        'actionNames':['HutongGames.PlayMaker.Actions.AudioPlaythroughAction'],
        'actionStartIndex':[0], 'actionEnabled':[1], 'paramName':['m_Delay'],
        'paramDataType':[2], 'paramDataPos':[0], 'byteData':list(struct.pack('<f', .25))}}]}}
    params, evidence = next(fsm_audio_actions(tree))
    assert params['delay'] == .25 and not evidence['resolved']
    assert evidence['anchor'] == 'state_entry'


def test_audio_plan_overlays_unresolved_runtime_sound(tmp_path):
    s=Service(tmp_path)
    known={'resolved':True,'anchor':'card_event','seconds':.5}
    s.card_audio=lambda *a:{'items':[
        {'id':'voice','kind':'voice','group':'play','timing':known},
        {'id':'music','name':'Music','kind':'sound','group':'play','timing':known},
        {'id':'shield','name':'Shield','kind':'sound','group':'play','timing':{'resolved':False}},
        {'id':'other','name':'Death','kind':'sound','group':'death','timing':known}]}
    plan=s.audio_plan('CARD','voice',paired_audio=True)
    assert [t['id'] for t in plan['tracks']]==['music', 'shield']
    assert plan['skipped']==[]
    assert plan['tracks'][1]['delay']==.5
    assert plan['tracks'][1]['placement']=='overlay'
    assert plan['main_delay']==.5


def test_music_scene_search_and_category_filter(tmp_path):
    s=Service(tmp_path);s.store=Store(tmp_path/'db')
    rows=[('1','musicexpansion_base_global-audio-0.unity3d','1','','ICC_Menu_Music','AudioClip','背景音乐','global',60),
          ('2','heromusic_base_global-audio-0.unity3d','2','','HeroMusic_Arthas','AudioClip','背景音乐','global',60)]
    with s.store.db:
        s.store.db.executemany('INSERT INTO assets VALUES (?,?,?,?,?,?,?,?,?)',rows)
        update_labels(s.store, rows)
    assert s.list_assets(query='冰封王座')['total']==1
    result=s.list_assets(category='背景音乐',subgroup='英雄主题音乐')
    assert result['total']==1 and len(result['subgroups'])==2
    assert result['items'][0]['annotation']=='英雄主题音乐'
    assert not result['music_indexed']
    s.store.close()


def test_scene_labels_keep_purpose():
    assert labels('find_opponent_search_music_loop_01','initial','背景音乐')['subgroup']=='匹配 / 起手 / 结算'
    assert labels('ETC_Store_Music','musicexpansion','背景音乐')['annotation']=='音乐节 · 商店'
    assert labels('Board_UNG_amb_loop','board','音效')['subgroup']=='棋盘 / 安戈洛'
