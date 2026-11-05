/**
 * Automated Test Suite for Parent-Child Interaction Cards & Reaction Recording UI
 * Covers loading activities catalog, timing, reaction recording, error handling,
 * idempotency client_id retention, scope isolation, and today's logs.
 */

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Setup mock DOM
const { getOrCreateElem, domRegistry } = require('./assistant_dom.cjs');

// Register Play UI elements
const playGrid = getOrCreateElem('playActivitiesGrid');
const playScopeBadge = getOrCreateElem('playScopeBadge');
const playScopeSelect = getOrCreateElem('playScopeSelect', 'select');
const todayList = getOrCreateElem('todayInteractionsList');

// Global mock activities
const activitiesData = JSON.parse(
  fs.readFileSync(path.join(__dirname, '../static/play/activities.json'), 'utf8')
);

// Global fetch stub
global.fetch = async (url, options = {}) => {
  const urlStr = String(url);
  if (urlStr.includes('activities.json')) {
    return {
      ok: true,
      status: 200,
      json: async () => activitiesData,
    };
  }
  return {
    ok: false,
    status: 404,
    json: async () => ({ error: 'Not found' }),
  };
};

const Play = require('../static/play.js');

async function runPlayTests() {
  console.log('--- 开始 Nestory Play 亲子互动与反应记录 UI 自动化测试 ---');

  // Test 1: 初始化与互动内容加载
  console.log('测试 1: 初始化与互动内容加载');
  assert.equal(Play.state.scope, 'real', '只保留一套真实记录');
  assert.ok(activitiesData.length >= 8, '互动内容库至少应包含 8 项原创内容');
  
  // Set mock handler to intercept API
  const inMemoryInteractions = {
    demo: [],
    real: [],
  };

  Play.setMockHandler(async (urlPath, options = {}) => {
    const method = options.method || 'GET';
    const parsed = new URL(urlPath, 'http://localhost:8000');

    if (method === 'GET' && parsed.pathname === '/api/interactions') {
      const scope = parsed.searchParams.get('scope') || 'demo';
      return {
        api_version: '1',
        scope,
        items: inMemoryInteractions[scope] || [],
      };
    }

    if (method === 'POST' && parsed.pathname === '/api/interactions') {
      const body = JSON.parse(options.body);
      if (body.title === 'FORCE_ERROR') {
        const err = new Error('网络繁忙，未能保存互动');
        err.status = 500;
        err.code = 'server_error';
        throw err;
      }
      const item = {
        id: 'inter-' + Date.now(),
        ...body,
        created_at: new Date().toISOString(),
      };
      inMemoryInteractions[body.scope].unshift(item);
      return {
        api_version: '1',
        interaction: item,
        replayed: false,
      };
    }

    if (method === 'DELETE' && parsed.pathname.startsWith('/api/interactions/')) {
      const id = parsed.pathname.split('/').pop();
      const scope = parsed.searchParams.get('scope') || 'demo';
      inMemoryInteractions[scope] = inMemoryInteractions[scope].filter(x => x.id !== id);
      return { api_version: '1', deleted: true };
    }

    throw new Error(`Unhandled ${method} ${urlPath}`);
  });

  await Play.loadActivities();
  assert.equal(Play.state.activities.length, activitiesData.length);
  assert.ok(playGrid.innerHTML.includes('小手指跳跳舞'), '页面应渲染出手指操内容');
  const defaultCardCount = (playGrid.innerHTML.match(/class="activity-card"/g) || []).length;
  assert.equal(defaultCardCount, 3, '默认应只展示前 3 张互动卡片');
  assert.ok(playGrid.innerHTML.includes('playExpandBtn'), '应显示展开全部互动按钮');
  Play.expandActivities();
  const expandedCardCount = (playGrid.innerHTML.match(/class="activity-card"/g) || []).length;
  assert.equal(expandedCardCount, Play.state.activities.length, '展开后应展示全部互动卡片');
  console.log('✓ 测试 1 通过: 互动内容目录加载与卡片结构正常渲染（默认显示3张，展开全部生效）');

  // Test 2: 分类筛选
  console.log('测试 2: 分类筛选');
  Play.setFilter('game');
  assert.equal(Play.state.filter, 'game');
  assert.ok(playGrid.innerHTML.includes('躲猫猫看世界'), '筛选 game 分类应显示亲子游戏');
  assert.ok(!playGrid.innerHTML.includes('小手指跳跳舞'), '筛选 game 不应出现 finger_play');
  Play.setFilter('all');
  console.log('✓ 测试 2 通过: 分类标签筛选准确无误');

  // Test 3: 启动互动计时与结束打卡
  console.log('测试 3: 启动互动计时与结束打卡');
  const targetAct = Play.state.activities[0];
  Play.startActivity(targetAct);
  assert.ok(Play.state.activeTimer, '应存在活跃计时器');
  assert.equal(Play.state.activeTimer.activity.id, targetAct.id);
  assert.ok(Play.state.activeTimer.clientId, '必须生成唯一的 client_id');
  const savedClientId = Play.state.activeTimer.clientId;

  // Simulate finish
  Play.finishActivity();
  assert.equal(Play.state.activeTimer.timerInterval, null, '计时器应停止');
  assert.ok(Play.state.activeTimer.endedAt, '应生成结束时间戳');
  assert.ok(playGrid.innerHTML.includes('孩子反应与情绪'), '应展示反应选择与情绪面板');
  console.log('✓ 测试 3 通过: 计时与完成状态转换正常');

  // Test 4: 保存失败时诚实显示错误并保留反应与 client_id
  console.log('测试 4: 保存失败时如实显示错误，不能假装成功，并复用同一个 client_id');
  Play.state.activeTimer.activity.title = 'FORCE_ERROR';
  Play.state.activeTimer.reaction = 'happy';
  Play.state.activeTimer.notes = '宝宝笑得非常开心';

  await Play.submitActiveActivity();
  assert.ok(Play.state.activeTimer, '失败时活跃表单绝对不能关闭');
  assert.ok(Play.state.activeTimer.error.includes('未能保存'), '必须如实呈现失败错误信息');
  assert.equal(Play.state.activeTimer.clientId, savedClientId, '重试时必须复用同一个 client_id 以保证幂等性');
  assert.equal(Play.state.activeTimer.reaction, 'happy', '已选中的反应状态必须予以保留');
  assert.equal(Play.state.activeTimer.notes, '宝宝笑得非常开心', '已输入的备注信息必须予以保留');
  assert.ok(playGrid.innerHTML.includes('保存失败'), '界面卡片上必须显示保存失败状态');
  console.log('✓ 测试 4 通过: 失败真实暴露、数据完整保留与 client_id 幂等复用');

  // Test 5: 成功保存并自动刷新今日记录
  console.log('测试 5: 成功保存并自动刷新今日记录');
  Play.state.activeTimer.activity.title = '小手指跳跳舞'; // restore valid title
  await Play.submitActiveActivity();
  assert.equal(Play.state.activeTimer, null, '成功后计时状态应清空');
  assert.equal(Play.state.todayItems.length, 1, '今日记录数量应增加');
  assert.equal(Play.state.todayItems[0].reaction, 'happy');
  assert.ok(todayList.innerHTML.includes('小手指跳跳舞'), '今日记录列表应渲染新保存的条目');
  console.log('✓ 测试 5 通过: 成功提交、持久化与今日列表刷新');

  // Test 6: 范围切换与数据隔离
  console.log('测试 6: 记录写入唯一的真实记录集');
  assert.equal(inMemoryInteractions.real.length, 1, '互动记录应保存到 real');
  assert.equal(inMemoryInteractions.demo.length, 0, '不应再写入 demo');
  console.log('✓ 测试 6 通过: 只使用一套真实记录');

  // Test 7: 删除互动记录
  console.log('测试 7: 删除互动记录');
  global.confirm = () => true;
  const toDeleteId = Play.state.todayItems[0].id;
  await Play.deleteInteraction(toDeleteId);
  assert.equal(Play.state.todayItems.length, 0, '删除后今日记录应清空');
  console.log('✓ 测试 7 通过: 互动记录删除生效');

  console.log('\n=============================================');
  console.log('🎉 全部 7 项 Play 亲子互动与反应 UI 测试通过！');
  console.log('=============================================');
}

runPlayTests().catch(err => {
  console.error('\n❌ Play UI 测试失败:', err);
  process.exit(1);
});
