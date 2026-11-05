const state = {
  status: null,
  alertsEnabled: false,
  audioContext: null,
  timer: null,
  snapshotTimer: null,
  cryDetection: null,
  cameraMode: "initial",
  accessMode: document.querySelector('meta[name="bmon-access-mode"]')?.content || "local",
  previousActivity: null,
  installPrompt: null,
  photoItems: [],
  photoFilter: "all",
  access: null,
  player: null,
  playerTimer: null,
  voiceTimer: null,
  volumeTimer: null,
  playerSeeking: false,
  observationMode: "infant",
};

const activityLabels = {
  sleeping: "正在睡觉",
  awake: "已经醒来",
  awake_happy: "开心醒着",
  awake_unhappy: "有点不开心",
  crying: "正在大哭",
  unknown: "正在检测",
};
const postureLabels = {
  supine: "仰睡",
  side: "侧睡",
  prone: "趴睡",
  left_side: "左侧睡",
  right_side: "右侧睡",
  left_prone: "左趴睡",
  right_prone: "右趴睡",
  unknown: "检测中",
};
const expressionLabels = {
  calm: "平静", happy: "开心", unhappy: "不开心", crying: "哭泣",
  neutral: "中性", unknown: "无法确认",
};
const activityColors = {
  sleeping: "#7e91a6", awake: "#7ca778", awake_happy: "#71a26c",
  awake_unhappy: "#cf9578", crying: "#c35f64", unknown: "#a9b0ab",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function formatDuration(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours) return `${hours}小时 ${minutes}分钟`;
  if (minutes) return `${minutes}分钟`;
  return `${seconds}秒`;
}

