// sessions.js — provides:
//   loadSessions, editCost, editLocation, delSession,
//   showSessionDetail, closeModal,
//   openAddSessionModal, closeAddSessionModal, submitAddSession,
//   loadCharts, destroyCharts, chartDefaults

async function editCost(id, kwh, currentPrice){
  var newPrice = prompt(
    'Session #'+id+' — '+fmt(kwh,2)+' kWh\n\nNeuen Preis pro kWh eingeben (€):\n(aktuell: '+fmt(currentPrice,4)+' €/kWh)\n\nOder leer lassen um Gesamtbetrag direkt einzugeben:',
    fmt(currentPrice,4)
  );
  if(newPrice===null) return;

  var body;
  if(newPrice.trim()===''){
    var totalCost=prompt('Gesamtkosten für Session #'+id+' ('+fmt(kwh,2)+' kWh) in €:');
    if(!totalCost||isNaN(parseFloat(totalCost))) return;
    body={cost_eur:parseFloat(totalCost)};
  } else {
    if(isNaN(parseFloat(newPrice))) return;
    body={price_per_kwh:parseFloat(newPrice),cost_eur:0};
  }

  var r=await apiFetch('/api/sessions/'+encodeURIComponent(id)+'/cost',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json();}).catch(function(){return {ok:false};});
  if(r.ok){ toast('Session #'+id+' Kosten aktualisiert: '+fmt(r.cost_eur,2)+' €'); loadSessions(); }
  else toast('Fehler beim Speichern','err');
}

async function loadSessions(){
  var loc = $('sFilter')?.value||'all';
  var vid = $('sVehicleFilter')?.value||'all';
  var params = new URLSearchParams({location:loc});
  if(vid && vid!=='all') params.set('vehicle_id',vid);
  var rows = await apiFetch('/api/sessions?'+params).then(function(r){return r.json();}).catch(function(){return [];});
  renderTbl($('allTbl'), rows, true);
}

async function editLocation(id, current){
  var labels = {'home':'🏠 Zuhause','extern':'⚡ Extern','unknown':'— Unbekannt'};
  var choice = prompt(
    'Standort für Session #'+id+' ändern:\n\n1 = 🏠 Zuhause\n2 = ⚡ Extern\n3 = — Unbekannt\n\nAktuell: '+(labels[current]||current)+'\n\nZahl eingeben:'
  );
  if(!choice) return;
  var map={'1':'home','2':'extern','3':'unknown'};
  var loc=map[choice.trim()];
  if(!loc){toast('Ungültige Eingabe — 1, 2 oder 3','err');return;}
  var r=await apiFetch('/api/sessions/'+encodeURIComponent(id)+'/location',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({location:loc})}).then(function(r){return r.json();}).catch(function(){return {ok:false};});
  if(r.ok){toast('Session #'+id+' → '+labels[loc]);loadSessions();}
  else toast('Fehler beim Speichern','err');
}

async function delSession(id){
  if(!confirm('Session #'+id+' wirklich löschen?')) return;
  var r=await apiFetch('/api/sessions/'+encodeURIComponent(id),{method:'DELETE'}).then(function(r){return r.json();}).catch(function(){return {ok:false};});
  if(r.ok){ toast('Session #'+id+' gelöscht'); loadSessions(); loadCharts(); }
  else toast((r.error||'Fehler beim Löschen'),'err');
}

// ── Manual Add Modal ─────────────────────────────────────────────────────────

var _addSessionOverlapData = null;

async function openAddSessionModal() {
  var vehicles = [];
  try {
    vehicles = await apiFetch('/api/vehicles').then(function(r){return r.json();});
  } catch(_e){}

  var now  = new Date();
  var iso  = function(d){ return new Date(d - d.getTimezoneOffset()*60000).toISOString().slice(0,16); };
  var vOpts = vehicles.length
    ? vehicles.map(function(v){return '<option value="'+v.id+'">'+(v.name||v.id)+'</option>';}).join('')
    : '<option value="v0">Fahrzeug v0</option>';

  // Pre-fill location from current vehicle state if available
  var defaultLoc = 'home';
  try {
    var s = await apiFetch('/api/status').then(function(r){return r.json();});
    var sl = normalizeLocation(s.effective_location || s.location_status || s.location || '');
    if (sl === 'home' || sl === 'extern') defaultLoc = sl;
  } catch(_e){}

  var modal = $('addSessionModal');
  if (!modal) return;

  $('as_vehicle').innerHTML = vOpts;
  $('as_start').value = iso(now);
  $('as_end').value   = '';
  $('as_kwh').value   = '';
  $('as_price').value = '';
  $('as_cost').value  = '';
  $('as_soc_start').value = '';
  $('as_soc_end').value   = '';
  $('as_odo_start').value = '';
  $('as_odo_end').value   = '';
  $('as_meter_old').value = '';
  $('as_meter_new').value = '';
  $('as_charger_power').value = '';
  $('as_max_power').value     = '';
  $('as_location').value  = defaultLoc;
  $('as_charger_type').value  = 'unknown';
  $('as_reason').value    = '';
  $('as_note').value      = '';
  $('as_avg_power').textContent = '';
  $('as_result').innerHTML = '';
  $('as_overlap_row').style.display = 'none';
  _addSessionOverlapData = null;

  modal.style.display = 'flex';
  $('as_start').focus();
}

