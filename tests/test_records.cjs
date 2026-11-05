// Exercise the actual record-page functions without a browser or server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const nodes = new Map();
function node() {
  return { textContent: '', innerHTML: '', children: [], value: '2026-09-20',
    replaceChildren() { this.children = []; },
    appendChild(child) { this.children.push(child); },
    append(...children) { this.children.push(...children); } };
}
const context = {
  state: {status: {}, observationMode: 'infant'},
  document: { createElement: node },
  $: id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
  formatDuration: n => `${n}`,
  formatTime: n => `${n}`,
  observationModeLabels: {infant: "婴幼儿", adult_test: "成人测试", legacy_unknown: "历史模式未知"},
  activityLabels: {},
  postureLabels: {},
  expressionLabels: {},
  activityColors: {},
  showToast: message => { throw new Error(message); }
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('async function loadTimeline('),
  source.indexOf('function renderObservations(')), context);
const obsStart = source.indexOf('function renderObservations(');
const obsEnd = source.indexOf('\nfunction ', obsStart + 1);
vm.runInContext(source.slice(obsStart, obsEnd), context);

(async () => {
  for (const observations of [
    {items: []},
    {items: null},
    null,
    {},
    {items: [null]},
  ]) {
    context.api = async url => observations;
    await context.loadTimeline();
    assert.equal(nodes.get('#observationCount').textContent, '0 条');
  }

  context.api = async url => ({
    items: [{
      ts: 1,
      duration_seconds: 60,
      sleep_state: 'sleeping',
      baby_visible: true,
      posture: 'supine',
      observation_mode: 'infant',
      observed_actions: []
    }]
  });
  await context.loadTimeline();
  assert.equal(nodes.get('#observationCount').textContent, '1 条');

  const requests = [];
  context.state.observationMode = 'adult_test';
  context.api = async url => {
    requests.push(url);
    return {items: []};
  };
  await context.loadTimeline();
  assert.equal(requests.length, 1);
  assert.ok(requests[0].includes('mode=adult_test'));

  context.renderObservations([{
    ts: 1,
    sleep_state: 'awake',
    baby_visible: true,
    posture: 'supine',
    observation_mode: 'adult_test',
    observed_actions: []
  }]);
  const adultCard = nodes.get('#observationList').children[0];
  assert.match(adultCard.children[1].textContent, /成人测试/);

  context.state.status = {person_test_mode: false};
  context.renderObservations([{
    ts: 1,
    sleep_state: 'awake',
    baby_visible: true,
    posture: 'supine',
    observation_mode: 'adult_test',
    observed_actions: []
  }]);
  assert.match(nodes.get('#observationList').children[0].children[1].textContent, /成人测试/);
  console.log('Record page empty and populated responses passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
