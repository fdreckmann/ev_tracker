// mobile.js — provides:
// normalizeLocation (also defined in status.js for desktop)
//   mobileNavTo, switchToDesktopSettings,
//   refreshMobileDashboard, renderMobileRecentSessions,
//   renderMobileSessionCards, buildSessionCard, mobileSessionDetail,
//   mobileQuickAddSession,
//   initMobileExport, setMobileLang, doMobileExport, doMobileExportPreview,
//   toggleSettingsGroup, initMobileMore, initMobileView

// =========================================================
// MOBILE NAVIGATION
// =========================================================

function normalizeLocation(val) {
  if (!val) return 'unknown';
  const v = String(val).trim().toLowerCase();
  if (['unknown','unavailable','disabled','none','n/a','null','offline',''].includes(v)) return 'unknown';
  if (['home','zuhause','at_home','home_charging','garage','local'].includes(v)) return 'home';
  if (['extern','external','not_home','away','unterwegs','extern_charging',
       'outside','remote','roaming','public','charging_away','travel'].includes(v)) return 'extern';
  return 'unknown';
}

var _mobileCurrentSection = 'home';
var _mobileLang = 'de';

function mobileNavTo(section) {
  _mobileCurrentSection = section;

  // Alle Mobile-Sections ausblenden
  var sections = ['mobileDashboard','mobileSessionCards','mobileExportFlow','mobileMore'];
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
    renderMobileSessionCards();
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
  }

  // Auf Mobile: Desktop-Hauptcontent ausblenden wenn wir eigene Section zeigen
  if (window.innerWidth <= 768) {
    var mainContent = document.getElementById('mainContent') || document.querySelector('.main-content') || document.querySelector('main');
    if (mainContent && section !== 'analysis') {
      mainContent.style.display = (section === 'home' || section === 'sessions' || section === 'export' || section === 'more') ? 'none' : 'block';
    }
  }
}

