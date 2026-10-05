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
  sub: 'pronostico',
  map: null,         // mapa principal (MAPS[0].map)
  mapReady: false,
  view: null,        // vista del mapa principal: {mode, stations:[...], props(i), ...}
  views: [],         // una vista por mapa (dos al comparar escenarios en Replay)
  sel: null,         // índice de estación seleccionada (mismo índice en los dos mapas)
  cardSide: 0,       // de qué mapa es la ficha abierta
  snapshot: null,
  zones: {on: {ahora: false, replay: false, pronostico: false, asignacion: false}, geo: null},  // interruptor independiente por vista
  nums: {replay: false, pronostico: false, asignacion: false},  // "Bicis por estación", independiente por vista
  replay: {index: null, days: {}, arms: {}, dayData: null, sel: [], keys: [], k: 0, timer: null, list: 'emitidas', side: 0, token: 0},
  live: {models: null, forecast: null, fk: 0, risk: 'all', sessionId: null, session: null, poll: null, step: -1, list: 'emitidas'},
};
// Escenario principal del Replay (el primero elegido).
Object.defineProperty(S.replay, 'arm', {get() { return this.sel[0] || null; }});
const SESSION_KEY = 'ecobici.asignacion.session';
const SIDE = ['A', 'B'];

// ───────────────────────── colores y estados ─────────────────────────
const CSSV = getComputedStyle(document.documentElement);
const cssv = n => CSSV.getPropertyValue(n).trim();
const ST_COLOR = {
  vacia: cssv('--st-vacia'), pocas: cssv('--st-pocas'), normal: cssv('--st-normal'),
  llena: cssv('--st-llena'), inactiva: cssv('--st-inactiva'),
};
const C = {deliver: cssv('--deliver'), pickup: cssv('--pickup'), relabel: cssv('--relabel'), detour: cssv('--detour'),
  trip: cssv('--trip-now'), gray: '#9aa4ad'};
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
function makeImage(map, id) {
  if (!map || map.hasImage(id)) return;
  const [kind, ...rest] = id.split('|');
  let c = null;
  if (kind === 'pin') c = pinImage(rest[0], rest[1]);
  else if (kind === 'num') c = numImage(rest[0], rest[1]);
  else if (kind === 'd') c = deltaImage(rest);
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
    layout: {'line-cap': 'round'}, paint: {'line-color': C.trip, 'line-opacity': 0.24, 'line-width': 1.1}});
  map.addLayer({id: 'trips-detour', type: 'line', source: 'trips', filter: ['==', ['get', 'k'], 'detour'],
    layout: {'line-cap': 'round'},
    paint: {'line-color': C.detour, 'line-opacity': 0.55, 'line-width': 1.6, 'line-dasharray': [1.6, 1.4]}});
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
      // Lejos solo se ven +N verde y −N rojo; el cambio neto de etiqueta (+N/−N azul) aparece al acercarse.
      'icon-image': ['step', ['zoom'], ['get', 'dlow'], 13, ['get', 'dimg']], 'icon-anchor': 'bottom', 'icon-offset': [0, -7],
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
  M.map.getSource('zones').setData(zonesGeo(v));
  M.map.setFilter('st-sel', ['==', ['get', 'i'], S.sel ?? -1]);
  M.popup?.remove(); M.hit = -1;
}

