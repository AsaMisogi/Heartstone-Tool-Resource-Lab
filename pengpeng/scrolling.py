"""绕过 Qt WebEngine 缓存行数和固定 20px/行的滚轮转换。

每个原始输入只发送一次页面调用，不重发 QWheelEvent、不运行动画定时器。
浏览器页面负责命中容器与显示帧；宿主仅保留系统输入的单位、距离与坐标。
"""
import ctypes
import json
import sys

from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QApplication

PAGE_SCROLL = 0xFFFFFFFF
PIXELS_PER_LINE = 100 / 3  # Chromium Windows 的行滚动换算，3 行约 100 DIP。


def system_scroll_units(horizontal=False):
    """读取当前设置，包括 0（不滚动）与整页；不使用 Qt 的启动时缓存。"""
    if sys.platform == 'win32':
        value = ctypes.c_uint()
        if ctypes.windll.user32.SystemParametersInfoW(108 if horizontal else 104, 0, ctypes.byref(value), 0):
            return value.value
    return QApplication.wheelScrollLines()


def wheel_distance(angle, units, zoom):
    """保留小刻度的小数距离；整页单位交给实际命中容器换算。"""
    if units == PAGE_SCROLL:
        return -angle / 120, True
    return -angle / 120 * units * PIXELS_PER_LINE / zoom, False


class WheelInput(QObject):
    def __init__(self, web):
        super().__init__(web)
        self.web, self.ready = web, False
        web.loadStarted.connect(lambda: self.set_ready(False))
        web.loadFinished.connect(self.set_ready)
        QApplication.instance().installEventFilter(self)

    def set_ready(self, ready):
        self.ready = ready

    def eventFilter(self, watched, event):
        if (not self.ready or event.type() != QEvent.Type.Wheel
                or watched != self.web.focusProxy()):
            return False
        modifiers = event.modifiers()
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
                        | Qt.KeyboardModifier.MetaModifier):
            return False  # 缩放及系统快捷操作保持原生行为。
        zoom = self.web.zoomFactor()
        pixel, angle = event.pixelDelta(), event.angleDelta()
        precise = not pixel.isNull() or event.phase() != Qt.ScrollPhase.NoScrollPhase
        if not pixel.isNull():
            dx, dy = -pixel.x() / zoom, -pixel.y() / zoom
            page_x = page_y = False
        else:
            dx, page_x = wheel_distance(angle.x(), system_scroll_units(True), zoom)
            dy, page_y = wheel_distance(angle.y(), system_scroll_units(), zoom)
        if modifiers & Qt.KeyboardModifier.ShiftModifier and not dx:
            dx, dy, page_x, page_y = dy, 0, page_y, False
        if dx or dy:
            payload = dict(x=event.position().x() / zoom, y=event.position().y() / zoom,
                           dx=dx, dy=dy, pageX=page_x, pageY=page_y, precise=precise)
            # 此调用只有数值和布尔值；使用 JSON 保留分数位移而非取整到角度单位。
            self.web.page().runJavaScript('WheelScroll.input(' + json.dumps(payload) + ');')
        event.accept()
        return True
