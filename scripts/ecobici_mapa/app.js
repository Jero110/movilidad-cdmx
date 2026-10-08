/* Ecobici · Rebalanceo — frontend sin framework.
   Tres pestañas sobre un solo mapa MapLibre:
   - Ahora: feed GBFS (/api/snapshot), buscador y ficha con todos los campos de `raw`.
   - Replay: un día simulado foto a foto (/api/replay/...), con el desglose `snap[k]` y el cuadre.
   - Predicción: pronóstico y asignación en vivo (/api/live/...).
   La app solo muestra lo que manda la API; en el navegador únicamente se suma o resta
   para enseñar la cuenta del cuadre de una estación. */
'use strict';

// ───────────────────────── utilidades ─────────────────────────
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const NF = new Intl.NumberFormat('es-MX');
const fmtDec = (n, d) => (n === null || n === undefined || Number.isNaN(+n)) ? '—' : (+n).toLocaleString('es-MX', {minimumFractionDigits: d, maximumFractionDigits: d});
const fmt = n => (n === null || n === undefined || Number.isNaN(+n)) ? '—' : NF.format(n);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const icon = (id, cls = 'ico') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${id}"/></svg>`;
const norm = s => String(s ?? '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const plural = (n, one, many) => `${fmt(n)} ${n === 1 ? one : many}`;

/** "2026-10-05T13:45" o "...:05" (hora local sin zona) → "13:45". */
const hhmm = iso => (typeof iso === 'string' && iso.length >= 16) ? iso.slice(11, 16) : '—';
/** "2025-09-01" → "lun 1 sep 2025". */
function dayLabel(d) {
  if (!d) return '—';
  const [y, m, dd] = d.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, dd).toLocaleDateString('es-MX', {weekday: 'short', day: 'numeric', month: 'short', year: 'numeric'}).replace(/\./g, '').replace(/,/g, '').replace(/ de /g, ' ');
}
/** "2026-09-30" → "30 sep 2026". */
function shortDay(d) {
  if (!d) return '—';
  const [y, m, dd] = d.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, dd).toLocaleDateString('es-MX', {day: 'numeric', month: 'short', year: 'numeric'}).replace(/\./g, '').replace(/ de /g, ' ');
}
/** "2026-10-05T12:10:00" → "5 oct, 12:10". */
function dateTimeLabel(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  const date = new Date(y, m - 1, d).toLocaleDateString('es-MX', {day: 'numeric', month: 'short'}).replace(/\./g, '').replace(/ de /g, ' ');
  return iso.length >= 16 ? `${date}, ${iso.slice(11, 16)}` : date;
}
/** Segundos Unix → fecha y hora local legible. */
const unixLabel = s => new Date(s * 1000).toLocaleString('es-MX', {day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
/** Suma minutos a "HH:MM" y regresa "HH:MM". */
function addMin(hm, mins) {
  if (!/^\d\d:\d\d$/.test(hm)) return '—';
  const t = (+hm.slice(0, 2) * 60 + +hm.slice(3) + mins) % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}

class ApiError extends Error {
  constructor(msg, status) { super(msg); this.status = status; }
}
async function api(path, {method = 'GET', timeout = 45000} = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(path, {method, cache: 'no-store', signal: ctl.signal});
    const text = await r.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch { body = null; }
    if (!r.ok) {
      let msg = body?.detail ?? body?.error ?? `El servidor respondió ${r.status}.`;
      if (typeof msg !== 'string') msg = JSON.stringify(msg);
      throw new ApiError(msg, r.status);
    }
    if (body && typeof body === 'object' && !Array.isArray(body) && body.error) throw new ApiError(body.error, r.status);
    return body;
  } catch (e) {
    if (e.name === 'AbortError') throw new ApiError('El servidor tardó demasiado en responder.', 0);
    if (e instanceof TypeError) throw new ApiError('No hay conexión con el servidor.', 0);
    throw e;
  } finally { clearTimeout(timer); }
}

let toastTimer = null;
function toast(msg, error = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.toggle('error', error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4500);
}
function showNote(sel, msg, {kind = 'error', retry = null} = {}) {
  const el = $(sel);
  if (!msg) { el.hidden = true; el.innerHTML = ''; return; }
  el.className = `note ${kind}`;
  el.innerHTML = `${icon(kind === 'error' ? 'alert' : 'info')}<span>${esc(msg)}</span>${retry ? '<button type="button">Reintentar</button>' : ''}`;
  if (retry) $('button', el).onclick = retry;
  el.hidden = false;
}
function setBusy(btn, busy, label) {
  btn.disabled = busy;
  btn.setAttribute('aria-busy', String(busy));
  if (label) $('.btn-label', btn).textContent = label;
}
function setRangeFill(el) {
  const max = +el.max || 1;
  el.style.setProperty('--p', `${((+el.value - +el.min) / (max - +el.min || 1)) * 100}%`);
}

// ───────────────────────── estado ─────────────────────────
const S = {
  tab: 'ahora',
  map: null,         // mapa principal (MAPS[0].map)
  mapReady: false,
  view: null,        // vista del mapa principal: {mode, stations:[...], props(i), ...}
  views: [],         // una vista por mapa (dos al comparar escenarios en Replay)
  sel: null,         // índice de estación seleccionada (mismo índice en los dos mapas)
  cardSide: 0,       // de qué mapa es la ficha abierta
  snapshot: null,
  zones: {on: {ahora: false, replay: false, pronostico: false, asignacion: false}, geo: null},  // interruptor independiente por vista
  nums: {replay: false, pronostico: false, asignacion: false},  // "Bicis por estación", independiente por vista
  replay: {index: null, days: {}, arms: {}, dayData: null, sel: [], keys: [], k: 0, timer: null, list: 'emitidas', side: 0, token: 0, alfa: 0},
  live: {models: null, sessionId: null, session: null, poll: null, clock: null, step: -1, follow: true, list: 'emitidas'},
};
// Escenario principal del Replay (el primero elegido).
Object.defineProperty(S.replay, 'arm', {get() { return this.sel[0] || null; }});
const SESSION_KEY = 'ecobici.asignacion.session';
const SIDE = ['A', 'B'];

// ───────────────────────── estilo de las líneas de órdenes ─────────────────────────
// Tres opciones visuales (se prueban con ?lineas=a|b|c). Todas: arcos o rectas finas, emitidas más claras y llegaron más
// intensas del mismo matiz (distinción por luminosidad, segura para daltonismo), reubicación punteada.
const LINEAS = {
  // A: arcos con degradado (tenue en el origen, intenso en el destino) y un punto en el destino; rosa → magenta.
  a: {nombre: 'Arcos con degradado, magenta', emit: '#ec7fbf', arrive: '#a1176f', curva: 0.2, degradado: true, flecha: false, ancho: [1.3, 2, 2.7], anchoLlegan: [1.6, 2.5, 3.3], emitOp: [0.35, 1], arriveOp: [0.4, 1]},
  // B: arcos lisos con flecha; gris arena (plan, discreto) → vino (ejecutado).
  b: {nombre: 'Arcos con flecha, arena y vino', emit: '#a89f91', arrive: '#8a1c5c', curva: 0.2, degradado: false, flecha: true, ancho: [1.1, 1.8, 2.5], anchoLlegan: [1.4, 2.3, 3.1], emitOp: [0.7, 0.7], arriveOp: [0.8, 0.8]},
  // C: rectas finas con flecha y halo blanco; turquesa → fucsia.
  c: {nombre: 'Rectas finas, turquesa y fucsia', emit: '#5fb7c9', arrive: '#d63384', curva: 0, degradado: false, flecha: true, halo: true, ancho: [1.1, 1.8, 2.5], anchoLlegan: [1.4, 2.3, 3.1], emitOp: [0.8, 0.8], arriveOp: [0.85, 0.85]},
};
const LINEA_DEFAULT = 'a';
// Viajes de la foto (son un toggle, no hace falta que sean tenues): opacidad sin y con órdenes en el mapa.
const TRIPS_OP = {now: 0.42, nowOrd: 0.34, detour: 0.78, detourOrd: 0.68};
// Paletas de color (emitida → llegó; el degradado va de tenue a intenso), para combinar con la UI fría y sobria. ?paleta=1..5
const PALETAS = {
  // Cada capa tiene UN matiz propio; el sentido (tenue en el origen → intenso en el destino) va en la opacidad.
  1: {nombre: 'Arena y azul tinta', emit: '#c4905c', arrive: '#1b4a8f'},
  2: {nombre: 'Ocre e índigo', emit: '#b8893a', arrive: '#2f3e6b'},
  3: {nombre: 'Camello y azul pizarra', emit: '#d09560', arrive: '#355070'},
  4: {nombre: 'Bronce y petróleo', emit: '#b87a4a', arrive: '#0e5a7a'},
  5: {nombre: 'Malva y petróleo', emit: '#9f7fae', arrive: '#0e5a7a'},
};
const PALETA_DEFAULT = 1;
const QS = new URLSearchParams(location.search);
const ORD = {...(LINEAS[QS.get('lineas')] || LINEAS[LINEA_DEFAULT]), ...(PALETAS[QS.get('paleta')] || PALETAS[PALETA_DEFAULT])};
const hexA = (hex, a) => `rgba(${[1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16)).join(',')},${a})`;
{
  const r = document.documentElement;
  r.style.setProperty('--emit', ORD.emit);
  r.style.setProperty('--arrive', ORD.arrive);
  r.style.setProperty('--emit-fade', hexA(ORD.emit, 0.18));
  r.style.setProperty('--arrive-fade', hexA(ORD.arrive, 0.18));
  r.classList.toggle('lg-grad', ORD.degradado);
}

// ───────────────────────── colores y estados ─────────────────────────
const CSSV = getComputedStyle(document.documentElement);
const cssv = n => CSSV.getPropertyValue(n).trim();
const ST_COLOR = {
  vacia: cssv('--st-vacia'), pocas: cssv('--st-pocas'), normal: cssv('--st-normal'),
  llena: cssv('--st-llena'), inactiva: cssv('--st-inactiva'),
};
const C = {deliver: cssv('--deliver'), pickup: cssv('--pickup'), relabel: cssv('--relabel'), detour: cssv('--detour'),
  trip: cssv('--trip-now'), emit: cssv('--emit'), arrive: cssv('--arrive'), gray: '#9aa4ad'};
const ST_LABEL = {vacia: 'Vacía', pocas: 'Pocas bicis', normal: 'Normal', llena: 'Llena', inactiva: 'Inactiva'};
/** Estado de una estación según bicis y anclajes libres. Pocas = 1 a 3 bicis. */
function stationState(bikes, docks, active = true) {
  if (!active) return 'inactiva';
  if (bikes <= 0) return 'vacia';
  if (docks <= 0) return 'llena';
  if (bikes <= 3) return 'pocas';
  return 'normal';
}

// ───────────────────────── imágenes del mapa ─────────────────────────
const FONT = '"Public Sans Variable", "Public Sans", system-ui, sans-serif';
const PR = 2;  // resolución de las imágenes

function drawBike(ctx, x, y, g, color) {
  ctx.save();
  ctx.strokeStyle = color; ctx.lineWidth = 1.25 * PR; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  const r = g * 0.22, wy = y + g * 0.68, lx = x + g * 0.22, rx = x + g * 0.78;
  ctx.beginPath(); ctx.arc(lx, wy, r, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath(); ctx.arc(rx, wy, r, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(lx, wy); ctx.lineTo(x + g * 0.40, y + g * 0.38); ctx.lineTo(x + g * 0.68, y + g * 0.38); ctx.lineTo(rx, wy);
  ctx.moveTo(x + g * 0.40, y + g * 0.38); ctx.lineTo(x + g * 0.52, wy);
  ctx.moveTo(x + g * 0.33, y + g * 0.24); ctx.lineTo(x + g * 0.47, y + g * 0.24);
  ctx.moveTo(x + g * 0.68, y + g * 0.38); ctx.lineTo(x + g * 0.62, y + g * 0.22); ctx.lineTo(x + g * 0.72, y + g * 0.22);
  ctx.stroke();
  ctx.restore();
}
function canvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.ceil(w); c.height = Math.ceil(h);
  return [c, c.getContext('2d')];
}
function pill(ctx, x, y, w, h) {
  const r = h / 2;
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arc(x + w - r, y + r, r, -Math.PI / 2, Math.PI / 2);
  ctx.lineTo(x + r, y + h); ctx.arc(x + r, y + r, r, Math.PI / 2, Math.PI * 1.5); ctx.closePath();
}
/** Pin de estación: insignia del color del estado, ícono de bici y número, con punta abajo. */
function pinImage(state, text) {
  const fg = state === 'pocas' ? '#2b1d00' : '#ffffff';
  const fs = 11.5 * PR, g = 12 * PR, padL = 6 * PR, gap = 3 * PR, padR = 7.5 * PR, h = 20 * PR, tail = 5 * PR, sw = 1.5 * PR;
  let [, m] = canvas(1, 1); m.font = `700 ${fs}px ${FONT}`;
  const tw = m.measureText(text).width;
  const w = padL + g + gap + tw + padR;
  const [c, ctx] = canvas(w + sw * 2, h + tail + sw * 2);
  ctx.font = `700 ${fs}px ${FONT}`;
  const x = sw, y = sw, cx = x + w / 2;
  pill(ctx, x, y, w, h);
  ctx.moveTo(cx - 4.5 * PR, y + h - 1); ctx.lineTo(cx, y + h + tail); ctx.lineTo(cx + 4.5 * PR, y + h - 1);
  ctx.fillStyle = ST_COLOR[state]; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = sw * 2;
  ctx.stroke(); ctx.fill();
  drawBike(ctx, x + padL, y + (h - g) / 2, g, fg);
  ctx.fillStyle = fg; ctx.textBaseline = 'middle';
  ctx.fillText(text, x + padL + g + gap, y + h / 2 + 0.5 * PR);
  return c;
}
/** Tono oscuro de cada estado para texto sobre blanco (contraste ≥ 4.5:1); neutral = gris discreto. */
const ST_INK = {vacia: '#a52a2a', pocas: '#7f5600', normal: '#166b3c', llena: '#3d2f8f', inactiva: '#5f6b76', neutral: '#5b6670'};
/** Número de bicis sobre una pastilla blanca, con el tono del estado de la estación (Replay) o gris (Asignación). */
function numImage(state, text) {
  const fs = 10.5 * PR, h = 16 * PR, pad = 5 * PR, sw = 1 * PR;
  let [, m] = canvas(1, 1); m.font = `600 ${fs}px ${FONT}`;
  const w = Math.max(h, m.measureText(text).width + pad * 2);
  const [c, ctx] = canvas(w + sw * 2, h + sw * 2);
  ctx.font = `600 ${fs}px ${FONT}`;
  pill(ctx, sw, sw, w, h);
  ctx.fillStyle = 'rgba(255,255,255,.94)'; ctx.fill();
  ctx.strokeStyle = state === 'neutral' ? '#c3cbd2' : ST_COLOR[state]; ctx.globalAlpha = state === 'neutral' ? 1 : 0.7; ctx.lineWidth = sw; ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.fillStyle = ST_INK[state] || ST_INK.neutral; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, sw + w / 2, sw + h / 2 + 0.5 * PR);
  return c;
}
/** Pastillas de la foto: +N verde (entregadas), −N rojo (recogidas) y, con prefijo L,
 *  +N/−N azul de contorno (cambio neto a rentables / a no rentables, sin mover bicis). */
function deltaImage(parts) {
  const fs = 11.5 * PR, h = 19 * PR, pad = 6 * PR, gap = 3 * PR, sw = 1.5 * PR;
  let [, m] = canvas(1, 1); m.font = `700 ${fs}px ${FONT}`;
  const label = parts.map(p => p[0] === 'L');
  parts = parts.map(p => (p[0] === 'L' ? p.slice(1) : p));
  const ws = parts.map(p => Math.max(h, m.measureText(p).width + pad * 2));
  const total = ws.reduce((a, b) => a + b, 0) + gap * (parts.length - 1);
  const [c, ctx] = canvas(total + sw * 2, h + sw * 2);
  ctx.font = `700 ${fs}px ${FONT}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  let x = sw;
  parts.forEach((p, j) => {
    const lb = label[j];
    const color = lb ? C.relabel : p[0] === '+' ? C.deliver : C.pickup;
    pill(ctx, x, sw, ws[j], h);
    ctx.strokeStyle = lb ? color : '#ffffff'; ctx.lineWidth = lb ? sw : sw * 2;
    ctx.fillStyle = lb ? '#ffffff' : color; ctx.fill(); ctx.stroke();
    ctx.fillStyle = lb ? color : '#ffffff'; ctx.fillText(p, x + ws[j] / 2, sw + h / 2 + 0.5 * PR);
    x += ws[j] + gap;
  });
  return c;
}
/** Flecha que marca el sentido de una línea de órdenes (apunta a la derecha; el mapa la gira con la línea). */
function arrowImage(kind) {
  const [c, ctx] = canvas(14 * PR, 14 * PR);
  ctx.scale(PR, PR);
  ctx.beginPath(); ctx.moveTo(3, 2.5); ctx.lineTo(12, 7); ctx.lineTo(3, 11.5); ctx.closePath();
  ctx.fillStyle = kind === 'emit' ? C.emit : C.arrive;
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.2; ctx.lineJoin = 'round';
  ctx.stroke(); ctx.fill();
  return c;
}
function makeImage(map, id) {
  if (!map || map.hasImage(id)) return;
  const [kind, ...rest] = id.split('|');
  let c = null;
  if (kind === 'pin') c = pinImage(rest[0], rest[1]);
  else if (kind === 'num') c = numImage(rest[0], rest[1]);
  else if (kind === 'd') c = deltaImage(rest);
  else if (kind === 'arrow') c = arrowImage(rest[0]);
  if (!c) return;
  const data = c.getContext('2d').getImageData(0, 0, c.width, c.height);
  map.addImage(id, data, {pixelRatio: PR});
}

// ───────────────────────── zonas (AGEB urbanas del INEGI) ─────────────────────────
// GET /api/zonas → FeatureCollection; cada AGEB trae {cvegeo, alcaldia, estaciones: [short_name, ...]}.
let zonasP = null;
function loadZonas() {
  if (!zonasP) {
    zonasP = api('/api/zonas', {timeout: 60000}).then(fc => {
      if (!Array.isArray(fc?.features)) throw new ApiError('La respuesta de zonas no es un GeoJSON válido.');
      S.zones.geo = fc.features.filter(f => f?.geometry && Array.isArray(f.properties?.estaciones));
      return S.zones.geo;
    }).catch(e => {
      zonasP = null;
      throw new ApiError(e.status === 404 ? 'El servidor todavía no publica las zonas (GET /api/zonas).' : `No se pudieron cargar las zonas. ${e.message}`, e.status);
    });
  }
  return zonasP;
}
const snKey = sn => String(sn ?? '').replace(/^0+(?=\d)/, '');
/** Colorea cada AGEB con el mismo estado y los mismos cortes que los pines (vacía, pocas, normal, llena),
 *  sumando bicis disponibles, capacidad y anclajes libres de sus estaciones activas. */
function zonesGeo(v) {
  if (!S.zones.on[v.mode] || !v.zone || !S.zones.geo) return EMPTY;
  if (!v._idx) v._idx = new Map(v.stations.map((s, i) => [snKey(s.short_name), i]));
  const feats = [];
  for (const f of S.zones.geo) {
    const a = {b: 0, cap: 0, docks: 0, n: 0};
    for (const sn of f.properties.estaciones) {
      const i = v._idx.get(snKey(sn));
      const z = i === undefined ? null : v.zone(i);
      if (!z) continue;
      a.b += z.b; a.cap += z.cap; a.docks += z.docks; a.n += 1;
    }
    if (!a.n) continue;
    const st = stationState(a.b, a.docks, true);
    feats.push({type: 'Feature', geometry: f.geometry, properties: {
      c: ST_COLOR[st], st, n: a.n, b: a.b, cap: a.cap, docks: a.docks,
      cvegeo: f.properties.cvegeo, alcaldia: f.properties.alcaldia || '', est: f.properties.estaciones.join(', ')}});
  }
  return {type: 'FeatureCollection', features: feats};
}
function zoneTooltip(p) {
  return `AGEB ${esc(p.cvegeo)} · ${esc(ST_LABEL[p.st])}<small>${esc(p.alcaldia)}${p.alcaldia ? ' · ' : ''}${fmt(p.b)} bicis disponibles / ${fmt(p.cap)} de capacidad<br>Estaciones: ${esc(p.est)}</small>`;
}
function syncZonesUI() {
  $$('[data-zones]').forEach(box => { $('.zones-on', box).checked = !!S.zones.on[box.dataset.zones]; });
}
$$('[data-zones]').forEach(box => {
  const key = box.dataset.zones, cb = $('.zones-on', box);
  cb.onchange = async () => {
    S.zones.on[key] = cb.checked;
    if (cb.checked && !S.zones.geo) {
      mapState('Cargando zonas…', 'busy');
      try { await loadZonas(); } catch (e) { S.zones.on[key] = false; syncZonesUI(); toast(e.message, true); }
      mapState(null);
    }
    renderMap();
  };
});

// ───────────────────────── mapa ─────────────────────────
const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
const HOME = {center: [-99.1685, 19.4095], zoom: 13.25};  // zona de Ecobici con pines visibles
const EMPTY = {type: 'FeatureCollection', features: []};
const MAPS = [];  // {map, side, view, popup, hit, ready}
const ST_LAYERS = ['st-delta', 'st-pin', 'st-num', 'st-num-all', 'st-dot'];
let syncing = false;

function mapState(text, kind = 'center') {
  const el = $('#mapState');
  if (!text) { el.hidden = true; return; }
  el.hidden = false;
  el.dataset.kind = kind;
  $('#mapStateText').textContent = text;
}

async function createMap(container, view) {
  const map = new maplibregl.Map({
    container, style: MAP_STYLE, center: view.center, zoom: view.zoom,
    attributionControl: {compact: true}, dragRotate: false, pitchWithRotate: false, fadeDuration: 0,
  });
  const M = {map, side: MAPS.length, view: null, popup: null, hit: -1, ready: false};
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'bottom-right');
  map.on('styleimagemissing', e => makeImage(map, e.id));
  await Promise.race([
    new Promise((res, rej) => { map.once('load', res); map.once('error', e => rej(e.error || new Error('Error del mapa'))); }),
    new Promise((_, rej) => setTimeout(() => rej(new Error('El mapa base no respondió.')), 20000)),
  ]);
  addLayers(M);
  M.ready = true;
  return M;
}

async function initMap() {
  mapState('Cargando mapa…');
  try {
    if (!window.maplibregl) throw new Error('No se pudo cargar la librería del mapa.');
    const gl = document.createElement('canvas').getContext('webgl2') || document.createElement('canvas').getContext('webgl');
    if (!gl) throw new Error('Este navegador no puede dibujar el mapa (WebGL desactivado).');
    try { await Promise.all([document.fonts.load(`700 12px ${FONT}`), document.fonts.load(`600 12px ${FONT}`)]); } catch { /* fuente del sistema */ }
    const M = await createMap('map', HOME);
    MAPS[0] = M;
    S.map = M.map;
    S.mapReady = true;
    mapState(null);
    renderMap();
  } catch (e) {
    mapState(e.message || 'No se pudo cargar el mapa.', 'error');
    console.warn('mapa:', e.message);
  }
}

/** Segundo mapa para comparar escenarios; se crea la primera vez y queda enlazado al primero. */
let map2P = null;
function ensureMap2() {
  if (!S.mapReady) return null;
  if (!map2P) {
    map2P = createMap('map2', {center: S.map.getCenter(), zoom: S.map.getZoom()}).then(M => {
      MAPS[1] = M;
      const follow = (src, dst) => src.on('move', () => {
        if (syncing) return;
        syncing = true;
        dst.jumpTo({center: src.getCenter(), zoom: src.getZoom()});
        syncing = false;
      });
      follow(MAPS[0].map, M.map);
      follow(M.map, MAPS[0].map);
      // El contenedor cambió de tamaño mientras cargaba: ajusta los dos lienzos.
      requestAnimationFrame(() => MAPS.forEach(m => m?.map.resize()));
      renderMap();
      return M;
    }).catch(e => { map2P = null; toast(`No se pudo abrir el segundo mapa. ${e.message}`, true); });
  }
  return map2P;
}

function addLayers(M) {
  const map = M.map;
  for (const l of map.getStyle().layers) {
    if (l.type === 'symbol' && /poi|housenum/.test(l.id)) map.setLayoutProperty(l.id, 'visibility', 'none');
  }
  map.addSource('zones', {type: 'geojson', data: EMPTY});
  map.addSource('trips', {type: 'geojson', data: EMPTY});
  map.addSource('st', {type: 'geojson', data: EMPTY});
  map.addLayer({id: 'zones-fill', type: 'fill', source: 'zones', paint: {'fill-color': ['get', 'c'], 'fill-opacity': 0.32}});
  map.addLayer({id: 'zones-line', type: 'line', source: 'zones', paint: {'line-color': '#ffffff', 'line-width': 0.8, 'line-opacity': 0.9}});
  map.addLayer({id: 'trips-now', type: 'line', source: 'trips', filter: ['==', ['get', 'k'], 'now'],
    layout: {'line-cap': 'round'}, paint: {'line-color': C.trip, 'line-opacity': TRIPS_OP.now, 'line-width': 1.3}});
  map.addLayer({id: 'trips-detour', type: 'line', source: 'trips', filter: ['==', ['get', 'k'], 'detour'],
    layout: {'line-cap': 'round'},
    paint: {'line-color': C.detour, 'line-opacity': TRIPS_OP.detour, 'line-width': 1.6, 'line-dasharray': [1.6, 1.4]}});
  // Órdenes (Replay y Asignación): origen → destino. Debajo de las estaciones.
  map.addSource('orders', {type: 'geojson', data: EMPTY, lineMetrics: true});
  const LS = ['==', ['geometry-type'], 'LineString'];
  const ordWs = a => ['interpolate', ['linear'], ['get', 'n'], 1, a[0], 10, a[1], 30, a[2]];
  const grad = (hex, [a0, a1]) => ['interpolate', ['linear'], ['line-progress'], 0, hexA(hex, a0), 1, hexA(hex, a1)];
  for (const [id, kind, hex, [o0, o1], ordW] of [['ord-emit', 'emit', ORD.emit, ORD.emitOp, ordWs(ORD.ancho)], ['ord-arrive', 'arrive', ORD.arrive, ORD.arriveOp, ordWs(ORD.anchoLlegan)]]) {
    if (ORD.halo) map.addLayer({id: `${id}-halo`, type: 'line', source: 'orders', filter: ['all', LS, ['==', ['get', 'k'], kind]],
      layout: {'line-cap': 'round'}, paint: {'line-color': '#ffffff', 'line-opacity': 0.55, 'line-width': ['+', ordW, 1.6]}});
    map.addLayer({id, type: 'line', source: 'orders', filter: ['all', LS, ['==', ['get', 'k'], kind]],
      layout: {'line-cap': 'round'},
      paint: ORD.degradado ? {'line-gradient': grad(hex, [o0, o1]), 'line-width': ordW}
                           : {'line-color': hex, 'line-opacity': o0, 'line-width': ordW}});
  }
  map.addLayer({id: 'ord-reloc', type: 'line', source: 'orders', filter: ['all', LS, ['==', ['get', 'k'], 'reloc']],
    layout: {'line-cap': 'butt'}, paint: {'line-color': ORD.arrive, 'line-opacity': 0.9, 'line-width': 2, 'line-dasharray': [1, 1.6]}});
  // Punto en el destino (solo con degradado: el sentido va en el degradado, no en una flecha).
  map.addLayer({id: 'ord-end', type: 'circle', source: 'orders', filter: ['all', ['==', ['geometry-type'], 'Point'], ['==', ['get', 'k'], 'end']],
    layout: {visibility: ORD.degradado ? 'visible' : 'none'},
    paint: {'circle-color': ['match', ['get', 'a'], 'emit', ORD.emit, ORD.arrive], 'circle-opacity': 0.95, 'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2, 15, 3.4],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1}});
  // Línea bajo el cursor: más gruesa y con borde blanco; las demás se atenúan (ver `resalta`).
  const hlFilter = ['all', LS, ['==', ['get', 'fid'], -1]];
  map.addLayer({id: 'ord-hl-casing', type: 'line', source: 'orders', filter: hlFilter,
    layout: {'line-cap': 'round'}, paint: {'line-color': '#ffffff', 'line-opacity': 0.95, 'line-width': 6}});
  map.addLayer({id: 'ord-hl', type: 'line', source: 'orders', filter: hlFilter,
    layout: {'line-cap': 'round'}, paint: {'line-color': ['match', ['get', 'a'], 'emit', ORD.emit, ORD.arrive], 'line-opacity': 1, 'line-width': 3.4}});
  map.addLayer({id: 'ord-arrow', type: 'symbol', source: 'orders', filter: LS,
    layout: {'symbol-placement': 'line-center', 'icon-image': ['concat', 'arrow|', ['get', 'a']], 'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'icon-size': ['interpolate', ['linear'], ['zoom'], 11, 0.5, 15, 0.8],
      visibility: ORD.flecha ? 'visible' : 'none'}});
  map.addLayer({id: 'ord-hit', type: 'line', source: 'orders', filter: LS, paint: {'line-color': '#000', 'line-opacity': 0, 'line-width': 14}});
  map.addLayer({id: 'st-dot', type: 'circle', source: 'st', layout: {'circle-sort-key': ['get', 'sort']},
    paint: {
      'circle-color': ['get', 'color'], 'circle-opacity': ['get', 'op'], 'circle-stroke-opacity': ['get', 'op'],
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 2.2, 12, 3.6, 14, 4.5, 16, 5.5],
      'circle-stroke-color': '#ffffff', 'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 10, 0.6, 13, 1.4],
    }});
  map.addLayer({id: 'st-sel', type: 'circle', source: 'st', filter: ['==', ['get', 'i'], -1],
    paint: {'circle-color': 'rgba(0,0,0,0)', 'circle-radius': 15, 'circle-stroke-color': '#15212b', 'circle-stroke-width': 2.5}});
  // Pines de estado (Ahora, Pronóstico): visibles desde el zoom inicial, un poco más chicos de lejos.
  map.addLayer({id: 'st-pin', type: 'symbol', source: 'st', minzoom: 12.4,
    filter: ['==', ['get', 'anchor'], 'bottom'],
    layout: {
      'icon-image': ['get', 'img'], 'icon-anchor': 'bottom', 'icon-allow-overlap': false, 'icon-padding': 0,
      'icon-size': ['interpolate', ['linear'], ['zoom'], 12.4, 0.74, 14, 0.9, 15.5, 1],
      'symbol-sort-key': ['get', 'sort'],
    }});
  // Número gris discreto (Replay, Asignación): solo de cerca, para que el rebalanceo sea lo que resalta.
  map.addLayer({id: 'st-num', type: 'symbol', source: 'st', minzoom: 13.2,
    filter: ['==', ['get', 'anchor'], 'center'],
    layout: {'icon-image': ['get', 'img'], 'icon-anchor': 'center', 'icon-allow-overlap': false, 'icon-padding': 0, 'symbol-sort-key': ['get', 'sort']}});
  // Números del Replay (interruptor "Bicis por estación"): a cualquier zoom; si se enciman, queda la bolita.
  map.addLayer({id: 'st-num-all', type: 'symbol', source: 'st',
    filter: ['==', ['get', 'anchor'], 'num'],
    layout: {'icon-image': ['get', 'img'], 'icon-anchor': 'center', 'icon-allow-overlap': false, 'icon-padding': 0, 'symbol-sort-key': ['get', 'sort']}});
  map.addLayer({id: 'st-delta', type: 'symbol', source: 'st',
    filter: ['!=', ['get', 'dimg'], ''],
    layout: {
      // +N verde, −N rojo y, si "Cambios de dañadas" está prendido, el cambio neto de etiqueta (±N azul): todos con el mismo zoom.
      'icon-image': ['get', 'dimg'], 'icon-anchor': 'bottom', 'icon-offset': [0, -7],
      'icon-allow-overlap': true, 'icon-ignore-placement': true, 'symbol-sort-key': ['get', 'sort'],
    }});

  M.popup = new maplibregl.Popup({closeButton: false, closeOnClick: false, offset: 14, maxWidth: '280px'});
  map.on('mousemove', ST_LAYERS, e => {
    map.getCanvas().style.cursor = 'pointer';
    const i = e.features[0]?.properties?.i;
    if (i === undefined || i === M.hit || !M.view) return;
    M.hit = i;
    const s = M.view.stations[i];
    M.popup.setLngLat([s.lon, s.lat]).setHTML(`${esc(s.short_name)} · ${esc(bare(s.name))}<small>${esc(M.view.hover?.(i) ?? '')}</small>`).addTo(map);
  });
  map.on('mouseleave', ST_LAYERS, () => { map.getCanvas().style.cursor = ''; M.hit = -1; M.popup.remove(); });
  map.on('click', ST_LAYERS, e => {
    const i = e.features[0]?.properties?.i;
    if (i !== undefined) openStation(i, {fly: false, side: M.side});
  });
  // Líneas de órdenes: al pasar, ficha con origen, destino, bicis y horas; al pulsar, la ficha se queda hasta cerrarla.
  M.pinned = new maplibregl.Popup({closeButton: true, closeOnClick: false, offset: 10, maxWidth: '280px', className: 'ord'});
  const sobreEstacion = pt => map.queryRenderedFeatures(pt, {layers: ST_LAYERS}).length > 0;
  const resalta = fid => {
    for (const l of ['ord-hl-casing', 'ord-hl']) map.setFilter(l, ['all', ['==', ['geometry-type'], 'LineString'], ['==', ['get', 'fid'], fid]]);
    // El resto de las líneas se atenúa mientras una está resaltada (con degradado, el degradado ya trae su opacidad).
    ordOpacidad(map, fid === -1 ? 1 : 0.28);
  };
  map.on('mousemove', 'ord-hit', e => {
    if (sobreEstacion(e.point)) { resalta(-1); return; }
    map.getCanvas().style.cursor = 'pointer';
    resalta(e.features[0].properties.fid);
    M.popup.setLngLat(e.lngLat).setHTML(e.features[0].properties.tip).addTo(map);
  });
  map.on('mouseleave', 'ord-hit', () => { resalta(-1); if (M.hit === -1) { map.getCanvas().style.cursor = ''; M.popup.remove(); } });
  map.on('click', 'ord-hit', e => {
    if (sobreEstacion(e.point)) return;
    M.popup.remove();
    M.pinned.setLngLat(e.lngLat).setHTML(e.features[0].properties.tip).addTo(map);
  });
  map.on('mousemove', 'zones-fill', e => {
    if (M.hit !== -1 || map.queryRenderedFeatures(e.point, {layers: ST_LAYERS}).length) return;
    const p = e.features[0]?.properties;
    if (!p) return;
    M.popup.setLngLat(e.lngLat).setHTML(zoneTooltip(p)).addTo(map);
  });
  map.on('mouseleave', 'zones-fill', () => { if (M.hit === -1) M.popup.remove(); });
}

/** Dibuja el modo activo en cada mapa. Cada vista dice qué estaciones y propiedades dibujar. */
function renderMap() {
  const views = buildViews();
  const prevMode = S.view?.mode;
  S.views = views;
  S.view = views[0];
  if (prevMode && prevMode !== S.view.mode) closeCard();
  setCompare(views.length > 1);
  renderLegend();
  views.forEach((v, j) => { const M = MAPS[j]; if (M?.ready) paint(M, v); });
}
function paint(M, v) {
  M.view = v;
  const feats = [];
  v.stations.forEach((s, i) => {
    if (!Number.isFinite(+s.lon) || !Number.isFinite(+s.lat)) return;
    const p = v.props(i);
    if (!p) return;
    feats.push({type: 'Feature', geometry: {type: 'Point', coordinates: [+s.lon, +s.lat]}, properties: {i, dlow: '', op: 1, ...p}});
  });
  M.map.getSource('st').setData({type: 'FeatureCollection', features: feats});
  M.map.getSource('trips').setData(v.trips ? v.trips() : EMPTY);
  const ord = v.orders ? v.orders() : EMPTY;
  M.map.getSource('orders').setData(ord);
  ordOpacidad(M.map, 1);
  M.pinned?.remove();
  for (const l of ['ord-hl-casing', 'ord-hl']) M.map.setFilter(l, ['all', ['==', ['geometry-type'], 'LineString'], ['==', ['get', 'fid'], -1]]);
  // Con órdenes en el mapa, los viajes de la foto bajan de tono para que las órdenes se lean.
  const hayOrdenes = ord.features.length > 0;
  M.map.setPaintProperty('trips-now', 'line-opacity', hayOrdenes ? TRIPS_OP.nowOrd : TRIPS_OP.now);
  M.map.setPaintProperty('trips-detour', 'line-opacity', hayOrdenes ? TRIPS_OP.detourOrd : TRIPS_OP.detour);
  M.map.getSource('zones').setData(zonesGeo(v));
  M.map.setFilter('st-sel', ['==', ['get', 'i'], S.sel ?? -1]);
  M.popup?.remove(); M.hit = -1;
}

function buildViews() {
  if (S.tab === 'replay' && S.replay.dayData && S.replay.sel.length) return S.replay.sel.map((a, j) => replayView(a, j));
  if (S.tab === 'prediccion' && S.snapshot) return [assignView()];
  return [nowView()];
}

function setCompare(on) {
  const stage = $('.stage');
  $('#mapLabel0').hidden = !on; $('#mapLabel1').hidden = !on;
  if (on && !MAPS[1]) ensureMap2();  // idempotente; reintenta si el primer mapa aún no estaba listo
  if (stage.classList.contains('compare') === on) return;
  stage.classList.toggle('compare', on);
  $('#main').classList.toggle('comparing', on);
  applyWidths();
  $('#map2').hidden = !on;
  requestAnimationFrame(() => MAPS.forEach(M => M?.map.resize()));
}

function flyTo(s) {
  if (!S.mapReady) return;
  const opts = {center: [+s.lon, +s.lat], zoom: Math.max(16, S.map.getZoom())};
  if (REDUCED) S.map.jumpTo(opts); else S.map.flyTo({...opts, speed: 1.6, curve: 1.3, essential: true});
}
function markSel() {
  MAPS.forEach(M => { if (M?.ready) M.map.setFilter('st-sel', ['==', ['get', 'i'], S.sel ?? -1]); });
}

function openStation(i, {fly = true, side = null} = {}) {
  if (!S.view?.stations[i]) return;
  S.sel = i;
  if (side !== null) S.cardSide = side;
  markSel();
  if (fly) flyTo(S.view.stations[i]);
  renderCard();
}
function closeCard() {
  $('#card').hidden = true;
  S.sel = null;
  markSel();
}
$('#cardClose').onclick = closeCard;

/** Enlaces externos de la estación (se abren en otra pestaña). */
const streetViewUrl = (lat, lon) => `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lon}`;
const mapsUrl = (lat, lon) => `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;
function placeLinks(s) {
  if (!Number.isFinite(+s.lat) || !Number.isFinite(+s.lon)) return '';
  return `<div class="card-links">
    <a class="btn sm" id="streetView" href="${streetViewUrl(+s.lat, +s.lon)}" target="_blank" rel="noopener">${icon('pano')}Ver en Street View<span class="sr-only"> (se abre en otra pestaña)</span></a>
    <a class="btn sm ghost" id="openMaps" href="${mapsUrl(+s.lat, +s.lon)}" target="_blank" rel="noopener">${icon('map')}Abrir en Google Maps<span class="sr-only"> (se abre en otra pestaña)</span></a>
  </div>`;
}
function renderCard() {
  const views = S.views.length ? S.views : [S.view];
  const side = Math.min(S.cardSide, views.length - 1);
  const v = views[side];
  if (S.sel === null || !v?.card) { $('#card').hidden = true; return; }
  const s = v.stations[S.sel];
  const c = v.card(S.sel);
  const num = $('#cardNum');
  num.textContent = s.short_name || '—';
  num.dataset.st = c.state || 'neutral';
  num.title = c.state ? ST_LABEL[c.state] : '';
  $('#cardTitle').textContent = s.name || `Estación ${s.short_name}`;
  const switcher = views.length > 1
    ? `<div class="segmented small card-side" role="tablist" aria-label="Escenario de la ficha">${views.map((vv, j) =>
      `<button role="tab" data-side="${j}" aria-selected="${j === side}" ${j === side ? '' : 'tabindex="-1"'}>${SIDE[j]} · ${esc(vv.label)}</button>`).join('')}</div>`
    : '';
  $('#cardBody').innerHTML = switcher + c.html + placeLinks(s);
  $('#card').hidden = false;
}
$('#cardBody').addEventListener('click', e => {
  const b = e.target.closest('.card-side [data-side]');
  if (b) { S.cardSide = +b.dataset.side; renderCard(); }
});

// ───────────────────────── leyenda y guía ─────────────────────────
// La leyenda completa va abajo de la barra izquierda; si la barra está plegada, una versión compacta
// flota sobre el mapa para que nunca se pierda.
const stateItems = (order, cls = '') => order.map(st => `<span class="legend-item"><i class="sw ${st}${cls}"></i>${ST_LABEL[st]}</span>`).join('');
function zoneLegend(compact) {
  if (!S.view?.zone || !S.zones.on[S.view.mode]) return '';
  if (compact) return `<div class="legend-row"><span class="legend-item"><i class="sw sq normal"></i>Zonas AGEB con los mismos colores</span></div>`;
  return `<div class="legend-zones" id="zoneLegend">
    <span class="legend-title">Zonas (AGEB urbanas del INEGI): estado sumando sus estaciones</span>
    <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' sq')}</div>
    <span>Mismos colores y cortes que las estaciones, con bicis y anclajes libres de la AGEB.</span>
  </div>`;
}
/** Leyenda de las líneas de órdenes que estén prendidas (la flecha marca el sentido: origen → destino). */
function ordersLegend(emitSel, arriveSel, reloc) {
  const e = $(emitSel)?.checked, a = $(arriveSel)?.checked;
  if (!e && !a) return '';
  const items = [
    e ? '<span class="legend-item"><i class="line-sw emit"></i>orden emitida: de dónde se recoge a dónde se entrega</span>' : '',
    a ? '<span class="legend-item"><i class="line-sw arrive"></i>se recogió o llegó en la foto: bicis movidas del origen al destino</span>' : '',
    a && reloc ? '<span class="legend-item"><i class="line-sw reloc"></i>no cupieron: del destino a la estación cercana</span>' : '',
  ].join('');
  return `<div class="legend-row">${items}</div>`;
}
function legendParts(mode) {
  const nums = !!S.nums[mode];
  const numChip = nums ? '<span class="legend-item"><i class="chip gray st-ink">12</i>bicis de la estación</span>' : '';
  if (mode === 'ahora') {
    return {
      full: `<span class="legend-title">Estado de la estación</span><div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena', 'inactiva'])}</div><span>El número del pin es de bicis para rentar. Pocas = 1 a 3.</span>`,
      compact: `<div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena', 'inactiva'])}</div>`,
    };
  }
  if (mode === 'replay') {
    const trips = $('#showTrips').checked, detours = $('#showDetours').checked;
    const lines = `${trips ? '<span class="legend-item"><i class="line-sw"></i>viajes que llegaron en esta foto</span>' : ''}${detours ? '<span class="legend-item"><i class="line-sw detour"></i>viaje desviado: de la estación buscada a la real</span>' : ''}`;
    const ord = ordersLegend('#showEmit', '#showArrive', true);
    const dam = $('#showDamaged').checked;
    return {
      full: `<span class="legend-title">Rebalanceo en esta foto</span>
        <div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>entregadas</span><span class="legend-item"><i class="chip minus">−N</i>recogidas</span></div>
        ${dam ? '<div class="legend-row"><span class="legend-item"><i class="chip pm">+N</i><i class="chip pm">−N</i>azul: pasan a rentables / a no rentables, sin moverse</span></div>' : ''}
        <span class="legend-title">Estado de la estación</span>
        <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}${numChip}</div>
        ${lines ? `<div class="legend-row">${lines}</div>` : ''}${ord}`,
      compact: `<div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>entregadas</span><span class="legend-item"><i class="chip minus">−N</i>recogidas</span>${dam ? '<span class="legend-item"><i class="chip pm">±</i>etiquetas</span>' : ''}</div>
        <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}</div>${lines ? `<div class="legend-row">${lines}</div>` : ''}${ord}`,
    };
  }
  if (mode === 'pronostico') {
    return {
      full: `<span class="legend-title">Estado proyectado, sin rebalanceo</span><div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'])}${numChip}</div><span>Con las bicis proyectadas al minuto elegido.</span>`,
      compact: `<div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'])}</div>`,
    };
  }
  const e = $('#showEmitA').checked, a = $('#showArriveA').checked;
  const ord = e || a ? `<div class="legend-row">${e ? '<span class="legend-item"><i class="line-sw emit"></i>mover: de dónde recoger (tenue) a dónde llevar (intenso)</span>' : ''}${a ? '<span class="legend-item"><i class="line-sw arrive"></i>órdenes que se recogen o llegan en este paso</span>' : ''}</div>` : '';
  return {  // asignación
    full: `<span class="legend-title">Qué mover en este paso</span>
      <div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>llevar aquí</span><span class="legend-item"><i class="chip minus">−N</i>recoger aquí</span></div>
      ${ord}
      <span class="legend-title">Estado actual de la estación</span>
      <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}${numChip}</div>`,
    compact: `<div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>llevar</span><span class="legend-item"><i class="chip minus">−N</i>recoger</span>${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}</div>${ord}`,
  };
}
function renderLegend() {
  const p = legendParts(S.view?.mode || 'ahora');
  $('#sideLegend').innerHTML = p.full + zoneLegend(false);
  $('#legend').innerHTML = p.compact + zoneLegend(true);
}

// ───────────────────────── paneles plegables ─────────────────────────
// El estado (plegado o no) se recuerda por pestaña.
const PANEL_KEY = 'ecobici.paneles';
let panelPrefs = {};
try { panelPrefs = JSON.parse(localStorage.getItem(PANEL_KEY) || '{}') || {}; } catch { panelPrefs = {}; }
function applyPanels() {
  const p = panelPrefs[S.tab] || {}, hasRight = S.tab !== 'ahora';
  const left = !!p.left, right = hasRight && !!p.right;
  const main = $('#main');
  main.classList.toggle('left-collapsed', left);
  main.classList.toggle('right-collapsed', right);
  $('#leftPanel').inert = left;
  $('#rightPanel').inert = right;
  const tl = $('#toggleLeft'), tr = $('#toggleRight');
  tl.setAttribute('aria-expanded', String(!left));
  $('#toggleLeftLabel').textContent = left ? 'Mostrar panel izquierdo' : 'Ocultar panel izquierdo';
  tl.title = `${left ? 'Mostrar' : 'Ocultar'} panel izquierdo ([)`;
  tr.hidden = !hasRight;
  $('#resizeRight').hidden = !hasRight;
  applyWidths();
  tr.setAttribute('aria-expanded', String(!right));
  $('#toggleRightLabel').textContent = right ? 'Mostrar panel derecho' : 'Ocultar panel derecho';
  tr.title = `${right ? 'Mostrar' : 'Ocultar'} panel derecho (])`;
}
// Ancho de los paneles: se arrastra el borde (o flechas sobre él); se recuerda por pestaña.
const PANEL_W = {
  left: {min: 240, max: 520, def: () => (innerWidth <= 1180 ? 288 : 320), key: () => 'lw'},
  right: {min: 280, max: 640, def: () => ($('#main').classList.contains('comparing') ? (innerWidth <= 1180 ? 400 : 452) : (innerWidth <= 1180 ? 300 : 340)),
    key: () => ($('#main').classList.contains('comparing') ? 'rwc' : 'rw')},
};
function panelWidth(side) {
  const v = (panelPrefs[S.tab] || {})[PANEL_W[side].key()];
  return Number.isFinite(v) ? v : PANEL_W[side].def();
}
function clampWidth(side, w) {
  const {min, max} = PANEL_W[side];
  const other = side === 'left' ? ($('#rightPanel').hidden ? 0 : $('#rightPanel').getBoundingClientRect().width) : $('#leftPanel').getBoundingClientRect().width;
  return Math.round(Math.max(min, Math.min(max, w, innerWidth - other - 320)));  // el mapa conserva al menos 320 px
}
function applyWidths() {
  const main = $('#main');
  const lw = panelWidth('left'), rw = panelWidth('right');
  main.style.setProperty('--left-w', `${lw}px`);
  main.style.setProperty(main.classList.contains('comparing') ? '--right-w-compare' : '--right-w', `${rw}px`);
  for (const [side, w] of [['left', lw], ['right', rw]]) {
    const h = $(side === 'left' ? '#resizeLeft' : '#resizeRight');
    h.setAttribute('aria-valuemin', PANEL_W[side].min);
    h.setAttribute('aria-valuemax', PANEL_W[side].max);
    h.setAttribute('aria-valuenow', w);
    h.setAttribute('aria-valuetext', `${w} píxeles`);
  }
}
function setPanelWidth(side, w, save = true) {
  const p = panelPrefs[S.tab] || (panelPrefs[S.tab] = {});
  if (w === null) delete p[PANEL_W[side].key()]; else p[PANEL_W[side].key()] = clampWidth(side, w);
  applyWidths();
  if (save) { try { localStorage.setItem(PANEL_KEY, JSON.stringify(panelPrefs)); } catch { /* solo dura esta visita */ } }
}
function bindResizer(handle, side) {
  let start = null;
  handle.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    start = {x: e.clientX, w: panelWidth(side)};
    handle.setPointerCapture(e.pointerId);
    $('#main').classList.add('resizing');
    e.preventDefault();
  });
  handle.addEventListener('pointermove', e => {
    if (!start) return;
    const dx = e.clientX - start.x;
    setPanelWidth(side, start.w + (side === 'left' ? dx : -dx), false);
  });
  const end = () => {
    if (!start) return;
    start = null;
    $('#main').classList.remove('resizing');
    setPanelWidth(side, panelWidth(side));  // guarda
    requestAnimationFrame(() => MAPS.forEach(M => M?.map.resize()));
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
  handle.addEventListener('dblclick', () => { setPanelWidth(side, null); requestAnimationFrame(() => MAPS.forEach(M => M?.map.resize())); });
  handle.addEventListener('keydown', e => {
    const grow = side === 'left' ? 1 : -1, step = e.shiftKey ? 48 : 16;
    let w = null;
    if (e.key === 'ArrowRight') w = panelWidth(side) + grow * step;
    else if (e.key === 'ArrowLeft') w = panelWidth(side) - grow * step;
    else if (e.key === 'Home') w = PANEL_W[side].min;
    else if (e.key === 'End') w = PANEL_W[side].max;
    if (w === null) return;
    setPanelWidth(side, w);
    requestAnimationFrame(() => MAPS.forEach(M => M?.map.resize()));
    e.preventDefault();
  });
}
bindResizer($('#resizeLeft'), 'left');
bindResizer($('#resizeRight'), 'right');

