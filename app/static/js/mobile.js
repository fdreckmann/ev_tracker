// mobile.js — provides:
//   mobileNavTo, switchToDesktopSettings,
//   refreshMobileDashboard, renderMobileRecentSessions,
//   renderMobileSessionCards, buildSessionCard, mobileSessionDetail,
//   mobileQuickAddSession,
//   initMobileExport, setMobileLang, doMobileExport, doMobileExportPreview,
//   toggleSettingsGroup, initMobileMore, initMobileView

// =========================================================
// MOBILE NAVIGATION
// =========================================================

// normalizeLocation() is defined in api.js (loaded first).

var _mobileCurrentSection = 'home';
var _mobileLang = 'de';

function mobileNavTo(section) {
  _mobileCurrentSection = section;

  // Alle Mobile-Sections ausblenden
  var sections = ['mobileDashboard','mobileSessionCards','mobileExportFlow','mobileMore','mobileVehicles'];
  sections.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });

  // Aktiven Nav-Button markieren
  document.querySelectorAll('.mobile-nav-btn').forEach(function(b) { b.classList.remove('active'); });

  // Aktiven Bereich anzeigen
  if (section === 'home') {
    var el = document.getElementById('mobileDashboard');
    if (el) el.style.display = 'block';
    document.getElementById('mbnHome')?.classList.add('active');
    refreshMobileDashboard();
  } else if (section === 'sessions') {
    var el = document.getElementById('mobileSessionCards');
    if (el) el.style.display = 'block';
    document.getElementById('mbnSessions')?.classList.add('active');
    loadMobileSessions();
  } else if (section === 'analysis') {
    // Auf Desktop-Analyse-Bereich scrollen oder cfgSection aufrufen
    document.getElementById('mbnAnalysis')?.classList.add('active');
    // Falls ein Analyse-Bereich existiert, dorthin scrollen
    var analysisEl = document.querySelector('[data-section="analysis"]') || document.getElementById('analysisSection');
    if (analysisEl) analysisEl.scrollIntoView({behavior:'smooth'});
    else {
      // Fallback: Desktop-Settings öffnen
      switchToDesktopSettings();
    }
  } else if (section === 'export') {
    var el = document.getElementById('mobileExportFlow');
    if (el) el.style.display = 'block';
    document.getElementById('mbnExport')?.classList.add('active');
    initMobileExport();
  } else if (section === 'more') {
    var el = document.getElementById('mobileMore');
    if (el) el.style.display = 'block';
    document.getElementById('mbnMore')?.classList.add('active');
    initMobileMore();
  } else if (section === 'vehicles') {
    var el = document.getElementById('mobileVehicles');
    if (el) el.style.display = 'block';
    document.getElementById('mbnVehicles')?.classList.add('active');
    if (typeof loadMobileVehicleCards === 'function') loadMobileVehicleCards();
  }

  // Auf Mobile: Desktop-Hauptcontent ausblenden wenn wir eigene Section zeigen
  if (window.innerWidth <= 768) {
    var mainContent = document.getElementById('mainContent') || document.querySelector('.main-content') || document.querySelector('main');
    if (mainContent && section !== 'analysis') {
      mainContent.style.display = (section === 'home' || section === 'sessions' || section === 'export' || section === 'more' || section === 'vehicles') ? 'none' : 'block';
    }
  }
}

function openDesktopConfigSection(sectionId) {
  switchToDesktopSettings();
  if (typeof tab === 'function') tab('config', document.querySelector('nav button:nth-child(2)'));
  setTimeout(function() {
    if (typeof cfgSection === 'function') cfgSection(sectionId);
  }, 50);
}

