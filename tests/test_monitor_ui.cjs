// Exercise the monitor controller against the IDs present in the shipped page.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('static/index.html', 'utf8');
const source = fs.readFileSync('static/app.js', 'utf8');
const nodes = new Map();
function node() {
  return {
    textContent: '', className: '', hidden: false, disabled: false, checked: false,
    style: {}, listeners: {},
    classList: {
      toggle() {},
    },
    addEventListener(event, handler) { this.listeners[event] = handler; },
  };
}
for (const [, id] of html.matchAll(/id="([^"]+)"/g)) nodes.set(`#${id}`, node());
nodes.set('#connectionPill span', node());
nodes.set('#cameraLiveBadge span', node());
const context = {
  document: {
    visibilityState: 'visible',
    querySelector: selector => nodes.get(selector) || null,
    querySelectorAll: () => [],
    addEventListener() {},
  },
  window: {location: {search: ''}},
  navigator: {userAgent: 'desktop', platform: 'MacIntel', maxTouchPoints: 0},
  URLSearchParams,
  setInterval: () => 1,
};
vm.createContext(context);
vm.runInContext(source, context);

function state(expression) { return vm.runInContext(expression, context); }
function readyVoice(overrides = {}) {
  context.renderVoiceSettings({enabled: false, cry_detection: {
    enabled: true, ready: true, running: true, ...overrides,
  }});
}
function status() {
  return {
    person_test_mode: false, observation_mode: 'infant', observation_valid: true,
    low_light: false,
    camera: {online: true, resolution: '1280×720'},
    activity: {value: 'sleeping', source: 'qwen3-vl-2b', duration_seconds: 60},
    posture: {value: 'supine', source: 'qwen3-vl-2b', duration_seconds: 60},
    model: {enabled: true, online: true, name: 'Qwen3-VL-2B-Instruct', last_error: null},
    storage: {mounted: true, warning: false},
    cry: {active: false, since: null},
  };
}
const text = id => nodes.get(`#${id}`).textContent;

const monitor = html.split('data-page="monitor"')[1].split('data-page="play"')[0];
assert.equal((monitor.match(/id="cameraStream"/g) || []).length, 1);
assert.doesNotMatch(monitor, /heroPhoto|heroState|cameraResolution|EmotiEffLib/);
assert.doesNotMatch(source, /refreshHeroSnapshot|heroSnapshot|#heroPhoto|#cameraResolution/);

readyVoice();
context.renderStatus(status());
assert.equal(text('activityTitle'), '正在睡觉');
assert.equal(text('activityDuration'), '已持续 1分钟');
assert.equal(text('postureTitle'), '仰睡');
assert.equal(text('modelTitle'), '运行中');
assert.equal(text('modelDetail'), '本地运行，不上传');
assert.equal(text('cryTitle'), '安静');
assert.equal(nodes.get('#cryAlertBanner').hidden, true);

const stale = status();
stale.observation_valid = false;
context.renderStatus(stale);
assert.equal(text('activityTitle'), '正在检测');
assert.match(text('activityDuration'), /过期/);
assert.equal(text('postureTitle'), '检测中');
assert.match(text('postureDuration'), /过期/);
assert.equal(text('modelTitle'), '结果过期');

const dark = status();
dark.low_light = true;
context.renderStatus(dark);
assert.equal(text('activityTitle'), '光线不足');
assert.equal(text('postureTitle'), '暂停识别');
assert.equal(text('modelTitle'), '已暂停');
assert.match(text('modelDetail'), /光线不足/);

const offline = status();
offline.camera.online = false;
context.renderStatus(offline);
assert.equal(text('activityTitle'), '等待摄像头画面');
assert.equal(nodes.get('#cameraOffline').hidden, false);
assert.equal(text('cameraLiveBadge span'), 'OFFLINE');
assert.equal(text('modelTitle'), '等待摄像头');
context.renderStatus(status());
assert.equal(nodes.get('#cameraOffline').hidden, true);

const failed = status();
failed.model.online = false;
failed.model.last_error = 'model request failed';
context.renderStatus(failed);
assert.equal(text('modelTitle'), '识别出错');
assert.match(text('modelDetail'), /无法识别/);
assert.equal(nodes.get('#modelTitle').className, 'state-warning');
const disabled = status();
disabled.model.enabled = false;
disabled.model.online = false;
context.renderStatus(disabled);
assert.equal(text('modelTitle'), '已关闭');
context.renderStatus(status());
assert.equal(nodes.get('#modelTitle').className, '');

const crying = status();
crying.cry = {active: true, since: 1792400000};
context.renderStatus(crying);
assert.equal(text('cryTitle'), '正在哭');
assert.equal(nodes.get('#cryTitle').className, 'state-warning');
assert.equal(nodes.get('#cryAlertBanner').hidden, false);
assert.match(text('cryAlertBanner'), /检测到哭声/);
context.renderStatus(status());
assert.equal(text('cryTitle'), '安静');
assert.equal(nodes.get('#cryTitle').className, '');

const noCry = status();
delete noCry.cry;
context.renderStatus(noCry);
assert.equal(text('cryTitle'), '未开启');
assert.equal(nodes.get('#cryAlertBanner').hidden, true);
context.renderStatus(status());
for (const unavailable of [{enabled: false}, {ready: false}, {running: false}]) {
  readyVoice(unavailable);
  assert.equal(text('cryTitle'), '未开启');
}
readyVoice();
assert.equal(text('cryTitle'), '安静');

context.configureCameraDisplay();
assert.match(nodes.get('#cameraStream').src, /^\/stream\.mjpg\?v=/);
nodes.get('#cameraStream').listeners.error();
assert.equal(state('state.cameraMode'), 'snapshot');
assert.match(nodes.get('#cameraStream').src, /^\/snapshot\.jpg\?t=/);
console.log('Monitor normal, stale, low-light, offline, model errors, cry readiness and camera refresh passed');
