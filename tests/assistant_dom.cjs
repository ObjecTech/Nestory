/** Minimal DOM for controller unit/HTTP tests; browser acceptance is separate. */
// Minimal mock DOM for Node.js test environment
function createMockElement(tagName = 'div') {
  const listeners = {};
  const classes = new Set();
  const dataset = {};
  const children = [];

  const elem = {
    tagName: tagName.toUpperCase(),
    textContent: '',
    value: '',
    hidden: false,
    disabled: false,
    dataset,
    style: {},
    children,
    classList: {
      add: (...cls) => cls.forEach(c => classes.add(c)),
      remove: (...cls) => cls.forEach(c => classes.delete(c)),
      toggle: (c, force) => {
        if (force === undefined) {
          classes.has(c) ? classes.delete(c) : classes.add(c);
        } else if (force) {
          classes.add(c);
        } else {
          classes.delete(c);
        }
      },
      contains: c => classes.has(c),
    },
    get className() {
      return [...classes].join(' ');
    },
    set className(val) {
      classes.clear();
      if (val) val.split(/\s+/).filter(Boolean).forEach(c => classes.add(c));
    },
    get innerHTML() {
      return elem._innerHTML || '';
    },
    set innerHTML(html) {
      elem._innerHTML = html;
      elem.children.length = 0;
    },
    appendChild(child) {
      children.push(child);
      child.parentNode = elem;
      return child;
    },
    removeChild(child) {
      const idx = children.indexOf(child);
      if (idx !== -1) children.splice(idx, 1);
      return child;
    },
    addEventListener(evt, fn) {
      if (!listeners[evt]) listeners[evt] = [];
      listeners[evt].push(fn);
    },
    removeEventListener(evt, fn) {
      if (listeners[evt]) {
        listeners[evt] = listeners[evt].filter(f => f !== fn);
      }
    },
    dispatchEvent(evt) {
      const fns = listeners[evt.type] || [];
      fns.forEach(fn => fn(evt));
    },
    click() {
      if (elem.onclick) elem.onclick({ stopPropagation: () => {}, currentTarget: elem, target: elem });
      elem.dispatchEvent({ type: 'click', stopPropagation: () => {}, currentTarget: elem, target: elem });
    },
    focus() {},
    setAttribute(name, value) { this[name] = String(value); },
    querySelector(sel) {
      for (const child of children) {
        if (matches(child, sel)) return child;
        const sub = child.querySelector?.(sel);
        if (sub) return sub;
      }
      return null;
    },
    querySelectorAll(sel) {
      const res = [];
      for (const child of children) {
        if (matches(child, sel)) res.push(child);
        if (child.querySelectorAll) res.push(...child.querySelectorAll(sel));
      }
      return res;
    },
  };

  function matches(el, sel) {
    if (!el) return false;
    if (sel.startsWith('#') && el.id === sel.slice(1)) return true;
    if (sel.startsWith('.') && el.classList?.contains(sel.slice(1))) return true;
    if (sel.startsWith('[') && sel.endsWith(']')) {
      const parts = sel.slice(1, -1).split('=');
      const attr = parts[0];
      const val = parts[1] ? parts[1].replace(/['"]/g, '') : null;
      if (attr.startsWith('data-')) {
        const key = attr.slice(5);
        return val !== null ? el.dataset[key] === val : el.dataset[key] !== undefined;
      }
    }
    return el.tagName === sel.toUpperCase();
  }

  return elem;
}

// Global elements registry
const domRegistry = new Map();
function getOrCreateElem(id, tag = 'div') {
  if (!domRegistry.has(id)) {
    const el = createMockElement(tag);
    el.id = id;
    domRegistry.set(id, el);
  }
  return domRegistry.get(id);
}

// Setup full browser environment
const localStorageData = {};
global.localStorage = {
  getItem: key => localStorageData[key] || null,
  setItem: (key, val) => { localStorageData[key] = String(val); },
  removeItem: key => { delete localStorageData[key]; },
  clear: () => { Object.keys(localStorageData).forEach(k => delete localStorageData[k]); },
};

global.document = {
  readyState: 'complete',
  visibilityState: 'visible',
  cookie: 'bmon_csrf=test-token-12345',
  createElement: tag => createMockElement(tag),
  getElementById: id => getOrCreateElem(id),
  querySelector: sel => {
    if (sel.startsWith('#')) return getOrCreateElem(sel.slice(1));
    for (const el of domRegistry.values()) {
      if (el.id && sel.includes(el.id)) return el;
      const found = el.querySelector(sel);
      if (found) return found;
    }
    return null;
  },
  querySelectorAll: sel => {
    const res = [];
    for (const el of domRegistry.values()) {
      res.push(...el.querySelectorAll(sel));
    }
    return res;
  },
  addEventListener: () => {},
};

global.window = {
  location: { origin: 'http://localhost:8000', assign: () => {} },
  addEventListener: () => {},
  document: global.document,
  localStorage: global.localStorage,
};

// Initialize DOM registry with Assistant template IDs
[
  'assistantPage', 'assistantHeader', 'assistantDrawerBtn', 'assistantConvCountBadge',
  'assistantConvTitle', 'assistantRenameConvBtn', 'assistantScopePill', 'assistantCareEventsBtn',
  'assistantDeleteConvBtn', 'assistantNewConvBtn', 'assistantDemoBanner', 'assistantRealBanner',
  'assistantMockBanner', 'assistantMessagesArea', 'assistantInput', 'assistantSendBtn',
  'assistantBusyIndicator', 'assistantDrawerBackdrop', 'assistantDrawer', 'assistantDrawerClose',
  'assistantDrawerNewBtn', 'assistantConvList', 'assistantDeleteModal', 'assistantDeleteCancelBtn',
  'assistantDeleteConfirmBtn', 'assistantRenameModal', 'assistantRenameInput',
  'assistantRenameCancelBtn', 'assistantRenameSaveBtn', 'assistantCareEventsModal',
  'careEventsModalClose', 'careEventsModalBackdrop', 'careEventFromDate', 'careEventToDate',
  'openAddCareEventBtn', 'careEventsList', 'editCareEventModal', 'editCareEventType',
  'editCareEventOccurred', 'editCareEventEnd', 'editCareEventAmount', 'editCareEventDiaper',
  'editCareEventNotes', 'cancelCareEventBtn', 'saveCareEventBtn', 'toast'
].forEach(id => getOrCreateElem(id));


module.exports = {getOrCreateElem, domRegistry};
