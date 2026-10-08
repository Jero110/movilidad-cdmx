// Captura del Replay del 1 sep 2025 para reporte-caso (Figs. de la app). Reutiliza el driver de scripts/ecobici_mapa/test_ui.cjs.
// Uso (desde la raíz): node report/figs/captura_app.cjs [salida]   → PNG + cajas.json; luego uv run python3 report/figs/recortes_app.py [salida]
'use strict';
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawn} = require('node:child_process');
const ROOT = path.resolve(__dirname, '..', '..');
const APP = path.join(ROOT, 'scripts', 'ecobici_mapa');
const OUTDIR = path.resolve(process.argv[2] || path.join(os.tmpdir(), 'capturas-caso'));
fs.mkdirSync(OUTDIR, {recursive: true});
const HERE = APP, SHOTS = OUTDIR;
const argv = ['--no-live'];
const flag = f => argv.includes(f);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const src = fs.readFileSync(path.join(APP, 'test_ui.cjs'), 'utf8');
const a = src.indexOf('// ───────────── servidor real'), b = src.indexOf('// ───────────── recorrido');
eval(src.slice(a, b).replace('class Page', 'globalThis.Page = class Page').replace('async function launchChrome', 'globalThis.launchChrome = async function launchChrome').replace('async function realServer', 'globalThis.realServer = async function realServer'));

(async () => {
  const port = 8800 + Math.floor(Math.random() * 100);
  const srv = await realServer(port);
  const {page, errors, close} = await launchChrome();
  try {
    await page.send('Emulation.setDeviceMetricsOverride', {width: 2400, height: 803, deviceScaleFactor: 1, mobile: false});
    await page.goto(`http://127.0.0.1:${port}/#replay/2025-09-01`);
    await page.click('#tab-replay');
    await page.waitFor(`S.replay.arm && !document.querySelector('#rk').disabled && document.querySelector('#rday').value==='2025-09-01'`, 'replay', 90000);
    // Deja seleccionados exactamente los escenarios `want`, en ese orden (uno o dos).
    const checked = () => page.eval(`[...document.querySelectorAll('#rarms input')].filter(i=>i.checked).map(i=>i.value)`);
    const setArms = async want => {
      while ((await checked()).length > 1) { await page.click(`#rarms input[value="${(await checked()).at(-1)}"]`); await sleep(1500); }
      const only = (await checked())[0];
      if (only !== want[0]) { await page.click(`#rarms input[value="${want[0]}"]`); await sleep(1500); await page.click(`#rarms input[value="${only}"]`); await sleep(1500); }
      if (want[1]) await page.click(`#rarms input[value="${want[1]}"]`);
      await page.waitFor(`S.replay.sel.length===${want.length} && ${want.length === 2 ? 'MAPS[1]?.ready' : "document.querySelector('#map2').hidden"} && ` +
        want.map((a, j) => `S.replay.sel[${j}].arm==='${a}'`).join(' && '), 'escenarios ' + want, 90000);
      // Como en los pies de figura: sin viajes desviados ni cambios de dañadas; solo zonas y órdenes.
      for (const id of ['#showDetours', '#showDamaged', '#showTrips', '#showNums'])
        if (await page.eval(`!!document.querySelector('${id}')?.checked`)) await page.click(id);
    };
    if (!(await page.eval(`document.querySelector('#view-replay .zones-on').checked`)))
      await page.eval(`(()=>{const z=document.querySelector('#view-replay .zones-on'); z.checked=true; z.dispatchEvent(new Event('change')); return true})()`);
    if (!(await page.eval(`document.querySelector('#main').classList.contains('left-collapsed')`))) await page.click('#toggleLeft');
    const shots = [];
    const jobs = [[['ecobici', 'lgbm_directo'], 'eco-lgbm', 803], [['sin_rebalanceo', 'lgbm_directo'], 'sin-lgbm', 803], [['sin_rebalanceo'], 'sin', 2077]];
    for (const [arms, tag, height] of jobs) {
      await page.send('Emulation.setDeviceMetricsOverride', {width: 2400, height, deviceScaleFactor: 1, mobile: false});
      await sleep(1000);
      await setArms(arms);
      await page.waitFor(`S.map.getSource('zones')?._data?.features?.length > 0`, 'zonas', 90000);
      for (const hhmm of ['09:00', '21:00']) {
        const k = await page.eval(`S.replay.dayData.times.indexOf('${hhmm}')`);
        if (k < 0) throw new Error('no hay foto ' + hhmm);
        await page.eval(`(()=>{S.replay.k=${k}; document.querySelector('#rk').value=${k}; renderReplay();
          const st=S.replay.dayData._stations.filter(s=>s.lat!=null); const lo=st.map(s=>s.lon), la=st.map(s=>s.lat);
          S.map.fitBounds([[Math.min(...lo),Math.min(...la)],[Math.max(...lo),Math.max(...la)]], {padding:30, animate:false}); return true})()`);
        await sleep(5000);
        const box = await page.eval(`(()=>{const st=S.replay.dayData._stations.filter(s=>s.lat!=null);
          return MAPS.slice(0,S.replay.sel.length).map(M=>{const r=M.map.getContainer().getBoundingClientRect(); const xs=st.map(s=>M.map.project([s.lon,s.lat]).x);
            return {left:r.left, width:r.width, top:r.top, height:r.height, x0:Math.min(...xs), x1:Math.max(...xs)}})})()`);
        const name = `${tag}-${hhmm.replace(':', '')}.png`;
        await page.shot(name);
        await page.eval(`(()=>{const s=document.createElement('style'); s.id='limpio'; s.textContent='#legend,.maplibregl-ctrl,.maplibregl-control-container,[id^=mapLabel],.replay-bar,#rbar,.timebar{display:none!important}'; document.head.appendChild(s); let e=document.querySelector('#rtime'); while(e && !['absolute','fixed'].includes(getComputedStyle(e).position)) e=e.parentElement; if(e){e.dataset.oculto='1'; e.style.visibility='hidden'} return true})()`);
        await sleep(800);
        await page.shot(name.replace('.png', '-limpio.png'));
        await page.eval(`(document.querySelector('#limpio').remove(), document.querySelectorAll('[data-oculto]').forEach(e=>e.style.visibility=''), true)`);
        shots.push({name, box, k});
      }
    }
    fs.writeFileSync(path.join(OUTDIR, 'cajas.json'), JSON.stringify(shots, null, 1));
    if (errors.length) console.log('errores de consola:', errors.slice(0, 5));
  } finally { await close(); srv.kill(); }
})().catch(e => { console.error(e); process.exit(1); });