function togglePanel(side) {
  const p = panelPrefs[S.tab] || (panelPrefs[S.tab] = {});
  p[side] = !p[side];
  try { localStorage.setItem(PANEL_KEY, JSON.stringify(panelPrefs)); } catch { /* sin almacenamiento: solo dura esta visita */ }
  applyPanels();
}
$('#toggleLeft').onclick = () => togglePanel('left');
$('#toggleRight').onclick = () => togglePanel('right');
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest('input, select, textarea')) return;
  if (e.key === '[') { togglePanel('left'); e.preventDefault(); }
  else if (e.key === ']' && S.tab !== 'ahora') { togglePanel('right'); e.preventDefault(); }
});
// El mapa (o los dos) se ajusta mientras la columna se abre o se cierra.
let resizeRaf = 0;
new ResizeObserver(() => {
  cancelAnimationFrame(resizeRaf);
  resizeRaf = requestAnimationFrame(() => MAPS.forEach(M => M?.map.resize()));
}).observe($('.stage'));

// "Bicis por estación": bolitas de color ↔ número con fondo blanco; independiente por pestaña.
$$('[data-nums]').forEach(cb => {
  cb.onchange = () => { S.nums[cb.dataset.nums] = cb.checked; renderMap(); };
});
/** Propiedades de una estación como bolita de estado (o número, si "Bicis por estación" está prendido). */
function stateMarker(mode, st, bikes, op) {
  const nums = !!S.nums[mode];
  return {color: ST_COLOR[st], op, img: nums ? `num|${st}|${bikes}` : '', anchor: nums ? 'num' : 'dot'};
}

