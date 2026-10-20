/* Local-file application: no server, modules, CDN, analytics, or JSON fetch. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), C = window.ReviewCore, bundle = window.REVIEW_DATA;
  if (!bundle || !C) { $('message').textContent = 'Пакет не загружен. Откройте index.html из полной папки expert_review; рядом нужен data.js.'; return; }
  const key = 'primarychronicle-review-v1:' + bundle.package_id;
  let edits = Object.create(null), filtered = [], active = null, selectedLines = new Set(), imageObjectURL = null;
  let storageOK = true, dirtySinceExport = false, lastReviewer = '';
  const flagNames = {
    private_use_encoding: 'частные коды Unicode: нужна конверсия',
    legacy_glyphs_require_expert_mapping: 'старые шрифтовые коды: проверить соответствия',
    superscript_position_in_evidence_html_only: 'положение надстрочных букв сохранено в HTML справа; восстановить в экспертной версии',
    latin_characters_check_against_source: 'проверить латинские символы',
    greek_characters_check_against_source: 'проверить греческие символы',
    recognition_requires_proofreading: 'невычитанное OCR/HTR',
    base_text_variants_and_boundaries_require_review: 'проверить основной текст, разночтения и границы',
    partial_segmentation: 'распознана только часть строк',
    automatic_left_column_and_footer_detection_requires_review: 'славянская колонка выделена автоматически — сверить',
    column_detection_failed: 'колонка не выделена — выбрать строки вручную'
  };
  const statusNames = {draft: 'Черновик', reviewed: 'Вычитано', excluded: 'Исключено'};
  function message(text, error=false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; }
  try {
    const saved = localStorage.getItem(key);
    if (saved) edits = C.validateImport(JSON.parse(saved), bundle);
  } catch (e) { storageOK = false; message('Браузерная копия недоступна или повреждена. Сохраняйте правки в файл. ' + e.message, true); }
  function persist() {
    try {
      localStorage.setItem(key, JSON.stringify(C.exportData(bundle, edits)));
      storageOK = true; $('save-status').textContent = 'Сохранено в браузере';
    } catch (_) {
      storageOK = false; $('save-status').textContent = 'Нужен экспорт в файл';
      message('Браузер не сохранил правки (ограничение хранилища или приватный режим). Они пока в памяти: нажмите «Сохранить правки в файл».', true);
    }
  }
  function state(record) { return edits[record.id] || {...C.initial(record), reviewer: lastReviewer}; }
  function capture(invalidate=false) {
    if (!active) return;
    const previous = state(active);
    const edit = {sourceHash: active.text_sha256, text: $('editor').value, note: $('note').value,
      status: $('review-status').value, reviewer: $('reviewer').value,
      bodyConfirmed: $('body-confirmed').checked, encodingConfirmed: $('encoding-confirmed').checked,
      updatedAt: new Date().toISOString()};
    if (invalidate && previous.status === 'reviewed') {
      edit.status = 'draft'; $('review-status').value = 'draft';
      message('Текст или подтверждение изменены. Состояние возвращено в «Черновик»; после проверки снова выберите «Вычитано».');
    }
    if (edit.status === 'reviewed') {
      const error = C.canReview(active, edit);
      if (error) { edit.status = 'draft'; $('review-status').value = 'draft'; message(error, true); }
    }
    edits[active.id] = edit; lastReviewer = edit.reviewer;
    dirtySinceExport = true; persist(); updateCount();
    const button = Array.from($('records').children).find(n => n.dataset.id === active.id);
    if (button) button.querySelector('small').textContent = statusNames[edit.status];
  }
  function updateCount() {
    const counts = {reviewed: 0, excluded: 0};
    for (const e of Object.values(edits)) if (e.status in counts) counts[e.status]++;
    $('count').textContent = `${filtered.length} материалов · вычитано ${counts.reviewed} / ${bundle.records.length} · исключено ${counts.excluded}`;
  }
  function filter() {
    const source = $('source').value, q = $('query').value.toLocaleLowerCase('ru'), status = $('status-filter').value;
    filtered = bundle.records.filter(r => (source === 'all' || r.source_id === source) &&
      (!q || (r.text + ' ' + (edits[r.id]?.text || '') + ' ' + r.label).toLocaleLowerCase('ru').includes(q)) &&
      (status === 'all' || status === 'encoding' && r.flags.includes('private_use_encoding') ||
       status === 'mixed' && r.segmentation === 'mixed_page_requires_selection' || state(r).status === status));
    $('records').replaceChildren();
    for (const r of filtered) {
      const button = document.createElement('button'); button.className = 'record'; button.dataset.id = r.id;
      const label = document.createElement('span'); label.textContent = r.label;
      const small = document.createElement('small'); small.textContent = statusNames[state(r).status];
      button.append(label, small); button.onclick = () => show(r); $('records').append(button);
    }
    updateCount();
    show(filtered.includes(active) ? active : filtered[0] || null);
  }
  function overlay() {
    $('overlay').replaceChildren(); $('overlay').hidden = !$('boxes').checked;
    if (!active?.image_size) return;
    $('overlay').setAttribute('viewBox', `0 0 ${active.image_size.join(' ')}`);
    for (const i of selectedLines) {
      const [x1,y1,x2,y2] = active.lines[i].box;
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      for (const [k,v] of Object.entries({x:x1,y:y1,width:x2-x1,height:y2-y1})) rect.setAttribute(k,v);
      $('overlay').append(rect);
    }
  }
  function show(record) {
    active = record;
    for (const el of $('records').children) el.classList.toggle('active', el.dataset.id === record?.id);
    for (const id of ['editor','note','reviewer','review-status','body-confirmed','encoding-confirmed','apply-lines']) $(id).disabled = !record;
    const index = filtered.indexOf(record);
    $('prev').disabled = index <= 0; $('next').disabled = index < 0 || index >= filtered.length-1;
    if (imageObjectURL) { URL.revokeObjectURL(imageObjectURL); imageObjectURL = null; }
    $('scan').removeAttribute('src'); $('image-error').hidden = true;
    if (!record) {
      $('title').textContent = 'Материалов по этому фильтру нет';
      for (const id of ['edition','source-notes','record-label','position','flags','provenance','raw-text','image-info']) $(id).textContent = '';
      $('actions').replaceChildren(); $('html-evidence').replaceChildren(); $('alternatives').replaceChildren(); $('lines').replaceChildren();
      $('image-frame').hidden = true; $('image-controls').hidden = true; $('line-details').hidden = true; $('html-details').hidden = true;
      $('editor').value = ''; $('note').value = ''; $('source-link').removeAttribute('href'); return;
    }
    const source = bundle.sources.find(s => s.id === record.source_id), edit = state(record);
    $('title').textContent = source.title; $('edition').textContent = source.edition;
    $('source-notes').textContent = source.notes;
    $('actions').replaceChildren(); for (const action of source.actions) { const li=document.createElement('li');li.textContent=action;$('actions').append(li); }
    $('record-label').textContent = record.label; $('position').textContent = `${index+1} / ${filtered.length}`;
    $('flags').textContent = record.flags.map(f => flagNames[f] || f).join(' · ') || 'Автоматически извлечённый текст: требуется сверка.';
    $('editor').value = edit.text; $('note').value = edit.note; $('reviewer').value = edit.reviewer;
    $('editor').className = record.legacy_font === 'menaion' ? 'menaion' : '';
    $('review-status').value = edit.status; $('body-confirmed').checked = edit.bodyConfirmed;
    $('encoding-confirmed').checked = edit.encodingConfirmed; $('save-status').textContent = edits[record.id] ? 'Есть правки' : 'Исходный черновик';
    $('source-link').href = record.source_url || '#';
    $('source-link').hidden = !record.source_url;
    $('raw-text').textContent = record.source_text;
    $('raw-text').className = record.legacy_font === 'menaion' ? 'menaion' : '';
    $('html-evidence').innerHTML = record.evidence_html || ''; // Only allowlisted markup produced by the builder.
    $('html-evidence').className = record.legacy_font || '';
    $('html-details').hidden = !record.evidence_html;
    $('provenance').textContent = JSON.stringify({id: record.id, source: record.source_path, source_sha256: record.source_sha256,
      recognition: record.recognition_path, fingerprint: record.run_fingerprint, physical_page: record.page,
      selector: record.selector, normalization: record.normalization_changes, locators: record.locators,
      source_text_sha256: record.text_sha256, segmentation: record.segmentation, statistics: record.statistics}, null, 2);
    const img = record.local_image || record.remote_image;
    $('image-frame').hidden = !img; $('image-controls').hidden = !img; $('boxes').checked = true;
    $('image-info').textContent = record.kind === 'recognition' ?
      `Номер ${record.page} — физическая страница файла или позиция манифеста, не печатная страница и не лист рукописи. ` +
      (record.local_image ? 'Локальное уменьшенное превью. ' : 'Скан загружается из Интернета. ') +
      'Подсветка обозначает строки, а не оценку правильности.' : 'Справа — фрагмент электронной публикации; печатное издание проверяйте по ссылке.';
    if (img) $('scan').src = img;
    selectedLines = new Set();
    const chosen = new Set((record.selected_boxes || []).map(b => b.join(',')));
    $('lines').replaceChildren();
    for (const [i,line] of (record.lines || []).entries()) {
      const label=document.createElement('label'), box=document.createElement('input'), span=document.createElement('span');
      box.type='checkbox'; box.checked=chosen.has(line.box.join(',')); if(box.checked) selectedLines.add(i);
      box.onchange=()=>{if(box.checked)selectedLines.add(i);else selectedLines.delete(i);overlay();};
      span.textContent=line.text; label.append(box,span); $('lines').append(label);
    }
    $('line-details').hidden = !record.lines?.length; overlay();
    $('alternatives').replaceChildren();
    for (const alt of record.alternatives || []) {
      const d=document.createElement('details'), s=document.createElement('summary'), p=document.createElement('pre');
      s.textContent=alt.label;p.textContent=alt.text;d.append(s,p);$('alternatives').append(d);
    }
  }
  function download(name, text, type) {
    const url=URL.createObjectURL(new Blob([text],{type})), a=document.createElement('a');a.href=url;a.download=name;
    document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('source').append(new Option('Все памятники','all'));
  for (const s of bundle.sources) $('source').append(new Option(s.title,s.id));
  for (const id of ['source','status-filter']) $(id).onchange=filter;
  let searchTimer; $('query').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(filter,180);};
  $('prev').onclick=()=>show(filtered[filtered.indexOf(active)-1]); $('next').onclick=()=>show(filtered[filtered.indexOf(active)+1]);
  for (const id of ['editor','note','reviewer']) $(id).oninput=()=>capture(id==='editor'||id==='reviewer');
  for (const id of ['body-confirmed','encoding-confirmed']) $(id).onchange=()=>capture(true);
  $('review-status').onchange=()=>capture();
  for (const symbol of ['ѣ','Ѣ','ѧ','ѫ','ꙗ','ѥ','ѡ','ѿ','і','ѳ','ѵ','ⷣ','ⷭ','҃','҇','[?]']) {
    const b=document.createElement('button');b.textContent=symbol;b.title=`Вставить ${symbol}`;
    b.onclick=()=>{if(!active)return;const t=$('editor');t.setRangeText(symbol,t.selectionStart,t.selectionEnd,'end');t.focus();capture(true);};$('symbols').append(b);
  }
  $('boxes').onchange=overlay;
  $('apply-lines').onclick=()=>{
    $('editor').value=Array.from(selectedLines).sort((a,b)=>a-b).map(i=>active.lines[i].text).join('\n');
    $('body-confirmed').checked=false;$('encoding-confirmed').checked=false;capture(true);
    message('Выбранные строки перенесены. Проверьте порядок, полноту, буквы и границы; предыдущая версия доступна в сохранённом файле правок.');
  };
  $('scan').onerror=()=>{ $('image-error').hidden=false; };
  $('scan').onload=()=>{ $('image-error').hidden=true; };
  $('local-image').onchange=event=>{
    const file=event.target.files[0];if(!file)return;
    if(imageObjectURL)URL.revokeObjectURL(imageObjectURL);imageObjectURL=URL.createObjectURL(file);$('scan').src=imageObjectURL;
    // Arbitrary local files may have different crops; boxes would be misleading.
    $('boxes').checked=false;overlay();event.target.value='';
  };
  $('export').onclick=()=>{
    try { download('primarychronicle-corrections.json',JSON.stringify(C.exportData(bundle,edits),null,2),'application/json;charset=utf-8');
      dirtySinceExport=false;message('Файл правок передан браузеру для сохранения. Храните его отдельно и передайте вместе с результатами.');
    } catch(e) {message(e.message,true);}
  };
  $('reviewed-export').onclick=()=>{
    const records=C.reviewedRecords(bundle,edits);
    if(!records.length){message('Пока нет вычитанных текстов с подтверждёнными границами и кодировкой.',true);return;}
    download('primarychronicle-reviewed-texts.json',JSON.stringify({schema_version:1,package_id:bundle.package_id,records},null,2),'application/json;charset=utf-8');
    message(`Выгружено вычитанных материалов: ${records.length}. Это транскрипции; семантическая разметка ещё не выполнена.`);
  };
  $('import').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    try {
      const incoming=C.validateImport(JSON.parse(await file.text()),bundle);
      // Preserve current edits; conflicting versions must be resolved by the reviewer.
      const conflicts=Object.keys(incoming).filter(id=>edits[id]&&!C.sameEdit(edits[id],incoming[id]));
      if(conflicts.length) throw Error(`Есть конфликтующие правки (${conflicts.length}). Текущие данные сохранены. Откройте пакет в отдельном браузере для сравнения версий.`);
      Object.assign(edits,incoming);dirtySinceExport=true;persist();filter();message(`Открыт файл: ${Object.keys(incoming).length} материалов.`);
    } catch(e){message('Импорт не выполнен: '+e.message,true);} finally{event.target.value='';}
  };
  window.addEventListener('beforeunload',event=>{if(dirtySinceExport){event.preventDefault();event.returnValue='';}});
  filter();
  if(storageOK)message('Работает локально, без Python. Правки хранятся в этом браузере; регулярно сохраняйте их в файл. Исходные автоматические тексты не изменяются.');
})();