function switchToDesktopSettings() {
  // Zeige Desktop-Ansicht wieder
  var mainContent = document.getElementById('mainContent') || document.querySelector('.main-content') || document.querySelector('main');
  if (mainContent) mainContent.style.display = '';
  // Blende Mobile-Sections aus
  ['mobileDashboard','mobileSessionCards','mobileExportFlow','mobileMore'].forEach(function(id) {
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
    // Fahrzeugname/Kennzeichen
    var cfg = window._currentConfig || {};
    var vehicleName = document.getElementById('vehicleNameDisplay')?.textContent
      || cfg.vehicle_name || cfg.vehicle_model || '—';
    var plate = cfg.kennzeichen || cfg.vehicle_plate || '—';
    var elName = document.getElementById('mobileVehicleName');
    var elPlate = document.getElementById('mobileVehiclePlate');
    if (elName) elName.textContent = vehicleName;
    if (elPlate) elPlate.textContent = plate;

    // SOC aus bestehendem State
    var socEl = document.querySelector('[data-soc]') || document.getElementById('socValue');
    var soc = socEl ? parseInt(socEl.textContent) : null;
    if (soc != null && !isNaN(soc)) {
      var bar = document.getElementById('mobileSocBar');
      var pct = document.getElementById('mobileSocPct');
      if (bar) bar.style.width = soc + '%';
      if (pct) pct.textContent = soc + '%';
      if (bar) {
        bar.style.background = soc > 60 ? '#64ffda' : soc > 20 ? '#ffd740' : '#ff5252';
      }
    }

    // Monatsstatistik aus bestehendem State lesen
    var sessions = window._allSessions || window._sessions || [];
    var now = new Date();
    var monthSessions = sessions.filter(function(s) {
      var d = new Date(s.start_time || s.date || '');
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
    renderMobileRecentSessions(sessions.slice(-3).reverse());
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
  var date = s.date || (s.start_time ? s.start_time.split('T')[0] : '—');
  var kwh = parseFloat(s.kwh_charged || 0).toFixed(1);
  var cost = parseFloat(s.cost_eur || 0).toFixed(2);
  var loc = s.location || '';
  var isHome = loc.toLowerCase().includes('home') || loc.toLowerCase().includes('zuhause') || loc === '';
  var cls = isHome ? 'home' : 'extern';
  var dur = s.duration || '';
  var start = s.start_time ? s.start_time.slice(11,16) : '';

  return '<div class="session-card '+cls+'" onclick="mobileSessionDetail('+(s.id||0)+')">' +
    '<div class="session-card-header">' +
      '<span class="session-card-date">'+date+(start ? ' · '+start : '')+'</span>' +
      '<span class="session-card-cost">'+cost+' €</span>' +
    '</div>' +
    '<div class="session-card-details">' +
      '<span>⚡ '+kwh+' kWh</span>' +
      (dur ? '<span>⏱ '+dur+'</span>' : '') +
      (loc ? '<span>📍 '+loc+'</span>' : '') +
    '</div>' +
  '</div>';
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
  // Vorhandene Funktion zum Hinzufügen nutzen
  if (typeof openAddSessionModal === 'function') openAddSessionModal();
  else if (typeof addSession === 'function') addSession();
  else {
    // Fallback: Desktop-Ansicht zeigen
    switchToDesktopSettings();
  }
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
      var r = await fetch('/api/export', {
        method: 'POST',
        headers: {'Content-Type':'application/json','X-CSRFToken': window._csrfToken||''},
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
        if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+(j.error||'Fehler')+'</p>';
      }
    }
  } catch(e) {
    if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+e.message+'</p>';
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
    var r = await fetch('/api/export/preview', {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-CSRFToken':window._csrfToken||''},
      body: JSON.stringify({month: monthVal, year: year, month_num: month, language: _mobileExportLang, include_signature: incSig})
    }).then(function(x) { return x.json(); });

    if (!r.ok) {
      if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+(r.error||'Fehler')+'</p>';
      return;
    }

    _mobileExportToken = r.download_token || null;

    var html = '';
    if (r.warnings && r.warnings.length > 0) {
      html += '<div style="background:#332;padding:8px;border-radius:6px;margin-bottom:8px;font-size:12px">⚠️ '+r.warnings.join(' · ')+'</div>';
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
          html += '<td style="padding:4px 6px;border:1px solid #2d3147;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+v.replace(/</g,'&lt;')+'</td>';
        });
        html += '</tr>';
      });
      html += '</table></div>';
    } else {
      html += '<p style="color:#8892b0;font-size:13px">Keine Vorschau verfügbar.</p>';
    }

    if (_mobileExportToken) {
      html += '<button onclick="window.location.href=\'/api/export/download/'+_mobileExportToken+'\'" style="width:100%;padding:12px;border-radius:8px;background:#3d5afe;border:none;color:#fff;font-size:14px;font-weight:600;cursor:pointer;margin-top:8px">📥 Diese Datei herunterladen</button>';
    }

    if (resultEl) resultEl.innerHTML = html;
  } catch(e) {
    if (resultEl) resultEl.innerHTML = '<p style="color:#ff5252">❌ '+e.message+'</p>';
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
  // Einfache Links zu den jeweiligen Desktop-Einstellungs-Bereichen
  var groups = {
    mobileGrpVehicles: [
      {label: '🚗 Fahrzeuge verwalten', action: function(){ switchToDesktopSettings(); typeof cfgSection === 'function' && cfgSection('fahrzeuge'); }},
    ],
    mobileGrpProvider: [
      {label: '🔌 Provider konfigurieren', action: function(){ switchToDesktopSettings(); typeof cfgSection === 'function' && cfgSection('verbindung'); }},
      {label: '📊 Zählerstand testen', action: function(){ switchToDesktopSettings(); typeof cfgSection === 'function' && cfgSection('zaehler'); }},
    ],
    mobileGrpExport: [
      {label: '📥 Export-Einstellungen', action: function(){ switchToDesktopSettings(); typeof cfgSection === 'function' && cfgSection('export-tpl'); }},
    ],
    mobileGrpSignature: [
      {label: '✍️ Unterschrift hochladen', action: function(){ switchToDesktopSettings(); typeof cfgSection === 'function' && cfgSection('auth'); }},
    ],
    mobileGrpSecurity: [
      {label: '👤 Benutzer & Sicherheit', action: function(){ switchToDesktopSettings(); typeof cfgSection === 'function' && cfgSection('auth'); }},
    ],
    mobileGrpSystem: [
      {label: '⚙️ Allgemeine Einstellungen', action: function(){ switchToDesktopSettings(); }},
      {label: '🔄 Updates & Version', action: function(){ switchToDesktopSettings(); document.getElementById('updateSection')?.scrollIntoView({behavior:'smooth'}); }},
    ],
    mobileGrpBackup: [
      {label: '💾 Backup & Restore', action: function(){ switchToDesktopSettings(); tab('backup', document.querySelector('nav button:nth-child(5)')); }},
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
    ['mobileDashboard','mobileSessionCards','mobileExportFlow','mobileMore'].forEach(function(id) {
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