const HELP = {
  ahora: ['Cada pin es una estación; el número son las bicis que se pueden rentar ahora mismo.',
    'El color dice el estado: <b>vacía</b>, <b>pocas</b> (1 a 3), <b>normal</b>, <b>llena</b> (sin anclajes libres) o <b>inactiva</b>.',
    'Busca por número o nombre, o pica un pin, para ver todos los datos que publica Ecobici de esa estación.',
    'Los datos del feed se actualizan solos cada 30 segundos. <b>Zonas de la ciudad</b> colorea las AGEB urbanas del INEGI con el mismo estado que los pines, sumando sus estaciones.',
    'Las flechas de los bordes del mapa (o las teclas [ y ]) ocultan y muestran los paneles; la leyenda queda sobre el mapa.'],
  replay: ['Reproduce un día ya simulado en fotos cada 15 minutos, desde las 05:00. Elige el día y uno o dos escenarios (políticas de rebalanceo); con dos, los mapas se ven lado a lado y se mueven juntos.',
    'En el mapa, <span class="k-plus">+N</span> son bicis entregadas y <span class="k-minus">−N</span> recogidas; en azul, <span class="k-pm">+N/−N</span> son bicis que pasan a rentables o a no rentables sin moverse. Cada estación es una bolita con el color de su estado; <b>Bicis por estación</b> muestra en su lugar el número de bicis.',
    'El panel derecho da cada número de la foto y su acumulado desde las 05:00. La ficha de cada estación muestra su cuenta: bicis anteriores − salidas + llegadas + entregadas − recogidas ± etiquetas = bicis actuales.',
    '<b>Zonas de la ciudad</b> colorea las AGEB urbanas del INEGI con el mismo estado que los pines (vacía, pocas, normal, llena), sumando sus estaciones; cambia con cada foto.',
    '<b>Órdenes emitidas</b> dibuja, en la foto en que se emitieron, una línea naranja de cada estación donde se recoge a la estación donde se entrega (la flecha marca el sentido). <b>Órdenes que llegaron</b> dibuja lo que el simulador realmente movió en la foto (azul): los traslados que se recogieron y los que se entregaron y, punteado, las bicis que no cupieron y se dejaron en la estación cercana. Pasa o pica una línea para ver origen, destino, bicis y horas.',
    'Pica una estación para ver su cuenta. Teclado: ← y → mueven 15 minutos, la barra espaciadora reproduce o pausa, y [ y ] ocultan los paneles.'],
  asignacion: ['Elige el pronóstico, cuántas horas emitir órdenes y el costo por km (α), y pulsa <b>Iniciar</b>. El asignador (greedy) decide cada 15 minutos qué bicis mover y de dónde a dónde.',
    'La barra de arriba tiene un paso por cada 15 minutos de la sesión. Se va llenando conforme corre; puedes regresar a cualquier paso ya calculado (← y →). Al lado dice cuánto lleva corriendo y cuánto le falta.',
    'A la derecha, <b>Mover</b> es la lista de trabajo del paso: de qué estación recoger, a cuál llevar, cuántas bicis y a qué hora. Pica una fila para ir a esa línea en el mapa.',
    '<b>Órdenes</b> es el historial: todas las órdenes hasta el paso elegido, por cuarto de hora. En el mapa, <b>Órdenes actuales</b> dibuja cada traslado de origen (tenue) a destino (intenso). <b>Órdenes aplicadas en el paso</b> dibuja los traslados emitidos antes que se recogen o se entregan en este paso.',
    'Cada orden recoge 15 minutos después de emitirse y entrega una hora después. Es una demo: Ecobici sigue operando y nadie ejecuta estas órdenes.',
    'El cálculo vive en el servidor: puedes recargar la página sin perder la sesión. Al detenerla o terminar, puedes iniciar otra.'],
};
function helpKey() { return S.tab === 'prediccion' ? 'asignacion' : S.tab; }
function renderHelp() {
  const key = helpKey();
  const titles = {ahora: 'Ahora', replay: 'Replay', asignacion: 'Asignación'};
  $('#helpTitle').textContent = `Cómo usar ${titles[key]}`;
  $('#helpBody').innerHTML = HELP[key].map(t => `<li>${t}</li>`).join('');
}
function toggleHelp(open) {
  const el = $('#help');
  open = open ?? el.hidden;
  if (open) renderHelp();
  el.hidden = !open;
  $('#helpBtn').setAttribute('aria-expanded', String(open));
  if (open) $('#helpClose').focus();
}
$('#helpBtn').onclick = () => toggleHelp();
$('#helpClose').onclick = () => { toggleHelp(false); $('#helpBtn').focus(); };
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!$('#help').hidden) { toggleHelp(false); $('#helpBtn').focus(); }
  else if (!$('#card').hidden) closeCard();
});
document.addEventListener('click', e => {
  if (!$('#help').hidden && !e.target.closest('#help') && !e.target.closest('#helpBtn')) toggleHelp(false);
});

