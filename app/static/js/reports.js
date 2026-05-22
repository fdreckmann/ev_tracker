// reports.js — report config, history, archive

var _repMonths = [];

async function loadReportConfig(){
  try {
    const r = await apiFetch('/api/report/config').then(r=>r.json());
    const s = id => $(id);
    if (s('rep_enabled'))           s('rep_enabled').checked             = !!r.report_email_enabled;
    if (s('rep_recipients'))        s('rep_recipients').value            = (r.report_email_recipients||[]).join('\n');
    if (s('rep_schedule_type'))     s('rep_schedule_type').value         = r.report_email_schedule_type||'monthly';
    if (s('rep_period_mode'))       s('rep_period_mode').value           = r.report_email_period_mode||'previous_period';
    if (s('rep_location_filter'))   s('rep_location_filter').value       = r.report_email_location_filter||'all';
    if (s('rep_include_excel'))     s('rep_include_excel').checked       = r.report_email_include_excel !== false;
    if (s('rep_include_summary'))   s('rep_include_summary').checked     = r.report_email_include_summary !== false;
    if (s('rep_include_signature')) s('rep_include_signature').checked   = !!r.report_email_include_signature;
    if (s('rep_language'))          s('rep_language').value              = r.report_email_language||'auto';
    onRepScheduleTypeChange();
    // Set dynamic fields after render
    setTimeout(() => {
      if (s('rep_time'))         s('rep_time').value         = r.report_email_time||'08:00';
      if (s('rep_weekday'))      s('rep_weekday').value      = r.report_email_weekday||1;
      if (s('rep_day_of_month')) s('rep_day_of_month').value = r.report_email_day_of_month||1;
      if (s('rep_month'))        s('rep_month').value        = r.report_email_month||1;
      if (s('rep_custom_days'))  s('rep_custom_days').value  = r.report_email_custom_days||14;
      if (s('rep_cron'))         s('rep_cron').value         = r.report_email_cron||'';
      if (s('rep_custom_start')) s('rep_custom_start').value = r.report_email_custom_start_date||'';
      if (s('rep_custom_end'))   s('rep_custom_end').value   = r.report_email_custom_end_date||'';
      if (s('rep_single_month')) s('rep_single_month').value = r.report_email_single_month||'';
      _repMonths = (r.report_email_months||[]).filter(m => /^\d{4}-\d{2}$/.test(m));
      _repMonths.sort();
      repMonthsRender();
    }, 50);
    // Populate vehicle filter
    const vf = s('rep_vehicle_filter');
    if (vf) {
      while (vf.options.length > 1) vf.remove(1);
      (window._allVehicles||[]).forEach(v => {
        const o = document.createElement('option'); o.value = v.id; o.textContent = v.name||v.id;
        vf.appendChild(o);
      });
      vf.value = r.report_email_vehicle_filter||'all';
    }
  } catch(e){ console.warn('loadReportConfig', e); }
}