// All times are shown in the household's timezone (Asia/Shanghai), whatever
// timezone the viewing phone or laptop is set to.
const SHANGHAI_PARTS = new Intl.DateTimeFormat("en-CA", {timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
function shanghaiParts(date) {
  const parts = {};
  for (const part of SHANGHAI_PARTS.formatToParts(date)) parts[part.type] = part.value;
  return parts;
}

function formatTime(ts) {
  if (!ts) return "—";
  const p = shanghaiParts(new Date(ts * 1000));
  return `${p.hour}:${p.minute}`;
}

function formatMediaTime(seconds) {
  seconds = Math.max(0, Math.floor(Number(seconds || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { toast.hidden = true; }, 2600);
}

async function api(path, options = {}) {
  const csrf = document.cookie
    .split("; ")
    .find(item => item.startsWith("bmon_csrf="))
    ?.split("=")
    .slice(1)
    .join("=");
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(csrf && options.method && options.method !== "GET"
        ? {"X-CSRF-Token": decodeURIComponent(csrf)}
        : {}),
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("登录已失效");
  }
  if (!response.ok) throw new Error(body.error || `请求失败 ${response.status}`);
  return body;
}

const observationModeLabels = {infant: "婴幼儿", adult_test: "成人测试", legacy_unknown: "历史模式未知"};

// The records list starts on the observation mode chosen in settings, so adult
// testing shows its own records without switching the dropdown by hand.
function syncRecordMode(mode) {
  if (!mode || state.observationMode === mode) return;
  state.observationMode = mode;
  if (document.querySelector('.page[data-page="records"].active')) {
    loadTimeline();
    if (window.TimelineController) {
      window.TimelineController.loadUnifiedTimeline?.();
    }
  }
}

function renderStatus(data) {
  syncRecordMode(data.observation_mode);
  if (!$("#observationSubjectSwitch").disabled) {
    $("#observationSubjectSwitch").checked = !!data.person_test_mode;
  }
  const previousActivity = state.previousActivity;
  state.status = data;
  const cameraOnline = data.camera.online;
  const lowLight = cameraOnline && !!data.low_light;
  const activityValid = data.observation_valid !== false || data.activity.source === "manual";
  const postureValid = data.observation_valid !== false || data.posture.source === "manual";
  const activity = cameraOnline && activityValid ? data.activity.value : "unknown";
  state.previousActivity = activity;
  const posture = cameraOnline && postureValid ? data.posture.value : "unknown";
  $("#activityTitle").textContent = !cameraOnline
    ? "等待摄像头画面"
    : lowLight ? "光线不足" : (activityLabels[activity] || activity);
  $("#activityChip").textContent = !cameraOnline
    ? "摄像头离线"
    : (data.activity.source === "manual" ? "手动校正" : "当前状态");
  $("#activityDuration").textContent = !cameraOnline
    ? "画面恢复后自动分析"
    : lowLight
      ? "视觉分析已暂停，夜间睡眠请告诉 AI 助手"
    : !activityValid
      ? "上次结果已过期，等待新的成功观察"
    : activity === "unknown"
      ? "等待识别结果"
    : `已持续 ${formatDuration(data.activity.duration_seconds)}`;
  $("#postureTitle").textContent = !cameraOnline
    ? "等待画面"
    : lowLight ? "暂停识别" : (postureLabels[posture] || posture);
  $("#postureDuration").textContent = !cameraOnline
    ? "摄像头恢复后重新判断"
    : lowLight
      ? "有亮光后自动恢复"
    : !postureValid
      ? "上次结果已过期，等待新的成功观察"
    : posture === "unknown"
      ? "等待识别结果"
    : `已持续 ${formatDuration(data.posture.duration_seconds)}`;

  const online = cameraOnline;
  $("#connectionPill").className = `connection-pill ${online ? "online" : "offline"}`;
  $("#connectionPill span").textContent = online
    ? (state.accessMode === "remote" ? "远程在线" : "本地在线")
    : "摄像头离线";
  $("#cameraOffline").hidden = online;
  $("#cameraError").textContent = online ? "" : "正在自动重连";
  $("#cameraLiveBadge").classList.toggle("offline", !online);
  $("#cameraLiveBadge span").textContent = online ? "LIVE" : "OFFLINE";
  const modelOnline = online && data.model.online;
  const modelError = data.model.enabled && !!data.model.last_error;
  $("#modelTitle").textContent = !online
    ? "等待摄像头"
    : lowLight ? "已暂停"
    : !data.model.enabled ? "已关闭"
    : modelError ? "识别出错"
    : modelOnline && data.observation_valid === false ? "结果过期"
    : modelOnline ? "运行中" : "正在连接";
  $("#modelTitle").className = modelError || (modelOnline && data.observation_valid === false) ? "state-warning" : "";
  $("#modelDetail").textContent = !online
    ? "收到新画面后自动分析"
    : lowLight ? "光线不足，有亮光后恢复"
    : !data.model.enabled ? "可手动校正"
    : modelError ? "暂时无法识别，可手动校正"
    : modelOnline && data.observation_valid === false ? "等待新的识别结果"
    : modelOnline ? "本地运行，不上传" : "等待本地识别启动";
  $("#cameraSetting").textContent = `Camera · ${data.camera.resolution}`;
  $("#cameraSettingBadge").textContent = online ? "正常" : "离线";
  $("#cameraSettingBadge").className = online ? "good" : "";
  $("#modelSettingBadge").textContent = modelOnline ? "运行中" : (online ? "正在连接" : "等待画面");
  $("#modelSettingBadge").className = modelOnline ? "good" : "";
  $("#modelSetting").textContent = modelOnline
    ? "本地运行，画面不上传"
    : (data.model.last_error || "本地运行，画面不上传");

  const storage = data.storage;
  const storageText = storage.mounted ? "存储就绪" : "存储不可用";
  $("#diskPill").textContent = storageText;
  $("#diskPill").className = `disk-pill ${storage.warning ? "warning" : ""}`;
  $("#storageSetting").textContent = "设备本地存储";
  $("#storageSettingBadge").textContent = storageText;
  $("#storageSettingBadge").className = storage.mounted && !storage.warning ? "good" : "";

  $$('[data-category="activity"]').forEach(button => button.classList.toggle("selected", button.dataset.value === activity));
  $$('[data-category="posture"]').forEach(button => button.classList.toggle("selected", button.dataset.value === posture));
  if (
    state.accessMode === "remote"
    && previousActivity === "sleeping"
    && ["awake", "awake_happy", "awake_unhappy", "crying"].includes(activity)
  ) {
    triggerWakeAlert();
  }

  renderCryStatus(data.cry);
  const cry = data.cry || {};
  const cryAlert = $("#cryAlertBanner");
  if (cryAlert) {
    if (cry.active) {
      cryAlert.textContent = `检测到哭声 · ${formatTime(cry.since)} 起`;
      cryAlert.hidden = false;
    } else {
      cryAlert.hidden = true;
    }
  }
}

function renderCryStatus(cry) {
  const detection = state.cryDetection;
  const ready = cry && detection && detection.enabled && detection.ready && detection.running;
  $("#cryTitle").textContent = !ready ? "未开启" : cry.active ? "正在哭" : "安静";
  $("#cryTitle").className = ready && cry.active ? "state-warning" : "";
  $("#cryDetail").textContent = !ready ? "尚未开始监听" : cry.active ? "仅记录时段，不录音" : "正在本地监听";
}

async function refreshStatus() {
  try {
    renderStatus(await api("/api/status"));
  } catch (error) {
    $("#connectionPill").className = "connection-pill offline";
    $("#connectionPill span").textContent = "服务离线";
  }
}

function connectEvents() {
  if (state.accessMode === "remote") return;
  const events = new EventSource("/api/events");
  events.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === "wake") triggerWakeAlert();
    if (message.type === "smile") {
      showToast("已把最清晰的笑脸保存到媒体存储");
      loadPhotos();
    }
    if (message.type === "voice") renderVoiceSettings(message);
    if (message.type === "cry") refreshStatus();
    if (message.type === "voice_record") {
      showToast(`语音记录：${message.message || ""}`);
      if (document.querySelector('.page[data-page="assistant"]')?.classList.contains("active")) {
        window.Assistant?.onPageShow?.();
      }
    }
    refreshStatus();
  };
  events.onerror = () => {};
}

function isIOSDevice() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function startSnapshotCamera() {
  if (state.snapshotTimer) return;
  state.cameraMode = "snapshot";
  const image = $("#cameraStream");
  const refresh = () => {
    if (document.visibilityState === "visible") {
      image.src = `/snapshot.jpg?t=${Date.now()}`;
    }
  };
  refresh();
  state.snapshotTimer = setInterval(refresh, state.accessMode === "remote" ? 1600 : 1000);
}

function configureCameraDisplay() {
  const image = $("#cameraStream");
  image.addEventListener("error", startSnapshotCamera);
  const forcedSnapshot = new URLSearchParams(window.location.search).has("snapshot");
  if (state.accessMode === "remote" || isIOSDevice() || forcedSnapshot) {
    startSnapshotCamera();
    return;
  }
  state.cameraMode = "mjpeg";
  image.src = `/stream.mjpg?v=${Date.now()}`;
}

function beep() {
  if (!state.audioContext) return;
  const ctx = state.audioContext;
  [0, .22, .44].forEach((offset, index) => {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    oscillator.frequency.value = [660, 820, 980][index];
    gain.gain.setValueAtTime(0.0001, ctx.currentTime + offset);
    gain.gain.exponentialRampToValueAtTime(.18, ctx.currentTime + offset + .02);
    gain.gain.exponentialRampToValueAtTime(.0001, ctx.currentTime + offset + .18);
    oscillator.connect(gain).connect(ctx.destination);
    oscillator.start(ctx.currentTime + offset);
    oscillator.stop(ctx.currentTime + offset + .2);
  });
}

function triggerWakeAlert() {
  if (!state.alertsEnabled) return;
  beep();
  if (navigator.vibrate) navigator.vibrate([300, 150, 300, 150, 500]);
  $("#wakeOverlay").hidden = false;
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification("Nestory：宝宝睡醒啦", {body: "请打开监控页面查看当前状态。"});
  }
}