// ───────────────────────── pestañas ─────────────────────────
function setTabs(listSel, attr, value) {
  $$(`${listSel} [role="tab"]`).forEach(b => {
    const on = b.dataset[attr] === value;
    b.setAttribute('aria-selected', String(on));
    b.tabIndex = on ? 0 : -1;
  });
}
/** Flechas izquierda/derecha entre pestañas de un tablist. */
function arrowNav(listEl, onPick) {
  listEl.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
    const tabs = $$('[role="tab"]', listEl);
    let j = tabs.indexOf(document.activeElement);
    if (j < 0) return;
    j = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (j + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[j].focus(); onPick(tabs[j]);
    e.preventDefault();
  });
}

function activateTab(tab, {fromHash = false} = {}) {
  if (!['ahora', 'replay', 'prediccion'].includes(tab)) tab = 'ahora';
  if (S.tab === 'replay' && tab !== 'replay') pause();
  S.tab = tab;
  setTabs('.tabs', 'tab', tab);
  $$('.view').forEach(v => { v.hidden = v.id !== `view-${tab}`; });
  $('#main').dataset.tab = tab;
  const right = tab !== 'ahora';
  $('#rightPanel').hidden = !right;
  $('#main').classList.toggle('has-right', right);
  $('#right-replay').hidden = tab !== 'replay';
  $('#replayDock').hidden = tab !== 'replay';
  updatePredLayout();
  applyPanels();
  if (!$('#help').hidden) renderHelp();
  if (S.mapReady) requestAnimationFrame(() => MAPS.forEach(M => M?.map.resize()));
  if (!fromHash) writeHash();
  if (tab === 'replay') enterReplay();
  if (tab === 'prediccion') enterPrediccion();
  renderMap();
}
$$('.tabs [role="tab"]').forEach(b => { b.onclick = () => activateTab(b.dataset.tab); });
arrowNav($('.tabs'), b => activateTab(b.dataset.tab));

function writeHash() {
  let h = S.tab;
  if (S.tab === 'replay' && S.replay.arm) h += `/${S.replay.arm.day}/${S.replay.sel.map(a => a.arm).join('+')}/${S.replay.k}`;
  if (S.tab === 'prediccion') h += '/asignacion';
  history.replaceState(null, '', `#${h}`);
}
function readHash() {
  const [tab, ...rest] = decodeURIComponent(location.hash.slice(1)).split('/');
  return {tab: tab || 'ahora', rest};
}

// ───────────────────────── Ahora ─────────────────────────
async function loadSnapshot() {
  const fs = $('#feedStatus');
  try {
    const data = await api('/api/snapshot', {timeout: 20000});
    S.snapshot = data;
    const st = data.stations || [];
    const total = st.reduce((n, s) => n + (+s.bikes || 0), 0);
    const active = st.filter(s => s.renting && s.installed).length;
    $('#totalBikes').textContent = fmt(total);
    $('#totalSub').textContent = `en ${fmt(active)} estaciones activas de ${fmt(st.length)}`;
    const when = data.last_updated ? new Date(data.last_updated * 1000).toLocaleTimeString('es-MX', {hour: '2-digit', minute: '2-digit', hour12: false}) : '—';
    fs.dataset.state = data.stale ? 'stale' : 'ok';
    $('#feedText').textContent = data.stale ? `Feed sin actualizar desde las ${when}` : `Feed de las ${when}`;
    fs.title = 'Hora de la última foto publicada por Ecobici';
    if (S.view?.mode === 'ahora' || S.view?.mode === 'asignacion' || !S.view) {
      const keep = S.sel !== null ? S.view?.stations[S.sel]?.short_name : null;
      renderMap();
      if (keep) { const j = S.view.stations.findIndex(s => s.short_name === keep); if (j >= 0) { S.sel = j; renderCard(); markSel(); } }
    }
    if ($('#q').value) search();
  } catch (e) {
    fs.dataset.state = 'error';
    $('#feedText').textContent = 'Feed no disponible';
    if (!S.snapshot) {
      $('#totalBikes').textContent = '—';
      $('#totalSub').textContent = `${e.message} Se reintenta en 30 s.`;
    }
  }
}

function nowView() {
  const st = S.snapshot?.stations || [];
  const state = s => stationState(+s.bikes, +s.docks, s.renting && s.installed);
  return {
    mode: 'ahora', stations: st,
    props: i => {
      const s = st[i], k = state(s);
      return {color: ST_COLOR[k], img: `pin|${k}|${s.bikes}`, anchor: 'bottom', dimg: '', sort: k === 'inactiva' ? 0 : 1};
    },
    zone: i => (st[i].renting && st[i].installed) ? {b: +st[i].bikes, cap: +st[i].capacity, docks: +st[i].docks} : null,
    hover: i => `${plural(st[i].bikes, 'bici', 'bicis')} · ${ST_LABEL[state(st[i])]}`,
    card: i => ({state: state(st[i]), html: nowCard(st[i])}),
  };
}

const RAW_LABELS = {
  station_id: 'ID de estación', external_id: 'ID externo', name: 'Nombre', short_name: 'Número', lat: 'Latitud', lon: 'Longitud',
  address: 'Dirección', cross_street: 'Cruce', post_code: 'Código postal', region_id: 'Región', capacity: 'Capacidad',
  rental_methods: 'Formas de pago', has_kiosk: 'Tiene kiosco', is_charging: 'Estación de carga',
  electric_bike_surcharge_waiver: 'Sin cargo extra por bici eléctrica', eightd_has_key_dispenser: 'Dispensador de llaves 8D',
  eightd_has_available_keys: 'Llaves 8D disponibles', num_bikes_available: 'Bicis disponibles', num_ebikes_available: 'Bicis eléctricas disponibles',
  num_bikes_disabled: 'Bicis no rentables', num_docks_available: 'Anclajes libres', num_docks_disabled: 'Anclajes fuera de servicio',
  is_installed: 'Instalada', is_renting: 'Presta bicis', is_returning: 'Recibe bicis', last_reported: 'Último reporte',
  vehicle_types_available: 'Tipos de bici disponibles', station_type: 'Tipo de estación', rental_uris: 'Enlaces de renta',
};
const RAW_ORDER = Object.keys(RAW_LABELS);
const RAW_TIME = new Set(['last_reported', 'last_updated']);
const RAW_BOOL01 = /^is_|^has_|^eightd_/;
function rawValue(key, v) {
  if (v === null || v === undefined || v === '') return '—';
  if (RAW_TIME.has(key) && Number.isFinite(+v) && +v > 1e9) return unixLabel(+v);
  if (typeof v === 'boolean') return v ? 'Sí' : 'No';
  if (RAW_BOOL01.test(key) && (v === 0 || v === 1)) return v ? 'Sí' : 'No';
  if (Array.isArray(v)) return v.length ? v.map(x => typeof x === 'object' ? JSON.stringify(x) : x).join(', ') : '—';
  if (typeof v === 'object') return JSON.stringify(v);
  if (typeof v === 'number' && (key === 'lat' || key === 'lon')) return v.toFixed(6);
  if (typeof v === 'number') return fmt(v);
  return v;
}
function nowCard(s) {
  const raw = s.raw;
  let table;
  if (raw && typeof raw === 'object') {
    const keys = Object.keys(raw).sort((a, b) => {
      const ia = RAW_ORDER.indexOf(a), ib = RAW_ORDER.indexOf(b);
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib) || a.localeCompare(b);
    });
    table = `<dl class="kv" id="rawFields">${keys.map(k => `<dt>${RAW_LABELS[k] ? esc(RAW_LABELS[k]) : `<code>${esc(k)}</code>`}</dt><dd data-key="${esc(k)}">${esc(rawValue(k, raw[k]))}</dd>`).join('')}</dl>`;
  } else {
    table = `<div class="note warn">${icon('alert')}<span>El servidor no envió los datos completos de esta estación.</span></div>`;
  }
  return `<div class="card-stats">
      <div class="card-stat hero"><b>${fmt(s.bikes)}</b><span>bicis disponibles para rentar</span></div>
      <div class="card-stat"><b>${fmt(s.bikes_disabled)}</b><span>no rentables</span></div>
      <div class="card-stat"><b>${fmt(s.docks)}</b><span>anclajes libres de ${fmt(s.capacity)}</span></div>
    </div>
    <section><h3>Todos los datos del feed</h3>${table}</section>`;
}

