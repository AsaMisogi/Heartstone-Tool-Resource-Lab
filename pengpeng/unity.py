"""本地 Unity 读取层。

沿用 Hermes 的 cards_map → CardDef → GUID catalog 思路，重新实现依赖解析和
有界资源包缓存。这里不加载 / 执行游戏程序集，不向游戏目录写入任何文件。
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict, deque
from pathlib import Path

import UnityPy

from .common import guid, references
from .bundle import BundleDirectory, UnsupportedBundle

log = logging.getLogger(__name__)


class UnityReader:
    def __init__(self, root: Path, store):
        self.root = root / 'Data' / 'Win'
        self.store = store
        self.cache = OrderedDict()
        self.cabs = {r['name']: r['bundle'] for r in store.db.execute('SELECT * FROM cabs')}
        self.file_bundle = {}
        self.directories = {}
        UnityPy.config.FALLBACK_UNITY_VERSION = '6000.3.11f1'
        env = self.load('asset_manifest.unity3d')
        def read(suffix):
            return next(v.read_typetree() for k, v in env.container.items() if k.lower().endswith(suffix))
        catalog = read('/base_assets_catalog.asset')
        self.catalog = {a['guid']: catalog['m_bundleNames'][a['bundleId']] for a in catalog['m_assets']}
        mapping = read('/cards_map.asset')['map']
        self.cards = {k: guid(v) for k, v in zip(mapping['keys'], mapping['values'])}
        data = read('/bundle_deps.asset')
        names = data['allBundleNames']
        self.dependencies = {name: [names[i] for i in deps['allDependencies'] if names[i] != name]
                             for name, deps in zip(names, data['bundles'])}
        self.locales = {}
        for path in self.root.glob('asset_manifest_*.unity3d'):
            locale = path.stem.removeprefix('asset_manifest_').lower()
            local = self.load(path.name)
            data = next(v.read_typetree() for k, v in local.container.items() if 'asset_catalog_locale_' in k)
            self.locales[locale] = {a['baseGuid']: (a['guid'], data['m_bundleNames'][a['bundleId']])
                                    for a in data['m_assets']}
        log.info('资源清单：%s 张卡牌，%s 项基础资源，语言包 %s', len(self.cards), len(self.catalog), list(self.locales))

    def load(self, bundle):
        if bundle in self.cache:
            self.cache.move_to_end(bundle)
            return self.cache[bundle]
        base = self.root.parent.parent / 'Hearthstone_Data' if bundle.startswith('@player/') else self.root
        filename = bundle.removeprefix('@player/')
        path = (base / filename).resolve()
        if path.parent != base.resolve():
            raise ValueError('资源包路径必须位于 Data/Win 内')
        if not path.is_file():
            raise FileNotFoundError(f'本地未安装资源包：{bundle}')
        log.debug('读取资源包 %s (%.1f MB)', bundle, path.stat().st_size / 1048576)
        try:
            directory = self.directory(bundle, path)
            env = directory.environment()
            # 目录常驻，解压块只属于最近加载的环境，避免全索引缓存所有正文。
            directory.cache.clear()
        except UnsupportedBundle:
            env = UnityPy.load(str(path))
        def register(file):
            if isinstance(file, UnityPy.files.SerializedFile):
                name = Path(file.name).name.lower()
                self.cabs[name] = bundle
                self.file_bundle[name] = bundle
                return
            for sub in getattr(file, 'files', {}).values():
                register(sub)
        for file in env.files.values():
            register(file)
        self.cache[bundle] = env
        # 复杂皮肤会交错访问多个预制体。按需读取后可保留更大的元数据热集合，
        # 同时限制序列化数据总量；旧格式整包加载仍按大包计费、尽快淘汰。
        def weight(name):
            record = self.directories.get(name)
            return sum(size for _, _, size, flags in record[1].nodes if flags & 4) if record else 64 * 1024 * 1024
        while len(self.cache) > 1 and (len(self.cache) > 24 or sum(weight(name) for name in self.cache) > 64 * 1024 * 1024):
            old, _ = self.cache.popitem(last=False)
            if old in self.directories:
                self.directories[old][1].cache.clear()
        return env

    def directory(self, bundle, path=None):
        """只读压缩目录定位 CAB，首次找引用不再试着解压每个依赖包。"""
        path = path or self.root / bundle
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        previous = self.directories.get(bundle)
        if previous is None or previous[0] != stamp:
            directory = BundleDirectory(path)
            self.directories[bundle] = (stamp, directory)
            for name, _, _, flags in directory.nodes:
                if flags & 4:
                    self.cabs[Path(name).name.lower()] = bundle
        return self.directories[bundle][1]

    def source_paths(self):
        """同时收录 AssetBundle 与 Unity Player 内置资源，避免遗漏界面音效。"""
        files = {p.name: p for p in self.root.glob('*.unity3d')}
        player = self.root.parent.parent / 'Hearthstone_Data'
        for path in player.iterdir():
            if path.is_file() and (path.suffix == '.assets' or path.name == 'globalgamemanagers'
                                    or re.fullmatch(r'level\d+', path.name)):
                files['@player/' + path.name] = path
        return files

    def resolve(self, ref, locale='zhcn'):
        key = guid(ref)
        bundle = self.catalog.get(key)
        actual_locale = 'global'
        if key in self.locales.get(locale, {}):
            key, bundle = self.locales[locale][key]
            actual_locale = locale
        if not bundle:
            raise KeyError(f'清单中没有资源：{ref}')
        env = self.load(bundle)
        if key not in env.container:
            raise KeyError(f'资源包缺少 GUID：{key} ({bundle})')
        return env.container[key].deref(), bundle, actual_locale

    def pointer(self, obj, ptr):
        """解析 PPtr，按 manifest 的 bundle_deps 查找外部 CAB，不按 pathID 猜测。"""
        pathid = ptr.get('m_PathID', 0)
        if not pathid:
            return None
        file = obj.assets_file
        fileid = ptr.get('m_FileID', 0)
        if fileid == 0:
            return file.objects.get(pathid)
        target = file.externals[fileid - 1].path.rsplit('/', 1)[-1].lower()
        # Unity 内建对象不在 AssetBundle 依赖清单中；扫描全部依赖也找不到。
        # 尤其默认粒子网格会反复命中这里，过去每次都解压大量无关资源包。
        if target in ('unity default resources', 'unity_builtin_extra'):
            raise FileNotFoundError(f'Unity 内建资源不在客户端资源包中：{target}')
        player_external = self.root.parent.parent / 'Hearthstone_Data' / target
        if target not in self.cabs and player_external.is_file() and player_external.suffix == '.assets':
            self.load('@player/' + target)
        if target not in self.cabs:
            owner = self.file_bundle.get(file.name.lower(), self.cabs.get(file.name.lower()))
            for dependency in self.dependencies.get(owner, []):
                if (self.root / dependency).exists():
                    try:
                        self.directory(dependency)
                    except UnsupportedBundle:
                        self.load(dependency)
                if target in self.cabs:
                    break
        if target not in self.cabs:
            raise FileNotFoundError(f'外部资源未安装或无法定位：{target}')
        env = self.load(self.cabs[target])
        # CAB + pathID 已是精确地址，不再为每条引用枚举全包对象。
        candidate_file = env.get_cab(target)
        if candidate_file is not None and hasattr(candidate_file, 'objects'):
            candidate = candidate_file.objects.get(pathid)
            if candidate is not None:
                return candidate
        raise KeyError(f'CAB 中没有对象 {pathid}: {target}')

    def definition(self, cardid):
        obj, _, _ = self.resolve(self.cards[cardid], 'global')
        tree = obj.read_typetree()
        for component in tree.get('m_Component', []):
            child = self.pointer(obj, component['component'])
            if child and child.type.name == 'MonoBehaviour':
                data = child.read_typetree()
                if 'm_PortraitTexturePath' in data:
                    return data
        raise ValueError(f'{cardid} 没有可识别的 CardDef')

    def walk(self, root, locale='zhcn', max_nodes=4000, errors=None, include_visual=False):
        """遍历一个预制体的真实引用图，跳过脚本及父指针，避免走进其他预制体。

        返回 (对象, 类型树)。只遍历组件、子层级和资源引用；纹理/音频是叶节点。
        遇到缺失引用记录日志；调用方可把错误显示在界面，不用空数据冒充成功。
        """
        queue = deque([root])
        seen = set()
        while queue:
            obj = queue.popleft()
            key = (obj.assets_file.name, obj.path_id)
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > max_nodes:
                raise ValueError(f'资源引用超过 {max_nodes} 个对象，未完成遍历')
            kind = obj.type.name
            if not include_visual and kind in ('ParticleSystem', 'ParticleSystemRenderer', 'MeshRenderer',
                                              'SkinnedMeshRenderer', 'MeshFilter', 'Animation', 'Animator', 'Light'):
                continue
            if kind in ('MonoScript', 'Shader', 'Mesh', 'AnimationClip', 'Texture2D', 'AudioClip', 'Sprite', 'Material'):
                yield obj, None
                continue
            tree = obj.read_typetree()
            yield obj, tree
            # 二维预览只消费粒子参数；材质纹理由 Service 定向读取。
            # 不追踪形状网格、碰撞、渲染器探针等不参与当前预览的引用。
            if include_visual and kind in ('ParticleSystem', 'ParticleSystemRenderer'):
                continue
            def visit(value, field=''):
                if field in ('m_Script', 'm_Father', 'm_Shader'):
                    return
                if not include_visual and field in ('m_Materials', 'm_Mesh', 'm_Texture'):
                    return
                if isinstance(value, dict):
                    if 'm_PathID' in value:
                        if value['m_PathID']:
                            try:
                                child = self.pointer(obj, value)
                                if child:
                                    queue.append(child)
                            except (KeyError, ValueError, FileNotFoundError) as exc:
                                if errors is not None:
                                    errors.append(str(exc))
                    else:
                        for k, v in value.items():
                            visit(v, k)
                elif isinstance(value, (list, tuple)):
                    for v in value:
                        visit(v, field)
                elif isinstance(value, str) and guid(value):
                    if not include_visual and value.split(':')[0].lower().endswith(('.mat', '.psd', '.tif', '.tga', '.png', '.jpg', '.fbx', '.anim')):
                        return
                    try:
                        child, _, _ = self.resolve(value, locale)
                        queue.append(child)
                    except (KeyError, ValueError, FileNotFoundError) as exc:
                        if errors is not None:
                            errors.append(str(exc))
            # 视觉预览需要纹理；语音图无需为了材质加载数百 MB 的图片。
            visit(tree)

    def material_textures(self, reference, locale='zhcn'):
        obj, _, _ = self.resolve(reference, locale)
        tree = obj.read_typetree()
        textures = []
        errors = []
        for name, value in tree.get('m_SavedProperties', {}).get('m_TexEnvs', []):
            pointer = value.get('m_Texture', {})
            if pointer.get('m_PathID'):
                try:
                    child = self.pointer(obj, pointer)
                    if child and child.type.name == 'Texture2D':
                        textures.append((name, child))
                except (KeyError, ValueError, FileNotFoundError) as exc:
                    log.warning('材质纹理 %s: %s', name, exc)
                    errors.append(f'材质纹理 {name}: {exc}')
        return textures, tree, errors
