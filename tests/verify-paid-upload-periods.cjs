'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', 'az-baze-auth', 'templates', 'uploads.html'), 'utf8');
const formMarkup = template.match(/<form\b[^>]*\bdata-az-paid-upload-form\b[^>]*>([\s\S]*?)<\/form>/)?.[1];
assert.ok(formMarkup, 'The paid upload form must exist.');
const scripts = [...template.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
  .map(match => match[1])
  .filter(script => script.includes('data-az-paid-upload-form') && script.includes('reconcileOverlay'));
assert.equal(scripts.length, 1, 'Execute the actual upload dialog script, without copying its implementation.');

function element(id = '') {
  const classes = new Set(id === 'reconcileOverlay' ? ['hidden'] : []);
  const listeners = new Map();
  let html = '';
  const result = {
    id, disabled: false, value: '', dataset: {}, textContent: '', providerSelects: [], providerReads: 0,
    open: false, showModal() { this.open = true; }, close() { this.open = false; }, focus() {},
    classList: {
      add: (...names) => names.forEach(name => classes.add(name)),
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle(name, force) {
        const enabled = force === undefined ? !classes.has(name) : !!force;
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
    },
    addEventListener(name, listener) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(listener);
    },
    async dispatch(name) {
      assert.equal(this.disabled, false, `${id} must be available for ${name}.`);
      let prevented = false;
      const event = { target: this, preventDefault() { prevented = true; } };
      await Promise.all((listeners.get(name) || []).map(listener => listener(event)));
      // submit's real listener starts preview() without returning its promise.
      // One event-loop turn drains the resolved fetch/json promise chains.
      await new Promise(resolve => setImmediate(resolve));
      return { prevented };
    },
    querySelectorAll(selector) {
      if (selector === 'input[type="file"]') return (this.inputs || []).filter(input => input.type === 'file').map(input => ({files: [input.value]}));
      assert.equal(selector, '[data-provider]');
      this.providerReads += 1;
      return this.providerSelects;
    },
  };
  Object.defineProperty(result, 'innerHTML', {
    get: () => html,
    set(value) {
      html = String(value);
      if (id === 'reconcileProviders') {
        result.providerSelects = [...html.matchAll(/<select\b[^>]*\bdata-provider="([^"]*)"/g)]
          .map(match => ({ value: '', dataset: { provider: match[1] } }));
      }
    },
  });
  return result;
}

function harness(previewPayload, commitPayload = { status: 'imported', message: 'Сохранено.' }) {
  const file = Object.freeze({ name: 'selected-period.xlsx', type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
  const form = element('paidForm');
  form.inputs = [...formMarkup.matchAll(/<input\b([^>]*)>/g)].map(match => {
    const attrs = match[1];
    const name = attrs.match(/\bname="([^"]+)"/)?.[1];
    const type = attrs.match(/\btype="([^"]+)"/)?.[1] || 'text';
    return { name, type, value: type === 'file' ? file : '', disabled: false };
  }).filter(input => input.name);
  for (const [name, value] of Object.entries({ csrf: 'form-csrf' })) {
    const input = form.inputs.find(item => item.name === name);
    assert.ok(input, `${name} must be an actual form input.`);
    input.value = value;
  }
  const ids = Object.fromEntries([
    'reconcileOverlay', 'reconcileTitle', 'reconcileMessage', 'reconcileStats', 'reconcileProviders',
    'reconcileKeep', 'reconcileNew', 'reconcileContinue', 'reconcileCancel', 'paidClientResult',
    'uploadResultDialog', 'uploadResultTitle', 'uploadResultMessage', 'uploadResultFiles', 'uploadResultPeriod', 'uploadResultOk',
  ].map(id => [id, element(id)]));
  const calls = [];
  const timers = [];
  const navigation = {reloads: 0};
  class FormData {
    constructor(source) {
      this.data = new Map((source?.inputs || []).filter(input => !input.disabled)
        .map(input => [input.name, input.value]));
    }
    set(key, value) { this.data.set(key, typeof value === 'object' && value !== null ? value : String(value)); }
    get(key) { return this.data.has(key) ? this.data.get(key) : null; }
    has(key) { return this.data.has(key); }
  }
  const context = vm.createContext({
    document: {
      querySelector: selector => selector === '[data-az-paid-upload-form]' ? form : null,
      getElementById: id => ids[id] || null,
      querySelectorAll: () => [],
    },
    FormData,
    fetch: async (url, options) => {
      calls.push({ url, options, body: options.body });
      assert.ok(calls.length <= 2, 'Unexpected additional request.');
      return { ok: true, json: async () => calls.length === 1 ? previewPayload : commitPayload };
    },
    setTimeout: callback => { timers.push(callback); return timers.length; },
    location: { reload() { navigation.reloads += 1; }, replace() {} },
  });
  vm.runInContext(scripts[0], context, { filename: 'uploads.html:reconcile', timeout: 1000 });
  return { form, ids, calls, file, timers, navigation };
}

function preview(overrides = {}) {
  return {
    status: 'preview', period: { from: '2026-01-01', to: '2026-06-30' },
    revision: 'new', overlap_revision: 'o'.repeat(64),
    counts: { new: 1, identical: 0, conflict: 0, overlap: 0 },
    overlaps: [], requires_choice: false, requires_review: false,
    requires_provider_mapping: false, unknown_providers: [], can_replace: true,
    summary: { paid: 260, dent: 260, structure: 0, lab: 0, opening: 100, closing: 40 },
    ...overrides,
  };
}

function verifyTransmission(test, decision, revision, overlapRevision = 'o'.repeat(64)) {
  assert.equal(test.calls.length, 2, 'One preview and one commit are expected.');
  assert.equal(test.calls[0].url, '/api/uploads/paid-services/preview');
  assert.equal(test.calls[1].url, '/api/uploads/paid-services/commit');
  for (const call of test.calls) {
    assert.equal(call.options.method, 'POST');
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.body.get('paid_services'), test.file, 'The selected file must survive preview → commit.');
    assert.equal(call.body.has('period_start'), false, 'The form must not ask for a period already present in the file.');
    assert.equal(call.body.has('period_end'), false, 'The form must not ask for a period already present in the file.');
    assert.equal(call.body.get('csrf'), 'form-csrf');
  }
  assert.equal(test.calls[1].body.get('decision'), decision);
  assert.equal(test.calls[1].body.get('expected_revision'), revision);
  assert.equal(test.calls[1].body.get('expected_overlap_revision'), overlapRevision);
}

