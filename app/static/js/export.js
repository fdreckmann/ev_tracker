// export.js — export and template management

var _lastExportPreviewToken = null;

async function checkTemplateMappingHash() {
  const banner = $('templateHashMismatchBanner');
  if (!banner) return;
  try {
    const r = await fetch('/api/template/mapping').then(r => r.json());
    if (r.hash_mismatch) {
      banner.style.display = '';
    } else {
      banner.style.display = 'none';
    }
  } catch(_) {}
}

async function doExport(){
  const y=$('expY').value, m=$('expM').value, loc=$('expLoc').value;
  const lang = $('expLang')?.value || 'de';
  const override=getColOverride();
  let url=`/api/export?year=${y}&month=${m}&location=${loc}&lang=${lang}`;
  if(override) url+=`&col_override=${encodeURIComponent(JSON.stringify(override))}`;
  const inclSig = $('expIncludeSig') && !$('expIncludeSig').disabled && $('expIncludeSig').checked;
  if(inclSig) url += '&include_signature=true';
  // check for error response before triggering download
  const resp = await fetch(url);
  if(!resp.ok){
    try{
      const err=await resp.json();
      toast('Export fehlgeschlagen: '+(err.error||resp.statusText),'err');
    } catch(_){ toast('Export fehlgeschlagen: '+resp.statusText,'err'); }
    return;
  }
  const blob=await resp.blob();
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download=`EV_Ladeprotokoll_${y}-${String(m).padStart(2,'0')}.xlsx`;
  a.click();
}

async function saveExportLang(){
  const lang = $('expLang')?.value || 'de';
  await apiFetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({export_language: lang})});
}

function doExportDownloadFromPreview() {
  if (_lastExportPreviewToken) {
    window.location.href = `/api/export/download/${_lastExportPreviewToken}`;
  } else {
    doExport();
  }
}

