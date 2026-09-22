"""从项目内矢量图生成 Windows 多尺寸图标，不依赖图像生成服务。"""
from pathlib import Path
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image


if __name__ == '__main__':
    app = QGuiApplication([])
    folder = Path(__file__).resolve().parents[1] / 'pengpeng/web'
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    QSvgRenderer(str(folder / 'icon.svg')).render(painter)
    painter.end()
    image.save(str(folder / 'icon.png'))
    Image.open(folder / 'icon.png').save(folder / 'icon.ico', sizes=[(s,s) for s in (16,24,32,48,64,128,256)])
