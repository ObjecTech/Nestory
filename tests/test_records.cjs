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
const context = { state: {status: {}}, document: { createElement: node },
  $: id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
  formatDuration: n => `${n}`, formatTime: n => `${n}`,
  activityLabels: {}, postureLabels: {}, expressionLabels: {}, activityColors: {},
  showToast: message => { throw new Error(message); } };
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function renderTimeline('),
  source.indexOf('async function loadTimeline(')), context);
vm.runInContext(source.slice(source.indexOf('async function loadTimeline('),
  source.indexOf('function renderObservations(')), context);
const obsStart = source.indexOf('function renderObservations(');
const obsEnd = source.indexOf('\nfunction ', obsStart + 1);
vm.runInContext(source.slice(obsStart, obsEnd), context);
(async () => {
  for (const [timeline, observations] of [
    [{intervals: [], totals: {}}, {items: []}],
    [{intervals: null, totals: {activity: null}}, {items: null}],
    [null, null], [{}, {}],
    [{intervals: [null], totals: null}, {items: [null]}],
  ]) {
    context.api = async url => url.startsWith('/api/timeline') ? timeline : observations;
    await context.loadTimeline();
    assert.equal(nodes.get('#sleepTotal').textContent, '0');
    assert.equal(nodes.get('#awakeTotal').textContent, '0');
    assert.equal(nodes.get('#observationCount').textContent, '0 条');
    assert.match(nodes.get('#postureTimeline').innerHTML, /当天还没有记录/);
  }
  context.api = async url => url.startsWith('/api/timeline')
    ? {intervals: [{category: 'activity', value: 'sleeping', start_ts: 1, duration_seconds: 12}],
       totals: {activity: {sleeping: 12, awake: 7}}} : {items: []};
  await context.loadTimeline();
  assert.equal(nodes.get('#sleepTotal').textContent, '12');
  assert.equal(nodes.get('#awakeTotal').textContent, '7');
  assert.equal(nodes.get('#recordCount').textContent, '1 条');
  console.log('Record page empty and populated responses passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