function switchToDesktopSettings() {
  // Zeige Desktop-Ansicht wieder
  var mainContent = document.getElementById('mainContent') || document.querySelector('.main-content') || document.querySelector('main');
  if (mainContent) mainContent.style.display = '';
  // Blende Mobile-Sections aus
  ['mobileDashboard','mobileSessionCards','mobileExportFlow','mobileMore','mobileVehicles'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
}

// =========================================================
// MOBILE DASHBOARD
// =========================================================

async function refreshMobileDashboard() {
  // Fahrzeugdaten aus dem vorhandenen State lesen
  // (nutzt dieselben Daten wie Desktop, kein doppelter API-Call)
  try {
    // Load summary (includes fresh location detection)
    var summary = null;
    try {
      summary = await apiFetch('/api/mobile/summary', {cache: 'no-store'}).then(function(r) { return r.json(); });
    } catch(_e) { summary = null; }

    // Fahrzeugname/Kennzeichen
    var cfg = window._currentConfig || {};
    var pv = (summary && summary.primary_vehicle) || {};
    var vehicleName = pv.name || document.getElementById('vehicleNameDisplay')?.textContent
      || cfg.vehicle_name || cfg.vehicle_model || '—';
    var plate = cfg.kennzeichen || cfg.vehicle_plate || '—';
    var elName = document.getElementById('mobileVehicleName');
    var elPlate = document.getElementById('mobileVehiclePlate');
    if (elName) elName.textContent = vehicleName;
    if (elPlate) elPlate.textContent = plate;

    // Standort
    var locEl = document.getElementById('mobileVehicleLocation');
    if (locEl) {
      var rawEffLoc = String(pv.effective_location || pv.location_status || '').trim().toLowerCase();
      var effLoc = rawEffLoc === 'disabled' ? 'disabled' :
                  normalizeEffectiveLocation(pv.effective_location, pv.location_status, pv.location);
      locEl.textContent = effLoc === 'home' ? '🏠 Zuhause' :
                          effLoc === 'extern' ? '📍 Extern' :
                          effLoc === 'disabled' ? 'Deaktiviert' :
                          '❓ Unbekannt';
    }

    // SOC
    var soc = (pv.soc != null) ? parseInt(pv.soc) : null;
    if (soc == null) {
      var socEl = document.querySelector('[data-soc]') || document.getElementById('socValue');
      soc = socEl ? parseInt(socEl.textContent) : null;
    }
    if (soc != null && !isNaN(soc)) {
      var bar = document.getElementById('mobileSocBar');
      var pct = document.getElementById('mobileSocPct');
      if (bar) bar.style.width = soc + '%';
      if (pct) pct.textContent = soc + '%';
      if (bar) {
        bar.style.background = soc > 60 ? '#64ffda' : soc > 20 ? '#ffd740' : '#ff5252';
      }
    }

    // Monatsstatistik
    var sessions = window._allSessions || window._sessions || [];
    if (summary && summary.recent_sessions && summary.recent_sessions.length > 0) {
      sessions = summary.recent_sessions;
    }
    var now = new Date();
    var monthSessions = sessions.filter(function(s) {
      var d = new Date(s.start_ts || s.start_time || s.date || '');
      return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
    });
    var totalKwh = monthSessions.reduce(function(a,s) { return a + (parseFloat(s.kwh_charged)||0); }, 0);
    var totalCost = monthSessions.reduce(function(a,s) { return a + (parseFloat(s.cost_eur)||0); }, 0);
    var avgPrice = totalKwh > 0 ? totalCost/totalKwh : 0;

    var kwh = document.getElementById('mobileMonthKwh');
    var cost = document.getElementById('mobileMonthCost');
    var cnt = document.getElementById('mobileMonthSessions');
    var avg = document.getElementById('mobileMonthAvgPrice');
    if (kwh) kwh.textContent = totalKwh.toFixed(1) + ' kWh';
    if (cost) cost.textContent = totalCost.toFixed(2) + ' €';
    if (cnt) cnt.textContent = monthSessions.length;
    if (avg) avg.textContent = avgPrice > 0 ? avgPrice.toFixed(3) + ' €' : '—';

    // Letzte 3 Ladevorgänge
    renderMobileRecentSessions(sessions.slice(0, 3));

    // Missing-charge hint
    try {
      var mc = await apiFetch('/api/missing-charges?status=open').then(function(r){return r.json();}).catch(()=>[]);
      var mcEl = document.getElementById('mobileMissingChargeHint');
      if (mcEl) {
        if (mc && mc.length > 0) {
          var c = mc[0];
          var fmtTs = function(ts){
            if(!ts) return '—';
            return new Date(ts).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
          };
          mcEl.style.display = '';
          mcEl.innerHTML = '<div style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.35);border-radius:10px;padding:12px 14px;margin:10px 0">' +
            '<div style="font-size:.75rem;color:#f59e0b;font-weight:600;margin-bottom:4px">⚠ Fehlender Ladevorgang möglich</div>' +
            '<div style="font-size:.78rem;color:#cdd6f4;margin-bottom:8px">' +
            'SOC ' + (c.soc_start!=null?c.soc_start.toFixed(0):'?') + '% → ' + (c.soc_end!=null?c.soc_end.toFixed(0):'?') + '% · ' +
            'ca. ' + (c.estimated_kwh!=null?c.estimated_kwh.toFixed(1):'?') + ' kWh</div>' +
            '<div style="display:flex;gap:8px">' +
            '<button onclick="mobileMissingChargeAccept(' + c.id + ')" style="flex:1;background:rgba(61,220,151,.15);color:#3ddc97;border:1px solid rgba(61,220,151,.3);border-radius:7px;padding:7px 10px;font-size:.75rem;cursor:pointer">✏ Übernehmen</button>' +
            '<button onclick="mobileMissingChargeDismiss(' + c.id + ')" style="flex:1;background:none;color:#8892b0;border:1px solid #2a3050;border-radius:7px;padding:7px 10px;font-size:.75rem;cursor:pointer">Ignorieren</button>' +
            '</div></div>';
        } else {
          mcEl.style.display = 'none';
          mcEl.innerHTML = '';
        }
      }
    } catch(_mce) {}
  } catch(e) {
    console.warn('Mobile dashboard refresh error:', e);
  }
}

function renderMobileRecentSessions(sessions) {
  var el = document.getElementById('mobileRecentSessions');
  if (!el) return;
  if (!sessions || sessions.length === 0) {
    el.innerHTML = '<p style="color:#8892b0;font-size:13px">Keine Ladevorgänge vorhanden.</p>';
    return;
  }
  el.innerHTML = sessions.map(function(s) { return buildSessionCard(s, true); }).join('');
}

// =========================================================
// MOBILE SESSION CARDS
// =========================================================

function renderMobileSessionCards() {
  var el = document.getElementById('mobileSessionCardList');
  if (!el) return;
  var sessions = window._allSessions || window._sessions || [];
  if (!sessions || sessions.length === 0) {
    el.innerHTML = '<p style="color:#8892b0">Keine Ladevorgänge vorhanden.</p>';
    return;
  }
  el.innerHTML = sessions.slice().reverse().map(function(s) { return buildSessionCard(s, false); }).join('');
}

function buildSessionCard(s, compact) {
  var date = s.date || (s.start_time ? s.start_time.split('T')[0] : (s.start_ts ? s.start_ts.split('T')[0] : '—'));
  var kwh = parseFloat(s.kwh_charged || 0).toFixed(1);
  var cost = parseFloat(s.cost_eur || 0).toFixed(2);
  var loc = s.location || '';
  var isHome = loc === 'home' || loc === '' || loc.toLowerCase().includes('home') || loc.toLowerCase().includes('zuhause');
  var cls = isHome ? 'home' : 'extern';
  var locLabels = {'home':'🏠 Zuhause','extern':'⚡ Extern','unknown':'— Unbekannt'};
  var locLabel = locLabels[loc] || (loc ? escapeHtml(loc) : '');
  var dur = s.duration || '';
  var start = (s.start_time || s.start_ts || '').slice(11,16);
  var sid = s.id || 0;

  var safeLoc = escapeHtml(loc || 'unknown');
  // Use data-loc attribute to avoid quoting hell in onclick
  var changeLocBtn = sid ? '<button data-sid="'+sid+'" data-loc="'+safeLoc+'" onclick="event.stopPropagation();mobileEditLocation(this.dataset.sid,this.dataset.loc)" '
    +'style="background:rgba(61,220,151,.14);color:#3ddc97;border:1px solid rgba(61,220,151,.3);border-radius:6px;padding:4px 10px;font-size:.7rem;cursor:pointer">📍</button>' : '';

  return '<div class="session-card '+cls+'" onclick="mobileSessionDetail('+sid+')">' +
    '<div class="session-card-header">' +
      '<span class="session-card-date">'+escapeHtml(date)+(start ? ' · '+start : '')+'</span>' +
      '<span class="session-card-cost">'+cost+' €</span>' +
    '</div>' +
    '<div class="session-card-details">' +
      '<span>⚡ '+kwh+' kWh</span>' +
      (dur ? '<span>⏱ '+escapeHtml(dur)+'</span>' : '') +
      (locLabel ? '<span>'+locLabel+'</span>' : '') +
      (!compact && sid ? '<span style="margin-left:auto">'+changeLocBtn+'</span>' : '') +
    '</div>' +
  '</div>';
}

async function loadMobileSessions() {
  var sessions = await apiFetch('/api/sessions').then(function(r) { return r.json(); }).catch(function() { return []; });
  window._allSessions = sessions;
  window._sessions = sessions;
  renderMobileSessionCards();
}

async function mobileEditLocation(sessionId, current) {
  if (typeof window.editLocation === 'function') {
    await window.editLocation(sessionId, current);
    await loadMobileSessions();
    if (typeof refreshMobileDashboard === 'function') refreshMobileDashboard();
  }
}

function mobileSessionDetail(sessionId) {
  // Vorhandene Detail-Modal-Funktion nutzen falls vorhanden
  if (typeof openSessionDetail === 'function') {
    openSessionDetail(sessionId);
  } else if (typeof showSessionDetail === 'function') {
    showSessionDetail(sessionId);
  }
}

function mobileQuickAddSession() {
  if (typeof openMobileSessionCreate === 'function') openMobileSessionCreate();
}

// =========================================================
// MOBILE EXPORT
// =========================================================

var _mobileExportLang = 'de';
var _mobileExportToken = null;

function initMobileExport() {
  var now = new Date();
  var monthInput = document.getElementById('mobileExportMonth');
  if (monthInput && !monthInput.value) {
    monthInput.value = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0');
  }
}

function setMobileLang(lang) {
  _mobileExportLang = lang;
  document.getElementById('mobileLangDe').style.background = lang === 'de' ? '#3d5afe' : '#2d3147';
  document.getElementById('mobileLangDe').style.color = lang === 'de' ? '#fff' : '#8892b0';
  document.getElementById('mobileLangEn').style.background = lang === 'en' ? '#3d5afe' : '#2d3147';
  document.getElementById('mobileLangEn').style.color = lang === 'en' ? '#fff' : '#8892b0';
}

async function doMobileExport() {
  var monthVal = document.getElementById('mobileExportMonth')?.value;
  if (!monthVal) { alert('Bitte Monat wählen'); return; }
  var incSig = document.getElementById('mobileIncludeSignature')?.checked || false;

  var resultEl = document.getElementById('mobileExportResult');
  if (resultEl) resultEl.innerHTML = '<p>⏳ Exportiere…</p>';

  try {
    // Token nutzen falls vorhanden, sonst neuen Export
    if (_mobileExportToken) {
      window.location.href = '/api/export/download/'+_mobileExportToken;
      return;
    }
    // Fallback: doExport() nutzen falls vorhanden
    if (typeof doExport === 'function') {
      doExport();
    } else {
      var r = await apiFetch('/api/export', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({month: monthVal, language: _mobileExportLang, include_signature: incSig})
      });
      if (r.ok) {
        var blob = await r.blob();
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url; a.download = 'ladeprotokoll_'+monthVal+'.xlsx'; a.click();
        if (resultEl) resultEl.innerHTML = '<p style="color:#64ffda">✅ Export erfolgreich</p>';
      } else {
        var j = await r.json();
        if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+escapeHtml(j.error||'Fehler')+'</p>';
      }
    }
  } catch(e) {
    if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+escapeHtml(e.message)+'</p>';
  }
}

