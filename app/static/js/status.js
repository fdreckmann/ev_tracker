/**
 * status.js — Dashboard status polling, table rendering, stat tile helpers.
 * Requires: api.js
 */

// normalizeLocation() is defined in api.js (loaded first).

// ── Auto-scale stat values to fit their tile ─────────────────────────────────
function fitText(el) {
  el.style.fontSize = '';
  const parent = el.closest('.stat') || el.parentElement;
  if (!parent) return;
  const available = parent.clientWidth - 36;
  if (el.scrollWidth <= available) return;
  let lo = 10, hi = parseFloat(getComputedStyle(el).fontSize);
  for (let i = 0; i < 12; i++) {
    const mid = (lo + hi) / 2;
    el.style.fontSize = mid + 'px';
    if (el.scrollWidth <= available) lo = mid; else hi = mid;
  }
}
function fitAllStats() {
  document.querySelectorAll('.sv').forEach(fitText);
}
window.addEventListener('resize', fitAllStats);

// ── Status polling ────────────────────────────────────────────────────────────
function _applyLocationToTile(locEl, locStatus, locSrc, meterActive, locTitle) {
  const rawLoc = locStatus ? String(locStatus).trim().toLowerCase() : '';
  if (rawLoc === 'disabled') {
    locEl.textContent = 'Deaktiviert';
    locEl.title = locTitle || 'Standortanzeige ist deaktiviert';
    locEl.className = 'sv';
    return;
  }
  const loc = normalizeEffectiveLocation(locStatus);
  if (loc === 'home') {
    const meterHint = locSrc === 'meter_delta' ? ' 📊' : '';
    locEl.textContent = ' Heim' + meterHint;
    locEl.title = locTitle || (locSrc === 'meter_delta' ? 'Zuhause erkannt über steigenden Wallbox-Zähler' : '');
    locEl.className = 'sv g';
  } else if (loc === 'extern') {
    const conflictHint = locSrc === 'meter_conflict' ? ' ⚠' : '';
    locEl.textContent = ' Extern' + conflictHint;
    locEl.title = locTitle || (locSrc === 'meter_conflict' ? 'Zähler steigt, aber Standortquelle meldet extern' : '');
    locEl.className = 'sv w';
  } else {
    const hint = meterActive ? ' 🔍' : '';
    locEl.textContent = '—' + hint;
    locEl.title = locTitle || (meterActive ? 'Zähler-Heimerkennung aktiv…' : '');
    locEl.className = 'sv';
  }
}

