"""开发调查：只读取已安装游戏的音频命名及声音组件，不执行游戏脚本。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service

if __name__ == '__main__':
    s = Service(Path('.cache/qa-05-workspace'))
    s.initialize('F:/Games/Hearthstone')
    out = Path('.cache/audio09'); out.mkdir(exist_ok=True)
    # 完整索引来自日常工作区，调查时以只读连接访问。
    import sqlite3
    db = sqlite3.connect(Path('workspace/cache/164e170a2a7138320324/index.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    rows = db.execute("SELECT name,bundle,category,locale,duration FROM assets WHERE kind='AudioClip'").fetchall()
    (out/'names.json').write_text(json.dumps(rows,ensure_ascii=False,indent=1),'utf8')
    for cid in ('EX1_116','BOT_021','ICC_829','ICC_830','ONY_005','BOT_548'):
        card=s.card(cid); result=[]
        for effect in card['effects']:
            if not any(x in effect['field'] for x in ('PlayEffect','Summon')): continue
            root=s.reader.resolve(effect['ref'])[0]
            for obj, tree in s.reader.walk(root, include_visual=True):
                if tree and obj.type.name in ('MonoBehaviour','AudioSource','Animation'):
                    result.append({'effect':effect,'id':obj.path_id,'type':obj.type.name,'tree':tree})
        (out/(cid+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=1),'utf8')
        print(cid,len(result),flush=True)
    s.store.close()
