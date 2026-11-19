// @ts-check
/**
 * Consola de supervisión en vivo. Con la sala 100% remota es el único
 * mecanismo de control (PRD): quién está y en qué estado, el caso en curso, el
 * tiempo en ese estado, la calidad de red de cada puesto, la escucha de llamadas
 * y, en power/predictivo, los indicadores del discador con el control de N.
 */
import { $, $$, api, barraLateral, conectarSala, duracion, encabezado, esc, exigir, fechaHora, modal, salir, toast } from './comun.js';

/** @type {any} */
const S = { yo: null, estudios: [], estudio: null, foto: null, vista: 'sala', telefono: null, sala: null, vivo: false };

S.yo = await exigir(['supervisor', 'administrador']);
S.estudios = (await api('GET', '/api/estudios')).filter((/** @type {any} */ e) => e.estado === 'activo');
S.estudio = S.estudios[0]?.id ?? null;
pintar();
S.sala = conectarSala({ rol: 'supervisor', estudio: S.estudio }, (m) => {
  if (m.tipo === 'sala') { S.foto = m; S.fotoEn = Date.now(); if (S.vista === 'sala') pintarSala(); }
}, (vivo) => { S.vivo = vivo; const e = $('#vivo'); if (e) e.className = `en-vivo ${vivo ? '' : 'off'}`; });
setInterval(() => { if (S.vista === 'sala' && S.foto) pintarSala(); }, 1000);

function pintar() {
  const items = [
    { id: 'sala', icono: '🎧', texto: 'Sala en vivo', seccion: 'Operación' },
    { id: 'parciales', icono: '✂️', texto: 'Parciales' },
    { id: 'amd', icono: '🤖', texto: 'Revisión de AMD' },
    { id: 'fuera', icono: '🔗', texto: 'Fuera de marco' },
  ];
  $('#app').innerHTML = barraLateral(S.yo, items, S.vista) + '<main class="main" id="main"></main>';
  $$('[data-nav]').forEach((b) => { b.onclick = () => { S.vista = b.dataset.nav; pintar(); }; });
  $('#salir').onclick = salir;
  ({ sala: pintarSala, parciales: pintarParciales, amd: pintarAmd, fuera: pintarFuera })[S.vista]();
}

function selectorEstudio() {
  return `<select class="fselect" id="sel-estudio" style="width:auto">${S.estudios.map((/** @type {any} */ e) =>
    `<option value="${e.id}" ${e.id === S.estudio ? 'selected' : ''}>${esc(e.codigo)} · ${esc(e.nombre)}</option>`).join('')}</select>`;
}

function enlazarSelector(/** @type {() => void} */ despues) {
  const s = $('#sel-estudio');
  if (s) s.onchange = () => { S.estudio = Number(s.value); S.sala?.enviar({ tipo: 'estudio', estudio: S.estudio }); despues(); };
}

// ───────────────────────────── sala ─────────────────────────────