async function enableAlerts() {
  state.audioContext = state.audioContext || new (window.AudioContext || window.webkitAudioContext)();
  await state.audioContext.resume();
  state.alertsEnabled = true;
  const button = $("#enableAlertButton");
  button.classList.add("enabled");
  button.querySelector("b").textContent = "已启用";
  if ("Notification" in window && window.isSecureContext && Notification.permission === "default") {
    await Notification.requestPermission();
  }
  beep();
  showToast("提醒已启用，请保持页面打开");
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches
    || window.navigator.standalone === true;
}

function configureAccessMode() {
  $("#remoteAccessSetting").hidden = false;
  $("#accessEyebrow").textContent = "NEST · STORY";
}

function renderAccessSettings(access) {
  state.access = access;
  $("#lanAccessSwitch").checked = access.lan_enabled;
  $("#lanAccessDetail").textContent = access.lan_enabled
    ? "仅已登录用户可查看监控"
    : "监控数据访问已关闭；采集仍在设备上运行";
  $("#privacyDetail").textContent = "所有访问均需登录；远程使用需配置 HTTPS。关闭访问不会停止采集。";
  $("#privacyBadge").textContent = "认证访问";
}

async function loadAccessSettings() {
  if (state.accessMode === "remote") return true;
  try {
    const access = await api("/api/access");
    renderAccessSettings(access);
    return access.lan_enabled;
  } catch (error) {
    showToast(`访问开关读取失败：${error.message}`);
    return true;
  }
}

