// vehicles.js — provides:
//   loadVehicleList, openAddVehicleModal, openEditVehicleModal,
//   loadVehicleModalFields, closeVehicleModal, saveVehicleModal,
//   archiveVehicleModal, suggestVehicleImage, openSilhouettePicker,
//   uploadVehicleImage, deleteVehicleImage, refreshVehicleModalImage

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
    var _eh = typeof escapeHtml === 'function' ? escapeHtml : function(s){return String(s||'').replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];});};
    var thumbSrc = '/api/vehicles/'+encodeURIComponent(v.id)+'/image/file';
    row.innerHTML =
      '<img src="'+thumbSrc+'" style="width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0;background:var(--bg2)" onerror="this.style.display=\'none\'">' +
      '<div style="flex:1;min-width:0">' +
        '<div style="font-weight:700;font-size:.85rem;color:#fff">'+_eh(v.name||'')+'</div>' +
        '<div style="font-size:.7rem;font-family:var(--mono);color:var(--mute);margin-top:3px">'+_eh(v.provider||'ha')+' · ID: '+_eh(v.id)+(isV0?' · Primär':'')+'</div>' +
      '</div>' +
      '<div style="font-size:.72rem;font-family:var(--mono);color:'+(active?'var(--acc)':'var(--mute)')+'">' +
        (active?'● Aktiv':'○ Inaktiv') +
      '</div>' +
      '<button class="btn-s" style="font-size:.72rem;padding:5px 12px" data-vid="'+_eh(v.id)+'">✏ Bearbeiten</button>';
    var editBtn = row.querySelector('.btn-s[data-vid]');
    if (editBtn) editBtn.addEventListener('click', function(){ openEditVehicleModal(this.dataset.vid); });
    el.appendChild(row);
  });
}

function _setVehicleModalButtons(isEdit, isV0) {
  var archiveBtn    = $('vm_archive_btn');
  var hardDeleteBtn = $('vm_hard_delete_btn');
  var showDelete = isEdit && !isV0;
  if (archiveBtn)    archiveBtn.style.display    = showDelete ? '' : 'none';
  if (hardDeleteBtn) hardDeleteBtn.style.display  = showDelete ? '' : 'none';
}

async function openAddVehicleModal() {
  try {
    _editingVehicleId = null;
    $('vehicleModalTitle').textContent = 'Fahrzeug hinzufügen';
    $('vm_name').value = '';
    $('vm_battery').value = '77.0';
    $('vm_poll').value = '60';
    $('vm_home_lat').value = '';
    $('vm_home_lon').value = '';
    $('vm_provider').selectedIndex = 0;
    $('vm_info').textContent = '';
    _setVehicleModalButtons(false);
    // Reset location fields
    var locEnabled = $('vm_loc_enabled');
    if (locEnabled) locEnabled.checked = false;
    var locMode = $('vm_loc_mode');
    if (locMode) locMode.value = 'home_external';
    var locSource = $('vm_loc_source');
    if (locSource) locSource.value = 'combined';
    var locDetect = $('vm_loc_detect_mode');
    if (locDetect) locDetect.value = 'any';
    var locRadius = $('vm_loc_radius');
    if (locRadius) locRadius.value = '150';
    var locEntities = $('vm_loc_ha_entities');
    if (locEntities) locEntities.value = '';
    var locHistory = $('vm_loc_history_enabled');
    if (locHistory) locHistory.checked = false;
    // Hide image section for new vehicles (no vehicle_id yet)
    var imgSection = $('vmImageSection');
    if (imgSection) imgSection.style.display = 'none';
    await loadVehicleModalFields();
    $('vehicleModal').style.display = 'flex';
  } catch(e) {
    console.error('openAddVehicleModal failed', e);
    toast('Fahrzeugdialog konnte nicht geöffnet werden: ' + e.message, 'err');
  }
}

