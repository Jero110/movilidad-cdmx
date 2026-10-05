/* Prueba de navegador de la app Ecobici · Rebalanceo (Ahora, Replay, Predicción).

Uso (desde la raíz del repo):
  node scripts/ecobici_mapa/test_ui.cjs                     # levanta el servidor real (uv run python3 server.py) y prueba contra él
  node scripts/ecobici_mapa/test_ui.cjs --url http://127.0.0.1:8765   # usa un servidor que ya está corriendo
  node scripts/ecobici_mapa/test_ui.cjs --fixtures          # servidor simulado con fixtures/ (desarrollo sin backend)
  node scripts/ecobici_mapa/test_ui.cjs --serve-fixtures [--port 8790]   # solo levanta el simulado para verlo a mano
Opciones: --shots guarda capturas en screenshots/; --headed abre Chrome visible; --no-live levanta el
servidor sin su bucle de lectura del feed (útil si ya hay otro servidor escribiendo la bitácora).

Recorrido: carga las tres pestañas sin errores de consola; busca una estación y abre su ficha con
los campos de `raw`; prende y apaga las zonas (AGEB de /api/zonas) en Ahora; pliega y despliega los paneles
(Ahora, Replay con 2 escenarios, Pronóstico) revisando leyenda y tamaño del mapa; avanza el Replay de 05:00 a 05:15 y lee
el panel derecho (foto y acumulado desde 05:00, contra la suma de snap[0..k]) con `cuadre_ok`;
revisa que solo se dibujen los viajes que llegaron en la foto y los desvíos; prende la coropleta
por AGEB (sumas, colores y cortes de los pines, tooltip) y ve que cambia con la foto; compara dos escenarios (dos mapas sincronizados, panel
A/B) y vuelve a uno; pide un pronóstico con cada modelo disponible revisando que el horizonte
dependa de su forma; arranca una asignación en vivo, ve su primer paso, recarga la página (la
sesión se retoma) y la detiene.

Sin dependencias npm: maneja Google Chrome por el protocolo DevTools con el WebSocket de Node (≥ 22).
Chrome se busca en CHROME o en /Applications/Google Chrome.app. */
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawn} = require('node:child_process');

const HERE = __dirname;
const FIX = path.join(HERE, 'fixtures');
const SHOTS = path.join(HERE, 'screenshots');
const argv = process.argv.slice(2);
const flag = f => argv.includes(f);
const opt = (f, d) => { const j = argv.indexOf(f); return j >= 0 ? argv[j + 1] : d; };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ───────────── servidor simulado con fixtures ─────────────
function fixtureServer(port) {
  const read = f => JSON.parse(fs.readFileSync(path.join(FIX, f), 'utf8'));
  const sessions = new Map();
  const forecasts = new Map();
  const STEP_MS = 20000;  // en el simulado, un paso cada 20 s
  const send = (res, code, body, type = 'application/json') => {
    res.writeHead(code, {'Content-Type': type, 'Cache-Control': 'no-store'});
    res.end(type === 'application/json' ? JSON.stringify(body) : body);
  };
  const sessionView = id => {
    const s = sessions.get(id), tpl = read('live_assign_template.json');
    const steps = Math.min(tpl.pasos.length, 1 + Math.floor((Date.now() - s.t0) / STEP_MS));
    const pasos = tpl.pasos.slice(0, steps);
    let estado = s.estado;
    if (estado === 'corriendo' && steps >= Math.min(tpl.pasos.length, s.horas * 4)) estado = 'terminada';
    return {estado, model: s.model, inicio: tpl.pasos[0].t, horas: s.horas, pasos,
      totales: {visitas: pasos.reduce((n, p) => n + p.visitas, 0), bicis_a_mover: pasos.reduce((n, p) => n + p.bicis_a_mover, 0)},
      siguiente_paso: tpl.pasos[steps]?.t ?? null};
  };
  const srv = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://x');
    const p = u.pathname;
    try {
      if (p === '/' || p === '/index.html') return send(res, 200, fs.readFileSync(path.join(HERE, 'index.html')), 'text/html');
      if (p === '/app.css') return send(res, 200, fs.readFileSync(path.join(HERE, 'app.css')), 'text/css');
      if (p === '/app.js') return send(res, 200, fs.readFileSync(path.join(HERE, 'app.js')), 'text/javascript');
      if (p === '/favicon.ico') return send(res, 200, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8" fill="#15212b"/></svg>', 'image/svg+xml');
      if (p === '/api/snapshot') return send(res, 200, read('snapshot.json'));
      if (p === '/api/replay/index') return send(res, 200, read('replay_index.json'));
      let m = p.match(/^\/api\/replay\/(\d{4}-\d\d-\d\d)\/([a-z_]+)$/);
      if (m) {
        const f = m[2] === 'dia' ? `replay_${m[1]}_dia.json` : `replay_${m[1]}_${m[2]}.json`;
        if (!fs.existsSync(path.join(FIX, f))) return send(res, 404, {detail: `${f} no existe en fixtures`});
        return send(res, 200, read(f));
      }
      if (p === '/api/zonas') return send(res, 200, read('zonas.json'));
      if (p === '/api/live/models') return send(res, 200, read('live_models.json'));
      if (p === '/api/live/forecast' && req.method === 'POST') {
        const model = u.searchParams.get('model'), hz = u.searchParams.get('horizonte');
        const mi = read('live_models.json').modelos.find(x => x.key === model);
        if (!mi?.disponible) return send(res, 400, {detail: `Modelo ${model} no disponible`});
        const f = read('live_forecast.json');
        const steps = hz === 'dia' ? f.minutes.length : Math.min(f.minutes.length, (+hz) * 4);
        const out = {...f, id: `fx-${model}-${hz}-${Date.now()}`, model,
          referencia: mi.forma === 'directa' ? f.referencia : {...f.referencia, viajes_del_dia: null},
          minutes: f.minutes.slice(0, steps), proyeccion: f.proyeccion.slice(0, steps),
          salidas: f.salidas.slice(0, steps), llegadas: f.llegadas.slice(0, steps),
          riesgos: f.riesgos.filter(r => r.minutos <= f.minutes[steps - 1])};
        forecasts.set(out.id, out);
        return setTimeout(() => send(res, 200, out), 600);
      }
      if (p === '/api/live/assign/start' && req.method === 'POST') {
        const fc = forecasts.get(u.searchParams.get('forecast_id'));
        if (!fc) return send(res, 404, {detail: 'Pronóstico no encontrado'});
        const id = `fx-sesion-${sessions.size + 1}`;
        sessions.set(id, {t0: Date.now(), horas: +u.searchParams.get('horas') || 1, model: fc.model, estado: 'corriendo'});
        return send(res, 200, {session_id: id});
      }
      m = p.match(/^\/api\/live\/assign\/([\w-]+)(\/stop)?$/);
      if (m) {
        if (!sessions.has(m[1])) return send(res, 404, {detail: 'Sesión no encontrada'});
        if (m[2]) { sessions.get(m[1]).estado = 'detenida'; return send(res, 200, sessionView(m[1])); }
        return send(res, 200, sessionView(m[1]));
      }
      return send(res, 404, {detail: 'no encontrado'});
    } catch (e) { return send(res, 500, {detail: String(e)}); }
  });
  return new Promise(r => srv.listen(port, '127.0.0.1', () => r(srv)));
}

