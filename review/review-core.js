(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ReviewCore = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const statuses = ['draft', 'reviewed', 'excluded'];
  const hasPUA = text => /[\uE000-\uF8FF\u{F0000}-\u{FFFFD}\u{100000}-\u{10FFFD}]/u.test(text);
  function normalize(text) { return text.replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ').normalize('NFC'); }
  function canReview(record, edit) {
    if (!edit.reviewer.trim()) return 'Укажите имя проверяющего.';
    if (!edit.bodyConfirmed) return 'Подтвердите, что оставлен только основной древнерусский текст.';
    if (!edit.text.trim()) return 'Пустой текст нельзя отметить как вычитанный. Используйте «Исключено».';
    if (hasPUA(edit.text)) return 'Остались частные коды Unicode (PUA). Сначала замените их проверенными символами.';
    if (!edit.encodingConfirmed) return 'Подтвердите проверку букв, титл и надстрочных знаков.';
    return '';
  }
  function initial(record) {
    return {sourceHash: record.text_sha256, text: record.text, note: '', status: 'draft',
      reviewer: '', bodyConfirmed: false, encodingConfirmed: false, updatedAt: null};
  }
  function validateImport(data, bundle) {
    if (!data || data.schema_version !== 1 || data.package_id !== bundle.package_id ||
        !data.edits || typeof data.edits !== 'object' || Array.isArray(data.edits)) {
      throw Error('Файл относится к другому пакету или имеет неподдерживаемый формат.');
    }
    const records = new Map(bundle.records.map(r => [r.id, r]));
    const result = Object.create(null);
    for (const [id, edit] of Object.entries(data.edits)) {
      const record = records.get(id);
      if (!record || !edit || edit.sourceHash !== record.text_sha256 ||
          typeof edit.text !== 'string' || typeof edit.note !== 'string' ||
          typeof edit.reviewer !== 'string' || !statuses.includes(edit.status) ||
          typeof edit.bodyConfirmed !== 'boolean' || typeof edit.encodingConfirmed !== 'boolean' ||
          !(edit.updatedAt === null || typeof edit.updatedAt === 'string')) {
        throw Error('Неверные данные или несовпадающий исходный текст: ' + id);
      }
      if (edit.status === 'reviewed' && canReview(record, edit)) throw Error('Неполная проверка: ' + id);
      result[id] = {sourceHash: edit.sourceHash, text: edit.text, note: edit.note, reviewer: edit.reviewer,
        status: edit.status, bodyConfirmed: edit.bodyConfirmed, encodingConfirmed: edit.encodingConfirmed,
        updatedAt: edit.updatedAt};
    }
    return result;
  }
  function exportData(bundle, edits) {
    const checked = validateImport({schema_version: 1, package_id: bundle.package_id, edits}, bundle);
    return {schema_version: 1, package_id: bundle.package_id, exported_at: new Date().toISOString(),
      normalization: 'UTF-8 / NFC; expert spelling preserved; original automatic text unchanged', edits: checked};
  }
  function sameEdit(a, b) {
    return ['sourceHash','text','note','reviewer','status','bodyConfirmed','encodingConfirmed','updatedAt']
      .every(key => a[key] === b[key]);
  }
  function reviewedRecords(bundle, edits) {
    return bundle.records.filter(r => edits[r.id]?.status === 'reviewed' && !canReview(r, edits[r.id]))
      .map(r => ({id: r.id, source_id: r.source_id, source_path: r.source_path, page: r.page ?? null,
        original_text_sha256: r.text_sha256, run_fingerprint: r.run_fingerprint ?? null,
        method: 'expert_proofread_transcription', reviewer: edits[r.id].reviewer,
        text: normalize(edits[r.id].text), note: edits[r.id].note,
        // Proofreading is not the semantic annotation used by the linguistic database.
        semantic_annotation: 'not_performed', reviewed_at: edits[r.id].updatedAt}));
  }
  return {hasPUA, normalize, initial, canReview, validateImport, exportData, reviewedRecords, sameEdit};
});