function closeAddSessionModal() {
  var modal = $('addSessionModal');
  if (modal) modal.style.display = 'none';
  _addSessionOverlapData = null;
}

function _asRecalc() {
  // kWh from meter readings
  var mOld = parseFloat($('as_meter_old')?.value);
  var mNew = parseFloat($('as_meter_new')?.value);
  if (!isNaN(mOld) && !isNaN(mNew) && mNew >= mOld) {
    var mKwh = (mNew - mOld).toFixed(3);
    if (!$('as_kwh').value) $('as_kwh').value = mKwh;
  }
  // Cost from kWh * price
  var kwh   = parseFloat($('as_kwh')?.value);
  var price = parseFloat($('as_price')?.value);
  if (!isNaN(kwh) && !isNaN(price) && !$('as_cost').value) {
    $('as_cost').value = (kwh * price).toFixed(2);
  }
  // Price from cost / kWh
  var cost = parseFloat($('as_cost')?.value);
  if (!isNaN(kwh) && !isNaN(cost) && kwh > 0 && !$('as_price').value) {
    $('as_price').value = (cost / kwh).toFixed(4);
  }
  // Avg power display
  var start = $('as_start')?.value;
  var end   = $('as_end')?.value;
  var avgEl = $('as_avg_power');
  if (avgEl && start && end && !isNaN(kwh) && kwh > 0) {
    var diffH = (new Date(end) - new Date(start)) / 3600000;
    if (diffH > 0) {
      avgEl.textContent = 'Ø ' + (kwh / diffH).toFixed(1) + ' kW';
    } else {
      avgEl.textContent = '';
    }
  }
}

function _asClearCost() { $('as_cost').value = ''; _asRecalc(); }
function _asClearPrice() { $('as_price').value = ''; _asRecalc(); }
function _asClearKwh() {
  // Only clear kWh auto-fill if it came from meter, not typed
  var mOld = parseFloat($('as_meter_old')?.value);
  var mNew = parseFloat($('as_meter_new')?.value);
  if (!isNaN(mOld) && !isNaN(mNew) && mNew >= mOld) {
    var mKwh = (mNew - mOld).toFixed(3);
    if ($('as_kwh').value === mKwh) $('as_kwh').value = '';
  }
  _asRecalc();
}

