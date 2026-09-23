"""真实客户端性能验收；独立工作区不污染用户缓存。运行时请关闭其他重负载任务。"""
from pathlib import Path
import argparse
import json
import sys
import time
import warnings
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pengpeng.service import Service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game', default='F:/Games/Hearthstone')
    parser.add_argument('--workspace', type=Path, default=Path('.cache/benchmark-resources'))
    parser.add_argument('--scan', action='store_true')
    args = parser.parse_args()
    warnings.filterwarnings('ignore', module='UnityPy')
    service = Service(args.workspace)
    results = {}
    try:
        start = time.perf_counter()
        service.initialize(args.game)
        results['initialize_seconds'] = time.perf_counter() - start
        results['cards'] = []
        for cid in ('EX1_116', 'HERO_01', 'HERO_01a', 'HERO_01bn'):
            start = time.perf_counter()
            data = service.card_audio(cid)
            item = {'card': cid, 'seconds': time.perf_counter()-start,
                    'items': len(data['items']), 'errors': data['errors']}
            if data['items']:
                start = time.perf_counter()
                audio = service.audio(assetid=data['items'][0]['id'])
                item.update(decode_seconds=time.perf_counter()-start, samples=len(audio['samples']))
            results['cards'].append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
        if args.scan:
            start = time.perf_counter()
            for progress in service.scan():
                if progress['done'] % 500 == 0:
                    print(progress['done'], '/', progress['total'], flush=True)
            results.update(scan_seconds=time.perf_counter()-start,
                           indexed=service.status()['indexed'], failed=service.status()['failed'])
        print(json.dumps(results, ensure_ascii=False, indent=2))
        (args.workspace / 'benchmark.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf-8')
    finally:
        if service.store:
            service.store.close()


if __name__ == '__main__':
    main()