async function refreshStatus() {
  try {
    const vid = window._activeVehicleId || 'v0';
    // /api/status now calls refresh_vehicle_location_state internally (TTL-cached).
    // We also fetch the location endpoint directly to get ha_debug and bypass the cache
    // on the first call after page load.
    const [s, locResp, meterResp] = await Promise.all([
      apiFetch(`/api/status?vehicle_id=${encodeURIComponent(vid)}`, {cache: 'no-store'}).then(r => r.json()),
      apiFetch(`/api/vehicles/${encodeURIComponent(vid)}/location`, {cache: 'no-store'}).then(r => r.json()).catch(() => null),
      apiFetch(`/api/meter/status?vehicle_id=${encodeURIComponent(vid)}`, {cache: 'no-store'}).then(r => r.json()).catch(() => null),
    ]);
    const dot = $('sDot'), txt = $('sTxt');

    const ts = s.tracker_status || (s.running || s.tracker_alive ? 'ready' : 'stopped');
    const _tsMap = {
      not_configured: ['dot warn',     'Nicht konfiguriert'],
      stopped:        ['dot err',      'Tracker gestoppt'],
      provider_error: ['dot err',      'Provider Fehler'],
      no_data:        ['dot warn',     'Warte auf Daten'],
      polling:        ['dot warn',     'Verbinde…'],
      charging:       ['dot charging', 'Lädt ⚡'],
      ready:          ['dot ok',       'Aktiv'],
    };
    const [dotCls, dotTxt] = _tsMap[ts] || ['dot ok', 'Aktiv'];
    dot.className = dotCls; txt.textContent = dotTxt;

    $('dSoc').textContent = s.soc_current != null ? fmt(s.soc_current, 0) + '%' : '—';
    $('dOdo').textContent = s.odo_current != null ? Math.round(s.odo_current).toLocaleString('de') : '—';
    $('dPoll').textContent = s.last_poll ? s.last_poll.substring(11, 16) : '—';

    const providerEl = $('dProviderName');
    if (providerEl) {
      const name = s.provider_name || s.provider || s.provider_id || '—';
      providerEl.textContent = name;
      if (s.provider_connected === false && s.provider_last_error) {
        providerEl.className = 'sv err';
        providerEl.title = s.provider_last_error;
      } else {
        providerEl.className = 'sv';
        providerEl.title = 'Provider: ' + (s.provider_id || s.provider || name);
      }
    }
    const providerSubEl = $('dProviderSub');
    if (providerSubEl) {
      if (s.provider_connected === false) {
        providerSubEl.textContent = 'Fehler';
        providerSubEl.style.color = 'var(--danger)';
      } else if (s.provider_connected === true) {
        providerSubEl.textContent = 'Verbunden';
        providerSubEl.style.color = 'var(--acc)';
      } else {
        providerSubEl.textContent = 'Provider';
        providerSubEl.style.color = '';
      }
    }

    const locEl = $('dLoc');
    if (locEl) {
      // Prefer direct location endpoint (may have bypassed TTL cache).
      // Fall back to /api/status fields which are always fresh after this fix.
      // Prefer the direct location endpoint; fall back to /api/status fields.
      // Preserve "disabled" explicitly — normalizeEffectiveLocation maps it to "unknown".
      let rawLocStatus = (locResp && locResp.status) || s.effective_location || s.location_status || s.location || 'unknown';
      let locStatus = rawLocStatus;
      let locSrc    = (locResp && locResp.source) || s.location_source || '';
      let locTitle  = '';
      if (locResp && locResp.ok) {
        // Only overwrite locStatus if the direct endpoint has a meaningful value
        const direct = String(locResp.status || '').trim().toLowerCase();
        if (direct === 'disabled') {
          locStatus = 'disabled';
        } else {
          locStatus = normalizeEffectiveLocation(locResp.status, s.effective_location, s.location_status, s.location);
        }
        locSrc   = locResp.source || locSrc;
        locTitle = locResp.source_detail || '';
      } else if (locResp && locResp.error) {
        locTitle = 'Fehler: ' + locResp.error;
      } else {
        // No direct location response — check if /api/status says disabled
        const fallbackRaw = String(s.effective_location || s.location_status || '').trim().toLowerCase();
        if (fallbackRaw === 'disabled') {
          locStatus = 'disabled';
        } else {
          locStatus = normalizeEffectiveLocation(s.effective_location, s.location_status, s.location);
        }
      }
      _applyLocationToTile(locEl, locStatus, locSrc, s.meter_home_det_active, locTitle);
    }

    const rows = await fetch('/api/sessions').then(r => r.json()).catch(() => []);
    const typeEl = $('dType'), pwrEl = $('dPwr'), chargeLabel = $('dChargeLabel');
    if (typeEl && chargeLabel) {
      if (s.charging) {
        chargeLabel.textContent = 'Lädt gerade ⚡';
        const _sloc = normalizeLocation(s.location_status || s.location);
        const locPart = _sloc === 'home' ? '🏠 Zuhause' : _sloc === 'extern' ? '🔌 Extern' : '';
        const typePart = s.charger_type === 'dc' ? 'DC' : s.charger_type === 'ac' ? 'AC' : '';
        typeEl.textContent = [locPart, typePart].filter(Boolean).join(' · ') || '⚡';
        typeEl.className = 'sv w';
        if (pwrEl) pwrEl.textContent = s.power_kw != null ? Number(s.power_kw).toFixed(1) + ' kW' : '—';
      } else {
        const lastSess = rows.find(r => r.end_ts && (r.charger_type || r.location));
        if (lastSess) {
          chargeLabel.textContent = 'Letzte Ladung';
          const _lloc = normalizeLocation(lastSess.location);
          const locPart = _lloc === 'home' ? '🏠 Zuhause' : _lloc === 'extern' ? '🔌 Extern' : '';
          const typePart = lastSess.charger_type === 'dc' ? 'DC' : lastSess.charger_type === 'ac' ? 'AC' : '';
          typeEl.textContent = [locPart, typePart].filter(Boolean).join(' · ') || '—';
          typeEl.className = 'sv';
          if (pwrEl) pwrEl.textContent = lastSess.end_ts ? _timeAgo(lastSess.end_ts) : '—';
        } else {
          chargeLabel.textContent = 'Ladevorgang';
          typeEl.textContent = '—'; typeEl.className = 'sv';
          if (pwrEl) pwrEl.textContent = '—';
        }
      }
    }

    if (s.charging) {
      $('dSt').textContent = 'Laden'; $('dSt').className = 'sv w';
      $('dStSub').textContent = 'Session #' + (s.session_id || '?');
      $('cbarWrap').classList.add('vis');
      $('cbarFill').style.width = (s.soc_current || 0) + '%';
      $('cbarSession').textContent = `Session #${s.session_id || '?'} · SOC ${fmt(s.soc_current, 0)}%`;
    } else {
      const _tileMap = {
        stopped:        ['Gestoppt',         'sv'],
        provider_error: ['Provider Fehler',  'sv err'],
        no_data:        ['Warte auf Daten',  'sv'],
        polling:        ['Verbinde…',        'sv'],
        ready:          ['Bereit',           'sv g'],
      };
      const [_stLabel, _stClass] = _tileMap[ts] || ['Bereit', 'sv g'];
      $('dSt').textContent = _stLabel;
      $('dSt').className = _stClass;
      $('dStSub').textContent = s.last_error ? ('⚠ ' + s.last_error) : (s.last_successful_poll ? '' : 'Noch kein Poll') || 'Kein Ladevorgang';
      $('cbarWrap').classList.remove('vis');
    }

    renderTbl($('recentTbl'), rows.slice(0, 5), false);

    // Live-Zählerstand from /api/meter/status (TTL-cached server-side)
    const meterTile = $('dMeterTile');
    const meterEl   = $('dMeter');
    const meterSub  = $('dMeterSub');
    if (meterTile && meterEl) {
      if (meterResp && meterResp.source && meterResp.source !== 'none') {
        const srcLabel = meterResp.source;
        const timeStr  = meterResp.last_read ? meterResp.last_read.substring(11, 16) : '';
        if (meterResp.ok && meterResp.value_kwh != null) {
          meterEl.textContent = Number(meterResp.value_kwh).toLocaleString('de', {maximumFractionDigits: 1});
          meterEl.className = 'sv';
          meterEl.title = (meterResp.endpoint ? meterResp.endpoint : '');
          if (meterSub) {
            meterSub.textContent = srcLabel + ' · ✓' + (timeStr ? ' · ' + timeStr : '');
            meterSub.style.color = '';
          }
        } else {
          meterEl.textContent = '—';
          meterEl.className = 'sv err';
          meterEl.title = meterResp.error || 'Lesefehler';
          if (meterSub) {
            meterSub.textContent = srcLabel + ' · Fehler';
            meterSub.style.color = 'var(--danger)';
          }
        }
        meterTile.style.display = '';
      } else {
        meterTile.style.display = 'none';
      }
    }

    fitAllStats();
  } catch (e) {
    $('sDot').className = 'dot err'; $('sTxt').textContent = 'Fehler';
  }
}

