# 滚动、声音叠放与分类（0.12）

## Windows 原生显示链路（0.12 默认）

本机显示器报告 170Hz，但原来的 Qt WebEngine 页面长期约 60fps。
0.11.1 减少 Paint 只降低了绘制开销，没有解决帧源与窗口呈现节奏。
实验中只解除 Chromium 帧率限制会让 rAF 明显超过实际 Qt 窗口更新次数，
因此没有把 `--disable-frame-rate-limit`、强制显卡开关或自建限帧器写入程序。

主界面改用已随 PySide6 提供的 Qt 官方 WebView2 插件，系统浏览器直接呈现
原生子窗口。默认平滑模式不安装阻塞 wheel 监听器，不向 Python 转发滚轮，
也不运行旧 ScrollMotion 动画。滚轮、轨道点击、拖拽、屏幕刷新同步和 Windows
系统行数由同一浏览器引擎负责。仅即时/减少动画模式按需安装直接定位处理；
恢复平滑后移除处理器。被动 scroll/scrollend 事件只控制悬停样式，不驱动位置。

`native_view.py` 和 `NativeView.qml` 只适配窗口与脚本返回。由于 Qt WebView
未公开 WebMessageReceived，`host.js` 使用批量业务队列，在原有后端 35ms
轮询中最多保留一个异步交换；滚动、媒体时钟与此轮询无关。目录选择采用请求
回调，响应沿用原有 ID。桥只开放既有业务操作，导航改变后丢弃上一代交换；
非本地入口导航停止，外链显式交给系统浏览器。保留原 CSP，不开放 HTTP 端口。

浏览器缓存位于工作区 `browser/`。只读检测已安装的 WebView2 Runtime，缺失时
自动回退内置 Qt WebEngine。正常应用不配置调试端口、不下载安装 Runtime。
可设置进程环境变量 `PENGPENG_RENDERER=qt` 验证兼容路径；设置页显示当前引擎。
Qt WebView 尚未暴露页面 ZoomFactor，原生路径使用 CSS zoom，并统一换算视口单位、
媒体查询断点与固定弹层坐标；反复调整按原始断点计算，不累乘。Edge 不允许读取
file: 外链样式的 CSSOM，因此宿主在开放业务桥前将随包 CSS 原样转为文档内样式，
不放宽来源限制、不复制维护第二份 CSS。最大字体和界面放大组合下，导航可独立
滚动，底部状态不会被裁掉；Qt 兼容路径继续使用原生页面 ZoomFactor。
开发截图需采集窗口所在的桌面合成结果，因为 QWidget 离屏 grab 不含原生 HWND。

本机实际卡图页约 170.2fps，rAF p95 6ms，单格 100px 呈现约 28 次位置变化，
连续十格 1000px。此值是页面刷新采样，不等同光学测量或所有机器的保证。
原生鼠标、拖拽、详情嵌套、音频及设置通过 `tools/gui_native12.py` 验证。
该工具仅在独立测试窗口内短暂操作鼠标，结束恢复光标；不更改系统滚动设置。
测试窗口被遮挡时停止输入；`--no-input` 可只验证帧率、布局、媒体和业务桥。