async function autoCommit() {
  const test = harness(preview());
  assert.equal((await test.form.dispatch('submit')).prevented, true);
  verifyTransmission(test, 'append', 'new');
  assert.equal(test.ids.paidClientResult.textContent, 'Сохранено.');
  assert.equal(test.timers.length, 0, 'No automatic reload before acknowledgement.');
  assert.equal(test.ids.uploadResultDialog.open, true);
  assert.equal(test.ids.uploadResultTitle.textContent, 'Данные загружены');
  assert.equal(test.ids.uploadResultFiles.textContent, 'selected-period.xlsx');
  assert.equal(test.navigation.reloads, 0);
  await test.ids.uploadResultOk.dispatch('click');
  assert.equal(test.ids.uploadResultDialog.open, false);
  assert.equal(test.navigation.reloads, 1);
}

async function confirmedReplacement() {
  const revision = 'a'.repeat(64), overlapRevision = 'x'.repeat(64);
  const test = harness(preview({
    revision, overlap_revision: overlapRevision, requires_choice: true, requires_review: true,
    changes_detected: true, counts: { conflict: 1, overlap: 0 },
    overlaps: [{from:'2026-01-01',to:'2026-06-30',relation:'exact',same_payload:false,source_filename:'old.xlsx'}],
  }));
  await test.form.dispatch('submit');
  assert.equal(test.calls.length, 1, 'A conflict must wait for the selected choice.');
  assert.equal(test.ids.reconcileOverlay.classList.contains('hidden'), false);
  assert.match(test.ids.reconcileMessage.innerHTML, /2026-01-01.*2026-06-30/);
  await test.ids.reconcileNew.dispatch('click');
  verifyTransmission(test, 'use_new', revision, overlapRevision);
  assert.match(test.ids.reconcileMessage.innerHTML, /данные внутри периода изменились/);
}

