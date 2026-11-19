// @ts-check
/**
 * Puesto del encuestador remoto. Todo lo demás existe para que acá haya una
 * llamada válida enfrente (PRD, "Usuarios y escenarios").
 *
 * Flujo: elegir estudio → prueba de audio → pata permanente de audio → turno.
 * El modo de discado del estudio decide quién inicia la llamada:
 *   manual   el encuestador marca con el teclado (panel visible)
 *   preview  pide el caso y el sistema disca
 *   power / predictivo   el caso llega por la sala cuando alguien atiende
 */
import { $, $$, api, conectarSala, duracion, esc, exigir, fechaHora, modal, salir, toast } from './comun.js';
import { Motor, COMPLETA, FUERA_DE_CUOTA } from './motor.js';
import { vigilar } from './calidad.js';

/** @type {any} */
const S = {
  yo: null, sesion: null, modo: 'preview', catalogo: null, motor: null, cuestionario: null,
  caso: null, llamada: null, llamadaDesde: 0, conectada: false, estado: 'desconectado',
  respuestas: {}, historial: [], pantalla: null, pantallaDesde: 0, telefono: null, sala: null,
  mudo: false, ultimoCaso: null, fin: null,
};

const main = () => $('#main');

// ───────────────────────────── arranque ─────────────────────────────

S.yo = await exigir(['encuestador', 'supervisor']);
$('#tb-nombre').textContent = S.yo.nombre;
$('#tb-avatar').textContent = (S.yo.nombre || '?')[0];
$('#tb-salir').onclick = async () => { await terminarTurno(); salir(); };
pintarEstado();
await elegirEstudio();

async function elegirEstudio() {
  const estudios = await api('GET', '/api/estacion/estudios');
  main().innerHTML = `<div class="ficha-wrap" style="max-width:720px">
    <div class="clock-hero"><div class="accent-bar"></div>
      <div class="greet">Hola, ${esc(S.yo.nombre.split(' ')[0])}</div>
      <div class="today-date">¿En qué estudio trabajás hoy?</div>
      <p class="ficha-note">Antes de empezar se hace una prueba de audio de unos segundos.</p>
      <div class="mt" id="lista-estudios"></div>
    </div></div>`;
  const lista = $('#lista-estudios');
  if (!estudios.length) {
    lista.innerHTML = '<div class="empty-state"><span class="ei">📭</span>No tenés estudios activos asignados. Avisale a la coordinación.</div>';
    return;
  }
  lista.innerHTML = estudios.map((/** @type {any} */ e) => `<button class="opcion-grande" data-id="${e.id}">
      <b>${esc(e.nombre)} <span class="badge badge-modo" style="margin-left:.4rem">${esc(e.modo_discado)}</span></b>
      <span>${esc(e.codigo)}${e.cliente ? ' · ' + esc(e.cliente) : ''}</span></button>`).join('');
  $$('[data-id]', lista).forEach((b) => { b.onclick = () => pruebaAudio(Number(b.dataset.id)); });
}

/** @param {number} estudioId */
async function pruebaAudio(estudioId) {
  const { probarMicrofono, tonoDePrueba } = await import('./telefono.js');
  main().innerHTML = `<div class="ficha-wrap"><div class="clock-hero"><div class="accent-bar"></div>
    <div class="greet">Prueba de audio</div><div class="today-date">Ponete el auricular</div>
    <p class="ficha-note">1. Tocá «Escuchar tono» y confirmá que lo oís. 2. Tocá «Probar micrófono» y decí tu nombre.</p>
    <div class="big-actions"><button class="big-btn big-btn-out" id="tono"><span class="bi">🎧</span>Escuchar tono</button>
    <button class="big-btn big-btn-in" id="mic"><span class="bi">🎙️</span>Probar micrófono</button></div>
    <div id="res" class="mt"></div>
    <button class="btn btn-dark btn-full mt" id="seguir" disabled>Empezar el turno</button></div></div>`;
  let oye = false, habla = false, resultado = /** @type {any} */ ({});
  const listo = () => { $('#seguir').disabled = !(oye && habla); };
  $('#tono').onclick = async () => {
    await tonoDePrueba();
    const r = await modal({ titulo: '¿Escuchaste el tono?', cuerpo: '<p>Si no lo oíste, revisá que el auricular sea la salida de audio del sistema.</p>',
      pie: '<button class="btn btn-outline" data-r="no">No</button><button class="btn btn-orange" data-r="si">Sí, lo escuché</button>' },
    (caja, cerrar) => $$('[data-r]', caja).forEach((b) => { b.onclick = () => cerrar(b.dataset.r === 'si'); }));
    oye = !!r;
    listo();
  };
  $('#mic').onclick = async () => {
    $('#res').innerHTML = '<div class="alert alert-info">Hablá ahora…</div>';
    resultado = await probarMicrofono(3);
    habla = resultado.microfono && resultado.nivel_max > 0.05;
    $('#res').innerHTML = habla
      ? `<div class="alert alert-success">Micrófono OK (${esc(resultado.dispositivo)}).</div>`
      : `<div class="alert alert-error">${resultado.microfono ? 'No se oyó tu voz: subí el volumen del micrófono.' : 'El navegador no tiene permiso para usar el micrófono.'}</div>`;
    listo();
  };
  $('#seguir').onclick = async () => {
    $('#seguir').disabled = true;
    try {
      await abrirTurno(estudioId, { ...resultado, tono: oye });
    } catch (e) {
      toast(String(/** @type {any} */ (e).message || e), 'err');
      $('#seguir').disabled = false;
    }
  };
}

