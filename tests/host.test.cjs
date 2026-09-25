const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync('pengpeng/web/host.js','utf8');
function native(){
  const listeners={},page={addEventListener:(name,fn)=>listeners['window:'+name]=fn};let nativeCalls=0;
  const style={setProperty(name,value){this[name]=value;}};
  const rules=[{type:4,conditionText:'(max-width: 800px) and (max-height: 600px)',media:{}},
    {type:4,conditionText:'(prefers-reduced-motion: reduce)',media:{}}];
  const context={
    window:page,innerWidth:1440,innerHeight:900,CSSRule:{MEDIA_RULE:4},
    document:{styleSheets:[{cssRules:rules}],addEventListener:(name,fn)=>listeners[name]=fn,documentElement:{style}},
    WheelScroll:{useNative:()=>nativeCalls++,setReduced:()=>{}},
  };
  const [connect,viewport]=runInNewContext(source+';[connectHost,logicalViewport];',context);
  let host;connect(h=>host=h);
  return {page,host,listeners,nativeCalls,style,rules,context,viewport};
}
test('原生缩放同步响应式断点、视口与弹层坐标，反复调整不累乘',()=>{
  const {host,style,rules,context,listeners,viewport}=native();
  host.setInterfaceScale(1.25);host.setInterfaceScale(1.4);
  assert.equal(rules[0].media.mediaText,'(max-width: 1120px) and (max-height: 840px)');
  assert.equal(rules[1].media.mediaText,'(prefers-reduced-motion: reduce)');
  host.setInterfaceScale(1.25);
  assert.equal(style['--viewport-width'],'1152px');
  assert.equal(viewport({left:125,right:250,top:250,bottom:375,width:125,height:125}).left,100);
  context.innerHeight=1000;listeners['window:resize']();
  assert.equal(style['--viewport-height'],'800px');
  host.setInterfaceScale(1);
  assert.equal(rules[0].media.mediaText,rules[0].conditionText);
});
test('原生业务桥批量交换请求与响应，队列消费一次',()=>{
  const {page,host,nativeCalls}=native();assert.equal(nativeCalls,1);
  let response;host.response.connect(value=>response=value);
  host.request('{"id":1,"method":"status"}');host.openLogs();
  const batch=page.NativeHost.exchange([{kind:'response',value:'{"id":1,"result":{}}'}]);
  assert.equal(batch.length,2);assert.equal(batch[0].method,'request');assert.equal(batch[1].method,'openLogs');
  assert.equal(response,'{"id":1,"result":{}}');assert.equal(page.NativeHost.exchange([]).length,0);
});
test('原生目录选择回调按请求匹配并只执行一次，外链发给系统浏览器',()=>{
  const {page,host,listeners}=native();let result='',calls=0;
  host.chooseDirectory('game',value=>{result=value;calls++;});
  const [request]=page.NativeHost.exchange([]);
  const answer={kind:'callback',id:request.id,value:'E:\\游戏 目录'};
  page.NativeHost.exchange([answer,answer]);assert.equal(result,answer.value);assert.equal(calls,1);
  let prevented=false;listeners.click({target:{closest:()=>({href:'https://example.com/project'})},preventDefault:()=>prevented=true});
  assert.equal(prevented,true);assert.equal(page.NativeHost.exchange([])[0].method,'openExternal');
});
test('兼容引擎沿用 WebChannel，不启用原生业务桥',()=>{
  let script,received;const host={request:()=>{}};const qt={webChannelTransport:{}};const page={qt};
  const connect=runInNewContext(source+';connectHost;',{
    window:page,qt,document:{createElement:()=>({}),head:{append:s=>script=s}},
    QWebChannel:function(transport,callback){assert.equal(transport,qt.webChannelTransport);callback({objects:{host}});}
  });connect(h=>received=h);script.onload();assert.equal(received,host);assert.equal(page.NativeHost,undefined);
});
