"""真实 Qt 设置回归：独立工作区、本地兼容 API、真实内置模型，不改用户设置。"""
import json
import multiprocessing as mp
from pathlib import Path
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pengpeng.app import Window, own_process_tree


class API(BaseHTTPRequestHandler):
    """只在回环地址接收测试生成的一秒静音，验证实际 multipart 请求。"""
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        valid = self.path == '/v1/audio/transcriptions' and b'RIFF' in body and b'test-model' in body and self.headers.get('Authorization') == 'Bearer test-key'
        self.send_response(200 if valid else 400)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"text":""}')

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    mp.freeze_support()
    job = own_process_tree()
    output = Path('.cache/qa-speech-settings').resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(dir=output, prefix='workspace-'))
    server = ThreadingHTTPServer(('127.0.0.1', 0), API)
    Thread(target=server.serve_forever, daemon=True).start()
    app = QApplication([])
    window = Window(workspace)
    window.show()
    stages = [
        ('bundled', """async()=>{
            document.querySelector('#welcome')?.close();
            await changeView('settings');
            if(document.querySelector('#speech-provider').value!=='bundled')throw Error('default');
            document.querySelector('#check-speech-status').click();
            await waitCheck();
            const message=document.querySelector('#speech-status-result').textContent;
            if(!message.startsWith('✓')||!message.includes('英语'))throw Error(message);
            return message;
        }"""),
        ('custom-missing', """async()=>{
            const select=document.querySelector('#speech-provider');select.value='local';select.dispatchEvent(new Event('input'));
            if(document.querySelector('#speech-local-fields').hidden)throw Error('local fields hidden');
            document.querySelector('#speech-zhcn').value='Z:/missing-vosk-model';
            document.querySelector('#check-speech-status').click();
            if(!document.querySelector('#speech-status-result').textContent.includes('尚未保存'))throw Error('dirty check');
            document.querySelector('#save-speech-config').click();await waitSaved('local');
            document.querySelector('#check-speech-status').click();await waitCheck();
            const message=document.querySelector('#speech-status-result').textContent;
            if(!message.includes('自定义模型不完整'))throw Error(message);
            return message;
        }"""),
        ('api', """async()=>{
            const select=document.querySelector('#speech-provider');select.value='api';select.dispatchEvent(new Event('input'));
            document.querySelector('#speech-url').value='http://127.0.0.1:PORT/v1/audio/transcriptions';
            document.querySelector('#speech-model').value='test-model';document.querySelector('#speech-key').value='test-key';
            document.querySelector('#show-speech-key').click();
            if(document.querySelector('#speech-key').type!=='text')throw Error('show key');
            document.querySelector('#save-speech-config').click();await waitSaved('api');
            if(document.querySelector('#speech-key').value!=='test-key')throw Error('key persistence');
            document.querySelector('#check-speech-status').click();await waitCheck();
            const message=document.querySelector('#speech-status-result').textContent;
            if(!message.startsWith('✓'))throw Error(message);
            return message;
        }""".replace('PORT', str(server.server_port))),
        ('restore-bundled', """async()=>{
            document.querySelector('#speech-provider').value='bundled';document.querySelector('#speech-key').value='';
            document.querySelector('#save-speech-config').click();await waitSaved('bundled');
            const settings=(await api('status')).settings;
            if(settings.speech_api_key!=='')throw Error('key not cleared');
            return '恢复内置模型、清除密钥通过';
        }"""),
    ]
    helper = """const pause=()=>new Promise(r=>setTimeout(r,100));
    async function waitCheck(){for(let i=0;i<1200;i++){await pause();if(!document.querySelector('#check-speech-status').disabled)return;}throw Error('check timeout');}
    async function waitSaved(provider){for(let i=0;i<100;i++){await pause();if(!document.querySelector('#save-speech-config').disabled&&state.status.settings.speech_config.provider===provider)return;}throw Error('save timeout');}
    """
    current = -1
    deadline = time.monotonic() + 180
    results = []
    failed = False

    def finish(error=None):
        global failed
        failed = bool(error)
        timer.stop()
        if error:
            results.append({'error': error})
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), 'utf8')
        window.close()
        app.quit()

    def advance():
        global current, deadline
        current += 1
        deadline = time.monotonic() + 180
        if current == len(stages):
            finish()
            return
        name, source = stages[current]
        if name == 'api':
            window.resize(1060, 900)
        print('QA START', name, flush=True)
        window.page.runJavaScript('window.__qa=null;(async()=>{' + helper + 'return await (' + source + ')();})().then(result=>window.__qa={result}).catch(e=>window.__qa={error:e.message});')

    def received(encoded):
        if not encoded:
            return
        data = json.loads(encoded)
        if 'error' in data:
            finish(data['error'])
            return
        timer.stop()
        window.page.runJavaScript("document.querySelector('.speech-config').scrollIntoView({block:'start'});")
        def capture():
            name = stages[current][0]
            window.grab().save(str(output / (name + '.png')))
            results.append({'stage': name, **data})
            print('QA PASS', name, flush=True)
            advance()
            if current < len(stages):
                timer.start()
        QTimer.singleShot(350, capture)

    def tick():
        if time.monotonic() > deadline:
            finish('GUI timeout')
        elif current < 0:
            window.page.runJavaScript('!!window.pengpeng?.state.status', lambda ready: advance() if ready and current < 0 else None)
        else:
            window.page.runJavaScript('window.__qa?JSON.stringify(window.__qa):null', received)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(250)
    app.exec()
    server.shutdown()
    assert window.bridge.speech.process is None and not window.worker.is_alive()
    sys.exit(1 if failed else 0)
