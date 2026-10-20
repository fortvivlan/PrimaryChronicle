const test = require('node:test');
const assert = require('node:assert/strict');
const C = require('../review/review-core.js');
const record = {id:'pvl--1',source_id:'pvl',source_path:'original.htm',text_sha256:'source-hash',text:'лѣто'};
const bundle = {package_id:'package-one',records:[record]};
const reviewed = () => ({...C.initial(record),reviewer:'Эксперт',bodyConfirmed:true,encodingConfirmed:true,status:'reviewed'});

test('mixed and PUA text cannot be approved without explicit checks', () => {
  assert.match(C.canReview(record,C.initial(record)),/имя/);
  assert.equal(C.canReview(record,reviewed()),'');
  assert.match(C.canReview(record,{...reviewed(),bodyConfirmed:false}),/основной/);
  assert.match(C.canReview(record,{...reviewed(),text:'\ue051'}),/PUA/);
  assert.match(C.canReview(record,{...reviewed(),text:'𠀀\u{F0001}'}),/PUA/);
  assert.match(C.canReview(record,{...reviewed(),text:' '}),/Пустой/);
});

test('backup round trip preserves provenance, edits and excluded pages', () => {
  const edit={...reviewed(),text:'исправленное лѣто',note:'сомнение'};
  const exported=C.exportData(bundle,{[record.id]:edit});
  const imported=C.validateImport(JSON.parse(JSON.stringify(exported)),bundle);
  assert.deepEqual({...imported[record.id]},edit);
  assert.equal(C.sameEdit(edit, imported[record.id]),true);
  assert.equal(C.sameEdit({...edit,note:'другое'}, imported[record.id]),false);
  assert.equal(record.text,'лѣто');
});

test('mismatched packages, altered originals and malformed entries are rejected atomically', () => {
  const good=C.exportData(bundle,{[record.id]:reviewed()});
  assert.throws(()=>C.validateImport({...good,package_id:'different'},bundle));
  assert.throws(()=>C.validateImport({...good,edits:{[record.id]:{...reviewed(),sourceHash:'bad'}}},bundle));
  assert.throws(()=>C.validateImport({...good,edits:{unknown:reviewed()}},bundle));
  assert.throws(()=>C.validateImport({...good,edits:{[record.id]:{...reviewed(),status:'anything'}}},bundle));
  assert.throws(()=>C.validateImport({...good,edits:{[record.id]:{...reviewed(),text:12}}},bundle));
});

test('reviewed-only export excludes drafts and retains historical letter distinctions', () => {
  assert.deepEqual(C.reviewedRecords(bundle,{[record.id]:C.initial(record)}),[]);
  const data=C.reviewedRecords(bundle,{[record.id]:{...reviewed(),text:'ѣ\r\nѫ і\u00a0ѡ'}});
  assert.equal(data[0].text,'ѣ\nѫ і ѡ');
  assert.equal(data[0].original_text_sha256,'source-hash');
  assert.equal(data[0].semantic_annotation,'not_performed');
});
