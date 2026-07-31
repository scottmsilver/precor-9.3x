'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const htmlPath = path.join(__dirname, '..', 'bringup', 'breadboard-wizard.html');
const html = fs.readFileSync(htmlPath, 'utf8');

function extractScript(id) {
  const pattern = new RegExp(`<script[^>]*id=["']${id}["'][^>]*>([\\s\\S]*?)<\\/script>`);
  const match = html.match(pattern);
  assert.ok(match, `missing ${id} script`);
  return match[1];
}

const model = JSON.parse(extractScript('wiring-data'));
const context = { window: {} };
vm.createContext(context);
vm.runInContext(extractScript('wizard-controller'), context);
const createController = context.window.createBreadboardController;

function fakeStorage(seed = {}) {
  const values = new Map(Object.entries(seed));
  const calls = [];
  return {
    calls,
    values,
    getItem(key) {
      calls.push(['getItem', key]);
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      calls.push(['setItem', key]);
      values.set(key, value);
    },
    removeItem(key) {
      calls.push(['removeItem', key]);
      values.delete(key);
    },
  };
}

function newController(options = {}) {
  return createController({
    model,
    storage: options.storage || fakeStorage(),
    confirmReset: options.confirmReset || (() => true),
  });
}

function confirmActiveAndAdvance(controller) {
  for (const id of controller.currentStep().confirmation_ids) {
    controller.setConfirmation(id, true);
  }
  assert.equal(controller.canNext(), true);
  controller.next();
}

test('Next is gated by every exact active-step confirmation and Back retains results', () => {
  const controller = newController();
  assert.equal(controller.index(), 0);
  assert.equal(controller.canNext(), false);
  controller.next();
  assert.equal(controller.index(), 0);

  while (controller.currentStep().id !== 'dpdt_identify') {
    const step = controller.currentStep();
    assert.equal(step.confirmation_ids.length, 1, `${step.id} must have one confirmation`);
    confirmActiveAndAdvance(controller);
  }

  assert.deepEqual(
    Array.from(controller.currentStep().confirmation_ids),
    ['placed', 'both_pole_pairs_meter_identified'],
  );
  controller.setConfirmation('placed', true);
  assert.equal(controller.canNext(), false);
  controller.setConfirmation('both_pole_pairs_meter_identified', true);
  assert.equal(controller.canNext(), true);
  controller.next();
  const retainedIndex = controller.index();
  controller.back();
  assert.equal(controller.currentStep().id, 'dpdt_identify');
  assert.equal(controller.isConfirmed('placed'), true);
  assert.equal(controller.isConfirmed('both_pole_pairs_meter_identified'), true);
  controller.next();
  assert.equal(controller.index(), retainedIndex);

  for (const step of model.steps) {
    if (step.id !== 'dpdt_identify') {
      assert.equal(step.confirmation_ids.length, 1, `${step.id} must have one confirmation`);
    }
  }
});

test('photo remains unreachable when any individual post-check is omitted', () => {
  const checkSteps = model.steps.filter((step) => step.id.startsWith('check_'));
  assert.ok(checkSteps.length > 0);

  for (const omitted of checkSteps) {
    const controller = newController();
    while (controller.currentStep().id !== omitted.id) {
      assert.notEqual(controller.currentStep().id, 'photo');
      confirmActiveAndAdvance(controller);
    }
    controller.next();
    assert.equal(controller.currentStep().id, omitted.id);
    assert.notEqual(controller.currentStep().id, 'photo');
  }
});

test('state persists under only the versioned key and reloads confirmations', () => {
  const storage = fakeStorage({ unrelated: 'keep' });
  const controller = newController({ storage });
  controller.setConfirmation('safety_power_disconnected', true);
  controller.next();
  controller.zoomIn();

  const touchedKeys = new Set(storage.calls.map((call) => call[1]));
  assert.deepEqual([...touchedKeys], ['esp32tap-breadboard-wizard-v1']);
  assert.equal(storage.values.get('unrelated'), 'keep');

  const restored = newController({ storage });
  assert.equal(restored.index(), 1);
  restored.back();
  assert.equal(restored.isConfirmed('safety_power_disconnected'), true);
  assert.equal(restored.zoom(), 1.1);
});

test('reset confirms, removes only the wizard key, and returns to index zero', () => {
  const storage = fakeStorage({ unrelated: 'keep' });
  let confirmations = 0;
  const controller = newController({
    storage,
    confirmReset: () => {
      confirmations += 1;
      return true;
    },
  });
  confirmActiveAndAdvance(controller);
  assert.equal(controller.reset(), true);
  assert.equal(confirmations, 1);
  assert.equal(controller.index(), 0);
  assert.equal(controller.canNext(), false);
  assert.equal(storage.values.get('unrelated'), 'keep');
  assert.equal(storage.values.has(model.storage_key), false);
  assert.deepEqual(storage.calls.filter((call) => call[0] === 'removeItem'), [
    ['removeItem', 'esp32tap-breadboard-wizard-v1'],
  ]);
});

test('cancelled reset preserves progress and zoom clamps to 0.7 through 2.0', () => {
  const controller = newController({ confirmReset: () => false });
  confirmActiveAndAdvance(controller);
  assert.equal(controller.reset(), false);
  assert.equal(controller.index(), 1);

  for (let i = 0; i < 30; i += 1) controller.zoomOut();
  assert.equal(controller.zoom(), 0.7);
  for (let i = 0; i < 30; i += 1) controller.zoomIn();
  assert.equal(controller.zoom(), 2.0);
});