async function changeAccessSetting(key, input) {
  const enabled = input.checked;
  if (
    key === "lan_enabled"
    && !enabled
    && !window.confirm("关闭监控访问后，画面、记录和照片将不可访问。设置页仍可打开并重新开启，确定继续吗？")
  ) {
    input.checked = true;
    return;
  }
  input.disabled = true;
  try {
    const access = await api("/api/access", {
      method: "POST",

      body: JSON.stringify({key, enabled}),
    });
    renderAccessSettings(access);
    showToast(`${key === "lan_enabled" ? "监控" : "外网"}访问已${enabled ? "开启" : "关闭"}`);
    if (key === "lan_enabled") setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    input.checked = !enabled;
    showToast(error.message);
  } finally {
    input.disabled = false;
  }
}

function renderVoiceSettings(voice) {
  const input = $("#voiceControlSwitch");
  input.checked = Boolean(voice.enabled);
  input.disabled = state.accessMode === "remote";
  $("#voiceWakeBadge").textContent = voice.wake_word || "佩奇";
  const schedule = `${voice.schedule_start || "07:00"}–${voice.schedule_end || "23:00"}`;
  $("#voiceWakeDetail").textContent = `每日 ${schedule} 监听，唤醒后八秒内说出指令；可以控制音乐，也可以说“记一下……”来记录`;
  const labels = {
    disabled: "已关闭，不监听环境声音",
    starting: "正在启动离线语音服务",
    listening: `已就绪，先说“${voice.wake_word || "佩奇"}”（${schedule}）`,
    outside_schedule: `当前不监听，将按每日 ${schedule} 自动运行`,
    waiting_command: "已唤醒，请在六秒内说出音乐指令",
    transcribing: "正在理解刚才说的音乐指令",
    executed: voice.last_result || "语音指令已执行",
    waiting_microphone: voice.error || "等待麦克风接入",
    runtime_missing: voice.error || "离线语音组件尚未准备完成",
    command_error: voice.error || "这条音乐指令暂时无法执行",
    error: voice.error || "语音服务异常",
  };
  $("#voiceControlDetail").textContent = state.accessMode === "remote"
    ? "语音监听只能在授权网络中开关"
    : (labels[voice.state] || "正在检查语音服务");

  const cry = voice.cry_detection || {};
  state.cryDetection = cry;
  if (state.status) renderCryStatus(state.status.cry);
  let cryText = "全天运行中 · 只记录哭声时间段，不保存录音";
  let cryBadge = "运行中";
  let isGood = true;
  if (cry.enabled === false) {
    cryText = "已关闭";
    cryBadge = "已关闭";
    isGood = false;
  } else if (cry.ready === false) {
    cryText = "哭声模型未就绪";
    cryBadge = "未就绪";
    isGood = false;
  } else if (!cry.running) {
    cryText = "正在启动，麦克风接入后自动开始";
    cryBadge = "启动中";
    isGood = false;
  }
  const cryDetail = $("#cryDetectionDetail");
  if (cryDetail) cryDetail.textContent = cryText;
  const cryBadgeEl = $("#cryDetectionBadge");
  if (cryBadgeEl) {
    cryBadgeEl.textContent = cryBadge;
    cryBadgeEl.className = isGood ? "good" : "";
  }
}

async function loadVoiceStatus() {
  try {
    renderVoiceSettings(await api("/api/voice"));
  } catch (error) {
    $("#voiceControlDetail").textContent = `语音状态读取失败：${error.message}`;
  }
}

async function changeObservationSubject(input) {
  const enabled = input.checked;
  input.disabled = true;
  try {
    const data = await api("/api/observation-subject", {
      method: "POST", body: JSON.stringify({person_test_mode: enabled}),
    });
    renderStatus(data);
    showToast(enabled ? "已切换成人 Prompt，等待重新识别" : "已恢复宝宝 Prompt，等待重新识别");
  } catch (error) {
    input.checked = !enabled;
    showToast(error.message);
  } finally {
    input.disabled = false;
  }
}

async function changeVoiceControl(input) {
  const enabled = input.checked;
  if (
    enabled
    && !window.confirm("开启后，摄像头麦克风会在每日 07:00–23:00 进行本地唤醒词检测，但不会保存录音。确定开启吗？")
  ) {
    input.checked = false;
    return;
  }
  input.disabled = true;
  try {
    const voice = await api("/api/voice", {
      method: "POST",

      body: JSON.stringify({enabled}),
    });
    renderVoiceSettings(voice);
    showToast(`语音控制已${enabled ? "开启" : "关闭"}`);
  } catch (error) {
    input.checked = !enabled;
    showToast(error.message);
  } finally {
    if (state.accessMode !== "remote") input.disabled = false;
  }
}