/** @param {number} estudioId @param {any} prueba */
async function abrirTurno(estudioId, prueba) {
  S.sesion = await api('POST', '/api/estacion/sesion', { estudio_id: estudioId });
  await api('POST', '/api/estacion/prueba-audio', prueba);
  S.modo = S.sesion.estudio.modo_discado;
  S.catalogo = S.sesion.catalogo;
  S.cuestionario = S.sesion.cuestionario;
  $('#tb-estudio').innerHTML = `${esc(S.sesion.estudio.nombre)} <span class="badge badge-modo">${esc(S.modo)}</span>`;
  S.sala = conectarSala({ rol: 'encuestador' }, alMensajeSala);
  await conectarAudio();
  S.estado = 'pausa';
  pintarEstado();
  pintarPuesto();
  const actual = await api('GET', '/api/estacion/caso');
  if (actual.caso) await mostrarCaso(actual.caso);
}

async function conectarAudio() {
  const { Telefono } = await import('./telefono.js');
  S.telefono = new Telefono(S.sesion.sip, S.sesion.ice, /** @type {HTMLAudioElement} */ ($('#audio-remoto')), {
    alConectar: () => pintarAudio(true),
    alCaer: () => { pintarAudio(false); toast('Se cortó el audio del puesto. Reconectando…', 'err'); setTimeout(reconectarAudio, 2000); },
  });
  try {
    await S.telefono.conectar();
  } catch (e) {
    pintarAudio(false);
    toast('No se pudo conectar el audio del puesto: revisá la conexión.', 'err');
  }
  vigilar(() => S.telefono?.conexion, (m) => api('POST', '/api/estacion/calidad', { muestras: m.map((/** @type {any} */ x) => ({ ...x, llamada_id: S.llamada })) }),
    (m) => {
      const mal = m.perdida_pct > 3 || m.jitter_ms > 30;
      $('#tb-red').innerHTML = `<span style="color:${mal ? '#ff8a73' : 'rgba(255,255,255,0.6)'}">red ${m.perdida_pct}% · ${m.jitter_ms} ms${m.relay ? ' · TURN' : ''}</span>`;
    });
}

async function reconectarAudio() {
  try { await S.telefono?.conectar(); } catch { setTimeout(reconectarAudio, 5000); }
}

/** @param {boolean} ok */
function pintarAudio(ok) {
  $('#tb-audio').className = `badge ${ok ? 'badge-on' : 'badge-err'}`;
  $('#tb-audio').textContent = ok ? 'Audio OK' : 'Sin audio';
}

function pintarEstado() {
  const textos = /** @type {Record<string,string>} */ ({ desconectado: 'Sin turno', pausa: 'En pausa', disponible: 'Disponible',
    preview: 'Con caso', en_llamada: 'En llamada', clasificando: 'Clasificando' });
  $('#tb-estado').innerHTML = `<span class="est est-${S.estado}">${textos[S.estado] || S.estado}</span>`;
}

async function terminarTurno() {
  if (!S.sesion) return;
  try { await api('DELETE', '/api/estacion/sesion'); } catch { /* ya cerrada */ }
  S.sala?.cerrar();
  await S.telefono?.desconectar();
}

// ───────────────────────────── sala ─────────────────────────────

/** @param {any} m */
async function alMensajeSala(m) {
  switch (m.tipo) {
    case 'llamada_conectada':
      // Power/predictivo: el caso llega con la ficha; la pantalla tiene que estar lista en < 1 s.
      S.conectada = true;
      S.llamada = m.llamada_id;
      S.llamadaDesde = Date.now();
      S.estado = 'en_llamada';
      if (m.caso && (!S.caso || S.caso.id !== m.caso.id)) await mostrarCaso(m.caso, true);
      else { pintarLlamada(); iniciarEntrevista(); }
      pintarEstado();
      break;
    case 'llamada_timbrando':
      if (m.llamada_id === S.llamada) $('#cb-estado') && ($('#cb-estado').textContent = 'Timbrando…');
      break;
    case 'llamada_finalizada':
      if (m.llamada_id === S.llamada) {
        S.estado = 'clasificando';
        pintarEstado();
        const conectada = S.conectada;
        S.conectada = false;
        pintarLlamada(true);
        if (!conectada) setTimeout(() => esperarDisposicionAutomatica(m.llamada_id), 600);
        else if (S.fin == null) toast('La llamada terminó. Clasificá el intento.', '');
      }
      break;
    case 'audio_desconectado':
      pintarAudio(false);
      break;
    case 'audio_conectado':
      pintarAudio(true);
      break;
    case 'escucha_iniciada':
      break; // el encuestador no se entera de la escucha
    default:
      break;
  }
}