async function openEditVehicleModal(vid) {
  try {
    _editingVehicleId = vid;
    var vehicles = await fetch('/api/vehicles').then(function(r){return r.json();});
    var v = vehicles.find(function(x){return x.id===vid;});
    if(!v) { toast('Fahrzeug nicht gefunden','err'); return; }
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
    $('vm_info').textContent = '';
    _setVehicleModalButtons(true, vid === 'v0');
    // Location fields
    var locEnabled = $('vm_loc_enabled');
    if (locEnabled) locEnabled.checked = !!v.location_enabled;
    var locMode = $('vm_loc_mode');
    if (locMode) locMode.value = v.location_mode || 'home_external';
    var locSource = $('vm_loc_source');
    if (locSource) locSource.value = v.location_source || 'combined';
    var locDetect = $('vm_loc_detect_mode');
    if (locDetect) locDetect.value = v.home_detection_mode || 'any';
    var locRadius = $('vm_loc_radius');
    if (locRadius) locRadius.value = v.home_radius_m || '150';
    var locEntities = $('vm_loc_ha_entities');
    if (locEntities) locEntities.value = (v.location_ha_entities||[]).join('\n');
    var locHistory = $('vm_loc_history_enabled');
    if (locHistory) locHistory.checked = !!v.location_history_enabled;
    // Show image section for existing vehicles
    var imgSection = $('vmImageSection');
    if (imgSection) imgSection.style.display = '';
    await loadVehicleModalFields(v);
    await refreshVehicleModalImage();
    $('vehicleModal').style.display = 'flex';
  } catch(e) {
    console.error('openEditVehicleModal failed', e);
    toast('Fahrzeugdialog konnte nicht geöffnet werden: ' + e.message, 'err');
  }
}

