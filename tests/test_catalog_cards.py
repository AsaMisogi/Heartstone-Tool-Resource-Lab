"""摘要查询回归：零值、多职业/种族、缺失数据与固定查询次数。"""
import json

from pengpeng.card_details import catalog_summaries
from pengpeng.storage import Store


def test_page_summaries_preserve_values_without_per_card_queries(tmp_path):
    store = Store(tmp_path / 'index.db')
    db = store.db
    db.executescript('''
      CREATE TABLE card_details (id TEXT PRIMARY KEY, data TEXT);
      CREATE TABLE card_filters (id TEXT PRIMARY KEY, rarity INT, card_type INT, cost INT, bg INT);
      CREATE TABLE card_classes (id TEXT, class_id INT);
      CREATE TABLE card_sets (id TEXT, set_id INT);
      INSERT INTO card_filters VALUES ('A',5,4,0,0),('B',0,3,0,1),('C',3,7,2,0);
      INSERT INTO card_classes VALUES ('A',4),('A',7),('B',12),('C',999);
      INSERT INTO card_sets VALUES ('A',1637),('A',9999);
    ''')
    raw = {'attack': 0, 'health': 1, 'races': [14, 24, 999]}
    db.execute('INSERT INTO card_details VALUES (?,?)', ('A', json.dumps(raw)))
    db.execute('INSERT INTO card_details VALUES (?,?)', ('C', json.dumps({'attack': 2, 'durability': 3})))
    statements = []
    db.set_trace_callback(statements.append)
    one = catalog_summaries(store, ['A'])
    one_count = len(statements)
    statements.clear()
    many = catalog_summaries(store, ['A', 'B', 'C', 'missing'])
    assert len(statements) == one_count  # 数量随页大小保持固定，不为每张卡重复查询。
    assert many['A'] == one['A']
    assert many['A']['cost'] == many['A']['attack'] == 0
    assert many['A']['classes'] == ['法师', '潜行者']
    assert many['A']['races'] == ['鱼人', '龙', '种族 999']
    assert many['A']['sets'] == ['系列 1637', '系列 9999']
    assert many['B']['battlegrounds'] and many['B']['health'] is None
    assert many['C']['durability'] == 3 and many['C']['health'] is None
    assert many['C']['classes'] == ['职业 999']
    assert 'missing' not in many
    store.close()


def test_summary_without_upgraded_index(tmp_path):
    store = Store(tmp_path / 'index.db')
    assert catalog_summaries(store, []) == {}
    assert catalog_summaries(store, ['OLD']) == {}
    store.close()