async function doMobileExportPreview() {
  var monthVal = document.getElementById('mobileExportMonth')?.value;
  if (!monthVal) { alert('Bitte Monat wählen'); return; }
  var parts = monthVal.split('-').map(Number);
  var year = parts[0], month = parts[1];
  var incSig = document.getElementById('mobileIncludeSignature')?.checked || false;
  var resultEl = document.getElementById('mobileExportResult');
  if (resultEl) resultEl.innerHTML = '<p>⏳ Lade Vorschau…</p>';

  try {
    var r = await apiFetch('/api/export/preview', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({month: monthVal, year: year, month_num: month, language: _mobileExportLang, include_signature: incSig})
    }).then(function(x) { return x.json(); });

    if (!r.ok) {
      if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+escapeHtml(r.error||'Fehler')+'</p>';
      return;
    }

    _mobileExportToken = r.download_token || null;

    var html = '';
    if (r.warnings && r.warnings.length > 0) {
      html += '<div style="background:#332;padding:8px;border-radius:6px;margin-bottom:8px;font-size:12px">⚠️ '+r.warnings.map(function(w){return escapeHtml(String(w));}).join(' · ')+'</div>';
    }

    // Erste 10 Datenzeilen aus erstem Sheet zeigen
    var sheet = (r.sheets||[])[0];
    if (sheet && sheet.rows && sheet.rows.length > 0) {
      var rows = typeof sheet.rows[0] === 'object' && sheet.rows[0].cells
        ? sheet.rows[0].cells.map ? sheet.rows.slice(0,10) : []
        : sheet.rows.slice(0,10).map(function(cells,i){return {row:i+1,is_data:true,cells:cells};});

      html += '<div style="overflow-x:auto;margin-bottom:8px"><table style="border-collapse:collapse;font-size:11px;width:100%">';
      rows.forEach(function(row) {
        var cells = row.cells || row;
        var isData = row.is_data !== undefined ? row.is_data : true;
        html += '<tr style="background:'+(isData?'transparent':'#1a2030')+'">';
        cells.slice(0, 6).forEach(function(c) {
          var v = c == null ? '' : String(c);
          html += '<td style="padding:4px 6px;border:1px solid #2d3147;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+escapeHtml(v)+'</td>';
        });
        html += '</tr>';
      });
      html += '</table></div>';
    } else {
      html += '<p style="color:#8892b0;font-size:13px">Keine Vorschau verfügbar.</p>';
    }

    if (_mobileExportToken) {
      html += '<button data-href="/api/export/download/'+encodeURIComponent(_mobileExportToken||'')+'" onclick="window.location.href=this.dataset.href" style="width:100%;padding:12px;border-radius:8px;background:#3d5afe;border:none;color:#fff;font-size:14px;font-weight:600;cursor:pointer;margin-top:8px">📥 Diese Datei herunterladen</button>';
    }

    if (resultEl) resultEl.innerHTML = html;
  } catch(e) {
    if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+escapeHtml(e.message)+'</p>';
  }
}

