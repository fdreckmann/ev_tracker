/**
 * config.js — Provider selection, config load/save, connection test, tracker restart.
 * Requires: api.js
 */

function selectProvider(id) {
  $('c_provider').value = id;
  document.querySelectorAll('[id^="pcard_"]').forEach(el => {
    const pid = el.id.replace('pcard_', '');
    el.style.border = pid === id ? '2px solid var(--acc)' : '2px solid var(--brd)';
    el.querySelector('div').style.color = pid === id ? 'var(--acc)' : '#fff';
  });
  document.querySelectorAll('[id^="notes_"]').forEach(el => {
    el.style.display = el.id === `notes_${id}` ? 'block' : 'none';
  });
  loadProviderFields(id);
}

async function loadProviderFields(pid) {
  if (!pid) return;
  const fields = await fetch(`/api/providers/${pid}/fields`).then(r => r.json()).catch(() => []);
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  const container = $('providerFields');
  if (!container) return;
  container.innerHTML = fields.map(f => {
    const val = cfg[f.id] || '';
    const w = fields.length <= 2 ? 'full' : '';
    const hasSaved = f.type === 'password' && !!val;
    let inp;
    if (f.type === 'checkbox') {
      const checked = cfg[f.id] ? 'checked' : '';
      inp = `<input type="checkbox" id="pf_${f.id}" ${checked} style="width:auto;margin-right:6px">`;
    } else if (f.type === 'select') {
      inp = `<select id="pf_${f.id}" style="background:var(--bg);border:1px solid var(--brd);border-radius:8px;color:var(--txt);font-family:var(--mono);font-size:.84rem;padding:9px 13px;width:100%">${(f.options || []).map(o => `<option value="${o}" ${val === o ? 'selected' : ''}>${o}</option>`).join('')}</select>`;
    } else {
      inp = `<input type="${f.type || 'text'}" id="pf_${f.id}" value="${f.type === 'password' ? '' : val}" placeholder="${f.placeholder || ''}">`;
    }
    const hintHtml = hasSaved
      ? `<span class="hint" style="color:var(--acc)">Token gespeichert – leer lassen zum Beibehalten</span>`
      : (f.hint ? `<span class="hint">${f.hint}</span>` : '');
    return `<div class="fg ${w}">
      <label>${f.label}${!f.required ? ' <span style="color:var(--mute);font-size:.65rem">(optional)</span>' : ''}</label>
      ${inp}${hintHtml}
    </div>`;
  }).join('');
}

async function saveConfig() {
  const cfg = {
    provider: $('c_provider')?.value || 'ha',
    car_name: $('c_car')?.value || '',
  };
  const bat  = parseFloat($('c_bat')?.value);
  const poll = parseInt($('c_poll')?.value);
  if (!isNaN(bat))  cfg.battery_capacity_kwh = bat;
  if (!isNaN(poll)) cfg.poll_interval = poll;
  document.querySelectorAll('[id^="pf_"]').forEach(el => {
    const key = el.id.replace('pf_', '');
    if (el.type === 'password' && (!el.value || el.value === '********')) return;
    if (el.type === 'checkbox') { cfg[key] = el.checked; return; }
    const v = el.type === 'number' ? parseFloat(el.value) : el.value;
    cfg[key] = v;
  });
  const r = await apiFetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(cfg),
  });
  if (r.ok) { toast('Konfiguration gespeichert'); if($('carName') && cfg.car_name) $('carName').textContent = cfg.car_name; }
  else toast('Fehler beim Speichern', 'err');
}

async function restartTracker() {
  const btn = $('btnRestartTracker');
  if (btn) btn.disabled = true;
  const r = await apiFetch('/api/tracker/restart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ vehicle_id: 'v0' }),
  }).then(x => x.json()).catch(() => ({ ok: false }));
  if (r.ok) toast('Tracker neu gestartet');
  else toast('Fehler: ' + (r.error || 'unbekannt'), 'err');
  if (btn) btn.disabled = false;
  setTimeout(refreshStatus, 2000);
}

async function testConn() {
  const res = $('connRes');
  res.className = 'conn-res'; res.style.display = 'block'; res.textContent = '⏳ Teste Verbindung…';
  const body = { provider: $('c_provider')?.value || 'ha' };
  document.querySelectorAll('[id^="pf_"]').forEach(el => {
    const key = el.id.replace('pf_', '');
    if (el.type === 'checkbox') body[key] = el.checked;
    else body[key] = el.value;
  });
  const saved = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  document.querySelectorAll('[id^="pf_"]').forEach(el => {
    const key = el.id.replace('pf_', '');
    if (el.type === 'password' && (!body[key] || body[key] === '********')) body[key] = saved[key] || '';
  });
  const r = await apiFetch('/api/test-connection', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json());
  if (r.ok) { res.className = 'conn-res ok'; res.textContent = r.message; }
  else { res.className = 'conn-res err'; res.textContent = '❌ ' + (r.message || r.error || 'Fehler'); }
}
