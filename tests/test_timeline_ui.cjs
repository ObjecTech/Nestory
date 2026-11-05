/**
 * Automated Test Suite for Unified Timeline UI
 * Covers:
 * 1. Metrics summary rendering (feeding, sleep, diaper, interaction)
 * 2. Visual stats separation and available=false alert
 * 3. All-day color ribbon rendering (0:00-24:00, sleep/awake/unknown colors, presence stripes, scale, legend)
 * 4. Clicking segment expands detail panel with overlapping AI observations
 * 5. Event stream filtering: posture and presence excluded by default; vision activity only shows sleeping
 * 6. 5-pill single-select filter (all, sleep, feeding, cry, interaction)
 * 7. Warnings, truncated alerts, and honest error handling
 */

const assert = require('node:assert/strict');
const { getOrCreateElem } = require('./assistant_dom.cjs');

// Mock DOM containers
const timelineCard = getOrCreateElem('unifiedTimelineCard');
const summaryCard = getOrCreateElem('dailySummaryCard');
const recordsScopeBadge = getOrCreateElem('recordsScopeBadge');
const recordsScopeSelect = getOrCreateElem('recordsScopeSelect', 'select');
const recordDate = getOrCreateElem('recordDate', 'input');

const Timeline = require('../static/timeline.js');