// =========================================================
// MOBILE MORE / SETTINGS
// =========================================================

function toggleSettingsGroup(header) {
  header.classList.toggle('open');
  var body = header.nextElementSibling;
  if (body) body.classList.toggle('open');
}

function initMobileMore() {
  var groups = {
    mobileGrpVehicles: [
      {label: '🚗 Fahrzeugliste öffnen',   action: function(){ mobileNavTo('vehicles'); }},
      {label: '➕ Fahrzeug hinzufügen',      action: function(){ mobileNavTo('vehicles'); setTimeout(function(){ if (typeof openAddVehicleModal === 'function') openAddVehicleModal(); }, 150); }},
    ],
    mobileGrpProvider: [
      {label: '🔌 Verbindung testen',        action: function(){ openMobileConnectionTest(); }},
      {label: '📊 Zählerstand testen',       action: function(){ openMobileMeterTest(); }},
      {label: '⚙️ Provider konfigurieren',   action: function(){ openDesktopConfigSection('verbindung'); }},
      {label: '🔌 Zähler & Wallbox',         action: function(){ openDesktopConfigSection('zaehler'); }},
    ],
    mobileGrpExport: [
      {label: '📥 Export erstellen',         action: function(){ mobileNavTo('export'); }},
      {label: '📋 Export-Vorlagen',          action: function(){ openDesktopConfigSection('export-tpl'); }},
    ],
    mobileGrpSignature: [
      {label: '✍️ Signatur verwalten',       action: function(){ openMobileSignatureSheet(); }},
      {label: '📋 Signaturposition in Vorlage', action: function(){ openDesktopConfigSection('export-tpl'); }},
    ],
    mobileGrpSecurity: [
      {label: '👤 Konto & Sicherheit',      action: function(){ openDesktopConfigSection('profil'); }},
      {label: '👥 Benutzer',                action: function(){ openDesktopConfigSection('benutzer'); }},
      {label: '🔐 Rollen & Rechte',         action: function(){ openDesktopConfigSection('roles'); }},
    ],
    mobileGrpSystem: [
      {label: '⚙️ System-Status',           action: function(){ openMobileSystemStatus(); }},
      {label: '🔄 Version & Update',        action: function(){ openDesktopConfigSection('version-info'); }},
      {label: '⚙️ Allgemeine Einstellungen', action: function(){ openDesktopConfigSection('fahrzeuge'); }},
    ],
    mobileGrpBackup: [
      {label: '💾 Backup erstellen',        action: function(){ openMobileBackupCreate(); }},
      {label: '📂 Backup & Restore',        action: function(){ switchToDesktopSettings(); if (typeof tab === 'function') tab('backup', document.querySelector('nav button:nth-child(5)')); }},
    ],
  };

  Object.entries(groups).forEach(function(entry) {
    var id = entry[0], items = entry[1];
    var el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = items.map(function(item) {
      return '<button onclick="('+item.action.toString()+')()" style="display:block;width:100%;text-align:left;padding:12px;margin-bottom:4px;border-radius:6px;background:transparent;border:1px solid #2d3147;color:#e6f1ff;font-size:14px;cursor:pointer">'+item.label+'</button>';
    }).join('');
  });
}