// buscador
let hitIdx = -1;
function search() {
  const q = norm($('#q').value.trim());
  const list = $('#hits');
  hitIdx = -1;
  if (!q) { list.innerHTML = ''; $('#q').setAttribute('aria-expanded', 'false'); return; }
  const st = S.snapshot?.stations || [];
  if (!st.length) { list.innerHTML = '<li class="none">El feed aún no carga.</li>'; return; }
  const scored = [];
  st.forEach((s, i) => {
    const sn = norm(s.short_name), nm = norm(s.name);
    let score = -1;
    if (sn === q || sn.replace(/^0+/, '') === q.replace(/^0+/, '')) score = 0;
    else if (sn.startsWith(q)) score = 1;
    else if (nm.includes(q)) score = 2 + nm.indexOf(q) / 1000;
    if (score >= 0) scored.push([score, i]);
  });
  scored.sort((a, b) => a[0] - b[0]);
  const top = scored.slice(0, 8);
  $('#q').setAttribute('aria-expanded', 'true');
  if (!top.length) { list.innerHTML = `<li class="none">Ninguna estación coincide con “${esc($('#q').value.trim())}”.</li>`; return; }
  list.innerHTML = top.map(([, i], j) => {
    const s = st[i], k = stationState(+s.bikes, +s.docks, s.renting && s.installed);
    return `<li><button class="result" role="option" id="hit-${j}" data-i="${i}" aria-selected="false">
      <span class="badge-num" data-st="${k}" title="${ST_LABEL[k]}">${esc(s.short_name)}</span>
      <span class="rname">${esc(bare(s.name))}</span>
      <span class="rbikes"><b>${fmt(s.bikes)}</b> bicis</span></button></li>`;
  }).join('');
  $$('.result', list).forEach(b => { b.onclick = () => pickHit(+b.dataset.i); });
}
function pickHit(i) {
  if (S.tab !== 'ahora') activateTab('ahora');
  if (S.view?.mode !== 'ahora') renderMap();
  openStation(i);
}
$('#q').addEventListener('input', search);
$('#q').addEventListener('keydown', e => {
  const items = $$('#hits .result');
  if (!items.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    hitIdx = (hitIdx + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items.forEach((b, j) => b.setAttribute('aria-selected', String(j === hitIdx)));
    $('#q').setAttribute('aria-activedescendant', items[hitIdx].id);
    e.preventDefault();
  } else if (e.key === 'Enter') {
    pickHit(+items[Math.max(0, hitIdx)].dataset.i);
    e.preventDefault();
  }
});

// ───────────────────────── órdenes: de dónde a dónde ─────────────────────────
/** Agrupa órdenes [{id, delta, paq, key, ex}] en paquetes: un receptor con sus donantes.
 *  Las órdenes sin paquete (las de Ecobici) no forman pares y se muestran como siempre. */
function packagesOf(orders) {
  const by = new Map();
  for (const o of orders) {
    if (!o.paq) continue;
    const id = `${o.key}|${o.paq}`;
    const g = by.get(id) || {paq: o.paq, key: o.key, recv: null, donors: []};
    if (o.delta > 0) g.recv = {id: o.id, n: o.delta, ex: o.ex}; else g.donors.push({id: o.id, n: -o.delta, ex: o.ex});
    by.set(id, g);
  }
  return [...by.values()].filter(g => g.recv && g.donors.length).sort((a, b) => a.paq - b.paq);
}
/** Opacidad de las líneas de órdenes × f (f < 1 las atenúa mientras otra está resaltada; con degradado, el degradado ya trae la suya). */
function ordOpacidad(map, f) {
  for (const [l, base] of [['ord-emit', ORD.emitOp[0]], ['ord-arrive', ORD.arriveOp[0]], ['ord-reloc', 0.9]]) {
    if (ORD.degradado && l !== 'ord-reloc') continue;
    map.setPaintProperty(l, 'line-opacity', base * f);
  }
}
let ORD_FID = 0;  // identifica cada línea para resaltarla al pasar el cursor
/** Línea origen → destino para el mapa; `tip` es la ficha que sale al pasar o pulsar. */
function ordFeature(st, o, d, props, tip) {
  if (o == null || d == null || o === d || st.lon[o] == null || st.lon[d] == null) return null;
  const A = [st.lon[o], st.lat[o]], B = [st.lon[d], st.lat[d]];
  return {type: 'Feature', properties: {...props, tip, fid: ORD_FID++}, geometry: {type: 'LineString', coordinates: arco(A, B, ORD.curva)}};
}
/** Arco suave (bézier cuadrática) de A a B que se curva siempre a la izquierda del sentido: A → B y B → A no se encinman.
 *  `curva` es la flecha del arco como fracción del largo; 0 = recta. */
function arco(A, B, curva) {
  if (!curva) return [A, B];
  const k = Math.cos(((A[1] + B[1]) / 2) * Math.PI / 180);  // longitud en grados → mismas unidades que la latitud
  const dx = (B[0] - A[0]) * k, dy = B[1] - A[1];
  const cx = (A[0] + B[0]) / 2 * k - dy * curva, cy = (A[1] + B[1]) / 2 + dx * curva;
  const pts = [];
  for (let i = 0; i <= 20; i++) {
    const t = i / 20, u = 1 - t;
    pts.push([(u * u * A[0] * k + 2 * u * t * cx + t * t * B[0] * k) / k, u * u * A[1] + 2 * u * t * cy + t * t * B[1]]);
  }
  return pts;
}
/** FeatureCollection de las órdenes: las líneas y, al final de cada una, su punto de destino (para el estilo con degradado). */
function ordCollection(feats) {
  const ends = feats.map(f => ({type: 'Feature', properties: {k: 'end', a: f.properties.a, fid: f.properties.fid},
    geometry: {type: 'Point', coordinates: f.geometry.coordinates.at(-1)}}));
  return {type: 'FeatureCollection', features: [...feats, ...ends]};
}
const bikesTxt = n => plural(n, 'bici', 'bicis');
const hhmmOf = t => String(t ?? '').slice(-5);

/** Paquetes emitidos en la foto k del Replay (decision.orders = [estación, delta, paquete]). */
function replayPackages(a, k) {
  return packagesOf((a.decision?.[k]?.orders || []).map(([i, d, q]) => ({id: i, delta: d, paq: q, key: k})));
}
/** Pares ejecutados que se aplicaron en la foto k: [paquete, origen, destino, bicis, tipo (1 = reubicación), dist_m, foto emitida,
 * foto de recogida, foto de entrega]. Los que se entregan en k y los traslados que se recogen en k (estos viven en la foto de su entrega). */
function replayArrived(a, k) {
  if (!a.pares) return [];
  if (!a._recogidas) {
    a._recogidas = a.pares.map(() => []);
    for (const fr of a.pares) for (const x of fr) if (!x[4] && x[7] != null && x[7] !== x[8] && a._recogidas[x[7]]) a._recogidas[x[7]].push(x);
  }
  return (a.pares[k] || []).concat(a._recogidas[k] || []);
}
/** El par se recogió en la foto k y aún va en camino (no se entrega en k). */
const recogidoEn = (x, k) => x[8] != null && x[8] !== k;

function replayOrders(a) {
  const emit = $('#showEmit').checked, arrive = $('#showArrive').checked;
  if (!emit && !arrive) return EMPTY;
  const dd = S.replay.dayData, k = S.replay.k, T = dd.times, ss = dd._stations, feats = [];
  const nm = i => `${ss[i].short_name} · ${esc(bare(ss[i].name))}`;
  const deliv = a.spec?.delivery || 60;
  const add = f => { if (f) feats.push(f); };
  if (emit) {
    for (const g of replayPackages(a, k)) for (const dn of g.donors) {
      add(ordFeature(dd.stations, dn.id, g.recv.id, {k: 'emit', a: 'emit', n: dn.n},
        `Recoge ${bikesTxt(dn.n)} en ${nm(dn.id)} → entrega en ${nm(g.recv.id)}<small>Paquete ${g.paq} · recoge a las ${esc(addMin(T[k], 15))} · entrega a las ${esc(addMin(T[k], deliv))}</small>`));
    }
  }
  if (arrive) {
    for (const x of replayArrived(a, k)) {
      const [q, o, d, n, tipo, m, ki] = x;
      const pick = addMin(T[ki], 15), when = addMin(T[ki], deliv);
      add(recogidoEn(x, k)
        ? ordFeature(dd.stations, o, d, {k: 'arrive', a: 'arrive', n},
          `Se recogieron ${bikesTxt(n)} en ${nm(o)} para ${nm(d)}<small>Paquete ${q} · recogidas a las ${esc(pick)} · se entregan a las ${esc(when)}</small>`)
        : tipo
        ? ordFeature(dd.stations, o, d, {k: 'reloc', a: 'arrive', n},
          `No cupieron ${bikesTxt(n)} en ${nm(o)}: dejadas en ${nm(d)}${m == null ? '' : ` (${fmt(m)} m)`}<small>Paquete ${q} · dejadas a las ${esc(when)}</small>`)
        : ordFeature(dd.stations, o, d, {k: 'arrive', a: 'arrive', n},
          `Llegaron ${bikesTxt(n)} de ${nm(o)} a ${nm(d)}<small>Paquete ${q} · recogidas a las ${esc(pick)} · entregadas a las ${esc(when)}</small>`));
    }
  }
  return ordCollection(feats);
}

/** Filas de la lista de órdenes emitidas de un paquete: encabezado + una fila por donante. */
function pkgRowsEmitidas(g, name, pickAt, delivAt) {
  const x = g.recv.n;
  const head = `<li class="pkg">Paquete ${g.paq} <small>· entrega ${bikesTxt(x)} en ${esc(name(g.recv.id))} a las ${esc(delivAt)}</small></li>`;
  const rows = g.donors.map(dn => `<li><button class="row" data-i="${dn.id}">${actChip('recoger')}
      <span class="r-main"><span class="r-title">Recoge en ${esc(name(dn.id))}</span><span class="r-sub">→ entrega en ${esc(name(g.recv.id))} · recoge a las ${esc(pickAt)}</span></span>
      <span class="r-end">${bikesTxt(dn.n)}</span></button></li>`);
  return [head, ...rows];
}

// ───────────────────────── Replay ─────────────────────────
const ARM_TEXT = {
  sin_rebalanceo: 'Nadie mueve bicis: solo ocurren los viajes del día.',
  ecobici: 'Los movimientos que hizo Ecobici ese día, inferidos de las fotos del feed.',
  ma_diaria: 'El asignador decide con un procedimiento greedy cada 15 min, con un pronóstico de media móvil del mismo tipo de día, hecho a las 05:00.',
  lgbm_diario: 'El asignador decide con un procedimiento greedy cada 15 min, con un pronóstico LightGBM del día completo, hecho a las 05:00.',
  lgbm_directo: 'El asignador decide con un procedimiento greedy cada 15 min, con un pronóstico LightGBM de las próximas horas que usa lo observado en el día.',
  oraculo_diario: 'El asignador (greedy) conoce los viajes reales del día desde las 05:00. Es un techo de referencia, no una estrategia.',
  oraculo_directo: 'El asignador (greedy) conoce los viajes reales de las próximas horas. Es un techo de referencia, no una estrategia.',
};
const MAX_ARMS = 2;

function enterReplay() {
  if (!S.replay.index) loadReplayIndex();
}
async function loadReplayIndex() {
  showNote('#rerror', null);
  try {
    const idx = await api('/api/replay/index');
    S.replay.index = idx;
    const days = Object.keys(idx.days || {}).sort();
    if (!days.length) throw new ApiError('No hay días precalculados. Corre: uv run python -m ecosim.replay', 404);
    $('#rday').innerHTML = days.map(d => `<option value="${esc(d)}">${esc(dayLabel(d))}</option>`).join('');
    const h = readHash();
    if (h.tab === 'replay' && days.includes(h.rest[0])) $('#rday').value = h.rest[0];
    fillArms(h.tab === 'replay' && h.rest[1] ? h.rest[1].split('+') : []);
    fillAlfa();
    $('#rday').disabled = false;
    if (idx.stale) showNote('#rerror', 'Este replay se calculó con otros parámetros congelados. Vuelve a precalcularlo para que coincida.', {kind: 'warn'});
    const k0 = h.tab === 'replay' ? +h.rest[2] : 0;
    await loadReplay(Number.isFinite(k0) ? k0 : 0);
  } catch (e) {
    $('#rday').innerHTML = '<option>Sin datos</option>';
    $('#rarms').innerHTML = '';
    showNote('#rerror', e.message, {retry: loadReplayIndex});
    if (S.tab === 'replay') mapState(null);
  }
}

/** Lista de escenarios con casillas: se eligen 1 o 2 (el orden de elección da A y B). */
function fillArms(prefer = []) {
  const arms = S.replay.index.days?.[$('#rday').value]?.arms || {};
  const keys = Object.keys(arms);
  let pick = (prefer.length ? prefer : S.replay.keys).filter(k => arms[k]).slice(0, MAX_ARMS);
  if (!pick.length) pick = [arms[S.replay.index.best_real] ? S.replay.index.best_real : keys[0]].filter(Boolean);
  S.replay.keys = pick;
  $('#rarms').innerHTML = keys.map(k => `<label class="arm-opt">
      <input type="checkbox" value="${esc(k)}"><span class="arm-name">${esc(arms[k].label || k)}</span><span class="arm-tag" aria-hidden="true"></span></label>`).join('');
  $$('#rarms input').forEach(cb => { cb.onchange = () => toggleArm(cb); });
  syncArmsUI();
}
/** α disponibles en el día elegido (0 siempre; las variantes existen solo en los brazos con asignador). */
function alfasDelDia() {
  const v = S.replay.index?.days?.[$('#rday').value]?.alfas || {};
  return [0, ...new Set(Object.values(v).flatMap(o => Object.keys(o).map(Number)))].sort((x, y) => x - y);
}
function fillAlfa() {
  const al = alfasDelDia();
  if (!al.includes(S.replay.alfa)) S.replay.alfa = 0;
  $('#ralfaField').hidden = al.length < 2;
  $('#ralfa').innerHTML = al.map(x => `<option value="${x}">${x === 0 ? '0 (sin costo por km)' : x}</option>`).join('');
  $('#ralfa').value = String(S.replay.alfa);
}
$('#ralfa').onchange = () => { S.replay.alfa = +$('#ralfa').value; loadReplay(S.replay.k); };
function syncArmsUI() {
  const keys = S.replay.keys, full = keys.length >= MAX_ARMS;
  $$('#rarms input').forEach(cb => {
    const j = keys.indexOf(cb.value), lab = cb.closest('.arm-opt');
    cb.checked = j >= 0;
    cb.disabled = j < 0 && full;
    lab.classList.toggle('disabled', cb.disabled);
    lab.dataset.side = keys.length > 1 && j >= 0 ? SIDE[j] : '';
    $('.arm-tag', lab).textContent = lab.dataset.side;
    lab.title = cb.disabled ? 'Máximo 2 escenarios: quita uno para elegir otro.' : '';
  });
  $('#rarmsHint').textContent = full ? 'Comparando A y B lado a lado. Quita uno para volver a un solo mapa.' : '';
}
function toggleArm(cb) {
  let keys = S.replay.keys.slice();
  if (cb.checked) {
    if (keys.length >= MAX_ARMS) { cb.checked = false; return; }
    keys.push(cb.value);
  } else {
    if (keys.length === 1) { cb.checked = true; toast('Deja al menos un escenario elegido.'); return; }
    keys = keys.filter(k => k !== cb.value);
  }
  S.replay.keys = keys;
  syncArmsUI();
  loadReplay(S.replay.k);
}
$('#rday').onchange = () => { fillArms(); fillAlfa(); loadReplay(S.replay.k); };
$('#showTrips').onchange = () => renderMap();
$('#showDetours').onchange = () => renderMap();
for (const id of ['#showDamaged', '#showEmit', '#showArrive', '#showEmitA', '#showArriveA']) $(id).onchange = () => renderMap();

/** Sumas desde la primera foto: acumulado de cada número de snap[k] (suma de lo que manda la API). */
const ACC_FIELDS = ['emitidas', 'recogidas', 'entregadas', 'salidas', 'llegadas', 'desvios_salida', 'desvios_llegada', 'a_rentable', 'a_no_rentable', 'E', 'F'];
function accumulate(a) {
  if (!Array.isArray(a.snap)) { a._acc = null; return; }
  const run = Object.fromEntries(ACC_FIELDS.map(f => [f, 0]));
  a._acc = a.snap.map(s => { for (const f of ACC_FIELDS) run[f] += +s?.[f] || 0; return {...run}; });
}

async function loadReplay(k = 0) {
  pause();
  const day = $('#rday').value, keys = S.replay.keys.slice();
  if (!day || !keys.length) return;
  const token = ++S.replay.token;
  showNote('#rerror', null);
  if (S.tab === 'replay') mapState(keys.length > 1 ? 'Cargando escenarios…' : 'Cargando escenario…', 'busy');
  $('#rk').disabled = true;
  try {
    const dayP = S.replay.days[day] || api(`/api/replay/${encodeURIComponent(day)}/dia`);
    S.replay.days[day] = dayP;
    const alfa = S.replay.alfa;
    const armPs = keys.map(arm => {
      // Con α > 0, los brazos con asignador usan su variante `<brazo>@a<α>`; Ecobici y "sin rebalanceo" no cambian.
      const file = alfa && S.replay.index.days?.[day]?.alfas?.[arm]?.[alfa] ? `${arm}@a${alfa}` : arm;
      const id = `${day}/${file}`;
      if (!S.replay.arms[id]) {
        S.replay.arms[id] = api(`/api/replay/${encodeURIComponent(day)}/${encodeURIComponent(file).replace('%40', '@')}`).then(a => {
          if (!Array.isArray(a?.bikes)) throw new ApiError('El archivo de replay no tiene el formato esperado. Vuelve a precalcular.');
          const out = {...a, day, arm, alfa: +a.alfa || 0};
          accumulate(out);
          return out;
        });
        S.replay.arms[id].catch(() => { delete S.replay.arms[id]; });
      }
      return S.replay.arms[id];
    });
    const [dd, ...arms] = await Promise.all([dayP, ...armPs]);
    if (token !== S.replay.token) return;  // respuesta vieja
    if (!dd?.stations?.short_name) throw new ApiError('El archivo del día no tiene el formato esperado. Vuelve a precalcular.');
    for (const a of arms) {
      if (dd.stations.short_name.length !== a.bikes[0]?.length) throw new ApiError(`${a.label || a.arm}: el día y el escenario no coinciden en estaciones. Vuelve a precalcular.`);
    }
    const n = dd.n_frames || dd.times.length;
    if (!dd._stations) {
      const st = dd.stations;
      dd._stations = st.short_name.map((sn, i) => ({short_name: sn, name: st.name[i], lat: st.lat[i], lon: st.lon[i]}));
    }
    S.replay.dayData = dd;
    S.replay.sel = arms;
    S.replay.side = Math.min(S.replay.side, arms.length - 1);
    S.cardSide = Math.min(S.cardSide, arms.length - 1);
    S.replay.k = Math.max(0, Math.min(n - 1, k || 0));
    const r = $('#rk');
    r.max = n - 1; r.value = S.replay.k; r.disabled = false;
    renderTicks('#rticks', dd.times, n);
    renderSpec();
    const noSnap = arms.filter(a => !Array.isArray(a.snap)).map(a => a.label || a.arm);
    if (noSnap.length) showNote('#rerror', `Sin desglose por foto (snap) en: ${noSnap.join(', ')}. Vuelve a precalcular con la versión actual.`, {kind: 'warn'});
    mapState(null);
    renderReplay();
  } catch (e) {
    if (token !== S.replay.token) return;
    delete S.replay.days[day];
    S.replay.dayData = null; S.replay.sel = [];
    mapState(null);
    showNote('#rerror', e.message, {retry: () => loadReplay(k)});
    renderReplayPanel();
    renderMap();
  }
}

function renderTicks(sel, times, n) {
  const el = $(sel);
  const marks = [];
  for (let k = 0; k < n; k++) {
    const t = times[k];
    if (t && t.endsWith(':00') && (+t.slice(0, 2)) % 3 === 2) marks.push([k, t.slice(0, 2) + ' h']);
  }
  el.innerHTML = marks.map(([k, t]) => `<span style="left:${(k / (n - 1 || 1)) * 100}%">${t}</span>`).join('');
}

function specHtml(a) {
  const sp = a.spec || {};
  const parts = [];
  if (sp.n) parts.push(['Horizonte', `${sp.n} h`]);
  if (sp.lam) parts.push(['λ', fmt(Math.round(sp.lam * 100) / 100)]);
  if (sp.n) parts.push(['Recoge', 't+15 min'], ['Entrega', `t+${sp.delivery || 60} min`]);
  if (a.alfa > 0) parts.push(['α', `${fmt(a.alfa)} min-estación/km`]);
  if (sp.forma && sp.train_start) parts.push(['Entrenado', `${sp.train_start} a ${sp.train_end}`]);
  if (a.final?.EF !== undefined) parts.push(['E+F del día', fmt(a.final.EF)]);
  return `<p>${esc(ARM_TEXT[a.arm] || a.label || a.arm)}</p>
    <div class="params">${parts.map(([k, v]) => `<span class="param">${esc(k)} <b>${esc(v)}</b></span>`).join('')}</div>`;
}
function renderSpec() {
  const arms = S.replay.sel;
  $('#rspec').innerHTML = arms.length > 1
    ? arms.map((a, j) => `<div class="spec-side"><span class="side-tag">${SIDE[j]}</span><div><b class="spec-name">${esc(a.label || a.arm)}</b>${specHtml(a)}</div></div>`).join('')
    : specHtml(arms[0]);
}

function stepReplay(d) {
  if (!S.replay.arm) return;
  const n = +$('#rk').max;
  S.replay.k = Math.max(0, Math.min(n, S.replay.k + d));
  $('#rk').value = S.replay.k;
  renderReplay();
}
$('#prev').onclick = () => { pause(); stepReplay(-1); };
$('#next').onclick = () => { pause(); stepReplay(1); };
$('#rk').oninput = () => { S.replay.k = +$('#rk').value; renderReplay(); };
function pause() {
  clearInterval(S.replay.timer); S.replay.timer = null;
  $('#play').innerHTML = icon('play');
  $('#play').setAttribute('aria-label', 'Reproducir'); $('#play').title = 'Reproducir (espacio)';
}
function play() {
  if (!S.replay.arm) return;
  if (S.replay.k >= +$('#rk').max) { S.replay.k = 0; $('#rk').value = 0; renderReplay(); }
  $('#play').innerHTML = icon('pause');
  $('#play').setAttribute('aria-label', 'Pausar'); $('#play').title = 'Pausar (espacio)';
  S.replay.timer = setInterval(() => {
    if (S.replay.k >= +$('#rk').max) { pause(); return; }
    stepReplay(1);
  }, 1100);
}
$('#play').onclick = () => (S.replay.timer ? pause() : play());
document.addEventListener('keydown', e => {
  if (S.tab !== 'replay' || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.target.closest('input, select, textarea, [role="tab"], [role="separator"], .help')) return;
  if (e.key === 'ArrowLeft') { pause(); stepReplay(-1); e.preventDefault(); }
  else if (e.key === 'ArrowRight') { pause(); stepReplay(1); e.preventDefault(); }
  else if (e.key === ' ' && !e.target.closest('button, a')) { S.replay.timer ? pause() : play(); e.preventDefault(); }
});

function renderReplay() {
  const dd = S.replay.dayData, arms = S.replay.sel;
  if (!dd || !arms.length) return;
  const k = S.replay.k, n = +$('#rk').max + 1;
  $('#rtime').textContent = dd.times[k] || '—';
  $('#rstep').textContent = `Foto ${k + 1} de ${n}`;
  setRangeFill($('#rk'));
  $('#prev').disabled = k <= 0;
  $('#next').disabled = k >= n - 1;
  arms.forEach((a, j) => { $(`#mapLabel${j}`).innerHTML = `<span class="side-tag">${SIDE[j]}</span> ${esc(a.label || a.arm)}${a.alfa > 0 ? ` · α ${fmt(a.alfa)}` : ''}`; });
  renderReplayPanel();
  renderMap();
  if (S.sel !== null) renderCard();
  writeHash();
}

/** Fila de est[k] por estación: [i, entregadas, recogidas, a_rentable, a_no_rentable, salidas, llegadas, devueltas?]. */
function estRow(a, k, i) {
  const rows = a?.est?.[k];
  if (!rows) return null;
  if (!rows._by) { rows._by = new Map(); rows.forEach(r => rows._by.set(r[0], r)); }
  return rows._by.get(i) || [i, 0, 0, 0, 0, 0, 0, 0];
}

function replayView(a, side) {
  const dd = S.replay.dayData, k = S.replay.k;
  const st = dd._stations, bikes = a.bikes[k], dis = a.dis?.[k] || [];
  const cap = dd.stations.cap, dk = dd.stations.docks_disabled || [], oos = dd.stations.out_of_service || [];
  const hasEst = Array.isArray(a.est?.[k]);
  return {
    mode: 'replay', stations: st, label: a.label || a.arm,
    props: i => {
      let dimg = '', dlow = '';
      if (hasEst) {
        const r = estRow(a, k, i);
        const parts = [];
        if (r[1] > 0) parts.push(`+${r[1]}`);
        if (r[2] > 0) parts.push(`−${r[2]}`);
        const net = r[3] - r[4];  // a_rentable − a_no_rentable: cambio neto de etiqueta en sitio
        if (net !== 0) parts.push(`L${net > 0 ? '+' : '−'}${Math.abs(net)}`);
        if (parts.length) dimg = `d|${parts.join('|')}`;
        const low = parts.filter(p => p[0] !== 'L');
        dlow = low.length ? `d|${low.join('|')}` : '';
        if (!$('#showDamaged').checked) dimg = dlow;  // sin "Cambios de dañadas": solo entregadas y recogidas
      }
      // Punto con el color de estado de los pines, translúcido para que resalte el rebalanceo.
      const free = cap[i] - bikes[i] - (dis[i] || 0) - (dk[i] || 0);
      const stt = stationState(bikes[i], free, !oos[i]);
      // Sin "Bicis por estación": bolita del color de estado, translúcida. Con "Bicis por estación": el número sobre fondo blanco,
      // con el tono del estado, a cualquier zoom (donde se encimen queda la bolita).
      return {...stateMarker('replay', stt, bikes[i], 0.45), dimg, dlow, sort: dlow ? 3 : dimg ? 2 : 1};
    },
    zone: i => oos[i] ? null : {b: bikes[i], cap: cap[i], docks: Math.max(0, cap[i] - bikes[i] - (dis[i] || 0) - (dk[i] || 0))},
    hover: i => `${plural(bikes[i], 'bici', 'bicis')} · ${fmt(dis[i])} no rentables · ${ST_LABEL[stationState(bikes[i], cap[i] - bikes[i] - (dis[i] || 0) - (dk[i] || 0), !oos[i])]}`,
    card: i => ({state: null, html: replayCard(a, i)}),
    trips: () => replayTrips(a),
    orders: () => replayOrders(a),
  };
}

/** Viajes de la foto: los que llegaron en (t_k−1, t_k], y los desvíos de esa foto (de la estación buscada a la real).
 *  Cada grupo tiene su interruptor y aplica a los dos mapas al comparar. */
function replayTrips(a) {
  const showTrips = $('#showTrips').checked, showDetours = $('#showDetours').checked;
  if (!showTrips && !showDetours) return EMPTY;
  const dd = S.replay.dayData, k = S.replay.k;
  const t = dd.trips, st = dd.stations, feats = [];
  const line = (o, d, kind) => {
    if (o < 0 || d < 0 || o === d || st.lon[o] == null || st.lon[d] == null) return;
    feats.push({type: 'Feature', properties: {k: kind}, geometry: {type: 'LineString', coordinates: [[st.lon[o], st.lat[o]], [st.lon[d], st.lat[d]]]}});
  };
  if (showTrips && k > 0) {
    const hi = 15 * k, lo = hi - 15;
    for (let j = 0; j < t.o.length; j++) {
      const arr = t.arr[j];
      if (arr > lo && arr <= hi) line(t.o[j], t.d[j], 'now');
    }
  }
  if (showDetours) for (const [, , io, ir] of a.desvios?.[k] || []) line(io, ir, 'detour');
  return {type: 'FeatureCollection', features: feats};
}

/** Km en línea recta de cada tramo donante → receptor ejecutado, por foto de entrega (de `pares` y las coordenadas del día). */
function kmTramos(a) {
  const dd = S.replay.dayData, st = dd.stations;
  if (a._km?.day !== dd.day) {
    const rad = x => x * Math.PI / 180;
    const dist = (o, d) => {
      if (st.lat[o] == null || st.lat[d] == null) return null;
      const h = Math.sin(rad(st.lat[d] - st.lat[o]) / 2) ** 2 + Math.cos(rad(st.lat[o])) * Math.cos(rad(st.lat[d])) * Math.sin(rad(st.lon[d] - st.lon[o]) / 2) ** 2;
      return 2 * 6371 * Math.asin(Math.sqrt(h));
    };
    const n = dd.times.length, km = Array(n).fill(0), bkm = Array(n).fill(0), bikes = Array(n).fill(0);
    (a.pares || []).forEach((fr, k) => { for (const [, o, d, b, tipo] of fr) { if (tipo) continue; const x = dist(o, d); if (x == null) continue; km[k] += x; bkm[k] += x * b; bikes[k] += b; } });
    const cum = v => v.reduce((r, x, i) => (r.push((r[i - 1] || 0) + x), r), []);
    a._km = {day: dd.day, km, bkm, bikes, ckm: cum(km), cbkm: cum(bkm), cbikes: cum(bikes)};
  }
  return a._km;
}

// Filas del panel derecho: [clave, etiqueta, marca]. Las filas de una sola celda son títulos de grupo.
const PANEL_ROWS = [
  ['Rebalanceo'],
  ['emitidas', 'Órdenes emitidas', ''],
  ['km_tramos', 'Km de tramos (donante → receptor)', '', 1],
  ['km_por_bici', 'Km por bici', '', 2],
  ['recogidas', 'Bicis recogidas', 'minus'],
  ['entregadas', 'Bicis entregadas', 'plus'],
  ['Viajes'],
  ['salidas', 'Iniciados', 'trip'],
  ['llegadas', 'Terminados', 'trip'],
  ['desvios', 'Desviados', 'detour'],
  ['Etiquetas, sin mover bicis'],
  ['a_rentable', 'No rentable → rentable', 'pm'],
  ['a_no_rentable', 'Rentable → no rentable', 'pm'],
  ['Minutos-estación sin servicio'],
  ['E', 'Vacías (E)', ''],
  ['F', 'Llenas (F)', ''],
  ['EF', 'E + F', ''],
];
/** Valor de la foto k (acc=false) o acumulado desde la primera foto (acc=true). E+F acumulado viene del backend. */
function snapVal(a, k, key, acc) {
  if (key === 'km_tramos' || key === 'km_por_bici') {
    if (!a.pares) return null;
    const m = kmTramos(a);
    if (key === 'km_tramos') return acc ? m.ckm[k] : m.km[k];
    const b = acc ? m.cbikes[k] : m.bikes[k];
    return b ? (acc ? m.cbkm[k] : m.bkm[k]) / b : null;
  }
  if (acc) {
    if (key === 'EF') return a.snap?.[k]?.EF_acum;
    const r = a._acc?.[k];
    if (!r) return null;
    return key === 'desvios' ? r.desvios_salida + r.desvios_llegada : r[key];
  }
  const s = a.snap?.[k];
  if (!s) return null;
  return key === 'desvios' ? (s.desvios_salida || 0) + (s.desvios_llegada || 0) : s[key];
}
/** Aviso solo si una foto NO cuadra (el backend lo prueba en todas; no debería verse). */
function cuadreAlert(arms, k) {
  const bad = arms.map((a, j) => [a, j]).filter(([a]) => a.snap?.[k] && a.snap[k].cuadre_ok === false);
  if (!bad.length) return '';
  return `<div class="note error" role="alert" id="cuadreAlert">${icon('alert')}<span>${bad.map(([a, j]) =>
    `${arms.length > 1 ? `${SIDE[j]} · esta` : 'Esta'} foto no cuadra en ${plural(a.snap[k].descuadre_estaciones, 'estación', 'estaciones')}`).join('; ')}. Revisa la ficha de cada estación para ver su cuenta.</span></div>`;
}

function renderReplayPanel() {
  const dd = S.replay.dayData, arms = S.replay.sel;
  if (!dd || !arms.length) {
    $('#rpTitle').textContent = 'Esta foto'; $('#rpSub').textContent = ' ';
    $('#cuadres').innerHTML = '';
    $('#rpStats').innerHTML = '<p class="rows"><span class="none">Elige un día y un escenario.</span></p>';
    $('#rpList').innerHTML = ''; $('#rpSide').hidden = true;
    return;
  }
  const k = S.replay.k, multi = arms.length > 1, T = dd.times;
  $('#rpTitle').textContent = k === 0 ? `Inicio del día, ${T[0]}` : `De ${T[k - 1]} a ${T[k]}`;
  $('#rpSub').textContent = k === 0 ? 'Estado inicial; los acumulados empiezan aquí.' : `Esta foto y lo acumulado desde las ${T[0]}.`;
  $('#cuadres').innerHTML = cuadreAlert(arms, k);
  if (arms.every(a => !a.snap?.[k])) {
    $('#rpStats').innerHTML = `<div class="note warn">${icon('alert')}<span>Este replay no trae los números de cada foto. Vuelve a precalcularlo.</span></div>`;
  } else {
    const head = multi
      ? `<thead><tr><th rowspan="2"><span class="sr-only">Concepto</span></th>${arms.map((a, j) => `<th colspan="2" class="side-h" scope="colgroup"><span class="side-tag">${SIDE[j]}</span> <span class="side-name">${esc(a.label || a.arm)}</span></th>`).join('')}</tr>
         <tr>${arms.map(() => '<th scope="col">Foto</th><th scope="col">Acum.</th>').join('')}</tr></thead>`
      : `<thead><tr><th><span class="sr-only">Concepto</span></th><th scope="col">Esta foto</th><th scope="col">Desde ${esc(T[0])}</th></tr></thead>`;
    const cols = 1 + arms.length * 2;
    const body = PANEL_ROWS.map(row => {
      if (row.length === 1) return `<tr class="grp"><th colspan="${cols}" scope="colgroup">${esc(row[0])}</th></tr>`;
      const [key, label, mk, dec] = row;
      const f = v => dec ? fmtDec(v, dec) : fmt(v);
      const cells = arms.map((a, j) => `<td class="foto" data-side="${j}">${f(snapVal(a, k, key, false))}</td><td class="acum" data-side="${j}">${f(snapVal(a, k, key, true))}</td>`).join('');
      return `<tr data-stat="${key}"><th scope="row">${mk ? `<i class="mk ${mk}"></i>` : ''}${esc(label)}</th>${cells}</tr>`;
    }).join('');
    const sysRows = [['min_desde_anterior', 'Minutos desde la foto anterior'], ['bicis_sistema', 'Bicis en el sistema'], ['en_viaje', 'Bicis en viaje'], ['en_camioneta', 'Bicis en camioneta']]
      .filter(([key]) => arms.some(a => a.snap?.[k]?.[key] !== undefined));
    const sys = `<table class="nums sys"><thead><tr><th scope="col">Al momento de la foto</th>${multi ? arms.map((a, j) => `<th scope="col"><span class="side-tag">${SIDE[j]}</span></th>`).join('') : '<th><span class="sr-only">Valor</span></th>'}</tr></thead><tbody>
      ${sysRows.map(([key, label]) => `<tr data-stat="${key}"><th scope="row">${esc(label)}</th>${arms.map((a, j) => `<td data-side="${j}">${fmt(a.snap?.[k]?.[key])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    $('#rpStats').innerHTML = `<table class="nums${multi ? ' multi' : ''}" id="rpTable">${head}<tbody>${body}</tbody></table>${sys}`;
  }
  // Selector A/B de las listas
  $('#rpSide').hidden = !multi;
  if (multi) {
    $$('#rpSide [role="tab"]').forEach((b, j) => { b.innerHTML = `<span class="side-tag">${SIDE[j]}</span> ${esc(arms[j].label || arms[j].arm)}`; });
    setTabs('#rpSide', 'side', String(S.replay.side));
  }
  const a = arms[S.replay.side] || arms[0];
  const counts = {
    emitidas: a.decision?.[k]?.orders?.length || 0,
    aplicadas: (a.applied?.[k] || []).filter(o => o[2] !== 0).length,
    llegaron: replayArrived(a, k).length,
    desvios: a.desvios?.[k]?.length || 0,
  };
  $$('#rpTabs [role="tab"]').forEach(b => {
    const base = {emitidas: 'Emitidas', aplicadas: 'Aplicadas', llegaron: 'Llegaron', desvios: 'Desviados'}[b.dataset.list];
    b.innerHTML = `${base}<span class="n">${fmt(counts[b.dataset.list])}</span>`;
  });
  renderReplayList();
}
function pickReplayList(b) { S.replay.list = b.dataset.list; setTabs('#rpTabs', 'list', S.replay.list); renderReplayList(); }
$$('#rpTabs [role="tab"]').forEach(b => { b.onclick = () => pickReplayList(b); });
arrowNav($('#rpTabs'), pickReplayList);
function pickReplaySide(b) { S.replay.side = +b.dataset.side; renderReplayPanel(); }
$$('#rpSide [role="tab"]').forEach(b => { b.onclick = () => pickReplaySide(b); });
arrowNav($('#rpSide'), pickReplaySide);

const MAX_ROWS = 80;
function rowsHtml(rows, empty) {
  if (!rows.length) return `<li class="none">${esc(empty)}</li>`;
  const more = rows.length > MAX_ROWS ? `<li class="more">Y ${fmt(rows.length - MAX_ROWS)} más en esta foto.</li>` : '';
  return rows.slice(0, MAX_ROWS).join('') + more;
}
function actChip(kind) {
  if (kind === 'recoger') return `<span class="act minus">${icon('up')}Recoger</span>`;
  if (kind === 'entregar') return `<span class="act plus">${icon('down')}Entregar</span>`;
  return '';
}
/** Nombre sin el prefijo "CE-123" que ya va en el número de estación. */
const bare = name => String(name ?? '').replace(/^[A-Z]{2,3}-[\d-]+\s+/, '').trim();
function renderReplayList() {
  const dd = S.replay.dayData, a = S.replay.sel[S.replay.side] || S.replay.arm;
  const el = $('#rpList');
  if (!dd || !a) { el.innerHTML = ''; return; }
  const k = S.replay.k, st = dd._stations, T = dd.times, side = S.replay.sel.indexOf(a);
  const name = i => st[i] ? `${st[i].short_name} · ${bare(st[i].name)}` : 'Estación desconocida';
  let rows = [], empty = '';
  if (S.replay.list === 'emitidas') {
    const deliv = a.spec?.delivery || 60;
    const pk = replayPackages(a, k);
    const orders = (a.decision?.[k]?.orders || []).filter(o => !o[2]).sort((x, y) => x[1] - y[1]);
    rows = pk.flatMap(g => pkgRowsEmitidas(g, name, addMin(T[k], 15), addMin(T[k], deliv)));
    rows.push(...orders.map(([i, d]) => `<li><button class="row" data-i="${i}">${actChip(d < 0 ? 'recoger' : 'entregar')}
      <span class="r-main"><span class="r-title">${esc(name(i))}</span><span class="r-sub">${d < 0 ? `Recoge a las ${esc(addMin(T[k], 15))}` : `Entrega a las ${esc(addMin(T[k], a.spec?.delivery || 60))}`}</span></span>
      <span class="r-end">${plural(Math.abs(d), 'bici', 'bicis')}</span></button></li>`));
    empty = 'No se emitieron órdenes en esta foto.';
  } else if (S.replay.list === 'aplicadas') {
    const ap = (a.applied?.[k] || []).filter(o => o[2] !== 0).slice().sort((x, y) => x[2] - y[2]);
    rows = ap.map(([i, d, real, ik]) => `<li><button class="row" data-i="${i}">${actChip(real < 0 ? 'recoger' : 'entregar')}
      <span class="r-main"><span class="r-title">${esc(name(i))}</span><span class="r-sub">Emitida a las ${esc(T[ik] ?? '—')}${real !== d ? ` · pedía ${fmt(Math.abs(d))}, se aplicaron ${fmt(Math.abs(real))}` : ''}</span></span>
      <span class="r-end">${plural(Math.abs(real), 'bici', 'bicis')}</span></button></li>`);
    empty = 'No se aplicaron órdenes en esta foto.';
  } else if (S.replay.list === 'llegaron') {
    const deliv = a.spec?.delivery || 60;
    const arr = replayArrived(a, k);
    const seen = new Set();
    for (const x of arr.slice().sort((x, y) => x[6] - y[6] || x[0] - y[0] || x[4] - y[4])) {
      const [q, o, d, n, tipo, m, ki] = x, pk = recogidoEn(x, k);
      if (!seen.has(`${ki}|${q}`)) {
        seen.add(`${ki}|${q}`);
        rows.push(`<li class="pkg">Paquete ${q} <small>· emitido a las ${esc(T[ki] ?? '—')}, ${pk ? `recogido a las ${esc(addMin(T[ki], 15))}, se entrega a las` : 'llegó a las'} ${esc(addMin(T[ki], deliv))}</small></li>`);
      }
      rows.push(pk
        ? `<li><button class="row" data-i="${o}"><span class="act minus">${icon('up')}Recogidas</span>
            <span class="r-main"><span class="r-title">${esc(name(o))}</span><span class="r-sub">→ ${esc(name(d))} · se entregan a las ${esc(addMin(T[ki], deliv))}</span></span>
            <span class="r-end">${bikesTxt(n)}</span></button></li>`
        : tipo
        ? `<li><button class="row" data-i="${d}"><span class="act reloc">${icon('down')}Dejadas</span>
            <span class="r-main"><span class="r-title">En ${esc(name(d))}</span><span class="r-sub">No cupieron en ${esc(name(o))}${m == null ? '' : ` · a ${fmt(m)} m`}</span></span>
            <span class="r-end">${bikesTxt(n)}</span></button></li>`
        : `<li><button class="row" data-i="${d}"><span class="act plus">${icon('down')}Llegaron</span>
            <span class="r-main"><span class="r-title">${esc(name(o))}</span><span class="r-sub">→ ${esc(name(d))} · recogidas a las ${esc(addMin(T[ki], 15))}</span></span>
            <span class="r-end">${bikesTxt(n)}</span></button></li>`);
    }
    empty = a.pares ? 'No se recogió ni llegó ninguna orden con camioneta en esta foto.' : 'Este replay no trae los pares ejecutados.';
  } else {
    const dv = a.desvios?.[k] || [];
    rows = dv.map(([, kind, io, ir, m]) => `<li><button class="row" data-i="${ir}"><span class="act detour">${kind === 'salida' ? 'Salida' : 'Llegada'}</span>
      <span class="r-main"><span class="r-title">${esc(name(ir))}</span><span class="r-sub">${kind === 'salida' ? 'Quería salir de' : 'Quería llegar a'} ${esc(st[io]?.short_name ?? '—')} ${esc(bare(st[io]?.name))}</span></span>
      <span class="r-end">${fmt(Math.round(m))} m</span></button></li>`);
    empty = a.desvios ? 'Ningún viaje se desvió en esta foto.' : 'Este replay no trae la lista de desvíos.';
  }
  el.innerHTML = rowsHtml(rows, empty);
  $$('.row', el).forEach(b => { b.onclick = () => openStation(+b.dataset.i, {side: Math.max(0, side)}); });
}

function replayCard(a, i) {
  const dd = S.replay.dayData, k = S.replay.k, T = dd.times;
  const st = dd.stations;
  const b = a.bikes[k][i], d = a.dis?.[k]?.[i] ?? 0;
  const cap = st.cap[i], dk = st.docks_disabled?.[i] || 0;
  const free = Math.max(0, cap - b - d - dk);
  let html = `<div class="card-stats">
      <div class="card-stat hero"><b>${fmt(b)}</b><span>bicis a las ${esc(T[k])}</span></div>
      <div class="card-stat"><b>${fmt(d)}</b><span>no rentables</span></div>
      <div class="card-stat"><b>${fmt(free)}</b><span>anclajes libres de ${fmt(cap)}</span></div>
    </div>`;
  const r = estRow(a, k, i);
  const prev = k > 0 ? a.bikes[k - 1][i] : a.inicial?.bikes?.[i];
  if (!r) {
    html += `<div class="note warn">${icon('alert')}<span>Este replay no trae el detalle por estación para mostrar el cuadre.</span></div>`;
  } else if (prev === undefined) {
    html += `<div class="note">${icon('info')}<span>Primera foto del día: no hay foto anterior con qué cuadrar.</span></div>`;
  } else {
    const [, ent, rec, ar, anr, sal, lle, dev] = r;
    const calc = prev - sal + lle + ent - rec + (dev || 0) + ar - anr;
    const ok = calc === b;
    const line = (label, v, sign, cls = '') => `<div class="eq-row"><span>${label}</span><b class="${cls}">${sign}${fmt(v)}</b></div>`;
    html += `<section><h3>${k > 0 ? `De ${esc(T[k - 1])} a ${esc(T[k])}` : 'Antes de la primera foto'}</h3>
      <div class="equation" id="cuadreEstacion">
        ${line(k > 0 ? `Bicis a las ${esc(T[k - 1])}` : 'Bicis al inicio', prev, '')}
        ${line('Salidas de viajes', sal, '− ')}
        ${line('Llegadas de viajes', lle, '+ ')}
        ${line('Entregadas por rebalanceo', ent, '+ ', 'plus')}
        ${line('Recogidas por rebalanceo', rec, '− ', 'minus')}
        ${r.length > 7 ? line('Devueltas al origen', dev, '+ ') : ''}
        ${line('No rentable → rentable', ar, '+ ', 'pm')}
        ${line('Rentable → no rentable', anr, '− ', 'pm')}
        <div class="eq-row total"><span>Resultado</span><b>${fmt(calc)}</b></div>
        <span class="eq-verdict ${ok ? 'ok' : 'bad'}">${icon(ok ? 'check' : 'alert')}${ok ? `Cuadra con las ${fmt(b)} bicis de la foto` : `No cuadra: la foto tiene ${fmt(b)} bicis`}</span>
      </div></section>`;
  }
  const dv = (a.desvios?.[k] || []).filter(x => x[3] === i);
  if (dv.length) html += `<p class="rp-sub" style="margin:0">${plural(dv.length, 'viaje desviado llegó o salió aquí', 'viajes desviados llegaron o salieron aquí')} en esta foto.</p>`;
  return html;
}

// ───────────────────────── Predicción: asignación en vivo ─────────────────────────
// Una sola vista: se elige pronóstico y α, se inicia, y la barra de arriba recorre los pasos ya calculados.
const PRON_KEY = sid => `ecobici.asignacion.pronostico.${sid}`;
function updatePredLayout() {
  const on = S.tab === 'prediccion';
  $('#right-asignacion').hidden = !on;
  $('#assignDock').hidden = !(on && (S.live.session?.pasos?.length || 0) > 0);
  $('.stage').classList.toggle('with-dock', !$('#replayDock').hidden || !$('#assignDock').hidden);
  clearInterval(S.live.clock);
  S.live.clock = on ? setInterval(renderAssignDock, 30000) : null;
}

function enterPrediccion() {
  if (!S.live.models) loadModels();
  if (!S.live.sessionId) {
    try { S.live.sessionId = localStorage.getItem(SESSION_KEY); } catch { /* sin almacenamiento */ }
    if (S.live.sessionId) { loadForecastOf(S.live.sessionId); pollSession(); }
  }
  renderAssign();
}

async function loadModels() {
  showNote('#aerror', null);
  try {
    const m = await api('/api/live/models');
    S.live.models = m;
    $('#lastPublished').textContent = shortDay(m.datos?.ultimo_dia_publicado);
    $('#modelsUpdated').textContent = dateTimeLabel(m.datos?.actualizado);
    const ms = m.modelos || [];
    const firstOk = ms.find(x => x.disponible)?.key;
    $('#models').innerHTML = ms.map(x => {
      const corte = corteText(x.corte);
      const note = x.disponible ? (x.nota || '') : (x.motivo || 'No disponible.');
      return `<label class="choice${x.disponible ? '' : ' disabled'}">
        <input type="radio" name="model" value="${esc(x.key)}" ${x.disponible ? '' : 'disabled'} ${x.key === firstOk ? 'checked' : ''}>
        <span class="c-name">${esc(x.label || x.key)}<small>${x.forma === 'directa' ? 'próximas horas' : 'día completo'}</small></span>
        <span class="c-note">${esc(note)}${x.disponible && corte ? `<br><span class="c-train">${esc(corte)}</span>` : ''}</span></label>`;
    }).join('') || '<p class="empty">El servidor no reporta modelos.</p>';
    $('#startAssign').disabled = !firstOk;
    renderAssign();
  } catch (e) {
    $('#lastPublished').textContent = '—'; $('#modelsUpdated').textContent = '—';
    $('#models').innerHTML = '';
    $('#startAssign').disabled = true;
    showNote('#aerror', `No se pudieron cargar los modelos. ${e.message}`, {retry: loadModels});
  }
}

/** Rango de datos con que se armó el modelo. */
function corteText(c) {
  if (!c) return '';
  if (c.train_start) return `Viajes ${shortDay(c.train_start)} – ${shortDay(c.train_end)}`;
  return c.train_end ? `Sin entrenamiento; viajes hasta ${shortDay(c.train_end)}` : (c.nota || '');
}
function modelInfo(key) { return S.live.models?.modelos?.find(m => m.key === key); }
/** Horizonte del pronóstico que usa la asignación: el más largo que ofrece el modelo (día completo o 4 h). */
function assignHorizon(mi) {
  const hz = (Array.isArray(mi?.horizontes) && mi.horizontes.length ? mi.horizontes : mi?.forma === 'directa' ? [1, 2, 3, 4] : ['dia']).map(String);
  return hz.includes('dia') ? 'dia' : hz.sort((a, b) => b - a)[0];
}
/** Lo que se guarda del pronóstico de una sesión (para verlo en la pestaña Pronóstico del panel, también tras recargar). */
function loadForecastOf(sid) {
  try { S.live.forecast = JSON.parse(localStorage.getItem(PRON_KEY(sid)) || 'null'); } catch { S.live.forecast = null; }
}

// ── controles ──
const LIVE_ALFAS = [0, 1, 3, 5, 7, 10];
for (const id of ['#aalfa', '#salfa']) $(id).innerHTML = LIVE_ALFAS.map(x => `<option value="${x}">${x === 0 ? '0 (sin costo por km)' : x}</option>`).join('');
$('#salfa').onchange = async () => {
  const id = S.live.sessionId, v = $('#salfa').value;
  if (!id) return;
  try {
    await api(`/api/live/assign/${encodeURIComponent(id)}/alfa?${new URLSearchParams({alfa: v})}`, {method: 'POST'});
    toast(`α = ${v}: aplica desde la siguiente decisión.`);
    pollSession();
  } catch (e) { showNote('#aerror', e.message); $('#salfa').value = String(S.live.session?.params?.alfa ?? 0); }
};
$('#startAssign').onclick = async () => {
  const model = $('input[name="model"]:checked')?.value;
  if (!model) { showNote('#aerror', 'Elige un pronóstico disponible.'); return; }
  const btn = $('#startAssign');
  showNote('#aerror', null);
  try {
    setBusy(btn, true, 'Pronosticando…');
    const f = await api(`/api/live/forecast?${new URLSearchParams({model, horizonte: assignHorizon(modelInfo(model))})}`, {method: 'POST', timeout: 180000});
    if (!f?.id) throw new ApiError('El servidor no devolvió el pronóstico.');
    setBusy(btn, true, 'Iniciando…');
    const r = await api(`/api/live/assign/start?${new URLSearchParams({forecast_id: f.id, horas: $('#ahours').value, alfa: $('#aalfa').value})}`, {method: 'POST', timeout: 180000});
    if (!r?.session_id) throw new ApiError('El servidor no devolvió una sesión.');
    clearTimeout(S.live.poll);
    S.live.forecast = {model: f.model, issued_at: f.issued_at, t_feed: f.t_feed, minutes: f.minutes?.at(-1), riesgos: f.riesgos || []};
    S.live.sessionId = r.session_id; S.live.session = null; S.live.step = -1; S.live.follow = true;
    try {
      localStorage.setItem(SESSION_KEY, r.session_id);
      localStorage.setItem(PRON_KEY(r.session_id), JSON.stringify(S.live.forecast));
    } catch { /* sin almacenamiento */ }
    renderAssign(); renderMap();
    await pollSession();
    toast('Asignación iniciada. Sigue corriendo aunque recargues la página.');
  } catch (e) {
    showNote('#aerror', e.message, {retry: () => $('#startAssign').click()});
  } finally {
    setBusy(btn, false, 'Iniciar');
  }
};
$('#stopAssign').onclick = async () => {
  const btn = $('#stopAssign');
  setBusy(btn, true, 'Deteniendo…');
  try {
    await api(`/api/live/assign/${encodeURIComponent(S.live.sessionId)}/stop`, {method: 'POST'});
    await pollSession();
    toast('Asignación detenida. Puedes iniciar otra.');
  } catch (e) {
    showNote('#aerror', e.message);
  } finally {
    setBusy(btn, false, 'Detener');
  }
};

async function pollSession() {
  clearTimeout(S.live.poll);
  const id = S.live.sessionId;
  if (!id) return;
  try {
    const s = await api(`/api/live/assign/${encodeURIComponent(id)}`, {timeout: 20000});
    if (id !== S.live.sessionId) return;
    const grew = (s.pasos?.length || 0) !== (S.live.session?.pasos?.length || 0);
    S.live.session = s;
    showNote('#aerror', s.estado === 'error' ? (s.error || s.detalle || 'La sesión terminó con error en el servidor.') : null);
    renderAssign();
    if (grew || S.view?.mode !== 'asignacion') renderMap();
    if (s.estado === 'corriendo') S.live.poll = setTimeout(pollSession, 10000);
  } catch (e) {
    if (e.status === 404) {
      S.live.sessionId = null; S.live.session = null;
      try { localStorage.removeItem(SESSION_KEY); } catch { /* sin almacenamiento */ }
      renderAssign(); renderMap();
      return;
    }
    showNote('#aerror', `Sin respuesta de la sesión: ${e.message} Se reintenta en 15 s.`);
    S.live.poll = setTimeout(pollSession, 15000);
  }
}

// ── panel izquierdo: formulario y sesión ──
const ESTADO_TXT = {corriendo: 'Corriendo', terminada: 'Terminada', detenida: 'Detenida', error: 'Error'};
function renderAssign() {
  const sess = S.live.session, id = S.live.sessionId;
  const running = sess?.estado === 'corriendo' || (id && !sess);
  $('#session').hidden = !id;
  $('#assignForm').hidden = !!running;  // detenida o terminada: se puede iniciar otra
  $('#assignFormTitle').hidden = !id;
  if (id) {
    const pill = $('#sessState');
    pill.dataset.s = sess?.estado || '';
    pill.textContent = ESTADO_TXT[sess?.estado] || 'Conectando…';
    $('#stopAssign').hidden = sess?.estado !== 'corriendo';
    const mi = modelInfo(sess?.model);
    const rows = sess ? [
      ['Pronóstico', mi?.label || sess.model || '—'],
      ['Costo por km (α)', fmt(sess.params?.alfa ?? 0)],
      ['Inicio', hhmm(sess.inicio)],
      ['Emite órdenes hasta', hhmm(sess.fin_emision)],
    ] : [];
    $('#salfaField').hidden = sess?.estado !== 'corriendo';
    if (sess?.estado === 'corriendo') $('#salfa').value = String(sess.params?.alfa ?? 0);
    $('#sessInfo').innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('') ||
      '<div><dt>Sesión</dt><dd><span class="skeleton w-8"></span></dd></div>';
    const n = sess?.pasos?.length || 0;
    if (n) S.live.step = S.live.follow || S.live.step < 0 ? n - 1 : Math.min(S.live.step, n - 1);
  }
  updatePredLayout();
  renderAssignDock();
  renderAssignPanel();
}

// ── barra de pasos (arriba del mapa) ──
const STEP_MIN = 15;
const toDate = iso => (iso ? new Date(iso) : null);
const addMins = (d, m) => new Date(d.getTime() + m * 60000);
const isoMin = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
/** Pasos esperados de la sesión: cada 15 min desde el inicio hasta la última entrega (último paso que emite + entrega). */
function plannedSteps(sess) {
  const pasos = sess?.pasos || [];
  if (sess?.estado !== 'corriendo') return pasos.map(p => p.t);
  const ini = toDate(sess.inicio), fin = toDate(sess.fin_emision);
  if (!ini || !fin) return pasos.map(p => p.t);
  const end = addMins(fin, (sess.params?.entrega_min ?? 60) - STEP_MIN);
  const out = [];
  for (let d = ini; d <= end; d = addMins(d, STEP_MIN)) out.push(isoMin(d));
  return out.length >= pasos.length ? out : pasos.map(p => p.t);
}
function durTxt(ms) {
  const m = Math.max(0, Math.round(ms / 60000));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}
function renderAssignDock() {
  const sess = S.live.session, pasos = sess?.pasos || [];
  if (!pasos.length) return;
  const plan = plannedSteps(sess), total = plan.length, done = pasos.length, k = S.live.step;
  const r = $('#ak');
  r.max = Math.max(0, total - 1); r.value = k;
  const pct = j => `${(j / Math.max(1, total - 1)) * 100}%`;
  r.style.setProperty('--p', pct(k));
  r.style.setProperty('--q', pct(done - 1));
  $('#atime').textContent = hhmm(pasos[k]?.t);
  $('#astepTxt').textContent = `Paso ${k + 1} de ${total}${k === done - 1 && sess.estado === 'corriendo' ? ' · el último' : ''}`;
  $('#aprev').disabled = k <= 0;
  $('#anext').disabled = k >= done - 1;
  const marks = plan.map((t, j) => [j, t]).filter(([, t]) => t.endsWith(':00'));
  $('#aticks').innerHTML = marks.map(([j, t]) => `<span style="left:${pct(j)}">${esc(hhmm(t).slice(0, 2))} h</span>`).join('');
  const now = new Date(), ini = toDate(sess.inicio);
  let run;
  if (sess.estado === 'corriendo') {
    const end = toDate(plan.at(-1));
    run = `<b>Lleva ${durTxt(now - ini)}</b><span>faltan ≈ ${durTxt(end - now)} · siguiente paso ${esc(hhmm(sess.siguiente_paso))}</span>`;
  } else {
    run = `<b>${esc(ESTADO_TXT[sess.estado] || '—')}</b><span>${plural(done, 'paso calculado', 'pasos calculados')}, de ${esc(hhmm(pasos[0].t))} a ${esc(hhmm(pasos.at(-1).t))}</span>`;
  }
  $('#arun').innerHTML = run;
}
function goAssignStep(k) {
  const n = S.live.session?.pasos?.length || 0;
  if (!n) return;
  S.live.step = Math.max(0, Math.min(n - 1, k));
  S.live.follow = S.live.step === n - 1;  // en el último paso, la barra sigue a los pasos nuevos
  renderAssignDock(); renderAssignPanel(); renderMap();
  if (S.sel !== null) renderCard();
}
$('#ak').oninput = () => goAssignStep(+$('#ak').value);  // los pasos que aún no corren no se pueden elegir
$('#aprev').onclick = () => goAssignStep(S.live.step - 1);
$('#anext').onclick = () => goAssignStep(S.live.step + 1);
document.addEventListener('keydown', e => {
  if (S.tab !== 'prediccion' || e.metaKey || e.ctrlKey || e.altKey || $('#assignDock').hidden) return;
  if (e.target.closest('input, select, textarea, [role="tab"], [role="separator"], .help')) return;
  if (e.key === 'ArrowLeft') { goAssignStep(S.live.step - 1); e.preventDefault(); }
  else if (e.key === 'ArrowRight') { goAssignStep(S.live.step + 1); e.preventDefault(); }
});

// ── panel derecho: qué mover ──
function currentStep() {
  const p = S.live.session?.pasos || [];
  return p.length ? p[Math.max(0, Math.min(S.live.step, p.length - 1))] : null;
}
function stationName(sn) { return S.snapshot?.stations?.find(s => s.short_name === sn)?.name || ''; }
function pick(b) { S.live.list = b.dataset.list; setTabs('#apTabs', 'list', S.live.list); renderAssignList(); }
$$('#apTabs [role="tab"]').forEach(b => { b.onclick = () => pick(b); });
arrowNav($('#apTabs'), pick);

/** Traslados (donante → receptor) de una lista de paquetes. */
const pairsOf = gs => gs.flatMap(g => g.donors.map(dn => ({paq: g.paq, key: g.key, fase: g.fase, from: dn.id, to: g.recv.id, n: dn.n, recoge: dn.ex.recoge, entrega: g.recv.ex.entrega})));
function renderAssignPanel() {
  const sess = S.live.session, p = currentStep();
  if (!sess || !p) {
    $('#apTitle').textContent = 'Qué mover';
    $('#apSub').textContent = sess ? 'Esperando el primer paso…' : ' ';
    $('#apStats').innerHTML = '';
    $$('#apTabs [role="tab"]').forEach(b => { b.querySelector('.n')?.remove(); });
    renderAssignList();
    return;
  }
  const mover = pairsOf(livePackages(p.emitidas));
  const km = liveKm(p);
  $('#apTitle').textContent = `Qué mover a las ${hhmm(p.t)}`;
  $('#apSub').textContent = `Foto del feed de las ${p.t_feed ? p.t_feed.slice(11, 19) : '—'}${p.alfa > 0 ? ` · α ${fmt(p.alfa)}` : ''}${p.nota ? ` · ${p.nota}` : ''}`;
  $('#apStats').innerHTML = `<div class="stat-pair tri">
      <div class="tile" data-stat="traslados"><b>${fmt(mover.length)}</b><span>traslados</span></div>
      <div class="tile" data-stat="bicis_a_mover"><b>${fmt(p.bicis_a_mover)}</b><span>bicis a mover</span></div>
      <div class="tile" data-stat="km_tramos"><b>${fmtDec(km.km, 1)}</b><span>km en línea recta</span></div></div>`;
  const counts = {emitidas: mover.length, llegaron: pairsOf(liveArrived(p)).length};
  $$('#apTabs [role="tab"]').forEach(b => {
    const base = {emitidas: 'Mover', historial: 'Órdenes', llegaron: 'Llegan', pronostico: 'Pronóstico'}[b.dataset.list];
    b.innerHTML = base + (counts[b.dataset.list] != null ? `<span class="n">${fmt(counts[b.dataset.list])}</span>` : '');
  });
  renderAssignList();
}
const nameOf = sn => `${sn} · ${bare(stationName(sn))}`;
/** Fila de un traslado: "N bicis · de X → a Y", con las horas. Al pulsarla, el mapa va a esa línea. */
function pairRow(x, verbo) {
  return `<li><button class="row move" data-pk="${esc(`${x.key}|${x.paq}|${x.from}`)}">
    <span class="mv-n"><b>${fmt(x.n)}</b>${x.n === 1 ? 'bici' : 'bicis'}</span>
    <span class="r-main"><span class="r-title">${verbo} ${esc(nameOf(x.from))}</span>
      <span class="r-title mv-to">→ a ${esc(nameOf(x.to))}</span>
      <span class="r-sub">recoger ${esc(hhmm(x.recoge))} · entregar ${esc(hhmm(x.entrega))}</span></span></button></li>`;
}
function renderAssignList() {
  const p = currentStep(), el = $('#apList'), L = S.live.list;
  const bind = () => {
    $$('.row[data-pk]', el).forEach(b => {
      b.onclick = () => {
        // Una orden de otro paso: primero se va a ese paso (sus líneas son las que están en el mapa).
        const j = (S.live.session?.pasos || []).findIndex(q => q.t === b.dataset.pk.split('|')[0]);
        if (j >= 0 && j !== S.live.step && S.live.list === 'historial') goAssignStep(j);
        focusPair(b.dataset.pk);
      };
    });
    $$('.row[data-i]', el).forEach(b => { b.onclick = () => { const i = +b.dataset.i; if (i >= 0) openStation(i); }; });
  };
  if (L === 'pronostico') {
    const f = S.live.forecast;
    if (!f) { el.innerHTML = '<li class="none">El pronóstico de esta sesión no está guardado en este navegador.</li>'; return; }
    const mi = modelInfo(f.model);
    const idx = new Map((S.snapshot?.stations || []).map((s, i) => [s.short_name, i]));
    const rows = (f.riesgos || []).slice().sort((a, b) => a.minutos - b.minutos).map(r => `<li><button class="row" data-i="${idx.get(r.short_name) ?? -1}">
      <span class="act ${r.tipo}">${r.tipo === 'vacia' ? 'Se vacía' : 'Se llena'}</span>
      <span class="r-main"><span class="r-title">${esc(nameOf(r.short_name))}</span></span>
      <span class="r-end">en ${fmt(r.minutos)} min</span></button></li>`);
    el.innerHTML = `<li><div class="note">${icon('info')}<span>${esc(mi?.label || f.model)}, emitido a las ${esc(hhmm(f.issued_at))}: estaciones que se vaciarían o llenarían si nadie mueve bicis.</span></div></li>`
      + rowsHtml(rows, 'Ninguna estación se vacía ni se llena en el horizonte del pronóstico.');
    bind();
    return;
  }
  if (!p) { el.innerHTML = `<li class="none">${S.live.session ? 'El primer paso aparece en unos segundos.' : 'Elige pronóstico y α y pulsa Iniciar: aquí sale qué bicis mover de dónde a dónde.'}</li>`; return; }
  if (L === 'llegaron') {
    el.innerHTML = rowsHtml(pairsOf(liveArrived(p)).map(x => pairRow(x, x.fase === 'recoger' ? 'Se recogen en' : 'Llegan de')),
      'No se recoge ni llega ninguna orden en este paso.');
    bind();
    return;
  }
  if (L === 'historial') {
    // Órdenes de todos los pasos hasta el elegido, por cuarto de hora, del más reciente al más viejo.
    const pasos = (S.live.session?.pasos || []).slice(0, S.live.step + 1).reverse();
    el.innerHTML = pasos.map(q => {
      const xs = pairsOf(livePackages(q.emitidas)).sort((a, b) => b.n - a.n);
      const bikes = xs.reduce((n, x) => n + x.n, 0);
      return `<li class="pkg">${esc(hhmm(q.t))} <small>· ${plural(xs.length, 'traslado', 'traslados')} · ${bikesTxt(bikes)}</small></li>`
        + (xs.length ? xs.map(x => pairRow(x, 'De')).join('') : '<li class="none">Sin órdenes en este cuarto de hora.</li>');
    }).join('');
    bind();
    return;
  }
  // Mover: un renglón por traslado, del más grande al más chico; las órdenes sin paquete van al final.
  const pairs = pairsOf(livePackages(p.emitidas)).sort((a, b) => b.n - a.n);
  const sueltas = (p.emitidas || []).filter(o => !o.paquete).map(o => {
    const i = (S.snapshot?.stations || []).findIndex(s => s.short_name === o.short_name);
    return `<li><button class="row" data-i="${i}">${actChip(o.accion)}
      <span class="r-main"><span class="r-title">${esc(nameOf(o.short_name))}</span><span class="r-sub">${o.accion === 'recoger' ? `recoger ${esc(hhmm(o.recoge))}` : `entregar ${esc(hhmm(o.entrega))}`}</span></span>
      <span class="r-end">${bikesTxt(o.n)}</span></button></li>`;
  });
  el.innerHTML = rowsHtml(pairs.map(x => pairRow(x, 'De')).concat(sueltas), 'El asignador no pidió mover bicis en este paso.');
  bind();
}
/** Lleva el mapa a un traslado y lo resalta con su ficha. */
function focusPair(pk) {
  const M = MAPS[0];
  if (!M?.ready) return;
  const f = M.map.getSource('orders')._data?.features?.find(x => x.properties.pk === pk && x.geometry.type === 'LineString');
  if (!f) { toast('Prende "Órdenes actuales" o "Órdenes aplicadas en el paso" para ver la línea.'); return; }
  const cs = f.geometry.coordinates;
  const b = cs.reduce((bb, c) => bb.extend(c), new maplibregl.LngLatBounds(cs[0], cs[0]));
  M.map.fitBounds(b, {padding: 120, maxZoom: 16, duration: REDUCED ? 0 : 700});
  for (const l of ['ord-hl-casing', 'ord-hl']) M.map.setFilter(l, ['all', ['==', ['geometry-type'], 'LineString'], ['==', ['get', 'fid'], f.properties.fid]]);
  ordOpacidad(M.map, 0.28);
  M.pinned.setLngLat(cs[Math.floor(cs.length / 2)]).setHTML(f.properties.tip).addTo(M.map);
  $$('#apList .row[data-pk]').forEach(b => b.classList.toggle('on', b.dataset.pk === pk));
}

/** Paquetes de una lista de órdenes de la sesión en vivo (`paquete` viene de la orden, `emitida` los separa entre pasos). */
function livePackages(orders) {
  return packagesOf((orders || []).map(o => ({id: o.short_name, delta: o.accion === 'entregar' ? o.n : -o.n, paq: o.paquete, key: o.emitida, ex: o})));
}
/** Paquetes que se aplican en el paso actual: su recogida o su entrega cae aquí (`fase`). En la demo en vivo no hay simulador:
 * se muestra lo planeado completo. */
function liveArrived(p) {
  const fase = new Map((p?.aplicadas || []).filter(o => o.paquete).map(o => [`${o.emitida}|${o.paquete}`, o.accion]));
  if (!fase.size) return [];
  return livePackages((S.live.session?.pasos || []).flatMap(x => x.emitidas || []))
    .filter(g => fase.has(`${g.key}|${g.paq}`)).map(g => ({...g, fase: fase.get(`${g.key}|${g.paq}`)}));
}
/** Km en línea recta de los tramos donante → receptor emitidos en un paso (coordenadas del feed). */
function liveKm(p) {
  const out = {km: 0, bkm: 0, bikes: 0};
  const st = new Map((S.snapshot?.stations || []).map(s => [s.short_name, s]));
  const rad = x => x * Math.PI / 180;
  for (const g of livePackages(p?.emitidas)) {
    const r = st.get(g.recv.id);
    for (const dn of g.donors) {
      const o = st.get(dn.id);
      if (!o || !r || o.lat == null || r.lat == null) continue;
      const h = Math.sin(rad(r.lat - o.lat) / 2) ** 2 + Math.cos(rad(o.lat)) * Math.cos(rad(r.lat)) * Math.sin(rad(r.lon - o.lon) / 2) ** 2;
      const d = 2 * 6371 * Math.asin(Math.sqrt(h));
      out.km += d; out.bkm += d * dn.n; out.bikes += dn.n;
    }
  }
  return out;
}
function assignOrders() {
  const emit = $('#showEmitA').checked, arrive = $('#showArriveA').checked, p = currentStep();
  if ((!emit && !arrive) || !p || !S.snapshot) return EMPTY;
  const st = S.snapshot.stations, idx = new Map(st.map((s, i) => [s.short_name, i]));
  const coords = {lon: st.map(s => s.lon), lat: st.map(s => s.lat)};
  const nm = sn => `${esc(sn)} · ${esc(bare(stationName(sn)))}`;
  const feats = [];
  const draw = (x, kind) => {
    const f = ordFeature(coords, idx.get(x.from), idx.get(x.to), {k: kind, a: kind, n: x.n, pk: `${x.key}|${x.paq}|${x.from}`},
      `${kind === 'emit' ? 'Mover' : x.fase === 'recoger' ? 'Se recogen' : 'Llegan'} ${bikesTxt(x.n)} de ${nm(x.from)} a ${nm(x.to)}<small>recoger ${esc(hhmm(x.recoge))} · entregar ${esc(hhmm(x.entrega))}</small>`);
    if (f) feats.push(f);
  };
  if (emit) pairsOf(livePackages(p.emitidas)).forEach(x => draw(x, 'emit'));
  if (arrive) pairsOf(liveArrived(p)).forEach(x => draw(x, 'arrive'));
  return ordCollection(feats);
}
function assignView() {
  const st = S.snapshot.stations, p = currentStep();
  const by = new Map();
  for (const o of p?.emitidas || []) {
    const e = by.get(o.short_name) || {plus: 0, minus: 0};
    if (o.accion === 'entregar') e.plus += o.n; else e.minus += o.n;
    by.set(o.short_name, e);
  }
  return {
    mode: 'asignacion', stations: st, orders: () => assignOrders(),
    props: i => {
      const s = st[i], e = by.get(s.short_name);
      const parts = [];
      if (e?.plus) parts.push(`+${e.plus}`);
      if (e?.minus) parts.push(`−${e.minus}`);
      const d = parts.length ? `d|${parts.join('|')}` : '';
      const stt = stationState(+s.bikes, +s.docks, s.renting && s.installed);
      return {...stateMarker('asignacion', stt, s.bikes, 0.45), dimg: d, dlow: d, sort: parts.length ? 2 : 1};
    },
    zone: i => (st[i].renting && st[i].installed) ? {b: +st[i].bikes, cap: +st[i].capacity, docks: +st[i].docks} : null,
    hover: i => `${plural(st[i].bikes, 'bici', 'bicis')} ahora`,
    card: i => {
      const s = st[i];
      const mine = pairsOf(livePackages(p?.emitidas)).filter(x => x.from === s.short_name || x.to === s.short_name);
      const li = x => x.from === s.short_name
        ? `<li class="row" style="cursor:default">${actChip('recoger')}<span class="r-main"><span class="r-title">Llevar a ${esc(nameOf(x.to))}</span><span class="r-sub">recoger ${esc(hhmm(x.recoge))}</span></span><span class="r-end">${bikesTxt(x.n)}</span></li>`
        : `<li class="row" style="cursor:default">${actChip('entregar')}<span class="r-main"><span class="r-title">Llegan de ${esc(nameOf(x.from))}</span><span class="r-sub">entregar ${esc(hhmm(x.entrega))}</span></span><span class="r-end">${bikesTxt(x.n)}</span></li>`;
      return {state: stationState(+s.bikes, +s.docks, s.renting && s.installed), html: `<div class="card-stats">
          <div class="card-stat hero"><b>${fmt(s.bikes)}</b><span>bicis disponibles ahora</span></div>
          <div class="card-stat"><b>${fmt(s.bikes_disabled)}</b><span>no rentables</span></div>
          <div class="card-stat"><b>${fmt(s.docks)}</b><span>anclajes libres de ${fmt(s.capacity)}</span></div></div>
        <section><h3>Movimientos de este paso</h3>${mine.length ? `<ul class="rows">${mine.map(li).join('')}</ul>` : '<p class="rp-sub" style="margin:0">Sin movimientos en este paso.</p>'}</section>`};
    },
  };
}

// ───────────────────────── arranque ─────────────────────────
(async () => {
  const h = readHash();
  activateTab(h.tab, {fromHash: true});
  renderLegend();
  const mapP = initMap();
  await loadSnapshot();
  setInterval(loadSnapshot, 30000);
  await mapP;
  renderMap();
  if (S.tab === 'replay' && S.replay.arm) renderReplay();
  document.body.dataset.ready = 'true';
})().catch(e => { console.error(e); toast(e.message, true); });