async function submitAddSession(force) {
  force = !!force;
  var res = $('as_result');
  res.innerHTML = '';
  $('as_overlap_row').style.display = 'none';
  _addSessionOverlapData = null;

  var start_ts = $('as_start')?.value;
  var end_ts   = $('as_end')?.value || null;
  var kwh      = parseFloat($('as_kwh')?.value);
  var price    = parseFloat($('as_price')?.value);
  var cost     = parseFloat($('as_cost')?.value);
  var soc_s    = parseFloat($('as_soc_start')?.value);
  var soc_e    = parseFloat($('as_soc_end')?.value);
  var odo_s    = parseFloat($('as_odo_start')?.value);
  var odo_e    = parseFloat($('as_odo_end')?.value);
  var m_old    = parseFloat($('as_meter_old')?.value);
  var m_new    = parseFloat($('as_meter_new')?.value);
  var cpwr     = parseFloat($('as_charger_power')?.value);
  var mpwr     = parseFloat($('as_max_power')?.value);

  if (!start_ts) {
    res.innerHTML = '<p class="as-err">⚠ Startzeit ist erforderlich.</p>';
    return;
  }
  if (isNaN(kwh) && (isNaN(m_old) || isNaN(m_new))) {
    res.innerHTML = '<p class="as-err">⚠ Bitte kWh oder Zählerstände eingeben.</p>';
    return;
  }

  var body = {
    vehicle_id:       $('as_vehicle')?.value || 'v0',
    start_ts,
    end_ts,
    location:         $('as_location')?.value || 'home',
    charger_type:     $('as_charger_type')?.value || 'unknown',
    manual_reason:    $('as_reason')?.value || null,
    manual_note:      $('as_note')?.value || null,
    force,
  };
  if (!isNaN(kwh))   body.kwh_charged     = kwh;
  if (!isNaN(price)) body.price_per_kwh   = price;
  if (!isNaN(cost))  body.cost_eur        = cost;
  if (!isNaN(soc_s)) body.soc_start       = soc_s;
  if (!isNaN(soc_e)) body.soc_end         = soc_e;
  if (!isNaN(odo_s)) body.odo_start       = odo_s;
  if (!isNaN(odo_e)) body.odo_end         = odo_e;
  if (!isNaN(m_old)) body.meter_old       = m_old;
  if (!isNaN(m_new)) body.meter_new       = m_new;
  if (!isNaN(cpwr))  body.charger_power_kw= cpwr;
  if (!isNaN(mpwr))  body.max_power_kw    = mpwr;

  res.innerHTML = '<p style="color:var(--mute)">⏳ Speichere…</p>';

  var r = await apiFetch('/api/sessions/manual', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  }).then(function(x){return x.json();}).catch(function(e){return {ok:false,error:e.message};});

  if (r.ok) {
    closeAddSessionModal();
    toast('✅ Session #' + r.id + ' gespeichert');
    loadSessions();
    loadCharts();
    if (typeof refreshStatus === 'function') refreshStatus();
  } else if (r.warning === 'overlap') {
    _addSessionOverlapData = r.overlapping_sessions;
    res.innerHTML = '<p class="as-err">⚠ ' + escapeHtml(r.message || 'Überschneidung mit bestehender Session.') + '</p>';
    $('as_overlap_row').style.display = 'flex';
  } else {
    res.innerHTML = '<p class="as-err">❌ ' + escapeHtml(r.error || 'Unbekannter Fehler') + '</p>';
  }
}

// ── Session Detail Modal ─────────────────────────────────────────────────────
var _modalChart = null;