// =========================================================
// MOBILE INIT
// =========================================================

function initMobileView() {
  if (window.innerWidth > 768) return; // Nur auf Mobile

  // Home-Section initial anzeigen
  mobileNavTo('home');

  // Auf vorhandene Session-Daten warten und dann aktualisieren
  var checkData = setInterval(function() {
    if (window._allSessions || window._sessions) {
      clearInterval(checkData);
      refreshMobileDashboard();
    }
  }, 500);
  setTimeout(function() { clearInterval(checkData); }, 10000);
}

// Beim Laden initialisieren
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMobileView);
} else {
  initMobileView();
}

// Beim Resize zwischen Mobile und Desktop umschalten
window.addEventListener('resize', function() {
  if (window.innerWidth > 768) {
    // Desktop: alle Mobile-Sections ausblenden, Main-Content zeigen
    ['mobileDashboard','mobileSessionCards','mobileExportFlow','mobileMore','mobileVehicles'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.style.display = 'none';
    });
    var mainContent = document.getElementById('mainContent') || document.querySelector('.main-content') || document.querySelector('main');
    if (mainContent) mainContent.style.display = '';
  } else {
    // Mobile: aktuelle Section anzeigen
    mobileNavTo(_mobileCurrentSection || 'home');
  }
});

// ─── Missing-charge mobile handlers ─────────────────────────────────────────

