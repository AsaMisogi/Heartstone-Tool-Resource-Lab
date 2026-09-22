"""预览裁切规则。此模块不修改导出的原画；只处理缩略图中的留白。"""
import numpy as np


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