function renderLanDisabled() {
  $("#connectionPill").className = "connection-pill offline";
  $("#connectionPill span").textContent = "监控访问已关闭";
  $("#cameraOffline").hidden = false;
  $("#cameraError").textContent = "可在设置页重新开启监控访问";
  $("#cameraStream").removeAttribute("src");
  $("#activityTitle").textContent = "监控已暂停";
  $("#activityDuration").textContent = "设置页仍可使用";
  showPage("settings");
}

async function installApp() {
  if (state.installPrompt) {
    state.installPrompt.prompt();
    await state.installPrompt.userChoice;
    state.installPrompt = null;
    $("#installAppSetting").hidden = true;
    return;
  }
  if (isIOSDevice()) {
    showToast("请点 Safari 分享按钮，然后选择“添加到主屏幕”");
  } else {
    showToast("请在浏览器菜单中选择“安装应用”或“添加到主屏幕”");
  }
}

async function logoutRemote() {
  const csrf = document.cookie
    .split("; ")
    .find(item => item.startsWith("bmon_csrf="))
    ?.split("=")
    .slice(1)
    .join("=");
  await fetch("/logout", {
    method: "POST",
    headers: csrf ? {"X-CSRF-Token": decodeURIComponent(csrf)} : {},
  });
  window.location.assign("/login");
}

function configurePwa() {
  if (!isStandalone() && (state.accessMode === "remote" || window.isSecureContext)) {
    $("#installAppSetting").hidden = false;
  }
  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    state.installPrompt = event;
    $("#installAppSetting").hidden = false;
  });
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {});
  }
}