async function mobileMissingChargeAccept(id) {
  var r = await apiFetch('/api/missing-charges/'+id+'/accept',{method:'POST'}).then(function(x){return x.json();}).catch(function(){return {ok:false};});
  if (!r.ok) { toast('Fehler: '+(r.error||'unbekannt'), 'err'); return; }
  var c = r.prefill;
  // Switch to desktop view and open add-session modal pre-filled
  if (typeof openMobileSessionCreate === 'function') {
    openMobileSessionCreate();
    setTimeout(function(){
      var el = function(id){ return document.getElementById(id); };
      if(el('msStart'))    el('msStart').value    = c.start_ts ? c.start_ts.replace('T',' ').substring(0,16) : '';
      if(el('msEnd'))      el('msEnd').value      = c.end_ts   ? c.end_ts.replace('T',' ').substring(0,16)   : '';
      if(el('msKwh'))      el('msKwh').value      = c.estimated_kwh!=null ? c.estimated_kwh.toFixed(2) : '';
      if(el('msSocStart')) el('msSocStart').value = c.soc_start!=null ? c.soc_start.toFixed(0) : '';
      if(el('msSocEnd'))   el('msSocEnd').value   = c.soc_end!=null   ? c.soc_end.toFixed(0)   : '';
      if(el('msOdoStart')) el('msOdoStart').value = c.odo_start!=null ? Math.round(c.odo_start) : '';
      if(el('msOdoEnd'))   el('msOdoEnd').value   = c.odo_end!=null   ? Math.round(c.odo_end)   : '';
      if(el('msLoc')&&c.suggested_location)         el('msLoc').value  = c.suggested_location;
      if(el('msType')&&c.suggested_charger_type)    el('msType').value = c.suggested_charger_type;
      if(el('msReason'))   el('msReason').value   = 'Offline-Abweichung erkannt';
      if(el('msNote'))     el('msNote').value     = 'Kandidat #'+id+': '+(c.reason||'');
    }, 200);
  } else if (typeof openCandidateAcceptDialog === 'function') {
    // Fall back to desktop dialog via re-accept (already accepted, just prefill)
    openAddSessionModal();
    setTimeout(function(){
      if(typeof $==='function'){
        if($('as_start'))    $('as_start').value    = c.start_ts ? c.start_ts.replace('T',' ').substring(0,16) : '';
        if($('as_end'))      $('as_end').value      = c.end_ts   ? c.end_ts.replace('T',' ').substring(0,16)   : '';
        if($('as_kwh'))      $('as_kwh').value      = c.estimated_kwh!=null ? c.estimated_kwh.toFixed(2) : '';
        if($('as_location')&&c.suggested_location)  $('as_location').value = c.suggested_location;
        if($('as_charger_type')&&c.suggested_charger_type) $('as_charger_type').value = c.suggested_charger_type;
        if($('as_reason'))   $('as_reason').value   = 'Offline-Abweichung erkannt';
        if($('as_note'))     $('as_note').value      = 'Kandidat #'+id+': '+(c.reason||'');
      }
    }, 200);
  }
}

async function mobileMissingChargeDismiss(id) {
  var r = await apiFetch('/api/missing-charges/'+id+'/dismiss',{method:'POST'}).then(function(x){return x.json();}).catch(function(){return {ok:false};});
  if (r.ok) {
    toast('Vorschlag ignoriert');
    if (typeof loadMobileSessions === 'function') await loadMobileSessions();
    if (typeof refreshMobileDashboard === 'function') refreshMobileDashboard();
  } else {
    toast('Fehler: '+(r.error||'unbekannt'), 'err');
  }
}