async function showSessionDetail(id){
  var rows = await apiFetch('/api/sessions').then(function(r){return r.json();}).catch(function(){return [];});
  var s = rows.find(function(r){return r.id===id;});
  if(!s) return;

  $('sessionModal').style.display='flex';

  var dt = function(d){ return new Date(d).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}); };
  var isManual = s.provider === 'manual' || s.created_mode === 'manual';
  var manualBadge = isManual ? ' <span style="background:rgba(100,200,255,.15);color:#64c8ff;border:1px solid rgba(100,200,255,.3);border-radius:4px;padding:1px 6px;font-size:.65rem;font-family:var(--mono)">✏ Manuell</span>' : '';
  $('modalTitle').innerHTML = 'Session #'+s.id+' — '+new Date(s.start_ts).toLocaleDateString('de-DE') + manualBadge;
  $('modalMeta').innerHTML = dt(s.start_ts)+' → '+(s.end_ts?dt(s.end_ts):'läuft noch')+' &nbsp;·&nbsp; '+locBadge(s.location)+' &nbsp;·&nbsp; '+typeBadge(s.charger_type,s.max_power_kw);

  var fmtMeterVal = function(v){ return v!=null ? Number(v).toLocaleString('de',{minimumFractionDigits:3,maximumFractionDigits:3})+' kWh' : null; };
  var stats = [
    {l:'SOC Start',      v:fmt(s.soc_start,0)+'%'},
    {l:'SOC Ende',       v:fmt(s.soc_end,0)+'%'},
    {l:'Geladen',        v:fmt(s.kwh_charged)+' kWh'},
    {l:'KM Start',       v:s.odo_start?Math.round(s.odo_start).toLocaleString('de')+' km':'—'},
    {l:'KM Ende',        v:s.odo_end?Math.round(s.odo_end).toLocaleString('de')+' km':'—'},
    {l:'Preis/kWh',      v:s.price_per_kwh?fmt(s.price_per_kwh,4)+' €':'—'},
    {l:'Kosten',         v:s.cost_eur!=null?fmt(s.cost_eur)+' €':'—'},
    {l:'Max. Leistung',  v:s.max_power_kw?Number(s.max_power_kw).toFixed(1)+' kW':'—'},
  ];
  if(s.meter_old!=null||s.meter_new!=null){
    stats.push({l:'Zähler Alt', v:fmtMeterVal(s.meter_old)||'—', c:'#a78bfa'});
    stats.push({l:'Zähler Neu', v:fmtMeterVal(s.meter_new)||'—', c:'#a78bfa'});
  }
  if(s.location_source){
    var locSrcLabels = {
      'meter_delta': '📊 Zähler-Delta',
      'meter_conflict': '⚠ Zähler-Konflikt',
      'provider': '📡 Provider',
      'ha': '🏠 Home Assistant',
      'gps': '📍 GPS',
      'manual': '✏️ Manuell',
      'unknown': '— Unbekannt',
    };
    var locSrcLabel = locSrcLabels[s.location_source] || s.location_source;
    stats.push({l:'Standortquelle', v:locSrcLabel, c: s.location_source==='meter_delta'?'#34d399': s.location_source==='meter_conflict'?'#f59e0b':null});
    if(s.location_source==='meter_delta'&&s.meter_home_detection_delta_kwh!=null){
      stats.push({l:'Zähler-Delta (Erkennung)', v:Number(s.meter_home_detection_delta_kwh).toFixed(3)+' kWh', c:'#34d399'});
    }
    if(s.location_confidence!=null&&s.location_confidence>0){
      stats.push({l:'Standort-Konfidenz', v:s.location_confidence+'%'});
    }
  }

  // Public charging price info (extern sessions)
  if (s.location === 'extern' && (s.price_source || s.charging_contract_name)) {
    var priceSrcLabels = {
      'enbw_live':  '🔴 EnBW Live',
      'contract':   '📄 Ladeabo',
      'fallback':   '⚙️ Fallback',
      'config':     '⚙️ Konfiguration',
      'manual':     '✏️ Manuell',
    };
    var pSrc = s.price_source ? (priceSrcLabels[s.price_source] || s.price_source) : null;
    if (pSrc) stats.push({l:'Preisquelle', v:pSrc, c: s.price_source==='enbw_live'?'#f87171': s.price_source==='contract'?'#60a5fa':'#94a3b8'});
    if (s.charging_contract_name) stats.push({l:'Ladeabo', v:escapeHtml(s.charging_contract_name), c:'#60a5fa'});
    if (s.price_confidence != null && s.price_confidence > 0) stats.push({l:'Preis-Konfidenz', v:s.price_confidence+'%'});
  }

  // Manual session specific fields
  if (isManual) {
    stats.push({l:'Quelle', v:'✏️ Manuell', c:'#64c8ff'});
    var kwhSrcLabels = {manual:'Manuell eingegeben', meter:'Zähler', soc:'SOC-Berechnung'};
    if (s.kwh_source) stats.push({l:'kWh-Quelle', v:kwhSrcLabels[s.kwh_source]||s.kwh_source});
    if (s.cost_manual) stats.push({l:'Kosten manuell', v:'Ja'});
    if (s.manual_reason) stats.push({l:'Grund', v:escapeHtml(s.manual_reason), c:'#f59e0b'});
    if (s.manual_note)   stats.push({l:'Notiz', v:escapeHtml(s.manual_note)});
  }

  $('modalStats').innerHTML = stats.map(function(x){return '<div class="stat"><div class="sl">'+x.l+'</div><div class="sv" style="font-size:1.1rem'+(x.c?';color:'+x.c:'')+'">'+x.v+'</div></div>';}).join('');

  var pts = await apiFetch('/api/sessions/'+id+'/points').then(function(r){return r.json();}).catch(function(){return [];});

  if(_modalChart){ _modalChart.destroy(); _modalChart=null; }

  if(!pts.length){
    $('modalChart').style.display='none';
    $('modalNoData').style.display='block';
    return;
  }
  $('modalChart').style.display='';
  $('modalNoData').style.display='none';

  var labels  = pts.map(function(p){return new Date(p.ts).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'});});
  var socData = pts.map(function(p){return p.soc;});
  var pwrData = pts.map(function(p){return p.power_kw;});

  _modalChart = new Chart($('modalChart'),{
    type:'line',
    data:{
      labels:labels,
      datasets:[
        {label:'SOC (%)',data:socData,borderColor:'#3ddc97',backgroundColor:'rgba(61,220,151,.08)',fill:true,tension:.3,pointRadius:2,yAxisID:'ySoc'},
        {label:'Leistung (kW)',data:pwrData,borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,.08)',fill:true,tension:.3,pointRadius:2,yAxisID:'yPwr'}
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{
        legend:{display:true,labels:{color:'#4a5c72',font:{family:'DM Mono',size:9}}},
        tooltip:{callbacks:{label:function(c){return c.dataset.label+': '+Number(c.raw||0).toFixed(1)+(c.dataset.yAxisID==='ySoc'?'%':' kW');}}}
      },
      scales:{
        x:{grid:{color:'#1c2430'},ticks:{color:'#4a5c72',font:{family:'DM Mono',size:9},maxTicksLimit:12}},
        ySoc:{position:'left',min:0,max:100,grid:{color:'#1c2430'},ticks:{color:'#3ddc97',font:{family:'DM Mono',size:9},callback:function(v){return v+'%';}}},
        yPwr:{position:'right',min:0,grid:{drawOnChartArea:false},ticks:{color:'#f59e0b',font:{family:'DM Mono',size:9},callback:function(v){return v+' kW';}}},
      }
    }
  });
}

function closeModal(){
  $('sessionModal').style.display='none';
  if(_modalChart){_modalChart.destroy();_modalChart=null;}
}

// ── Charts ────────────────────────────────────────────────────────────────────
var charts = {};

function destroyCharts(){
  Object.values(charts).forEach(function(c){if(c)c.destroy();});
  charts={};
}

function chartDefaults(){
  return {
    responsive:true, maintainAspectRatio:false,
    plugins:{legend:{display:false}},
    scales:{
      x:{grid:{color:'#1c2430'},ticks:{color:'#4a5c72',font:{family:'DM Mono',size:10}}},
      y:{grid:{color:'#1c2430'},ticks:{color:'#4a5c72',font:{family:'DM Mono',size:10}}}
    }
  };
}

async function loadCharts(){
  var monthly = await apiFetch('/api/stats/monthly').then(function(r){return r.json();}).catch(function(){return [];});
  var sessions= await apiFetch('/api/sessions').then(function(r){return r.json();}).catch(function(){return [];});
  destroyCharts();

  var months  = monthly.map(function(m){return m.month;}).reverse();
  var costs   = monthly.map(function(m){return +(m.total_cost||0).toFixed(2);}).reverse();
  var kwhs    = monthly.map(function(m){return +(m.total_kwh||0).toFixed(2);}).reverse();

  charts.cost = new Chart($('chartCost'), {
    type:'bar',
    data:{labels:months, datasets:[{data:costs,backgroundColor:'rgba(245,158,11,.35)',borderColor:'#f59e0b',borderWidth:1.5,borderRadius:4}]},
    options:Object.assign({}, chartDefaults(), {plugins:Object.assign({}, chartDefaults().plugins,{tooltip:{callbacks:{label:function(c){return c.raw+' €';}}}})}),
  });

  charts.kwh = new Chart($('chartKwh'), {
    type:'bar',
    data:{labels:months, datasets:[{data:kwhs,backgroundColor:'rgba(0,180,255,.25)',borderColor:'#00b4ff',borderWidth:1.5,borderRadius:4}]},
    options:Object.assign({}, chartDefaults(), {plugins:Object.assign({}, chartDefaults().plugins,{tooltip:{callbacks:{label:function(c){return c.raw+' kWh';}}}})}),
  });

  var last10 = sessions.slice(0,10).reverse();
  var socStart= last10.map(function(s){return s.soc_start||0;});
  var socEnd  = last10.map(function(s){return s.soc_end||0;});
  var socLabels=last10.map(function(s){return new Date(s.start_ts).toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'});});
  charts.soc = new Chart($('chartSoc'), {
    type:'line',
    data:{labels:socLabels, datasets:[
      {label:'SOC Start',data:socStart,borderColor:'#4a5c72',backgroundColor:'rgba(74,92,114,.1)',fill:true,tension:.3,pointRadius:3},
      {label:'SOC Ende', data:socEnd,  borderColor:'#3ddc97',backgroundColor:'rgba(61,220,151,.1)',fill:true,tension:.3,pointRadius:3},
    ]},
    options:Object.assign({}, chartDefaults(), {
      plugins:{legend:{display:true,labels:{color:'#4a5c72',font:{family:'DM Mono',size:9}}}},
      scales:Object.assign({}, chartDefaults().scales, {y:Object.assign({}, chartDefaults().scales.y,{min:0,max:100,ticks:Object.assign({}, chartDefaults().scales.y.ticks, {callback:function(v){return v+'%';}})})}),
    }),
  });

  var eff = monthly.map(function(m){
    if(!m.km_driven||m.km_driven<=0) return null;
    return +(m.total_kwh/m.km_driven*100).toFixed(1);
  }).reverse();
  charts.km = new Chart($('chartKm'), {
    type:'line',
    data:{labels:months, datasets:[{data:eff,borderColor:'#3ddc97',backgroundColor:'rgba(61,220,151,.08)',fill:true,tension:.3,pointRadius:4,spanGaps:true}]},
    options:Object.assign({}, chartDefaults(), {plugins:Object.assign({}, chartDefaults().plugins,{tooltip:{callbacks:{label:function(c){return c.raw+' kWh/100km';}}}})}),
  });
}