// ───────────── servidor real ─────────────
async function realServer(port) {
  const args = ['run', 'python3', 'server.py', '--no-browser', '--port', String(port)];
  if (flag('--no-live')) args.push('--no-live');  // no duplicar la bitácora del feed si ya corre otro servidor
  const proc = spawn('uv', args, {cwd: HERE, stdio: ['ignore', 'pipe', 'pipe']});
  let log = '';
  proc.stdout.on('data', d => { log += d; });
  proc.stderr.on('data', d => { log += d; });
  for (let i = 0; i < 240; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}/`); if (r.ok) return proc; } catch { /* aún no */ }
    if (proc.exitCode !== null) throw new Error(`el servidor terminó:\n${log}`);
    await sleep(500);
  }
  proc.kill();
  throw new Error(`el servidor no respondió en 120 s:\n${log}`);
}

// ───────────── Chrome por DevTools ─────────────
class Page {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.onmessage = ev => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const {res, rej} = this.pending.get(msg.id); this.pending.delete(msg.id);
        msg.error ? rej(new Error(msg.error.message)) : res(msg.result);
      } else if (msg.method) this.handlers.forEach(h => h(msg));
    };
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({id, method, params}));
    const where = params.expression ? `\n  en: ${params.expression.slice(0, 160)}` : '';
    return new Promise((res, rej) => this.pending.set(id, {res, rej: e => rej(new Error(`${e.message} (${method})${where}`))}));
  }
  async eval(expr) {
    const r = await this.send('Runtime.evaluate', {expression: expr, awaitPromise: true, returnByValue: true});
    if (r.exceptionDetails) throw new Error(`eval: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}\n${expr}`);
    return r.result.value;
  }
  async waitFor(expr, label, timeout = 30000) {
    const t0 = Date.now();
    let last;
    while (Date.now() - t0 < timeout) {
      try { last = await this.eval(expr); if (last) return last; } catch (e) { last = e.message; }
      await sleep(200);
    }
    throw new Error(`timeout esperando: ${label} (último valor: ${JSON.stringify(last)})`);
  }
  async drag(x1, y1, x2, y2) {
    await this.send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: x1, y: y1});
    await this.send('Input.dispatchMouseEvent', {type: 'mousePressed', x: x1, y: y1, button: 'left', clickCount: 1});
    for (let i = 1; i <= 6; i++) await this.send('Input.dispatchMouseEvent', {type: 'mouseMoved', x: x1 + (x2 - x1) * i / 6, y: y1 + (y2 - y1) * i / 6, button: 'left', buttons: 1});
    await this.send('Input.dispatchMouseEvent', {type: 'mouseReleased', x: x2, y: y2, button: 'left', clickCount: 1});
  }
  click(sel) { return this.eval(`(()=>{const e=document.querySelector(${JSON.stringify(sel)}); if(!e) throw new Error('no existe ${sel}'); e.click(); return true})()`); }
  async shot(name) {
    const r = await this.send('Page.captureScreenshot', {format: 'png'});
    fs.mkdirSync(SHOTS, {recursive: true});
    fs.writeFileSync(path.join(SHOTS, name), Buffer.from(r.data, 'base64'));
    console.log(`  captura: screenshots/${name}`);
  }
  async goto(url) {
    await this.send('Page.navigate', {url});
    await this.waitFor(`document.readyState === 'complete' && document.body?.dataset.ready === 'true'`, 'app lista', 60000);
  }
}

async function launchChrome() {
  const bin = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (!fs.existsSync(bin)) throw new Error(`No encuentro Chrome en ${bin}; define CHROME=/ruta/al/binario`);
  const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'ecobici-ui-'));
  const port = 9300 + Math.floor(Math.random() * 500);
  const args = [`--remote-debugging-port=${port}`, `--user-data-dir=${prof}`, '--no-first-run', '--no-default-browser-check',
    '--window-size=1440,900', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--hide-scrollbars', '--lang=es-MX', 'about:blank'];
  if (!flag('--headed')) args.unshift('--headless=new');
  const proc = spawn(bin, args, {stdio: 'ignore'});
  let target;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(250);
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === 'page'); } catch { /* aún no */ }
  }
  if (!target) { proc.kill(); throw new Error('Chrome no abrió el puerto de depuración'); }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const page = new Page(ws);
  const errors = [];
  page.handlers.push(msg => {
    if (msg.method === 'Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') errors.push(msg.params.args.map(a => a.value ?? a.description).join(' '));
    if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') errors.push(`${msg.params.entry.text} ${msg.params.entry.url || ''}`);
  });
  await page.send('Runtime.enable'); await page.send('Log.enable'); await page.send('Page.enable');
  await page.send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
  const close = async () => {
    try { ws.close(); } catch { /* ya cerrado */ }
    const done = new Promise(r => proc.once('exit', r));
    proc.kill();
    await Promise.race([done, sleep(5000)]);
    try { fs.rmSync(prof, {recursive: true, force: true, maxRetries: 5, retryDelay: 200}); } catch { /* perfil temporal; el sistema lo limpia */ }
  };
  return {page, errors, close};
}

// ───────────── recorrido ─────────────
async function run(base) {
  const shots = flag('--shots');
  const {page, errors, close} = await launchChrome();
  const ok = msg => console.log(`  ✓ ${msg}`);
  // Plegar y desplegar paneles: la columna se cierra, el panel queda inerte, el mapa (o los dos) se
  // redimensiona y la leyenda compacta aparece sobre el mapa; al desplegar todo vuelve como estaba.
  // Cambiar el ancho arrastrando el borde y con el teclado; se guarda por pestaña y el mapa se ajusta.
  const checkResize = async (label, side) => {
    const sel = side === 'left' ? '#resizeLeft' : '#resizeRight', panel = side === 'left' ? '#leftPanel' : '#rightPanel';
    const w = () => page.eval(`Math.round(document.querySelector('${panel}').getBoundingClientRect().width)`);
    const w0 = await w();
    const r = await page.eval(`(()=>{const b=document.querySelector('${sel}').getBoundingClientRect(); return {x:b.x+b.width/2, y:b.y+b.height/3}})()`);
    const dx = side === 'left' ? 80 : -80;
    await page.drag(r.x, r.y, r.x + dx, r.y);
    await sleep(400);
    const w1 = await w();
    const fit = await page.eval(`MAPS.filter(M=>M&&!M.map.getContainer().hidden).every(M=>Math.abs(M.map.getCanvas().clientWidth-M.map.getContainer().clientWidth)<=1)`);
    if (Math.abs(w1 - w0 - 80) > 2 || !fit) throw new Error(`${label}: arrastrar el borde ${side}: ${w0} → ${w1}, mapa ajustado ${fit}`);
    await page.eval(`(document.querySelector('${sel}').focus(), document.querySelector('${sel}').dispatchEvent(new KeyboardEvent('keydown', {key: '${side === 'left' ? 'ArrowLeft' : 'ArrowRight'}', bubbles: true})), true)`);
    await sleep(300);
    const w2 = await w();
    const saved = await page.eval(`JSON.parse(localStorage.getItem('ecobici.paneles'))[S.tab]`);
    const aria = await page.eval(`+document.querySelector('${sel}').getAttribute('aria-valuenow')`);
    if (w2 !== w1 - 16 || aria !== w2) throw new Error(`${label}: flecha sobre el borde ${side}: ${w1} → ${w2} (aria ${aria})`);
    await page.eval(`(document.querySelector('${sel}').dispatchEvent(new MouseEvent('dblclick', {bubbles: true})), true)`);
    await sleep(300);
    const w3 = await w();
    if (Math.abs(w3 - w0) > 1) throw new Error(`${label}: doble clic no restablece el ancho ${w0} (queda ${w3})`);
    await page.eval(`(document.activeElement?.blur(), true)`);
    ok(`${label}: el borde ${side === 'left' ? 'izquierdo' : 'derecho'} cambia el ancho arrastrando (${w0} → ${w1} px), con flechas (→ ${w2} px, aria-valuenow ${aria}), lo guarda (${JSON.stringify(saved)}) y doble clic lo restablece`);
  };
  // Regresión: plegar/desplegar la barra DERECHA 3 veces seguidas. En cada vuelta: el panel mide 0 al
  // plegar y su ancho original al abrir, el botón sigue visible y encima de todo, y el mapa (o los dos)
  // ocupa exactamente el espacio que deja el panel.
  const checkRightToggle = async label => {
    const read = () => page.eval(`(()=>{const tr=document.querySelector('#toggleRight'), b=tr.getBoundingClientRect(), top=document.elementFromPoint(b.x+b.width/2, b.y+b.height/2);
      return {panel:Math.round(document.querySelector('#rightPanel').getBoundingClientRect().width), stage:Math.round(document.querySelector('.stage').getBoundingClientRect().width),
        btn:{shown:!tr.hidden && b.width > 0 && b.x >= 0 && b.right <= innerWidth, onTop:top===tr||tr.contains(top), exp:tr.getAttribute('aria-expanded')},
        maps:MAPS.filter(M=>M&&!M.map.getContainer().hidden).map(M=>[M.map.getCanvas().clientWidth, Math.round(M.map.getContainer().getBoundingClientRect().width)])}})()`);
    const open0 = await read();
    if (!open0.panel || !open0.btn.shown || !open0.btn.onTop) throw new Error(`${label}: estado inicial ${JSON.stringify(open0)}`);
    for (let i = 1; i <= 3; i++) {
      await page.click('#toggleRight');
      await page.waitFor(`Math.round(document.querySelector('#rightPanel').getBoundingClientRect().width) === 0`, `${label}: vuelta ${i}, el panel derecho llega a 0 px`, 4000);
      await sleep(400);
      const shut = await read();
      if (!shut.btn.shown || !shut.btn.onTop || shut.btn.exp !== 'false' || Math.abs(shut.stage - (open0.stage + open0.panel)) > 2
        || shut.maps.some(([c, m]) => Math.abs(c - m) > 1) || Math.abs(shut.maps.reduce((a, [, m]) => a + m, 0) - shut.stage) > shut.maps.length * 2 + 1)
        throw new Error(`${label}: vuelta ${i} plegada ${JSON.stringify({open0, shut})}`);
      await page.click('#toggleRight');
      await page.waitFor(`Math.round(document.querySelector('#rightPanel').getBoundingClientRect().width) === ${open0.panel}`, `${label}: vuelta ${i}, el panel derecho vuelve a ${open0.panel} px`, 4000);
      await sleep(400);
      const back = await read();
      if (!back.btn.shown || !back.btn.onTop || back.btn.exp !== 'true' || Math.abs(back.stage - open0.stage) > 2 || back.maps.some(([c, m]) => Math.abs(c - m) > 1))
        throw new Error(`${label}: vuelta ${i} abierta ${JSON.stringify({open0, back})}`);
    }
    ok(`${label}: barra derecha plegada y abierta 3 veces (panel ${open0.panel} ↔ 0 px, mapa ${open0.stage} ↔ ${open0.stage + open0.panel} px, ${open0.maps.length} mapa${open0.maps.length > 1 ? 's' : ''} ajustado${open0.maps.length > 1 ? 's' : ''}, botón visible y encima)`);
  };
  const checkPanels = async (label, hasRight) => {
    const read = () => page.eval(`(()=>({w:document.querySelector('.stage').clientWidth,
      maps:MAPS.filter(M=>M&&!M.map.getContainer().hidden).map(M=>[M.map.getCanvas().clientWidth, M.map.getContainer().clientWidth]),
      legend:getComputedStyle(document.querySelector('#legend')).display, side:document.querySelector('#sideLegend').textContent.trim().length,
      compact:document.querySelector('#legend').textContent.trim().length}))()`);
    const before = await read();
    if (before.legend !== 'none' || !before.side) throw new Error(`${label}: con la barra abierta la leyenda debe ir en la barra izquierda: ${JSON.stringify(before)}`);
    await page.click('#toggleLeft');
    await page.waitFor(`document.querySelector('#main').classList.contains('left-collapsed') && document.querySelector('#leftPanel').inert && document.querySelector('#toggleLeft').getAttribute('aria-expanded') === 'false'`, `${label}: plegar izquierda`);
    if (hasRight) {
      await page.click('#toggleRight');
      await page.waitFor(`document.querySelector('#main').classList.contains('right-collapsed') && document.querySelector('#rightPanel').inert && Math.round(document.querySelector('#rightPanel').getBoundingClientRect().width) === 0`, `${label}: plegar derecha (panel en 0 px)`, 4000);
    }
    await page.waitFor(`document.querySelector('.stage').clientWidth > ${before.w}`, `${label}: el mapa se ensancha`, 5000);
    await sleep(500);
    const folded = await read();
    if (!(folded.w > before.w) || folded.maps.some(([c, m]) => Math.abs(c - m) > 1) || folded.legend === 'none' || !folded.compact) throw new Error(`${label}: al plegar ${JSON.stringify({before, folded})}`);
    await page.click('#toggleLeft');
    if (hasRight) await page.click('#toggleRight');
    await page.waitFor(`!document.querySelector('#main').classList.contains('left-collapsed') && !document.querySelector('#main').classList.contains('right-collapsed')`, `${label}: desplegar`);
    await page.waitFor(`Math.abs(document.querySelector('.stage').clientWidth - ${before.w}) <= 1`, `${label}: el mapa vuelve a su ancho`, 5000);
    await sleep(500);
    const back = await read();
    if (Math.abs(back.w - before.w) > 1 || back.maps.some(([c, m]) => Math.abs(c - m) > 1) || back.legend !== 'none') throw new Error(`${label}: al desplegar ${JSON.stringify({before, back})}`);
    ok(`${label}: ${hasRight ? 'paneles izquierdo y derecho se pliegan' : 'panel izquierdo se pliega'} (mapa de ${before.w} a ${folded.w} px${folded.maps.length > 1 ? `, ${folded.maps.length} mapas redimensionados` : ', redimensionado'}, leyenda compacta sobre el mapa) y ${hasRight ? 'vuelven' : 'vuelve'}`);
  };
  try {
    console.log(`Probando ${base}`);
    await page.send('Runtime.evaluate', {expression: 'localStorage.clear()'}).catch(() => {});
    await page.goto(`${base}/#ahora`);
    await page.waitFor('S.mapReady', 'mapa cargado', 60000);
    ok('Ahora carga con mapa');

    // Ahora: total y búsqueda
    const total = await page.waitFor(`/\\d/.test(document.querySelector('#totalBikes').textContent) && document.querySelector('#totalBikes').textContent`, 'total de bicis');
    const target = await page.eval(`(()=>{const s=S.snapshot.stations.filter(s=>s.renting&&s.installed&&s.bikes>3).sort((a,b)=>b.bikes-a.bikes)[0]; return {sn:s.short_name, name:s.name}})()`);
    await page.eval(`(()=>{const q=document.querySelector('#q'); q.value=${JSON.stringify(target.sn)}; q.dispatchEvent(new Event('input')); return true})()`);
    await page.waitFor(`document.querySelectorAll('#hits .result').length > 0`, 'resultados de búsqueda');
    await page.click('#hits .result');
    await page.waitFor(`!document.querySelector('#card').hidden && document.querySelectorAll('#rawFields dd').length >= 8`, 'ficha con campos raw');
    const raw = await page.eval(`(()=>{const keys=[...document.querySelectorAll('#rawFields dd')].map(d=>d.dataset.key); return {n:keys.length, keys, title:document.querySelector('#cardTitle').textContent, num:document.querySelector('#cardNum').textContent}})()`);
    if (raw.num !== target.sn) throw new Error(`la ficha abrió ${raw.num} en vez de ${target.sn}`);
    for (const k of ['num_bikes_available', 'num_docks_available', 'capacity', 'last_reported']) if (!raw.keys.includes(k)) throw new Error(`falta ${k} en la ficha`);
    ok(`buscar "${target.sn}" abre la ficha de ${raw.title} con ${raw.n} campos de raw (total ${total} bicis)`);
    // Enlaces externos en la ficha: Street View y Google Maps, en otra pestaña.
    const checkLinks = async label => {
      const l = await page.eval(`(()=>{const a=document.querySelector('#card #streetView'), b=document.querySelector('#card #openMaps'); const s=S.views[S.cardSide]?.stations[S.sel]||S.view.stations[S.sel];
        return {sv:a?.href, mp:b?.href, t:[a?.target,b?.target], rel:[a?.rel,b?.rel], lat:+s.lat, lon:+s.lon}})()`);
      if (!l.sv || !l.sv.startsWith('https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=') || !l.sv.endsWith(`${l.lat},${l.lon}`) || !l.mp?.endsWith(`query=${l.lat},${l.lon}`) || l.t.some(t => t !== '_blank') || l.rel.some(r => !/noopener/.test(r))) throw new Error(`${label}: enlaces de la ficha ${JSON.stringify(l)}`);
      ok(`${label}: la ficha trae "Ver en Street View" y "Abrir en Google Maps" (${l.lat}, ${l.lon}) en otra pestaña`);
    };
    await checkLinks('Ahora');
    await checkPanels('Ahora', false);
    await checkResize('Ahora', 'left');
    await sleep(2600);
    if (shots) await page.shot('ux-01-ahora-ficha.png');

    // Ahora: interruptor de zonas (AGEB de GET /api/zonas) con el estado actual
    const zonasOk = (await page.eval(`fetch('/api/zonas').then(r=>r.status)`)) === 200;
    const skipped = [];
    if (zonasOk) {
      await page.eval(`(()=>{const z=document.querySelector('#view-ahora .zones-on'); z.checked=true; z.dispatchEvent(new Event('change')); return true})()`);
      await page.waitFor(`S.zones.geo && S.map.getSource('zones')._data?.features?.length > 0 && !!document.querySelector('#zoneLegend')`, 'zonas en Ahora', 60000);
      const zn = await page.eval(`S.map.getSource('zones')._data.features.length`);
      await page.eval(`(()=>{const z=document.querySelector('#view-ahora .zones-on'); z.checked=false; z.dispatchEvent(new Event('change')); return true})()`);
      await page.waitFor(`!S.map.getSource('zones')._data?.features?.length && !document.querySelector('#zoneLegend')`, 'zonas apagadas');
      ok(`Ahora: el interruptor de zonas pinta ${zn} AGEB con su leyenda y se apaga`);
    } else {
      skipped.push('zonas (GET /api/zonas no responde 200)');
      console.log('  ⚠ OMITIDO: zonas en Ahora, el servidor no publica GET /api/zonas');
    }

    // Replay
    await page.click('#tab-replay');
    await page.waitFor(`S.replay.arm && !document.querySelector('#rk').disabled`, 'replay cargado', 60000);
    await page.waitFor(`document.querySelector('#rtime').textContent === '05:00'`, 'foto de las 05:00');
    await page.click('#next');
    await page.waitFor(`document.querySelector('#rtime').textContent === '05:15'`, 'foto de las 05:15');
    const fmt = n => Number(n).toLocaleString('es-MX');
    // Panel derecho: cada número de la foto y su acumulado desde 05:00 (suma de snap[0..k]; E+F con EF_acum).
    const readPanel = (side = 0) => page.eval(`(()=>{const out={};
      for (const tr of document.querySelectorAll('#rpTable tr[data-stat]')) out[tr.dataset.stat]={foto:tr.querySelector('td.foto[data-side="${side}"]')?.textContent, acum:tr.querySelector('td.acum[data-side="${side}"]')?.textContent};
      return {rows:out, alert:document.querySelector('#cuadreAlert')?.textContent.trim() ?? null}})()`);
    const checkPanel = async (side, k) => {
      const p = await readPanel(side);
      const sn = await page.eval(`S.replay.sel[${side}].snap.slice(0, ${k + 1})`);
      // Sin insignia "Cuadra": el aviso solo aparece si la API dice cuadre_ok=false.
      if (String(sn[k].cuadre_ok) !== 'true') throw new Error(`snap[${k}].cuadre_ok del escenario ${'AB'[side]} no es true`);
      if (p.alert) throw new Error(`aviso de descuadre con cuadre_ok=true: ${p.alert}`);
      const sum = f => sn.reduce((n, s) => n + (+s[f] || 0), 0);
      const want = {
        emitidas: [sn[k].emitidas, sum('emitidas')], recogidas: [sn[k].recogidas, sum('recogidas')], entregadas: [sn[k].entregadas, sum('entregadas')],
        salidas: [sn[k].salidas, sum('salidas')], llegadas: [sn[k].llegadas, sum('llegadas')],
        desvios: [sn[k].desvios_salida + sn[k].desvios_llegada, sum('desvios_salida') + sum('desvios_llegada')],
        a_rentable: [sn[k].a_rentable, sum('a_rentable')], a_no_rentable: [sn[k].a_no_rentable, sum('a_no_rentable')],
        E: [sn[k].E, sum('E')], F: [sn[k].F, sum('F')], EF: [sn[k].EF, sn[k].EF_acum],
      };
      for (const [key, [foto, acum]] of Object.entries(want)) {
        const got = p.rows[key];
        if (!got || got.foto !== fmt(foto) || got.acum !== fmt(acum)) throw new Error(`panel ${'AB'[side]} ${key}: muestra ${JSON.stringify(got)}, la API da foto ${foto} y acumulado ${acum}`);
      }
      return p;
    };
    const p1 = await checkPanel(0, 1);
    ok(`Replay 05:00 → 05:15: cuadre_ok=true sin aviso; foto/acumulado: salidas ${p1.rows.salidas.foto}/${p1.rows.salidas.acum}, emitidas ${p1.rows.emitidas.foto}/${p1.rows.emitidas.acum}, recogidas ${p1.rows.recogidas.foto}/${p1.rows.recogidas.acum}, entregadas ${p1.rows.entregadas.foto}/${p1.rows.entregadas.acum}, E+F ${p1.rows.EF.foto}/${p1.rows.EF.acum}`);
    await page.eval(`(openStation(S.replay.dayData._stations.findIndex(s=>s.lat!=null), {fly:false}), true)`);
    await page.waitFor(`!document.querySelector('#card').hidden`, 'ficha de replay');
    await checkLinks('Replay');
    await page.eval(`(closeCard(), true)`);
    await page.eval(`(()=>{S.replay.k=24; document.querySelector('#rk').value=24; renderReplay(); return true})()`);
    const p24 = await checkPanel(0, 24);
    const alertTxt = await page.eval(`(()=>{const s=S.replay.arm.snap[24], old=[s.cuadre_ok, s.descuadre_estaciones]; s.cuadre_ok=false; s.descuadre_estaciones=3; renderReplayPanel();
      const t=document.querySelector('#cuadreAlert')?.textContent.trim(); [s.cuadre_ok, s.descuadre_estaciones]=old; renderReplayPanel(); return t || null})()`);
    if (!alertTxt || !/3 estaciones/.test(alertTxt) || await page.eval(`!!document.querySelector('#cuadreAlert')`)) throw new Error(`aviso de descuadre: ${alertTxt}`);
    ok(`sin insignia "Cuadra"; si una foto llegara con cuadre_ok=false sale el aviso: "${alertTxt}"`);
    ok(`foto ${await page.eval(`S.replay.dayData.times[24]`)}: acumulados = suma de snap[0..24] (E+F acumulado ${p24.rows.EF.acum} = EF_acum del backend)`);
    // Valores por defecto: todo apagado salvo "Viajes desviados"; +N/−N siempre visibles.
    const defs = await page.eval(`(()=>({nums:document.querySelector('#showNums').checked, trips:document.querySelector('#showTrips').checked, detours:document.querySelector('#showDetours').checked, zones:document.querySelector('#view-replay .zones-on').checked,
      now:S.map.getSource('trips')._data.features.filter(f=>f.properties.k==='now').length, numImgs:S.map.getSource('st')._data.features.filter(f=>f.properties.img).length,
      deltas:S.map.getSource('st')._data.features.filter(f=>f.properties.dimg).length}))()`);
    if (defs.nums || defs.trips || !defs.detours || defs.zones || defs.now || defs.numImgs) throw new Error(`valores por defecto del Replay: ${JSON.stringify(defs)}`);
    ok(`por defecto: Bicis por estación, Viajes de la foto y Zonas apagados; Viajes desviados prendido; bolitas de colores y ${defs.deltas} estaciones con +N/−N`);
    // Interruptor "Bicis por estación": de bolitas de color a números con fondo blanco y tono del estado.
    await page.click('#showNums');
    const nm = await page.eval(`(()=>{const f=S.map.getSource('st')._data.features; return {num:f.filter(x=>x.properties.anchor==='num'&&x.properties.img.startsWith('num|')).length, n:f.length, okState:f.every(x=>x.properties.img.split('|')[1]===Object.keys(ST_COLOR).find(k=>ST_COLOR[k]===x.properties.color))}})()`);
    if (nm.num !== nm.n || !nm.okState) throw new Error(`"Bicis por estación" no puso un número por estación con el color de su estado: ${JSON.stringify(nm)}`);
    await page.click('#showNums');
    if (await page.eval(`S.map.getSource('st')._data.features.some(f=>f.properties.img)`)) throw new Error('"Bicis por estación" no se apaga');
    ok(`interruptor "Bicis por estación": ${nm.num} estaciones pasan de bolita a número con el tono de su estado, y vuelven`);
    await checkRightToggle('Replay con 1 escenario');
    await page.click('#showTrips');
    // Viajes de la foto: solo los que llegaron en el intervalo y los desvíos.
    const tr = await page.eval(`(()=>{const k=S.replay.k, t=S.replay.dayData.trips, d=S.map.getSource('trips')._data.features;
      const lo=15*(k-1), hi=15*k; let ok=0; for(let j=0;j<t.o.length;j++) if(t.arr[j]>lo&&t.arr[j]<=hi&&t.o[j]>=0&&t.d[j]>=0&&t.o[j]!==t.d[j]) ok++;
      return {now:d.filter(f=>f.properties.k==='now').length, detour:d.filter(f=>f.properties.k==='detour').length, other:d.filter(f=>!['now','detour'].includes(f.properties.k)).length, expected:ok}})()`);
    if (tr.other) throw new Error(`hay ${tr.other} líneas de viajes que no son de la foto`);
    if (tr.now > tr.expected) throw new Error(`más líneas (${tr.now}) que viajes que llegaron en la foto (${tr.expected})`);
    ok(`viajes en el mapa: ${tr.now} que llegaron en la foto y ${tr.detour} desvíos; sin viajes de otras fotos`);
    await page.click('#showTrips');  // vuelve al valor por defecto (apagado) para las capturas
    if (shots) {
      // Foto con entregas, recogidas y cambios de etiqueta, centrada donde se juntan los tres.
      const focus = await page.eval(`(()=>{const a=S.replay.arm, st=S.replay.dayData._stations; let best=null;
        for(let k=8;k<a.est.length-4;k++){const rows=a.est[k]; const p=rows.filter(r=>r[1]>0).length, m=rows.filter(r=>r[2]>0).length, l=rows.filter(r=>r[3]-r[4]!==0).length;
          const sc=Math.min(p,m,l); if(sc>0&&(!best||sc>best.sc)) best={k,sc}}
        const k=best?best.k:20, rows=a.est[k]; let c=null, cs=-1;
        for(const r of rows){ if(!(r[1]>0||r[2]>0)) continue; const s=st[r[0]]; if(s.lat==null) continue;
          const near=rows.filter(q=>{const t=st[q[0]]; return t.lat!=null&&Math.abs(t.lat-s.lat)<0.012&&Math.abs(t.lon-s.lon)<0.02});
          const kinds=[near.some(q=>q[1]>0),near.some(q=>q[2]>0),near.some(q=>q[3]-q[4]!==0)].filter(Boolean).length*100+near.length;
          if(kinds>cs){cs=kinds;c=s} }
        S.replay.k=k; document.querySelector('#rk').value=k; renderReplay(); S.map.jumpTo({center:[c.lon,c.lat], zoom:14.1});
        return {k, t:S.replay.dayData.times[k], c:c.short_name}})()`);
      await sleep(2500);
      ok(`captura de Replay en la foto ${focus.t} alrededor de la estación ${focus.c}`);
      await page.shot('ux-02-replay.png');
      await page.eval(`(()=>{const a=S.replay.arm,k=S.replay.k; const r=a.est[k].filter(r=>r[1]>0||r[2]>0).sort((x,y)=>(y[1]+y[2])-(x[1]+x[2]))[0]; openStation(r[0]); return r[0]})()`);
      await page.waitFor(`!document.querySelector('#card').hidden && !!document.querySelector('#cuadreEstacion')`, 'ficha de replay');
      await sleep(2600);
      await page.shot('ux-03-replay-ficha-cuadre.png');
      await page.eval(`closeCard()`);
    }

    // Coropleta por AGEB en Replay: colores y cortes de los pines; se actualiza con la foto.
    if (zonasOk) {
      await page.eval(`(()=>{const z=document.querySelector('#view-replay .zones-on'); z.checked=true; z.dispatchEvent(new Event('change')); return true})()`);
      await page.waitFor(`S.zones.geo && S.map.getSource('zones')._data?.features?.length > 0 && !!document.querySelector('#zoneLegend')`, 'zonas en Replay', 60000);
      const zA = await page.eval(`JSON.stringify(S.map.getSource('zones')._data.features.map(f=>f.properties.b))`);
      await page.click('#next');
      await page.waitFor(`JSON.stringify(S.map.getSource('zones')._data.features.map(f=>f.properties.b)) !== ${JSON.stringify(zA)}`, 'zonas cambian con la foto', 10000);
      // Cada AGEB: suma de bicis/anclajes de sus estaciones, estado y color iguales a los de los pines; tooltip completo.
      const zb = await page.eval(`(()=>{const f=S.map.getSource('zones')._data.features, a=S.replay.arm, k=S.replay.k, st=S.replay.dayData.stations;
        const idx=new Map(S.replay.dayData._stations.map((s,i)=>[snKey(s.short_name),i])); const geo=new Map(S.zones.geo.map(g=>[g.properties.cvegeo,g]));
        let bad=0; const by={};
        for(const x of f){const p=x.properties; let b=0; for(const sn of geo.get(p.cvegeo).properties.estaciones){const i=idx.get(snKey(sn)); if(i!==undefined&&!st.out_of_service[i]) b+=a.bikes[k][i];}
          if(b!==p.b||p.st!==stationState(p.b,p.docks,true)||p.c!==ST_COLOR[p.st]) bad++; by[p.st]=(by[p.st]||0)+1;}
        const tip=zoneTooltip(f[0].properties); return {n:f.length, total:S.zones.geo.length, bad, by, tipOk:tip.includes(f[0].properties.cvegeo)&&tip.includes('Estaciones')&&tip.includes('capacidad')}})()`);
      if (zb.bad || !zb.tipOk) throw new Error(`zonas con suma, estado, color o tooltip incorrectos: ${JSON.stringify(zb)}`);
      ok(`coropleta por AGEB: ${zb.n} de ${zb.total} zonas con estaciones activas, sumas y colores de los pines (${Object.entries(zb.by).map(([k, v]) => `${k} ${v}`).join(', ')}), tooltip con AGEB, alcaldía, estaciones y bicis/capacidad; cambia con cada foto`);
      if (shots) {
        await page.eval(`(()=>{S.replay.k=60; document.querySelector('#rk').value=60; renderReplay(); S.map.jumpTo({center:[-99.168,19.412], zoom:13.2}); return true})()`);
        await sleep(2500);
        await page.shot('ux-06-coropleta.png');
      }
    } else {
      skipped.push('coropleta en Replay');
      console.log('  ⚠ OMITIDO: coropleta en Replay, el servidor no publica GET /api/zonas');
    }

    // Comparar dos escenarios lado a lado
    const second = await page.eval(`(()=>{const ks=[...document.querySelectorAll('#rarms input')].map(i=>i.value).filter(v=>v!==S.replay.arm.arm); return ks.includes('sin_rebalanceo')?'sin_rebalanceo':ks[0]})()`);
    await page.click(`#rarms input[value="${second}"]`);
    await page.waitFor(`S.replay.sel.length === 2 && MAPS[1]?.ready && !document.querySelector('#map2').hidden`, 'segundo mapa', 60000);
    await page.waitFor(`!!document.querySelector('#rpTable td.foto[data-side="1"]')`, 'panel con dos columnas');
    const disabled = await page.eval(`[...document.querySelectorAll('#rarms input')].filter(i=>!i.checked).every(i=>i.disabled)`);
    if (!disabled) throw new Error('con 2 escenarios los demás deberían quedar deshabilitados (máximo 2)');
    await page.eval(`(()=>{S.replay.k=1; document.querySelector('#rk').value=1; renderReplay(); return true})()`);
    await checkPanel(0, 1); await checkPanel(1, 1);
    await page.click('#next');
    await page.waitFor(`document.querySelector('#rtime').textContent === S.replay.dayData.times[2]`, 'avanza la foto');
    await checkPanel(0, 2); await checkPanel(1, 2);
    await page.eval(`(S.map.jumpTo({center:[-99.18,19.43], zoom:14.2}), true)`);
    await sleep(400);
    const sync = await page.eval(`(()=>{const a=MAPS[0].map, b=MAPS[1].map; return {dx:Math.abs(a.getCenter().lng-b.getCenter().lng), dy:Math.abs(a.getCenter().lat-b.getCenter().lat), dz:Math.abs(a.getZoom()-b.getZoom()), zones:b.getSource('zones')._data?.features?.length, la:document.querySelector('#mapLabel0').textContent, lb:document.querySelector('#mapLabel1').textContent}})()`);
    if (sync.dx > 1e-6 || sync.dy > 1e-6 || sync.dz > 1e-6) throw new Error(`los mapas no están sincronizados: ${JSON.stringify(sync)}`);
    if (zonasOk && !sync.zones) throw new Error('la coropleta no aplica al segundo mapa');
    // Interruptores separados de viajes y desvíos; aplican a los dos mapas.
    if (!(await page.eval(`document.querySelector('#showTrips').checked`))) await page.click('#showTrips');
    const kinds = () => page.eval(`MAPS.map(M=>{const f=M.map.getSource('trips')._data.features; return {now:f.filter(x=>x.properties.k==='now').length, detour:f.filter(x=>x.properties.k==='detour').length}})`);
    await page.eval(`(()=>{const n=S.replay.dayData.times.length; let k=20; for(let j=8;j<n;j++){ if(S.replay.sel.every(a=>(a.desvios?.[j]||[]).some(d=>d[2]!==d[3]))){k=j;break} } S.replay.k=k; document.querySelector('#rk').value=k; renderReplay(); return k})()`);
    const both = await kinds();
    if (!both[0].detour || !both[1].detour) throw new Error(`no encontré una foto con desvíos en ambos escenarios: ${JSON.stringify(both)}`);
    await page.click('#showTrips');
    const noTrips = await kinds();
    await page.click('#showTrips'); await page.click('#showDetours');
    const noDetours = await kinds();
    await page.click('#showDetours');
    if (noTrips.some(m => m.now) || noDetours.some(m => m.detour)) throw new Error(`los interruptores no apagan su capa: ${JSON.stringify({noTrips, noDetours})}`);
    if (noTrips.some((m, j) => m.detour !== both[j].detour) || noDetours.some((m, j) => m.now !== both[j].now)) throw new Error(`un interruptor apagó la otra capa: ${JSON.stringify({both, noTrips, noDetours})}`);
    if (!both[0].now || !both[1].now) throw new Error(`faltan viajes de la foto en algún mapa: ${JSON.stringify(both)}`);
    await page.click('#showNums');
    const numsBoth = await page.eval(`MAPS.map(M=>M.map.getSource('st')._data.features.filter(f=>f.properties.anchor==='num').length)`);
    await page.click('#showNums');
    if (numsBoth.some(n => !n)) throw new Error(`"Bicis por estación" no aplica a los dos mapas: ${JSON.stringify(numsBoth)}`);
    ok(`"Bicis por estación" aplica a los dos mapas; interruptores "Viajes de la foto" y "Viajes desviados" independientes en ambos mapas (A ${both[0].now} viajes/${both[0].detour} desvíos, B ${both[1].now}/${both[1].detour})`);
    await checkRightToggle('Replay con 2 escenarios');
    await page.eval(`(document.querySelector('#resizeRight').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', shiftKey: true, bubbles: true})), document.activeElement?.blur(), true)`);
    await sleep(400);
    await checkRightToggle('Replay con 2 escenarios y ancho cambiado');
    await page.eval(`(document.querySelector('#resizeRight').dispatchEvent(new MouseEvent('dblclick', {bubbles: true})), true)`);
    await sleep(400);
    await checkPanels('Replay con 2 escenarios', true);
    await checkResize('Replay con 2 escenarios', 'right');
    const k0 = await page.eval(`S.replay.k`);
    if (k0 !== await page.eval(`S.replay.k`)) throw new Error('las flechas sobre el borde movieron la foto del Replay');
    ok(`comparación: mapas "${sync.la}" y "${sync.lb}" en la misma foto, mismo zoom y paneo${zonasOk ? ', coropleta en ambos' : ''}, cuadre y acumulados de A y B correctos`);
    if (shots) {
      await page.eval(`(()=>{S.replay.k=34; document.querySelector('#rk').value=34; renderReplay(); S.map.jumpTo({center:[-99.168,19.415], zoom:12.9}); return true})()`);
      await sleep(3000);
      await page.shot('ux-07-replay-comparar.png');
    }
    await page.click(`#rarms input[value="${second}"]`);
    await page.waitFor(`S.replay.sel.length === 1 && document.querySelector('#map2').hidden && !document.querySelector('#rpTable td.foto[data-side="1"]')`, 'vuelve a un escenario');
    await page.eval(`(()=>{const z=document.querySelector('#view-replay .zones-on'); z.checked=false; z.dispatchEvent(new Event('change')); return true})()`);
    ok('al quitar un escenario vuelve a un solo mapa');

    // Predicción · Pronóstico
    await page.click('#tab-prediccion');
    await page.waitFor(`S.live.models && document.querySelectorAll('input[name="model"]').length > 0`, 'modelos cargados', 30000);
    const models = await page.eval(`[...document.querySelectorAll('input[name="model"]')].map(i=>({key:i.value, ok:!i.disabled}))`);
    const avail = models.filter(m => m.ok).map(m => m.key);
    if (!avail.length) throw new Error('ningún modelo disponible');
    const pub = await page.eval(`document.querySelector('#lastPublished').textContent + ' · ' + document.querySelector('#modelsUpdated').textContent`);
    ok(`modelos: ${models.map(m => `${m.key}${m.ok ? '' : ' (deshabilitado)'}`).join(', ')}; viajes publicados y modelos: ${pub}`);
    const order = avail.filter(k => k !== 'lgbm_directo').concat(avail.includes('lgbm_directo') ? ['lgbm_directo'] : []);
    for (const key of order) {
      // El horizonte depende de la forma del modelo: diaria → solo día completo; directa → solo 1 a 4 h.
      await page.click(`input[name="model"][value="${key}"]`);
      const hzs = await page.eval(`(()=>{const mi=modelInfo('${key}'); return {forma:mi.forma, api:mi.horizontes ?? null, shown:[...document.querySelectorAll('#horizon label')].filter(l=>!l.hidden).map(l=>l.querySelector('input').value), checked:document.querySelector('input[name="hz"]:checked')?.value}})()`);
      const want = hzs.api ? hzs.api.map(String) : hzs.forma === 'diaria' ? ['dia'] : ['1', '2', '3', '4'];
      if (hzs.forma === 'diaria' ? JSON.stringify(want) !== '["dia"]' : want.includes('dia')) throw new Error(`horizontes del backend para ${key} no siguen su forma: ${JSON.stringify(hzs)}`);
      if (JSON.stringify(hzs.shown) !== JSON.stringify(want) || !want.includes(hzs.checked)) throw new Error(`horizontes de ${key} (${hzs.forma}): ${JSON.stringify(hzs)}`);
      const hz = hzs.checked;
      const prevId = await page.eval(`S.live.forecast?.id ?? null`);
      await page.click('#runForecast');
      await page.waitFor(`(S.live.forecast && S.live.forecast.id !== ${JSON.stringify(prevId)} && S.live.forecast.model === '${key}') || !document.querySelector('#ferror').hidden`, `pronóstico ${key}`, 300000);
      const err = await page.eval(`document.querySelector('#ferror').hidden ? null : document.querySelector('#ferror').textContent`);
      if (err) throw new Error(`pronóstico ${key}: ${err}`);
      const f = await page.eval(`(()=>{const f=S.live.forecast; return {n:f.minutes.length, last:f.minutes.at(-1), riesgos:(f.riesgos||[]).length, info:document.querySelector('#forecastInfo').innerText.replace(/\\n+/g,' | ')}})()`);
      if (!f.n) throw new Error(`pronóstico ${key} sin pasos`);
      ok(`pronóstico ${key} (${hzs.forma}; horizontes ofrecidos ${hzs.shown.join(', ')}; pedido ${hz}): ${f.n} pasos hasta +${f.last} min, ${f.riesgos} en riesgo · ${f.info}`);
    }
    // Zonas en Pronóstico: interruptor propio, apagado por defecto, con la proyección del minuto elegido.
    if (zonasOk) {
      const offs = await page.eval(`[...document.querySelectorAll('[data-zones] .zones-on')].map(c=>c.closest('[data-zones]').dataset.zones+':'+c.checked).join(' ')`);
      if (/true/.test(offs)) throw new Error(`algún interruptor de zonas quedó prendido al llegar a Pronóstico: ${offs}`);
      await page.click('[data-zones="pronostico"] .zones-on');
      await page.waitFor(`S.map.getSource('zones')._data?.features?.length > 0`, 'zonas en Pronóstico', 30000);
      const zf = await page.eval(`(()=>{const f=S.live.forecast, j=S.live.fk, idx=new Map(f._stations.map((s,i)=>[snKey(s.short_name),i])), geo=new Map(S.zones.geo.map(g=>[g.properties.cvegeo,g]));
        const feats=S.map.getSource('zones')._data.features; let bad=0;
        for (const x of feats) { let b=0; for (const sn of geo.get(x.properties.cvegeo).properties.estaciones) { const i=idx.get(snKey(sn)); if (i!==undefined && f._stations[i].cap) b+=f.proyeccion[j][i]; } if (b!==x.properties.b) bad++; }
        return {n:feats.length, bad, others:['ahora','replay','asignacion'].map(k=>document.querySelector('[data-zones="'+k+'"] .zones-on').checked)}})()`);
      if (zf.bad || zf.others.some(Boolean)) throw new Error(`zonas de Pronóstico: ${JSON.stringify(zf)}`);
      ok(`Pronóstico: zonas propias con la proyección del minuto elegido (${zf.n} AGEB); Ahora, Replay y Asignación siguen apagados`);
      await page.click('[data-zones="pronostico"] .zones-on');  // vuelve al valor por defecto para la captura
    }
    // "Bicis por estación" en Pronóstico: propio, apagado por defecto, con el estado proyectado.
    const nf = await page.eval(`(()=>{const before=document.querySelector('#showNumsF').checked; document.querySelector('#showNumsF').click();
      const f=S.map.getSource('st')._data.features, j=S.live.fk, p=S.live.forecast;
      const ok=f.every(x=>x.properties.anchor==='num' && x.properties.img.split('|')[2]===String(p.proyeccion[j][x.properties.i]) && x.properties.img.split('|')[1]===stationState(p.proyeccion[j][x.properties.i], (p._stations[x.properties.i].cap ?? Infinity)-p.proyeccion[j][x.properties.i], true));
      const r={before, n:f.length, ok, replay:document.querySelector('#showNums').checked, asign:document.querySelector('#showNumsA').checked};
      document.querySelector('#showNumsF').click(); return r})()`);
    if (nf.before || !nf.ok || nf.replay || nf.asign) throw new Error(`"Bicis por estación" en Pronóstico: ${JSON.stringify(nf)}`);
    await page.eval(`(openStation(S.live.forecast._byShort.get(S.live.forecast.riesgos?.[0]?.short_name) ?? 0, {fly:false}), true)`);
    await page.waitFor(`!document.querySelector('#card').hidden`, 'ficha de pronóstico');
    await checkLinks('Pronóstico');
    await page.eval(`(closeCard(), true)`);
    ok(`Pronóstico: "Bicis por estación" propio (apagado por defecto) pone ${nf.n} números con el estado proyectado; Replay y Asignación no cambian`);
    await checkPanels('Pronóstico', true);
    await page.eval(`(()=>{const r=document.querySelector('#fk'); r.value=Math.min(+r.max, 7); r.dispatchEvent(new Event('input')); S.map.jumpTo({center:[-99.168,19.418], zoom:13.4}); return true})()`);
    await sleep(5000);
    if (shots) await page.shot('ux-04-pronostico.png');

    // Predicción · Asignación
    await page.click('#sub-asignacion');
    await page.waitFor(`!document.querySelector('#assignForm').hidden`, 'formulario de asignación');
    await page.eval(`document.querySelector('#ahours').value='1'`);
    await page.click('#startAssign');
    await page.waitFor(`S.live.session && (S.live.session.pasos||[]).length >= 1 || !document.querySelector('#aerror').hidden`, 'primer paso de la asignación', 300000);
    const aerr = await page.eval(`document.querySelector('#aerror').hidden ? null : document.querySelector('#aerror').textContent`);
    if (aerr) throw new Error(`asignación: ${aerr}`);
    const a1 = await page.eval(`(()=>{const s=S.live.session,p=s.pasos[0]; return {id:S.live.sessionId, estado:s.estado, t:p.t, emitidas:p.emitidas.length, visitas:p.visitas, bicis:p.bicis_a_mover, sig:s.siguiente_paso, title:document.querySelector('#apTitle').textContent, rows:document.querySelectorAll('#apList .row').length}})()`);
    ok(`asignación ${a1.id} ${a1.estado}: primer paso ${a1.t} con ${a1.emitidas} órdenes (${a1.visitas} visitas, ${a1.bicis} bicis), siguiente paso ${a1.sig}; panel "${a1.title}" con ${a1.rows} filas`);
    if (shots) {
      await page.eval(`(()=>{const p=S.live.session.pasos.at(-1); const sn=p.emitidas[0]?.short_name; const s=S.snapshot.stations.find(x=>x.short_name===sn); if(s) S.map.jumpTo({center:[s.lon,s.lat], zoom:12.6}); return true})()`);
      await sleep(5000);
      await page.shot('ux-05-asignacion.png');
    }
    if (zonasOk) {
      const before = await page.eval(`document.querySelector('[data-zones="asignacion"] .zones-on').checked`);
      if (before) throw new Error('las zonas de Asignación deberían arrancar apagadas');
      await page.click('[data-zones="asignacion"] .zones-on');
      await page.waitFor(`S.view.mode === 'asignacion' && S.map.getSource('zones')._data?.features?.length > 0`, 'zonas en Asignación', 30000);
      const za = await page.eval(`(()=>({n:S.map.getSource('zones')._data.features.length, pron:document.querySelector('[data-zones="pronostico"] .zones-on').checked}))()`);
      await page.click('[data-zones="asignacion"] .zones-on');
      ok(`Asignación: zonas propias con el estado actual del feed (${za.n} AGEB); el de Pronóstico no cambió (${za.pron ? 'prendido' : 'apagado'})`);
    }
    const na = await page.eval(`(()=>{const before=document.querySelector('#showNumsA').checked; document.querySelector('#showNumsA').click();
      const f=S.map.getSource('st')._data.features; const st=S.snapshot.stations;
      const ok=f.length>0 && f.every(x=>x.properties.anchor==='num' && x.properties.img.split('|')[1]===stationState(+st[x.properties.i].bikes, +st[x.properties.i].docks, st[x.properties.i].renting&&st[x.properties.i].installed));
      const r={before, n:f.length, ok, pron:document.querySelector('#showNumsF').checked}; document.querySelector('#showNumsA').click(); return r})()`);
    if (na.before || !na.ok || na.pron) throw new Error(`"Bicis por estación" en Asignación: ${JSON.stringify(na)}`);
    ok(`Asignación: "Bicis por estación" propio pone ${na.n} números con el estado actual del feed`);
    await page.goto(`${base}/#prediccion/asignacion`);
    await page.waitFor(`S.live.session && S.live.sessionId === ${JSON.stringify(a1.id)}`, 'sesión retomada tras recargar', 30000);
    ok('al recargar la página se retoma la sesión activa');
    if (await page.eval(`S.live.session.estado === 'corriendo'`)) {
      await page.click('#stopAssign');
      await page.waitFor(`S.live.session.estado === 'detenida'`, 'sesión detenida', 30000);
      ok(`asignación detenida: ${await page.eval(`document.querySelector('#sessState').textContent`)}`);
    } else {
      ok(`la sesión ya estaba ${await page.eval(`S.live.session.estado`)}; no hay nada que detener`);
    }

    // El plegado y el ancho se recuerdan por pestaña (localStorage) al recargar.
    await page.click('#tab-replay');
    await sleep(400);
    const wBase = await page.eval(`Math.round(document.querySelector('#leftPanel').getBoundingClientRect().width)`);
    await page.eval(`(document.querySelector('#resizeLeft').dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', shiftKey: true, bubbles: true})), true)`);
    await sleep(400);
    const wSaved = await page.eval(`Math.round(document.querySelector('#leftPanel').getBoundingClientRect().width)`);
    if (wSaved !== wBase + 48) throw new Error(`Mayús+→ sobre el borde: ${wBase} → ${wSaved}`);
    await page.goto(`${base}/#replay`);
    await page.waitFor(`Math.round(document.querySelector('#leftPanel').getBoundingClientRect().width) === ${wSaved}`, 'Replay recuerda el ancho del panel', 15000);
    await page.click('#tab-ahora');
    await sleep(400);
    if (await page.eval(`Math.round(document.querySelector('#leftPanel').getBoundingClientRect().width)`) === wSaved) throw new Error('el ancho de Replay se aplicó a Ahora');
    await page.click('#tab-replay');
    await page.eval(`(document.querySelector('#resizeLeft').dispatchEvent(new MouseEvent('dblclick', {bubbles: true})), true)`);
    ok(`el ancho del panel (${wSaved} px) se recuerda por pestaña al recargar`);
    await page.click('#toggleLeft');
    await page.goto(`${base}/#replay`);
    await page.waitFor(`document.querySelector('#main').classList.contains('left-collapsed')`, 'Replay recuerda la barra plegada', 15000);
    await page.click('#tab-ahora');
    if (await page.eval(`document.querySelector('#main').classList.contains('left-collapsed')`)) throw new Error('el plegado de Replay se aplicó a Ahora');
    await page.click('#tab-replay');
    await page.click('#toggleLeft');
    ok('el plegado se recuerda por pestaña al recargar y no pasa a otras pestañas');

    // Vuelta por las tres pestañas para cazar errores
    for (const t of ['ahora', 'replay', 'prediccion']) { await page.click(`#tab-${t}`); await sleep(600); }
    // Sin /api/zonas, el único 404 esperado es el de la sonda de arriba.
    const bad = errors.filter(e => !/favicon/.test(e) && (zonasOk || !/404.*\/api\/zonas/.test(e)));
    if (bad.length) throw new Error(`errores de consola:\n  ${bad.join('\n  ')}`);
    ok('tres pestañas sin errores de consola');
    console.log(skipped.length ? `test_ui: PASS con partes omitidas: ${skipped.join('; ')}` : 'test_ui: PASS');
  } finally { await close(); }
}

(async () => {
  if (flag('--serve-fixtures')) {
    const port = +opt('--port', 8790);
    await fixtureServer(port);
    console.log(`Servidor simulado con fixtures en http://127.0.0.1:${port}  (Ctrl+C para salir)`);
    return;
  }
  let srv = null, proc = null, base = opt('--url');
  if (!base) {
    const port = 8700 + Math.floor(Math.random() * 200);
    if (flag('--fixtures')) { srv = await fixtureServer(port); console.log('Modo fixtures (servidor simulado)'); }
    else { console.log('Levantando el servidor real…'); proc = await realServer(port); }
    base = `http://127.0.0.1:${port}`;
  }
  try { await run(base.replace(/\/$/, '')); }
  catch (e) { console.error(`test_ui: FAIL\n${e.message}`); process.exitCode = 1; }
  finally { srv?.close(); proc?.kill(); }
})();