async function saveReportConfig(){
  const g = id => $(id);
  const recipients_raw = (g('rep_recipients')?.value||'').split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  const payload = {
    report_email_enabled:          g('rep_enabled')?.checked||false,
    report_email_recipients:       recipients_raw,
    report_email_schedule_type:    g('rep_schedule_type')?.value||'monthly',
    report_email_period_mode:      g('rep_period_mode')?.value||'previous_period',
    report_email_time:             g('rep_time')?.value||'08:00',
    report_email_weekday:          parseInt(g('rep_weekday')?.value||1),
    report_email_day_of_month:     parseInt(g('rep_day_of_month')?.value||1),
    report_email_month:            parseInt(g('rep_month')?.value||1),
    report_email_custom_days:      parseInt(g('rep_custom_days')?.value||14),
    report_email_cron:             g('rep_cron')?.value||'',
    report_email_custom_start_date: g('rep_custom_start')?.value||'',
    report_email_custom_end_date:   g('rep_custom_end')?.value||'',
    report_email_single_month:      g('rep_single_month')?.value||'',
    report_email_months:            _repMonths.slice(),
    report_email_location_filter:  g('rep_location_filter')?.value||'all',
    report_email_vehicle_filter:   g('rep_vehicle_filter')?.value||'all',
    report_email_include_excel:    g('rep_include_excel')?.checked!==false,
    report_email_include_summary:  g('rep_include_summary')?.checked!==false,
    report_email_include_signature: g('rep_include_signature')?.checked||false,
    report_email_language:         g('rep_language')?.value||'auto',
  };
  const st = $('rep_status');
  if (st) { st.textContent = '⏳ Speichere…'; st.style.color = 'var(--mute)'; }
  try {
    const r = await apiFetch('/api/report/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json());
    if (st) { st.textContent = r.ok ? '✅ Gespeichert' : '❌ '+(r.error||'Fehler'); st.style.color = r.ok ? '#6ee7b7' : '#f87171'; }
  } catch(e){ if (st) { st.textContent = '❌ '+e.message; st.style.color='#f87171'; } }
}

async function sendReportNow(isTest){
  const g = id => $(id);
  const loc = g('rep_location_filter')?.value||'all';
  const veh = g('rep_vehicle_filter')?.value||'all';
  const lang = g('rep_language')?.value||'auto';
  const recipients_raw = (g('rep_recipients')?.value||'').split(/[\n,]+/).map(s=>s.trim()).filter(Boolean);
  const payload = {
    report_email_location_filter: loc,
    report_email_vehicle_filter:  veh,
    report_email_language:        lang,
    report_email_recipients:      isTest ? recipients_raw.slice(0,1) : recipients_raw,
    report_email_schedule_type:   g('rep_schedule_type')?.value||'monthly',
    report_email_period_mode:     g('rep_period_mode')?.value||'previous_period',
    report_email_single_month:    g('rep_single_month')?.value||'',
    report_email_months:          _repMonths.slice(),
  };
  const st = $('rep_status');
  if (st) { st.textContent = '⏳ Sende…'; st.style.color = 'var(--mute)'; }
  try {
    const r = await apiFetch('/api/report/send-now',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json());
    if (st) { st.textContent = r.ok ? '✅ Gesendet' : '❌ '+(r.error||'Fehler'); st.style.color = r.ok ? '#6ee7b7' : '#f87171'; }
  } catch(e){ if (st) { st.textContent = '❌ '+e.message; st.style.color='#f87171'; } }
}

async function loadReportHistory(){
  const card = $('rep_history_card');
  const body = $('rep_history_body');
  if (!card||!body) return;
  card.style.display = 'block';
  body.textContent = '⏳ Lade…';
  try {
    const rows = await apiFetch('/api/report/history').then(r=>r.json());
    if (!rows.length) { body.textContent = 'Noch keine Reports gesendet.'; return; }
    const STATUS_COLORS = {sent:'#6ee7b7',error:'#f87171',skipped:'#f59e0b'};
    const LOC_LABELS = {all:'Alle',home:'Zuhause',external:'Extern'};
    body.innerHTML = `<table style="width:100%;border-collapse:collapse">
      <thead><tr style="color:var(--mute);border-bottom:1px solid var(--brd)">
        <th style="padding:6px 10px;text-align:left">Datum</th>
        <th style="padding:6px 10px;text-align:left">Zeitraum / Label</th>
        <th style="padding:6px 10px;text-align:left">Filter</th>
        <th style="padding:6px 10px;text-align:left">Empfänger</th>
        <th style="padding:6px 10px;text-align:left">Status</th>
        <th style="padding:6px 10px;text-align:left">Auslöser</th>
      </tr></thead>
      <tbody>${rows.map(r=>{
        const recip = (() => { try { return JSON.parse(r.recipients||'[]').join(', '); } catch { return r.recipients||''; } })();
        const color = STATUS_COLORS[r.status]||'var(--mute)';
        return `<tr style="border-bottom:1px solid rgba(255,255,255,.04)">
          <td style="padding:5px 10px">${(r.sent_at||'').replace('T',' ').slice(0,16)}</td>
          <td style="padding:5px 10px">${r.period_label || ((r.period_start||'') + ' – ' + (r.period_end||''))}</td>
          <td style="padding:5px 10px">${LOC_LABELS[r.location_filter||'all']||r.location_filter||'alle'}</td>
          <td style="padding:5px 10px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(recip)}">${escapeHtml(recip)}</td>
          <td style="padding:5px 10px;color:${color};font-weight:600">${r.status||''}</td>
          <td style="padding:5px 10px;color:var(--mute)">${r.triggered_by||''}</td>
        </tr>`;
      }).join('')}</tbody></table>`;
  } catch(e){ body.textContent = '❌ '+e.message; }
}

async function loadReportArchive(){
  const body = $('archive_table_body');
  if(!body) return;
  body.textContent='⏳ Lade…';
  try{
    const rows = await apiFetch('/api/reports/archive').then(r=>r.json());
    if(!rows.length){body.textContent='Noch keine archivierten Reports.';return;}
    const STATUS_COLORS={generated:'#6ee7b7',sent:'#6ee7b7',approved:'#6ee7b7',failed:'#f87171',draft:'#f59e0b'};
    body.innerHTML=`<table style="width:100%;border-collapse:collapse">
      <thead><tr style="color:var(--mute);border-bottom:1px solid var(--brd)">
        <th style="padding:6px 8px;text-align:left">Datum</th>
        <th style="padding:6px 8px;text-align:left">Zeitraum</th>
        <th style="padding:6px 8px;text-align:left">Filter</th>
        <th style="padding:6px 8px;text-align:left">Status</th>
        <th style="padding:6px 8px;text-align:left">Aktionen</th>
      </tr></thead>
      <tbody>${rows.map(r=>{
        const c=STATUS_COLORS[r.status]||'var(--mute)';
        return `<tr style="border-bottom:1px solid rgba(255,255,255,.04)">
          <td style="padding:5px 8px">${(r.created_at||'').slice(0,16).replace('T',' ')}</td>
          <td style="padding:5px 8px">${escapeHtml(r.period_label||'')}</td>
          <td style="padding:5px 8px">${r.location_filter||'all'}</td>
          <td style="padding:5px 8px;color:${c};font-weight:600">${r.status||''}</td>
          <td style="padding:5px 8px;display:flex;gap:6px;flex-wrap:wrap">
            ${r.has_excel ? `<button onclick="downloadReport(${r.id},'excel')" class="btn-s" style="font-size:.7rem;padding:3px 8px">📥 XLSX</button>` : `<button class="btn-s" disabled style="font-size:.7rem;padding:3px 8px;opacity:.4;cursor:not-allowed" title="Kein XLSX für diesen Report">📥 XLSX</button>`}
            ${r.has_pdf   ? `<button onclick="downloadReport(${r.id},'pdf')"   class="btn-s" style="font-size:.7rem;padding:3px 8px">📄 PDF</button>`  : `<button class="btn-s" disabled style="font-size:.7rem;padding:3px 8px;opacity:.4;cursor:not-allowed" title="Kein PDF für diesen Report">📄 PDF</button>`}
            <button onclick="sendArchiveReport(${r.id})" class="btn-s" style="font-size:.7rem;padding:3px 8px">📤 Senden</button>
          </td>
        </tr>`;
      }).join('')}</tbody></table>`;
  }catch(e){body.textContent='❌ '+e.message;}
}

async function createReport(){
  const st   = $('archive_table_body');
  const mode = $('arc_period_mode')?.value || 'previous_period';
  const payload = {
    period_mode:      mode,
    location_filter:  $('arc_loc_filter')?.value || 'all',
    lang:          'de',
    include_excel: $('arc_inc_excel')?.checked !== false,
    include_pdf:   $('arc_inc_pdf')?.checked   || false,
  };
  if(mode === 'single_month'){
    const mv = $('arc_single_month')?.value;
    if(!mv){ toast('Bitte einen Monat auswählen','err'); return; }
    payload.single_month = mv;  // YYYY-MM — direkt, kein email-Config-Key
  }
  if(st) st.textContent='⏳ Erstelle Report…';
  try{
    const r = await apiFetch('/api/reports/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json());
    if(r.ok){ if(st) st.textContent=''; loadReportArchive(); }
    else if(st) st.textContent='❌ '+(r.error||'Fehler');
  }catch(e){if(st) st.textContent='❌ '+e.message;}
}

function downloadReport(id, fmt){
  window.open(`/api/reports/${id}/download/${fmt}`, '_blank');
}

async function sendArchiveReport(id){
  const to = prompt('Empfänger E-Mail:');
  if(!to) return;
  const r = await apiFetch(`/api/reports/${id}/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({recipients:[to]})}).then(r=>r.json());
  alert(r.ok ? '✅ Gesendet' : '❌ '+(r.error||'Fehler'));
}

