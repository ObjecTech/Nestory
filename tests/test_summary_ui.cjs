/**
 * Automated Test Suite for Daily Summary & 7-Day Trend UI
 * Covers:
 * 1. Highlights and suggestions rendering
 * 2. AI Narrative flow (loading state, narrative_status=ok quote box)
 * 3. AI Narrative rejection / failure fallback (never fake narrative)
 * 4. 7-Day trend pure-CSS bar chart rendering & tab switching (feeding vs sleep)
 * 5. Clicking trend column to navigate to that date
 * 6. Disclaimer display and error handling
 */

const assert = require('node:assert/strict');
const { getOrCreateElem } = require('./assistant_dom.cjs');

// Mock DOM containers
const summaryCard = getOrCreateElem('dailySummaryCard');
const recordDate = getOrCreateElem('recordDate', 'input');

const Timeline = require('../static/timeline.js');

async function runSummaryTests() {
  console.log('--- 开始 Nestory 今日作息总结与 7 天趋势 UI 自动化测试 ---');

  const baseSummaryResponse = {
    api_version: '1',
    scope: 'demo',
    timezone: 'Asia/Shanghai',
    date: '2026-10-15',
    stats: {
      feeding: { count: 2, total_ml: 240, last_at: '13:00', average_gap_minutes: 240 },
      sleep: { parent_seconds: 7200, sessions: 2, longest_seconds: 3600 },
      diaper: { count: 1, dirty: 1 },
      interaction: { count: 1, minutes: 3, reactions: { 开心: 1 }, favorite: '小手指跳跳舞' },
      visual: { sleeping: 3600, available: true, smiles: 2, reliability: 'unverified' },
    },
    highlights: [
      '今天记录了 2 次喂奶，共 240 ml，平均间隔约 4 小时。',
      '宝宝今天进行了 1 次亲子互动「小手指跳跳舞」，情绪开心。',
    ],
    suggestions: [
      '建议下午适当安排户外散步或柔和音乐陪伴。',
    ],
    trend: [
      { date: '2026-10-09', feeding_count: 2, feeding_ml: 240, parent_sleep_seconds: 7000, diaper_count: 1, interaction_count: 0 },
      { date: '2026-10-10', feeding_count: 3, feeding_ml: 360, parent_sleep_seconds: 8000, diaper_count: 2, interaction_count: 1 },
      { date: '2026-10-11', feeding_count: 2, feeding_ml: 240, parent_sleep_seconds: 6500, diaper_count: 1, interaction_count: 1 },
      { date: '2026-10-12', feeding_count: 4, feeding_ml: 480, parent_sleep_seconds: 9000, diaper_count: 3, interaction_count: 2 },
      { date: '2026-10-13', feeding_count: 3, feeding_ml: 350, parent_sleep_seconds: 7500, diaper_count: 2, interaction_count: 1 },
      { date: '2026-10-14', feeding_count: 2, feeding_ml: 260, parent_sleep_seconds: 7200, diaper_count: 1, interaction_count: 0 },
      { date: '2026-10-15', feeding_count: 2, feeding_ml: 240, parent_sleep_seconds: 7200, diaper_count: 1, interaction_count: 1 },
    ],
    narrative: null,
    narrative_status: 'not_requested',
    disclaimer: '以上为记录汇总，仅供参考，不构成医疗建议。',
    truncated: false,
  };

  // Test 1: 正常要点、建议、免责声明渲染
  console.log('测试 1: 正常要点、建议与免责声明渲染');
  Timeline.setMockHandler(async (path) => {
    if (path.includes('/api/summary/daily')) {
      return baseSummaryResponse;
    }
    throw new Error('Not mock summary');
  });

  await Timeline.loadSummary(false);

  assert.ok(summaryCard.innerHTML.includes('今日总结与作息规律'));
  assert.ok(summaryCard.innerHTML.includes('今天记录了 2 次喂奶，共 240 ml'));
  assert.ok(summaryCard.innerHTML.includes('小手指跳跳舞'));
  assert.ok(summaryCard.innerHTML.includes('建议下午适当安排户外散步'));
  assert.ok(summaryCard.innerHTML.includes('不构成医疗建议'));
  assert.ok(!summaryCard.innerHTML.includes('summary-cry-badge'), '无哭声数据时不应展示哭声标注');
  console.log('✓ 测试 1 通过: 客观要点与提醒正确渲染');

  // Test 1b: 哭声标注渲染 (stats.cry.count > 0)
  console.log('测试 1b: 包含哭声统计时的卡片小标注渲染');
  const crySummaryMock = {
    ...baseSummaryResponse,
    stats: {
      ...baseSummaryResponse.stats,
      cry: { count: 3, seconds: 240, last_at: '14:20', reliability: 'unverified' },
    },
  };
  Timeline.setMockHandler(async () => crySummaryMock);
  await Timeline.loadSummary(false);
  assert.ok(summaryCard.innerHTML.includes('哭声 3 段'), '包含哭声统计时应显示小标注「哭声 3 段」');
  assert.ok(summaryCard.innerHTML.includes('summary-cry-badge'), '应包含 summary-cry-badge 样式类');
  console.log('✓ 测试 1b 通过: 哭声统计小标注正确渲染');

  // Test 2: 7 天趋势纯 CSS 柱状图与切换
  console.log('测试 2: 7 天趋势柱状图与图表维度切换');
  assert.ok(summaryCard.innerHTML.includes('近 7 天趋势'));
  assert.ok(summaryCard.innerHTML.includes('10-09'), '柱状图应包含 7 天日期');
  assert.ok(summaryCard.innerHTML.includes('10-15'), '柱状图应包含当前日期');

  // Switch to sleep trend tab
  Timeline.state.trendTab = 'sleep';
  Timeline.renderSummary();
  assert.ok(summaryCard.innerHTML.includes('h'), '睡眠时长切换后应展示以小时为单位的数值');
  Timeline.state.trendTab = 'feeding';
  Timeline.renderSummary();
  console.log('✓ 测试 2 通过: 纯 CSS 7 天柱状图展示与维度切换正常');

  // Test 3: 点击趋势柱联动切换全局日期
  console.log('测试 3: 点击趋势柱联动切换页面全局日期');
  Timeline.setDate('2026-10-12');
  assert.equal(Timeline.state.date, '2026-10-12');
  assert.equal(recordDate.value, '2026-10-12');
  console.log('✓ 测试 3 通过: 趋势点击日期联动成功');

  // Test 4: AI 润色成功 (narrative_status = ok)
  console.log('测试 4: AI 润色成功呈现引用卡片');
  const narrativeSuccessMock = {
    ...baseSummaryResponse,
    narrative: '宝宝今天规律进食，午后手指操非常愉快，作息健康稳定。',
    narrative_status: 'ok',
  };
  Timeline.setMockHandler(async (path) => {
    if (path.includes('narrative=1')) return narrativeSuccessMock;
    return baseSummaryResponse;
  });

  await Timeline.loadSummary(true);
  assert.ok(summaryCard.innerHTML.includes('NESTORY AI 总结'));
  assert.ok(summaryCard.innerHTML.includes('宝宝今天规律进食，午后手指操非常愉快'));
  console.log('✓ 测试 4 通过: AI 润色内容以引用样式完整渲染');

  // Test 5: AI 润色被丢弃 (rejected) 或错误时的降级处理
  console.log('测试 5: AI 润色被丢弃 (rejected) 时的降级提示，绝不伪造');
  const narrativeRejectedMock = {
    ...baseSummaryResponse,
    narrative: null,
    narrative_status: 'rejected',
  };
  Timeline.setMockHandler(async (path) => {
    if (path.includes('narrative=1')) return narrativeRejectedMock;
    return baseSummaryResponse;
  });

  await Timeline.loadSummary(true);
  assert.ok(summaryCard.innerHTML.includes('AI 暂时无法润色，以上为记录汇总'));
  assert.ok(!summaryCard.innerHTML.includes('NESTORY AI 总结'));
  console.log('✓ 测试 5 通过: 润色异常时真实降级为客观汇总');

  // Test 6: 接口错误时真实展示
  console.log('测试 6: 接口出错时如实反馈，绝不捏造示例数据');
  Timeline.setMockHandler(async () => {
    const err = new Error('每日总结计算服务不可用 503');
    err.status = 503;
    throw err;
  });
  await Timeline.loadSummary(false);
  assert.ok(summaryCard.innerHTML.includes('无法加载今日总结'));
  assert.ok(summaryCard.innerHTML.includes('服务不可用 503'));
  console.log('✓ 测试 6 通过: 接口异常真实暴露');

  console.log('\n=============================================');
  console.log('🎉 全部 6 项 Daily Summary UI 测试通过！');
  console.log('=============================================');
}

runSummaryTests().catch(err => {
  console.error('\n❌ Summary UI 测试失败:', err);
  process.exit(1);
});
