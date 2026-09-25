"""客户端快照比较。版本依据本地清单，不把工具升级误报为游戏更新。"""
import json
import sqlite3
from pathlib import Path

from .common import fingerprint, executable_version


def probe(workspace, root):
    root = Path(root).resolve()
    paths = [root / 'Data/Win/asset_manifest.unity3d', root / 'Data/Win/dbf.unity3d']
    if not all(p.is_file() for p in paths):
        return {'available': False, 'changed': False}
    paths += list((root / 'Data/Win').glob('asset_manifest_*.unity3d'))
    stamp = fingerprint(paths)
    receipt = workspace / 'game-snapshot.json'
    previous = json.loads(receipt.read_text('utf8')) if receipt.exists() else {}
    # 首次升级工具时，用已有缓存建立基线；新安装不把所有卡牌都标成 NEW。
    settings_path = workspace / 'settings.json'
    saved_root = json.loads(settings_path.read_text('utf8')).get('game_path','') if settings_path.exists() else ''
    if not previous and str(Path(saved_root).resolve()).casefold() == str(root).casefold() and not (workspace / 'cache' / stamp / 'index.sqlite3').exists():
        candidates = sorted((workspace / 'cache').glob('*/index.sqlite3'), key=lambda p:p.stat().st_mtime, reverse=True)
        if candidates:
            previous = {'fingerprint':candidates[0].parent.name, 'root':str(root)}
    same_root = previous.get('root', '').casefold() == str(root).casefold()
    return {'available':True, 'changed':bool(same_root and previous.get('fingerprint') != stamp),
            'fingerprint':stamp, 'root':str(root), 'game_version':executable_version(root / 'Hearthstone.exe'),
            'previous':previous.get('fingerprint', '') if same_root else '',
            'previous_version':previous.get('game_version', '') if same_root else ''}


def previous_db(workspace, stamp):
    """只读打开旧快照，不将其迁移或修改；指纹来自本工具保存的清单。"""
    if not stamp or not all(c in '0123456789abcdef' for c in stamp):
        return None
    path = workspace / 'cache' / stamp / 'index.sqlite3'
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) if path.exists() else None


def analyze(service, report):
    if report.get('changed'):
        service.store.set_meta('previous_snapshot', report['previous'])
        service.store.set_meta('update_index_pending', True)
        old = previous_db(service.workspace, report['previous'])
        if old:
            try:
                ids = {r[0] for r in old.execute('SELECT id FROM cards')}
                added = [(r[0],) for r in service.store.db.execute('SELECT id FROM cards') if r[0] not in ids]
                with service.store.db:
                    service.store.db.execute('DELETE FROM new_content')
                    service.store.db.executemany("INSERT INTO new_content VALUES ('card',?)", added)
                    # 新快照沿用收藏。资源的路径 ID 可能重排，音频收藏在完整
                    # 扫描后按稳定的名称/语言/类型重新匹配。
                    service.store.db.executemany("INSERT OR IGNORE INTO favorites VALUES ('card',?)",
                        [(r[0],) for r in old.execute("SELECT id FROM favorites WHERE kind='card'") if r[0] in ids])
            finally:
                old.close()
    receipt = service.workspace / 'game-snapshot.json'
    temp = receipt.with_suffix('.tmp')
    temp.write_text(json.dumps({k:report[k] for k in ('root','fingerprint','game_version')}, ensure_ascii=False), 'utf8')
    temp.replace(receipt)


def compare_assets(service):
    """扫描全部成功后发布新增资源；路径 ID 可能重排，按资源名/类型/语言比较。

    旧索引未完整扫描时不推断资源新增，避免把以前未发现的素材标为本次更新。
    暂停、失败不清掉待比较状态，恢复扫描后再提交结果。
    """
    if not service.store.get_meta('update_index_pending'):
        return
    if service.store.db.execute("SELECT 1 FROM bundles WHERE error<>''").fetchone():
        return
    old = previous_db(service.workspace, service.store.get_meta('previous_snapshot'))
    comparable = False
    if old:
        try:
            comparable = bool(old.execute("SELECT 1 FROM meta WHERE key='last_scan'").fetchone()) and not old.execute("SELECT 1 FROM bundles WHERE error<>''").fetchone()
            if comparable:
                known = set(old.execute('SELECT kind,locale,name FROM assets'))
                added = [(r['id'],) for r in service.store.db.execute('SELECT id,kind,locale,name FROM assets')
                         if (r['kind'],r['locale'],r['name']) not in known]
                with service.store.db:
                    service.store.db.execute("DELETE FROM new_content WHERE kind='asset'")
                    service.store.db.executemany("INSERT INTO new_content VALUES ('asset',?)", added)
            favorite_keys = set(old.execute("SELECT a.kind,a.locale,a.name FROM assets a JOIN favorites f ON f.id=a.id WHERE f.kind='asset'"))
            with service.store.db:
                service.store.db.executemany("INSERT OR IGNORE INTO favorites VALUES ('asset',?)",
                    [(r['id'],) for r in service.store.db.execute('SELECT id,kind,locale,name FROM assets')
                     if (r['kind'],r['locale'],r['name']) in favorite_keys])
        finally:
            old.close()
    service.store.set_meta('asset_comparison_available', comparable)
    service.store.set_meta('update_index_pending', False)
