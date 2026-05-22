// vehicles.js — provides:
//   loadVehicleList, openAddVehicleModal, openEditVehicleModal,
//   loadVehicleModalFields, closeVehicleModal, saveVehicleModal,
//   deleteVehicleModal

var _editingVehicleId = null;

async function loadVehicleList() {
  var vehicles = await fetch('/api/vehicles').then(function(r){return r.json();}).catch(function(){return [];});
  var el = $('vehicleList');
  if(!el) return;
  el.innerHTML = '';
  vehicles.forEach(function(v) {
    var isV0 = v.id === 'v0';
    var active = v.active !== false;
    var row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:10px;background:var(--bg);border:1px solid var(--brd);border-radius:10px;padding:12px 14px';
    row.innerHTML =
      '<div style="flex:1;min-width:0">' +
        '<div style="font-weight:700;font-size:.85rem;color:#fff">'+v.name+'</div>' +
        '<div style="font-size:.7rem;font-family:var(--mono);color:var(--mute);margin-top:3px">'+(v.provider||'ha')+' · ID: '+v.id+(isV0?' · Primär':'')+'</div>' +
      '</div>' +
      '<div style="font-size:.72rem;font-family:var(--mono);color:'+(active?'var(--acc)':'var(--mute)')+'">' +
        (active?'● Aktiv':'○ Inaktiv') +
      '</div>' +
      (isV0 ? '' : '<button class="btn-s" style="font-size:.72rem;padding:5px 12px" onclick="openEditVehicleModal(\''+v.id+'\')">✏ Bearbeiten</button>');
    el.appendChild(row);
  });
}

async function openAddVehicleModal() {
  _editingVehicleId = null;
  $('vehicleModalTitle').textContent = 'Fahrzeug hinzufügen';
  $('vm_name').value = '';
  $('vm_battery').value = '77.0';
  $('vm_poll').value = '60';
  $('vm_home_lat').value = '';
  $('vm_home_lon').value = '';
  $('vm_provider').selectedIndex = 0;
  $('vm_delete_btn').style.display = 'none';
  $('vm_info').textContent = '';
  await loadVehicleModalFields();
  $('vehicleModal').style.display = 'flex';
}

async function openEditVehicleModal(vid) {
  _editingVehicleId = vid;
  var vehicles = await fetch('/api/vehicles').then(function(r){return r.json();});
  var v = vehicles.find(function(x){return x.id===vid;});
  if(!v) return;
  $('vehicleModalTitle').textContent = 'Fahrzeug bearbeiten';
  $('vm_name').value = v.name||'';
  $('vm_battery').value = v.battery_capacity_kwh||'77.0';
  $('vm_poll').value = v.poll_interval||'60';
  $('vm_home_lat').value = v.home_lat||'';
  $('vm_home_lon').value = v.home_lon||'';
  var sel = $('vm_provider');
  for(var i=0;i<sel.options.length;i++){
    if(sel.options[i].value===v.provider){ sel.selectedIndex=i; break; }
  }
  $('vm_delete_btn').style.display = '';
  $('vm_info').textContent = '';
  await loadVehicleModalFields(v);
  $('vehicleModal').style.display = 'flex';
}

async function loadVehicleModalFields(existingVehicle) {
  existingVehicle = existingVehicle || null;
  var provider = $('vm_provider').value;
  var fields = await fetch('/api/providers/'+provider+'/fields').then(function(r){return r.json();}).catch(function(){return [];});
  var container = $('vm_fields');
  container.innerHTML = '';
  fields.forEach(function(f) {
    var val = existingVehicle ? (existingVehicle[f.id]||'') : '';
    var wrap = document.createElement('div');
    if(f.type === 'select'){
      wrap.innerHTML = '<label class="lbl">'+f.label+'</label>' +
        '<select class="inp" id="vmf_'+f.id+'">'+(f.options||[]).map(function(o){return '<option value="'+o+'" '+(val===o?'selected':'')+'>'+o+'</option>';}).join('')+'</select>' +
        (f.hint?'<span class="hint">'+f.hint+'</span>':'');
    } else {
      wrap.innerHTML = '<label class="lbl">'+f.label+(f.required?' *':'')+'</label>' +
        '<input class="inp" type="'+(f.type||'text')+'" id="vmf_'+f.id+'" value="'+val+'" placeholder="'+(f.placeholder||'')+'">' +
        (f.hint?'<span class="hint">'+f.hint+'</span>':'');
    }
    container.appendChild(wrap);
  });
}

function closeVehicleModal() {
  $('vehicleModal').style.display = 'none';
}

async function saveVehicleModal() {
  var provider = $('vm_provider').value;
  var fields = await fetch('/api/providers/'+provider+'/fields').then(function(r){return r.json();}).catch(function(){return [];});
  var data = {
    name:                $('vm_name').value.trim() || 'Neues Fahrzeug',
    provider:            provider,
    active:              true,
    battery_capacity_kwh: parseFloat($('vm_battery').value)||77,
    poll_interval:       parseInt($('vm_poll').value)||60,
    home_lat:            $('vm_home_lat').value.trim(),
    home_lon:            $('vm_home_lon').value.trim(),
  };
  fields.forEach(function(f) {
    var el = $('vmf_'+f.id);
    if(el) data[f.id] = el.value;
  });

  var url  = _editingVehicleId ? '/api/vehicles/'+_editingVehicleId : '/api/vehicles';
  var meth = _editingVehicleId ? 'PUT' : 'POST';
  var r = await fetch(url,{method:meth,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(function(x){return x.json();});
  if(r.ok){
    closeVehicleModal();
    toast(_editingVehicleId ? 'Fahrzeug aktualisiert' : 'Fahrzeug hinzugefügt', 'ok');
    loadVehicleList();
  } else {
    $('vm_info').innerHTML = '<span style="color:var(--danger)">❌ '+(r.error||'Fehler')+'</span>';
  }
}

async function deleteVehicleModal() {
  if(!_editingVehicleId) return;
  if(!confirm('Fahrzeug löschen? Ladevorgänge bleiben erhalten.')) return;
  var r = await fetch('/api/vehicles/'+_editingVehicleId,{method:'DELETE'}).then(function(x){return x.json();});
  if(r.ok){
    closeVehicleModal();
    toast('Fahrzeug gelöscht','ok');
    loadVehicleList();
  }
}