async function setManualState(button) {
  button.disabled = true;
  try {
    const cancelSelection = button.classList.contains("selected");
    const result = await api("/api/manual-state", {
      method: "POST",
      body: JSON.stringify({
        category: button.dataset.category,
        value: cancelSelection ? "unknown" : button.dataset.value,
      }),
    });
    await refreshStatus();
    if (cancelSelection) {
      showToast("已取消选择");
    } else {
      showToast(result.training_sample_saved
        ? "状态已更新，并保存为个体化训练样本"
        : "当前状态已更新");
    }
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function savePhoto() {
  const button = $("#savePhotoButton");
  button.disabled = true;
  try {
    const result = await api("/api/save-photo", {method: "POST", body: "{}"});
    const savedTypes = result.saved.map(item =>
      item.category === "activity"
        ? (activityLabels[item.value] || item.value)
        : (postureLabels[item.value] || item.value)
    );
    showToast(`照片已按 ${savedTypes.join("、")} 分类保存`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadTimeline() {
  const date = $("#recordDate")?.value || "";
  const mode = state.observationMode || "infant";
  try {
    const observations = await api(`/api/observations?date=${encodeURIComponent(date)}&mode=${encodeURIComponent(mode)}&limit=200`);
    renderObservations(observations?.items);
    if (typeof window !== "undefined" && window.TimelineController && typeof window.TimelineController.setObservations === "function") {
      window.TimelineController.setObservations(observations?.items || []);
    }
  } catch (error) {
    showToast(error.message);
  }
}

function renderObservations(items) {
  items = Array.isArray(items) ? items.filter(item => item != null) : [];
  const container = $("#observationList");
  container.replaceChildren();
  $("#observationCount").textContent = `${items.length} 条`;
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "timeline-empty";
    empty.textContent = "当天还没有大模型观察记录";
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
    const card = document.createElement("article");
    card.className = "observation-item";

    const header = document.createElement("div");
    header.className = "observation-header";
    const time = document.createElement("time");
    time.textContent = formatTime(item.ts);
    const confidence = document.createElement("span");
    const relevantConfidence = item.baby_visible === false
      ? item.visibility_confidence
      : item.confidence;
    confidence.textContent = `${formatDuration(item.duration_seconds)} · 可信度 ${Math.round(Number(relevantConfidence || 0) * 100)}%`;
    header.append(time, confidence);

    const title = document.createElement("strong");
    const sleepLabel = item.sleep_state === "sleeping"
      ? "正在睡觉"
      : item.sleep_state === "awake"
      ? "清醒"
      : item.sleep_state === "drowsy"
      ? "有些困倦"
      : "观察记录";
    title.textContent = `${item.suggested_state || sleepLabel} · ${observationModeLabels[item.observation_mode] || "历史模式未知"}`;

    const description = document.createElement("p");
    description.textContent = item.description || "模型没有提供可确认的画面描述。";

    const tags = document.createElement("div");
    tags.className = "observation-tags";
    const values = [
      item.baby_visible === false
        ? (item.observation_mode === "adult_test" ? "未看见测试成人" : item.observation_mode === "infant" ? "未看见宝宝" : "对象未见（模式未知）")
        : item.baby_visible === true
        ? (item.observation_mode === "adult_test" ? "看见测试成人" : item.observation_mode === "infant" ? "看见宝宝" : "对象可见（模式未知）")
        : "可见性未知",
      item.sleep_state === "sleeping" ? "睡眠" : item.sleep_state === "awake" ? "清醒" : item.sleep_state === "drowsy" ? "困倦" : "睡醒未知",
      postureLabels[item.posture] || item.posture,
      expressionLabels[item.expression] || item.expression,
      ...(item.observed_actions || []).slice(0, 3),
    ];
    for (const value of values.filter(Boolean)) {
      const tag = document.createElement("span");
      tag.textContent = value;
      tags.appendChild(tag);
    }
    card.append(header, title, description, tags);
    container.appendChild(card);
  }
}

function photoMatchesFilter(item) {
  if (state.photoFilter === "all") return true;
  if (state.photoFilter === "smile") return item.kind === "smile";
  if (state.photoFilter.startsWith("expression:")) {
    return item.kind === "expression"
      && item.value === state.photoFilter.slice("expression:".length);
  }
  return item.value === state.photoFilter;
}

function renderPhotos() {
  const items = state.photoItems.filter(photoMatchesFilter);
  const grid = $("#smileGrid");
  grid.replaceChildren();
  $("#smileEmpty").hidden = items.length > 0;
  $("#photoEmptyTitle").textContent = state.photoItems.length ? "该分类还没有照片" : "还没有收藏";
  $("#photoEmptyText").textContent = state.photoItems.length
    ? "可以切换其他分类，或从监控页手动保存照片。"
    : "自动检测或手动保存的照片会出现在这里。";

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "smile-item";

    const link = document.createElement("a");
    link.className = "photo-link";
    link.href = item.original_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.setAttribute("aria-label", `查看${item.label}原图`);

    const image = document.createElement("img");
    image.src = item.thumbnail_url;
    image.alt = `${item.label}照片`;
    image.loading = "lazy";
    link.appendChild(image);

    const caption = document.createElement("span");
    caption.className = "smile-caption";
    const title = document.createElement("strong");
    title.textContent = `${item.label} · ${observationModeLabels[item.observation_mode] || "历史模式未知"}`;
    const details = document.createElement("span");
    const day = shanghaiParts(new Date(item.ts * 1000));
    details.textContent = `${day.year}/${Number(day.month)}/${Number(day.day)} · ${formatTime(item.ts)}`;
    caption.append(title, details);
    link.appendChild(caption);

    const trash = document.createElement("button");
    trash.className = "photo-trash-button";
    trash.type = "button";
    trash.textContent = "移入回收站";
    trash.addEventListener("click", () => trashPhoto(item));
    card.append(link, trash);
    grid.appendChild(card);
  }
}

async function loadPhotos() {
  try {
    const {items} = await api("/api/photos?limit=300");
    state.photoItems = items;
    renderPhotos();
  } catch (error) {
    state.photoItems = [];
    renderPhotos();
    $("#smileEmpty").hidden = false;
  }
}

async function trashPhoto(item) {
  if (!window.confirm(`将这张“${item.label}”照片移入媒体存储回收站？`)) return;
  try {
    await api("/api/photos/trash", {
      method: "POST",
      body: JSON.stringify({id: item.id, event_type: item.event_type}),
    });
    state.photoItems = state.photoItems.filter(photo => photo.id !== item.id);
    renderPhotos();
    showToast("照片已移入回收站，可从硬盘恢复");
  } catch (error) {
    showToast(error.message);
  }
}

function selectPhotoFilter(button) {
  state.photoFilter = button.dataset.photoFilter;
  $$("[data-photo-filter]").forEach(item => {
    item.classList.toggle("selected", item === button);
  });
  renderPhotos();
}

const playerStateLabels = {
  connecting: "正在连接音响",
  error: "播放异常",
  paused: "已暂停",
  playing: "正在播放",
  stopped: "音响待机",
};

function categorySymbol(name) {
  if (name.includes("钢琴")) return "♩";
  if (name.includes("英文")) return "A";
  if (name.includes("法语") || name.includes("法文")) return "F";
  if (name.includes("古诗")) return "诗";
  if (name.includes("故事")) return "书";
  return "♫";
}

function renderPlayCategories(data) {
  const container = $("#playCategoryGrid");
  container.replaceChildren();
  if (!data.categories.length) {
    const empty = document.createElement("div");
    empty.className = "play-category-loading";
    empty.textContent = "媒体存储中还没有可播放的类目";
    container.appendChild(empty);
    return;
  }
  for (const category of data.categories) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "play-category-card";
    button.classList.toggle("selected", category.name === data.category);
    button.disabled = category.ready_count === 0;
    button.addEventListener("click", () => selectPlayCategory(category.name, button));

    const symbol = document.createElement("span");
    symbol.className = "category-symbol";
    symbol.textContent = categorySymbol(category.name);
    const title = document.createElement("strong");
    title.textContent = category.name;
    const detail = document.createElement("small");
    detail.textContent = category.count === 0
      ? "等待上传音频"
      : category.preparing_count
      ? `${category.ready_count}/${category.count} 首可播放`
      : category.unplayable_count
      ? `${category.ready_count} 首 · ${category.unplayable_count} 个无音轨`
      : `${category.count} 首 · 随机播放`;
    button.append(symbol, title, detail);
    container.appendChild(button);
  }
}

function renderPlayer(data) {
  state.player = data;
  renderPlayCategories(data);
  const active = ["connecting", "playing", "paused"].includes(data.state);
  const pill = $("#speakerPill");
  pill.className = `speaker-pill ${active ? "playing" : data.state === "error" ? "error" : ""}`;
  pill.querySelector("span").textContent = playerStateLabels[data.state] || data.state;
  $("#playerCategory").textContent = data.category || "尚未选择类目";
  $("#playerTitle").textContent = data.current_title || "选一个喜欢的类目吧";
  $("#playerDetail").textContent = data.error
    || data.prepare_error
    || (data.current_title
      ? `本轮还剩 ${data.queue_remaining} 首`
      : `选择类目后开始播放`);

  const art = $("#recordArt");
  art.classList.toggle("spinning", ["connecting", "playing", "paused"].includes(data.state));
  art.classList.toggle("paused", data.state === "paused");

  const pauseButton = $("#playPauseButton");
  const isPaused = data.state === "paused";
  pauseButton.dataset.playerAction = isPaused ? "resume" : "pause";
  pauseButton.firstChild.textContent = isPaused ? "▶" : "Ⅱ";
  pauseButton.querySelector("small").textContent = isPaused ? "继续" : "暂停";
  pauseButton.disabled = !data.current_title || ["stopped", "error"].includes(data.state);
  $("[data-player-action='previous']").disabled = !data.can_previous;
  $("[data-player-action='next']").disabled = !data.current_title;
  $("[data-player-action='stop']").disabled = !data.current_title;

  const progress = $("#playerProgress");
  progress.max = Math.max(1, Math.floor(data.duration_seconds || 0));
  progress.disabled = !data.seekable;
  if (!state.playerSeeking) progress.value = Math.floor(data.position_seconds || 0);
  $("#playerCurrentTime").textContent = formatMediaTime(
    state.playerSeeking ? progress.value : data.position_seconds
  );
  $("#playerDuration").textContent = formatMediaTime(data.duration_seconds);

  const volume = $("#playerVolume");
  if (document.activeElement !== volume) volume.value = data.volume;
  $("#playerVolumeLabel").textContent = `${volume.value}%`;
}

async function loadPlayer(silent = true) {
  if (state.accessMode === "remote") return;
  try {
    renderPlayer(await api("/api/player"));
  } catch (error) {
    if (!silent) showToast(error.message);
  }
}

async function selectPlayCategory(category, button) {
  button.disabled = true;
  try {
    renderPlayer(await api("/api/player/category", {
      method: "POST",
      body: JSON.stringify({category}),
    }));
    showToast(`开始随机播放“${category}”`);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function playerControl(button) {
  const action = button.dataset.playerAction;
  button.disabled = true;
  try {
    renderPlayer(await api("/api/player/control", {
      method: "POST",
      body: JSON.stringify({action}),
    }));
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

function changePlayerVolume(input) {
  $("#playerVolumeLabel").textContent = `${input.value}%`;
  clearTimeout(state.volumeTimer);
  state.volumeTimer = setTimeout(async () => {
    try {
      renderPlayer(await api("/api/player/volume", {
        method: "POST",
        body: JSON.stringify({volume: Number(input.value)}),
      }));
    } catch (error) {
      showToast(error.message);
    }
  }, 250);
}

function previewPlayerSeek(input) {
  state.playerSeeking = true;
  $("#playerCurrentTime").textContent = formatMediaTime(input.value);
}

async function seekPlayer(input) {
  const positionSeconds = Number(input.value);
  try {
    const data = await api("/api/player/seek", {
      method: "POST",
      body: JSON.stringify({position_seconds: positionSeconds}),
    });
    state.playerSeeking = false;
    renderPlayer(data);
    showToast(`已跳转到 ${formatMediaTime(positionSeconds)}`);
  } catch (error) {
    state.playerSeeking = false;
    showToast(error.message);
    loadPlayer();
  }
}

function showPage(target) {
  document.body.classList.toggle("page-assistant-active", target === "assistant");
  $$(".page").forEach(page => page.classList.toggle("active", page.dataset.page === target));
  $$(".bottom-nav button").forEach(button => button.classList.toggle("active", button.dataset.target === target));
  const dataAccessAllowed = state.accessMode === "remote" || state.access?.lan_enabled !== false;
  if (target === "records" && dataAccessAllowed) {
    loadTimeline();
    if (window.TimelineController) {
      const dateVal = $("#recordDate")?.value;
      if (dateVal) window.TimelineController.state.date = dateVal;
      window.TimelineController.loadSummary?.();
      window.TimelineController.loadUnifiedTimeline?.();
    }
  }
  if (target === "smiles" && dataAccessAllowed) loadPhotos();
  if (target === "play" && dataAccessAllowed) {
    loadPlayer(false);
    if (window.PlayInteractions) {
      if (!window.PlayInteractions.state.activities.length) {
        window.PlayInteractions.loadActivities?.();
      }
      window.PlayInteractions.loadTodayInteractions?.();
    }
  }
  if (target === "assistant") window.Assistant?.onPageShow?.();
  window.scrollTo({top: 0, behavior: "smooth"});
}

async function initialize() {
  configureAccessMode();
  configurePwa();
  const today = shanghaiParts(new Date());
  $("#recordDate").value = `${today.year}-${today.month}-${today.day}`;
  $("#recordDate").addEventListener("change", () => {
    loadTimeline();
    if (window.TimelineController && $("#recordDate").value) {
      window.TimelineController.setDate($("#recordDate").value);
    }
  });
  $("#enableAlertButton").addEventListener("click", enableAlerts);
  $("#dismissWake").addEventListener("click", () => { $("#wakeOverlay").hidden = true; });
  $("#savePhotoButton").addEventListener("click", savePhoto);
  $("#installAppButton").addEventListener("click", installApp);
  $("#logoutButton").addEventListener("click", logoutRemote);
  $("#lanAccessSwitch").addEventListener("change", event => changeAccessSetting("lan_enabled", event.target));
  $("#observationSubjectSwitch").addEventListener("change", event => changeObservationSubject(event.target));
  $("#voiceControlSwitch").addEventListener("change", event => changeVoiceControl(event.target));
  $$("[data-category]").forEach(button => button.addEventListener("click", () => setManualState(button)));
  $$("[data-photo-filter]").forEach(button => button.addEventListener("click", () => selectPhotoFilter(button)));
  const photoMoreBtn = $("#photoFilterMoreBtn");
  if (photoMoreBtn) {
    photoMoreBtn.addEventListener("click", () => {
      const moreContainer = $("#photoFiltersMore");
      if (moreContainer) {
        moreContainer.hidden = !moreContainer.hidden;
        photoMoreBtn.textContent = moreContainer.hidden ? "更多 ▾" : "收起 ▴";
      }
    });
  }
  $$("[data-player-action]").forEach(button => button.addEventListener("click", () => playerControl(button)));
  $("#playerVolume").addEventListener("input", event => changePlayerVolume(event.target));
  $("#playerProgress").addEventListener("input", event => previewPlayerSeek(event.target));
  $("#playerProgress").addEventListener("change", event => seekPlayer(event.target));
  $$(".bottom-nav button").forEach(button => button.addEventListener("click", () => showPage(button.dataset.target)));
  const lanEnabled = await loadAccessSettings();
  if (!lanEnabled && state.accessMode === "local") {
    renderLanDisabled();
    return;
  }
  configureCameraDisplay();
  refreshStatus();
  loadVoiceStatus();
  connectEvents();
  state.timer = setInterval(refreshStatus, 5000);
  state.playerTimer = setInterval(() => {
    if ($('[data-page="play"]').classList.contains("active")) loadPlayer();
  }, 2000);
  state.voiceTimer = setInterval(loadVoiceStatus, 5000);
}

document.addEventListener("DOMContentLoaded", initialize);
