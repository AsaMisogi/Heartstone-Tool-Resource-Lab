"""SQLite 索引：每个资源包独立事务，取消 / 退出后可继续扫描。"""
import json
import sqlite3
from pathlib import Path

SCHEMA = 1


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY, name TEXT, ename TEXT, hero INTEGER,
                guid TEXT, data TEXT NOT NULL, definition TEXT);
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, bundle TEXT, pathid TEXT, guid TEXT,
                name TEXT, kind TEXT, category TEXT, locale TEXT, duration REAL);
            CREATE INDEX IF NOT EXISTS asset_kind ON assets(kind, locale, category);
            CREATE INDEX IF NOT EXISTS asset_bundle ON assets(bundle);
            CREATE TABLE IF NOT EXISTS audio_labels (id TEXT PRIMARY KEY, subgroup TEXT, annotation TEXT);
            CREATE INDEX IF NOT EXISTS audio_subgroup ON audio_labels(subgroup);
            CREATE TABLE IF NOT EXISTS bundles (name TEXT PRIMARY KEY, stamp TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS favorites (kind TEXT, id TEXT, PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS new_content (kind TEXT,id TEXT,PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS cabs (name TEXT PRIMARY KEY, bundle TEXT);
            CREATE INDEX IF NOT EXISTS card_scope ON cards(hero,id);
            CREATE INDEX IF NOT EXISTS card_default_order ON cards(
                hero, CASE WHEN id LIKE 'CORE_%' THEN 0 WHEN id LIKE 'HERO_%' THEN 1 ELSE 2 END, id);
            CREATE TABLE IF NOT EXISTS card_releases (
                id TEXT PRIMARY KEY, release_date TEXT, source TEXT NOT NULL);
        ''')

    def get_meta(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()

    def close(self):
        self.db.close()
