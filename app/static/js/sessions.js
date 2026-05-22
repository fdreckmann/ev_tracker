// sessions.js — provides:
//   loadSessions, editCost, editLocation, delSession,
//   showSessionDetail, closeModal,
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

  var r=await fetch('/api/sessions/'+id+'/cost',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(function(r){return r.json();});
  if(r.ok){ toast('Session #'+id+' Kosten aktualisiert: '+fmt(r.cost_eur,2)+' €'); loadSessions(); }
  else toast('Fehler','err');
}

async function loadSessions(){
  var loc = $('sFilter')?.value||'all';
  var vid = $('sVehicleFilter')?.value||'all';
  var params = new URLSearchParams({location:loc});
  if(vid && vid!=='all') params.set('vehicle_id',vid);
  var rows = await fetch('/api/sessions?'+params).then(function(r){return r.json();});
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
  var r=await fetch('/api/sessions/'+id+'/location',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({location:loc})}).then(function(r){return r.json();});
  if(r.ok){toast('Session #'+id+' → '+labels[loc]);loadSessions();}
  else toast('Fehler','err');
}

async function delSession(id){
  if(!confirm('Session #'+id+' wirklich löschen?')) return;
  await fetch('/api/sessions/'+id, {method:'DELETE'});
  toast('Session #'+id+' gelöscht');
  loadSessions(); loadCharts();
}

// ── Session Detail Modal ─────────────────────────────────────────────────────
var _modalChart = null;

async function showSessionDetail(id){
  // fetch session
  var rows = await fetch('/api/sessions').then(function(r){return r.json();});
  var s = rows.find(function(r){return r.id===id;});
  if(!s) return;

  $('sessionModal').style.display='flex';

  var dt = function(d){ return new Date(d).toLocaleString('de-DE',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}); };
  $('modalTitle').textContent = 'Session #'+s.id+' — '+new Date(s.start_ts).toLocaleDateString('de-DE');
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
  $('modalStats').innerHTML = stats.map(function(x){return '<div class="stat"><div class="sl">'+x.l+'</div><div class="sv" style="font-size:1.1rem'+(x.c?';color:'+x.c:'')+'">'+x.v+'</div></div>';}).join('');

  // fetch charge curve points
  var pts = await fetch('/api/sessions/'+id+'/points').then(function(r){return r.json();});

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
        {
          label:'SOC (%)',
          data:socData,
          borderColor:'#3ddc97',
          backgroundColor:'rgba(61,220,151,.08)',
          fill:true,tension:.3,pointRadius:2,
          yAxisID:'ySoc',
        },
        {
          label:'Leistung (kW)',
          data:pwrData,
          borderColor:'#f59e0b',
          backgroundColor:'rgba(245,158,11,.08)',
          fill:true,tension:.3,pointRadius:2,
          yAxisID:'yPwr',
        }
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{
        legend:{display:true,labels:{color:'#4a5c72',font:{family:'DM Mono',size:9}}},
        tooltip:{callbacks:{
          label:function(c){return c.dataset.label+': '+Number(c.raw||0).toFixed(1)+(c.dataset.yAxisID==='ySoc'?'%':' kW');}
        }}
      },
      scales:{
        x:{grid:{color:'#1c2430'},ticks:{color:'#4a5c72',font:{family:'DM Mono',size:9},maxTicksLimit:12}},
        ySoc:{position:'left',min:0,max:100,
          grid:{color:'#1c2430'},
          ticks:{color:'#3ddc97',font:{family:'DM Mono',size:9},callback:function(v){return v+'%';}}},
        yPwr:{position:'right',min:0,
          grid:{drawOnChartArea:false},
          ticks:{color:'#f59e0b',font:{family:'DM Mono',size:9},callback:function(v){return v+' kW';}}},
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
  var monthly = await fetch('/api/stats/monthly').then(function(r){return r.json();});
  var sessions= await fetch('/api/sessions').then(function(r){return r.json();});
  destroyCharts();

  var months  = monthly.map(function(m){return m.month;}).reverse();
  var costs   = monthly.map(function(m){return +(m.total_cost||0).toFixed(2);}).reverse();
  var kwhs    = monthly.map(function(m){return +(m.total_kwh||0).toFixed(2);}).reverse();

  // Cost chart
  charts.cost = new Chart($('chartCost'), {
    type:'bar',
    data:{labels:months, datasets:[{data:costs,
      backgroundColor:'rgba(245,158,11,.35)',borderColor:'#f59e0b',borderWidth:1.5,borderRadius:4}]},
    options:Object.assign({}, chartDefaults(), {plugins:Object.assign({}, chartDefaults().plugins,
      {tooltip:{callbacks:{label:function(c){return c.raw+' €';}}}})}),
  });

  // kWh chart
  charts.kwh = new Chart($('chartKwh'), {
    type:'bar',
    data:{labels:months, datasets:[{data:kwhs,
      backgroundColor:'rgba(0,180,255,.25)',borderColor:'#00b4ff',borderWidth:1.5,borderRadius:4}]},
    options:Object.assign({}, chartDefaults(), {plugins:Object.assign({}, chartDefaults().plugins,
      {tooltip:{callbacks:{label:function(c){return c.raw+' kWh';}}}})}),
  });

  // SOC verlauf (last 10 sessions)
  var last10 = sessions.slice(0,10).reverse();
  var socStart= last10.map(function(s){return s.soc_start||0;});
  var socEnd  = last10.map(function(s){return s.soc_end||0;});
  var socLabels=last10.map(function(s){return new Date(s.start_ts).toLocaleDateString('de-DE',{day:'2-digit',month:'2-digit'});});
  charts.soc = new Chart($('chartSoc'), {
    type:'line',
    data:{labels:socLabels, datasets:[
      {label:'SOC Start',data:socStart,borderColor:'#4a5c72',backgroundColor:'rgba(74,92,114,.1)',
       fill:true,tension:.3,pointRadius:3},
      {label:'SOC Ende', data:socEnd,  borderColor:'#3ddc97',backgroundColor:'rgba(61,220,151,.1)',
       fill:true,tension:.3,pointRadius:3},
    ]},
    options:Object.assign({}, chartDefaults(), {
      plugins:{legend:{display:true,labels:{color:'#4a5c72',font:{family:'DM Mono',size:9}}}},
      scales:Object.assign({}, chartDefaults().scales, {y:Object.assign({}, chartDefaults().scales.y,
        {min:0,max:100,ticks:Object.assign({}, chartDefaults().scales.y.ticks, {callback:function(v){return v+'%';}})})}),
    }),
  });

  // Verbrauch kWh/100km per month
  var eff = monthly.map(function(m){
    if(!m.km_driven||m.km_driven<=0) return null;
    return +(m.total_kwh/m.km_driven*100).toFixed(1);
  }).reverse();
  charts.km = new Chart($('chartKm'), {
    type:'line',
    data:{labels:months, datasets:[{data:eff,
      borderColor:'#3ddc97',backgroundColor:'rgba(61,220,151,.08)',
      fill:true,tension:.3,pointRadius:4,spanGaps:true}]},
    options:Object.assign({}, chartDefaults(), {plugins:Object.assign({}, chartDefaults().plugins,
      {tooltip:{callbacks:{label:function(c){return c.raw+' kWh/100km';}}}})}),
  });
}