function pintarSala() {
  const m = $('#main');
  if (!$('#puestos')) {
    m.innerHTML = encabezado('Sala', 'en vivo', 'Estado, caso y calidad de red de cada puesto remoto.') +
      `<div id="discador"></div><div class="card"><div class="card-header"><span class="card-header-title">Puestos</span>
       <span id="vivo" class="en-vivo ${S.vivo ? '' : 'off'}">en vivo</span></div><div class="card-body"><div class="puestos" id="puestos"></div></div></div>`;
    $('#ph-acciones').innerHTML = selectorEstudio() + '<button class="btn btn-outline btn-sm" id="audio-sup">Conectar audio de escucha</button>';
    enlazarSelector(() => { $('#puestos').innerHTML = ''; });
    $('#audio-sup').onclick = conectarAudioSupervisor;
  }
  const f = S.foto;
  if (!f) return;
  const puestos = f.puestos.filter((/** @type {any} */ p) => !S.estudio || p.estudio_id === S.estudio);
  const ahora = Date.now();
  $('#puestos').innerHTML = puestos.length ? puestos.map((/** @type {any} */ p) => {
    const seg = p.segundos_en_estado + Math.floor((ahora - S.fotoEn) / 1000 || 0);
    const malP = p.perdida_pct != null && p.perdida_pct > 3, malJ = p.jitter_ms != null && p.jitter_ms > 30;
    return `<div class="puesto p-${esc(p.estado)} ${p.red_degradada ? 'red-mala' : ''}">
      <div class="row between"><span class="p-nombre">${esc(p.nombre)}</span><span class="est est-${esc(p.estado)}">${esc(p.estado.replace('_', ' '))}</span></div>
      <div class="p-meta">${duracion(seg)} en el estado${p.caso_id ? ` · caso ${esc(p.caso_identificador || p.caso_id)}` : ''}
        ${p.caso_origen && p.caso_origen !== 'marco' ? `<span class="badge badge-warn">${esc(p.caso_origen)}</span>` : ''}</div>
      <div class="p-red">${p.audio_conectado ? '' : '<span class="mal">sin audio</span>'}
        <span class="${malP ? 'mal' : ''}">pérdida ${p.perdida_pct ?? '—'}%</span>
        <span class="${malJ ? 'mal' : ''}">jitter ${p.jitter_ms ?? '—'} ms</span>
        ${p.relay ? '<span>TURN</span>' : ''}${p.codec ? `<span>${esc(p.codec)}</span>` : ''}</div>
      <div class="p-acciones">${p.estado === 'en_llamada'
        ? `<button class="btn btn-dark btn-sm" data-escuchar="${p.participante_id}">Escuchar</button>
           <button class="btn btn-outline btn-sm" data-susurrar="${p.participante_id}">Susurrar</button>
           <button class="btn btn-outline btn-sm" data-dejar="${p.participante_id}">Dejar</button>` : ''}</div></div>`;
  }).join('') : '<div class="empty-state"><span class="ei">🪑</span>No hay encuestadores conectados en este estudio.</div>';
  $$('[data-escuchar]').forEach((b) => { b.onclick = () => escuchar(Number(b.dataset.escuchar), false); });
  $$('[data-susurrar]').forEach((b) => { b.onclick = () => escuchar(Number(b.dataset.susurrar), true); });
  $$('[data-dejar]').forEach((b) => { b.onclick = () => api('POST', `/api/supervision/dejar-de-escuchar/${b.dataset.dejar}`, {}).catch((e) => toast(e.message, 'err')); });
  pintarDiscador(f.discador);
}

/** @param {any} d */
function pintarDiscador(d) {
  const caja = $('#discador');
  if (!d || !['power', 'predictivo'].includes(d.modo)) { caja.innerHTML = ''; return; }
  const real = d.abandono.abandono_real;
  const pct = (real.valor ?? 0) * 100, umbral = d.abandono_objetivo * 100;
  caja.innerHTML = `<div class="stat-grid">
      <div class="stat s-total"><div class="stat-num">${d.n_vigente.toFixed(2)}</div><div class="stat-label">N vigente (máx ${d.n_maximo})</div></div>
      <div class="stat ${d.sobre_umbral ? 's-bad' : 's-ok'}"><div class="stat-num">${esc(real.texto)}</div>
        <div class="stat-label">Abandono real · ${d.ventana_min} min</div>
        <div class="medidor ${d.sobre_umbral ? 'alto' : ''}"><div style="width:${Math.min(100, pct * 10)}%"></div><span class="umbral" style="left:${Math.min(100, umbral * 10)}%"></span></div></div>
      <div class="stat s-warn"><div class="stat-num">${d.abandono.abandono_reportada.texto}</div><div class="stat-label">Abandono reportado (sin FP de AMD)</div></div>
      <div class="stat s-free"><div class="stat-num">${d.descartadas_ventana}</div><div class="stat-label">Descartadas sin encuestador</div></div>
      <div class="stat"><div class="stat-num">${d.llamadas_en_curso}</div><div class="stat-label">Llamadas en curso</div></div>
    </div>
    <div class="card mb"><div class="card-body row between">
      <span class="small"><b>Modo ${esc(d.modo)}</b> · objetivo de abandono ${umbral.toFixed(1)}%${d.ultimo_ajuste ? ` · último ajuste ${fechaHora(d.ultimo_ajuste.ocurrido_en)}: ${esc(d.ultimo_ajuste.datos.motivo || '')}` : ''}</span>
      <div class="row"><input type="number" id="n-nuevo" step="0.1" min="1" max="${d.n_maximo}" value="${d.n_vigente.toFixed(1)}" style="width:90px" />
        <label class="check-inline"><input type="checkbox" id="n-auto" /> ajuste automático</label>
        <button class="btn btn-orange btn-sm" id="n-fijar">Fijar N</button></div></div></div>`;
  $('#n-fijar').onclick = async () => {
    try { await api('POST', `/api/supervision/estudios/${S.estudio}/n`, { n: Number($('#n-nuevo').value), ajuste_automatico: $('#n-auto').checked }); toast('N actualizado.', 'ok'); }
    catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
  };
}

