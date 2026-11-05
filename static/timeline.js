/**
 * Nestory Unified Timeline & Daily Summary Controller
 * Connects /api/timeline/unified and /api/summary/daily
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
      let d;
      if (typeof isoStr === 'number' || /^\d+$/.test(String(isoStr))) {
        const num = Number(isoStr);
        d = new Date(num < 1e11 ? num * 1000 : num);
      } else {
        d = new Date(isoStr);
      }
      const p = shanghaiParts(d);
      return `${p.hour}:${p.minute}`;
    } catch {
      return String(isoStr);
    }
  }

  function formatDurationText(sec) {
    if (sec == null || isNaN(sec) || sec <= 0) return '—';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h > 0) {
      return `${h} 小时 ${m > 0 ? m + ' 分钟' : ''}`;
    }
    return `${m} 分钟`;
  }

  function formatCryDuration(sec) {
    if (sec == null || isNaN(sec)) return '—';
    if (sec <= 0) return '0 分钟';
    if (sec < 60) return '<1 分钟';
    return `${Math.round(sec / 60)} 分钟`;
  }

  const REACTION_CONFIG = {
    happy: { label: '开心', emoji: '😄' },
    calm: { label: '平静', emoji: '😌' },
    uninterested: { label: '没兴趣', emoji: '😐' },
    fussy: { label: '哭闹', emoji: '😢' },
  };

  const RIBBON_ACTIVITY_COLORS = {
    sleeping: '#7e91a6',
    awake: '#7ca778',
    awake_happy: '#71a26c',
    awake_unhappy: '#cf9578',
    crying: '#c35f64',
    unknown: '#a9b0ab',
  };

  const timelineState = {
    scope: 'real', // single record set; Demo/Real switching removed
    date: getTodayDateString(),
    timezone: 'Asia/Shanghai',
    summaryData: null,
    timelineData: null,
    observations: [],
    selectedSegmentId: null,
    activeFilter: 'all', // 'all' | 'sleep' | 'feeding' | 'cry' | 'interaction'
    isSummaryLoading: false,
    isTimelineLoading: false,
    isNarrativeLoading: false,
    summaryError: null,
    timelineError: null,
    trendTab: 'feeding', // 'feeding' | 'sleep'
    filters: {
      activity: true,
      posture_presence: false, // Default OFF to prevent list bloat
      care: true,
      interaction: true,
      cry: true,
    },
    mockHandler: null,
  };

  async function apiFetch(path, timeoutMs = 15000) {
    if (timelineState.mockHandler) {
      return timelineState.mockHandler(path);
    }

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    let response;
    try {
      response = await fetch(path, {
        cache: 'no-store',
        signal: controller.signal,
      });
    } catch (err) {
      clearTimeout(timer);
      if (err.name === 'AbortError') {
        const timeoutErr = new Error('请求超时，请稍后再试');
        timeoutErr.code = 'timeout';
        throw timeoutErr;
      }
      const netErr = new Error('网络连接失败');
      netErr.code = 'network_error';
      throw netErr;
    }
    clearTimeout(timer);

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
      throw err;
    }
    return body;
  }

  async function loadSummary(withNarrative = false) {
    timelineState.isSummaryLoading = !withNarrative;
    timelineState.isNarrativeLoading = withNarrative;
    timelineState.summaryError = null;
    renderSummary();

    const narrativeParam = withNarrative ? '&narrative=1' : '';
    const url = `/api/summary/daily?scope=${encodeURIComponent(timelineState.scope)}&date=${encodeURIComponent(timelineState.date)}&timezone=${encodeURIComponent(timelineState.timezone)}${narrativeParam}`;

    try {
      const data = await apiFetch(url, withNarrative ? 65000 : 15000);
      timelineState.summaryData = data;
    } catch (err) {
      console.warn('[Timeline] Failed to load summary:', err);
      timelineState.summaryError = err.message || '加载今日总结失败';
      if (!withNarrative) timelineState.summaryData = null;
    } finally {
      timelineState.isSummaryLoading = false;
      timelineState.isNarrativeLoading = false;
      renderSummary();
    }
  }

  async function loadUnifiedTimeline() {
    timelineState.isTimelineLoading = true;
    timelineState.timelineError = null;
    renderTimeline();

    const url = `/api/timeline/unified?scope=${encodeURIComponent(timelineState.scope)}&date=${encodeURIComponent(timelineState.date)}&timezone=${encodeURIComponent(timelineState.timezone)}`;

    try {
      // Observations arrive from app.js loadTimeline(), which knows the current observation mode.
      const data = await apiFetch(url, 15000);
      timelineState.timelineData = data;
    } catch (err) {
      console.warn('[Timeline] Failed to load unified timeline:', err);
      timelineState.timelineError = err.message || '加载统一时间线失败';
      timelineState.timelineData = null;
    } finally {
      timelineState.isTimelineLoading = false;
      renderTimeline();
    }
  }

  function setScope(newScope) {
    if (newScope !== 'demo' && newScope !== 'real') return;
    timelineState.scope = newScope;
    safeStorageSet('nestory_records_scope', newScope);
    updateScopeUI();
    loadSummary();
    loadUnifiedTimeline();
  }

  function setDate(newDate) {
    if (!newDate) return;
    timelineState.date = newDate;
    const dateInput = document.getElementById('recordDate');
    if (dateInput && dateInput.value !== newDate) {
      dateInput.value = newDate;
    }
    loadSummary();
    loadUnifiedTimeline();
  }

  function updateScopeUI() {
    const badge = document.getElementById('recordsScopeBadge');
    if (badge) {
      badge.textContent = timelineState.scope === 'demo' ? '演示模式 (Demo)' : '真实环境 (Real)';
      badge.hidden = timelineState.scope !== 'demo';
    }
    const select = document.getElementById('recordsScopeSelect');
    if (select && select.value !== timelineState.scope) {
      select.value = timelineState.scope;
    }
  }

  /* ========================================================
     Render: Daily Summary Card
     ======================================================== */
  function renderSummary() {
    const card = document.getElementById('dailySummaryCard');
    if (!card) return;

    if (timelineState.summaryError && !timelineState.summaryData) {
      card.innerHTML = `
        <div class="summary-header">
          <div class="summary-title-wrap">
            <span style="font-size:18px;">📋</span>
            <h3>今日总结与作息规律</h3>
          </div>
        </div>
        <div class="activity-form-error visible" style="display:block;">
          无法加载今日总结：${escapeHtml(timelineState.summaryError)}
        </div>
      `;
      return;
    }

    if (!timelineState.summaryData) {
      card.innerHTML = `
        <div class="summary-header">
          <div class="summary-title-wrap">
            <span style="font-size:18px;">📋</span>
            <h3>今日总结与作息规律</h3>
          </div>
        </div>
        <div class="today-empty-tip">正在生成今日作息总结…</div>
      `;
      return;
    }

    const data = timelineState.summaryData;

    // 1. Narrative Section
    let narrativeHtml = '';
    if (timelineState.isNarrativeLoading) {
      narrativeHtml = `<div class="narrative-notice">✦ AI 正在整理今日育儿叙述，请稍候…</div>`;
    } else if (data.narrative_status === 'ok' && data.narrative) {
      narrativeHtml = `
        <div class="narrative-box">
          <div style="font-weight:600; font-size:11px; margin-bottom:4px; color:#a855f7;">✦ NESTORY AI 总结</div>
          <div>${escapeHtml(data.narrative)}</div>
        </div>
      `;
    } else if (data.narrative_status !== 'not_requested') {
      narrativeHtml = `<div class="narrative-notice">AI 暂时无法润色，以上为记录汇总。</div>`;
    }

    // 2. Highlights List
    const highlights = Array.isArray(data.highlights) && data.highlights.length > 0
      ? data.highlights
      : ['今天暂无生活或看护记录。'];
    const highlightsHtml = highlights.map(h => `<li>${escapeHtml(h)}</li>`).join('');

    // 3. Suggestions
    let suggestionsHtml = '';
    if (Array.isArray(data.suggestions) && data.suggestions.length > 0) {
      const items = data.suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join('');
      suggestionsHtml = `
        <div class="suggestions-box">
          <div class="suggestions-title">💡 温馨提醒</div>
          <ul class="suggestions-list">${items}</ul>
        </div>
      `;
    }

    // 4. 7-Day Trend CSS Bars
    const trend = Array.isArray(data.trend) ? data.trend : [];
    let maxVal = 1;
    trend.forEach(t => {
      const v = timelineState.trendTab === 'feeding'
        ? (t.feeding_count || 0)
        : ((t.parent_sleep_seconds || 0) / 3600);
      if (v > maxVal) maxVal = v;
    });

    const trendBarsHtml = trend.map(item => {
      const isCurrent = item.date === timelineState.date;
      const rawVal = timelineState.trendTab === 'feeding'
        ? item.feeding_count
        : (item.parent_sleep_seconds ? item.parent_sleep_seconds / 3600 : null);

      const displayVal = rawVal != null
        ? (timelineState.trendTab === 'feeding' ? `${rawVal}次` : `${rawVal.toFixed(1)}h`)
        : '0';

      const numVal = rawVal || 0;
      const heightPercent = Math.max(4, Math.round((numVal / maxVal) * 100));
      const shortDate = item.date.slice(5); // "MM-DD"

      return `
        <div class="trend-col ${isCurrent ? 'is-current' : ''}" data-trend-date="${item.date}" title="${item.date}: ${displayVal}">
          <span class="trend-val">${displayVal}</span>
          <div class="trend-bar-track">
            <div class="trend-bar-fill" style="height: ${heightPercent}%;"></div>
          </div>
          <span class="trend-date">${shortDate}</span>
        </div>
      `;
    }).join('');

    const disclaimerText = data.disclaimer || '以上为记录汇总，仅供参考，不构成医疗建议。';
    const cryCount = data.stats?.cry?.count || 0;
    const cryBadgeHtml = cryCount > 0 ? `<span class="summary-cry-badge">哭声 ${cryCount} 段</span>` : '';

    card.innerHTML = `
      <div class="summary-header">
        <div class="summary-title-wrap">
          <span style="font-size:18px;">📋</span>
          <h3>今日总结与作息规律</h3>
          ${cryBadgeHtml}
        </div>
        <button type="button" class="btn-ai-narrative" id="btnAiNarrative" ${timelineState.isNarrativeLoading ? 'disabled' : ''}>
          <span>✦</span> ${timelineState.isNarrativeLoading ? '正在整理…' : 'AI 润色'}
        </button>
      </div>

      ${narrativeHtml}

      <div style="font-size:12px; font-weight:600; color:var(--ink);">今日要点：</div>
      <ul class="highlights-list">${highlightsHtml}</ul>

      ${suggestionsHtml}

      <div class="trend-section">
        <div class="trend-header">
          <span class="trend-title">近 7 天趋势</span>
          <div class="trend-tabs">
            <button type="button" class="trend-tab-btn ${timelineState.trendTab === 'feeding' ? 'active' : ''}" data-trend-tab="feeding">喂奶次数</button>
            <button type="button" class="trend-tab-btn ${timelineState.trendTab === 'sleep' ? 'active' : ''}" data-trend-tab="sleep">睡眠时长</button>
          </div>
        </div>
        <div class="trend-bars-container">${trendBarsHtml}</div>
      </div>

      <p class="summary-disclaimer">${escapeHtml(disclaimerText)}</p>
    `;

    // Bind Summary Card Events
    const narrativeBtn = card.querySelector('#btnAiNarrative');
    if (narrativeBtn) {
      narrativeBtn.onclick = () => loadSummary(true);
    }

    card.querySelectorAll('.trend-tab-btn').forEach(btn => {
      btn.onclick = () => {
        timelineState.trendTab = btn.dataset.trendTab;
        renderSummary();
      };
    });

    card.querySelectorAll('.trend-col').forEach(col => {
      col.onclick = () => {
        const d = col.dataset.trendDate;
        if (d && d !== timelineState.date) {
          setDate(d);
        }
      };
    });
  }

  /* ========================================================
     Render: Unified Timeline Card
     ======================================================== */
  function renderTimeline() {
    const card = document.getElementById('unifiedTimelineCard');
    if (!card) return;

    if (timelineState.timelineError && !timelineState.timelineData) {
      card.innerHTML = `
        <div class="unified-header">
          <h3 class="unified-title">今日统一时间线</h3>
        </div>
        <div class="activity-form-error visible" style="display:block;">
          无法加载时间线：${escapeHtml(timelineState.timelineError)}
        </div>
      `;
      return;
    }

    if (!timelineState.timelineData) {
      card.innerHTML = `
        <div class="unified-header">
          <h3 class="unified-title">今日统一时间线</h3>
        </div>
        <div class="today-empty-tip">正在加载全景时间线…</div>
      `;
      return;
    }

    const data = timelineState.timelineData;
    const summary = data.summary || {};
    const rawItems = Array.isArray(data.items) ? data.items : [];

    // 1. Ribbon Track & Detail Panel
    const dateStr = (data && (data.from || data.date)) || timelineState.date || getTodayDateString();
    const startOfDayMs = Date.parse(`${dateStr}T00:00:00+08:00`);
    const endOfDayMs = startOfDayMs + 86400000;
    const isToday = dateStr === getTodayDateString();
    const nowMs = Date.now();

    function getSegmentBounds(item) {
      const itemStartMs = Date.parse(item.start_at);
      let itemEndMs;
      if (item.end_at) {
        itemEndMs = Date.parse(item.end_at);
      } else if (item.open) {
        itemEndMs = isToday ? Math.min(nowMs, endOfDayMs) : endOfDayMs;
      } else if (item.duration_seconds != null) {
        itemEndMs = itemStartMs + item.duration_seconds * 1000;
      } else {
        itemEndMs = itemStartMs;
      }
      const segStart = Math.max(startOfDayMs, Math.min(endOfDayMs, itemStartMs));
      const segEnd = Math.max(startOfDayMs, Math.min(endOfDayMs, itemEndMs));
      return { segStart, segEnd };
    }

    const activityItems = rawItems.filter(item => item.source === 'vision' && item.category === 'activity');
    const presenceItems = rawItems.filter(item => item.category === 'presence' && item.type === 'not_visible');

    const ribbonSegments = [];

    activityItems.forEach((item, idx) => {
      const bounds = getSegmentBounds(item);
      if (bounds.segEnd <= bounds.segStart) return;
      const leftPct = ((bounds.segStart - startOfDayMs) / 86400000) * 100;
      const widthPct = ((bounds.segEnd - bounds.segStart) / 86400000) * 100;
      const segId = item.id || `act-${idx}`;
      const isSelected = timelineState.selectedSegmentId === segId;
      const color = RIBBON_ACTIVITY_COLORS[item.type] || (item.type && item.type.startsWith('awake') ? '#7ca778' : '#a9b0ab');
      ribbonSegments.push(`
        <div class="ribbon-segment ${isSelected ? 'selected' : ''}" data-segment-id="${escapeHtml(segId)}" style="left:${leftPct.toFixed(2)}%;width:${widthPct.toFixed(2)}%;background:${color};" title="${escapeHtml(item.label || item.type || '')}"></div>
      `);
    });

    presenceItems.forEach((item, idx) => {
      const bounds = getSegmentBounds(item);
      if (bounds.segEnd <= bounds.segStart) return;
      const leftPct = ((bounds.segStart - startOfDayMs) / 86400000) * 100;
      const widthPct = ((bounds.segEnd - bounds.segStart) / 86400000) * 100;
      const segId = item.id || `pres-${idx}`;
      const isSelected = timelineState.selectedSegmentId === segId;
      ribbonSegments.push(`
        <div class="ribbon-segment ribbon-segment-not-visible ${isSelected ? 'selected' : ''}" data-segment-id="${escapeHtml(segId)}" style="left:${leftPct.toFixed(2)}%;width:${widthPct.toFixed(2)}%;" title="不在画面"></div>
      `);
    });

    const allRibbonSegments = [...activityItems, ...presenceItems];
    const selectedSegment = timelineState.selectedSegmentId
      ? allRibbonSegments.find((item, idx) => {
          const segId = item.id || (item.category === 'activity' ? `act-${activityItems.indexOf(item)}` : `pres-${presenceItems.indexOf(item)}`);
          return segId === timelineState.selectedSegmentId;
        })
      : null;

    let detailPanelHtml = '';
    if (selectedSegment) {
      const bounds = getSegmentBounds(selectedSegment);
      const startTime = formatTime(selectedSegment.start_at);
      const endTime = selectedSegment.open ? '现在' : (selectedSegment.end_at ? formatTime(selectedSegment.end_at) : formatTime(new Date(bounds.segEnd)));
      const durationSec = selectedSegment.duration_seconds != null
        ? selectedSegment.duration_seconds
        : Math.round((bounds.segEnd - bounds.segStart) / 1000);
      const durationText = formatDurationText(durationSec);

      let statusLabel = selectedSegment.label || selectedSegment.type || '未知';
      if (selectedSegment.category === 'presence' && selectedSegment.type === 'not_visible') {
        statusLabel = '不在画面';
      } else if (selectedSegment.type === 'sleeping') {
        statusLabel = '睡着';
      } else if (selectedSegment.type === 'awake') {
        statusLabel = '清醒';
      }

      const segStartMs = bounds.segStart;
      const segEndMs = bounds.segEnd;
      const obsList = Array.isArray(timelineState.observations) ? timelineState.observations : [];
      const overlapping = obsList.filter(obs => {
        if (!obs) return false;
        const obsStartMs = (Number(obs.ts) || 0) * 1000;
        const obsEndSec = obs.end_ts != null ? Number(obs.end_ts) : ((Number(obs.ts) || 0) + (Number(obs.duration_seconds) || 0));
        const obsEndMs = Math.max(obsStartMs, obsEndSec * 1000);
        return obsStartMs <= segEndMs && obsEndMs >= segStartMs;
      });

      let obsContentHtml = '';
      if (overlapping.length > 0) {
        obsContentHtml = `
          <div class="ribbon-detail-obs-list">
            ${overlapping.map(obs => `
              <div class="ribbon-detail-obs-item">
                <span class="ribbon-detail-obs-time">${formatTime(obs.ts)}</span>
                <span class="ribbon-detail-obs-desc">${escapeHtml(obs.description || obs.suggested_state || '模型观察记录')}</span>
              </div>
            `).join('')}
          </div>
        `;
      } else {
        obsContentHtml = `<div class="ribbon-detail-obs-empty">这段时间没有模型描述</div>`;
      }

      detailPanelHtml = `
        <div class="ribbon-detail-panel" id="ribbonDetailPanel">
          <div class="ribbon-detail-header">
            <div class="ribbon-detail-status">
              <strong>${escapeHtml(statusLabel)}</strong>
              <span class="ribbon-detail-time">${startTime} ~ ${endTime} (${durationText})</span>
            </div>
            <button type="button" class="ribbon-detail-close" aria-label="关闭面板">✕</button>
          </div>
          <div class="ribbon-detail-body">
            ${obsContentHtml}
          </div>
        </div>
      `;
    }

    // 2. Metric Summary Grid (Parent confirmed metrics)
    const feedingCount = summary.feeding_count != null ? summary.feeding_count : '—';
    const feedingMl = summary.feeding_ml != null ? `${summary.feeding_ml} ml` : '—';
    const sleepDur = formatDurationText(summary.parent_sleep_seconds);
    const diaperCount = summary.diaper_count != null ? `${summary.diaper_count} 次` : '—';
    const interactCount = summary.interaction_count != null ? `${summary.interaction_count} 次` : '—';
    const cryCount = summary.cry_count != null ? `${summary.cry_count} 段` : '—';
    const cryDur = formatCryDuration(summary.cry_seconds);

    // 3. Camera stats
    const visualStats = summary.visual_activity_seconds || {};
    const visualSleepStr = formatDurationText(visualStats.sleeping);
    const visualAwakeStr = formatDurationText(visualStats.awake);
    const visualAvailable = data.visual?.available !== false;

    // 4. Filter stream items
    const activeFilter = timelineState.activeFilter || 'all';

    const filteredItems = rawItems.filter(item => {
      if (!item) return false;
      // Default: exclude camera posture and presence
      if (item.category === 'posture' || item.category === 'presence') return false;

      // Camera activity: only list sleeping
      if (item.source === 'vision' && item.category === 'activity') {
        if (item.type !== 'sleeping') return false;
      }

      // Single-select filter
      if (activeFilter === 'all') return true;
      if (activeFilter === 'sleep') {
        return item.type === 'sleeping' || (item.category === 'care' && item.type === 'sleep') || item.category === 'sleep';
      }
      if (activeFilter === 'feeding') {
        return item.source === 'parent_chat' || item.source === 'parent_manual' || item.category === 'care';
      }
      if (activeFilter === 'cry') {
        return item.category === 'cry' || (item.source === 'audio' && item.category === 'cry');
      }
      if (activeFilter === 'interaction') {
        return item.category === 'interaction' || item.source === 'interaction';
      }
      return true;
    });

    // 5. Stream Items HTML
    let streamHtml = '';
    if (filteredItems.length === 0) {
      streamHtml = `<div class="timeline-empty-tip">没有符合当前筛选条件的时间线记录</div>`;
    } else {
      streamHtml = filteredItems.map(item => {
        let sourceClass = 'source-care';
        let sourceLabel = '家长记录';
        if (item.source === 'audio' && item.category === 'cry') {
          sourceClass = 'source-audio';
          sourceLabel = '麦克风';
        } else if (item.source === 'vision') {
          sourceClass = 'source-vision';
          sourceLabel = item.category === 'moment' ? '抓拍' : '摄像头';
        } else if (item.source === 'interaction' || item.category === 'interaction') {
          sourceClass = 'source-interaction';
          sourceLabel = '亲子互动';
        }

        const startTime = formatTime(item.start_at);
        let timeRange = startTime;
        if (item.open) {
          timeRange = `${startTime} ~ 进行中`;
        } else if (item.end_at) {
          timeRange = `${startTime} ~ ${formatTime(item.end_at)}`;
        }

        let reactionBadge = '';
        if (item.reaction && REACTION_CONFIG[item.reaction]) {
          const r = REACTION_CONFIG[item.reaction];
          reactionBadge = `<span class="timeline-item-reaction-pill">${r.emoji} ${r.label}</span>`;
        }

        const notesHtml = item.notes ? `<span class="timeline-item-notes">(${escapeHtml(item.notes)})</span>` : '';

        return `
          <div class="timeline-stream-item ${sourceClass}">
            <div class="timeline-item-meta">
              <span class="timeline-item-time">${timeRange}</span>
              <span class="timeline-source-badge ${sourceClass}">${sourceLabel}</span>
            </div>
            <div class="timeline-item-content">
              <strong>${escapeHtml(item.label || item.type || '')}</strong>
              ${reactionBadge}
              ${notesHtml}
            </div>
          </div>
        `;
      }).join('');
    }

    // 6. Warning bar
    const warningText = (data.warnings && data.warnings[0]) || '';
    const warningHtml = warningText
      ? `<div class="timeline-warning-bar">${escapeHtml(warningText)}</div>`
      : '';

    const truncatedHtml = data.truncated
      ? `<div class="timeline-truncated-tip">记录较多，仅展示部分条目</div>`
      : '';

    card.innerHTML = `
      <div class="unified-header">
        <h3 class="unified-title">今日统一时间线</h3>
      </div>

      <div class="timeline-ribbon-card">
        <div class="ribbon-track" id="ribbonTrack">
          ${ribbonSegments.join('')}
        </div>
        <div class="ribbon-scale">
          <span>0:00</span>
          <span>6:00</span>
          <span>12:00</span>
          <span>18:00</span>
          <span>24:00</span>
        </div>
        <div class="ribbon-legend">
          <span class="ribbon-legend-item"><i style="background:#7e91a6;"></i>睡眠</span>
          <span class="ribbon-legend-item"><i style="background:#7ca778;"></i>清醒</span>
          <span class="ribbon-legend-item"><i style="background:#a9b0ab;"></i>未知</span>
          <span class="ribbon-legend-item"><i class="striped"></i>不在画面</span>
        </div>
        ${detailPanelHtml}
      </div>

      <div class="timeline-summary-grid">
        <div class="timeline-stat-box">
          <span>喂奶总量</span>
          <strong>${feedingMl}</strong>
          <small style="color:var(--muted); font-size:10px;">共 ${feedingCount} 次</small>
        </div>
        <div class="timeline-stat-box">
          <span>家长记录睡眠</span>
          <strong>${sleepDur}</strong>
        </div>
        <div class="timeline-stat-box">
          <span>换尿布</span>
          <strong>${diaperCount}</strong>
        </div>
        <div class="timeline-stat-box">
          <span>亲子互动</span>
          <strong>${interactCount}</strong>
        </div>
        <div class="timeline-stat-box">
          <span>哭声</span>
          <strong>${cryCount}</strong>
          <small style="color:var(--muted); font-size:10px;">${cryDur !== '—' ? `共 ${cryDur}` : '—'}</small>
        </div>
      </div>

      <div class="timeline-visual-notice-box">
        <div class="timeline-visual-notice-left">
          <span>📷 摄像头观察（仅供参考）</span>
          ${!visualAvailable ? '<span style="color:#ef4444;">(暂无数据)</span>' : ''}
        </div>
        <div class="timeline-visual-notice-durations">
          <span>睡着: ${visualSleepStr}</span>
          <span>清醒: ${visualAwakeStr}</span>
        </div>
      </div>

      <div class="timeline-filters-bar" role="tablist" aria-label="时间线筛选">
        <button type="button" class="timeline-filter-pill ${activeFilter === 'all' ? 'active' : ''}" data-filter="all">全部</button>
        <button type="button" class="timeline-filter-pill ${activeFilter === 'sleep' ? 'active' : ''}" data-filter="sleep">睡眠</button>
        <button type="button" class="timeline-filter-pill ${activeFilter === 'feeding' ? 'active' : ''}" data-filter="feeding">喂养</button>
        <button type="button" class="timeline-filter-pill ${activeFilter === 'cry' ? 'active' : ''}" data-filter="cry">哭声</button>
        <button type="button" class="timeline-filter-pill ${activeFilter === 'interaction' ? 'active' : ''}" data-filter="interaction">互动</button>
      </div>

      ${warningHtml}

      <div class="timeline-stream-container">
        ${streamHtml}
      </div>

      ${truncatedHtml}
    `;

    // Event delegation on card
    card.onclick = (e) => {
      const target = e.target;
      if (!target) return;

      const pill = (target.closest && target.closest('.timeline-filter-pill')) || (target.classList?.contains('timeline-filter-pill') ? target : null);
      if (pill && pill.dataset?.filter) {
        timelineState.activeFilter = pill.dataset.filter;
        renderTimeline();
        return;
      }

      const seg = (target.closest && target.closest('.ribbon-segment')) || (target.classList?.contains('ribbon-segment') ? target : null);
      if (seg && seg.dataset?.segmentId) {
        const segId = seg.dataset.segmentId;
        timelineState.selectedSegmentId = (timelineState.selectedSegmentId === segId ? null : segId);
        renderTimeline();
        return;
      }

      const closeBtn = (target.closest && target.closest('.ribbon-detail-close')) || (target.classList?.contains('ribbon-detail-close') ? target : null);
      if (closeBtn) {
        timelineState.selectedSegmentId = null;
        renderTimeline();
        return;
      }
    };
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
    const scopeSelect = document.getElementById('recordsScopeSelect');
    if (scopeSelect) {
      scopeSelect.value = timelineState.scope;
      scopeSelect.onchange = (e) => setScope(e.target.value);
    }
    updateScopeUI();

    const dateInput = document.getElementById('recordDate');
    if (dateInput) {
      if (dateInput.value) {
        timelineState.date = dateInput.value;
      } else {
        dateInput.value = timelineState.date;
      }
      dateInput.addEventListener('change', () => {
        if (dateInput.value && dateInput.value !== timelineState.date) {
          setDate(dateInput.value);
        }
      });
    }

    loadSummary();
    loadUnifiedTimeline();
  }

  const TimelineController = {
    state: timelineState,
    init,
    setScope,
    setDate,
    setFilter: (f) => { timelineState.activeFilter = f; renderTimeline(); },
    selectSegment: (id) => {
      timelineState.selectedSegmentId = (timelineState.selectedSegmentId === id ? null : id);
      renderTimeline();
    },
    setObservations: (items) => {
      timelineState.observations = Array.isArray(items) ? items : [];
      renderTimeline();
    },
    loadSummary,
    loadUnifiedTimeline,
    renderSummary,
    renderTimeline,
    setMockHandler: (fn) => { timelineState.mockHandler = fn; },
  };

  if (typeof window !== 'undefined') {
    window.TimelineController = TimelineController;
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = TimelineController;
  }

  if (typeof window !== 'undefined' && typeof document !== 'undefined' && !window.__TEST_ENV__ && typeof module === 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        if (document.getElementById('dailySummaryCard')) {
          TimelineController.init();
        }
      });
    } else {
      if (document.getElementById('dailySummaryCard')) {
        TimelineController.init();
      }
    }
  }

})();