依据：[Qt WebView 平台说明](https://doc.qt.io/qt-6/qtwebview-index.html)、
[QQuickWidget 性能约束](https://doc.qt.io/qt-6/qquickwidget.html#performance-considerations)。

## Qt 兼容引擎输入与页面绘制（0.11 实现保留）

0.10 只打开 Qt 的原生平滑开关仍未解决实际手感。本次确认 Qt 6.10 的
`setBlinkWheelEventDelta` 将滚动行数存为 static，按 20 DIP/行换算，并忽略
Windows 的 pixelDelta。这意味着默认 3 行只有约 60px，启动后改变系统设置
也不能反映到滚动幅度；Qt 的行为不能直接等同 Chrome。

`pengpeng/scrolling.py` 在输入边界每次读取 SystemParametersInfoW 的当前行数/
字符数，参考 Chromium Windows 行距 100/3 DIP 换算，3 行约 100 DIP。保留
分数刻度与缩放坐标；0 行不滚动；整页标志交给页面按命中容器的可见高度减
40px 换算。既有 pixelDelta 直接保留，并绕过额外动画；Ctrl/Alt/Meta 操作
保持原生路径。没有修改 Windows 系统设置，也没有重发合成滚轮事件。

每个输入只向页面提交一次。`web/scrolling.js` 按实际鼠标位置寻找最近滚动
容器，保留 overscroll 边界。普通刻度使用单一 requestAnimationFrame 链，
90ms Hermite 曲线在终点停止。同向续接已显示的速度与未完成距离，反向从
当前位置立即改向。新方向输入当次呈现约半帧进度，避免额外等待一帧；
同向续接不预进，保持速度连续。没有渐近拖尾，没有 CSS transform，也不叠加浏览器的
smooth 动画。帧回调直接写 scrollTop/Left，文字、图片和原生滚动条共享位置。

指针按下、键盘操作、窗口失焦、页面隐藏，以及程序主动改变滚动位置都会
终止余量，不抢回用户拖动的位置。设置中提供轻量平滑与即时滚动，偏好保存
在工作区；系统减少动画优先使用即时路径。普通鼠标与像素手势不共用惯性。

缩略图继续限制两个解码 worker；先 Image.decode，再一次替换已有占位，
保持固定容器和版本角标。滚轮动画期间暂停卡片悬停渐变，避免静止光标经过
多张卡片时反复产生明暗/阴影变化。没有给整个长列表强制创建巨大 GPU 图层。

源码依据：[Qt 6.10 输入转换](https://github.com/qt/qtwebengine/blob/6.10/src/core/web_event_factory.cpp)、
[Chromium Windows 事件转换](https://chromium.googlesource.com/chromium/src/+/main/ui/events/blink/web_input_event_builders_win.cc)。

## 内容绘制与滚动条（0.11.1）

0.11.0 的滑块虽然保留了浏览器交互，但 `::-webkit-scrollbar` 伪元素仍属于
自定义绘制。真实 Chromium 轨迹显示：即使卡图全部预热且 Layout 为零，滑块
移动仍几乎每帧触发根节点 Paint。卡片局部 contain 在对照中没有明显收益，
因此最终修复只替换已经证实有开销的滚动条样式。

改用继承的 `scrollbar-color` 标准属性，保留默认宽度、轨道与滑块交互，移除
自绘圆角、边框及 hover 宽度变化。页面、详情及下拉容器共用原生绘制路径。
`scrollbar-gutter:stable` 继续避免结果变短时网格横向跳动。滚轮距离和曲线不变。
依据见 [Chrome 标准滚动条样式文档](https://developer.chrome.com/docs/css-ui/scrollbar-styling)。

测试工具 `tools/profile_scroll_paint.py` 通过本机临时调试接口读取 Chromium
Tracing，预热 96 张卡牌的可见图片后比较同一路径，输出 `.cache/qa-11-1-paint/`。
本机 4 秒内：Paint 240 → 20 次，累计 380.57 → 38.81ms；主帧任务累计
695.63 → 211.13ms；Layout 均为零。rAF p95 为 17.8 → 17.6ms，两组均未测到
超过 25ms 的间隔，因此只能说明显著减少绘制开销，不能称帧率提高十倍。
原生宽度略有不同，页面总高从 8217 变为 8205px；图片、窗口及滚动距离相同。
这是本机合成滚动路径的绘制对照；另用 `gui_scroll11.py` 验证真实 Windows
滚轮与按下/移动/松开滚动条，二者互补，均不能代替用户设备上的视觉验收。

复测时在两个 PowerShell 窗口运行（仅用于独立测试工作区）：

```powershell
# 窗口一：进程环境变量只对本窗口及子进程有效，正式启动不配置此项。
$env:PENGPENG_RENDERER='qt'
$env:QTWEBENGINE_REMOTE_DEBUGGING='127.0.0.1:9227'
.venv/Scripts/python.exe -X utf8 launch.py --workspace .cache/qa-05-workspace --quit-after 120
Remove-Item Env:QTWEBENGINE_REMOTE_DEBUGGING
Remove-Item Env:PENGPENG_RENDERER
# 窗口二：等测试页面初始化后运行。
.venv/Scripts/python.exe -X utf8 tools/profile_scroll_paint.py
```

## 一份播放计划、一条媒体时钟

| 时间证据 | 试听/合并导出行为 |
| --- | --- |
| 已确定卡牌事件起点 | 使用该音轨的静态延迟 |
| 与已确定主轨同零点的时间 | 使用已确定时间 |
| 状态进入时刻未知、随机触发、通用落地/盾牌音效 | 随主语音起点叠放 |
| 未勾选配套/通用音效 | 原始单轨播放 |

`Service.audio_plan` 按资源 ID 去重，配套与通用列表重叠时优先保留已解析
时间。回退轨道记录 `placement=overlay`，资源原有 `timing` 不被改写；这是
试听约定，不是对炉石真实战斗触发时间的断言。若主轨有 0.1 秒延迟，未知轨道
也从 0.1 秒开始，避免短音效在语音开始前已经放完。主轨时间未知但配套具有
卡牌事件延迟时，仍保留配套延迟。

勾选立即影响下一次播放，后台保存失败才恢复原状态。UI 不再显示试听时间轴；
真正的解码失败仍会提示。保留原始单轨试听、导出与资源证据查看功能。

`audio_mix.mix_samples` 按样本偏移混合，保留源文件前置静音、主轨完整长度和
配套尾声。配套每轨读取最多 15 秒，逐轨累加，避免音轨数乘以时长的大型数组；
采样率统一、单声道向双声道广播、峰值超限时整体衰减。缓存生成的单个 WAV
供一个 HTMLAudioElement 播放，暂停、定位与重播不需要多播放器定时同步。
合并导出调用同一播放计划，报告保留偏移和时间证据。

## 声音类别与默认配乐

大类为：角色语音、音效、短音乐/登场曲、背景音乐。场景通过第二个筛选器表达。
分类先看片段命名：VO/voice 是语音；stinger/jingle 是短曲；明确的撞击、施法、
按钮、环境循环等音效证据优先于所在包。音乐包不再让所有片段自动变成音乐。

`music_titles.py` 保存本机已核对资源目录中的乐曲名称，仅含名称，不含游戏音频。
它补足没有 music 前缀的完整乐曲，采用精确匹配，不依赖当前资源包哈希。
`Main_Title`、`Duel`、`Better Hand`、`Don't Let Your Guard Down`、`On a Roll`、
`Bad Reputation`、`Collection Manager` 与 Mulligan 片段位于“经典默认音乐”。
时长只辅助区分已有音乐证据的长短曲，未知的长环境循环不会仅因时长而归音乐。
命名规则不能完成声学鉴定；没有可靠命名证据的片段仍显示为待细分。

类别和中文标签分别有迁移版本，升级只重算已索引元数据，不重解压音频，
不改变资源 ID、收藏或导出记录。旧类别筛选记忆会转换，避免升级后落入不存在
的分类。中文注释是命名规则说明，不冒充官方中文曲名。

## 页面索引与完整索引

音乐定向扫描包含通用音频包和 Player 内置资源，完成仅写 `last_music_scan`。
完整扫描才写 `last_scan` 并计算新资源差分。worker 的完成事件携带实际 `scope`，
页面分别提示“当前页面索引已完成”和“完整资源索引已完成”。
任何包解析失败均保留成功部分并报告错误，不写本轮完整成功标记；重试复用
未变化的成功包。这里的“完成”表示索引发现过程完成，不保证每个资源都可解码。

## 可重复验收

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q
node --check pengpeng/web/app.js
node --check pengpeng/web/scrolling.js
node --test tests/interaction.test.cjs tests/scrolling.test.cjs
.venv/Scripts/python.exe -X utf8 tools/gui_scroll11.py
.venv/Scripts/python.exe -X utf8 tools/verify_10.py
```

真实资源与 GUI 使用 `.cache/qa-05-workspace`，滚动结果写入 `.cache/qa-11`。
滚轮通过测试窗口的 WM_MOUSEWHEEL 进入 Qt Windows 分派。不同系统行数通过
替换设置读取返回值验证，不修改用户 Windows 设置；本机实际设置另以 Win32
读取确认。`--controls` 单独验证原生滚动条拖动与设置保存。

本机默认 3 行移动 100px，连续十格移动 1000px；1 行约 33px，6 行 200px，
0 行不移动，940px 容器整页移动 900px，1.25 倍缩放移动 80 CSS px。
显示帧采样的平滑收尾约 101–117ms（包含宿主调度和采样相位），帧间隔 p95
约 17–19ms；即时模式仅一次位移。嵌套详情外层位移为零，跟踪内容的布局漂移
不足 0.001px；滚动条拖动到 1372.5px，松开 250ms 后保持不变。

这些记录验证输入幅度、时序与位置稳定性，不能替代用户鼠标、显示器与驱动
环境下的主观手感验收。0.10 的旧测量不是本版验收依据。