async function conectarAudioSupervisor() {
  try {
    if (!S.estudio) return toast('Elegí un estudio.', 'err');
    const ses = await api('POST', '/api/estacion/sesion', { estudio_id: S.estudio });
    const { Telefono } = await import('./telefono.js');
    S.telefono = new Telefono(ses.sip, ses.ice, /** @type {HTMLAudioElement} */ ($('#audio-remoto')), {
      alConectar: () => toast('Audio de escucha conectado.', 'ok'), alCaer: () => toast('Se cortó el audio de escucha.', 'err') });
    await S.telefono.conectar();
    $('#audio-sup').textContent = 'Audio de escucha listo';
    $('#audio-sup').disabled = true;
  } catch (e) { toast(/** @type {any} */ (e).message || 'No se pudo conectar el audio.', 'err'); }
}

/** @param {number} pid @param {boolean} susurro */
async function escuchar(pid, susurro) {
  try { await api('POST', `/api/supervision/escuchar/${pid}`, { susurro }); toast(susurro ? 'Susurrando al encuestador.' : 'Escuchando la llamada.', 'ok'); }
  catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

// ───────────────────────────── parciales ─────────────────────────────

async function pintarParciales() {
  $('#main').innerHTML = encabezado('Entrevistas', 'parciales', 'Una interrumpida solo pasa a completa si un supervisor la promueve, con motivo (decisión 6 del diccionario).') +
    '<div class="card"><div class="card-body tight" id="tabla"></div></div>';
  $('#ph-acciones').innerHTML = selectorEstudio();
  enlazarSelector(pintarParciales);
  const filas = await api('GET', `/api/supervision/estudios/${S.estudio}/parciales`);
  $('#tabla').innerHTML = filas.length ? `<div class="table-wrap"><table><thead><tr><th>Caso</th><th>Cuándo</th><th>Encuestador</th><th>Motivo</th><th>Respuestas</th><th></th></tr></thead><tbody>
    ${filas.map((/** @type {any} */ f) => `<tr><td class="td-strong">${esc(f.identificador || f.caso_id)}</td><td>${fechaHora(f.ocurrido_en)}</td>
      <td>${esc(f.encuestador || '—')}</td><td class="td-muted">${esc(f.motivo || '')}</td><td>${f.respuestas}</td>
      <td><button class="btn btn-orange btn-sm" data-promover="${f.caso_id}">Promover a completa</button></td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty-state"><span class="ei">✅</span>No hay entrevistas interrumpidas.</div>';
  $$('[data-promover]').forEach((b) => {
    b.onclick = async () => {
      const motivo = await modal({ titulo: 'Promover a completa', cuerpo: '<div class="form-group"><label>Motivo (queda registrado con tu nombre y la hora)</label><textarea class="finput" id="mot"></textarea></div>',
        pie: '<button class="btn btn-orange" data-ok>Promover</button>' }, (caja, cerrar) => { $('[data-ok]', caja).onclick = () => cerrar($('#mot', caja).value.trim()); });
      if (!motivo) return;
      try { await api('POST', `/api/supervision/casos/${b.dataset.promover}/promover`, { motivo }); toast('Promovida.', 'ok'); pintarParciales(); }
      catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
    };
  });
}

// ───────────────────────────── AMD ─────────────────────────────

async function pintarAmd() {
  $('#main').innerHTML = encabezado('Revisión', 'de AMD', 'Escuchá llamadas que el detector clasificó como contestador. Si era una persona, es un abandono disfrazado: marcalo como falso positivo (32).') +
    '<div class="stat-grid" id="cal"></div><div class="card"><div class="card-body tight" id="tabla"></div></div>';
  $('#ph-acciones').innerHTML = selectorEstudio();
  enlazarSelector(pintarAmd);
  const [cal, filas] = await Promise.all([api('GET', `/api/estudios/${S.estudio}/reportes/amd`), api('GET', `/api/supervision/estudios/${S.estudio}/amd`)]);
  $('#cal').innerHTML = `<div class="stat s-total"><div class="stat-num">${cal.detectadas}</div><div class="stat-label">Detectadas como máquina</div></div>
    <div class="stat s-free"><div class="stat-num">${cal.con_grabacion}</div><div class="stat-label">Con grabación</div></div>
    <div class="stat s-bad"><div class="stat-num">${cal.falsos_positivos}</div><div class="stat-label">Falsos positivos</div></div>
    <div class="stat s-warn"><div class="stat-num">${esc(cal.tasa_falsos_positivos.texto)}</div><div class="stat-label">Tasa de error del AMD</div></div>`;
  $('#tabla').innerHTML = filas.length ? `<div class="table-wrap"><table><thead><tr><th>Cuándo</th><th>Número</th><th>Grabación</th><th></th></tr></thead><tbody>
    ${filas.map((/** @type {any} */ f) => `<tr><td>${fechaHora(f.originada_en)}</td><td class="td-strong">${esc(f.numero)}</td>
      <td class="td-muted">${esc(f.grabacion)}</td><td>${f.reclasificada ? '<span class="badge badge-err">Falso positivo</span>'
        : `<button class="btn btn-outline btn-sm btn-del" data-fp="${f.llamada_id}">Era una persona</button>`}</td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty-state"><span class="ei">🎙️</span>No hay llamadas de contestador grabadas para revisar.</div>';
  $$('[data-fp]').forEach((b) => { b.onclick = async () => { await api('POST', `/api/supervision/llamadas/${b.dataset.fp}/falso-positivo`, {}); pintarAmd(); }; });
}

// ───────────────────────────── fuera de marco ─────────────────────────────

async function pintarFuera() {
  $('#main').innerHTML = encabezado('Casos', 'fuera de marco', 'Quién creó casos derivados y cuántos terminaron en completa. Se marca al que se desvía del resto de la sala.') +
    '<div class="card"><div class="card-body tight" id="tabla"></div></div>';
  $('#ph-acciones').innerHTML = selectorEstudio();
  enlazarSelector(pintarFuera);
  const filas = await api('GET', `/api/supervision/estudios/${S.estudio}/fuera-de-marco`);
  $('#tabla').innerHTML = filas.length ? `<div class="table-wrap"><table><thead><tr><th>Encuestador</th><th>Creados</th><th>Completas</th><th></th></tr></thead><tbody>
    ${filas.map((/** @type {any} */ f) => `<tr><td class="td-strong">${esc(f.nombre)}</td><td>${f.creados}</td><td>${f.completas}</td>
      <td>${f.desviado ? '<span class="badge badge-err">Se desvía de la sala</span>' : ''}</td></tr>`).join('')}</tbody></table></div>`
    : '<div class="empty-state"><span class="ei">🔗</span>Este estudio no tiene casos fuera de marco.</div>';
}

S.fotoEn = Date.now();
