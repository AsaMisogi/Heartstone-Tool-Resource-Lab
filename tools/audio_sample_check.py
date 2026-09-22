"""从真实索引按类别抽样解码，验证采样信息及 RIFF/WAVE，不声称验证全部音频。"""
from pathlib import Path
import json
import sys
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service

if __name__ == '__main__':
    service = Service(Path('workspace'))
    service.initialize()
    rows = []
    for category in service.list_assets()['categories']:
        rows.extend(service.store.db.execute(
            'SELECT id,name,category,locale FROM assets WHERE kind=? AND category=? AND duration BETWEEN 1 AND 25 ORDER BY name LIMIT 2',
            ('AudioClip', category)).fetchall())
    results = []
    for row in rows:
        try:
            samples = service.audio(assetid=row['id'])['samples']
            for sample in samples:
                with wave.open(sample['path']) as stream:
                    assert stream.getnframes() > 0 and stream.getsampwidth() == 2
            results.append({**dict(row), 'ok': True, 'samples': len(samples)})
        except Exception as exc:
            results.append({**dict(row), 'ok': False, 'error': str(exc)})
    Path('.cache/audio-check.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    sys.exit(0 if all(r['ok'] for r in results) else 1)
