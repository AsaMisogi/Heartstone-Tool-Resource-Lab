// 对照语言按同一资源与触发上下文匹配，不依赖语言包排序或展示文案。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const locales = runInNewContext(readFileSync('pengpeng/web/interaction.js', 'utf8') + '; VoiceLocales;');

test('语言包顺序、文件 ID 和台词不同仍按完整音频名匹配', () => {
  const first = {id:'en1', name:'VO_ATTACK_01', event:'m_Attack', text:'English'};
  const second = {...first, id:'cn1', name:'vo_attack_01', text:'中文'};
  const other = {...second, id:'cn2', name:'VO_ATTACK_02'};
  const index = locales.index([other, second]);
  assert.equal(index.get(locales.key(first)), second);
  assert.notEqual(locales.key(first), locales.key(other));
});

test('不混淆同音频的事件、触发卡牌和条件，原始条件字段顺序无关', () => {
  const item = {id:'1', name:'VO_TRIGGER_01', event:'a', trigger_card:'CARD', condition_raw:{b:2,a:1}};
  assert.equal(locales.key(item), locales.key({...item, condition_raw:{a:1,b:2}}));
  for (const change of [{event:'b'}, {trigger_card:'OTHER'}, {condition_raw:{a:2,b:2}}])
    assert.notEqual(locales.key(item), locales.key({...item,...change}));
});

test('缺失或同一匹配键存在多个文件时不猜测，重复引用可合并', () => {
  const item = {id:'1', name:'VO_PLAY_01'};
  assert.equal(locales.index([item,item]).get(locales.key(item)), item);
  assert.equal(locales.index([item,{...item,id:'2'},item]).get(locales.key(item)), null);
  assert.equal(locales.index([]).get(locales.key(item)), undefined);
});
