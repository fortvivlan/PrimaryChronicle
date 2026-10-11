const { test } = require('node:test');
const assert = require('node:assert/strict');
const { records } = require('../docs/assets/records.json');
const { matches } = require('../docs/filter.js');
const defaults = { edition: 'both', semantics: [], subject: '', protograph: null, from: null, to: null, includePreamble: true };
const select = overrides => records.filter(record => matches(record, { ...defaults, ...overrides }));

test('both editions count source examples once; unavailable edition is excluded', () => {
  assert.equal(select({}).length, 179);
  assert.equal(select({ edition: 'ipat' }).length, 179);
  assert.equal(select({ edition: 'lavr' }).length, 177);
  assert.ok(!select({ edition: 'lavr' }).some(r => r.id === 176 || r.id === 178));
});
test('multiple semantics use OR and empty source annotations are selectable', () => {
  assert.equal(select({ semantics: ['EXIST'] }).length, 13);
  assert.equal(select({ semantics: ['EVID', 'EXIST'] }).length, 31);
  assert.equal(select({ semantics: [''] }).length, 120);
  assert.equal(select({ semantics: ['FALS'] }).length, 4);
});
test('inclusive ranges treat undated preamble independently', () => {
  assert.equal(select({ includePreamble: false }).length, 171);
  assert.deepEqual(select({ from: 852, to: 852, includePreamble: false }).map(r => r.id), [9]);
  assert.equal(select({ from: 852, to: 852 }).length, 9);
  assert.equal(select({ from: 1200, includePreamble: false }).length, 0);
});
test('edition-qualified mixed subjects follow the selected edition', () => {
  const record = records.find(r => r.id === 65);
  assert.equal(matches(record, { ...defaults, edition: 'lavr', subject: 'pro' }), true);
  assert.equal(matches(record, { ...defaults, edition: 'lavr', subject: 'NP' }), false);
  assert.equal(matches(record, { ...defaults, edition: 'ipat', subject: 'NP' }), true);
  assert.equal(matches(record, { ...defaults, edition: 'ipat', subject: 'pro' }), false);
  assert.equal(matches(record, { ...defaults, subject: 'NP' }), true);
  assert.equal(matches(records[50], { ...defaults, subject: 'NP' }), false);
});
test('protograph preserves corrected and alternative readings, including blanks', () => {
  assert.equal(select({ protograph: '' }).length, 3);
  assert.equal(select({ protograph: '→ Аорист' }).length, 5);
  assert.ok(select({ protograph: 'Перфект I' }).every(r => r.protographKey === 'Перфект I'));
  assert.equal(select({ protograph: 'Аорист' }).length, 1);
});
test('all filter groups combine using AND', () => {
  const selected = select({ edition: 'ipat', semantics: ['EXIST', 'EVID'], subject: 'NP', protograph: 'Перфект II', from: 1114, to: 1114, includePreamble: false });
  assert.deepEqual(selected.map(r => r.id), [178]);
  assert.equal(select({ edition: 'lavr', semantics: ['EXIST', 'EVID'], subject: 'NP', protograph: 'Перфект II', from: 1114, to: 1114, includePreamble: false }).length, 0);
});
