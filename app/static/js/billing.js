// billing.js — Billing/reimbursement tab functions
// Provides: billingTab, onBillingReimbModeChange, loadBillingConfig,
//           loadBillingSummary, saveBillingConfig

function billingTab(n, btn){
  [1,2,3,4].forEach(i=>{ const el=$('billing_step_'+i); if(el) el.style.display=i===n?'':'none'; });
  document.querySelectorAll('.billing-tab').forEach(b=>b.classList.remove('active'));
  if(btn) btn.classList.add('active');
}

function onBillingReimbModeChange(){
  const m = $('billing_reimb_mode')?.value||'fixed_price';
  const fp = $('billing_fixed_price_row');
  if(fp) fp.style.display = m==='fixed_price'?'':'none';
}

async function loadBillingConfig(){
  const vid = $('billing_vehicle_id')?.value||'v0';
  try{
    const r = await apiFetch(`/api/billing/config/${vid}`).then(r=>r.json());
    const s = id=>$(id);
    if(s('billing_enabled'))      s('billing_enabled').checked         = !!r.enabled;
    if(s('billing_location_filter')) s('billing_location_filter').value= r.location_filter||'all';
    if(s('billing_reimb_mode'))   s('billing_reimb_mode').value        = r.reimbursement_mode||'fixed_price';
    if(s('billing_reimb_price'))  s('billing_reimb_price').value       = r.reimbursement_price_per_kwh||0.30;
    if(s('billing_driver'))       s('billing_driver').value            = r.driver_name||'';
    if(s('billing_plate'))        s('billing_plate').value             = r.license_plate||'';
    if(s('billing_dept'))         s('billing_dept').value              = r.department||'';
    if(s('billing_cc'))           s('billing_cc').value                = r.cost_center||'';
    if(s('billing_emp_id'))       s('billing_emp_id').value            = r.employee_id||'';
    if(s('billing_employer_email')) s('billing_employer_email').value  = r.employer_email||'';
    if(s('billing_req_signature')) s('billing_req_signature').checked  = !!r.requires_signature;
    if(s('billing_req_approval'))  s('billing_req_approval').checked   = !!r.requires_approval;
    if(s('billing_auto_send'))     s('billing_auto_send').checked      = !!r.auto_send;
    if(s('billing_recipients'))    s('billing_recipients').value       = (r.recipients||[]).join('\n');
    onBillingReimbModeChange();
    loadBillingSummary();
  }catch(e){ console.warn('loadBillingConfig',e); }
}

async function loadBillingSummary(){
  const strip = $('billing_summary_strip');
  if(!strip) return;
  try{
    const r = await apiFetch('/api/billing/summary').then(r=>r.json());
    const _ehB = typeof escapeHtml === 'function' ? escapeHtml : function(s){return String(s||'').replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];});};
    strip.innerHTML = [
      `<span>📅 <b>${_ehB(r.month||'')}</b></span>`,
      `<span>⚡ kWh gesamt: <b>${(r.total_kwh||0).toFixed(2)}</b></span>`,
      `<span>💰 Kosten: <b>${(r.total_cost||0).toFixed(2)} €</b></span>`,
      `<span>💼 Erstattbar: <b>${(r.reimbursable_kwh||0).toFixed(2)} kWh</b></span>`,
      `<span style="color:#6ee7b7">✅ Erstattung: <b>${(r.reimbursement_total||0).toFixed(2)} €</b></span>`,
    ].join('');
  }catch(e){ if(strip) strip.innerHTML='<span style="color:var(--mute)">Keine Daten</span>'; }
}

async function saveBillingConfig(){
  const g=id=>$(id), st=$('billing_status');
  const vid = g('billing_vehicle_id')?.value||'v0';
  const payload = {
    enabled:                    g('billing_enabled')?.checked||false,
    location_filter:            g('billing_location_filter')?.value||'all',
    reimbursement_mode:         g('billing_reimb_mode')?.value||'fixed_price',
    reimbursement_price_per_kwh: parseFloat(g('billing_reimb_price')?.value||0.30),
    driver_name:                g('billing_driver')?.value||'',
    license_plate:              g('billing_plate')?.value||'',
    department:                 g('billing_dept')?.value||'',
    cost_center:                g('billing_cc')?.value||'',
    employee_id:                g('billing_emp_id')?.value||'',
    employer_email:             g('billing_employer_email')?.value||'',
    requires_signature:         g('billing_req_signature')?.checked||false,
    requires_approval:          g('billing_req_approval')?.checked||false,
    auto_send:                  g('billing_auto_send')?.checked||false,
    recipients: (g('billing_recipients')?.value||'').split(/[\n,]+/).map(s=>s.trim()).filter(Boolean),
  };
  if(st){st.textContent='⏳ Speichere…';st.style.color='var(--mute)';}
  try{
    const r = await apiFetch(`/api/billing/config/${vid}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}).then(r=>r.json());
    if(st){st.textContent=r.ok?'✅ Gespeichert':'❌ '+(r.error||'Fehler');st.style.color=r.ok?'#6ee7b7':'#f87171';}
  }catch(e){if(st){st.textContent='❌ '+e.message;st.style.color='#f87171';}}
}