async function doExportPreview(){
  const y=$('expY').value, m=$('expM').value, loc=$('expLoc').value;
  const lang = $('expLang')?.value || 'de';
  const inclSig = $('expIncludeSig') && !$('expIncludeSig').disabled && $('expIncludeSig').checked;
  _lastExportPreviewToken = null;
  $('expPreviewArea').style.display='';
  $('expPreviewContent').innerHTML='<div class="empty">⏳ Lade Vorschau…</div>';
  $('expPreviewWarnings').textContent='';
  try {
    const r = await apiFetch('/api/export/preview', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({year:parseInt(y), month:parseInt(m), location:loc, lang, include_signature:inclSig})
    }).then(r=>r.json());

    if(!r.ok){ $('expPreviewContent').innerHTML=`<div class="empty" style="color:var(--danger)">${escapeHtml(r.error||'Vorschau fehlgeschlagen')}</div>`; return; }

    if(r.download_token) {
      _lastExportPreviewToken = r.download_token;
    }

    // Show warnings
    let html = '';
    if(r.warnings && r.warnings.length > 0){
      $('expPreviewWarnings').textContent = '⚠ ' + r.warnings.join(' · ');
      html += `<div style="background:#332;padding:8px;border-radius:4px;margin-bottom:8px"><b>⚠️ Hinweise:</b><ul style="margin:4px 0 0 16px">${r.warnings.map(w=>`<li>${escapeHtml(w)}</li>`).join('')}</ul></div>`;
    }

    // Show header values summary
    if(r.header_values){
      const hv = r.header_values;
      const fields = [
        ['Monat', hv.month_year], ['Fahrer', hv.fahrer], ['Kennzeichen', hv.kennzeichen],
        ['Sessions', hv.total_sessions], ['Gesamt kWh', hv.total_kwh ? parseFloat(hv.total_kwh).toFixed(2)+' kWh' : null],
        ['Gesamtkosten', hv.total_cost ? '€'+parseFloat(hv.total_cost).toFixed(2) : null],
        ['Ladezeit', hv.total_charging_hours ? parseFloat(hv.total_charging_hours).toFixed(1)+' h' : null],
        ['Ø Ladeleistung', hv.avg_charge_power_kw ? parseFloat(hv.avg_charge_power_kw).toFixed(2)+' kW' : null],
      ].filter(([k,v]) => v !== null && v !== 'None' && v !== undefined);
      html += `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">`;
      for(const [k,v] of fields){
        html += `<div style="background:rgba(255,255,255,.06);padding:4px 10px;border-radius:4px;font-size:.72rem;font-family:var(--mono)"><span style="color:var(--mute)">${escapeHtml(String(k))}:</span> ${escapeHtml(String(v))}</div>`;
      }
      html += '</div>';
    }

    // Show sheet preview
    const sheets = r.sheets || [];
    if(sheets.length === 0){
      html += '<p>Keine Vorschau verfügbar.</p>';
    } else {
      for(const sheet of sheets){
        html += `<div style="font-size:.72rem;color:var(--mute);margin-bottom:4px">Tabellenblatt: ${escapeHtml(sheet.name)} · Datenstartzeile: ${sheet.data_start_row}</div>`;
        html += '<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:.71rem;font-family:var(--mono);min-width:100%">';
        const rows = normalizeRows(sheet.rows || []);
        for(const row of rows){
          const style = row.is_data ? 'background:rgba(255,255,255,.03)' : 'background:rgba(99,179,237,.08);font-weight:600';
          html += `<tr style="${style}"><td style="color:var(--mute);padding:2px 6px;border-right:1px solid var(--brd)">${row.row}</td>`;
          for(const cell of (row.cells||[])){
            const cellStr = cell == null ? '' : String(cell);
            html += `<td style="padding:2px 8px;border:1px solid rgba(255,255,255,.05);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${cellStr.replace(/"/g,'&quot;')}">${escapeHtml(cellStr)}</td>`;
          }
          html += '</tr>';
        }
        html += '</table></div>';
      }
    }

    $('expPreviewContent').innerHTML = html || '<div class="empty">Keine Vorschau verfügbar</div>';
  } catch(e) {
    $('expPreviewContent').innerHTML = `<div class="empty" style="color:var(--danger)">Fehler: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadExportTemplates(){
  const templates=await fetch('/api/export/templates').then(r=>r.json()).catch(()=>[]);
  const el=$('exportTemplateList');
  if(!el) return;
  if(!templates.length){
    el.innerHTML='<div class="empty" style="padding:20px">Noch keine Vorlagen gespeichert</div>';
    return;
  }
  const canManage = typeof hasPermission === 'function' ? hasPermission('export:templates_manage') : true;
  el.innerHTML='';
  templates.forEach(t=>{
    const tid=escapeHtml(t.id||'');
    const colCount=Object.keys(t.column_mapping||t.mapping||{}).length;
    const cellCount=Object.keys(t.cell_mapping||{}).length;
    const defBadge=t.is_default?'<span style="color:var(--acc);margin-left:8px">★ Standard</span>':'';
    const div=document.createElement('div');
    div.style.cssText='display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--brd);border-radius:10px;padding:12px 14px';
    // Always show Load button; manage buttons only for export:templates_manage
    const manageBtns = canManage
      ? `<button class="btn-g" style="font-size:.72rem;padding:5px 12px" data-tid="${tid}" onclick="setDefaultTemplate(this.dataset.tid)">★</button>
         <button class="btn-d" style="font-size:.72rem;padding:5px 12px" data-tid="${tid}" onclick="deleteExportTemplate(this.dataset.tid)">✕</button>`
      : '';
    div.innerHTML=`
      <div style="flex:1">
        <div style="font-weight:700;font-size:.85rem;color:#fff"></div>
        <div style="font-size:.7rem;font-family:var(--mono);color:var(--mute);margin-top:2px">
          ${colCount} Spalten · ${cellCount} Einzelzellen ${defBadge}
        </div>
      </div>
      <button class="btn-s" style="font-size:.72rem;padding:5px 12px" data-tid="${tid}" onclick="loadExportTemplate(this.dataset.tid)">📂 Laden</button>
      ${manageBtns}
    `;
    div.querySelector('div[style*="font-weight:700"]').textContent=t.name||'';
    el.appendChild(div);
  });
}

async function loadExportTemplate(tid){
  const templates=await fetch('/api/export/templates').then(r=>r.json());
  const t=templates.find(x=>x.id===tid);
  if(!t){ toast('Vorlage nicht gefunden','err'); return; }
  // Support both old "mapping" and new "column_mapping" field names
  const colMap = t.column_mapping || t.mapping || {};
  const payload = {
    mapping:           colMap,
    column_mapping:    colMap,
    cell_mapping:      t.cell_mapping || {},
    signature_mapping: t.signature_mapping || {},
    start_row:         t.start_row,
    header_row:        t.header_row,
    footer_start_row:  t.footer_start_row || null,
    sheet:             t.sheet,
    include_signature: t.include_signature || false,
  };
  const r=await apiFetch('/api/template/mapping',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload)}).then(r=>r.json()).catch(()=>({}));
  if(r && r.ok!==false){ toast('Vorlage "'+escapeHtml(t.name)+'" geladen','ok'); loadMappingPreview(); checkTemplateMappingHash(); }
  else toast('Fehler: '+(r&&r.error||''),'err');
}

async function deleteExportTemplate(tid){
  if(!confirm('Vorlage löschen?')) return;
  await apiFetch(`/api/export/templates/${tid}`,{method:'DELETE'});
  toast('Vorlage gelöscht','ok');
  loadExportTemplates();
}