async function loadVehicleModalFields(existingVehicle) {
  existingVehicle = existingVehicle || null;
  var provider = $('vm_provider').value;
  var fields = await fetch('/api/providers/'+provider+'/fields').then(function(r){return r.json();}).catch(function(){return [];});
  var container = $('vm_fields');
  container.innerHTML = '';
  var _eh = typeof escapeHtml === 'function' ? escapeHtml : function(s){return String(s||'').replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];});};
  fields.forEach(function(f) {
    var val = existingVehicle ? (existingVehicle[f.id]||'') : '';
    var wrap = document.createElement('div');
    if(f.type === 'select'){
      wrap.innerHTML = '<label class="lbl">'+_eh(f.label)+'</label>' +
        '<select class="inp" id="vmf_'+_eh(f.id)+'">'+(f.options||[]).map(function(o){return '<option value="'+_eh(o)+'" '+(val===o?'selected':'')+'>'+_eh(o)+'</option>';}).join('')+'</select>' +
        (f.hint?'<span class="hint">'+_eh(f.hint)+'</span>':'');
    } else {
      wrap.innerHTML = '<label class="lbl">'+_eh(f.label)+(f.required?' *':'')+'</label>' +
        '<input class="inp" type="'+(f.type||'text')+'" id="vmf_'+_eh(f.id)+'" value="'+_eh(String(val||''))+'" placeholder="'+_eh(f.placeholder||'')+'">' +
        (f.hint?'<span class="hint">'+_eh(f.hint)+'</span>':'');
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
  var haEntities = ($('vm_loc_ha_entities')||{value:''}).value.split('\n').map(function(s){return s.trim();}).filter(Boolean);
  var data = {
    name:                $('vm_name').value.trim() || 'Neues Fahrzeug',
    provider:            provider,
    active:              true,
    battery_capacity_kwh: parseFloat($('vm_battery').value)||77,
    poll_interval:       parseInt($('vm_poll').value)||60,
    home_lat:            $('vm_home_lat').value.trim(),
    home_lon:            $('vm_home_lon').value.trim(),
    location_enabled:    !!($('vm_loc_enabled')||{}).checked,
    location_mode:       ($('vm_loc_mode')||{value:'home_external'}).value,
    location_source:     ($('vm_loc_source')||{value:'combined'}).value,
    home_detection_mode: ($('vm_loc_detect_mode')||{value:'any'}).value,
    home_radius_m:       parseFloat(($('vm_loc_radius')||{value:'150'}).value)||150,
    location_ha_entities: haEntities,
    location_history_enabled: !!($('vm_loc_history_enabled')||{}).checked,
  };
  fields.forEach(function(f) {
    var el = $('vmf_'+f.id);
    if(!el) return;
    if(f.type === 'password') {
      if(!el.value || el.value === '********') return; // keep stored secret
      data[f.id] = el.value;
    } else if(f.type === 'checkbox') {
      data[f.id] = !!el.checked;
    } else if(f.type === 'number') {
      data[f.id] = el.value === '' ? null : parseFloat(el.value);
    } else {
      data[f.id] = el.value;
    }
  });

  // v0 uses car_name instead of name in backend config
  if (_editingVehicleId === 'v0') {
    data.car_name = data.name;
    delete data.name;
  }

  var url  = _editingVehicleId ? '/api/vehicles/'+_editingVehicleId : '/api/vehicles';
  var meth = _editingVehicleId ? 'PUT' : 'POST';
  var resp = await apiFetch(url,{method:meth,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  var r = await resp.json().catch(function(){return {};});
  if(resp.ok && r.ok){
    closeVehicleModal();
    toast(_editingVehicleId ? 'Fahrzeug aktualisiert' : 'Fahrzeug hinzugefügt', 'ok');
    loadVehicleList();
    if (typeof refreshStatus === 'function') setTimeout(refreshStatus, 500);
    if (typeof loadMobileVehicleCards === 'function') loadMobileVehicleCards();
    if (typeof refreshMobileDashboard === 'function') refreshMobileDashboard();
  } else {
    var errMsg = r.error || 'Fehler';
    if (resp.status === 403) {
      errMsg = 'Keine Berechtigung: Fahrzeuge erstellen/bearbeiten';
    }
    $('vm_info').innerHTML = '<span style="color:var(--danger)">❌ '+escapeHtml(errMsg)+'</span>';
  }
}

async function archiveVehicleModal() {
  if(!_editingVehicleId) return;
  var vname = $('vm_name').value || _editingVehicleId;
  if(!confirm('Fahrzeug "'+vname+'" archivieren?\nLadevorgänge bleiben erhalten. Das Fahrzeug wird nicht mehr aktiv gepollt.')) return;
  var r = await apiFetch('/api/vehicles/'+_editingVehicleId,{method:'DELETE'}).then(function(x){return x.json();}).catch(function(){return {ok:false,error:'Netzwerkfehler'};});
  if(r.ok){
    closeVehicleModal();
    toast('Fahrzeug archiviert','ok');
    loadVehicleList();
    if(typeof refreshMobileDashboard === 'function') refreshMobileDashboard();
  } else {
    $('vm_info').innerHTML = '<span style="color:var(--danger)">❌ '+escapeHtml(r.error||'Fehler')+'</span>';
  }
}

function deleteVehicleModal() {
  return archiveVehicleModal();
}

// ── Vehicle Image Functions ──────────────────────────────────────────────────

async function refreshVehicleModalImage() {
  if(!_editingVehicleId) return;
  var preview = $('vmImagePreview');
  if(!preview) return;
  // Check for manual/auto/no image first
  var meta = await fetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image').then(function(x){return x.json();}).catch(function(){return null;});
  if(meta && (meta.has_manual || meta.has_auto)) {
    var url = '/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image/file';
    preview.src = url + '?t='+Date.now();
    var srcLabel = $('vmImageSourceLabel');
    if(srcLabel) srcLabel.textContent = meta.has_manual ? 'Manuell hochgeladen' : 'Automatisch (Provider)';
    return;
  }
  // Fall back to suggest endpoint for silhouette
  var r = await fetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image/suggest').then(function(x){return x.json();}).catch(function(){return null;});
  if(!r) return;
  var url = r.resolved_url || '/static/vehicle_images/placeholder_car.svg';
  preview.src = url + (url.indexOf('?')<0 ? '?t='+Date.now() : '&t='+Date.now());
  var srcLabel = $('vmImageSourceLabel');
  if(srcLabel) srcLabel.textContent = 'Silhouette';
}

async function suggestVehicleImage() {
  if(!_editingVehicleId) return;
  var r = await fetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image/suggest').then(function(x){return x.json();}).catch(function(){return null;});
  if(!r || !r.suggested_key) { toast('Kein Vorschlag verfügbar','warn'); return; }
  var ok = await apiFetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image/default-key',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:r.suggested_key})}).then(function(x){return x.json();}).catch(function(){return {ok:false};});
  if(ok.ok) {
    var preview = $('vmImagePreview');
    if(preview) { preview.src = r.suggested_url+'?t='+Date.now(); }
    toast('Silhouette: '+r.suggested_key,'ok');
  }
}

async function openSilhouettePicker() {
  var picker = $('vmSilhouettePicker');
  var grid   = $('vmSilhouetteGrid');
  if(!picker || !grid) return;
  var manifest = await fetch('/api/vehicle-images/manifest').then(function(x){return x.json();}).catch(function(){return null;});
  if(!manifest) { toast('Manifest nicht geladen','warn'); return; }
  grid.innerHTML = '';
  (manifest.silhouettes||[]).forEach(function(s) {
    var btn = document.createElement('button');
    btn.title = s.label;
    btn.style.cssText = 'background:var(--bg2);border:1px solid var(--brd);border-radius:8px;padding:4px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:4px';
    btn.innerHTML = '<img src="/static/vehicle_images/'+s.file+'" style="width:90px;height:52px;object-fit:contain" onerror="this.style.display=\'none\'">' +
                    '<span style="font-size:.6rem;color:var(--mute)">'+s.label+'</span>';
    btn.onclick = function() { selectSilhouette(s.key, '/static/vehicle_images/'+s.file); };
    grid.appendChild(btn);
  });
  picker.style.display = picker.style.display === 'none' ? '' : 'none';
}

async function selectSilhouette(key, url) {
  if(!_editingVehicleId) return;
  var r = await apiFetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image/default-key',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key})}).then(function(x){return x.json();}).catch(function(){return {ok:false};});
  if(r.ok) {
    var preview = $('vmImagePreview');
    if(preview) preview.src = url+'?t='+Date.now();
    var picker = $('vmSilhouettePicker');
    if(picker) picker.style.display = 'none';
    toast('Silhouette gespeichert','ok');
  } else {
    toast(r.error||'Fehler','err');
  }
}

async function uploadVehicleImage() {
  if(!_editingVehicleId) return;
  var fileEl = $('vmImageFile');
  if(!fileEl || !fileEl.files || !fileEl.files[0]) return;
  var fd = new FormData();
  fd.append('file', fileEl.files[0]);
  // No Content-Type header — browser sets multipart boundary automatically.
  // apiFetch adds X-CSRF-Token without overriding Content-Type.
  var r = await apiFetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image/upload', {method:'POST',body:fd}).then(function(x){return x.json();}).catch(function(){return {ok:false,error:'Netzwerkfehler'};});
  if(r.ok) {
    var preview = $('vmImagePreview');
    if(preview) preview.src = r.url+'?t='+Date.now();
    toast('Bild hochgeladen','ok');
    // Refresh dashboard image if this is the active vehicle
    if(typeof refreshDashboardVehicleImage === 'function') refreshDashboardVehicleImage(_editingVehicleId);
  } else {
    toast(r.error||'Upload fehlgeschlagen','err');
  }
  fileEl.value = '';
}

async function deleteVehicleImage() {
  if(!_editingVehicleId) return;
  if(!confirm('Fahrzeugbild löschen?')) return;
  var r = await apiFetch('/api/vehicles/'+encodeURIComponent(_editingVehicleId)+'/image',{method:'DELETE'}).then(function(x){return x.json();}).catch(function(){return {ok:false};});
  if(r.ok) {
    await refreshVehicleModalImage();
    toast('Bild entfernt','ok');
    if(typeof refreshDashboardVehicleImage === 'function') refreshDashboardVehicleImage(_editingVehicleId);
  }
}