async function runTimelineTests() {
  console.log('--- 开始 Nestory 今日统一时间线 UI 自动化测试 ---');

  const mockTimelineResponse = {
    api_version: '1',
    scope: 'demo',
    timezone: 'Asia/Shanghai',
    from: '2026-10-15',
    to: '2026-10-15',
    visual: {
      observation_mode: 'adult_test',
      continuity_verified: false,
      available: true,
    },
    items: [
      {
        id: 'care:001',
        source: 'parent_chat',
        category: 'care',
        type: 'feeding',
        label: '喂奶 150 ml',
        start_at: '2026-10-15T09:00:00Z',
        end_at: null,
        duration_seconds: null,
        reliability: 'parent_reported',
        amount_ml: 150,
      },
      {
        id: 'vision:interval:12',
        source: 'vision',
        category: 'activity',
        type: 'sleeping',
        label: '睡着',
        start_at: '2026-10-15T10:00:00Z',
        end_at: '2026-10-15T11:00:00Z',
        duration_seconds: 3600,
        open: false,
        reliability: 'unverified',
      },
      {
        id: 'vision:posture:01',
        source: 'vision',
        category: 'posture',
        type: 'supine',
        label: '仰睡',
        start_at: '2026-10-15T10:05:00Z',
        end_at: '2026-10-15T10:45:00Z',
        duration_seconds: 2400,
        open: false,
        reliability: 'unverified',
      },
      {
        id: 'vision:presence:01',
        source: 'vision',
        category: 'presence',
        type: 'not_visible',
        label: '不在画面',
        start_at: '2026-10-15T12:00:00Z',
        end_at: '2026-10-15T12:30:00Z',
        duration_seconds: 1800,
        open: false,
        reliability: 'unverified',
      },
      {
        id: 'vision:activity:awake',
        source: 'vision',
        category: 'activity',
        type: 'awake',
        label: '清醒',
        start_at: '2026-10-15T13:00:00Z',
        end_at: '2026-10-15T13:30:00Z',
        duration_seconds: 1800,
        open: false,
        reliability: 'unverified',
      },
      {
        id: 'interaction:001',
        source: 'interaction',
        category: 'interaction',
        type: 'finger_play',
        label: '小手指跳跳舞',
        start_at: '2026-10-15T14:00:00Z',
        end_at: '2026-10-15T14:03:00Z',
        duration_seconds: 180,
        reaction: 'happy',
        notes: '宝宝很配合',
      },
      {
        id: 'audio:interval:01',
        source: 'audio',
        category: 'cry',
        type: 'crying',
        label: '哭声',
        start_at: '2026-10-15T15:30:00Z',
        end_at: '2026-10-15T15:33:00Z',
        duration_seconds: 180,
        open: false,
        reliability: 'unverified',
      },
    ],
    summary: {
      feeding_count: 2,
      feeding_ml: 240,
      diaper_count: 1,
      parent_sleep_seconds: 7200,
      interaction_count: 1,
      cry_count: 1,
      cry_seconds: 180,
      visual_activity_seconds: { sleeping: 3600, awake: 1800 },
      visual_reliability: 'unverified',
    },
    truncated: false,
    warnings: ['摄像头监测连续性未验证，视觉时长仅供参考，不能与家长记录相加。'],
  };

  Timeline.setMockHandler(async (path) => {
    if (path.includes('/api/timeline/unified')) {
      return mockTimelineResponse;
    }
    if (path.includes('/api/observations')) {
      return { items: [] };
    }
    throw new Error('Not mock timeline');
  });

  recordDate.value = '2026-10-15';
  Timeline.state.date = '2026-10-15';

  await Timeline.loadUnifiedTimeline();

  assert.ok(timelineCard.innerHTML.includes('240 ml'), '应显示喂奶总量 240 ml');
  assert.ok(timelineCard.innerHTML.includes('共 2 次'), '应显示喂奶次数');
  assert.ok(timelineCard.innerHTML.includes('2 小时'), '应显示家长记录睡眠 2 小时');
  assert.ok(timelineCard.innerHTML.includes('1 次'), '应显示互动与换尿布次数');
  assert.ok(timelineCard.innerHTML.includes('1 段'), '应显示哭声 1 段');
  assert.ok(timelineCard.innerHTML.includes('3 分钟'), '应显示哭声时长 3 分钟');
  assert.ok(timelineCard.innerHTML.includes('摄像头观察（仅供参考）'), '必须包含单独的摄像头观察区域');
  assert.ok(timelineCard.innerHTML.includes('睡着: 1 小时'), '应显示摄像头观察的睡眠时长');
  assert.ok(timelineCard.innerHTML.includes('摄像头监测连续性未验证'), '必须显示 warnings[0]');
  console.log('✓ 测试 1 通过: 汇总卡渲染与家长/摄像头数据严格分离');

  // Test 2: 全天色带铺色、刻度与图例
  console.log('测试 2: 全天色带铺色、刻度与图例渲染');
  assert.ok(timelineCard.innerHTML.includes('ribbon-track'), '应包含全天色带轨道');
  assert.ok(timelineCard.innerHTML.includes('ribbon-segment'), '色带应包含切片');
  assert.ok(timelineCard.innerHTML.includes('background:#7e91a6'), '睡眠切片应使用 #7e91a6 色调');
  assert.ok(timelineCard.innerHTML.includes('ribbon-segment-not-visible'), '不在画面切片应带有斜纹样式类');
  assert.ok(timelineCard.innerHTML.includes('0:00'), '应包含 0:00 刻度');
  assert.ok(timelineCard.innerHTML.includes('24:00'), '应包含 24:00 刻度');
  assert.ok(timelineCard.innerHTML.includes('ribbon-legend'), '应包含色带图例');
  assert.ok(timelineCard.innerHTML.includes('不在画面'), '图例中应说明不在画面');
  console.log('✓ 测试 2 通过: 全天色带铺色与图例刻度正常渲染');

  // Test 3: 点色带段展开小面板与重叠模型观察
  console.log('测试 3: 点色带某一段展示时段信息与模型描述');
  const sleepStartSec = Date.parse('2026-10-15T10:00:00Z') / 1000;
  Timeline.setObservations([
    {
      ts: sleepStartSec + 600,
      duration_seconds: 60,
      description: '宝宝在婴儿床熟睡，呼吸平稳',
      suggested_state: '睡眠',
    },
  ]);

  Timeline.selectSegment('vision:interval:12');
  assert.ok(timelineCard.innerHTML.includes('ribbon-detail-panel'), '选中切片应展开详情面板');
  assert.ok(timelineCard.innerHTML.includes('睡着'), '详情面板应显示该段状态');
  assert.ok(timelineCard.innerHTML.includes('宝宝在婴儿床熟睡，呼吸平稳'), '详情面板应列出重叠的模型观察描述');

  // 点击无观察时段，展示「这段时间没有模型描述」
  Timeline.selectSegment('vision:presence:01');
  assert.ok(timelineCard.innerHTML.includes('ribbon-detail-panel'), '切换切片应更新面板');
  assert.ok(timelineCard.innerHTML.includes('不在画面'), '详情面板应显示不在画面');
  assert.ok(timelineCard.innerHTML.includes('这段时间没有模型描述'), '无重叠观察时应显示占位提示');

  // 再次点击收起面板
  Timeline.selectSegment('vision:presence:01');
  assert.ok(!timelineCard.innerHTML.includes('ribbon-detail-panel'), '再次点击该段应收起详情面板');
  console.log('✓ 测试 3 通过: 点选色带展开与收起详情、重叠模型观察匹配正常');

  // Test 4: 时间流默认过滤规则 (不列 posture、presence，摄像头 activity 仅列 sleeping)
  console.log('测试 4: 时间流默认过滤规则');
  Timeline.setFilter('all');
  assert.ok(timelineCard.innerHTML.includes('喂奶 150 ml'), '时间线包含喂奶记录');
  assert.ok(timelineCard.innerHTML.includes('小手指跳跳舞'), '时间线包含亲子互动');
  assert.ok(timelineCard.innerHTML.includes('开心'), '亲子互动展示开心反应徽标');
  assert.ok(timelineCard.innerHTML.includes('睡着'), '时间线包含睡眠活动状态');
  assert.ok(timelineCard.innerHTML.includes('麦克风'), '哭声条目来源标签应显示麦克风');
  assert.ok(timelineCard.innerHTML.includes('source-audio'), '哭声条目应使用 source-audio 独立颜色类');
  assert.ok(!timelineCard.innerHTML.includes('仰睡'), '默认情况下 posture 睡姿应当被排除隐藏');
  assert.ok(!timelineCard.innerHTML.includes('<strong>不在画面</strong>'), '默认情况下 presence 条目应当在列表中被排除隐藏');
  // 摄像头清醒段不应在列表中出现
  assert.ok(!timelineCard.innerHTML.includes('timeline-stream-item source-vision') || !timelineCard.innerHTML.includes('<strong>清醒</strong>'), '摄像头的清醒段不应列入时间流');
  console.log('✓ 测试 4 通过: 时间线流正常呈现且默认过滤睡姿/在画面及非睡眠视觉状态');

  // Test 5: 5 个单选筛选按钮交互
  console.log('测试 5: 5 个单选筛选按钮切换过滤');
  assert.ok(timelineCard.innerHTML.includes('timeline-filters-bar'), '应包含单选筛选按钮栏');
  assert.ok(timelineCard.innerHTML.includes('data-filter="all"'), '应包含全部按钮');
  assert.ok(timelineCard.innerHTML.includes('data-filter="sleep"'), '应包含睡眠按钮');
  assert.ok(timelineCard.innerHTML.includes('data-filter="feeding"'), '应包含喂养按钮');
  assert.ok(timelineCard.innerHTML.includes('data-filter="cry"'), '应包含哭声按钮');
  assert.ok(timelineCard.innerHTML.includes('data-filter="interaction"'), '应包含互动按钮');

  // 睡眠筛选
  Timeline.setFilter('sleep');
  assert.ok(timelineCard.innerHTML.includes('睡着'), '睡眠筛选下保留睡着条目');
  assert.ok(!timelineCard.innerHTML.includes('喂奶 150 ml'), '睡眠筛选下排除喂奶');
  assert.ok(!timelineCard.innerHTML.includes('小手指跳跳舞'), '睡眠筛选下排除互动');
  assert.ok(!timelineCard.innerHTML.includes('source-audio'), '睡眠筛选下排除哭声');

  // 喂养筛选
  Timeline.setFilter('feeding');
  assert.ok(timelineCard.innerHTML.includes('喂奶 150 ml'), '喂养筛选下保留喂奶');
  assert.ok(!timelineCard.innerHTML.includes('<strong>睡着</strong>'), '喂养筛选下排除睡眠条目');

  // 哭声筛选
  Timeline.setFilter('cry');
  assert.ok(timelineCard.innerHTML.includes('source-audio'), '哭声筛选下保留哭声');
  assert.ok(!timelineCard.innerHTML.includes('<strong>睡着</strong>'), '哭声筛选下排除睡眠条目');
  assert.ok(!timelineCard.innerHTML.includes('喂奶 150 ml'), '哭声筛选下排除喂奶');

  // 互动筛选
  Timeline.setFilter('interaction');
  assert.ok(timelineCard.innerHTML.includes('小手指跳跳舞'), '互动筛选下保留互动');
  assert.ok(!timelineCard.innerHTML.includes('<strong>睡着</strong>'), '互动筛选下排除睡眠条目');
  assert.ok(!timelineCard.innerHTML.includes('喂奶 150 ml'), '互动筛选下排除喂奶');

  // 切回全部
  Timeline.setFilter('all');
  assert.ok(timelineCard.innerHTML.includes('喂奶 150 ml'), '全部筛选下恢复喂奶');
  assert.ok(timelineCard.innerHTML.includes('<strong>睡着</strong>'), '全部筛选下恢复睡着');

  // 哭声短时长展示验证
  const shortCryMock = {
    ...mockTimelineResponse,
    summary: { ...mockTimelineResponse.summary, cry_count: 1, cry_seconds: 40 },
  };
  Timeline.setMockHandler(async () => shortCryMock);
  await Timeline.loadUnifiedTimeline();
  assert.ok(timelineCard.innerHTML.includes('<1 分钟'), '不足 1 分钟时应显示 <1 分钟');
  console.log('✓ 测试 5 通过: 5 项单选筛选按钮与短时长展示正常');

  // Test 6: 摄像头不可用与截断提示
  console.log('测试 6: 摄像头不可用 (available=false) 与截断 (truncated=true) 提示');
  const alertMock = {
    ...mockTimelineResponse,
    visual: { ...mockTimelineResponse.visual, available: false },
    truncated: true,
  };
  Timeline.setMockHandler(async () => alertMock);
  await Timeline.loadUnifiedTimeline();
  assert.ok(timelineCard.innerHTML.includes('暂无数据'), '摄像头不可用时提示暂无数据');
  assert.ok(timelineCard.innerHTML.includes('记录较多，仅展示部分条目'), 'truncated 时提示仅展示部分');
  console.log('✓ 测试 6 通过: 摄像头不可用及截断提示生效');

  // Test 7: 接口异常时诚实显示错误，不伪造数据
  console.log('测试 7: 接口异常时如实提示错误，不展示示例数据');
  Timeline.setMockHandler(async () => {
    const err = new Error('统一时间线服务异常 500');
    err.status = 500;
    throw err;
  });
  await Timeline.loadUnifiedTimeline();
  assert.ok(timelineCard.innerHTML.includes('无法加载时间线'), '失败时明确提示无法加载');
  assert.ok(timelineCard.innerHTML.includes('服务异常 500'), '显示真实后端错误');
  assert.ok(!timelineCard.innerHTML.includes('喂奶 150 ml'), '接口失败时严禁展示假数据');
  console.log('✓ 测试 7 通过: 接口错误真实披露');

  console.log('\n=============================================');
  console.log('🎉 全部 7 项 Unified Timeline UI 测试通过！');
  console.log('=============================================');
}

runTimelineTests().catch(err => {
  console.error('\n❌ Timeline UI 测试失败:', err);
  process.exit(1);
});
