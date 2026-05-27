// templates.js — Template management functions
// Provides: previewGalleryTemplate, useGalleryTemplate, updateActiveTemplateInfo,
//           runTemplateAnalysis, saveCurrentAsTemplate, setDefaultTemplate
// Helpers:  showAnalysisPanel, highlightAnalysisCells

function previewGalleryTemplate(id){
  window.open(`/api/template/gallery/${id}/preview`, '_blank');
}

async function useGalleryTemplate(id, name){
  if(!confirm(`Vorlage "${name}" aktivieren? Dein aktuelles Template und Mapping werden überschrieben.`)) return;
  try {
    const r = await apiFetch(`/api/template/gallery/${id}/use`, {method:'POST'}).then(r=>r.json());
    if(r.ok){
      toast(`✓ Vorlage "${name}" aktiviert`);
      _galleryData = null; // force reload
      await loadGallery();
      renderGallery();
      closeGalleryModal();
      // Refresh template status UI
      await refreshTplInfo();
      loadMappingPreview();
      updateActiveTemplateInfo(r.active_template);
    } else {
      toast('❌ ' + (r.error||'Fehler'), 'err');
    }
  } catch(e){
    toast('❌ ' + e.message, 'err');
  }
}

function updateActiveTemplateInfo(info){
  const el = $('activeTemplateInfo');
  if(!el) return;
  if(!info || !info.source){
    el.textContent = '';
  } else if(info.source === 'builtin'){
    const sp = document.createElement('span');
    sp.style.color = 'var(--acc)';
    sp.textContent = '✓ ' + (info.name || '');
    el.textContent = '';
    el.appendChild(sp);
  } else {
    const sp = document.createElement('span');
    sp.style.color = 'var(--mute)';
    sp.textContent = 'Eigene Vorlage';
    el.textContent = '';
    el.appendChild(sp);
  }
}

async function runTemplateAnalysis(){
  const btn = $('analyzeBtn');
  btn.disabled = true; btn.textContent = '⏳ Analysiere…';
  try {
    const res = await apiFetch('/api/template/analyze').then(r=>r.json());
    btn.disabled = false; btn.textContent = '🔍 Automatisch analysieren';
    if(!res.ok){ toast('❌ ' + (res.error||'Analysefehler'), 'err'); return; }
    // Store result but do NOT apply immediately (TEIL 7)
    _lastAnalysis = res;
    _analysisResult = res;
    showAnalysisPanel(res);
    // Show sig suggestion button if available
    if(res.signature_suggestion && $('sigAnalysisSuggestBtn')){
      $('sigAnalysisSuggestBtn').style.display = '';
    }
  } catch(e){
    btn.disabled = false; btn.textContent = '🔍 Automatisch analysieren';
    toast('❌ Analysefehler: ' + e.message, 'err');
  }
}

function showAnalysisPanel(a){
  const panel = $('analysisPanel');
  panel.style.display = 'block';

  // Confidence bar
  const pct = Math.round((a.confidence||0)*100);
  const col = pct>=80?'var(--acc)':pct>=60?'#eab308':'var(--danger)';
  var _eh2 = typeof escapeHtml === 'function' ? escapeHtml : function(s){return String(s||'').replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];});};
  $('analysisConfidence').innerHTML =
    `<span style="color:${col};font-weight:600">${pct}% Konfidenz</span>` +
    (a.sheet ? `<span style="color:var(--mute);margin-left:10px">Sheet: ${_eh2(a.sheet)}</span>` : '') +
    (a.start_row ? `<span style="color:var(--mute);margin-left:10px">Startzeile: ${_eh2(String(a.start_row))}</span>` : '');

  // Warnings
  const warnings = a.warnings||[];
  $('analysisWarnings').innerHTML = warnings.length
    ? `<div style="color:#f59e0b;font-size:.72rem;font-family:var(--mono)">${warnings.map(w=>`⚠ ${_eh2(w)}`).join('<br>')}</div>`
    : '';

  // Suggestions summary
  const colCount  = Object.keys(a.column_mapping||{}).length;
  const cellCount = Object.keys(a.cell_mapping||{}).length;
  const phCount   = (a.placeholders||[]).length;
  $('analysisSuggestions').innerHTML =
    `✓ ${colCount} Spalten erkannt &nbsp;·&nbsp; ${cellCount} Einzelzellen erkannt &nbsp;·&nbsp; ${phCount} Platzhalter`;

  // Highlight cells in grid
  highlightAnalysisCells(a);
}

function highlightAnalysisCells(a){
  // Highlight column headers
  Object.entries(a.column_mapping||{}).forEach(([col, info])=>{
    const th = document.querySelector(`#excelGridHead th[data-col="${col}"]`);
    if(th){
      const field = info.field||info;
      const color = FIELD_COLORS[field]||'#22c55e';
      th.style.background = color + '33';
      th.style.color = color;
    }
  });
  // Highlight cell mapping cells
  Object.entries(a.cell_mapping||{}).forEach(([addr, info])=>{
    const col = addr.replace(/[0-9]/g,'');
    const row = parseInt(addr.replace(/[A-Z]/gi,''));
    const colIdx = col.split('').reduce((acc,c)=>acc*26+c.charCodeAt(0)-64,0);
    const td = document.getElementById(`gc_${row}_${colIdx}`);
    if(td){
      td.style.outline = '2px solid #f59e0b';
      td.title = `→ ${info.field||info} (${Math.round((info.confidence||0)*100)}%)`;
    }
  });
  // Highlight placeholders
  (a.placeholders||[]).forEach(ph=>{
    const col = ph.cell.replace(/[0-9]/g,'');
    const row = parseInt(ph.cell.replace(/[A-Z]/gi,''));
    const colIdx = col.split('').reduce((acc,c)=>acc*26+c.charCodeAt(0)-64,0);
    const td = document.getElementById(`gc_${row}_${colIdx}`);
    if(td){
      td.style.outline = '2px solid #a855f7';
      td.title = `Platzhalter → ${ph.field}`;
    }
  });
}

async function saveCurrentAsTemplate(){
  const name=prompt('Name für diese Vorlage:','Meine Vorlage');
  if(!name) return;
  const saved=await fetch('/api/template/mapping').then(r=>r.json()).catch(()=>({}));
  const colMap = saved.column_mapping || saved.mapping || {};
  const payload = {
    name,
    mapping:            colMap,
    column_mapping:     colMap,
    cell_mapping:       saved.cell_mapping || {},
    signature_mapping:  saved.signature_mapping || {},
    start_row:          saved.start_row,
    header_row:         saved.header_row,
    footer_start_row:   saved.footer_start_row || null,
    sheet:              saved.sheet,
    include_signature:  saved.include_signature || false,
  };
  const r=await apiFetch('/api/export/templates',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)
  }).then(r=>r.json());
  if(r.ok){ toast('Vorlage gespeichert','ok'); loadExportTemplates(); }
  else toast('Fehler','err');
}

async function setDefaultTemplate(tid){
  await apiFetch(`/api/export/templates/${tid}`,{method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({is_default:true})});
  toast('Als Standardvorlage gesetzt','ok');
  loadExportTemplates();
}