/** Una llamada que no conectó la clasifica el sistema (no contesta, ocupado...). @param {string} llamada */
async function esperarDisposicionAutomatica(llamada) {
  if (!S.caso) return;
  const ficha = await api('GET', `/api/estacion/caso`);
  const ultimo = ficha.caso ? ficha.caso.historial?.[0] : null;
  if (!ficha.caso) {
    toast(`Intento registrado automáticamente.`, 'ok');
    S.llamada = null;
    await siguienteOEsperar();
  } else if (ultimo?.disp_tel && S.llamada === llamada) {
    toast(`Intento registrado: ${ultimo.disp_tel_etiqueta}.`, 'ok');
  }
}

// ───────────────────────────── puesto ─────────────────────────────

function pintarPuesto() {
  main().innerHTML = `<div class="estacion">
    <div class="stack">
      <div class="callbar" id="callbar"></div>
      ${S.modo === 'manual' ? `<div class="card"><div class="card-header"><span class="card-header-title">Teclado</span>
        <button class="btn btn-outline btn-sm" id="kp-admin">Llamada administrativa</button></div>
        <div class="card-body"><input type="text" id="kp-num" class="dial-input" inputmode="tel" placeholder="Número" />
        <div class="keypad">${'123456789*0#'.split('').map((d) => `<button data-d="${d}">${d}</button>`).join('')}</div>
        <button class="btn btn-green btn-full mt" id="kp-llamar">Llamar</button></div></div>` : ''}
      <div class="card ficha"><div class="card-header"><span class="card-header-title">Ficha del contacto</span><span id="ficha-origen"></span></div>
        <div class="card-body" id="ficha"><div class="muted small">Sin caso en pantalla.</div></div></div>
      <div class="card"><div class="card-header"><span class="card-header-title">Historial del caso</span></div>
        <div class="card-body hist" id="historial"><div class="muted small">—</div></div></div>
    </div>
    <div class="card" id="entrevista"></div>
  </div>`;
  if (S.modo === 'manual') {
    $$('.keypad [data-d]').forEach((b) => { b.onclick = () => { $('#kp-num').value += b.dataset.d; if (S.conectada) dtmf(b.dataset.d); }; });
    $('#kp-llamar').onclick = () => marcarManual($('#kp-num').value);
    $('#kp-admin').onclick = () => marcarManual($('#kp-num').value, true);
  }
  pintarLlamada();
  pintarEspera();
}

function pintarEspera() {
  if (S.caso) return;
  const auto = S.modo === 'power' || S.modo === 'predictivo';
  $('#entrevista').innerHTML = `<div class="vacio-grande"><div class="accent-bar"></div>
    <h3>${S.estado === 'disponible' ? (auto ? 'Esperando que alguien atienda' : 'Listo para el próximo caso') : 'Estás en pausa'}</h3>
    <p>${auto ? 'En este estudio el sistema disca por vos: cuando un respondente atienda, su ficha y el cuestionario aparecen acá solos.'
      : S.modo === 'manual' ? 'Pedí un caso o marcá un número con el teclado.' : 'Pedí el próximo caso: vas a ver la ficha antes de que el sistema llame.'}</p>
    <div class="row" style="justify-content:center">
      ${S.estado !== 'disponible' ? '<button class="btn btn-green" id="disp">Quedar disponible</button>' : '<button class="btn btn-outline" id="pausa">Pausa</button>'}
      ${!auto && S.estado === 'disponible' ? '<button class="btn btn-orange" id="pedir">Pedir caso</button>' : ''}
    </div></div>`;
  $('#disp') && ($('#disp').onclick = () => cambiarEstado('disponible'));
  $('#pausa') && ($('#pausa').onclick = () => cambiarEstado('pausa'));
  $('#pedir') && ($('#pedir').onclick = pedirCaso);
}

