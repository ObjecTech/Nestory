/**
 * Nestory AI Assistant Frontend
 * Compliant with docs/ASSISTANT_API_CONTRACT.md (v1)
 */

(function () {
  'use strict';

  // --- Utility Helpers ---
  function $(selector, root) {
    const doc = root || (typeof document !== 'undefined' ? document : null);
    return doc && typeof doc.querySelector === 'function' ? doc.querySelector(selector) : null;
  }
  function $$(selector, root) {
    const doc = root || (typeof document !== 'undefined' ? document : null);
    return doc && typeof doc.querySelectorAll === 'function' ? [...doc.querySelectorAll(selector)] : [];
  }

  function getCsrfToken() {
    if (typeof document === 'undefined') return '';
    const match = document.cookie
      .split('; ')
      .find(row => row.startsWith('bmon_csrf='));
    return match ? decodeURIComponent(match.split('=')[1] || '') : '';
  }

  // All times are shown in the household's timezone (Asia/Shanghai), whatever
  // timezone the viewing phone or laptop is set to.
  const SHANGHAI_PARTS = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  function shanghaiParts(d) {
    const p = {};
    for (const part of SHANGHAI_PARTS.formatToParts(d)) p[part.type] = part.value;
    return p;
  }

  function formatTime(isoString) {
    if (!isoString) return '—';
    try {
      const p = shanghaiParts(new Date(isoString));
      return `${p.hour}:${p.minute}`;
    } catch {
      return isoString;
    }
  }

  function formatDateTime(isoString) {
    if (!isoString) return '—';
    try {
      const p = shanghaiParts(new Date(isoString));
      return `${p.month}-${p.day} ${p.hour}:${p.minute}`;
    } catch {
      return isoString;
    }
  }

  function formatDurationZh(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    seconds = Math.max(0, Math.floor(Number(seconds)));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (hours && minutes) return `${hours}小时 ${minutes}分钟`;
    if (hours) return `${hours}小时`;
    if (minutes) return `${minutes}分钟`;
    return `${seconds}秒`;
  }

  function showToast(msg) {
    const toast = $('#toast');
    if (!toast) {
      console.log('[Toast]', msg);
      return;
    }
    toast.textContent = msg;
    toast.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => {
      toast.hidden = true;
    }, 2800);
  }

  function generateUUID() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return 'u-' + Math.random().toString(36).slice(2, 11) + '-' + Date.now();
  }

  // --- State ---
  const assistantState = {
    scope: 'real', // single record set; Demo/Real switching removed
    conversations: [],
    activeConversationId: null,
    activeConversation: null,
    messages: [],
    messagesHasMore: false,
    messagesNextCursor: null,
    conversationsHasMore: false,
    conversationsNextCursor: null,
    pendingTurn: null,
    activePolls: new Map(), // convId -> { timer, pollUrl, userMessageId }
    isSending: false,
    isMock: false,
    timezone: 'Asia/Shanghai',
    initialized: false,
    selectionEpoch: 0,
    listEpoch: 0,
    careListEpoch: 0,
  };

  // --- Mock Service (Strictly following API Contract v1) ---
  const mockStore = {
    demo: {
      conversations: [
        {
          id: 'conv-demo-a',
          title: '今天上午11点宝宝喝了150ml奶',
          title_source: 'auto',
          scope: 'demo',
          timezone: 'Asia/Shanghai',
          created_at: '2026-10-13T03:05:00Z',
          updated_at: '2026-10-13T03:06:00Z',
          last_message_preview: '已记录本次喂奶：11:00，150 ml。',
          version: 3,
        },
        {
          id: 'conv-demo-b',
          title: '下午1点半到2点20分补觉',
          title_source: 'auto',
          scope: 'demo',
          timezone: 'Asia/Shanghai',
          created_at: '2026-10-13T06:20:00Z',
          updated_at: '2026-10-13T06:25:00Z',
          last_message_preview: '已记录宝宝午睡：13:30 至 14:20，共 50 分钟。',
          version: 2,
        },
        {
          id: 'conv-demo-c',
          title: '昨天宝宝睡了多久？',
          title_source: 'auto',
          scope: 'demo',
          timezone: 'Asia/Shanghai',
          created_at: '2026-10-13T07:00:00Z',
          updated_at: '2026-10-13T07:01:00Z',
          last_message_preview: '根据已有记录，昨天登记睡眠共 50 分钟。摄像头监测连续性尚未验证。',
          version: 2,
        },
      ],
      messages: {
        'conv-demo-a': [
          {
            id: 'msg-user-a1',
            conversation_id: 'conv-demo-a',
            role: 'user',
            content: '今天上午11点宝宝喝了150ml奶',
            created_at: '2026-10-13T03:05:00Z',
            status: 'completed',
            client_message_id: 'mock-uuid-001',
            turn_id: 'turn-a1',
          },
          {
            id: 'msg-assistant-a1',
            conversation_id: 'conv-demo-a',
            role: 'assistant',
            content: '已记录本次喂奶：11:00，150 ml。宝宝胃口不错！',
            created_at: '2026-10-13T03:06:00Z',
            status: 'completed',
            client_message_id: null,
            turn_id: 'turn-a1',
            record_actions: [
              {
                action_id: 'action-a1-0',
                operation: 'create',
                status: 'saved',
                reason_code: 'explicit_past_event',
                message: '已记录喂奶',
                event: {
                  id: 'care-demo-001',
                  event_type: 'feeding',
                  scope: 'demo',
                  occurred_at: '2026-10-13T03:00:00Z',
                  end_at: null,
                  amount_ml: 150,
                  diaper_type: null,
                  notes: '',
                  source: 'parent_chat',
                  status: 'confirmed',
                  timezone: 'Asia/Shanghai',
                  duration_seconds: null,
                  source_message_id: 'msg-user-a1',
                  source_reference_id: 'source-001',
                  version: 1,
                  created_at: '2026-10-13T03:06:00Z',
                  updated_at: '2026-10-13T03:06:00Z',
                  deleted_at: null,
                },
                proposed_event: null,
                missing_fields: [],
                target_event_id: null,
                expected_version: null,
                expires_at: null,
              },
            ],
          },
        ],
        'conv-demo-b': [
          {
            id: 'msg-user-b1',
            conversation_id: 'conv-demo-b',
            role: 'user',
            content: '下午1点半到2点20分补觉',
            created_at: '2026-10-13T06:20:00Z',
            status: 'completed',
            client_message_id: 'mock-uuid-002',
            turn_id: 'turn-b1',
          },
          {
            id: 'msg-assistant-b1',
            conversation_id: 'conv-demo-b',
            role: 'assistant',
            content: '已记录宝宝午睡：13:30 至 14:20，共 50 分钟。',
            created_at: '2026-10-13T06:21:00Z',
            status: 'completed',
            client_message_id: null,
            turn_id: 'turn-b1',
            record_actions: [
              {
                action_id: 'action-b1-0',
                operation: 'create',
                status: 'saved',
                reason_code: 'explicit_past_event',
                message: '已记录睡眠',
                event: {
                  id: 'care-demo-002',
                  event_type: 'sleep',
                  scope: 'demo',
                  occurred_at: '2026-10-13T05:30:00Z',
                  end_at: '2026-10-13T06:20:00Z',
                  amount_ml: null,
                  diaper_type: null,
                  notes: '午睡',
                  source: 'parent_chat',
                  status: 'confirmed',
                  timezone: 'Asia/Shanghai',
                  duration_seconds: 3000,
                  source_message_id: 'msg-user-b1',
                  source_reference_id: 'source-002',
                  version: 1,
                  created_at: '2026-10-13T06:21:00Z',
                  updated_at: '2026-10-13T06:21:00Z',
                  deleted_at: null,
                },
                proposed_event: null,
                missing_fields: [],
                target_event_id: null,
                expected_version: null,
                expires_at: null,
              },
            ],
          },
        ],
        'conv-demo-c': [
          {
            id: 'msg-user-c1',
            conversation_id: 'conv-demo-c',
            role: 'user',
            content: '昨天宝宝睡了多久？',
            created_at: '2026-10-13T07:00:00Z',
            status: 'completed',
            client_message_id: 'mock-uuid-003',
            turn_id: 'turn-c1',
          },
          {
            id: 'msg-assistant-c1',
            conversation_id: 'conv-demo-c',
            role: 'assistant',
            content: '根据已有记录，昨天登记睡眠共 50 分钟。摄像头监测连续性尚未验证，无法计算全天连续睡眠。',
            created_at: '2026-10-13T07:01:00Z',
            status: 'completed',
            client_message_id: null,
            turn_id: 'turn-c1',
            facts: {
              scope: 'demo',
              timezone: 'Asia/Shanghai',
              from: '2026-10-12',
              to: '2026-10-12',
              parent_sleep: {
                seconds: 3000,
                event_count: 1,
                reliability: 'parent_reported',
                source: 'parent_reported',
                method: 'clipped_interval_union',
              },
              camera_sleep: {
                seconds: null,
                reliability: 'unverified',
                source: 'camera_observation',
                observation_mode: null,
                reason_code: 'demo_visual_data_unavailable',
              },
              combined_sleep_seconds: null,
              coverage: { complete_day: false, unknown_seconds: null },
              warnings: [
                '当前为 Demo 模拟数据；摄像头监测连续性尚未验证，不作为医学临床依据。',
              ],
            },
          },
        ],
      },
      care_events: [
        {
          id: 'care-demo-001',
          event_type: 'feeding',
          scope: 'demo',
          occurred_at: '2026-10-13T03:00:00Z',
          end_at: null,
          amount_ml: 150,
          diaper_type: null,
          notes: '',
          source: 'parent_chat',
          status: 'confirmed',
          timezone: 'Asia/Shanghai',
          duration_seconds: null,
          source_message_id: 'msg-user-a1',
          source_reference_id: 'source-001',
          version: 1,
          created_at: '2026-10-13T03:06:00Z',
          updated_at: '2026-10-13T03:06:00Z',
          deleted_at: null,
        },
        {
          id: 'care-demo-002',
          event_type: 'sleep',
          scope: 'demo',
          occurred_at: '2026-10-13T05:30:00Z',
          end_at: '2026-10-13T06:20:00Z',
          amount_ml: null,
          diaper_type: null,
          notes: '午睡',
          source: 'parent_chat',
          status: 'confirmed',
          timezone: 'Asia/Shanghai',
          duration_seconds: 3000,
          source_message_id: 'msg-user-b1',
          source_reference_id: 'source-002',
          version: 1,
          created_at: '2026-10-13T06:21:00Z',
          updated_at: '2026-10-13T06:21:00Z',
          deleted_at: null,
        },
      ],
      pending_turns: {},
    },
    real: {
      conversations: [],
      messages: {},
      care_events: [],
      pending_turns: {},
    },
  };

  async function mockApiHandler(method, path, body = null, queryParams = {}) {
    let cleanPath = path;
    if (cleanPath.includes('?')) {
      const [p, q] = cleanPath.split('?');
      cleanPath = p;
      if (q) {
        const sp = new URLSearchParams(q);
        for (const [k, v] of sp.entries()) {
          if (queryParams[k] === undefined) queryParams[k] = v;
        }
      }
    }
    path = cleanPath;
    const scope = queryParams.scope || (body && body.scope) || 'demo';
    const store = mockStore[scope];
    if (!store) {
      return { status: 422, data: { api_version: '1', error: { code: 'invalid_scope', message: 'Scope must be demo or real' } } };
    }

    // 1. GET /api/assistant/conversations
    if (method === 'GET' && path === '/api/assistant/conversations') {
      const convs = store.conversations
        .map(c => ({ ...c }))
        .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      return {
        status: 200,
        data: {
          api_version: '1',
          scope,
          conversations: convs,
          next_cursor: null,
          has_more: false,
        },
      };
    }

    // 2. POST /api/assistant/conversations
    if (method === 'POST' && path === '/api/assistant/conversations') {
      const nowIso = new Date().toISOString();
      const newConv = {
        id: 'conv-' + scope + '-' + Date.now().toString(36),
        title: '新对话',
        title_source: 'default',
        scope,
        timezone: body?.timezone || 'Asia/Shanghai',
        created_at: nowIso,
        updated_at: nowIso,
        last_message_preview: null,
        version: 1,
      };
      store.conversations.unshift(newConv);
      store.messages[newConv.id] = [];
      return {
        status: 201,
        data: { api_version: '1', conversation: newConv },
      };
    }

    // 3. PATCH /api/assistant/conversations/{id}
    const patchConvMatch = path.match(/^\/api\/assistant\/conversations\/([^/?]+)$/);
    if (method === 'PATCH' && patchConvMatch) {
      const convId = patchConvMatch[1];
      const conv = store.conversations.find(c => c.id === convId);
      if (!conv) {
        return { status: 404, data: { api_version: '1', error: { code: 'conversation_not_found', message: 'Conversation not found' } } };
      }
      if (body?.expected_version && conv.version !== body.expected_version) {
        return { status: 409, data: { api_version: '1', error: { code: 'version_conflict', message: '版本冲突，请重试' } } };
      }
      conv.title = body?.title || conv.title;
      conv.title_source = 'manual';
      conv.version += 1;
      conv.updated_at = new Date().toISOString();
      return { status: 200, data: { api_version: '1', conversation: conv } };
    }

    // 4. DELETE /api/assistant/conversations/{id}
    if (method === 'DELETE' && patchConvMatch) {
      const convId = patchConvMatch[1];
      const idx = store.conversations.findIndex(c => c.id === convId);
      if (idx === -1) {
        return { status: 404, data: { api_version: '1', error: { code: 'conversation_not_found', message: 'Conversation not found' } } };
      }
      store.conversations.splice(idx, 1);
      delete store.messages[convId];
      delete store.pending_turns[convId];
      return {
        status: 200,
        data: { api_version: '1', deleted: true, conversation_id: convId, care_events_preserved: true },
      };
    }

    // 5. GET /api/assistant/conversations/{id}/messages
    const getMsgsMatch = path.match(/^\/api\/assistant\/conversations\/([^/?]+)\/messages$/);
    if (method === 'GET' && getMsgsMatch) {
      const convId = getMsgsMatch[1];
      const conv = store.conversations.find(c => c.id === convId);
      if (!conv) {
        return { status: 404, data: { api_version: '1', error: { code: 'conversation_not_found', message: 'Conversation not found' } } };
      }
      const msgs = store.messages[convId] || [];
      const pending = store.pending_turns[convId] || null;
      return {
        status: 200,
        data: {
          api_version: '1',
          conversation: { ...conv },
          messages: msgs.map(m => ({ ...m })),
          pending_turn: pending ? { ...pending } : null,
          next_cursor: null,
          has_more: false,
        },
      };
    }

    // 6. POST /api/assistant/conversations/{id}/messages
    if (method === 'POST' && getMsgsMatch) {
      const convId = getMsgsMatch[1];
      const conv = store.conversations.find(c => c.id === convId);
      if (!conv) {
        return { status: 404, data: { api_version: '1', error: { code: 'conversation_not_found', message: 'Conversation not found' } } };
      }
      const content = (body?.content || '').trim();
      if (!content) {
        return { status: 422, data: { api_version: '1', error: { code: 'validation_error', message: '消息内容不能为空' } } };
      }

      const clientMsgId = body?.client_message_id || generateUUID();
      const userMsgId = 'msg-user-' + Date.now().toString(36);
      const turnId = 'turn-' + Date.now().toString(36);
      const nowIso = new Date().toISOString();

      const userMessage = {
        id: userMsgId,
        conversation_id: convId,
        role: 'user',
        content,
        created_at: nowIso,
        status: 'queued',
        client_message_id: clientMsgId,
        turn_id: turnId,
      };

      if (!store.messages[convId]) store.messages[convId] = [];
      store.messages[convId].push(userMessage);

      // Auto update conversation title if default
      if (conv.title_source === 'default') {
        conv.title = content.slice(0, 24);
        conv.title_source = 'auto';
      }
      conv.last_message_preview = content.slice(0, 80);
      conv.updated_at = nowIso;
      conv.version += 1;

      // Setup pending turn for polling
      const pollUrl = `/api/assistant/conversations/${convId}/messages/${userMsgId}?scope=${scope}`;
      const pendingTurn = {
        turn_id: turnId,
        user_message_id: userMsgId,
        conversation_id: convId,
        content,
        created_at: nowIso,
        poll_count: 0,
        status: 'queued',
        poll_url: pollUrl,
        poll_after_ms: 1000,
      };
      store.pending_turns[convId] = pendingTurn;

      return {
        status: 202,
        data: {
          api_version: '1',
          status: 'queued',
          user_message: userMessage,
          assistant_message: null,
          record_actions: [],
          conversation: conv,
          facts: null,
          error: null,
          replayed: false,
          attempt: 1,
          poll_url: pollUrl,
          poll_after_ms: 1000,
        },
      };
    }

    // 7. GET /api/assistant/conversations/{id}/messages/{user_message_id} (Polling route)
    const pollMatch = path.match(/^\/api\/assistant\/conversations\/([^/?]+)\/messages\/([^/?]+)$/);
    if (method === 'GET' && pollMatch) {
      const [_, convId, userMsgId] = pollMatch;
      const conv = store.conversations.find(c => c.id === convId);
      if (!conv) {
        return { status: 404, data: { api_version: '1', error: { code: 'conversation_not_found', message: 'Conversation not found' } } };
      }
      const msgs = store.messages[convId] || [];
      const userMsg = msgs.find(m => m.id === userMsgId);
      if (!userMsg) {
        return { status: 404, data: { api_version: '1', error: { code: 'message_not_found', message: 'User message not found' } } };
      }

      const pending = store.pending_turns[convId];
      if (pending && pending.user_message_id === userMsgId) {
        pending.poll_count = (pending.poll_count || 0) + 1;

        // Transition states: 1st poll -> running; 2nd poll -> completed/failed
        if (pending.poll_count === 1) {
          pending.status = 'running';
          userMsg.status = 'running';
          return {
            status: 200,
            data: {
              api_version: '1',
              status: 'running',
              user_message: userMsg,
              assistant_message: null,
              record_actions: [],
              conversation: conv,
              facts: null,
              error: null,
              replayed: false,
              attempt: 1,
              poll_url: pending.poll_url,
              poll_after_ms: 1000,
            },
          };
        }

        // Completed or simulated error
        const text = pending.content || '';
        if (text.includes('模拟失败') || text.includes('simulate_error')) {
          pending.status = 'failed';
          userMsg.status = 'failed';
          delete store.pending_turns[convId];
          return {
            status: 200,
            data: {
              api_version: '1',
              status: 'failed',
              user_message: userMsg,
              assistant_message: null,
              record_actions: [],
              conversation: conv,
              facts: null,
              error: {
                code: 'model_unavailable',
                message: '本地模型推理超时或繁忙，请重试。',
                retryable: true,
                details: [],
                retry_after_seconds: 3,
              },
              replayed: false,
              attempt: 1,
              poll_url: pending.poll_url,
              poll_after_ms: null,
            },
          };
        }

        // Generate realistic mock response & record actions
        pending.status = 'completed';
        userMsg.status = 'completed';
        delete store.pending_turns[convId];

        const asstMsgId = 'msg-asst-' + Date.now().toString(36);
        let asstText = '已收到您的记录。';
        const actions = [];
        let facts = null;

        // Pattern matching for realistic baby care extraction
        const nowIso = new Date().toISOString();
        if (text.includes('好像喝了') || text.includes('冲了')) {
          // Ambiguous feeding -> needs_confirmation
          asstText = '宝宝喝了多少奶？是在什么时间喝完的呢？请在下方卡片确认或调整。';
          actions.push({
            action_id: 'action-' + Date.now().toString(36),
            operation: 'create',
            status: 'needs_confirmation',
            reason_code: 'actual_intake_unknown',
            message: '实际喝了多少、什么时间喝完？',
            event: null,
            proposed_event: {
              event_type: 'feeding',
              scope,
              occurred_at: nowIso,
              end_at: null,
              amount_ml: 120,
              diaper_type: null,
              notes: '家长聊天描述未确定量',
              timezone: 'Asia/Shanghai',
            },
            missing_fields: ['occurred_at', 'amount_ml'],
            target_event_id: null,
            expected_version: null,
            expires_at: new Date(Date.now() + 86400000).toISOString(),
          });
        } else if (text.includes('明天') || text.includes('准备') || text.includes('计划')) {
          // Future plan -> not_saved
          asstText = '这是未来计划，已为您留存备注，未作为已发生的记录保存。';
          actions.push({
            action_id: 'action-' + Date.now().toString(36),
            operation: 'none',
            status: 'not_saved',
            reason_code: 'future_plan',
            message: '这是未来计划，未写入已发生记录。',
            event: null,
            proposed_event: null,
            missing_fields: [],
            target_event_id: null,
            expected_version: null,
            expires_at: null,
          });
        } else if (text.includes('奶') || text.includes('喝')) {
          // Definite feeding -> saved
          const mlMatch = text.match(/(\d+)\s*ml/i);
          const ml = mlMatch ? parseInt(mlMatch[1], 10) : 150;
          asstText = `已记录本次喂奶：${ml} ml。`;
          const careEvent = {
            id: 'care-' + Date.now().toString(36),
            event_type: 'feeding',
            scope,
            occurred_at: nowIso,
            end_at: null,
            amount_ml: ml,
            diaper_type: null,
            notes: '',
            source: 'parent_chat',
            status: 'confirmed',
            timezone: 'Asia/Shanghai',
            duration_seconds: null,
            source_message_id: userMsgId,
            source_reference_id: 'ref-' + Date.now().toString(36),
            version: 1,
            created_at: nowIso,
            updated_at: nowIso,
            deleted_at: null,
          };
          store.care_events.push(careEvent);
          actions.push({
            action_id: 'action-' + Date.now().toString(36),
            operation: 'create',
            status: 'saved',
            reason_code: 'explicit_past_event',
            message: '已记录喂奶',
            event: careEvent,
            proposed_event: null,
            missing_fields: [],
            target_event_id: null,
            expected_version: null,
            expires_at: null,
          });
        } else if (text.includes('睡多久') || text.includes('睡眠规律') || text.includes('睡了多久')) {
          // Sleep inquiry facts
          asstText = '查询了最近的睡眠记录。由于摄像头连续性尚未完全验证，以下统计仅基于家长已确认的记录。';
          facts = {
            scope,
            timezone: 'Asia/Shanghai',
            from: '2026-10-13',
            to: '2026-10-13',
            parent_sleep: {
              seconds: 3000,
              event_count: 1,
              reliability: 'parent_reported',
              source: 'parent_reported',
              method: 'clipped_interval_union',
            },
            camera_sleep: {
              seconds: null,
              reliability: 'unverified',
              source: 'camera_observation',
              observation_mode: scope === 'real' ? 'infant' : null,
              reason_code: scope === 'real' ? 'monitoring_continuity_unverified' : 'demo_visual_data_unavailable',
            },
            combined_sleep_seconds: null,
            coverage: { complete_day: false, unknown_seconds: null },
            warnings: [
              '摄像头监测连续性尚未验证，无法可靠计算全天无缝睡眠。',
            ],
          };
        } else if (text.includes('睡')) {
          // Sleep event -> saved
          asstText = '已为您记录这段睡眠。';
          const careEvent = {
            id: 'care-' + Date.now().toString(36),
            event_type: 'sleep',
            scope,
            occurred_at: new Date(Date.now() - 3600000).toISOString(),
            end_at: nowIso,
            amount_ml: null,
            diaper_type: null,
            notes: '宝宝小睡',
            source: 'parent_chat',
            status: 'confirmed',
            timezone: 'Asia/Shanghai',
            duration_seconds: 3600,
            source_message_id: userMsgId,
            source_reference_id: 'ref-' + Date.now().toString(36),
            version: 1,
            created_at: nowIso,
            updated_at: nowIso,
            deleted_at: null,
          };
          store.care_events.push(careEvent);
          actions.push({
            action_id: 'action-' + Date.now().toString(36),
            operation: 'create',
            status: 'saved',
            reason_code: 'explicit_past_event',
            message: '已记录睡眠',
            event: careEvent,
            proposed_event: null,
            missing_fields: [],
            target_event_id: null,
            expected_version: null,
            expires_at: null,
          });
        } else if (text.includes('尿布') || text.includes('便') || text.includes('嘘嘘')) {
          // Diaper event
          const isDirty = text.includes('便') || text.includes('臭');
          const diaperType = isDirty ? 'dirty' : 'wet';
          asstText = `已记录换纸尿裤（${isDirty ? '便便' : '湿'}）。`;
          const careEvent = {
            id: 'care-' + Date.now().toString(36),
            event_type: 'diaper',
            scope,
            occurred_at: nowIso,
            end_at: null,
            amount_ml: null,
            diaper_type: diaperType,
            notes: '',
            source: 'parent_chat',
            status: 'confirmed',
            timezone: 'Asia/Shanghai',
            duration_seconds: null,
            source_message_id: userMsgId,
            source_reference_id: 'ref-' + Date.now().toString(36),
            version: 1,
            created_at: nowIso,
            updated_at: nowIso,
            deleted_at: null,
          };
          store.care_events.push(careEvent);
          actions.push({
            action_id: 'action-' + Date.now().toString(36),
            operation: 'create',
            status: 'saved',
            reason_code: 'explicit_past_event',
            message: '已记录换尿布',
            event: careEvent,
            proposed_event: null,
            missing_fields: [],
            target_event_id: null,
            expected_version: null,
            expires_at: null,
          });
        } else {
          asstText = '我是 Nestory 本地育儿助手。可以告诉我宝宝的喂奶、换尿布或睡眠情况，我将自动帮您整理并记录。';
        }

        const asstMessage = {
          id: asstMsgId,
          conversation_id: convId,
          role: 'assistant',
          content: asstText,
          created_at: new Date(Date.now() + 50).toISOString(),
          status: 'completed',
          client_message_id: null,
          turn_id: userMsg.turn_id,
          record_actions: actions,
          facts,
        };
        msgs.push(asstMessage);

        conv.last_message_preview = asstText.slice(0, 80);
        conv.updated_at = asstMessage.created_at;
        conv.version += 1;

        return {
          status: 200,
          data: {
            api_version: '1',
            status: 'completed',
            user_message: userMsg,
            assistant_message: asstMessage,
            record_actions: actions,
            conversation: conv,
            facts,
            error: null,
            replayed: false,
            attempt: 1,
            poll_url: pending.poll_url,
            poll_after_ms: null,
          },
        };
      }

      // Check if already completed previously
      const asstMsg = msgs.find(m => m.turn_id === userMsg.turn_id && m.role === 'assistant');
      return {
        status: 200,
        data: {
          api_version: '1',
          status: 'completed',
          user_message: userMsg,
          assistant_message: asstMsg || null,
          record_actions: asstMsg?.record_actions || [],
          conversation: conv,
          facts: asstMsg?.facts || null,
          error: null,
          replayed: true,
          attempt: 1,
          poll_url: `/api/assistant/conversations/${convId}/messages/${userMsgId}?scope=${scope}`,
          poll_after_ms: null,
        },
      };
    }

    // 8. POST /api/assistant/conversations/{id}/messages/{user_message_id}/record-actions/{action_id}/confirm
    const confirmMatch = path.match(/^\/api\/assistant\/conversations\/([^/?]+)\/messages\/([^/?]+)\/record-actions\/([^/?]+)\/confirm$/);
    if (method === 'POST' && confirmMatch) {
      const [_, convId, userMsgId, actionId] = confirmMatch;
      const msgs = store.messages[convId] || [];
      const asstMsg = msgs.find(m => m.role === 'assistant' && (m.record_actions || []).some(a => a.action_id === actionId));
      if (!asstMsg) {
        return { status: 404, data: { api_version: '1', error: { code: 'action_not_found', message: 'Action not found' } } };
      }
      const action = asstMsg.record_actions.find(a => a.action_id === actionId);

      if (body?.decision === 'dismiss') {
        action.status = 'not_saved';
        action.reason_code = 'parent_dismissed';
        action.event = null;
        return {
          status: 200,
          data: {
            api_version: '1',
            resolution: 'dismissed',
            record_action: action,
          },
        };
      }

      // Confirm: create/update care event
      const nowIso = new Date().toISOString();
      const fields = body?.fields || {};
      const newEvent = {
        id: 'care-' + Date.now().toString(36),
        event_type: fields.event_type || action.proposed_event?.event_type || 'feeding',
        scope,
        occurred_at: fields.occurred_at || action.proposed_event?.occurred_at || nowIso,
        end_at: fields.end_at || action.proposed_event?.end_at || null,
        amount_ml: fields.amount_ml != null ? Number(fields.amount_ml) : (action.proposed_event?.amount_ml || 120),
        diaper_type: fields.diaper_type || action.proposed_event?.diaper_type || null,
        notes: fields.notes || action.proposed_event?.notes || '家长确认保存',
        source: 'parent_chat',
        status: 'confirmed',
        timezone: 'Asia/Shanghai',
        duration_seconds: null,
        source_message_id: userMsgId,
        source_reference_id: 'ref-' + Date.now().toString(36),
        version: 1,
        created_at: nowIso,
        updated_at: nowIso,
        deleted_at: null,
      };
      store.care_events.push(newEvent);

      action.status = 'saved';
      action.reason_code = 'parent_confirmed';
      action.message = '已确认并保存记录';
      action.event = newEvent;
      action.proposed_event = null;

      return {
        status: 200,
        data: {
          api_version: '1',
          resolution: 'confirmed',
          record_action: action,
        },
      };
    }

    // 9. GET /api/assistant/care-events
    if (method === 'GET' && path === '/api/assistant/care-events') {
      let events = store.care_events.filter(e => e.status === 'confirmed');
      if (queryParams.event_type && queryParams.event_type !== 'all') {
        events = events.filter(e => e.event_type === queryParams.event_type);
      }
      events.sort((a, b) => new Date(b.occurred_at) - new Date(a.occurred_at));
      return {
        status: 200,
        data: {
          api_version: '1',
          scope,
          timezone: 'Asia/Shanghai',
          from: queryParams.from || '2026-10-13',
          to: queryParams.to || '2026-10-13',
          events: events.map(e => ({ ...e })),
          next_cursor: null,
          has_more: false,
        },
      };
    }

    // 10. POST /api/assistant/care-events
    if (method === 'POST' && path === '/api/assistant/care-events') {
      const nowIso = new Date().toISOString();
      const newEvent = {
        id: 'care-manual-' + Date.now().toString(36),
        event_type: body?.event_type || 'feeding',
        scope,
        occurred_at: body?.occurred_at || nowIso,
        end_at: body?.end_at || null,
        amount_ml: body?.amount_ml ? Number(body.amount_ml) : null,
        diaper_type: body?.diaper_type || null,
        notes: body?.notes || '',
        source: 'parent_manual',
        status: 'confirmed',
        timezone: body?.timezone || 'Asia/Shanghai',
        duration_seconds: body?.end_at && body?.occurred_at
          ? Math.max(0, Math.floor((new Date(body.end_at) - new Date(body.occurred_at)) / 1000))
          : null,
        source_message_id: null,
        source_reference_id: 'ref-manual-' + Date.now().toString(36),
        version: 1,
        created_at: nowIso,
        updated_at: nowIso,
        deleted_at: null,
      };
      store.care_events.push(newEvent);
      return {
        status: 201,
        data: { api_version: '1', replayed: false, event: newEvent },
      };
    }

    // 11. PATCH /api/assistant/care-events/{id}
    const patchEventMatch = path.match(/^\/api\/assistant\/care-events\/([^/?]+)$/);
    if (method === 'PATCH' && patchEventMatch) {
      const eventId = patchEventMatch[1];
      const ev = store.care_events.find(e => e.id === eventId && e.status === 'confirmed');
      if (!ev) {
        return { status: 404, data: { api_version: '1', error: { code: 'event_not_found', message: 'Event not found' } } };
      }
      if (body?.expected_version && ev.version !== body.expected_version) {
        return { status: 409, data: { api_version: '1', error: { code: 'version_conflict', message: '生活记录已被修改，请刷新重试' } } };
      }
      if (body?.occurred_at) ev.occurred_at = body.occurred_at;
      if (body?.end_at !== undefined) ev.end_at = body.end_at;
      if (body?.amount_ml !== undefined) ev.amount_ml = body.amount_ml ? Number(body.amount_ml) : null;
      if (body?.diaper_type !== undefined) ev.diaper_type = body.diaper_type;
      if (body?.notes !== undefined) ev.notes = body.notes;
      if (ev.end_at && ev.occurred_at) {
        ev.duration_seconds = Math.max(0, Math.floor((new Date(ev.end_at) - new Date(ev.occurred_at)) / 1000));
      }
      ev.version += 1;
      ev.updated_at = new Date().toISOString();
      return { status: 200, data: { api_version: '1', event: { ...ev } } };
    }

    // 12. DELETE /api/assistant/care-events/{id}
    if (method === 'DELETE' && patchEventMatch) {
      const eventId = patchEventMatch[1];
      const ev = store.care_events.find(e => e.id === eventId && e.status === 'confirmed');
      if (!ev) {
        return { status: 404, data: { api_version: '1', error: { code: 'event_not_found', message: 'Event not found' } } };
      }
      const expVer = queryParams.expected_version ? Number(queryParams.expected_version) : null;
      if (expVer && ev.version !== expVer) {
        return { status: 409, data: { api_version: '1', error: { code: 'version_conflict', message: '版本不匹配，请刷新重试' } } };
      }
      ev.status = 'deleted';
      ev.deleted_at = new Date().toISOString();
      ev.version += 1;
      return {
        status: 200,
        data: { api_version: '1', deleted: true, event_id: eventId, version: ev.version, deleted_at: ev.deleted_at },
      };
    }

    // 13. GET /api/assistant/sleep-summary
    if (method === 'GET' && path === '/api/assistant/sleep-summary') {
      return {
        status: 200,
        data: {
          api_version: '1',
          scope,
          timezone: 'Asia/Shanghai',
          from: queryParams.from || '2026-10-13',
          to: queryParams.to || '2026-10-13',
          parent_sleep: {
            seconds: 3000,
            event_count: 1,
            reliability: 'parent_reported',
            source: 'parent_reported',
            method: 'clipped_interval_union',
          },
          camera_sleep: {
            seconds: null,
            reliability: 'unverified',
            source: 'camera_observation',
            observation_mode: scope === 'real' ? 'infant' : null,
            reason_code: scope === 'real' ? 'monitoring_continuity_unverified' : 'demo_visual_data_unavailable',
          },
          combined_sleep_seconds: null,
          coverage: { complete_day: false, unknown_seconds: null },
          warnings: [
            '摄像头监测连续性尚未验证，无法可靠计算全天连续睡眠。',
          ],
        },
      };
    }

    return { status: 404, data: { api_version: '1', error: { code: 'route_not_found', message: `Not found: ${method} ${path}` } } };
  }

  // Mock is opt-in on loopback development pages or in CommonJS tests.
  const mockAllowed = typeof module !== 'undefined' && !!module.exports ||
    (typeof window !== 'undefined' && ['127.0.0.1', 'localhost', '[::1]'].includes(window.location.hostname) &&
      new URLSearchParams(window.location.search).get('assistant_mock') === '1');
  assistantState.isMock = !!(mockAllowed && typeof module === 'undefined');

  async function assistantApi(path, options = {}) {
    const method = options.method || 'GET';
    const urlObj = new URL(path, typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
    if (urlObj.origin !== (typeof window !== 'undefined' ? window.location.origin : 'http://localhost') ||
        !urlObj.pathname.startsWith('/api/assistant/')) throw new Error('Assistant API 地址无效');
    if (assistantState.isMock && mockAllowed) {
      const res = await mockApiHandler(method, urlObj.pathname, options.body ? JSON.parse(options.body) : null,
        Object.fromEntries(urlObj.searchParams.entries()));
      if (res.status < 400) return res.data;
      const err = new Error(res.data?.error?.message || `请求失败 (${res.status})`);
      Object.assign(err, res.data?.error, {status: res.status, response: res.data});
      throw err;
    }
    let response;
    try {
      response = await fetch(urlObj.pathname + urlObj.search, {
        ...options, method, cache: 'no-store', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', ...(options.headers || {}),
          ...(method !== 'GET' ? {'X-CSRF-Token': getCsrfToken()} : {})},
      });
    } catch {
      const err = new Error('连接失败，请检查网络后重试；本次操作未确认保存。');
      err.code = 'network_error'; err.retryable = true;
      throw err;
    }
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const messages = {401: '登录已失效，请重新登录', 404: '请求的对话或记录不存在，请刷新列表。'};
      const err = new Error(messages[response.status] || body?.error?.message || `请求失败 (${response.status})`);
      Object.assign(err, body?.error, {status: response.status, response: body,
        message: messages[response.status] || body?.error?.message || `请求失败 (${response.status})`});
      if (response.status === 401 && typeof window !== 'undefined') window.location.assign('/login');
      throw err;
    }
    if (!body || body.api_version !== '1') throw new Error('服务器响应无效，操作结果未确认。');
    return body;
  }

  function updateMockBanner() {
    const banner = $('#assistantMockBanner');
    if (banner) banner.hidden = !assistantState.isMock;
  }

  // --- Conversations Management ---
  async function loadConversations(scope = assistantState.scope, autoSelectFirst = true) {
    const epoch = ++assistantState.listEpoch;
    try {
      const data = await assistantApi(`/api/assistant/conversations?scope=${scope}&limit=50`);
      if (scope !== assistantState.scope || epoch !== assistantState.listEpoch) return;
      assistantState.conversations = data.conversations || [];
      assistantState.conversationsHasMore = !!data.has_more;
      assistantState.conversationsNextCursor = data.next_cursor || null;

      renderConversationList();

      if (autoSelectFirst || !assistantState.conversations.some(c => c.id === assistantState.activeConversationId)) {
        // Try to restore saved conversation ID from localStorage
        const savedId = typeof localStorage !== 'undefined' ? localStorage.getItem(`nestory_conv_${scope}`) : null;
        if (savedId && !assistantState.conversations.some(c => c.id === savedId)) {
          await selectConversation(savedId);
          if (scope !== assistantState.scope || epoch !== assistantState.listEpoch) return;
          if (assistantState.activeConversationId === savedId) return;
        }
        const target = assistantState.conversations.find(c => c.id === savedId) || assistantState.conversations[0];
        if (target) {
          await selectConversation(target.id);
        } else {
          assistantState.activeConversationId = null;
          assistantState.activeConversation = null;
          assistantState.messages = [];
          renderMessages();
          updateHeaderTitle();
        }
      }
    } catch (err) {
      console.error('[Assistant] Failed to load conversations:', err);
      showToast(err.message || '加载会话列表失败');
    }
  }

  async function selectConversation(convId) {
    if (!convId) return;
    const scope = assistantState.scope;
    const epoch = ++assistantState.selectionEpoch;
    assistantState.activeConversationId = convId;
    assistantState.messages = [];
    assistantState.messagesHasMore = false;
    assistantState.messagesNextCursor = null;
    assistantState.pendingTurn = null;
    renderMessages();
    updateBusyIndicator();
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(`nestory_conv_${assistantState.scope}`, convId);
    }

    const conv = assistantState.conversations.find(c => c.id === convId);
    assistantState.activeConversation = conv || null;
    updateHeaderTitle();
    renderConversationList();

    // Load messages
    try {
      const data = await assistantApi(`/api/assistant/conversations/${convId}/messages?scope=${scope}&limit=50`);
      // Double check user didn't switch to another conversation during fetch
      const messages = data.messages || [];
      await hydrateMessages(convId, scope, messages);
      if (assistantState.activeConversationId !== convId || assistantState.scope !== scope || assistantState.selectionEpoch !== epoch) return;
      const current = assistantState.conversations.find(c => c.id === convId);
      assistantState.activeConversation = current && current.version > (data.conversation?.version || 0) ? current : data.conversation || conv;
      if (!assistantState.conversations.some(c => c.id === convId) && data.conversation) {
        assistantState.conversations.push(data.conversation);
        renderConversationList();
      }
      updateHeaderTitle();

      assistantState.messages = messages;
      assistantState.messagesHasMore = !!data.has_more;
      assistantState.messagesNextCursor = data.next_cursor || null;
      assistantState.pendingTurn = data.pending_turn || null;

      renderMessages();

      // If there is an active pending turn for this conversation, ensure polling is running
      if (assistantState.pendingTurn && !assistantState.activePolls.has(convId)) {
        startPolling(convId, assistantState.pendingTurn.user_message_id, assistantState.pendingTurn.poll_url, assistantState.pendingTurn.poll_after_ms || 1500);
      }
    } catch (err) {
      console.error(`[Assistant] Failed to load messages for ${convId}:`, err);
      if (assistantState.scope !== scope || assistantState.selectionEpoch !== epoch) return;
      if (err.status === 404) {
        stopPolling(convId);
        assistantState.activeConversationId = null;
        assistantState.activeConversation = null;
        localStorage.removeItem(`nestory_conv_${scope}`);
        localStorage.removeItem(`nestory_request_${scope}_${convId}`);
        updateHeaderTitle();
      }
      showToast(err.message || '加载消息失败');
    }
  }

  function attachTurn(messages, turn) {
    const user = messages.find(m => m.id === turn.user_message?.id);
    if (user) Object.assign(user, turn.user_message, {error: turn.error});
    const reply = messages.find(m => m.id === turn.assistant_message?.id);
    if (reply) Object.assign(reply, turn.assistant_message, {record_actions: turn.record_actions || [],
      facts: turn.facts || null, user_message_id: turn.user_message?.id});
  }

  async function hydrateMessages(convId, scope, messages) {
    const users = messages.filter(m => m.role === 'user' && ['completed', 'failed'].includes(m.status));
    for (let i = 0; i < users.length; i += 4) {
      await Promise.all(users.slice(i, i + 4).map(async user => {
        const turn = await assistantApi(`/api/assistant/conversations/${convId}/messages/${user.id}?scope=${scope}`);
        attachTurn(messages, turn);
      }));
    }
  }

  async function loadMoreConversations() {
    const scope = assistantState.scope, epoch = assistantState.listEpoch;
    const cursor = assistantState.conversationsNextCursor;
    if (!cursor) return;
    try {
      const data = await assistantApi(`/api/assistant/conversations?scope=${scope}&limit=50&cursor=${encodeURIComponent(cursor)}`);
      if (scope !== assistantState.scope || epoch !== assistantState.listEpoch) return;
      for (const conv of data.conversations || []) {
        if (!assistantState.conversations.some(c => c.id === conv.id)) assistantState.conversations.push(conv);
      }
      assistantState.conversationsHasMore = !!data.has_more;
      assistantState.conversationsNextCursor = data.next_cursor || null;
      renderConversationList();
    } catch (err) { showToast(err.message); }
  }

  async function loadOlderMessages() {
    const scope = assistantState.scope, convId = assistantState.activeConversationId, epoch = assistantState.selectionEpoch;
    const cursor = assistantState.messagesNextCursor;
    if (!cursor || !convId) return;
    try {
      const data = await assistantApi(`/api/assistant/conversations/${convId}/messages?scope=${scope}&limit=50&cursor=${encodeURIComponent(cursor)}`);
      const older = data.messages || [];
      await hydrateMessages(convId, scope, older);
      if (scope !== assistantState.scope || convId !== assistantState.activeConversationId || epoch !== assistantState.selectionEpoch) return;
      assistantState.messages = [...older, ...assistantState.messages].filter((m,i,all)=>all.findIndex(x=>x.id===m.id)===i);
      assistantState.messagesHasMore = !!data.has_more;
      assistantState.messagesNextCursor = data.next_cursor || null;
      renderMessages();
    } catch (err) { showToast(err.message); }
  }

  async function createConversation(scope = assistantState.scope) {
    try {
      const data = await assistantApi('/api/assistant/conversations', {
        method: 'POST',
        body: JSON.stringify({ scope, timezone: assistantState.timezone }),
      });
      if (scope !== assistantState.scope) return;
      const newConv = data.conversation;
      if (!newConv) throw new Error('未能创建新会话');

      assistantState.listEpoch++;
      assistantState.conversations.unshift(newConv);
      renderConversationList();
      await selectConversation(newConv.id);
      closeDrawer();
      showToast('已新建对话');

      const input = $('#assistantInput');
      if (input) input.focus();
    } catch (err) {
      console.error('[Assistant] Failed to create conversation:', err);
      showToast(err.message || '创建对话失败');
    }
  }

  async function renameConversation(convId, newTitle) {
    newTitle = (newTitle || '').trim();
    if (!newTitle) {
      showToast('对话标题不能为空');
      return;
    }
    if (newTitle.length > 80) {
      showToast('标题最多 80 个字');
      return;
    }

    const scope = assistantState.scope;
    try {
      const data = await assistantApi(`/api/assistant/conversations/${convId}?scope=${scope}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: newTitle }),
      });
      if (scope !== assistantState.scope) return;
      const updated = data.conversation;
      assistantState.listEpoch++;
      const idx = assistantState.conversations.findIndex(c => c.id === convId);
      if (idx !== -1) assistantState.conversations[idx] = updated;

      if (assistantState.activeConversationId === convId) {
        assistantState.activeConversation = updated;
        updateHeaderTitle();
      }
      renderConversationList();
      showToast('对话重命名成功');
    } catch (err) {
      console.error('[Assistant] Failed to rename conversation:', err);
      showToast(err.message || '重命名失败');
    }
  }

  async function deleteConversation(convId) {
    if (!convId) return;

    const scope = assistantState.scope;

    try {
      await assistantApi(`/api/assistant/conversations/${convId}?scope=${scope}`, {
        method: 'DELETE',
      });

      stopPolling(convId);
      if (typeof localStorage !== 'undefined') localStorage.removeItem(`nestory_request_${scope}_${convId}`);
      if (scope !== assistantState.scope) return;
      if (typeof localStorage !== 'undefined' && localStorage.getItem(`nestory_conv_${scope}`) === convId) localStorage.removeItem(`nestory_conv_${scope}`);
      assistantState.listEpoch++;
      assistantState.conversations = assistantState.conversations.filter(c => c.id !== convId);
      renderConversationList();
      showToast('对话已删除，生活记录已妥善保留');

      if (assistantState.activeConversationId === convId) {
        assistantState.selectionEpoch++;
        if (assistantState.conversations.length > 0) {
          await selectConversation(assistantState.conversations[0].id);
        } else {
          assistantState.activeConversationId = null;
          assistantState.activeConversation = null;
          assistantState.messages = [];
          renderMessages();
          updateHeaderTitle();
        }
      }
    } catch (err) {
      console.error('[Assistant] Failed to delete conversation:', err);
      showToast(err.message || '删除会话失败');
    }
  }

  // --- Message Sending & Polling ---
  async function sendMessage(content, retry = false, clientMessageId = null) {
    content = (content || '').trim();
    if (!content || assistantState.isSending || content.length > 4000) return;

    // Ensure we have an active conversation
    if (!assistantState.activeConversationId) {
      await createConversation(assistantState.scope);
      if (!assistantState.activeConversationId) return;
    }

    const scope = assistantState.scope;
    const convId = assistantState.activeConversationId;
    const draft = $('#assistantInput')?.value;
    const requestKey = `nestory_request_${scope}_${convId}`;
    let previous;
    try { previous = JSON.parse(localStorage.getItem(requestKey) || 'null'); } catch {}
    const clientMsgId = clientMessageId || (previous?.content === content ? previous.client_message_id : null) || generateUUID();
    const request = {content, client_message_id: clientMsgId,
      timezone: previous?.content === content ? previous.timezone : assistantState.timezone, retry: !!retry};
    if (typeof localStorage !== 'undefined') localStorage.setItem(requestKey, JSON.stringify(request));

    assistantState.isSending = true;
    updateSendButtonState();

    try {
      const res = await assistantApi(`/api/assistant/conversations/${convId}/messages?scope=${scope}`, {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (typeof localStorage !== 'undefined') localStorage.removeItem(requestKey);
      // Post returns 202 with status: queued
      if (scope !== assistantState.scope || !assistantState.conversations.some(c => c.id === convId)) return;
      const userMsg = res.user_message;
      if (userMsg && convId === assistantState.activeConversationId) {
        // If not already in messages list, add it
        const exists = assistantState.messages.find(m => m.id === userMsg.id || m.client_message_id === clientMsgId);
        if (!exists) {
          assistantState.messages.push(userMsg);
        } else {
          Object.assign(exists, userMsg);
        }
        renderMessages();
        scrollToBottom();
      }

      // Update conversation in list
      if (res.conversation) {
        const cIdx = assistantState.conversations.findIndex(c => c.id === convId);
        if (cIdx !== -1) assistantState.conversations[cIdx] = res.conversation;
        if (convId === assistantState.activeConversationId) {
          assistantState.activeConversation = res.conversation;
          updateHeaderTitle();
        }
        renderConversationList();
      }

      // Clear input
      const textarea = $('#assistantInput');
      if (textarea && !retry && convId === assistantState.activeConversationId && textarea.value === draft) {
        textarea.value = '';
        textarea.style.height = 'auto';
      }

      // Start background polling
      startPolling(convId, userMsg.id, res.poll_url, res.poll_after_ms || 1500);

    } catch (err) {
      console.error('[Assistant] Failed to send message:', err);
      showToast(err.message || '发送失败');
    } finally {
      assistantState.isSending = false;
      updateSendButtonState();
    }
  }

  function startPolling(convId, userMsgId, pollUrl, intervalMs = 1500) {
    stopPolling(convId);

    const pollTask = {
      convId,
      userMsgId,
      pollUrl,
      timer: null,
      active: true,
    };
    assistantState.activePolls.set(convId, pollTask);
    updateBusyIndicator();

    const executePoll = async () => {
      if (!pollTask.active) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        // Postpone polling while hidden
        pollTask.timer = setTimeout(executePoll, 2500);
        return;
      }

      try {
        const res = await assistantApi(pollUrl);
        if (!pollTask.active) return;

        const { status, user_message, assistant_message, record_actions, conversation, error } = res;

        // If this poll belongs to the CURRENT active conversation, update UI immediately!
        if (assistantState.activeConversationId === convId) {
          // Update user message status
          if (user_message) {
            const uMsg = assistantState.messages.find(m => m.id === user_message.id);
            if (uMsg) Object.assign(uMsg, user_message);
          }

          if (status === 'completed') {
            stopPolling(convId);
            if (assistant_message) {
              const exists = assistantState.messages.find(m => m.id === assistant_message.id);
              if (!exists) {
                // Attach actions & facts
                assistant_message.user_message_id = user_message?.id;
                assistant_message.record_actions = record_actions || [];
                assistant_message.facts = res.facts || null;
                assistantState.messages.push(assistant_message);
              }
            }
            if (conversation) {
              assistantState.activeConversation = conversation;
              updateHeaderTitle();
            }
            renderMessages();
            scrollToBottom();
          } else if (status === 'failed') {
            stopPolling(convId);
            if (user_message) {
              const uMsg = assistantState.messages.find(m => m.id === user_message.id);
              if (uMsg) {
                uMsg.status = 'failed';
                uMsg.error = error;
              }
            }
            renderMessages();
            showToast(formatFriendlyError(error));
          } else {
            // queued or running
            renderMessages();
            pollTask.timer = setTimeout(executePoll, res.poll_after_ms || intervalMs);
          }
        } else {
          // Poll belongs to a BACKGROUND conversation (switched away)!
          // Strict isolation: do not touch assistantState.messages DOM!
          if (status === 'completed' || status === 'failed') {
            stopPolling(convId);
          } else {
            pollTask.timer = setTimeout(executePoll, res.poll_after_ms || intervalMs);
          }
        }

        // Always update conversation preview in drawer if updated
        if (conversation) {
          const cIdx = assistantState.conversations.findIndex(c => c.id === convId);
          if (cIdx !== -1) assistantState.conversations[cIdx] = conversation;
          renderConversationList();
        }

      } catch (err) {
        if (!pollTask.active) return;
        console.warn(`[Assistant] Poll error for ${convId}:`, err);
        if (assistantState.activeConversationId === convId) showToast(err.message || '连接失败，无法获取处理结果');
        if (err.status && err.status < 500) {
          stopPolling(convId);
          if (err.status === 404 && assistantState.activeConversationId === convId) {
            assistantState.activeConversationId = null;
            assistantState.activeConversation = null;
            assistantState.messages = [];
            assistantState.selectionEpoch++;
            localStorage.removeItem(`nestory_conv_${assistantState.scope}`);
            localStorage.removeItem(`nestory_request_${assistantState.scope}_${convId}`);
            renderMessages(); updateHeaderTitle();
          }
        } else {
          pollTask.timer = setTimeout(executePoll, 2500);
        }
      }
    };

    pollTask.timer = setTimeout(executePoll, intervalMs);
  }

  function stopPolling(convId) {
    if (assistantState.activePolls.has(convId)) {
      const task = assistantState.activePolls.get(convId);
      task.active = false;
      clearTimeout(task.timer);
      assistantState.activePolls.delete(convId);
      updateBusyIndicator();
    }
  }

  // --- Proposal Confirmation ---
  async function confirmRecordAction(actionId, userMsgId, decision, fields = {}) {
    const convId = assistantState.activeConversationId;
    const scope = assistantState.scope;
    if (!convId) return;

    try {
      const res = await assistantApi(
        `/api/assistant/conversations/${convId}/messages/${userMsgId}/record-actions/${actionId}/confirm?scope=${scope}`,
        {
          method: 'POST',
          body: JSON.stringify({ decision, fields }),
        }
      );

      if (scope !== assistantState.scope || convId !== assistantState.activeConversationId) return;
      if (decision === 'confirm' && !['saved', 'updated', 'deleted'].includes(res.record_action?.status)) throw new Error('保存结果未确认');
      showToast(decision === 'confirm' ? '已确认生活记录变更' : '已忽略该记录');

      // Update in active messages
      for (const msg of assistantState.messages) {
        if (msg.record_actions) {
          const act = msg.record_actions.find(a => a.action_id === actionId);
          if (act) {
            Object.assign(act, res.record_action);
            break;
          }
        }
      }
      renderMessages();

    } catch (err) {
      console.error('[Assistant] Failed to confirm action:', err);
      showToast(err.message || '确认记录失败');
    }
  }

  // --- Care Events Management (CRUD) ---
  async function loadCareEvents(from, to, eventType = 'all', cursor = null) {
    try {
      const params = new URLSearchParams({
        scope: assistantState.scope,
        timezone: assistantState.timezone,
        limit: '50',
      });
      if (cursor) params.set('cursor', cursor);
      if (from) params.set('from', from);
      if (to) params.set('to', to);
      if (eventType && eventType !== 'all') {
        params.set('event_type', eventType);
      }

      const data = await assistantApi(`/api/assistant/care-events?${params.toString()}`);
      return Object.assign(data.events || [], {has_more: !!data.has_more, next_cursor: data.next_cursor || null});
    } catch (err) {
      console.error('[Assistant] Failed to load care events:', err);
      showToast(err.message || '加载生活记录失败');
      throw err;
    }
  }

  async function updateCareEvent(eventId, fields, expectedVersion) {
    try {
      const data = await assistantApi(`/api/assistant/care-events/${eventId}?scope=${assistantState.scope}`, {
        method: 'PATCH',
        body: JSON.stringify({
          expected_version: expectedVersion,
          ...fields,
        }),
      });
      if (!data.event?.id || data.event.status !== 'confirmed') throw new Error('保存结果未确认');
      showToast('生活记录更新成功');
      return data.event;
    } catch (err) {
      console.error('[Assistant] Failed to update care event:', err);
      showToast(err.message || '修改失败');
      throw err;
    }
  }

  async function deleteCareEvent(eventId, expectedVersion) {
    try {
      await assistantApi(`/api/assistant/care-events/${eventId}?scope=${assistantState.scope}&expected_version=${expectedVersion}`, {
        method: 'DELETE',
      });
      showToast('生活记录已删除');
    } catch (err) {
      console.error('[Assistant] Failed to delete care event:', err);
      showToast(err.message || '删除记录失败');
      throw err;
    }
  }

  async function createManualCareEvent(fields) {
    try {
      const data = await assistantApi('/api/assistant/care-events', {
        method: 'POST',
        body: JSON.stringify({
          scope: assistantState.scope,
          timezone: assistantState.timezone,
          client_event_id: generateUUID(),
          ...fields,
        }),
      });
      if (!data.event?.id || data.event.status !== 'confirmed') throw new Error('保存结果未确认');
      showToast('生活记录已保存');
      return data.event;
    } catch (err) {
      console.error('[Assistant] Failed to create care event:', err);
      showToast(err.message || '保存记录失败');
      throw err;
    }
  }

  // --- Scope Switching ---
  async function setScope(newScope) {
    if (newScope !== 'demo' && newScope !== 'real') return;
    if (assistantState.scope === newScope) return;

    for (const convId of assistantState.activePolls.keys()) stopPolling(convId);
    assistantState.selectionEpoch++;
    assistantState.listEpoch++;
    assistantState.careListEpoch++;
    assistantState.activeConversationId = null;
    assistantState.activeConversation = null;
    assistantState.messages = [];
    assistantState.pendingTurn = null;
    assistantState.conversations = [];
    closeCareEventsModal();
    closeEditCareEventModal();
    closeDeleteModal();
    closeRenameModal();
    assistantState.scope = newScope;
    renderMessages();
    updateHeaderTitle();
    updateBusyIndicator();
    updateScopeUi();
    await loadConversations(newScope, true);
  }

  function updateScopeUi() {
    const isDemo = assistantState.scope === 'demo';

    // Header Scope Pill
    const pill = $('#assistantScopePill');
    if (pill) {
      pill.className = `assistant-scope-pill ${isDemo ? 'demo' : 'real'}`;
      pill.innerHTML = isDemo
        ? '<span class="scope-full">Demo 模式</span><span class="scope-short">Demo</span>'
        : '<span class="scope-full">Real 真实</span><span class="scope-short">Real</span>';
    }

    // Drawer Scope Tabs
    $$('.assistant-scope-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.scope === assistantState.scope);
    });

    // Banner
    const demoBanner = $('#assistantDemoBanner');
    const realBanner = $('#assistantRealBanner');
    if (demoBanner) demoBanner.hidden = !isDemo;
    if (realBanner) realBanner.hidden = isDemo;
  }

  function updateHeaderTitle() {
    const titleEl = $('#assistantConvTitle');
    if (titleEl) {
      titleEl.textContent = assistantState.activeConversation?.title || '新对话';
    }
    const badgeEl = $('#assistantConvCountBadge');
    if (badgeEl) {
      badgeEl.textContent = String(assistantState.conversations.length);
    }
  }

  function updateBusyIndicator() {
    const hint = $('#assistantBusyIndicator');
    const isBusy = assistantState.activePolls.has(assistantState.activeConversationId);
    if (hint) {
      hint.hidden = !isBusy;
    }
  }

  function updateSendButtonState() {
    const btn = $('#assistantSendBtn');
    const textarea = $('#assistantInput');
    if (btn) {
      const hasText = textarea ? textarea.value.trim().length > 0 : false;
      const isBusy = assistantState.activePolls.has(assistantState.activeConversationId) || assistantState.isSending;
      btn.disabled = !hasText || isBusy;
    }
  }

  function scrollToBottom() {
    const area = $('#assistantMessagesArea');
    if (area) {
      area.scrollTop = area.scrollHeight;
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => {
          if (area) area.scrollTop = area.scrollHeight;
        });
      }
    }
  }

  // --- Safe DOM Rendering (No XSS) ---
  function renderConversationList() {
    const list = $('#assistantConvList');
    if (!list) return;

    list.innerHTML = '';

    if (assistantState.conversations.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'assistant-conv-empty';
      empty.textContent = '暂无对话，点击上方新建';
      list.appendChild(empty);
      return;
    }

    assistantState.conversations.forEach(conv => {
      const item = document.createElement('div');
      item.className = `assistant-conv-item ${conv.id === assistantState.activeConversationId ? 'active' : ''}`;
      item.dataset.convId = conv.id;

      const left = document.createElement('div');
      left.className = 'assistant-conv-item-left';

      const title = document.createElement('div');
      title.className = 'assistant-conv-item-title';
      title.textContent = conv.title || '新对话';

      const preview = document.createElement('div');
      preview.className = 'assistant-conv-item-preview';
      preview.textContent = conv.last_message_preview || '（无消息）';

      left.appendChild(title);
      left.appendChild(preview);

      const right = document.createElement('div');
      right.className = 'assistant-conv-item-right';

      const time = document.createElement('div');
      time.className = 'assistant-conv-item-time';
      time.textContent = formatDateTime(conv.updated_at);

      const actions = document.createElement('div');
      actions.className = 'assistant-conv-item-actions';

      const renameBtn = document.createElement('button');
      renameBtn.className = 'assistant-mini-btn';
      renameBtn.textContent = '✎';
      renameBtn.title = '重命名';
      renameBtn.onclick = (e) => {
        e.stopPropagation();
        openRenameModal(conv.id, conv.title);
      };

      const deleteBtn = document.createElement('button');
      deleteBtn.className = 'assistant-mini-btn danger';
      deleteBtn.textContent = '🗑';
      deleteBtn.title = '删除会话';
      deleteBtn.onclick = (e) => {
        e.stopPropagation();
        openDeleteModal(conv.id);
      };

      actions.appendChild(renameBtn);
      actions.appendChild(deleteBtn);

      right.appendChild(time);
      right.appendChild(actions);

      item.appendChild(left);
      item.appendChild(right);

      item.onclick = () => {
        selectConversation(conv.id);
        closeDrawer();
      };

      list.appendChild(item);
    });
    if (assistantState.conversationsHasMore) {
      const more = document.createElement('button'); more.className = 'assistant-btn-text';
      more.textContent = '加载更多对话'; more.onclick = loadMoreConversations; list.appendChild(more);
    }
  }

  function formatFriendlyError(err) {
    if (!err) return '模型处理失败';
    const code = err.code;
    if (code === 'model_timeout' || code === 'invalid_model_output' || code === 'model_failed') {
      return '这次没能理解，请换个说法或点下方示例。';
    }
    if (code === 'queue_full') {
      return 'AI 正忙，请稍后重试。';
    }
    if (code === 'model_unavailable' || code === 'assistant_disabled' || code === 'service_restarted') {
      return '本地 AI 暂不可用，请稍后再试。';
    }
    return err.message || '模型处理失败';
  }

  function renderMessages() {
    const area = $('#assistantMessagesArea');
    if (!area) return;

    area.innerHTML = '';
    if (assistantState.messagesHasMore) {
      const more = document.createElement('button'); more.className = 'assistant-btn-text';
      more.textContent = '加载更早消息'; more.onclick = loadOlderMessages; area.appendChild(more);
    }

    // If no active conversation or 0 messages, show welcome greeting
    if (!assistantState.activeConversation || assistantState.messages.length === 0) {
      renderWelcomeCard(area);
      return;
    }

    assistantState.messages.forEach(msg => {
      const row = document.createElement('div');
      row.className = `assistant-msg-row ${msg.role}`;
      row.dataset.msgId = msg.id;

      if (msg.role === 'user') {
        const bubble = document.createElement('div');
        bubble.className = 'assistant-bubble';
        bubble.textContent = msg.content; // textContent prevents XSS
        row.appendChild(bubble);

        const meta = document.createElement('div');
        meta.className = 'assistant-msg-meta';

        const timeSpan = document.createElement('span');
        timeSpan.textContent = formatTime(msg.created_at);
        meta.appendChild(timeSpan);

        const isLongWait = Boolean(
          msg.created_at && (Date.now() - new Date(msg.created_at).getTime() > 5000)
        );
        const waitNotice = isLongWait ? '（摄像头分析占用中，稍候）' : '';

        if (msg.status === 'queued') {
          const statusSpan = document.createElement('span');
          statusSpan.className = 'assistant-status-indicator queued';
          statusSpan.textContent = '排队中...' + waitNotice;
          meta.appendChild(statusSpan);
        } else if (msg.status === 'running') {
          const statusSpan = document.createElement('span');
          statusSpan.className = 'assistant-status-indicator running';
          statusSpan.textContent = '分析生成中...' + waitNotice;
          meta.appendChild(statusSpan);
        } else if (msg.status === 'failed') {
          const statusSpan = document.createElement('span');
          statusSpan.className = 'assistant-status-indicator failed';
          statusSpan.textContent = formatFriendlyError(msg.error);

          const retryBtn = document.createElement('button');
          retryBtn.className = 'assistant-retry-btn';
          retryBtn.textContent = '重试';
          retryBtn.onclick = () => sendMessage(msg.content, true, msg.client_message_id);
          if (msg.error?.retryable) statusSpan.appendChild(retryBtn);

          meta.appendChild(statusSpan);
        }

        row.appendChild(meta);
      } else {
        // Assistant Message
        const avatar = document.createElement('div');
        avatar.className = 'assistant-avatar-small';
        avatar.textContent = '✦';
        row.appendChild(avatar);

        const contentCol = document.createElement('div');
        contentCol.className = 'assistant-content-col';

        const bubble = document.createElement('div');
        bubble.className = 'assistant-bubble';
        bubble.textContent = msg.content; // textContent prevents XSS
        contentCol.appendChild(bubble);

        // Record Action Cards if present
        if (msg.record_actions && msg.record_actions.length > 0) {
          const cardList = document.createElement('div');
          cardList.className = 'record-card-list';
          msg.record_actions.forEach(act => {
            cardList.appendChild(createRecordActionCard(act, msg));
          });
          contentCol.appendChild(cardList);
        }

        // Sleep Facts if present
        if (msg.facts?.parent_sleep) {
          contentCol.appendChild(createSleepFactsCard(msg.facts));
        }

        const meta = document.createElement('div');
        meta.className = 'assistant-msg-meta';
        const timeSpan = document.createElement('span');
        timeSpan.textContent = formatTime(msg.created_at);
        meta.appendChild(timeSpan);
        contentCol.appendChild(meta);

        row.appendChild(contentCol);
      }

      area.appendChild(row);
    });

    updateBusyIndicator();
    scrollToBottom();
  }

  function renderWelcomeCard(container) {
    const card = document.createElement('div');
    card.className = 'assistant-welcome-card';

    card.innerHTML = `
      <div class="assistant-welcome-header">
        <div class="assistant-avatar-large">✦</div>
        <div class="assistant-welcome-title">
          <h3>Nestory AI 育儿助手</h3>
          <p>基于本地大模型，多会话管理与智能生活记录</p>
        </div>
      </div>
      <div class="assistant-prompts-label">快捷记录与咨询示例：</div>
      <div class="assistant-quick-prompts">
        <button class="assistant-prompt-chip" data-prompt="今天上午9点宝宝喝了120ml奶">🍼 今天上午9点宝宝喝了120ml奶</button>
        <button class="assistant-prompt-chip" data-prompt="今天上午11点宝宝喝了150ml奶">🍼 11点宝宝喝了150ml奶</button>
        <button class="assistant-prompt-chip" data-prompt="下午1点半到2点20分补觉">🌙 13:30到14:20补觉</button>
        <button class="assistant-prompt-chip" data-prompt="刚才给宝宝换了纸尿裤，有点便便">🧷 刚换了纸尿裤，有点便便</button>
        <button class="assistant-prompt-chip" data-prompt="今天宝宝喝了多少奶？">❓ 今天宝宝喝了多少奶？</button>
        <button class="assistant-prompt-chip" data-prompt="昨天宝宝睡了多久？">📊 昨天宝宝睡了多久？</button>
      </div>
    `;

    // Click quick prompt to prefill & send
    card.querySelectorAll('.assistant-prompt-chip').forEach(btn => {
      btn.onclick = () => {
        const text = btn.dataset.prompt;
        const input = $('#assistantInput');
        if (input) {
          input.value = text;
          updateSendButtonState();
          sendMessage(text);
        }
      };
    });

    container.appendChild(card);
  }

  // --- Record Action Card Component ---
  function createRecordActionCard(action, parentMsg) {
    const card = document.createElement('div');
    card.className = `record-card ${action.status}`;

    // Head
    const head = document.createElement('div');
    head.className = 'record-card-head';

    const typeDiv = document.createElement('div');
    typeDiv.className = 'record-card-type';

    const ev = action.event || action.proposed_event || {};
    const evType = ev.event_type || 'activity';

    let icon = '🎈';
    let typeName = '生活记录';
    if (evType === 'feeding') {
      icon = '🍼';
      typeName = '喂奶记录';
    } else if (evType === 'sleep') {
      icon = '🌙';
      typeName = '睡眠记录';
    } else if (evType === 'diaper') {
      icon = '🧷';
      typeName = '换尿布记录';
    }

    typeDiv.innerHTML = `<span class="record-card-type-icon">${icon}</span><span>${typeName}</span>`;
    head.appendChild(typeDiv);

    // Status Badge
    const badge = document.createElement('span');
    if (action.event?.status === 'deleted') {
      badge.className = 'record-status-badge not-saved';
      badge.textContent = '已删除（历史记录）';
    } else if (action.status === 'saved') {
      badge.className = 'record-status-badge saved';
      badge.textContent = '✓ 已保存';
    } else if (action.status === 'needs_confirmation') {
      badge.className = 'record-status-badge confirm';
      badge.textContent = '? 待确认';
    } else {
      badge.className = 'record-status-badge not-saved';
      badge.textContent = '○ 未保存';
    }
    head.appendChild(badge);
    card.appendChild(head);

    // Fields Grid
    const fields = document.createElement('div');
    fields.className = 'record-card-fields';

    if (ev.occurred_at) {
      fields.appendChild(createFieldEl('发生时间', formatTime(ev.occurred_at)));
    }
    if (ev.end_at) {
      fields.appendChild(createFieldEl('结束时间', formatTime(ev.end_at)));
    }
    if (ev.amount_ml != null) {
      fields.appendChild(createFieldEl('饮用奶量', `${ev.amount_ml} ml`));
    }
    if (ev.diaper_type) {
      const diaperMap = { wet: '湿', dirty: '便', mixed: '便+湿', unknown: '未说明' };
      fields.appendChild(createFieldEl('尿布类型', diaperMap[ev.diaper_type] || ev.diaper_type));
    }
    if (ev.duration_seconds != null) {
      fields.appendChild(createFieldEl('时长', formatDurationZh(ev.duration_seconds)));
    }
    if (ev.notes) {
      const f = createFieldEl('备注', ev.notes);
      f.classList.add('full-width');
      fields.appendChild(f);
    }
    const sourceLabel = !action.event ? '聊天提取（尚未保存）' : ev.source === 'parent_chat' ? '家长聊天提取' : '手动记录';
    const sourceEl = createFieldEl('记录来源', sourceLabel);
    sourceEl.classList.add('full-width');
    fields.appendChild(sourceEl);

    card.appendChild(fields);

    // Confirmation Interactive Form
    if (action.status === 'needs_confirmation') {
      const confirmBox = document.createElement('div');
      confirmBox.className = 'record-confirm-box';

      const promptMsg = document.createElement('div');
      promptMsg.className = 'record-confirm-prompt';
      promptMsg.textContent = action.message || '请确认或补充以下信息：';
      confirmBox.appendChild(promptMsg);

      const editRow = document.createElement('div');
      editRow.className = 'record-edit-row';

      // Amount Input (if feeding)
      let amountInput = null;
      if (evType === 'feeding') {
        const grp = document.createElement('div');
        grp.className = 'record-input-group';
        grp.innerHTML = '<label>奶量 (ml)</label>';
        amountInput = document.createElement('input');
        amountInput.type = 'number';
        amountInput.min = '1';
        amountInput.max = '2000';
        amountInput.value = ev.amount_ml ?? '';
        amountInput.setAttribute('aria-label', '确认饮用奶量 (ml)');
        grp.appendChild(amountInput);
        editRow.appendChild(grp);
      }

      // Diaper select (if diaper)
      let diaperSelect = null;
      if (evType === 'diaper') {
        const grp = document.createElement('div');
        grp.className = 'record-input-group';
        grp.innerHTML = '<label>尿布类型</label>';
        diaperSelect = document.createElement('select');
        diaperSelect.innerHTML = `
          <option value="wet">湿</option>
          <option value="dirty">便便</option>
          <option value="mixed">便+湿</option>
          <option value="unknown">未说明</option>
        `;
        diaperSelect.value = ev.diaper_type || 'unknown';
        grp.appendChild(diaperSelect);
        editRow.appendChild(grp);
      }

      const timeInputs = {};
      for (const [field, label] of [['occurred_at', '确认发生时间'], ['end_at', '确认结束时间']]) {
        if (field === 'end_at' && evType !== 'sleep' && evType !== 'activity') continue;
        const group = document.createElement('div'); group.className = 'record-input-group';
        const title = document.createElement('label'); title.textContent = label;
        const input = document.createElement('input'); input.type = 'datetime-local';
        input.setAttribute('aria-label', label);
        // Start an empty start time at Shanghai "now" so the browser does not
        // fill in the device's own time zone (the Mac may be set to Tokyo).
        input.value = ev[field] ? toLocalIsoDatetime(ev[field])
          : field === 'occurred_at' ? toLocalIsoDatetime(new Date().toISOString()) : '';
        group.appendChild(title); group.appendChild(input); editRow.appendChild(group); timeInputs[field] = input;
      }
      confirmBox.appendChild(editRow);

      // Actions (Confirm / Dismiss)
      const actionsDiv = document.createElement('div');
      actionsDiv.className = 'record-confirm-actions';

      const dismissBtn = document.createElement('button');
      dismissBtn.className = 'record-btn-dismiss';
      dismissBtn.textContent = '忽略';
      dismissBtn.onclick = () => {
        confirmRecordAction(action.action_id, parentMsg.user_message_id || assistantState.messages.find(m => m.role === 'user' && m.turn_id === parentMsg.turn_id)?.id, 'dismiss');
      };

      const confirmBtn = document.createElement('button');
      confirmBtn.className = 'record-btn-confirm';
      confirmBtn.textContent = '确认保存';
      confirmBtn.onclick = () => {
        const updateFields = {};
        for (const [field, input] of Object.entries(timeInputs)) {
          // The input holds Asia/Shanghai wall time (UTC+8, no DST).
          if (!input.value) continue;
          const when = new Date(input.value + ':00+08:00');
          if (when.getTime() > Date.now() + 60000) {
            showToast('时间不能晚于现在（按北京时间填写）');
            return;
          }
          updateFields[field] = when.toISOString();
        }
        if (amountInput && amountInput.value) updateFields.amount_ml = parseInt(amountInput.value, 10);
        if (diaperSelect) updateFields.diaper_type = diaperSelect.value;
        confirmRecordAction(action.action_id, parentMsg.user_message_id || assistantState.messages.find(m => m.role === 'user' && m.turn_id === parentMsg.turn_id)?.id, 'confirm', updateFields);
      };

      actionsDiv.appendChild(dismissBtn);
      actionsDiv.appendChild(confirmBtn);
      confirmBox.appendChild(actionsDiv);

      card.appendChild(confirmBox);
    }

    return card;
  }

  function createFieldEl(label, value) {
    const el = document.createElement('div');
    el.className = 'record-field';
    const l = document.createElement('span');
    l.className = 'record-field-label';
    l.textContent = label;
    const v = document.createElement('span');
    v.className = 'record-field-val';
    v.textContent = value;
    el.appendChild(l);
    el.appendChild(v);
    return el;
  }

  // --- Sleep Facts Card Component ---
  function createSleepFactsCard(facts) {
    const card = document.createElement('div');
    card.className = 'sleep-facts-card';

    const head = document.createElement('div');
    head.className = 'sleep-facts-head';
    head.innerHTML = '<strong>🌙 睡眠数据与分析</strong><span class="record-status-badge not-saved">数据来源说明</span>';
    card.appendChild(head);

    const grid = document.createElement('div');
    grid.className = 'sleep-facts-grid';

    // Parent Sleep
    const pSec = facts.parent_sleep?.seconds;
    const pItem = document.createElement('div');
    pItem.className = 'sleep-fact-item';
    pItem.innerHTML = `
      <span>家长确认睡眠</span>
      <strong>${pSec != null ? formatDurationZh(pSec) : '无记录'}</strong>
      <small>来源：家长记录区间并集</small>
    `;
    grid.appendChild(pItem);

    // Camera Sleep
    const cItem = document.createElement('div');
    cItem.className = 'sleep-fact-item';
    cItem.innerHTML = `
      <span>摄像头观察</span>
      <strong>未验证</strong>
      <small>原因：监测连续性未验证</small>
    `;
    grid.appendChild(cItem);

    card.appendChild(grid);

    // Warnings Box
    if (facts.warnings && facts.warnings.length > 0) {
      const warnBox = document.createElement('div');
      warnBox.className = 'sleep-warnings-box';
      warnBox.textContent = facts.warnings.join('；');
      card.appendChild(warnBox);
    }

    return card;
  }

  // --- Modal Helpers ---
  function openRenameModal(convId, currentTitle) {
    const modal = $('#assistantRenameModal');
    const input = $('#assistantRenameInput');
    if (!modal || !input) return;

    input.value = currentTitle || '';
    modal.classList.add('active');
    modal.dataset.convId = convId;
    input.focus();
  }

  function closeRenameModal() {
    const modal = $('#assistantRenameModal');
    if (modal) modal.classList.remove('active');
  }

  function openDeleteModal(convId) {
    const modal = $('#assistantDeleteModal');
    if (!modal) return;
    modal.classList.add('active');
    modal.dataset.convId = convId;
  }

  function closeDeleteModal() {
    const modal = $('#assistantDeleteModal');
    if (modal) modal.classList.remove('active');
  }

  function openDrawer() {
    const drawer = $('#assistantDrawer');
    const backdrop = $('#assistantDrawerBackdrop');
    if (drawer) drawer.classList.add('active');
    if (backdrop) backdrop.classList.add('active');
  }

  function closeDrawer() {
    const drawer = $('#assistantDrawer');
    const backdrop = $('#assistantDrawerBackdrop');
    if (drawer) drawer.classList.remove('active');
    if (backdrop) backdrop.classList.remove('active');
  }

  // --- Care Events Sheet & Modals ---
  async function openCareEventsModal() {
    const modal = $('#assistantCareEventsModal');
    if (!modal) return;
    modal.classList.add('active');

    const todayStr = new Intl.DateTimeFormat('en-CA', {timeZone: assistantState.timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
    const fromInput = $('#careEventFromDate');
    const toInput = $('#careEventToDate');
    if (fromInput && !fromInput.value) fromInput.value = todayStr;
    if (toInput && !toInput.value) toInput.value = todayStr;

    await refreshCareEventsList();
  }

  function closeCareEventsModal() {
    const modal = $('#assistantCareEventsModal');
    if (modal) modal.classList.remove('active');
  }

  async function refreshCareEventsList(cursor = null, previous = []) {
    if (typeof cursor !== 'string') cursor = null;
    const epoch = ++assistantState.careListEpoch;
    const from = $('#careEventFromDate')?.value;
    const to = $('#careEventToDate')?.value;
    const typePill = $('.care-type-pill.active');
    const eventType = typePill ? typePill.dataset.type : 'all';

    let events;
    const scope = assistantState.scope;
    try { events = await loadCareEvents(from, to, eventType, cursor); }
    catch { const container = $('#careEventsList'); if (container && epoch === assistantState.careListEpoch) container.textContent = '加载失败，无法确认生活记录；请重试。'; return; }
    if (scope !== assistantState.scope || epoch !== assistantState.careListEpoch) return;
    const container = $('#careEventsList');
    if (!container) return;

    container.innerHTML = '';
    if (events.length === 0) {
      container.innerHTML = '<div class="assistant-conv-empty">所选日期范围内暂无生活记录</div>';
      return;
    }

    const rows = [...previous, ...events].filter((e,i,all)=>all.findIndex(x=>x.id===e.id)===i);
    rows.forEach(ev => {
      const item = document.createElement('div');
      item.className = 'care-event-item';
      item.dataset.eventId = ev.id;

      const left = document.createElement('div');
      left.className = 'care-event-left';

      const head = document.createElement('div');
      head.className = 'care-event-head';

      let icon = '🎈';
      let title = '生活活动';
      if (ev.event_type === 'feeding') {
        icon = '🍼';
        title = `喂奶 · ${ev.amount_ml || 0} ml`;
      } else if (ev.event_type === 'sleep') {
        icon = '🌙';
        title = `睡眠 · ${ev.duration_seconds ? formatDurationZh(ev.duration_seconds) : '小睡'}`;
      } else if (ev.event_type === 'diaper') {
        icon = '🧷';
        const dMap = { wet: '尿湿', dirty: '便便', mixed: '便+湿', unknown: '未说明' };
        title = `换尿布 · ${dMap[ev.diaper_type] || ev.diaper_type}`;
      }

      head.innerHTML = `<span class="care-event-title">${icon} ${title}</span>`;
      left.appendChild(head);

      const time = document.createElement('div');
      time.className = 'care-event-time';
      time.textContent = `${formatDateTime(ev.occurred_at)}${ev.end_at ? ' 至 ' + formatTime(ev.end_at) : ''}`;
      left.appendChild(time);

      if (ev.notes) {
        const desc = document.createElement('div');
        desc.className = 'care-event-desc';
        desc.textContent = ev.notes;
        left.appendChild(desc);
      }

      const src = document.createElement('div');
      src.className = 'care-event-source';
      src.textContent = `来源: ${ev.source === 'parent_chat' ? '聊天自动记录' : '家长手动记'} (v${ev.version})`;
      left.appendChild(src);

      item.appendChild(left);

      // Actions
      const actions = document.createElement('div');
      actions.className = 'care-event-actions';

      const editBtn = document.createElement('button');
      editBtn.className = 'assistant-mini-btn';
      editBtn.textContent = '✎ 编辑';
      editBtn.onclick = () => openEditCareEventModal(ev);

      const delBtn = document.createElement('button');
      delBtn.className = 'assistant-mini-btn danger';
      delBtn.textContent = '🗑 删除';
      delBtn.onclick = () => {
        if (confirm(`确定删除这条生活记录吗？\n${title} (${formatDateTime(ev.occurred_at)})`)) {
          deleteCareEvent(ev.id, ev.version).then(refreshCareEventsList).catch(() => refreshCareEventsList());
        }
      };

      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      item.appendChild(actions);

      container.appendChild(item);
    });
    if (events.has_more) {
      const more = document.createElement('button'); more.className = 'assistant-btn-text';
      more.textContent = '加载更多生活记录'; more.onclick = () => refreshCareEventsList(events.next_cursor, rows);
      container.appendChild(more);
    }
  }

  function openEditCareEventModal(eventObj) {
    const modal = $('#editCareEventModal');
    if (!modal) return;
    modal.classList.add('active');
    modal.dataset.eventId = eventObj.id;
    modal.dataset.version = String(eventObj.version);

    $('#editCareEventType').value = eventObj.event_type;
    $('#editCareEventType').disabled = true;
    $('#editCareEventOccurred').value = toLocalIsoDatetime(eventObj.occurred_at);
    $('#editCareEventEnd').value = eventObj.end_at ? toLocalIsoDatetime(eventObj.end_at) : '';
    $('#editCareEventAmount').value = eventObj.amount_ml || '';
    $('#editCareEventDiaper').value = eventObj.diaper_type || 'wet';
    $('#editCareEventNotes').value = eventObj.notes || '';

    // Show/hide fields depending on type
    toggleCareEventFormFields(eventObj.event_type);
  }

  function openCreateCareEventModal() {
    const modal = $('#editCareEventModal');
    if (!modal) return;
    modal.classList.add('active');
    modal.dataset.eventId = '';
    modal.dataset.version = '';

    const nowStr = toLocalIsoDatetime(new Date().toISOString());
    $('#editCareEventType').value = 'feeding';
    $('#editCareEventType').disabled = false;
    $('#editCareEventOccurred').value = nowStr;
    $('#editCareEventEnd').value = '';
    $('#editCareEventAmount').value = '150';
    $('#editCareEventDiaper').value = 'wet';
    $('#editCareEventNotes').value = '';

    toggleCareEventFormFields('feeding');
  }

  function closeEditCareEventModal() {
    const modal = $('#editCareEventModal');
    if (modal) modal.classList.remove('active');
  }

  function toggleCareEventFormFields(type) {
    const amountGrp = $('#editCareEventAmountGroup');
    const diaperGrp = $('#editCareEventDiaperGroup');
    const endGrp = $('#editCareEventEndGroup');
    if (amountGrp) amountGrp.hidden = type !== 'feeding';
    if (diaperGrp) diaperGrp.hidden = type !== 'diaper';
    if (endGrp) endGrp.hidden = type !== 'sleep' && type !== 'activity';
  }

  function toLocalIsoDatetime(utcIsoStr) {
    if (!utcIsoStr) return '';
    try {
      const p = shanghaiParts(new Date(utcIsoStr));
      return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
    } catch {
      return '';
    }
  }

  // --- Setup Event Listeners ---
  function initAssistantEvents() {
    // Drawer open / close
    $('#assistantDrawerBtn')?.addEventListener('click', openDrawer);
    $('#assistantDrawerClose')?.addEventListener('click', closeDrawer);
    $('#assistantDrawerBackdrop')?.addEventListener('click', closeDrawer);

    // New conversation
    $('#assistantNewConvBtn')?.addEventListener('click', () => createConversation(assistantState.scope));
    $('#assistantDrawerNewBtn')?.addEventListener('click', () => createConversation(assistantState.scope));

    // Scope Pill Toggle (Header)
    $('#assistantScopePill')?.addEventListener('click', () => {
      setScope(assistantState.scope === 'demo' ? 'real' : 'demo');
    });

    // Drawer Scope Tabs
    $$('.assistant-scope-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        setScope(tab.dataset.scope);
      });
    });

    // Rename Conv Button (Header)
    $('#assistantRenameConvBtn')?.addEventListener('click', () => {
      if (assistantState.activeConversation) {
        openRenameModal(assistantState.activeConversation.id, assistantState.activeConversation.title);
      }
    });

    // Rename Modal Actions
    $('#assistantRenameSaveBtn')?.addEventListener('click', () => {
      const modal = $('#assistantRenameModal');
      const convId = modal?.dataset.convId;
      const newTitle = $('#assistantRenameInput')?.value;
      if (convId && newTitle) {
        renameConversation(convId, newTitle);
        closeRenameModal();
      }
    });
    $('#assistantRenameCancelBtn')?.addEventListener('click', closeRenameModal);
    $('#assistantRenameModal')?.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeRenameModal();
    });

    // Delete Conv Button (Header)
    $('#assistantDeleteConvBtn')?.addEventListener('click', () => {
      if (assistantState.activeConversation) {
        openDeleteModal(assistantState.activeConversation.id);
      }
    });

    // Delete Modal Actions
    $('#assistantDeleteConfirmBtn')?.addEventListener('click', () => {
      const modal = $('#assistantDeleteModal');
      const convId = modal?.dataset.convId;
      if (convId) {
        deleteConversation(convId);
        closeDeleteModal();
      }
    });
    $('#assistantDeleteCancelBtn')?.addEventListener('click', closeDeleteModal);
    $('#assistantDeleteModal')?.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeDeleteModal();
    });

    // Textarea input & send button
    const textarea = $('#assistantInput');
    const sendBtn = $('#assistantSendBtn');

    if (textarea) {
      textarea.addEventListener('input', () => {
        textarea.style.height = 'auto';
        textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px';
        updateSendButtonState();
      });

      textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          if (!sendBtn?.disabled) {
            sendMessage(textarea.value);
          }
        }
      });
    }

    sendBtn?.addEventListener('click', () => {
      if (textarea && textarea.value.trim()) {
        sendMessage(textarea.value);
      }
    });

    // Care Events Button (Header)
    $('#assistantCareEventsBtn')?.addEventListener('click', openCareEventsModal);
    $('#careEventsModalClose')?.addEventListener('click', closeCareEventsModal);
    $('#careEventsModalBackdrop')?.addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeCareEventsModal();
    });

    $('#careEventFromDate')?.addEventListener('change', refreshCareEventsList);
    $('#careEventToDate')?.addEventListener('change', refreshCareEventsList);

    $$('.care-type-pill').forEach(pill => {
      pill.addEventListener('click', () => {
        $$('.care-type-pill').forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        refreshCareEventsList();
      });
    });

    $('#openAddCareEventBtn')?.addEventListener('click', openCreateCareEventModal);
    $('#editCareEventType')?.addEventListener('change', (e) => {
      toggleCareEventFormFields(e.target.value);
    });

    $('#saveCareEventBtn')?.addEventListener('click', async () => {
      const modal = $('#editCareEventModal');
      const eventId = modal?.dataset.eventId;
      const expVer = modal?.dataset.version ? Number(modal.dataset.version) : null;
      const type = $('#editCareEventType')?.value;
      const occurred = $('#editCareEventOccurred')?.value;
      const end = $('#editCareEventEnd')?.value;
      const amount = $('#editCareEventAmount')?.value;
      const diaper = $('#editCareEventDiaper')?.value;
      const notes = $('#editCareEventNotes')?.value;

      if (!occurred) {
        showToast('发生时间不能为空');
        return;
      }

      const payload = {
        event_type: type,
        occurred_at: new Date(occurred).toISOString(),
        end_at: end ? new Date(end).toISOString() : null,
        amount_ml: type === 'feeding' && amount ? parseInt(amount, 10) : null,
        diaper_type: type === 'diaper' ? diaper : null,
        notes: notes || '',
      };

      try {
        if (eventId) {
          delete payload.event_type;
          await updateCareEvent(eventId, payload, expVer);
        } else {
          await createManualCareEvent(payload);
        }
        closeEditCareEventModal();
        await refreshCareEventsList();
      } catch {
        // error toast handled in updateCareEvent
      }
    });

    $('#cancelCareEventBtn')?.addEventListener('click', closeEditCareEventModal);

    // Visibility change listener to handle background tab polling pause/resume
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
          // Immediately trigger active polls
          for (const [convId, poll] of assistantState.activePolls.entries()) {
            if (poll.active) {
              clearTimeout(poll.timer);
              startPolling(convId, poll.userMsgId, poll.pollUrl, 200);
            }
          }
        }
      });
    }
  }

  // --- Public Assistant Controller API ---
  const AssistantController = {
    state: assistantState,
    mockStore,
    init: async function () {
      if (assistantState.initialized) return;
      assistantState.initialized = true;
      initAssistantEvents();
      updateScopeUi();
      updateMockBanner();
      await loadConversations(assistantState.scope, true);
    },
    onPageShow: async function () {
      if (!assistantState.initialized) {
        await this.init();
      } else {
        await loadConversations(assistantState.scope, false);
      }
      scrollToBottom();
    },
    setMock: function (isMock) {
      if (isMock && !mockAllowed) throw new Error('Mock 仅可在显式启用的本机开发测试中使用');
      assistantState.isMock = !!isMock;
      updateMockBanner();
    },
    setScope,
    selectConversation,
    createConversation,
    renameConversation,
    deleteConversation,
    sendMessage,
    confirmRecordAction,
    loadCareEvents,
    updateCareEvent,
    deleteCareEvent,
    createManualCareEvent,
    mockApiHandler,
    assistantApi,
    formatFriendlyError,
  };

  if (typeof window !== 'undefined') {
    window.Assistant = AssistantController;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = AssistantController;
  }

  // Auto-init only in browser (not in Node module export)
  if (typeof window !== 'undefined' && typeof document !== 'undefined' && !window.__TEST_ENV__ && typeof module === 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        if (typeof document.getElementById === 'function' && document.getElementById('assistantPage')) {
          AssistantController.init();
        }
      });
    } else {
      if (typeof document.getElementById === 'function' && document.getElementById('assistantPage')) {
        AssistantController.init();
      }
    }
  }

})();