async function keepOldWithoutClassificationPermission() {
  const revision = 'b'.repeat(64);
  const test = harness(preview({
    revision, requires_choice: true, requires_provider_mapping: true,
    unknown_providers: ['Unknown U.'], can_replace: false,
  }), { status: 'duplicate', message: 'Сохранены старые данные.' });
  await test.form.dispatch('submit');
  assert.equal(test.ids.reconcileNew.disabled, true);
  assert.equal(test.ids.reconcileKeep.classList.contains('hidden'), false);
  assert.equal(test.ids.reconcileKeep.disabled, false, 'Keeping the old report must not require replacement rights.');
  assert.equal(test.ids.reconcileProviders.providerSelects[0].value, '');
  await test.ids.reconcileKeep.dispatch('click');
  verifyTransmission(test, 'keep_old', revision);
  assert.equal(test.ids.reconcileProviders.providerReads, 0, 'Do not request classification for the rejected file.');
  assert.equal(test.calls[1].body.has('provider_decisions'), false);
  assert.equal(test.ids.paidClientResult.textContent, 'Сохранены старые данные.');
  assert.equal(test.ids.uploadResultTitle.textContent, 'Данные не изменены');
  assert.equal(test.timers.length, 0);
}

async function overlapReview() {
  const overlapRevision = 'z'.repeat(64);
  const test = harness(preview({
    overlap_revision: overlapRevision,
    requires_review: true,
    counts: {new:1, identical:0, conflict:0, overlap:1},
    overlaps: [{from:'2026-01-01',to:'2026-03-31',relation:'partial',same_payload:null,source_filename:'q1.xlsx'}],
  }));
  await test.form.dispatch('submit');
  assert.equal(test.calls.length, 1, 'An overlapping period must be shown before any write.');
  assert.equal(test.ids.reconcileOverlay.classList.contains('hidden'), false);
  assert.match(test.ids.reconcileMessage.innerHTML, /Найдены наложения/);
  assert.match(test.ids.reconcileMessage.innerHTML, /частичное пересечение/);
  await test.ids.reconcileContinue.dispatch('click');
  verifyTransmission(test, 'append', 'new', overlapRevision);
}

async function invalidClassificationStaysInDialog() {
  const test = harness(preview({ requires_provider_mapping: true, unknown_providers: ['Unknown U.'] }));
  await test.form.dispatch('submit');
  await test.ids.reconcileContinue.dispatch('click');
  assert.equal(test.calls.length, 1, 'A missing classification must not issue a commit.');
  assert.equal(test.ids.reconcileOverlay.classList.contains('hidden'), false);
  assert.match(test.ids.reconcileMessage.textContent, /Для каждого нового врача нужно выбрать направление/);
  assert.equal(test.ids.reconcileContinue.disabled, false, 'The error must allow correction.');
  test.ids.reconcileProviders.providerSelects[0].value = 'dent';
  await test.ids.reconcileContinue.dispatch('click');
  verifyTransmission(test, 'append', 'new');
  assert.deepEqual(JSON.parse(test.calls[1].body.get('provider_decisions')), {
    'Unknown U.': { direction: 'dent', display_name: 'Unknown U.' },
  });
}

(async () => {
  for (const test of [autoCommit, confirmedReplacement, keepOldWithoutClassificationPermission, overlapReview, invalidClassificationStaysInDialog]) {
    await test();
    console.log(`${test.name}: PASS`);
  }
  console.log('PAID_UPLOAD_PERIOD_FORM: PASS (5 scenarios, actual template script)');
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
