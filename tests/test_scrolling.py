"""系统幅度、零行、整页与缩放，均不修改测试机器的 Windows 设置。"""
import pytest
from pengpeng.scrolling import wheel_distance, PAGE_SCROLL
from pengpeng.service import Service


@pytest.mark.parametrize('lines,expected', [(0,0),(1,100/3),(3,100),(6,200),(10,1000/3)])
def test_system_line_count_is_proportional(lines, expected):
    assert wheel_distance(-120, lines, 1) == pytest.approx((expected,False))


def test_page_small_delta_and_zoom():
    assert wheel_distance(-120,PAGE_SCROLL,1.4)==(1,True)
    assert wheel_distance(-30,3,1)==(25,False)
    assert wheel_distance(120,3,1.25)==(-80,False)


def test_scroll_preference_survives_restart(tmp_path):
    s=Service(tmp_path);s.save_settings(scroll_mode='instant')
    assert Service(tmp_path).settings['scroll_mode']=='instant'
    with pytest.raises(ValueError):s.save_settings(scroll_mode='invalid')