// ── Table helpers ─────────────────────────────────────────────────────────────
function locBadge(loc, locSource) {
  const n = normalizeLocation(loc);
  let badge = '';
  if (n === 'home')   badge = '<span class="loc-home">🏠 Zuhause</span>';
  else if (n === 'extern') badge = '<span class="loc-ext">⚡ Extern</span>';
  else badge = '<span class="loc-unk">—</span>';
  if (locSource === 'meter_delta') badge += '<span title="Erkannt über Zähler-Delta" style="color:#34d399;font-size:.6rem;margin-left:3px">📊</span>';
  else if (locSource === 'meter_conflict') badge += '<span title="Zähler steigt, aber Standort extern gemeldet" style="color:#f59e0b;font-size:.6rem;margin-left:3px">⚠</span>';
  return badge;
}
function typeBadge(t, kw) {
  const pw = kw ? ` ${Number(kw).toFixed(1)} kW` : '';
  if (t === 'dc') return `<span style="color:#f59e0b;font-size:.7rem">⚡ DC${pw}</span>`;
  if (t === 'ac') return `<span style="color:#00b4ff;font-size:.7rem">🔌 AC${pw}</span>`;
  return `<span style="color:var(--mute);font-size:.7rem">—</span>`;
}

function renderTbl(el, rows, showDel = true) {
  if (!rows.length) { el.innerHTML = '<div class="empty">Keine Ladevorgänge</div>'; return; }
  const hasMeter   = rows.some(r => r.meter_old != null || r.meter_new != null);
  const hasVehicle = rows.some(r => r.vehicle_id && r.vehicle_id !== 'v0') || new Set(rows.map(r => r.vehicle_id)).size > 1;
  const fmtMeter   = v => v != null ? Math.round(Number(v)).toLocaleString('de') : '—';
  el.innerHTML = `<table>
    <thead><tr>
      <th>#</th><th>Datum</th><th>Start→Ende</th>
      ${hasVehicle ? '<th>Fahrzeug</th>' : ''}
      <th>SOC</th><th>Lader</th><th>kWh</th><th>Preis</th><th>Kosten</th>
      ${hasMeter ? '<th>Zähler Alt→Neu</th>' : ''}
      <th>Standort</th>
      ${showDel ? '<th></th>' : ''}
    </tr></thead>
    <tbody>${rows.map(r => `<tr style='cursor:pointer' onclick='showSessionDetail(${r.id})'>
      <td class="num">${r.id}</td>
      <td>${fmtDate(r.start_ts).split(' ')[0]}</td>
      <td>${fmtTime(r.start_ts)} → ${r.end_ts ? fmtTime(r.end_ts) : '…'}</td>
      ${hasVehicle ? `<td style="font-size:.72rem;font-family:var(--mono);color:var(--acc2)">${r.vehicle_id || 'v0'}</td>` : ''}
      <td>${fmt(r.soc_start, 0)}% → ${fmt(r.soc_end, 0)}%</td>
      <td>${typeBadge(r.charger_type, r.max_power_kw)}</td>
      <td class="g">${fmt(r.kwh_charged)} kWh</td>
      <td style="font-size:.72rem;color:var(--mute)">${r.price_per_kwh ? fmt(r.price_per_kwh, 4) + ' €/kWh' : '—'}</td>
      <td class="w" id="cost_${r.id}">
        ${r.cost_eur != null ? fmt(r.cost_eur) + ' €' : '—'}
        ${r.cost_manual ? '<span title="Kosten manuell" style="color:var(--acc);font-size:.65rem"> ✎</span>' : ''}
      </td>
      ${hasMeter ? `<td style="font-size:.72rem;color:#a78bfa;font-family:var(--mono)">${fmtMeter(r.meter_old)} → ${fmtMeter(r.meter_new)}</td>` : ''}
      <td>${locBadge(r.location, r.location_source)}${(r.provider==='manual'||r.created_mode==='manual')?' <span title="Manuell erfasst" style="background:rgba(100,200,255,.12);color:#64c8ff;border:1px solid rgba(100,200,255,.25);border-radius:3px;padding:1px 5px;font-size:.6rem;font-family:var(--mono)">✏</span>':''}</td>
      ${showDel ? `<td style="display:flex;gap:4px;padding:8px 4px">
        <button onclick="editCost(${r.id},${r.kwh_charged || 0},${r.price_per_kwh || 0})"
          style="background:rgba(0,180,255,.12);color:#00b4ff;border:1px solid rgba(0,180,255,.25);
          border-radius:6px;padding:3px 8px;font-size:.65rem;cursor:pointer" title="Kosten bearbeiten">✎</button>
        <button onclick="editLocation(${r.id},${JSON.stringify(r.location||'unknown')})"
          style="background:rgba(61,220,151,.12);color:#3ddc97;border:1px solid rgba(61,220,151,.25);
          border-radius:6px;padding:3px 8px;font-size:.65rem;cursor:pointer" title="Standort ändern">📍</button>
        <button onclick="delSession(${r.id})"
          style="background:rgba(239,68,68,.12);color:#ef4444;border:1px solid rgba(239,68,68,.25);
          border-radius:6px;padding:3px 8px;font-size:.65rem;cursor:pointer">✕</button>
      </td>` : ''}
    </tr>`).join('')}</tbody>
  </table>`;
}
