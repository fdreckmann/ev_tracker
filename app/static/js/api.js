/**
 * api.js — Core helpers: DOM access, toast notifications, formatting, CSRF, fetch wrapper.
 * Loaded before all other EV Tracker scripts.
 * Uses var/window assignments to allow safe multi-script-tag loading.
 */

/* jshint esversion:6 */
var $ = function(id) { return document.getElementById(id); };

function toast(msg, type) {
  type = type || 'ok';
  var el = document.createElement('div');
  el.className = 'toast ' + type;
  el.innerHTML = (type === 'ok' ? '✅' : '❌') + ' ' + msg;
  document.getElementById('toasts').appendChild(el);
  setTimeout(function() { el.remove(); }, 3500);
}

function fmt(v, d) { d = d !== undefined ? d : 2; return v != null && v !== '' ? Number(v).toFixed(d) : '—'; }
function fmtDate(ts) {
  if (!ts) return '—';
  var d = new Date(ts);
  return d.toLocaleDateString('de-DE') + ' ' + d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}
function fmtTime(ts) {
  return ts ? new Date(ts).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) : '—';
}

function _timeAgo(isoTs) {
  if (!isoTs) return '';
  var diff = Math.floor((Date.now() - new Date(isoTs).getTime()) / 1000);
  if (diff < 60)    return 'vor ' + diff + ' Sek.';
  if (diff < 3600)  return 'vor ' + Math.floor(diff / 60) + ' Min.';
  if (diff < 86400) return 'vor ' + Math.floor(diff / 3600) + ' Std.';
  return 'vor ' + Math.floor(diff / 86400) + ' Tagen';
}

// ── CSRF ─────────────────────────────────────────────────────────────────────
var csrfToken = '';
async function initCsrf() {
  try {
    var r = await fetch('/api/csrf-token').then(function(r) { return r.json(); });
    csrfToken = r.token || '';
  } catch (e) { csrfToken = ''; }
}

function apiFetch(url, opts) {
  opts = opts || {};
  opts.headers = opts.headers || {};
  if (csrfToken) opts.headers['X-CSRF-Token'] = csrfToken;
  return fetch(url, opts);
}
