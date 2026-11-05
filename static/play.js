/**
 * Nestory Play With Me - Parent-Child Interactions Controller
 * Handles activity library rendering, timing, reaction recording and daily logs
 */

(function () {
  'use strict';

  function safeStorageGet(key, fallback) {
    try {
      if (typeof localStorage !== 'undefined') {
        const val = localStorage.getItem(key);
        return val !== null ? val : fallback;
      }
    } catch {
      // storage unavailable
    }
    return fallback;
  }

  function safeStorageSet(key, val) {
    try {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(key, val);
      }
    } catch {
      // storage unavailable
    }
  }

  function generateUUID() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return 'act-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
  }

  const KIND_LABELS = {
    finger_play: '手指操',
    game: '亲子游戏',
    sensory: '感官探索',
    music: '音乐互动',
  };

  const REACTION_CONFIG = {
    happy: { label: '开心', emoji: '😄' },
    calm: { label: '平静', emoji: '😌' },
    uninterested: { label: '没兴趣', emoji: '😐' },
    fussy: { label: '哭闹', emoji: '😢' },
  };

  const playState = {
    scope: 'real', // single record set; Demo/Real switching removed
    activities: [],
    filter: 'all',
    showAllActivities: false,
    activeTimer: null, // { activityId, startedAt, endedAt, clientId, timerInterval, elapsedSeconds, reaction, notes, error }
    todayItems: [],
    isLoadingActivities: false,
    isLoadingLogs: false,
    mockHandler: null,
  };

  async function playApi(path, options = {}) {
    if (playState.mockHandler) {
      return playState.mockHandler(path, options);
    }

    const csrf = typeof document !== 'undefined'
      ? document.cookie.split('; ').find(item => item.startsWith('bmon_csrf='))?.split('=').slice(1).join('=')
      : null;

    const headers = {
      'Content-Type': 'application/json',
      ...(csrf && options.method && options.method !== 'GET' ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}),
      ...(options.headers || {}),
    };

    let response;
    try {
      response = await fetch(path, {
        cache: 'no-store',
        ...options,
        headers,
      });
    } catch {
      const err = new Error('网络连接失败，请检查网络');
      err.code = 'network_error';
      throw err;
    }

    const body = await response.json().catch(() => ({}));
    if (response.status === 401) {
      if (typeof window !== 'undefined') window.location.assign('/login');
      throw new Error('登录已失效');
    }
    if (!response.ok) {
      const errMsg = body?.error?.message || body?.error || `请求失败 (${response.status})`;
      const err = new Error(errMsg);
      err.status = response.status;
      err.code = body?.error?.code || 'request_failed';
      err.details = body?.error?.details;
      throw err;
    }
    return body;
  }

  // All times are shown in the household's timezone (Asia/Shanghai), whatever
  // timezone the viewing phone or laptop is set to.
  const SHANGHAI_PARTS = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  function shanghaiParts(d) {
    const p = {};
    for (const part of SHANGHAI_PARTS.formatToParts(d)) p[part.type] = part.value;
    return p;
  }

  function getTodayDateString() {
    const p = shanghaiParts(new Date());
    return `${p.year}-${p.month}-${p.day}`;
  }

  function formatTime(isoStr) {
    if (!isoStr) return '';
    try {
      const p = shanghaiParts(new Date(isoStr));
      return `${p.hour}:${p.minute}`;
    } catch {
      return isoStr;
    }
  }

  function formatDuration(sec) {
    if (sec == null || isNaN(sec)) return '0:00';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  async function loadActivities() {
    playState.isLoadingActivities = true;
    try {
      const res = await fetch('/play/activities.json', { cache: 'no-cache' });
      if (res.ok) {
        playState.activities = await res.json();
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (err) {
      console.warn('[Play] Failed to load activities catalog:', err);
    } finally {
      playState.isLoadingActivities = false;
      renderActivities();
    }
  }

  async function loadTodayInteractions() {
    playState.isLoadingLogs = true;
    const dateStr = getTodayDateString();
    const tz = 'Asia/Shanghai';
    try {
      const data = await playApi(`/api/interactions?scope=${encodeURIComponent(playState.scope)}&date=${encodeURIComponent(dateStr)}&timezone=${encodeURIComponent(tz)}`);
      playState.todayItems = Array.isArray(data.items) ? data.items : [];
    } catch (err) {
      console.warn('[Play] Failed to load interactions:', err);
      playState.todayItems = [];
    } finally {
      playState.isLoadingLogs = false;
      renderTodayLogs();
    }
  }

  async function setScope(newScope) {
    if (newScope !== 'demo' && newScope !== 'real') return;
    playState.scope = newScope;
    safeStorageSet('nestory_play_scope', newScope);
    updateScopeUI();
    playState.todayItems = [];
    await loadTodayInteractions();
  }

  function updateScopeUI() {
    const badge = document.getElementById('playScopeBadge');
    if (badge) {
      badge.textContent = playState.scope === 'demo' ? '演示模式 (Demo)' : '真实环境 (Real)';
      badge.hidden = playState.scope !== 'demo';
    }
    const select = document.getElementById('playScopeSelect');
    if (select && select.value !== playState.scope) {
      select.value = playState.scope;
    }
  }

  function setFilter(filter) {
    playState.filter = filter;
    playState.showAllActivities = false;
    document.querySelectorAll('.play-filter-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    renderActivities();
  }

  function expandActivities() {
    playState.showAllActivities = true;
    renderActivities();
  }

  function startActivity(act) {
    if (playState.activeTimer && playState.activeTimer.timerInterval) {
      clearInterval(playState.activeTimer.timerInterval);
    }

    playState.activeTimer = {
      activity: act,
      startedAt: new Date().toISOString(),
      endedAt: null,
      clientId: generateUUID(),
      elapsedSeconds: 0,
      reaction: null,
      notes: '',
      error: null,
      timerInterval: setInterval(() => {
        if (playState.activeTimer) {
          playState.activeTimer.elapsedSeconds += 1;
          updateTimerUI();
        }
      }, 1000),
    };
    renderActivities();
  }

  function updateTimerUI() {
    const timerEl = document.querySelector(`.activity-timer-text[data-id="${playState.activeTimer?.activity?.id}"]`);
    if (timerEl && playState.activeTimer) {
      timerEl.textContent = formatDuration(playState.activeTimer.elapsedSeconds);
    }
  }

  function finishActivity() {
    if (!playState.activeTimer) return;
    if (playState.activeTimer.timerInterval) {
      clearInterval(playState.activeTimer.timerInterval);
      playState.activeTimer.timerInterval = null;
    }
    playState.activeTimer.endedAt = new Date().toISOString();
    renderActivities();
  }

  function cancelActiveActivity() {
    if (playState.activeTimer?.timerInterval) {
      clearInterval(playState.activeTimer.timerInterval);
    }
    playState.activeTimer = null;
    renderActivities();
  }

  async function submitActiveActivity() {
    const timer = playState.activeTimer;
    if (!timer) return;

    if (timer.notes && timer.notes.length > 200) {
      timer.error = '备注字数不能超过 200 字';
      renderActivities();
      return;
    }

    const payload = {
      client_id: timer.clientId,
      scope: playState.scope,
      activity_id: timer.activity.id,
      kind: timer.activity.kind,
      title: timer.activity.title,
      timezone: 'Asia/Shanghai',
      started_at: timer.startedAt,
      ended_at: timer.endedAt || new Date().toISOString(),
      reaction: timer.reaction || null,
      notes: timer.notes || '',
    };

    try {
      timer.error = null;
      const res = await playApi('/api/interactions', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      if (res && (res.interaction || res.replayed !== undefined)) {
        // Success!
        playState.activeTimer = null;
        renderActivities();
        await loadTodayInteractions();
        if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
          window.showToast('互动记录已保存');
        }
      } else {
        throw new Error('保存未确认');
      }
    } catch (err) {
      // Must honestly display failure without faking success
      timer.error = `保存失败：${err.message || '请重试'}`;
      renderActivities();
    }
  }

  async function deleteInteraction(id) {
    if (!confirm('确定要删除这条互动记录吗？')) return;
    try {
      await playApi(`/api/interactions/${encodeURIComponent(id)}?scope=${encodeURIComponent(playState.scope)}`, {
        method: 'DELETE',
      });
      await loadTodayInteractions();
      if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
        window.showToast('记录已删除');
      }
    } catch (err) {
      alert(`删除失败：${err.message}`);
    }
  }

  function renderActivities() {
    const container = document.getElementById('playActivitiesGrid');
    if (!container) return;

    const filtered = playState.filter === 'all'
      ? playState.activities
      : playState.activities.filter(a => a.kind === playState.filter);

    if (filtered.length === 0) {
      container.innerHTML = `<div class="today-empty-tip">暂无该分类的亲子互动内容</div>`;
      return;
    }

    const shouldCollapse = !playState.showAllActivities && filtered.length > 3;
    const displayed = shouldCollapse ? filtered.slice(0, 3) : filtered;
    // Keep a running or just-finished interaction on screen even when the list is collapsed.
    const activeAct = playState.activeTimer && playState.activeTimer.activity;
    if (shouldCollapse && activeAct && !displayed.some(a => a.id === activeAct.id)) {
      const match = filtered.find(a => a.id === activeAct.id);
      if (match) displayed.push(match);
    }

    const cardsHtml = displayed.map(act => {
      const isActive = playState.activeTimer && playState.activeTimer.activity.id === act.id;
      const isTiming = isActive && playState.activeTimer.timerInterval !== null;
      const isFinished = isActive && playState.activeTimer.timerInterval === null;

      const kindClass = `kind-${act.kind}`;
      const kindText = KIND_LABELS[act.kind] || act.kind;
      const ageText = act.age_months ? `${act.age_months[0]}~${act.age_months[1]} 个月` : '';
      const durationText = act.minutes ? `约 ${act.minutes} 分钟` : '';

      const stepsHtml = (act.steps || []).map(s => `<li>${escapeHtml(s)}</li>`).join('');

      let actionHtml = '';
      if (!isActive) {
        actionHtml = `
          <div class="activity-action-bar">
            <button class="activity-btn activity-btn-primary" data-action="start" data-id="${act.id}">开始互动</button>
          </div>
        `;
      } else if (isTiming) {
        actionHtml = `
          <div class="activity-action-bar">
            <div class="activity-timer-box">
              <span class="activity-pulse-dot"></span>
              <span class="activity-timer-text" data-id="${act.id}">${formatDuration(playState.activeTimer.elapsedSeconds)}</span>
            </div>
            <button class="activity-btn activity-btn-danger" data-action="finish" data-id="${act.id}">结束并记录</button>
          </div>
        `;
      } else if (isFinished) {
        const reactionsHtml = Object.entries(REACTION_CONFIG).map(([key, item]) => {
          const isSelected = playState.activeTimer.reaction === key;
          return `
            <button type="button" class="reaction-btn ${isSelected ? 'selected' : ''}" data-reaction="${key}">
              <span class="emoji">${item.emoji}</span>
              <span>${item.label}</span>
            </button>
          `;
        }).join('');

        const errorHtml = playState.activeTimer.error
          ? `<div class="activity-form-error visible">${escapeHtml(playState.activeTimer.error)}</div>`
          : `<div class="activity-form-error"></div>`;

        actionHtml = `
          <div class="activity-record-form">
            <div class="activity-form-label">孩子反应与情绪</div>
            <div class="reaction-buttons-row">${reactionsHtml}</div>
            <textarea class="activity-notes-input" placeholder="写写宝宝的特别反应或表现（可选，限200字）" maxlength="200">${escapeHtml(playState.activeTimer.notes || '')}</textarea>
            ${errorHtml}
            <div class="activity-form-footer">
              <button type="button" class="activity-btn" data-action="cancel">取消</button>
              <button type="button" class="activity-btn activity-btn-primary" data-action="save">保存记录</button>
            </div>
          </div>
        `;
      }

      return `
        <article class="activity-card" data-id="${act.id}">
          <div class="activity-card-header">
            <span class="activity-kind-tag ${kindClass}">${kindText}</span>
            <h4 class="activity-card-title">${escapeHtml(act.title)}</h4>
          </div>
          <div class="activity-meta-row">
            <span>👶 ${ageText}</span>
            <span>⏱️ ${durationText}</span>
          </div>
          <details class="activity-steps-details" ${isActive ? 'open' : ''}>
            <summary>查看互动步骤（${act.steps ? act.steps.length : 0} 步）</summary>
            <ol class="activity-steps-list">${stepsHtml}</ol>
          </details>
          ${actionHtml}
        </article>
      `;
    }).join('');

    let expandHtml = '';
    if (shouldCollapse) {
      expandHtml = `
        <div class="play-expand-box">
          <button type="button" class="play-expand-btn" id="playExpandBtn">查看全部 ${filtered.length} 个互动</button>
        </div>
      `;
    } else if (playState.showAllActivities && filtered.length > 3) {
      expandHtml = `
        <div class="play-expand-box">
          <button type="button" class="play-expand-btn" id="playExpandBtn">收起互动（只显示前 3 个）</button>
        </div>
      `;
    }

    container.innerHTML = cardsHtml + expandHtml;
    bindCardEvents(container);

    const expandBtn = container.querySelector('#playExpandBtn');
    if (expandBtn) {
      expandBtn.onclick = () => {
        playState.showAllActivities = !playState.showAllActivities;
        renderActivities();
      };
    }
  }

  function bindCardEvents(container) {
    container.querySelectorAll('[data-action="start"]').forEach(btn => {
      btn.onclick = () => {
        const id = btn.dataset.id;
        const act = playState.activities.find(a => a.id === id);
        if (act) startActivity(act);
      };
    });

    container.querySelectorAll('[data-action="finish"]').forEach(btn => {
      btn.onclick = () => finishActivity();
    });

    container.querySelectorAll('[data-action="cancel"]').forEach(btn => {
      btn.onclick = () => cancelActiveActivity();
    });

    container.querySelectorAll('[data-action="save"]').forEach(btn => {
      btn.onclick = () => submitActiveActivity();
    });

    container.querySelectorAll('.reaction-btn').forEach(btn => {
      btn.onclick = () => {
        if (!playState.activeTimer) return;
        playState.activeTimer.reaction = btn.dataset.reaction;
        renderActivities();
      };
    });

    const notesInput = container.querySelector('.activity-notes-input');
    if (notesInput) {
      notesInput.oninput = (e) => {
        if (playState.activeTimer) {
          playState.activeTimer.notes = e.target.value;
        }
      };
    }
  }

  function renderTodayLogs() {
    const listEl = document.getElementById('todayInteractionsList');
    if (!listEl) return;

    if (playState.todayItems.length === 0) {
      listEl.innerHTML = `<div class="today-empty-tip">今天暂无互动记录，挑选一个互动开始陪伴吧！</div>`;
      return;
    }

    listEl.innerHTML = playState.todayItems.map(item => {
      const timeStr = formatTime(item.started_at);
      const reactInfo = item.reaction && REACTION_CONFIG[item.reaction];
      const reactBadge = reactInfo ? `<span class="interaction-item-reaction">${reactInfo.emoji} ${reactInfo.label}</span>` : '';
      const notesHtml = item.notes ? `<div class="interaction-item-notes">${escapeHtml(item.notes)}</div>` : '';
      const durationStr = item.duration_seconds ? ` (${Math.round(item.duration_seconds / 60)} 分钟)` : '';

      return `
        <div class="today-interaction-item" data-id="${item.id}">
          <div class="interaction-item-main">
            <div class="interaction-item-top">
              <span class="activity-kind-tag kind-${item.kind}">${KIND_LABELS[item.kind] || item.kind}</span>
              <strong class="interaction-item-title">${escapeHtml(item.title)}</strong>
              ${reactBadge}
              <span style="color:var(--muted); font-size:11px;">${timeStr}${durationStr}</span>
            </div>
            ${notesHtml}
          </div>
          <button class="interaction-delete-btn" data-delete-id="${item.id}" title="删除记录">✕</button>
        </div>
      `;
    }).join('');

    listEl.querySelectorAll('[data-delete-id]').forEach(btn => {
      btn.onclick = () => {
        deleteInteraction(btn.dataset.deleteId);
      };
    });
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function init() {
    const select = document.getElementById('playScopeSelect');
    if (select) {
      select.value = playState.scope;
      select.onchange = (e) => setScope(e.target.value);
    }
    updateScopeUI();

    document.querySelectorAll('.play-filter-btn').forEach(btn => {
      btn.onclick = () => setFilter(btn.dataset.filter);
    });

    loadActivities();
    loadTodayInteractions();
  }

  const PlayController = {
    state: playState,
    init,
    setScope,
    setFilter,
    expandActivities,
    startActivity,
    finishActivity,
    cancelActiveActivity,
    submitActiveActivity,
    deleteInteraction,
    loadActivities,
    loadTodayInteractions,
    setMockHandler: (fn) => { playState.mockHandler = fn; },
    playApi,
  };

  if (typeof window !== 'undefined') {
    window.PlayInteractions = PlayController;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = PlayController;
  }

  if (typeof window !== 'undefined' && typeof document !== 'undefined' && !window.__TEST_ENV__ && typeof module === 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('playActivitiesGrid')) {
          PlayController.init();
        }
      });
    } else {
      if (document.getElementById('playActivitiesGrid')) {
        PlayController.init();
      }
    }
  }

})();