function buildViews() {
  if (S.tab === 'replay' && S.replay.dayData && S.replay.sel.length) return S.replay.sel.map((a, j) => replayView(a, j));
  if (S.tab === 'prediccion' && S.sub === 'pronostico' && S.live.forecast) return [forecastView()];
  if (S.tab === 'prediccion' && S.sub === 'asignacion' && S.snapshot) return [assignView()];
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
    return {
      full: `<span class="legend-title">Rebalanceo en esta foto</span>
        <div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>entregadas</span><span class="legend-item"><i class="chip minus">−N</i>recogidas</span></div>
        <div class="legend-row"><span class="legend-item"><i class="chip pm">+N</i><i class="chip pm">−N</i>azul: pasan a rentables / a no rentables, sin moverse (al acercar)</span></div>
        <span class="legend-title">Estado de la estación</span>
        <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}${numChip}</div>
        ${lines ? `<div class="legend-row">${lines}</div>` : ''}`,
      compact: `<div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>entregadas</span><span class="legend-item"><i class="chip minus">−N</i>recogidas</span><span class="legend-item"><i class="chip pm">±</i>etiquetas</span></div>
        <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}</div>${lines ? `<div class="legend-row">${lines}</div>` : ''}`,
    };
  }
  if (mode === 'pronostico') {
    return {
      full: `<span class="legend-title">Estado proyectado, sin rebalanceo</span><div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'])}${numChip}</div><span>Con las bicis proyectadas al minuto elegido.</span>`,
      compact: `<div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'])}</div>`,
    };
  }
  return {  // asignación
    full: `<span class="legend-title">Órdenes del paso</span>
      <div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>entregar</span><span class="legend-item"><i class="chip minus">−N</i>recoger</span></div>
      <span class="legend-title">Estado actual de la estación</span>
      <div class="legend-row">${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}${numChip}</div>`,
    compact: `<div class="legend-row"><span class="legend-item"><i class="chip plus">+N</i>entregar</span><span class="legend-item"><i class="chip minus">−N</i>recoger</span>${stateItems(['vacia', 'pocas', 'normal', 'llena'], ' faint')}</div>`,
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
    'Pica una estación para ver su cuenta. Teclado: ← y → mueven 15 minutos, la barra espaciadora reproduce o pausa, y [ y ] ocultan los paneles.'],
  pronostico: ['Pronostica, con el feed de este momento, cuántas bicis tendrá cada estación en las próximas horas o en lo que queda del día.',
    'Elige modelo y pulsa <b>Pronosticar</b>. Los modelos diarios pronostican el día completo; el directo, de 1 a 4 horas. Mueve la barra de arriba para ver cada cuarto de hora.',
    'A la derecha están las estaciones que se vaciarán o llenarán y en cuántos minutos.',
    'La proyección no incluye rebalanceo: es lo que pasaría si nadie mueve bicis. <b>Bicis por estación</b> y <b>Zonas de la ciudad</b> usan la proyección del minuto elegido.'],
  asignacion: ['Con el pronóstico elegido, el asignador decide cada 15 minutos qué estaciones visitar y cuántas bicis recoger o entregar.',
    'Pulsa <b>Iniciar</b> y déjalo corriendo: el cálculo vive en el servidor, así que puedes recargar la página sin perder la sesión.',
    'Cada orden recoge 15 minutos después de emitirse y entrega una hora después de emitirse.',
    'Los viajes del panel derecho se estiman con los cambios del feed; no son viajes registrados.',
    '<b>Bicis por estación</b> y <b>Zonas de la ciudad</b> usan el estado actual del feed. Las flechas de los bordes (o [ y ]) ocultan los paneles.'],
};
function helpKey() { return S.tab === 'prediccion' ? S.sub : S.tab; }
function renderHelp() {
  const key = helpKey();
  const titles = {ahora: 'Ahora', replay: 'Replay', pronostico: 'Pronóstico', asignacion: 'Asignación'};
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
  if (S.tab === 'prediccion') h += `/${S.sub}`;
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

// ───────────────────────── Replay ─────────────────────────
const ARM_TEXT = {
  sin_rebalanceo: 'Nadie mueve bicis: solo ocurren los viajes del día.',
  ecobici: 'Los movimientos que hizo Ecobici ese día, inferidos de las fotos del feed.',
  ma_diaria: 'El asignador decide cada 15 min con un pronóstico de media móvil del mismo tipo de día, hecho a las 05:00.',
  lgbm_diario: 'El asignador decide cada 15 min con un pronóstico LightGBM del día completo, hecho a las 05:00.',
  lgbm_directo: 'El asignador decide cada 15 min con un pronóstico LightGBM de las próximas horas que usa lo observado en el día.',
  oraculo_diario: 'El asignador conoce los viajes reales del día desde las 05:00. Es un techo de referencia, no una estrategia.',
  oraculo_directo: 'El asignador conoce los viajes reales de las próximas horas. Es un techo de referencia, no una estrategia.',
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
$('#rday').onchange = () => { fillArms(); loadReplay(S.replay.k); };
$('#showTrips').onchange = () => renderMap();
$('#showDetours').onchange = () => renderMap();

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
    const armPs = keys.map(arm => {
      const id = `${day}/${arm}`;
      if (!S.replay.arms[id]) {
        S.replay.arms[id] = api(`/api/replay/${encodeURIComponent(day)}/${encodeURIComponent(arm)}`).then(a => {
          if (!Array.isArray(a?.bikes)) throw new ApiError('El archivo de replay no tiene el formato esperado. Vuelve a precalcular.');
          const out = {...a, day, arm};
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
  arms.forEach((a, j) => { $(`#mapLabel${j}`).innerHTML = `<span class="side-tag">${SIDE[j]}</span> ${esc(a.label || a.arm)}`; });
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

// Filas del panel derecho: [clave, etiqueta, marca]. Las filas de una sola celda son títulos de grupo.
const PANEL_ROWS = [
  ['Rebalanceo'],
  ['emitidas', 'Órdenes emitidas', ''],
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
      const [key, label, mk] = row;
      const cells = arms.map((a, j) => `<td class="foto" data-side="${j}">${fmt(snapVal(a, k, key, false))}</td><td class="acum" data-side="${j}">${fmt(snapVal(a, k, key, true))}</td>`).join('');
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
    desvios: a.desvios?.[k]?.length || 0,
  };
  $$('#rpTabs [role="tab"]').forEach(b => {
    const base = {emitidas: 'Emitidas', aplicadas: 'Aplicadas', desvios: 'Desviados'}[b.dataset.list];
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
    const orders = (a.decision?.[k]?.orders || []).slice().sort((x, y) => x[1] - y[1]);
    rows = orders.map(([i, d]) => `<li><button class="row" data-i="${i}">${actChip(d < 0 ? 'recoger' : 'entregar')}
      <span class="r-main"><span class="r-title">${esc(name(i))}</span><span class="r-sub">${d < 0 ? `Recoge a las ${esc(addMin(T[k], 15))}` : `Entrega a las ${esc(addMin(T[k], a.spec?.delivery || 60))}`}</span></span>
      <span class="r-end">${plural(Math.abs(d), 'bici', 'bicis')}</span></button></li>`);
    empty = 'No se emitieron órdenes en esta foto.';
  } else if (S.replay.list === 'aplicadas') {
    const ap = (a.applied?.[k] || []).filter(o => o[2] !== 0).slice().sort((x, y) => x[2] - y[2]);
    rows = ap.map(([i, d, real, ik]) => `<li><button class="row" data-i="${i}">${actChip(real < 0 ? 'recoger' : 'entregar')}
      <span class="r-main"><span class="r-title">${esc(name(i))}</span><span class="r-sub">Emitida a las ${esc(T[ik] ?? '—')}${real !== d ? ` · pedía ${fmt(Math.abs(d))}, se aplicaron ${fmt(Math.abs(real))}` : ''}</span></span>
      <span class="r-end">${plural(Math.abs(real), 'bici', 'bicis')}</span></button></li>`);
    empty = 'No se aplicaron órdenes en esta foto.';
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

// ───────────────────────── Predicción ─────────────────────────
function updatePredLayout() {
  const on = S.tab === 'prediccion';
  setTabs('#view-prediccion .segmented', 'sub', S.sub);
  $('#pane-pronostico').hidden = S.sub !== 'pronostico';
  $('#pane-asignacion').hidden = S.sub !== 'asignacion';
  $('#right-pronostico').hidden = !(on && S.sub === 'pronostico');
  $('#right-asignacion').hidden = !(on && S.sub === 'asignacion');
  $('#forecastDock').hidden = !(on && S.sub === 'pronostico' && S.live.forecast);
  $('[data-zones="pronostico"]').hidden = !S.live.forecast;  // las zonas del pronóstico necesitan una proyección
  $('.stage').classList.toggle('with-dock', !$('#replayDock').hidden || !$('#forecastDock').hidden);
}
function pickSub(sub) {
  S.sub = sub;
  updatePredLayout();
  if (!$('#help').hidden) renderHelp();
  writeHash();
  if (sub === 'asignacion') renderAssign();
  renderMap();
}
$$('#view-prediccion .segmented [role="tab"]').forEach(b => { b.onclick = () => pickSub(b.dataset.sub); });
arrowNav($('#view-prediccion .segmented'), b => pickSub(b.dataset.sub));
$('#goForecast').onclick = () => pickSub('pronostico');

function enterPrediccion() {
  if (!S.live.models) loadModels();
  if (!S.live.sessionId) {
    try { S.live.sessionId = localStorage.getItem(SESSION_KEY); } catch { /* sin almacenamiento */ }
    if (S.live.sessionId) pollSession();
  }
  renderForecastPanel();
  renderAssign();
}

async function loadModels() {
  showNote('#ferror', null);
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
    $$('input[name="model"]').forEach(r => { r.onchange = syncHorizon; });
    syncHorizon();
    $('#runForecast').disabled = !firstOk;
  } catch (e) {
    $('#lastPublished').textContent = '—'; $('#modelsUpdated').textContent = '—';
    $('#models').innerHTML = '';
    $('#runForecast').disabled = true;
    showNote('#ferror', `No se pudieron cargar los modelos. ${e.message}`, {retry: loadModels});
  }
}

/** Rango de datos con que se armó el modelo. */
function corteText(c) {
  if (!c) return '';
  if (c.train_start) return `Viajes ${shortDay(c.train_start)} – ${shortDay(c.train_end)}`;
  return c.train_end ? `Sin entrenamiento; viajes hasta ${shortDay(c.train_end)}` : (c.nota || '');
}
/** El horizonte depende de la forma del modelo: diaria → día completo; directa → 1 a 4 h. */
/** Horizontes válidos del modelo: los manda el backend (`horizontes`); si faltan, se deducen de la forma. */
function horizonsOf(mi) {
  if (Array.isArray(mi?.horizontes) && mi.horizontes.length) return mi.horizontes.map(String);
  if (!mi?.forma) return ['1', '2', '3', '4', 'dia'];
  return mi.forma === 'diaria' ? ['dia'] : ['1', '2', '3', '4'];
}
function syncHorizon() {
  const mi = modelInfo($('input[name="model"]:checked')?.value);
  const valid = horizonsOf(mi), daily = !valid.some(h => h !== 'dia');
  $$('#horizon label').forEach(l => {
    const inp = $('input', l);
    const ok = valid.includes(inp.value);
    l.hidden = !ok;
    inp.disabled = !ok;
  });
  const cur = $('input[name="hz"]:checked');
  if (!cur || cur.disabled) {
    const pick = valid.includes('2') ? '2' : valid[0];
    const el = $(`input[name="hz"][value="${pick}"]`);
    if (el) el.checked = true;
  }
  $('#hzHint').textContent = !mi ? '' : daily
    ? 'Los modelos diarios pronostican lo que queda del día completo.'
    : 'El modelo directo pronostica de 1 a 4 horas hacia adelante.';
}

$('#runForecast').onclick = async () => {
  const model = $('input[name="model"]:checked')?.value;
  const hz = $('input[name="hz"]:checked')?.value;
  if (!model) { showNote('#ferror', 'Elige un modelo disponible.'); return; }
  const btn = $('#runForecast');
  setBusy(btn, true, 'Pronosticando…');
  showNote('#ferror', null);
  mapState('Calculando pronóstico…', 'busy');
  try {
    const f = await api(`/api/live/forecast?${new URLSearchParams({model, horizonte: hz})}`, {method: 'POST', timeout: 180000});
    if (!Array.isArray(f?.proyeccion) || !Array.isArray(f?.minutes)) throw new ApiError('La respuesta del pronóstico no tiene el formato esperado.');
    const st = f.stations || {};
    f._stations = Array.isArray(st) ? st : (st.short_name || []).map((sn, i) => ({short_name: sn, name: st.name?.[i], lat: st.lat?.[i], lon: st.lon?.[i], cap: st.cap?.[i]}));
    f._byShort = new Map(f._stations.map((s, i) => [s.short_name, i]));
    f._hz = hz;
    S.live.forecast = f;
    const r = $('#fk');
    r.max = f.minutes.length - 1;
    S.live.fk = Math.min(f.minutes.findIndex(m => m >= 60) >= 0 ? f.minutes.findIndex(m => m >= 60) : 0, f.minutes.length - 1);
    r.value = S.live.fk;
    renderForecastTicks();
    updatePredLayout();
    renderForecastPanel();
    renderForecastDock();
    renderMap();
    renderAssign();
    toast(`Pronóstico listo: ${plural(f.riesgos?.length || 0, 'estación en riesgo', 'estaciones en riesgo')}.`);
  } catch (e) {
    showNote('#ferror', e.message, {retry: () => $('#runForecast').click()});
  } finally {
    setBusy(btn, false, 'Pronosticar');
    mapState(null);
  }
};

function modelInfo(key) { return S.live.models?.modelos?.find(m => m.key === key); }

function renderForecastTicks() {
  const f = S.live.forecast, n = f.minutes.length;
  const step = n > 24 ? 16 : n > 8 ? 4 : 1;
  const el = $('#fticks');
  const marks = [];
  for (let j = step - 1; j < n; j += step) marks.push([j, f.minutes[j] % 60 === 0 ? `${f.minutes[j] / 60} h` : `${f.minutes[j]}′`]);
  el.innerHTML = marks.map(([j, t]) => `<span style="left:${(j / (n - 1 || 1)) * 100}%">${t}</span>`).join('');
}
function renderForecastDock() {
  const f = S.live.forecast;
  if (!f) return;
  const m = f.minutes[S.live.fk];
  const base = hhmm(f.t_feed || f.issued_at);
  $('#ftime').textContent = `+${m} min`;
  $('#fstep').textContent = base !== '—' ? `hacia las ${addMin(base, m)}` : 'proyección';
  setRangeFill($('#fk'));
}
$('#fk').oninput = () => {
  S.live.fk = +$('#fk').value;
  renderForecastDock();
  renderMap();
  if (S.sel !== null) renderCard();
};

function renderForecastPanel() {
  const f = S.live.forecast;
  const info = $('#forecastInfo');
  if (!f) {
    info.hidden = true;
    $('#riskCount').textContent = '';
    $('#riskList').innerHTML = '<li class="none">Pide un pronóstico para ver qué estaciones se vaciarán o llenarán.</li>';
    return;
  }
  const mi = modelInfo(f.model);
  const ref = f.referencia || {};
  const rows = [
    ['Modelo', mi?.label || f.model],
    ['Entrenamiento', mi?.corte?.train_start ? `${shortDay(mi.corte.train_start)} – ${shortDay(mi.corte.train_end)}` : mi?.corte?.train_end ? `No se entrena; viajes hasta ${shortDay(mi.corte.train_end)}` : '—'],
    ['Rezagos', ref.rezagos === 'reales' ? 'Reales' : ref.rezagos === 'dia_referencia' ? `Día de referencia ${shortDay(ref.dia_referencia)}` : '—'],
    ['Foto del feed', hhmm(f.t_feed)],
  ];
  if (ref.viajes_del_dia === 'inferidos_feed') {
    rows.push(['Viajes de hoy', 'Inferidos del feed']);
    const fh = ref.feed_hoy || {};
    rows.push(['Lecturas de hoy desde', fh.desde ? `${hhmm(fh.desde)}${fh.fotos !== undefined ? ` (${fmt(fh.fotos)} fotos)` : ''}` : 'Sin lecturas']);
  }
  const warn = [];
  if (ref.rezagos === 'dia_referencia') warn.push('Faltan días publicados para los rezagos de hoy; se usa el día de referencia indicado.');
  if (ref.viajes_del_dia === 'inferidos_feed') warn.push('Las salidas y llegadas de hoy se infieren de los cambios del feed; es una demostración de cómo operaría, no un resultado medido.');
  if (ref.feed_hoy && ref.feed_hoy.completo === false) warn.push('No hay lecturas continuas del feed desde las 05:00.');
  info.innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('') +
    `<dd class="full">Proyección sin rebalanceo, emitida a las ${esc(hhmm(f.issued_at))}.${warn.length ? ' ' + esc(warn.join(' ')) : ''}</dd>`;
  info.hidden = false;
  renderRisks();
}

function pickRisk(b) { S.live.risk = b.dataset.risk; setTabs('#riskTabs', 'risk', S.live.risk); renderRisks(); }
$$('#riskTabs [role="tab"]').forEach(b => { b.onclick = () => pickRisk(b); });
arrowNav($('#riskTabs'), pickRisk);
function renderRisks() {
  const f = S.live.forecast;
  if (!f) return;
  const all = (f.riesgos || []).slice().sort((a, b) => a.minutos - b.minutos);
  const list = S.live.risk === 'all' ? all : all.filter(r => r.tipo === S.live.risk);
  $('#riskCount').textContent = plural(all.length, 'estación', 'estaciones');
  const rows = list.map(r => {
    const i = f._byShort.get(r.short_name);
    const s = f._stations[i] || {};
    const now = i !== undefined ? f.bikes_now?.[i] : undefined;
    return `<li><button class="row" data-i="${i ?? -1}"><span class="act ${r.tipo}">${r.tipo === 'vacia' ? 'Se vacía' : 'Se llena'}</span>
      <span class="r-main"><span class="r-title">${esc(r.short_name)} · ${esc(bare(s.name))}</span><span class="r-sub">${now !== undefined ? `Ahora ${plural(now, 'bici', 'bicis')}` : ''}${s.cap ? ` de ${fmt(s.cap)}` : ''}</span></span>
      <span class="r-end">en ${fmt(r.minutos)} min</span></button></li>`;
  });
  $('#riskList').innerHTML = rowsHtml(rows, S.live.risk === 'all' ? 'Ninguna estación se vacía ni se llena en este horizonte.' : 'Ninguna estación en esta categoría.');
  $$('#riskList .row').forEach(b => { b.onclick = () => { const i = +b.dataset.i; if (i >= 0) openStation(i); }; });
}

function forecastView() {
  const f = S.live.forecast, j = S.live.fk, st = f._stations;
  const proj = f.proyeccion[j] || [];
  const state = i => stationState(proj[i], (st[i].cap ?? Infinity) - proj[i], true);
  return {
    mode: 'pronostico', stations: st,
    props: i => (proj[i] === undefined || proj[i] === null) ? null : ({...stateMarker('pronostico', state(i), proj[i], 0.8), dimg: '', sort: 1}),
    // Zonas con la proyección del minuto elegido (sin dato de no rentables: anclajes libres = capacidad − bicis).
    zone: i => (proj[i] === undefined || proj[i] === null || !st[i].cap) ? null : {b: proj[i], cap: st[i].cap, docks: Math.max(0, st[i].cap - proj[i])},
    hover: i => `${plural(proj[i], 'bici proyectada', 'bicis proyectadas')} en +${f.minutes[j]} min`,
    card: i => ({state: state(i), html: forecastCard(i)}),
  };
}
function forecastCard(i) {
  const f = S.live.forecast, j = S.live.fk, s = f._stations[i];
  const risk = (f.riesgos || []).find(r => r.short_name === s.short_name);
  return `<div class="card-stats">
      <div class="card-stat hero"><b>${fmt(f.proyeccion[j][i])}</b><span>bicis proyectadas en +${fmt(f.minutes[j])} min</span></div>
      <div class="card-stat"><b>${fmt(f.bikes_now?.[i])}</b><span>bicis ahora</span></div>
      <div class="card-stat"><b>${fmt(s.cap)}</b><span>capacidad</span></div>
    </div>
    <section><h3>En el cuarto de hora que termina en +${fmt(f.minutes[j])} min</h3>
      <dl class="kv"><dt>Salidas esperadas</dt><dd>${fmt(f.salidas?.[j]?.[i])}</dd><dt>Llegadas esperadas</dt><dd>${fmt(f.llegadas?.[j]?.[i])}</dd></dl></section>
    ${risk ? `<div class="note warn">${icon('alert')}<span>${risk.tipo === 'vacia' ? 'Se vacía' : 'Se llena'} en ${fmt(risk.minutos)} minutos si nadie mueve bicis.</span></div>` : ''}`;
}

// ── Asignación ──
function renderAssign() {
  const f = S.live.forecast, sess = S.live.session, id = S.live.sessionId;
  const running = sess?.estado === 'corriendo';
  $('#session').hidden = !id;
  $('#assignEmpty').hidden = !!id || !!f;
  $('#assignForm').hidden = !!id || !f;
  if (f && !id) {
    const mi = modelInfo(f.model);
    $('#assignUsing').innerHTML = `Usará el pronóstico de <b>${esc(mi?.label || f.model)}</b> emitido a las <b>${esc(hhmm(f.issued_at))}</b>.`;
  }
  if (id) {
    const pill = $('#sessState');
    const label = {corriendo: 'Corriendo', terminada: 'Terminada', detenida: 'Detenida', error: 'Error'}[sess?.estado] || 'Conectando…';
    pill.dataset.s = sess?.estado || '';
    pill.textContent = label;
    $('#stopAssign').hidden = !running;
    $('#newAssign').hidden = !sess || running;
    const pasos = sess?.pasos || [];
    const mi = modelInfo(sess?.model);
    const rows = sess ? [
      ['Modelo', mi?.label || sess.model || '—'],
      ['Inicio', hhmm(sess.inicio)],
      ['Duración', sess.horas ? `${sess.horas} h` : '—'],
      ['Pasos registrados', fmt(pasos.length)],
    ] : [];
    if (running) rows.splice(3, 0, ['Siguiente paso', hhmm(sess.siguiente_paso)]);
    $('#sessInfo').innerHTML = rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd${k === 'Siguiente paso' ? ' id="nextStep"' : ''}>${esc(v)}</dd></div>`).join('') ||
      '<div><dt>Sesión</dt><dd><span class="skeleton w-8"></span></dd></div>';
    $('#stepPick').hidden = pasos.length < 2;
    if (pasos.length) {
      const sel = $('#astep');
      const follow = S.live.step < 0 || S.live.step >= pasos.length - 1 || S.live.follow;
      sel.innerHTML = pasos.map((p, j) => `<option value="${j}">${esc(hhmm(p.t))} · ${plural(p.emitidas?.length || 0, 'orden', 'órdenes')}</option>`).join('');
      S.live.step = follow ? pasos.length - 1 : Math.min(S.live.step, pasos.length - 1);
      sel.value = S.live.step;
    }
  }
  renderAssignPanel();
}
$('#astep').onchange = () => {
  S.live.step = +$('#astep').value;
  S.live.follow = S.live.step === (S.live.session?.pasos?.length || 0) - 1;
  renderAssignPanel(); renderMap();
};

$('#startAssign').onclick = async () => {
  const f = S.live.forecast;
  if (!f) return;
  const btn = $('#startAssign');
  setBusy(btn, true, 'Iniciando…');
  showNote('#aerror', null);
  try {
    const r = await api(`/api/live/assign/start?${new URLSearchParams({forecast_id: f.id, horas: $('#ahours').value})}`, {method: 'POST', timeout: 180000});
    if (!r?.session_id) throw new ApiError('El servidor no devolvió una sesión.');
    S.live.sessionId = r.session_id; S.live.session = null; S.live.step = -1; S.live.follow = true;
    try { localStorage.setItem(SESSION_KEY, r.session_id); } catch { /* sin almacenamiento */ }
    renderAssign();
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
    toast('Asignación detenida.');
  } catch (e) {
    showNote('#aerror', e.message);
  } finally {
    setBusy(btn, false, 'Detener');
  }
};
$('#newAssign').onclick = () => {
  clearTimeout(S.live.poll);
  S.live.sessionId = null; S.live.session = null; S.live.step = -1;
  try { localStorage.removeItem(SESSION_KEY); } catch { /* sin almacenamiento */ }
  renderAssign(); renderMap();
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

function currentStep() {
  const p = S.live.session?.pasos || [];
  return p.length ? p[Math.max(0, Math.min(S.live.step, p.length - 1))] : null;
}
function stationName(sn) {
  const st = S.snapshot?.stations?.find(s => s.short_name === sn) || S.live.forecast?._stations?.[S.live.forecast._byShort.get(sn)];
  return st?.name || '';
}
function pick(b) { S.live.list = b.dataset.list; setTabs('#apTabs', 'list', S.live.list); renderAssignList(); }
$$('#apTabs [role="tab"]').forEach(b => { b.onclick = () => pick(b); });
arrowNav($('#apTabs'), pick);

function renderAssignPanel() {
  const sess = S.live.session, p = currentStep();
  if (!sess || !p) {
    $('#apTitle').textContent = 'Órdenes';
    $('#apSub').textContent = sess ? 'Esperando el primer paso…' : ' ';
    $('#apStats').innerHTML = '';
    $('#apList').innerHTML = `<li class="none">${sess ? 'El primer paso aparece en unos segundos.' : 'Inicia una asignación para ver aquí las órdenes de cada paso.'}</li>`;
    $$('#apTabs [role="tab"]').forEach(b => { b.querySelector('.n')?.remove(); });
    return;
  }
  $('#apTitle').textContent = `Paso de las ${hhmm(p.t)}`;
  $('#apSub').textContent = `Foto del feed de las ${p.t_feed ? p.t_feed.slice(11, 19) : '—'}`;
  const tot = sess.totales || {};
  $('#apStats').innerHTML = `
    <div class="stat-group"><h3>Este paso</h3><div class="stat-pair">
      <div class="tile" data-stat="visitas"><b>${fmt(p.visitas)}</b><span>visitas</span></div>
      <div class="tile" data-stat="bicis_a_mover"><b>${fmt(p.bicis_a_mover)}</b><span>bicis a mover</span></div></div></div>
    <div class="stat-group"><h3>Acumulado de la sesión</h3><div class="stat-pair">
      <div class="tile"><b>${fmt(tot.visitas)}</b><span>visitas</span></div>
      <div class="tile"><b>${fmt(tot.bicis_a_mover)}</b><span>bicis a mover</span></div></div></div>`;
  const counts = {emitidas: p.emitidas?.length || 0, aplicadas: p.aplicadas?.length || 0};
  $$('#apTabs [role="tab"]').forEach(b => {
    const base = {emitidas: 'Emitidas', aplicadas: 'Aplicadas', viajes: 'Viajes'}[b.dataset.list];
    b.innerHTML = base + (b.dataset.list in counts ? `<span class="n">${fmt(counts[b.dataset.list])}</span>` : '');
  });
  renderAssignList();
}
function renderAssignList() {
  const p = currentStep(), el = $('#apList');
  if (!p) return;
  if (S.live.list === 'viajes') {
    const pasos = S.live.session.pasos;
    el.innerHTML = `<li><div class="note warn">${icon('info')}<span>Estimados de los cambios del feed entre pasos; no son viajes registrados.</span></div></li>
      <li class="stat"><span><i class="mk trip"></i>Salidas estimadas en este paso</span><b>${fmt(p.salidas_est)}</b></li>
      <li class="stat"><span><i class="mk trip"></i>Llegadas estimadas en este paso</span><b>${fmt(p.llegadas_est)}</b></li>
      ${pasos.length > 1 ? `<li class="stat-group" style="margin-top:12px"><h3>Por paso (salidas / llegadas estimadas)</h3>${pasos.map(x => `<div class="stat"><span>${esc(hhmm(x.t))}</span><b>${fmt(x.salidas_est)} / ${fmt(x.llegadas_est)}</b></div>`).join('')}</li>` : ''}`;
    return;
  }
  const orders = (S.live.list === 'emitidas' ? p.emitidas : p.aplicadas) || [];
  const rows = orders.slice().sort((a, b) => (a.accion > b.accion ? -1 : 1) || b.n - a.n).map(o => {
    const i = S.view?.mode === 'asignacion' ? S.view.stations.findIndex(s => s.short_name === o.short_name) : -1;
    const when = o.accion === 'recoger' ? `Recoge a las ${hhmm(o.recoge)} · entrega a las ${hhmm(o.entrega)}` : `Entrega a las ${hhmm(o.entrega)}`;
    return `<li><button class="row" data-i="${i}">${actChip(o.accion)}
      <span class="r-main"><span class="r-title">${esc(o.short_name)} · ${esc(bare(stationName(o.short_name)))}</span><span class="r-sub" title="${esc(when)}">${esc(when)}</span></span>
      <span class="r-end">${plural(o.n, 'bici', 'bicis')}</span></button></li>`;
  });
  el.innerHTML = rowsHtml(rows, S.live.list === 'emitidas' ? 'El asignador no emitió órdenes en este paso.' : 'Ninguna orden se aplica en este paso.');
  $$('.row', el).forEach(b => { b.onclick = () => { const i = +b.dataset.i; if (i >= 0) openStation(i); }; });
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
    mode: 'asignacion', stations: st,
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
      const mine = (p?.emitidas || []).filter(o => o.short_name === s.short_name);
      return {state: stationState(+s.bikes, +s.docks, s.renting && s.installed), html: `<div class="card-stats">
          <div class="card-stat hero"><b>${fmt(s.bikes)}</b><span>bicis disponibles ahora</span></div>
          <div class="card-stat"><b>${fmt(s.bikes_disabled)}</b><span>no rentables</span></div>
          <div class="card-stat"><b>${fmt(s.docks)}</b><span>anclajes libres de ${fmt(s.capacity)}</span></div></div>
        <section><h3>Órdenes de este paso</h3>${mine.length ? `<ul class="rows">${mine.map(o => `<li class="row" style="cursor:default">${actChip(o.accion)}<span class="r-main"><span class="r-sub">${o.accion === 'recoger' ? `Recoge a las ${esc(hhmm(o.recoge))}` : `Entrega a las ${esc(hhmm(o.entrega))}`}</span></span><span class="r-end">${plural(o.n, 'bici', 'bicis')}</span></li>`).join('')}</ul>` : '<p class="rp-sub" style="margin:0">Sin órdenes en este paso.</p>'}</section>`};
    },
  };
}

// ───────────────────────── arranque ─────────────────────────
(async () => {
  const h = readHash();
  if (h.tab === 'prediccion' && ['pronostico', 'asignacion'].includes(h.rest[0])) S.sub = h.rest[0];
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
