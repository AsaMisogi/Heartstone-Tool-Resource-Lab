"""预览裁切规则。此模块不修改导出的原画；只处理缩略图中的留白。"""
import numpy as np
from collections import deque
from PIL import Image


def crop_preview(image):
    """裁掉连续透明边及近乎纯白的外边，保留图片内部的白色绘画内容。

    部分炉石纹理把竖画填进方形白底，object-fit 无法去除像素里的白边。
    按整列 / 整行判断边缘而非逐像素抠图；限制裁切面积，避免异常纯色纹理
    被裁成几像素。只在预览调用，原始 PNG 的尺寸、内容和透明度保持不变。
    """
    if image.mode == 'RGBA' and image.getbbox():
        image = image.crop(image.getbbox())
    rgb = np.asarray(image.convert('RGB'))
    nonwhite = np.any(rgb < 246, axis=2)
    columns = np.flatnonzero(nonwhite.mean(axis=0) > .025)
    rows = np.flatnonzero(nonwhite.mean(axis=1) > .025)
    if len(columns) and len(rows):
        left, right = int(columns[0]), int(columns[-1]) + 1
        top, bottom = int(rows[0]), int(rows[-1]) + 1
        # 少量卡图使用倾斜原画，白底形成三角形外边；对边界取分位数，
        # 让预览矩形避开大部分白角，同时不进行原图抠图或写回。
        content = nonwhite[top:bottom, left:right]
        if content.size:
            starts = np.argmax(content, axis=1)
            ends = content.shape[1] - np.argmax(content[:, ::-1], axis=1)
            valid = content.any(axis=1)
            inset_left = int(np.quantile(starts[valid], .9))
            inset_right = int(np.quantile(ends[valid], .1))
            if inset_right - inset_left >= content.shape[1] * .75:
                right = left + inset_right
                left += inset_left
        if right - left >= image.width * .45 and bottom - top >= image.height * .45:
            image = image.crop((left, top, right, bottom))
    return image


def portrait_preview(image):
    """为方形卡片找最大的无外底色正方形；RGB 是绘画，Alpha 是游戏材质蒙版。

    外底色只接受角落连续的近纯色区域，不全图删除白色，避免误伤雪景。
    在 128px 掩码上做线性动态规划，能覆盖椭圆、斜边、横图和竖图。
    导出原纹理走独立路径，不会丢失供研究使用的 Alpha 通道。
    """
    image = image.convert('RGB')
    thumb = image.copy()
    thumb.thumbnail((128, 128), Image.Resampling.BILINEAR)
    pixels = np.asarray(thumb).astype(np.int16)
    h, w = pixels.shape[:2]
    outside = np.zeros((h, w), dtype=bool)
    for y, x in ((0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)):
        color = pixels[y, x]
        # 天空、盔甲等平滑绘画也可能占据角落。只有近白色才视为外底，
        # 不能把任意纯色洪泛后剔除，否则海盗的天空和阿尔萨斯的头部会消失。
        if np.min(color) < 240:
            continue
        patch = pixels[max(0, y-2):min(h, y+3), max(0, x-2):min(w, x+3)]
        if np.max(np.abs(patch - color)) > 18:
            continue
        candidate = np.max(np.abs(pixels - color), axis=2) < 22
        queue = deque([(y, x)])
        seen = np.zeros_like(outside)
        while queue:
            cy, cx = queue.popleft()
            if seen[cy, cx] or not candidate[cy, cx]:
                continue
            seen[cy, cx] = True
            for ny, nx in ((cy-1, cx), (cy+1, cx), (cy, cx-1), (cy, cx+1)):
                if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx]:
                    queue.append((ny, nx))
        if seen.sum() >= h*w*.025:
            outside |= seen
    valid = ~outside
    sizes = np.zeros((h+1, w+1), dtype=int)
    best = (0, 0, 0)
    distance = float('inf')
    for y in range(h):
        for x in range(w):
            if valid[y, x]:
                size = 1 + min(sizes[y, x], sizes[y, x+1], sizes[y+1, x])
                sizes[y+1, x+1] = size
                center = (x+1-size/2-w/2)**2 + (y+1-size/2-h/2)**2
                if size > best[0] or (size == best[0] and center < distance):
                    best, distance = (size, x+1, y+1), center
    size, x, y = best
    if size < min(w, h)*.35:
        return image  # 无可靠绘画区域时保留画面，由组件居中 cover。
    # 两个缩放因子保留原图坐标；内缩一像素防止抗锯齿残留。
    inset = 1 if outside.any() and size > 4 else 0
    return image.crop((round((x-size+inset)*image.width/w), round((y-size+inset)*image.height/h),
                       round((x-inset)*image.width/w), round((y-inset)*image.height/h)))
