/* Browser smoke test. Load after app.js in a copy of the generated index.html.
   No test libraries or application-specific browser flags are required. */
window.addEventListener('load', async () => {
  const report = document.createElement('pre'); report.id = 'browser-test-result'; document.body.prepend(report);
  const $ = id => document.getElementById(id);
  const change = (id,value) => { $(id).value=value;$(id).dispatchEvent(new Event('change')); };
  const assert = (condition,message) => {if(!condition)throw Error(message);};
  const tick = () => new Promise(resolve=>setTimeout(resolve,30));
  try {
    assert(location.protocol==='file:', 'Must test direct file opening');
    assert($('editor').value.includes('повѣсти'), 'Initial source text not loaded');
    change('source','daniel');
    assert($('editor').value.startsWith('ЖИТЬЕ И ХОЖЕНЬЕ'), 'BLDR original not selected');
    assert(!$('editor').value.includes('Вот я, недостойный'), 'Modern translation leaked');
    change('review-status','reviewed');
    assert($('review-status').value==='draft','Premature approval accepted');
    $('reviewer').value='Browser QA';$('reviewer').dispatchEvent(new Event('input'));
    for(const id of ['body-confirmed','encoding-confirmed']){$(id).checked=true;$(id).dispatchEvent(new Event('change'));}
    change('review-status','reviewed');
    assert($('review-status').value==='reviewed','Approval with checks failed');
    $('editor').value+='\nПроверка';$('editor').dispatchEvent(new Event('input'));
    assert($('review-status').value==='draft','Editing must invalidate approval');
    change('review-status','reviewed');
    let blob,filename;
    const oldURL=URL.createObjectURL,oldClick=HTMLAnchorElement.prototype.click;
    URL.createObjectURL=b=>{blob=b;return oldURL(b);};HTMLAnchorElement.prototype.click=function(){filename=this.download;};
    $('export').click();
    assert(filename==='primarychronicle-corrections.json','Correction download failed');
    const backup=JSON.parse(await blob.text());
    assert(backup.edits['daniel--bldr4-1'].text.endsWith('Проверка'),'Export lost correction');
    $('reviewed-export').click();const reviewed=JSON.parse(await blob.text());
    assert(reviewed.records.length===1,'Reviewed export included drafts');
    assert(reviewed.records[0].original_text_sha256,'Reviewed export lost provenance');
    URL.createObjectURL=oldURL;HTMLAnchorElement.prototype.click=oldClick;
    change('source','josephus');
    const checks=$('lines').querySelectorAll('input');
    assert(checks.length>2,'OCR line selection missing');
    checks[0].checked=true;checks[0].dispatchEvent(new Event('change'));
    checks[1].checked=true;checks[1].dispatchEvent(new Event('change'));$('apply-lines').click();
    assert($('editor').value.split('\n').length===2,'Line selection did not replace mixed OCR');
    assert(!$('body-confirmed').checked,'Line selection should require fresh boundary review');
    await $('scan').decode();assert($('scan').naturalWidth>0,'Local preview failed');
    assert($('overlay').children.length===2,'Coordinates not linked to selected lines');
    change('source','pchela');
    assert(window.ReviewCore.hasPUA($('editor').value),'PUA evidence missing');
    $('query').value='THIS_RECORD_DOES_NOT_EXIST';$('query').dispatchEvent(new Event('input'));
    await new Promise(resolve=>setTimeout(resolve,240));
    assert($('editor').disabled,'Empty results left stale editor enabled');
    // Do not leave synthetic corrections in the reviewer's browser.
    localStorage.removeItem('primarychronicle-review-v1:'+window.REVIEW_DATA.package_id);
    report.textContent='PASS: local file, original/translation separation, approval guards, editing, backup, reviewed export, line selection, scan, coordinates, PUA, empty search.';
    // Suppress the application's unsaved-file prompt in this disposable test page.
    window.onbeforeunload=null;
  } catch(error) {report.textContent='FAIL: '+error.stack;}
});