/** @param {string} estado */
async function cambiarEstado(estado) {
  try {
    const r = await api('POST', '/api/estacion/estado', { estado });
    S.estado = r.estado;
    pintarEstado();
    pintarEspera();
    if (estado === 'disponible' && S.modo === 'preview') await pedirCaso();
  } catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

async function pedirCaso() {
  const r = await api('POST', '/api/estacion/siguiente');
  if (!r.caso) { toast('No hay casos disponibles en este momento.', ''); return; }
  await mostrarCaso(r.caso);
}

async function siguienteOEsperar() {
  S.caso = null; S.llamada = null; S.conectada = false; S.respuestas = {}; S.historial = []; S.fin = null;
  pintarFicha();
  pintarLlamada();
  if (S.estado === 'disponible' && S.modo === 'preview') return pedirCaso().catch(() => pintarEspera());
  pintarEspera();
}

/** @param {any} caso @param {boolean} [conectada] */
async function mostrarCaso(caso, conectada = false) {
  S.caso = caso;
  S.ultimoCaso = caso.id;
  S.fin = null;
  S.respuestas = await api('GET', `/api/estacion/casos/${caso.id}/respuestas`);
  S.motor = new Motor(S.cuestionario.definicion, caso.propiedades || {}, caso.id);
  S.historial = [];
  pintarFicha();
  pintarLlamada();
  if (conectada) iniciarEntrevista();
  else pintarPrevia();
}

function pintarFicha() {
  const c = S.caso;
  if (!c) { $('#ficha').innerHTML = '<div class="muted small">Sin caso en pantalla.</div>'; $('#historial').innerHTML = '—'; $('#ficha-origen').innerHTML = ''; return; }
  const props = Object.entries(c.propiedades || {});
  $('#ficha-origen').innerHTML = c.origen !== 'marco' ? `<span class="badge badge-warn">${esc(c.origen)}</span>` : '';
  $('#ficha').innerHTML = `<dl>${c.identificador ? `<dt>Id</dt><dd>${esc(c.identificador)}</dd>` : ''}
      ${props.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}
      ${c.estrato ? `<dt>Estrato</dt><dd>${esc(c.estrato)}</dd>` : ''}
      <dt>Intentos</dt><dd>${c.intentos}</dd>
      ${c.agenda_desde ? `<dt>Agenda</dt><dd>${fechaHora(c.agenda_desde)} – ${fechaHora(c.agenda_hasta)}</dd>` : ''}</dl>
    <div class="tel-list mt">${c.telefonos.map((/** @type {any} */ t) => `<div class="tel"><span class="num">${esc(t.numero)}</span>
      ${t.bloqueado ? '<span class="badge badge-err">No llamar</span>'
        : S.modo === 'power' || S.modo === 'predictivo' ? '' : `<button class="btn btn-green btn-sm" data-tel="${t.id}" ${S.llamada ? 'disabled' : ''}>Llamar</button>`}</div>`).join('')}</div>`;
  $$('[data-tel]').forEach((b) => { b.onclick = () => llamar(Number(b.dataset.tel)); });
  $('#historial').innerHTML = c.historial?.length
    ? `<ul>${c.historial.map((/** @type {any} */ h) => `<li><b>${fechaHora(h.originada_en)}</b> · ${esc(h.disp_tel_etiqueta || 'en curso')}
        ${h.encuestador ? ` · ${esc(h.encuestador)}` : ''}${h.nota ? `<br><span class="muted">${esc(h.nota)}</span>` : ''}</li>`).join('')}</ul>`
    : '<div class="muted small">Primer intento.</div>';
}

function pintarPrevia() {
  $('#entrevista').innerHTML = `<div class="card-header"><span class="card-header-title">Caso ${esc(S.caso.identificador || S.caso.id)}</span>
      <button class="btn btn-outline btn-sm" id="saltar">Devolver a la cola</button></div>
    <div class="card-body"><div class="speech">${esc(S.motor.texto(S.cuestionario.definicion.presentacion?.texto, S.respuestas)) || 'Revisá la ficha y llamá.'}</div>
    ${S.cuestionario.definicion.presentacion?.ayuda ? `<div class="ayuda">${esc(S.cuestionario.definicion.presentacion.ayuda)}</div>` : ''}
    <p class="muted small mt">Cuando atiendan, tocá «Empezar encuesta». Si no corresponde encuestar, clasificá el intento.</p>
    <div class="row mt"><button class="btn btn-orange" id="empezar">Empezar encuesta</button><button class="btn btn-outline" id="clasificar">Clasificar</button></div></div>`;
  $('#saltar').onclick = async () => {
    const motivo = prompt('¿Por qué lo devolvés a la cola?') || '';
    try { await api('POST', `/api/estacion/casos/${S.caso.id}/saltar`, { motivo }); await siguienteOEsperar(); }
    catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
  };
  $('#empezar').onclick = iniciarEntrevista;
  $('#clasificar').onclick = () => clasificar();
}

// ───────────────────────────── llamada ─────────────────────────────

/** @param {boolean} [terminada] */
function pintarLlamada(terminada = false) {
  const cb = $('#callbar');
  if (!cb) return;
  const en = !!S.llamada && !terminada;
  cb.innerHTML = `<div class="cb-estado" id="cb-estado">${en ? (S.conectada ? 'Conectada' : 'Llamando…') : terminada ? 'Llamada terminada' : 'Sin llamada'}</div>
    <div class="cb-numero">${esc(S.numero || (S.caso ? (S.caso.telefonos?.[0]?.numero ?? '') : '—'))}</div>
    <div class="cb-timer" id="cb-timer">${en ? duracion((Date.now() - S.llamadaDesde) / 1000) : '00:00'}</div>
    <div class="cb-acciones">
      <button class="btn btn-danger btn-sm" id="cb-colgar" ${en ? '' : 'disabled'}>Colgar</button>
      <button class="btn btn-outline btn-sm" id="cb-mudo" ${en ? '' : 'disabled'}>${S.mudo ? 'Activar micrófono' : 'Mudo'}</button>
      <button class="btn btn-outline btn-sm" id="cb-pausa">${S.estado === 'pausa' ? 'Disponible' : 'Pausa al terminar'}</button>
    </div>`;
  $('#cb-colgar').onclick = () => api('POST', '/api/estacion/llamada/colgar', {}).catch((e) => toast(e.message, 'err'));
  $('#cb-mudo').onclick = async () => {
    S.mudo = !S.mudo;
    await api('POST', '/api/estacion/llamada/mudo', { activar: S.mudo }).catch((e) => toast(e.message, 'err'));
    pintarLlamada();
  };
  $('#cb-pausa').onclick = () => {
    if (S.estado === 'pausa') return cambiarEstado('disponible');
    S.pausaAlTerminar = !S.pausaAlTerminar;
    toast(S.pausaAlTerminar ? 'Quedás en pausa al clasificar este caso.' : 'Seguís disponible al terminar.');
  };
  clearInterval(S.reloj);
  if (en) S.reloj = setInterval(() => { const t = $('#cb-timer'); if (t) t.textContent = duracion((Date.now() - S.llamadaDesde) / 1000); }, 500);
}

/** @param {number} telefonoId */
async function llamar(telefonoId) {
  try {
    const r = await api('POST', '/api/estacion/llamar', { caso_id: S.caso.id, telefono_id: telefonoId });
    S.llamada = r.llamada_id; S.numero = r.numero; S.llamadaDesde = Date.now(); S.conectada = false;
    S.estado = 'preview';
    pintarEstado(); pintarLlamada(); pintarFicha();
  } catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

/** @param {string} d */
function dtmf(d) { api('POST', '/api/estacion/llamada/dtmf', { digitos: d }).catch(() => { /* sin llamada */ }); }

/**
 * Discado manual. ADR-008: antes de discar, un número desconocido se clasifica
 * con el diálogo de tres opciones, que no se puede saltear.
 * @param {string} numero @param {boolean} [administrativa]
 */
async function marcarManual(numero, administrativa = false) {
  numero = (numero || '').trim();
  if (numero.replace(/\D/g, '').length < 6) return toast('Marcá un número completo.', 'err');
  try {
    if (administrativa) return await llamarAdministrativa(numero);
    const c = await api('POST', '/api/estacion/numero/clasificar', { numero, caso_actual: S.caso?.id ?? null });
    if (c.situacion === 'lista_negra') return toast('Ese número pidió no ser llamado nunca más.', 'err');
    if (c.situacion === 'en_caso_actual') {
      const t = S.caso.telefonos.find((/** @type {any} */ x) => x.numero.replace(/\D/g, '').endsWith(numero.replace(/\D/g, '').replace(/^0+/, '')));
      return llamar(t ? t.id : S.caso.telefonos[0].id);
    }
    if (c.situacion === 'en_otro_caso') {
      return modal({ titulo: 'Ese número ya está en la muestra', cuerpo: `<p>Pertenece al caso <b>${esc(c.caso_id)}</b> de este estudio. No se crea un caso duplicado: si corresponde, pedí ese caso a la coordinación.</p>`,
        pie: '<button class="btn btn-dark" data-ok>Entendido</button>' }, (caja, cerrar) => { $('[data-ok]', caja).onclick = () => cerrar(null); });
    }
    const opcion = await dialogoTresOpciones(numero, c);
    if (!opcion) return;
    if (opcion.opcion === 'administrativa') return await llamarAdministrativa(numero);
    const r = await api('POST', '/api/estacion/numero/resolver', { ...opcion, numero });
    if (opcion.opcion !== 'mismo_caso') {
      const ficha = await api('GET', '/api/estacion/caso');
      await mostrarCaso(ficha.caso);
    } else {
      S.caso.telefonos.push({ id: r.telefono_id, numero, bloqueado: false });
    }
    await llamar(r.telefono_id);
  } catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

/** @param {string} numero */
async function llamarAdministrativa(numero) {
  const r = await api('POST', '/api/estacion/llamar', { administrativa: true, numero });
  S.llamada = r.llamada_id; S.numero = numero; S.llamadaDesde = Date.now(); S.administrativa = true;
  pintarLlamada();
  toast('Llamada administrativa: no cuenta como intento ni lleva disposición.');
}

/** @param {string} numero @param {any} c */
function dialogoTresOpciones(numero, c) {
  const origen = S.caso?.id ?? S.ultimoCaso;
  return modal({
    titulo: `¿Quién es ${numero}?`, sinCerrar: false, ancho: 560,
    cuerpo: `<p class="muted small mb">El número no está en la muestra. Elegí antes de llamar: es el único momento en que sabés la respuesta.</p>
      <button class="opcion-grande" data-o="mismo_caso" ${c.puede_mismo_caso ? '' : 'disabled'}><b>Otro teléfono del mismo caso</b>
        <span>${c.puede_mismo_caso ? 'Ej.: «el titular no está, llamalo al celular». Se agrega al caso actual.' : 'Necesitás un caso abierto en pantalla.'}</span></button>
      <button class="opcion-grande" data-o="derivado" ${c.puede_derivado && origen ? '' : 'disabled'}><b>Persona nueva derivada</b>
        <span>${c.puede_derivado ? (origen ? 'Ej.: «hablá con mi colega». Crea un caso fuera de marco, atribuido a vos.' : 'Necesitás haber trabajado un caso que la derive.') : esc(c.motivo_derivado)}</span></button>
      <button class="opcion-grande" data-o="administrativa"><b>Llamada administrativa</b>
        <span>Coordinación, verificación o supervisión. No es un intento de encuesta.</span></button>
      <div id="extra-derivado" class="hidden mt"><div class="form-group"><label>Motivo de la derivación</label>
        <input type="text" id="motivo-der" placeholder="Ej.: es la gerenta de compras" /></div>
        <button class="btn btn-orange btn-full" id="confirmar-der">Crear caso y llamar</button></div>`,
  }, (caja, cerrar) => {
    $$('[data-o]', caja).forEach((b) => {
      b.onclick = () => {
        if (b.dataset.o === 'derivado') { $('#extra-derivado', caja).classList.remove('hidden'); $('#motivo-der', caja).focus(); return; }
        cerrar({ opcion: b.dataset.o });
      };
    });
    $('#confirmar-der', caja).onclick = () => {
      const motivo = $('#motivo-der', caja).value.trim();
      if (!motivo) return toast('Contá brevemente por qué se deriva.', 'err');
      cerrar({ opcion: 'derivado', caso_origen: origen, motivo });
    };
  });
}

// ───────────────────────────── entrevista ─────────────────────────────

async function iniciarEntrevista() {
  if (!S.caso) return;
  api('POST', `/api/estacion/casos/${S.caso.id}/iniciar`, { llamada_id: S.llamada }).catch(() => { /* registro no bloqueante */ });
  const primera = S.motor.primera(S.respuestas);
  S.historial = [];
  if (primera.fin != null) return terminarEntrevista(primera.fin);
  mostrarPantalla(primera.pantalla);
}

/** @param {string} pid */
function mostrarPantalla(pid) {
  S.pantalla = pid;
  S.pantallaDesde = Date.now();
  const m = S.motor;
  const p = m.pantallas[pid];
  const preguntas = m.preguntas(pid, S.respuestas);
  const idx = m.secuencia.indexOf(pid);
  $('#entrevista').innerHTML = `<div class="card-header"><span class="card-header-title">${esc(S.cuestionario.definicion.nombre)}</span>
      <span class="small muted">${idx + 1} / ${m.secuencia.length}</span></div>
    <div class="progreso"><div style="width:${Math.round(((idx + 1) / m.secuencia.length) * 100)}%"></div></div>
    <div class="card-body">
      ${p.texto ? `<div class="speech">${esc(m.texto(p.texto, S.respuestas))}</div>` : ''}
      ${p.ayuda ? `<div class="ayuda">${esc(p.ayuda)}</div>` : ''}
      <div id="preguntas">${preguntas.map((/** @type {any} */ q) => pregunta(q)).join('')}</div>
      <div id="err-pantalla"></div>
      <div class="row between mt">
        <button class="btn btn-outline" id="atras" ${S.historial.length ? '' : 'disabled'}>Atrás</button>
        <div class="row"><button class="btn btn-outline" id="clasificar">Terminar y clasificar</button>
        <button class="btn btn-orange" id="siguiente">Siguiente</button></div>
      </div></div>`;
  $('#atras').onclick = () => { const prev = S.historial.pop(); if (prev) mostrarPantalla(prev); };
  $('#siguiente').onclick = avanzar;
  $('#clasificar').onclick = () => clasificar();
  $$('input[data-otro]').forEach((i) => { i.oninput = () => { S.respuestas[i.dataset.otro] = i.value; }; });
  const primero = $('#preguntas input, #preguntas select, #preguntas textarea');
  primero?.focus();
}

/** @param {any} q */
function pregunta(q) {
  const m = S.motor;
  const r = S.respuestas;
  const v = r[q.id];
  const cab = `<div class="q-id">${esc(q.id)}</div><div class="q-texto">${esc(m.texto(q.texto, r))}</div>${q.ayuda ? `<div class="ayuda">${esc(q.ayuda)}</div>` : ''}`;
  let cuerpo = '';
  if (q.tipo === 'simple' || q.tipo === 'multiple') {
    const tipo = q.tipo === 'simple' ? 'radio' : 'checkbox';
    const sel = Array.isArray(v) ? v.map(String) : v != null ? [String(v)] : [];
    cuerpo = `<div class="opciones">${m.opciones(q, r).map((/** @type {any} */ o) => `<label class="opcion">
        <input type="${tipo}" name="${esc(q.id)}" value="${esc(o.codigo)}" ${sel.includes(o.codigo) ? 'checked' : ''} />
        ${esc(o.texto)}<span class="cod">${esc(o.codigo)}</span></label>
        ${o.otro ? `<input type="text" class="mt" data-otro="${esc(q.id)}.otro" placeholder="Especificar" value="${esc(r[q.id + '.otro'] ?? '')}" />` : ''}`).join('')}</div>`;
  } else if (q.tipo === 'grilla') {
    const ops = m.opciones(q, r);
    cuerpo = `<div class="grilla table-wrap"><table><thead><tr><th></th>${ops.map((/** @type {any} */ o) => `<th>${esc(o.texto)}</th>`).join('')}</tr></thead><tbody>
      ${m.filas(q, r).map((/** @type {any} */ f) => `<tr><td>${esc(f.texto)}</td>${ops.map((/** @type {any} */ o) => `<td><input type="radio" name="${esc(q.id)}.${esc(f.codigo)}" value="${esc(o.codigo)}"
        ${String(r[`${q.id}.${f.codigo}`] ?? '') === o.codigo ? 'checked' : ''} style="accent-color:var(--orange)" /></td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  } else if (q.tipo === 'formula') {
    return '';
  } else {
    const tipo = q.tipo === 'fecha' ? 'date' : q.tipo === 'hora' ? 'time' : q.tipo === 'numero' || q.tipo === 'codigo' ? 'text' : 'text';
    const modo = q.tipo === 'numero' || q.tipo === 'codigo' ? 'inputmode="decimal"' : '';
    cuerpo = q.tipo === 'texto' && (q.largo_maximo ?? 0) > 120
      ? `<textarea class="finput" name="${esc(q.id)}" maxlength="${q.largo_maximo}">${esc(v ?? '')}</textarea>`
      : `<input type="${tipo}" ${modo} name="${esc(q.id)}" value="${esc(v ?? '')}" />`;
  }
  return `<div class="pregunta" data-q="${esc(q.id)}">${cab}${cuerpo}<div class="q-error" data-err="${esc(q.id)}"></div></div>`;
}

function leerPantalla() {
  const m = S.motor;
  for (const q of m.preguntas(S.pantalla, S.respuestas)) {
    if (q.tipo === 'simple') {
      const x = $(`input[name="${CSS.escape(q.id)}"]:checked`);
      S.respuestas[q.id] = x ? x.value : null;
    } else if (q.tipo === 'multiple') {
      S.respuestas[q.id] = $$(`input[name="${CSS.escape(q.id)}"]:checked`).map((x) => x.value);
    } else if (q.tipo === 'grilla') {
      for (const f of m.filas(q, S.respuestas)) {
        const x = $(`input[name="${CSS.escape(q.id + '.' + f.codigo)}"]:checked`);
        S.respuestas[`${q.id}.${f.codigo}`] = x ? x.value : null;
      }
    } else if (q.tipo !== 'formula') {
      const x = $(`[name="${CSS.escape(q.id)}"]`);
      const val = x ? x.value.trim() : '';
      S.respuestas[q.id] = val === '' ? null : val;
    }
  }
  S.respuestas = m.calcularFormulas(S.pantalla, S.respuestas);
}

async function avanzar() {
  leerPantalla();
  const errores = S.motor.validarPantalla(S.pantalla, S.respuestas);
  $$('[data-err]').forEach((e) => { e.textContent = ''; });
  $('#err-pantalla').innerHTML = '';
  if (Object.keys(errores).length) {
    for (const [k, msg] of Object.entries(errores)) {
      if (k === '*') $('#err-pantalla').innerHTML = `<div class="alert alert-error mt">${esc(msg)}</div>`;
      else { const e = $(`[data-err="${CSS.escape(k)}"]`); if (e) e.textContent = msg; }
    }
    return;
  }
  const visibles = Object.fromEntries(Object.entries(S.respuestas).filter(([, v]) => v !== undefined));
  try {
    await api('POST', `/api/estacion/casos/${S.caso.id}/respuestas`, {
      respuestas: visibles, llamada_id: S.llamada,
      pantalla: { id: S.pantalla, segundos: Math.round((Date.now() - S.pantallaDesde) / 1000) } });
    // Cuotas en vivo: se reevalúan en cada pantalla, no una sola vez al cargar el caso.
    const llenas = await api('POST', `/api/estacion/casos/${S.caso.id}/cuotas`, { respuestas: visibles });
    if (llenas.length) return fueraDeCuota(llenas);
  } catch (e) { return toast(/** @type {any} */ (e).message, 'err'); }
  const sig = S.motor.siguiente(S.pantalla, S.respuestas);
  if (sig.fin != null) return terminarEntrevista(sig.fin);
  S.historial.push(S.pantalla);
  mostrarPantalla(sig.pantalla);
}

/** @param {any[]} llenas */
async function fueraDeCuota(llenas) {
  await modal({ titulo: 'Fuera de cuota', cuerpo: `<p>La cuota <b>${esc(llenas.map((q) => q.nombre).join(', '))}</b> ya está completa.
      Agradecé y cerrá la entrevista: queda clasificada como <b>Fuera de Cuota</b>.</p>`,
    pie: '<button class="btn btn-orange" data-ok>Cerrar entrevista</button>', sinCerrar: true },
  (caja, cerrar) => { $('[data-ok]', caja).onclick = () => cerrar(true); });
  await finalizar({ disposicion_telefono: 4, disposicion_encuesta: FUERA_DE_CUOTA, origen: 'sistema' });
}

/** @param {number} codigo */
async function terminarEntrevista(codigo) {
  S.fin = codigo;
  const etiqueta = S.catalogo.encuesta.find((/** @type {any} */ d) => d.codigo === codigo)?.etiqueta ?? codigo;
  $('#entrevista').innerHTML = `<div class="vacio-grande"><div class="accent-bar"></div><h3>Fin del cuestionario</h3>
    <p>La entrevista termina como <b>${esc(etiqueta)}</b>. Despedite y confirmá.</p>
    <button class="btn btn-orange" id="confirmar">Confirmar ${esc(etiqueta)}</button>
    <button class="btn btn-outline" id="volver">Volver a la última pantalla</button></div>`;
  $('#confirmar').onclick = async () => {
    const d = S.catalogo.encuesta.find((/** @type {any} */ x) => x.codigo === codigo);
    let motivo = null;
    if (d?.comportamiento === 21) motivo = prompt('Esta disposición pide un motivo:') || '';
    await finalizar({ disposicion_telefono: 4, disposicion_encuesta: codigo, motivo });
  };
  $('#volver').onclick = () => { S.fin = null; mostrarPantalla(S.pantalla || S.motor.primera(S.respuestas).pantalla); };
}

// ───────────────────────────── clasificación ─────────────────────────────

/** Diálogo de disposición del intento: teléfono y, si contestó, encuesta. */
async function clasificar() {
  const tel = S.catalogo.telefono.filter((/** @type {any} */ d) => [3, 4, 5, 6, 7, 8, 9].includes(d.codigo));
  const enc = S.catalogo.encuesta.filter((/** @type {any} */ d) => d.visible_encuestador && d.codigo !== COMPLETA && d.codigo !== FUERA_DE_CUOTA);
  const ahora = new Date();
  const pad = (/** @type {number} */ n) => String(n).padStart(2, '0');
  const fechaLocal = `${ahora.getFullYear()}-${pad(ahora.getMonth() + 1)}-${pad(ahora.getDate())}`;
  const r = await modal({
    titulo: 'Clasificar el intento', ancho: 600,
    cuerpo: `<div class="form-group"><label>¿Qué pasó con la línea?</label>
        <select class="fselect" id="d-tel">${tel.map((/** @type {any} */ d) => `<option value="${d.codigo}" ${d.codigo === 4 && S.conectada ? 'selected' : ''}>${d.codigo} · ${esc(d.etiqueta)}</option>`).join('')}</select>
        <div class="field-hint">Si la llamada no llegó a conectarse, el sistema ya registró ocupado, no contesta o fuera de servicio por su cuenta.</div></div>
      <div class="form-group" id="g-enc"><label>¿Qué pasó con el caso?</label>
        <select class="fselect" id="d-enc">${enc.map((/** @type {any} */ d) => `<option value="${d.codigo}" data-c="${d.comportamiento}">${d.codigo} · ${esc(d.etiqueta)}</option>`).join('')}</select></div>
      <div class="form-group hidden" id="g-motivo"><label>Motivo</label><textarea class="finput" id="d-motivo"></textarea></div>
      <div class="hidden" id="g-agenda"><div class="form-row">
          <div class="form-group"><label>Día</label><input type="date" id="ag-dia" value="${fechaLocal}" /></div>
          <div class="form-group"><label>Desde / hasta</label><div class="row" style="flex-wrap:nowrap"><input type="time" id="ag-desde" value="18:00" /><input type="time" id="ag-hasta" value="19:00" /></div></div></div>
        <label class="check-inline"><input type="checkbox" id="ag-mismo" checked /> Que la retome yo</label></div>`,
    pie: '<button class="btn btn-outline" data-pausa>Clasificar y pausa</button><button class="btn btn-orange" data-ok>Clasificar</button>',
  }, (caja, cerrar) => {
    const actualizar = () => {
      const contesto = $('#d-tel', caja).value === '4';
      $('#g-enc', caja).classList.toggle('hidden', !contesto);
      const op = $('#d-enc', caja).selectedOptions[0];
      $('#g-motivo', caja).classList.toggle('hidden', !contesto || op?.dataset.c !== '21');
      $('#g-agenda', caja).classList.toggle('hidden', !contesto || op?.dataset.c !== '22');
    };
    $('#d-tel', caja).onchange = actualizar;
    $('#d-enc', caja).onchange = actualizar;
    actualizar();
    const enviar = (/** @type {boolean} */ pausa) => {
      const dt = Number($('#d-tel', caja).value);
      /** @type {any} */ const d = { disposicion_telefono: dt, siguiente_estado: pausa ? 'pausa' : undefined };
      if (dt === 4) {
        d.disposicion_encuesta = Number($('#d-enc', caja).value);
        const c = $('#d-enc', caja).selectedOptions[0].dataset.c;
        if (c === '21') d.motivo = $('#d-motivo', caja).value.trim();
        if (c === '22') {
          const dia = $('#ag-dia', caja).value;
          d.agenda = { desde: new Date(`${dia}T${$('#ag-desde', caja).value}`).toISOString(),
                       hasta: new Date(`${dia}T${$('#ag-hasta', caja).value}`).toISOString(), preservar_encuestador: $('#ag-mismo', caja).checked };
        }
      }
      cerrar(d);
    };
    $('[data-ok]', caja).onclick = () => enviar(false);
    $('[data-pausa]', caja).onclick = () => enviar(true);
  });
  if (r) await finalizar(r);
}

/** @param {any} datos */
async function finalizar(datos) {
  try {
    if (S.pausaAlTerminar) { datos.siguiente_estado = 'pausa'; S.pausaAlTerminar = false; }
    await api('POST', '/api/estacion/finalizar', { caso_id: S.caso.id, llamada_id: S.llamada, ...datos });
    if (S.llamada && !datos.mantenerLlamada) await api('POST', '/api/estacion/llamada/colgar', {}).catch(() => { /* ya colgada */ });
    toast('Intento clasificado.', 'ok');
    S.estado = datos.siguiente_estado || 'disponible';
    pintarEstado();
    await siguienteOEsperar();
  } catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

window.addEventListener('beforeunload', () => { navigator.sendBeacon?.('/api/estacion/salida'); });
