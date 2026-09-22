"""界面状态只保存可重放的查询条件与图鉴布局，不保存媒体、DOM 或临时选择。"""
from .common import LOCALES


def validate_views(value):
    if not isinstance(value, dict):
        raise ValueError('页面筛选必须为对象')
    result = {}
    for view in ('cards', 'heroes', 'audio', 'effects'):
        item = value.get(view)
        if not isinstance(item, dict):
            continue
        filters = item.get('filters', {})
        order = item.get('order', {})
        result[view] = {
            'query': str(item.get('query', ''))[:2000],
            'locale': item.get('locale') if item.get('locale') in LOCALES else 'zhcn',
            'category': str(item.get('category', ''))[:100],
            'favorites': item.get('favorites') is True,
            'filters': {k: str(v)[:100] for k, v in filters.items()
                        if k in {'set', 'format', 'class', 'rarity', 'cost', 'type',
                                 'hero_group', 'collectible', 'battlegrounds'} and isinstance(v, (str, int))}
                        if isinstance(filters, dict) else {},
            'order': {'sort': order.get('sort') if isinstance(order, dict) and order.get('sort') in
                      ('default', 'name', 'id', 'release') else 'default',
                      'descending': isinstance(order, dict) and order.get('descending') is True}}
        if view in ('cards', 'heroes'):
            # 旧工作区没有 display 时沿用默认；只允许已知模式和有限尺寸。
            display = item.get('display')
            display = display if isinstance(display, dict) else {}
            size = display.get('size')
            result[view]['display'] = {
                'mode': 'list' if display.get('mode') == 'list' else 'grid',
                'size': max(180, min(300, size)) if type(size) is int else 220}
    return result
