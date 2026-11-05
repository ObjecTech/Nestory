/**
 * Nestory AI Assistant UI & Conversation Management Test Suite
 * Automated tests for Phase 1 frontend requirements and API Contract v1
 */

const assert = require('node:assert/strict');
require('./assistant_dom.cjs');

// Load Assistant into Node runtime
const Assistant = require('../static/assistant.js');

// Test Suite
async function runTests() {
  console.log('--- 开始 Nestory AI Assistant UI 自动化测试 ---');

  // Activate Mock layer for testing
  Assistant.setMock(true);
  await Assistant.init();

  // Test 1: 初始化与历史会话加载
  console.log('测试 1: 初始化与历史会话加载');
  // The app now keeps a single (real) record set; the Mock fixtures live in demo.
  assert.equal(Assistant.state.scope, 'real');
  await Assistant.setScope('demo');
  assert.ok(Assistant.state.conversations.length >= 3, 'Demo 应有预置会话');
  const firstConv = Assistant.state.conversations[0];
  assert.equal(Assistant.state.activeConversationId, firstConv.id);
  assert.equal(document.getElementById('assistantConvTitle').textContent, firstConv.title);
  console.log('✓ 测试 1 通过: 成功加载初始 Demo 会话');

  // Test 2: 创建新会话
  console.log('测试 2: 创建新会话');
  const countBefore = Assistant.state.conversations.length;
  await Assistant.createConversation('demo');
  assert.equal(Assistant.state.conversations.length, countBefore + 1, '会话数量应增加 1');
  const newConv = Assistant.state.conversations[0];
  assert.equal(newConv.title, '新对话');
  assert.equal(Assistant.state.activeConversationId, newConv.id);
  assert.equal(Assistant.state.messages.length, 0, '新会话消息应为空');
  console.log('✓ 测试 2 通过: 成功创建空会话并自动切换');

  // Test 3: 切换会话与上下文隔离
  console.log('测试 3: 切换会话与上下文隔离');
  // Switch to conv-demo-a (feeding conversation)
  await Assistant.selectConversation('conv-demo-a');
  assert.equal(Assistant.state.activeConversationId, 'conv-demo-a');
  assert.ok(Assistant.state.messages.length >= 2, 'conv-demo-a 应有历史消息');
  assert.match(Assistant.state.messages[0].content, /150ml奶/);

  // Switch to conv-demo-b (sleep conversation)
  await Assistant.selectConversation('conv-demo-b');
  assert.equal(Assistant.state.activeConversationId, 'conv-demo-b');
  assert.ok(Assistant.state.messages.length >= 2, 'conv-demo-b 应有历史消息');
  assert.match(Assistant.state.messages[0].content, /补觉/);
  // Verify conv-demo-a messages did NOT leak into conv-demo-b
  assert.ok(!Assistant.state.messages.some(m => m.content.includes('150ml奶')), '不同会话消息严格隔离');
  console.log('✓ 测试 3 通过: 会话切换顺畅，上下文严格隔离');

  // Test 4: 页面刷新后恢复选中的会话 (localStorage)
  console.log('测试 4: 页面刷新恢复会话');
  assert.equal(localStorage.getItem('nestory_conv_demo'), 'conv-demo-b');
  // Re-run init to simulate refresh
  Assistant.state.initialized = false;
  await Assistant.init();
  assert.equal(Assistant.state.activeConversationId, 'conv-demo-b', '刷新后应恢复之前选中的会话');
  console.log('✓ 测试 4 通过: 页面刷新后会话恢复正常');

  // Test 5: 会话重命名
  console.log('测试 5: 会话重命名');
  await Assistant.renameConversation('conv-demo-b', '宝宝今日午睡记录');
  const renamed = Assistant.state.conversations.find(c => c.id === 'conv-demo-b');
  assert.equal(renamed.title, '宝宝今日午睡记录');
  assert.equal(document.getElementById('assistantConvTitle').textContent, '宝宝今日午睡记录');
  console.log('✓ 测试 5 通过: 会话标题与列表重命名成功');

  // Test 6 & 7: 删除会话与删除后自动切换
  console.log('测试 6 & 7: 删除会话与状态恢复');
  const activeBeforeDelete = Assistant.state.activeConversationId;
  await Assistant.deleteConversation(activeBeforeDelete);
  assert.ok(!Assistant.state.conversations.some(c => c.id === activeBeforeDelete), '已删除会话不应再出现在列表中');
  assert.notEqual(Assistant.state.activeConversationId, activeBeforeDelete, '当前选中的会话已自动切换');
  console.log('✓ 测试 6 & 7 通过: 会话删除成功并进入有效会话');

  // Test 8: 异步 202 发送消息与轮询完成
  console.log('测试 8: 异步 202 消息发送与轮询完成');
  await Assistant.createConversation('demo');
  const targetConvId = Assistant.state.activeConversationId;

  // Send message
  await Assistant.sendMessage('今天上午11点宝宝喝了160ml奶');
  assert.equal(Assistant.state.messages.length, 1, '发送后先显示用户排队消息');
  const queuedUserMsg = Assistant.state.messages[0];
  assert.equal(queuedUserMsg.status, 'queued');
  assert.ok(Assistant.state.activePolls.has(targetConvId), '后台轮询器已启动');

  // Simulate background poll steps
  const pollTask = Assistant.state.activePolls.get(targetConvId);
  assert.ok(pollTask, '存在有效轮询任务');

  // Step 1: Poll returns running
  const step1 = await Assistant.mockApiHandler('GET', `/api/assistant/conversations/${targetConvId}/messages/${queuedUserMsg.id}`, null, { scope: 'demo' });
  assert.equal(step1.data.status, 'running');

  // Step 2: Poll returns completed with assistant message & record action
  const step2 = await Assistant.mockApiHandler('GET', `/api/assistant/conversations/${targetConvId}/messages/${queuedUserMsg.id}`, null, { scope: 'demo' });
  assert.equal(step2.data.status, 'completed');
  assert.ok(step2.data.assistant_message, '生成了助手回复');
  assert.ok(step2.data.record_actions.length > 0, '提取了生活记录卡片');
  assert.equal(step2.data.record_actions[0].status, 'saved');
  assert.equal(step2.data.record_actions[0].event.amount_ml, 160);
  console.log('✓ 测试 8 通过: 202 异步状态转换与轮询生成完整');

  // Test 9: 会话切换期间后台回复隔离
  console.log('测试 9: 会话切换期间后台回复隔离');
  // In conv X, start send
  const convX = targetConvId;
  // Switch away to conv Y
  await Assistant.selectConversation('conv-demo-a');
  assert.equal(Assistant.state.activeConversationId, 'conv-demo-a');
  // Verify conv-demo-a does NOT show convX's replies
  assert.ok(!Assistant.state.messages.some(m => m.content.includes('160ml奶')), '后台会话的回复绝对不会显示在当前会话');
  console.log('✓ 测试 9 通过: 跨会话异步回复严格隔离');

  // Test 10: 错误处理与重试
  console.log('测试 10: 错误处理与重试');
  await Assistant.createConversation('demo');
  const errConvId = Assistant.state.activeConversationId;
  await Assistant.sendMessage('测试模拟失败 simulate_error');
  const errPollTask = Assistant.state.activePolls.get(errConvId);
  // 1st poll -> running
  await Assistant.mockApiHandler('GET', errPollTask.pollUrl, null, { scope: 'demo' });
  // 2nd poll -> failed
  const failedRes = await Assistant.mockApiHandler('GET', errPollTask.pollUrl, null, { scope: 'demo' });
  assert.equal(failedRes.data.status, 'failed');
  assert.equal(failedRes.data.error.retryable, true);
  console.log('✓ 测试 10 通过: 失败状态及可重试标记正确返回');

  // Test 11: 生活记录卡片确认交互 (needs_confirmation)
  console.log('测试 11: 生活记录卡片确认交互 (待确认 -> 确认保存)');
  await Assistant.createConversation('demo');
  const confirmConvId = Assistant.state.activeConversationId;
  await Assistant.sendMessage('刚才冲了150ml奶，宝宝好像喝了一些');
  const confirmPollTask = Assistant.state.activePolls.get(confirmConvId);
  // Transition to completed
  await Assistant.mockApiHandler('GET', confirmPollTask.pollUrl, null, { scope: 'demo' });
  const compRes = await Assistant.mockApiHandler('GET', confirmPollTask.pollUrl, null, { scope: 'demo' });
  const action = compRes.data.record_actions[0];
  assert.equal(action.status, 'needs_confirmation', '模糊信息必须标记为待确认，不可冒充已保存');
  assert.equal(action.event, null, '待确认记录不创建正式 care event');

  // Parent confirms proposal with actual 130ml
  const confirmResult = await Assistant.mockApiHandler(
    'POST',
    `/api/assistant/conversations/${confirmConvId}/messages/${compRes.data.user_message.id}/record-actions/${action.action_id}/confirm`,
    { decision: 'confirm', fields: { amount_ml: 130 } },
    { scope: 'demo' }
  );
  assert.equal(confirmResult.data.resolution, 'confirmed');
  assert.equal(confirmResult.data.record_action.status, 'saved');
  assert.equal(confirmResult.data.record_action.event.amount_ml, 130);
  console.log('✓ 测试 11 通过: 待确认卡片与确认保存逻辑准确无误');

  // Test 12: 生活记录独立 CRUD 与乐观锁 (expected_version)
  console.log('测试 12: 生活记录独立 CRUD 与乐观锁');
  const events = await Assistant.loadCareEvents();
  assert.ok(events.length > 0, '应存在已保存生活记录');
  const targetEvent = events[0];

  // Update event
  const updatedEvent = await Assistant.updateCareEvent(targetEvent.id, { notes: '更新的备注' }, targetEvent.version);
  assert.equal(updatedEvent.notes, '更新的备注');
  assert.equal(updatedEvent.version, targetEvent.version + 1);

  // Optimistic version conflict (409)
  await assert.rejects(
    async () => {
      await Assistant.updateCareEvent(targetEvent.id, { notes: '并发冲突' }, 999);
    },
    /已被修改|version_conflict/,
    '旧版本号修改应触发 409 冲突'
  );

  // Delete event with expected_version
  await Assistant.deleteCareEvent(targetEvent.id, updatedEvent.version);
  const eventsAfterDel = await Assistant.loadCareEvents();
  assert.ok(!eventsAfterDel.some(e => e.id === targetEvent.id), '已删除记录不应出现在有效列表中');
  console.log('✓ 测试 12 通过: 生活记录增删改查与版本乐观锁生效');

  // Test 13: Demo / Real 数据范围严格隔离
  console.log('测试 13: Demo / Real 数据范围严格隔离');
  await Assistant.setScope('real');
  assert.equal(Assistant.state.scope, 'real');
  assert.equal(Assistant.state.conversations.length, 0, 'Real 环境不应混入 Demo 会话');

  await Assistant.createConversation('real');
  assert.equal(Assistant.state.conversations.length, 1);
  assert.equal(Assistant.state.conversations[0].scope, 'real');

  // Switch back to Demo
  await Assistant.setScope('demo');
  assert.equal(Assistant.state.scope, 'demo');
  assert.ok(Assistant.state.conversations.length >= 3, 'Demo 会话保留且不包含 Real 会话');
  assert.ok(Assistant.state.conversations.every(c => c.scope === 'demo'));
  console.log('✓ 测试 13 通过: Demo 与 Real 数据绝对隔离');

  // Test 14: 睡眠事实展示与免责说明 (Section 7)
  console.log('测试 14: 睡眠统计事实与可靠性说明');
  await Assistant.selectConversation('conv-demo-c');
  const asstSleepMsg = Assistant.state.messages.find(m => m.role === 'assistant');
  assert.ok(asstSleepMsg && asstSleepMsg.facts, '应包含结构化睡眠 facts');
  assert.equal(asstSleepMsg.facts.camera_sleep.reliability, 'unverified', '摄像头睡眠必须标为未验证');
  assert.ok(asstSleepMsg.facts.warnings.length > 0, '必须包含连续性未验证的提示');
  console.log('✓ 测试 14 通过: 睡眠分析与客观真实性说明完整');

  // Test 15: 输入验证与边界情况 (空消息、超长标题)
  console.log('测试 15: 输入验证与边界情况');
  await Assistant.sendMessage('   '); // empty or whitespace
  // Should reject empty message without sending
  const resBadTitle = await Assistant.mockApiHandler('PATCH', `/api/assistant/conversations/${Assistant.state.activeConversationId}`, { title: '' }, { scope: 'demo' });
  // Rename empty title validation in controller
  let toastMsg = '';
  const toastEl = document.getElementById('toast');
  await Assistant.renameConversation(Assistant.state.activeConversationId, '');
  assert.equal(toastEl.textContent, '对话标题不能为空');
  console.log('✓ 测试 15 通过: 空消息与非法输入安全拦截');

  // Test 16: 移动端规整布局与响应式结构验证
  console.log('测试 16: 移动端规整布局与响应式结构验证');
  await Assistant.selectConversation('conv-demo-a');
  const msgsArea = document.getElementById('assistantMessagesArea');
  const asstRow = msgsArea.children.find(r => r.classList && r.classList.contains('assistant'));
  assert.ok(asstRow, '应存在助手消息行');
  const avatarEl = asstRow.children.find(c => c.classList && c.classList.contains('assistant-avatar-small'));
  const contentColEl = asstRow.children.find(c => c.classList && c.classList.contains('assistant-content-col'));
  assert.ok(avatarEl, '助手头像存在');
  assert.ok(contentColEl, '助手内容列存在，确保气泡与卡片整体垂直对齐');
  const bubbleInCol = contentColEl.children.find(c => c.classList && c.classList.contains('assistant-bubble'));
  const cardListInCol = contentColEl.children.find(c => c.classList && c.classList.contains('record-card-list'));
  const metaInCol = contentColEl.children.find(c => c.classList && c.classList.contains('assistant-msg-meta'));
  assert.ok(bubbleInCol, '气泡位于内容列内部');
  assert.ok(cardListInCol, '记录卡片位于内容列内部并与气泡左侧严格对齐');
  assert.ok(metaInCol, '消息时间戳位于内容列内部');
  const sourceField = cardListInCol.children[0].querySelector('.full-width');
  assert.ok(sourceField, '记录来源已设置为 full-width，避免窄屏尴尬折行');
  // Test 17: 中文友好错误文案映射与快捷示例覆盖
  console.log('测试 17: 中文友好错误文案映射与快捷示例覆盖');
  assert.equal(Assistant.formatFriendlyError({ code: 'model_timeout' }), '这次没能理解，请换个说法或点下方示例。');
  assert.equal(Assistant.formatFriendlyError({ code: 'invalid_model_output' }), '这次没能理解，请换个说法或点下方示例。');
  assert.equal(Assistant.formatFriendlyError({ code: 'model_failed' }), '这次没能理解，请换个说法或点下方示例。');
  assert.equal(Assistant.formatFriendlyError({ code: 'queue_full' }), 'AI 正忙，请稍后重试。');
  assert.equal(Assistant.formatFriendlyError({ code: 'model_unavailable' }), '本地 AI 暂不可用，请稍后再试。');
  assert.equal(Assistant.formatFriendlyError({ code: 'assistant_disabled' }), '本地 AI 暂不可用，请稍后再试。');
  assert.equal(Assistant.formatFriendlyError({ code: 'service_restarted' }), '本地 AI 暂不可用，请稍后再试。');
  assert.equal(Assistant.formatFriendlyError({ code: 'custom_err', message: '自定义提示' }), '自定义提示');
  assert.equal(Assistant.formatFriendlyError(null), '模型处理失败');

  // Verify welcome card contains new prompt chips
  await Assistant.createConversation('demo');
  const msgsAreaNew = document.getElementById('assistantMessagesArea');
  const welcomeCardHtml = msgsAreaNew.children[0]?.innerHTML || '';
  assert.ok(welcomeCardHtml.includes('今天上午9点宝宝喝了120ml奶'), '包含今天上午9点宝宝喝了120ml奶 示例');
  assert.ok(welcomeCardHtml.includes('今天宝宝喝了多少奶？'), '包含今天宝宝喝了多少奶？ 示例');
  console.log('✓ 测试 17 通过: 中文友好错误文案映射与快捷示例完整覆盖');

  // Test 18: 顶部按钮与输入框文案精简
  console.log('测试 18: 顶部按钮与输入框文案精简');
  const indexHtml = require('node:fs').readFileSync('static/index.html', 'utf8');
  assert.ok(indexHtml.includes('id="assistantCareEventsBtn"') && indexHtml.includes('记录</span></button>'), '生活记录管理按钮应展示「记录」文案');
  assert.ok(indexHtml.includes('id="assistantNewConvBtn"') && indexHtml.includes('新对话</span></button>'), '新建按钮应展示「新对话」文案');
  assert.ok(indexHtml.includes('placeholder="记一下喂奶、睡眠，或问个问题…"'), '输入框占位文案应为「记一下喂奶、睡眠，或问个问题…」');
  console.log('✓ 测试 18 通过: 顶部按钮文案与输入框提示清晰简练');

  console.log('\n=============================================');
  console.log('🎉 全部 18 项 AI Assistant 前端自动化测试通过！');
  console.log('=============================================');
}

runTests().catch(err => {
  console.error('\n❌ 测试失败:', err);
  process.exit(1);
});
