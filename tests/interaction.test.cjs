// 纯分类逻辑回归；只使用 Node 内置测试工具，不增加前端运行依赖。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const groups = runInNewContext(readFileSync('pengpeng/web/interaction.js', 'utf8') + '; VoiceGroups;');

test('明确资源关联优先于音频名称，并区分对局、节日与操作提示', () => {
  for (const [item, expected] of [
    [{adventure:true, trigger_card:'A', name:'Start_Jaina_01'}, 'adventure'],
    [{trigger_card:'A', name:'VO_Attack_01'}, 'trigger'],
    [{name:'VO_HERO_11_Male_Human_Start_Jaina_01', event:'m_EmoteDefs[7]'}, 'interaction'],
    [{name:'VO_HERO_11_Male_Human_MIRROR_START_01'}, 'interaction'],
    [{name:'VO_HERO_11_Male_Human_Start_01', event:'m_EmoteDefs[7]'}, 'basic'],
    [{name:'VO_HERO_11_Male_Human_HAPPY_NEW_YEAR_01', event:'m_EmoteDefs[30]'}, 'holiday'],
    [{name:'VO_HERO_11_Male_Human_ERROR_PLAY_01', event:'m_EmoteDefs[20]'}, 'prompt'],
    [{name:'VO_HERO_11_Male_Human_Greetings_01', event:'m_EmoteDefs[0]'}, 'emote'],
    [{name:'VO_Innkeeper_Male_Dwarf_HERONAME_01'}, 'scene'],
    [{name:'unknown', text:'面对法师，节日快乐'}, 'other']
  ]) assert.equal(groups.classify(item), expected, JSON.stringify(item));
});

const items = [
  {id:'1', event:'a', name:'VO_Attack_01', text:'测试'},
  {id:'1', event:'a', name:'VO_Attack_01', text:'测试'},
  {id:'1', event:'b', trigger_card:'CARD', name:'VO_Trigger_01', text:'测试'},
  {id:'2', event:'a', adventure:true, name:'adventure', speech_text:'测试'},
  {id:'3', event:'a', kind:'sound', name:'测试音效'},
];
test('分组计数互斥、保留相同音频的不同事件，分页顺序不变', () => {
  const all = groups.select(items, 'voice', 'all', '测试');
  assert.equal(all.rows.length, 3);
  assert.equal(all.counts.all, 3);
  assert.equal(Object.entries(all.counts).filter(([k])=>k!=='all').reduce((n,[,v])=>n+v,0),3);
  assert.equal(groups.select(items, 'voice', 'trigger', '测试').rows[0], items[2]);
  assert.equal(groups.select(items, 'voice', 'adventure', '测试').rows[0], items[3]);
});
test('搜索与分组组合，音效不受角色分组影响，空匹配有准确数量', () => {
  assert.equal(groups.select(items, 'voice', 'basic', 'adventure').rows.length,0);
  assert.equal(groups.select(items, 'voice', 'all', '不存在').counts.all,0);
  assert.equal(groups.select(items, 'sound', 'adventure', '').rows[0],items[4]);
});
