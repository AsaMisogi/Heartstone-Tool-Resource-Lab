// 原生 WebView2 自行呈现内容；Qt Quick 只负责窗口尺寸和脚本回调。
import QtQuick
import QtWebView

Item {
    id: root
    property string address: browser.url.toString()
    signal completed(int identity, string encoded)
    signal loaded(bool ok)
    signal navigated(string address)
    WebView {
        id: browser
        anchors.fill: parent
        settings.javaScriptEnabled: true
        settings.allowFileAccess: true
        onUrlChanged: root.navigated(url.toString())
        onLoadingChanged: function(request) {
            if (request.status === WebView.LoadSucceededStatus) root.loaded(true)
            else if (request.status === WebView.LoadFailedStatus) root.loaded(false)
        }
    }
    function navigate(address) { browser.url = address }
    function stopNavigation() { browser.stop() }
    function evaluate(script, identity) {
        browser.runJavaScript(script, function(result) {
            root.completed(identity, JSON.stringify(result) ?? "null")
        })
    }
}
