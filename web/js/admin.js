// @ts-check
/**
 * Administración: estudios (modo de discado y sus parámetros, reintentos,
 * franjas, fuera de marco), muestra, cuotas, encuestadores, reportes del
 * piloto, exportación y usuarios.
 */
import { $, $$, api, barraLateral, descargar, encabezado, esc, exigir, fechaHora, modal, salir, toast } from './comun.js';

/** @type {any} */
const S = { yo: null, vista: 'estudios', estudio: null };
S.yo = await exigir(['administrador', 'investigador']);
const ADMIN = S.yo.roles.includes('administrador');
pintar();

function pintar() {
  const items = [
    { id: 'estudios', icono: '📋', texto: 'Estudios', seccion: 'Estudios' },
    ...(S.estudio ? [
      { id: 'parametros', icono: '⚙️', texto: 'Parámetros' },
      { id: 'muestra', icono: '📇', texto: 'Muestra' },
      { id: 'cuotas', icono: '🎯', texto: 'Cuotas' },
      { id: 'reportes', icono: '📊', texto: 'Reportes', seccion: 'Resultados' },
      { id: 'exportar', icono: '⬇️', texto: 'Exportar' },
    ] : []),
    ...(ADMIN ? [{ id: 'usuarios', icono: '👥', texto: 'Usuarios', seccion: 'Casa' }] : []),
  ];
  $('#app').innerHTML = barraLateral(S.yo, items, S.vista) + '<main class="main" id="main"></main>';
  $$('[data-nav]').forEach((b) => { b.onclick = () => { S.vista = b.dataset.nav; pintar(); }; });
  $('#salir').onclick = salir;
  const f = /** @type {Record<string, () => Promise<void>>} */ ({ estudios, parametros, muestra, cuotas, reportes, exportar, usuarios })[S.vista];
  f().catch((e) => { $('#main').innerHTML += `<div class="alert alert-error">${esc(e.message)}</div>`; });
}

const nombreEstudio = () => S.estudio ? `${S.estudio.codigo} · ${S.estudio.nombre}` : '';
/** @param {string} estado */
const badgeEstado = (estado) => `<span class="est est-${{ activo: 'completa', borrador: 'no_correspondia', pausado: 'sin_salida', cerrado: 'inactivo' }[estado] || 'inactivo'}">${esc(estado)}</span>`;

// ───────────────────────────── estudios ─────────────────────────────

async function estudios() {
  const lista = await api('GET', '/api/estudios');
  $('#main').innerHTML = encabezado('Estudios', '', 'Elegí un estudio para configurarlo, cargar muestra o ver sus reportes.') +
    `<div class="card"><div class="card-body tight"><div class="table-wrap"><table><thead><tr><th>Código</th><th>Nombre</th><th>Estado</th><th>Modo</th><th>Casos</th><th>Completas</th><th>Marco</th><th></th></tr></thead><tbody>
    ${lista.map((/** @type {any} */ e) => `<tr><td class="td-strong">${esc(e.codigo)}</td><td>${esc(e.nombre)}${e.es_estudio ? '' : ' <span class="badge badge-user">prueba</span>'}</td>
      <td>${badgeEstado(e.estado)}</td><td><span class="badge badge-modo">${esc(e.modo_discado)}</span></td><td>${e.casos}</td>
      <td>${e.completas}${e.objetivo_completas ? ` / ${e.objetivo_completas}` : ''}</td><td class="td-muted">${esc(e.tipo_marco)}</td>
      <td><button class="btn btn-outline btn-sm" data-abrir="${e.id}">Abrir</button></td></tr>`).join('') || '<tr><td colspan="8" class="empty-state">Todavía no hay estudios.</td></tr>'}
    </tbody></table></div></div></div>`;
  if (ADMIN) {
    $('#ph-acciones').innerHTML = '<button class="btn btn-orange" id="nuevo">Nuevo estudio</button>';
    $('#nuevo').onclick = nuevoEstudio;
  }
  $$('[data-abrir]').forEach((b) => { b.onclick = async () => { S.estudio = await api('GET', `/api/estudios/${b.dataset.abrir}`); S.vista = 'parametros'; pintar(); }; });
}

async function nuevoEstudio() {
  const d = await modal({ titulo: 'Nuevo estudio', cuerpo: `<div class="form-row"><div class="form-group"><label>Código</label><input type="text" id="n-cod" placeholder="BCO-2026-11" /></div>
      <div class="form-group"><label>Cliente</label><input type="text" id="n-cli" /></div></div>
      <div class="form-group"><label>Nombre</label><input type="text" id="n-nom" /></div>
      <div class="form-row"><div class="form-group"><label>Tipo de marco</label><select class="fselect" id="n-marco">
        <option value="probabilistico">Muestra probabilística de población general</option><option value="base_cliente">Base de clientes del cliente</option>
        <option value="empresas">Empresas o instituciones</option><option value="dificil">Población difícil de alcanzar</option><option value="otro">Otro</option></select></div>
      <div class="form-group"><label>Modo de discado</label><select class="fselect" id="n-modo"><option>preview</option><option>manual</option><option>power</option><option>predictivo</option></select></div></div>
      <label class="check-inline"><input type="checkbox" id="n-prueba" /> Es una prueba o capacitación (no sale en reportes)</label>`,
  pie: '<button class="btn btn-orange" data-ok>Crear</button>' }, (caja, cerrar) => {
    $('[data-ok]', caja).onclick = () => cerrar({ codigo: $('#n-cod', caja).value.trim(), nombre: $('#n-nom', caja).value.trim(), cliente: $('#n-cli', caja).value.trim() || null,
      tipo_marco: $('#n-marco', caja).value, modo_discado: $('#n-modo', caja).value, es_estudio: !$('#n-prueba', caja).checked,
      // Política de fuera de marco: prendido solo para empresas y poblaciones difíciles.
      fuera_marco_habilitado: ['empresas', 'dificil'].includes($('#n-marco', caja).value) });
  });
  if (!d) return;
  try { const e = await api('POST', '/api/estudios', d); S.estudio = await api('GET', `/api/estudios/${e.id}`); S.vista = 'parametros'; pintar(); }
  catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

// ───────────────────────────── parámetros ─────────────────────────────

/** @param {string} id @param {string} etiqueta @param {any} valor @param {string} [ayuda] @param {string} [tipo] */
const campo = (id, etiqueta, valor, ayuda = '', tipo = 'number') => `<div class="form-group"><label for="${id}">${etiqueta}</label>
  <input type="${tipo}" id="${id}" value="${esc(valor ?? '')}" ${tipo === 'number' ? 'step="any"' : ''} ${ADMIN ? '' : 'disabled'} />${ayuda ? `<div class="field-hint">${ayuda}</div>` : ''}</div>`;
/** @param {string} id @param {string} etiqueta @param {boolean} valor */
const check = (id, etiqueta, valor) => `<label class="check-inline mb"><input type="checkbox" id="${id}" ${valor ? 'checked' : ''} ${ADMIN ? '' : 'disabled'} /> ${etiqueta}</label><br>`;

async function parametros() {
  const e = S.estudio = await api('GET', `/api/estudios/${S.estudio.id}`);
  const usuarios = ADMIN ? await api('GET', '/api/usuarios') : [];
  const asignados = new Set(e.encuestadores.map((/** @type {any} */ u) => u.uid));
  $('#main').innerHTML = encabezado(e.nombre, '', `${e.codigo} · creado ${fechaHora(e.creado_en)} · diccionario v${e.version_diccionario} · fórmulas v${e.version_formulas}`) +
    `<div class="grid-2">
      <div class="card"><div class="card-header"><span class="card-header-title">Discado</span></div><div class="card-body">
        <div class="form-group"><label>Modo</label><select class="fselect" id="p-modo_discado" ${ADMIN ? '' : 'disabled'}>${['manual', 'preview', 'power', 'predictivo'].map((m) => `<option ${m === e.modo_discado ? 'selected' : ''}>${m}</option>`).join('')}</select>
          <div class="field-hint">En manual los parámetros de sobre-discado no aplican: el encuestador marca y el sistema registra.</div></div>
        <div class="form-row">${campo('p-n_inicial', 'N inicial', e.n_inicial, 'Llamadas por encuestador disponible al arrancar')}${campo('p-n_maximo', 'N máximo', e.n_maximo, 'Techo duro, no superable por el ajuste')}</div>
        <div class="form-row">${campo('p-abandono_objetivo', 'Abandono objetivo', e.abandono_objetivo, 'Proporción: 0,03 = 3 %')}${campo('p-ventana_abandono_min', 'Ventana (min)', e.ventana_abandono_min, 'Ventana deslizante, entre 30 y 60')}</div>
        <div class="form-row">${campo('p-min_agentes_predictivo', 'Mínimo de agentes para predictivo', e.min_agentes_predictivo, 'Por debajo cae a power')}${campo('p-muestreo_grabacion', 'Muestreo de grabación', e.muestreo_grabacion, '0,10 = una de cada diez')}</div>
        ${check('p-ajuste_automatico', 'Ajuste automático de N', e.ajuste_automatico)}
      </div></div>
      <div class="card"><div class="card-header"><span class="card-header-title">Estudio y muestra</span><span>${badgeEstado(e.estado)}</span></div><div class="card-body">
        ${campo('p-nombre', 'Nombre', e.nombre, '', 'text')}
        <div class="form-row">${campo('p-objetivo_completas', 'Objetivo de completas', e.objetivo_completas)}${campo('p-cliente', 'Cliente', e.cliente, '', 'text')}</div>
        <div class="form-row">${campo('p-tope_intentos_caso', 'Tope de intentos por caso', e.tope_intentos_caso)}${campo('p-tope_intentos_franja', 'Tope por franja', e.tope_intentos_franja)}</div>
        ${check('p-fuera_marco_habilitado', 'Admite casos fuera de marco (derivados)', e.fuera_marco_habilitado)}
        ${campo('p-umbral_alerta_derivados', 'Alerta de composición derivada por estrato', e.umbral_alerta_derivados, 'Proporción; vacío = sin alerta')}
        ${check('p-sustitutos_habilitados', 'Usa grupos de sustitutos', e.sustitutos_habilitados)}
        ${check('p-preservar_encuestador', 'Las agendas vuelven al mismo encuestador', e.preservar_encuestador)}
        ${check('p-ignorar_agendados', 'Ignorar agendados en la asignación', e.ignorar_agendados)}
        ${check('p-es_estudio', 'Es un estudio (desmarcado = prueba o capacitación)', e.es_estudio)}
      </div></div>
    </div>
    <div class="grid-2 mt">
      <div class="card"><div class="card-header"><span class="card-header-title">Reintentos por disposición</span></div><div class="card-body tight"><div class="table-wrap"><table>
        <thead><tr><th>Disposición</th><th>Espera (min)</th><th>Tope</th><th>Ventana</th></tr></thead><tbody>
        ${e.reintentos.map((/** @type {any} */ r) => `<tr data-regla="${r.codigo_telefono}"><td><b>${r.codigo_telefono}</b> ${esc(r.etiqueta)}</td>
          <td><input type="number" data-k="espera_min" value="${r.espera_min}" style="width:90px" ${ADMIN ? '' : 'disabled'} /></td>
          <td><input type="number" data-k="tope" value="${r.tope ?? ''}" style="width:70px" ${ADMIN ? '' : 'disabled'} /></td>
          <td><select class="fselect" data-k="ventana" ${ADMIN ? '' : 'disabled'}>${['misma', 'rotar', 'cualquiera'].map((v) => `<option ${v === r.ventana ? 'selected' : ''}>${v}</option>`).join('')}</select></td></tr>`).join('')}
        </tbody></table></div></div></div>
      <div class="card"><div class="card-header"><span class="card-header-title">Franjas horarias</span>${ADMIN ? '<button class="btn btn-outline btn-sm" id="franja-mas">Agregar</button>' : ''}</div>
        <div class="card-body" id="franjas">${e.franjas.map((/** @type {any} */ f) => filaFranja(f)).join('')}</div></div>
    </div>
    ${ADMIN ? `<div class="card mt"><div class="card-header"><span class="card-header-title">Encuestadores asignados</span></div><div class="card-body">
      <div class="pick-list">${usuarios.filter((/** @type {any} */ u) => u.roles.includes('encuestador')).map((/** @type {any} */ u) => `<label class="pick">
        <input type="checkbox" data-uid="${esc(u.uid)}" ${asignados.has(u.uid) ? 'checked' : ''} /><div class="pick-avatar">${esc(u.nombre[0])}</div>
        <div><div class="pick-name">${esc(u.nombre)}</div><div class="pick-email">${esc(u.email)}</div></div></label>`).join('') || '<div class="empty-state">No hay encuestadores dados de alta.</div>'}</div>
      <button class="btn btn-outline btn-sm mt" id="guardar-enc">Guardar asignación</button></div></div>` : ''}`;
  if (ADMIN) {
    $('#ph-acciones').innerHTML = `${e.estado !== 'activo' ? '<button class="btn btn-green" data-estado="activo">Activar</button>' : '<button class="btn btn-outline" data-estado="pausado">Pausar</button>'}
      ${e.estado !== 'cerrado' ? '<button class="btn btn-outline btn-del" data-estado="cerrado">Cerrar</button>' : ''}<button class="btn btn-orange" id="guardar">Guardar</button>`;
    $$('[data-estado]').forEach((b) => { b.onclick = () => guardarParametros({ estado: b.dataset.estado }); });
    $('#guardar').onclick = () => guardarParametros(leerParametros());
    $('#franja-mas').onclick = () => $('#franjas').insertAdjacentHTML('beforeend', filaFranja({ nombre: '', desde: '', hasta: '' }));
    $('#guardar-enc').onclick = async () => {
      await api('PUT', `/api/estudios/${e.id}/encuestadores`, { uids: $$('[data-uid]').filter((x) => x.checked).map((x) => x.dataset.uid) });
      toast('Asignación guardada.', 'ok');
    };
  }
}

/** @param {any} f */
function filaFranja(f) {
  return `<div class="row mb" data-franja><input type="text" data-k="nombre" value="${esc(f.nombre)}" placeholder="nombre" style="width:140px" ${ADMIN ? '' : 'disabled'} />
    <input type="time" data-k="desde" value="${esc(String(f.desde).slice(0, 5))}" style="width:130px" ${ADMIN ? '' : 'disabled'} />
    <input type="time" data-k="hasta" value="${esc(String(f.hasta).slice(0, 5))}" style="width:130px" ${ADMIN ? '' : 'disabled'} /></div>`;
}

function leerParametros() {
  /** @type {any} */ const d = {};
  $$('[id^="p-"]').forEach((el) => {
    const k = el.id.slice(2);
    if (el.type === 'checkbox') d[k] = el.checked;
    else if (el.type === 'number') d[k] = el.value === '' ? null : Number(el.value);
    else d[k] = el.value || null;
  });
  d.reintentos = $$('[data-regla]').map((tr) => ({ codigo_telefono: Number(tr.dataset.regla), espera_min: Number($('[data-k=espera_min]', tr).value),
    tope: $('[data-k=tope]', tr).value === '' ? null : Number($('[data-k=tope]', tr).value), ventana: $('[data-k=ventana]', tr).value }));
  d.franjas = $$('[data-franja]').map((r) => ({ nombre: $('[data-k=nombre]', r).value.trim(), desde: $('[data-k=desde]', r).value, hasta: $('[data-k=hasta]', r).value }))
    .filter((/** @type {any} */ f) => f.nombre && f.desde && f.hasta);
  return d;
}

/** @param {any} d */
async function guardarParametros(d) {
  try { S.estudio = await api('PATCH', `/api/estudios/${S.estudio.id}`, d); toast('Estudio actualizado.', 'ok'); parametros(); }
  catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

// ───────────────────────────── muestra ─────────────────────────────

async function muestra() {
  const e = S.estudio = await api('GET', `/api/estudios/${S.estudio.id}`);
  $('#main').innerHTML = encabezado('Muestra', nombreEstudio(), 'Guías cargadas (phonebooks). Columnas: telefono, telefono2…, identificador, estrato, area, sustituto, grupo_sustituto; el resto va a la ficha.') +
    `<div class="card"><div class="card-body tight"><div class="table-wrap"><table><thead><tr><th>Guía</th><th>Casos</th><th>Fuera de marco</th><th>Tope fuera de marco</th><th>Prioridad</th><th>Activa</th><th>Cargada</th></tr></thead><tbody>
    ${e.guias.map((/** @type {any} */ g) => `<tr><td class="td-strong">${esc(g.nombre)}</td><td>${g.casos}</td><td>${g.fuera_marco}</td>
      <td>${g.tope_fuera_marco != null ? `${(g.tope_fuera_marco * 100).toFixed(0)}%` : 'sin tope'}</td><td>${g.prioridad}</td>
      <td>${ADMIN ? `<button class="btn btn-outline btn-sm" data-guia="${g.id}" data-activa="${g.activa}">${g.activa ? 'Desactivar' : 'Activar'}</button>` : g.activa ? 'sí' : 'no'}</td>
      <td class="td-muted">${fechaHora(g.cargada_en)}</td></tr>`).join('') || '<tr><td colspan="7" class="empty-state">Sin guías cargadas.</td></tr>'}
    </tbody></table></div></div></div>
    ${ADMIN ? `<div class="card mt"><div class="card-header"><span class="card-header-title">Cargar guía</span></div><div class="card-body">
      <div class="form-row"><div class="form-group"><label>Nombre</label><input type="text" id="g-nombre" /></div>
        <div class="form-group"><label>Origen</label><select class="fselect" id="g-origen"><option value="marco">Marco (muestra del diseño)</option>
          <option value="lista_externa">Lista externa (agregado, fuera de marco)</option></select></div></div>
      <div class="form-row"><div class="form-group"><label>Prioridad</label><input type="number" id="g-prio" value="0" /></div>
        <div class="form-group"><label>Tope de fuera de marco</label><input type="number" id="g-tope" step="0.01" placeholder="vacío = sin tope" /></div></div>
      <div class="form-group"><label>Archivo CSV</label><input type="file" id="g-archivo" accept=".csv,text/csv" /></div>
      <button class="btn btn-orange" id="g-cargar">Cargar</button><div id="g-res" class="mt"></div></div></div>` : ''}`;
  $$('[data-guia]').forEach((b) => { b.onclick = async () => { await api('PATCH', `/api/guias/${b.dataset.guia}`, { activa: b.dataset.activa !== 'true' }); muestra(); }; });
  if (!ADMIN) return;
  $('#g-cargar').onclick = async () => {
    const archivo = $('#g-archivo').files[0];
    if (!archivo || !$('#g-nombre').value.trim()) return toast('Nombre y archivo.', 'err');
    $('#g-cargar').disabled = true;
    try {
      const r = await api('POST', `/api/estudios/${e.id}/guias`, { nombre: $('#g-nombre').value.trim(), csv: await archivo.text(),
        origen: $('#g-origen').value, prioridad: Number($('#g-prio').value || 0), tope_fuera_marco: $('#g-tope').value === '' ? null : Number($('#g-tope').value) });
      $('#g-res').innerHTML = `<div class="alert alert-success">${r.casos} casos y ${r.telefonos} teléfonos cargados.
        ${r.total_rechazadas ? ` ${r.total_rechazadas} líneas rechazadas.` : ''}${r.casos_con_numero_en_lista_negra ? ` ${r.casos_con_numero_en_lista_negra} casos tienen un número en la lista de no llamar.` : ''}</div>
        ${r.rechazadas.length ? `<div class="small muted">${r.rechazadas.slice(0, 20).map((/** @type {any} */ x) => `línea ${x.linea}: ${esc(x.motivo)}`).join('<br>')}</div>` : ''}`;
      setTimeout(muestra, 2500);
    } catch (err) { toast(/** @type {any} */ (err).message, 'err'); }
    $('#g-cargar').disabled = false;
  };
}

// ───────────────────────────── cuotas ─────────────────────────────

async function cuotas() {
  const lista = await api('GET', `/api/estudios/${S.estudio.id}/cuotas`);
  $('#main').innerHTML = encabezado('Cuotas', nombreEstudio(), 'Avance calculado sobre completas, incluidas las de origen fuera de marco (cuentan para cuota, no para la tasa de respuesta).') +
    `<div class="card"><div class="card-body tight"><div class="table-wrap"><table><thead><tr><th>Cuota</th><th>Tipo</th><th>Condición</th><th>Avance</th><th>Fuera de marco</th><th></th></tr></thead><tbody>
    ${lista.map((/** @type {any} */ q) => {
      const pct = q.objetivo ? Math.min(100, (q.logradas / q.objetivo) * 100) : 0;
      return `<tr><td class="td-strong">${esc(q.nombre)}</td><td>${esc(q.tipo)}</td><td class="td-muted">${esc(q.pregunta || q.propiedad || 'área')} ∈ {${esc(q.valores.join(', '))}}</td>
        <td style="min-width:180px">${q.logradas} / ${q.objetivo ?? '—'}<div class="medidor ${pct >= 100 ? 'alto' : ''}"><div style="width:${pct}%"></div></div></td>
        <td>${q.logradas_fuera_marco}</td><td>${ADMIN ? `<button class="btn btn-outline btn-sm btn-del" data-borrar="${q.cuota_id}">Borrar</button>` : ''}</td></tr>`;
    }).join('') || '<tr><td colspan="6" class="empty-state">Sin cuotas.</td></tr>'}</tbody></table></div></div></div>`;
  if (!ADMIN) return;
  $('#ph-acciones').innerHTML = '<button class="btn btn-orange" id="nueva">Nueva cuota</button>';
  $$('[data-borrar]').forEach((b) => { b.onclick = async () => { await api('DELETE', `/api/cuotas/${b.dataset.borrar}`); cuotas(); }; });
  $('#nueva').onclick = async () => {
    const d = await modal({ titulo: 'Nueva cuota', cuerpo: `<div class="form-group"><label>Nombre</label><input type="text" id="q-nom" /></div>
      <div class="form-row"><div class="form-group"><label>Tipo</label><select class="fselect" id="q-tipo"><option value="pregunta">Por pregunta</option><option value="propiedad">Por propiedad de la ficha</option><option value="area">Por área</option></select></div>
      <div class="form-group"><label>Pregunta o propiedad</label><input type="text" id="q-var" placeholder="SEXO / departamento" /></div></div>
      <div class="form-group"><label>Valores (separados por coma)</label><input type="text" id="q-val" placeholder="1,2" /></div>
      <div class="form-row"><div class="form-group"><label>Objetivo absoluto</label><input type="number" id="q-obj" /></div>
      <div class="form-group"><label>o % del objetivo del estudio</label><input type="number" id="q-pct" /></div></div>`,
    pie: '<button class="btn btn-orange" data-ok>Crear</button>' }, (caja, cerrar) => {
      $('[data-ok]', caja).onclick = () => {
        const tipo = $('#q-tipo', caja).value, v = $('#q-var', caja).value.trim();
        cerrar({ nombre: $('#q-nom', caja).value.trim(), tipo, pregunta: tipo === 'pregunta' ? v : null, propiedad: tipo === 'propiedad' ? v : null,
          valores: $('#q-val', caja).value, objetivo: $('#q-obj', caja).value ? Number($('#q-obj', caja).value) : null,
          porcentaje: $('#q-pct', caja).value ? Number($('#q-pct', caja).value) : null });
      };
    });
    if (!d) return;
    try { await api('POST', `/api/estudios/${S.estudio.id}/cuotas`, d); cuotas(); } catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
  };
}

// ───────────────────────────── reportes ─────────────────────────────

async function reportes() {
  const corte = S.corte || 'parcial';
  $('#main').innerHTML = encabezado('Reportes', nombreEstudio(), 'Cada número sale del log de eventos con las fórmulas v1 escritas y su denominador al lado.') + '<div id="rep"><div class="spinner"></div></div>';
  $('#ph-acciones').innerHTML = `<div class="seg">${['parcial', 'cierre'].map((c) => `<label><input type="radio" name="corte" value="${c}" ${c === corte ? 'checked' : ''}/><span>${c === 'parcial' ? 'Parcial' : 'De cierre'}</span></label>`).join('')}</div>`;
  $$('[name=corte]').forEach((r) => { r.onchange = () => { S.corte = r.value; reportes(); }; });
  let ficha;
  try { ficha = await api('GET', `/api/estudios/${S.estudio.id}/reportes/ficha?corte=${corte}`); }
  catch (e) { $('#rep').innerHTML = `<div class="alert alert-warn">${esc(/** @type {any} */ (e).message)}</div>`; return; }
  const [prod, fuera] = await Promise.all([
    api('GET', `/api/estudios/${S.estudio.id}/reportes/productividad?desde=${new Date(Date.now() - 30 * 864e5).toISOString()}`),
    api('GET', `/api/estudios/${S.estudio.id}/reportes/fuera-de-marco`)]);
  const t = ficha.tasas;
  const g = ficha.conteos_por_grupo;
  $('#rep').innerHTML = `${ficha.aviso_corte ? `<div class="alert alert-info">${esc(ficha.aviso_corte)}</div>` : ''}
    ${ficha.advertencia_comparabilidad ? `<div class="alert alert-warn">${esc(ficha.advertencia_comparabilidad)}</div>` : ''}
    <div class="stat-grid">
      ${[['respuesta', 's-ok'], ['cooperacion', 's-free'], ['contacto', 's-total'], ['rechazo', 's-bad']].map(([k, c]) =>
        `<div class="stat ${c}"><div class="stat-num" style="font-size:1.35rem">${esc(t[k].texto)}</div><div class="stat-label">${esc(t[k].nombre)}</div></div>`).join('')}
      <div class="stat s-warn"><div class="stat-num" style="font-size:1.35rem">${esc(ficha.proporcion_fuera_de_marco.texto)}</div><div class="stat-label">Completas fuera de marco</div></div>
    </div>
    <div class="grid-2">
      <div class="card"><div class="card-header"><span class="card-header-title">Ficha técnica</span></div><div class="card-body small"><dl class="ficha" style="display:grid;grid-template-columns:max-content 1fr;gap:.4rem .9rem">
        <dt>Variante</dt><dd>${esc(ficha.variante)}</dd><dt>Marco</dt><dd>${ficha.tamano_marco} casos · ${ficha.casos_trabajados} trabajados</dd>
        <dt>Completas entregadas</dt><dd>${ficha.completas_entregadas} (${ficha.completas_fuera_de_marco} fuera de marco)</dd>
        <dt>Diccionario</dt><dd>v${ficha.version_diccionario}</dd><dt>Fórmulas</dt><dd>v${ficha.version_formulas}</dd><dt>Corte</dt><dd>${esc(ficha.corte)} · ${fechaHora(ficha.hasta)}</dd>
        ${ficha.abandono ? `<dt>Abandono real</dt><dd>${esc(ficha.abandono.abandono_real.texto)}</dd>` : ''}</dl></div></div>
      <div class="card"><div class="card-header"><span class="card-header-title">Casos del marco por grupo</span></div><div class="card-body tight"><table><tbody>
        ${[['C', 'Entrevistas completas'], ['I', 'Interrumpidas no promovidas'], ['R', 'Rechazos'], ['NC', 'Elegibles sin contacto o sin cierre'], ['AB', 'Abandonadas por el sistema'],
          ['NE', 'No elegibles'], ['UD', 'Elegibilidad desconocida'], ['EP', 'En proceso (fuera de las tasas)'], ['SC', 'Estado extendido sin clasificar']]
          .map(([k, n]) => `<tr><td><span class="kbd">${k}</span> ${n}</td><td class="right td-strong">${g[k] ?? 0}</td></tr>`).join('')}</tbody></table></div></div>
    </div>
    <div class="card mt"><div class="card-header"><span class="card-header-title">Productividad · últimos 30 días</span></div><div class="card-body tight"><div class="table-wrap"><table>
      <thead><tr><th>Encuestador</th><th>Horas de sesión</th><th>Intentos</th><th>Contactos</th><th>Completas</th><th>Completas/hora</th><th>Cooperación</th><th>Ocupación</th><th>Derivados creados</th></tr></thead><tbody>
      ${prod.map((/** @type {any} */ p) => `<tr><td class="td-strong">${esc(p.nombre)}</td><td>${p.horas_sesion}</td><td>${p.intentos}</td><td>${p.contactos}</td><td>${p.completas}</td>
        <td class="td-strong">${p.completas_por_hora ?? '—'}</td><td>${esc(p.cooperacion.texto)}</td><td>${p.ocupacion != null ? (p.ocupacion * 100).toFixed(1) + '%' : '—'}</td><td>${p.fuera_marco_creados}</td></tr>`).join('')
        || '<tr><td colspan="9" class="empty-state">Sin actividad en el período.</td></tr>'}</tbody></table></div>
      <div class="card-body small muted">Los encuestadores se evalúan con cooperación y completas por hora juntas, nunca con la tasa de respuesta (documento de fórmulas).</div></div>
    <div class="card mt"><div class="card-header"><span class="card-header-title">Fuera de marco</span></div><div class="card-body tight"><table><tbody>
      ${fuera.por_origen.map((/** @type {any} */ o) => `<tr><td>${esc(o.origen)}</td><td>${o.casos} casos</td><td>${o.completas} completas</td></tr>`).join('')}
      ${fuera.alertas_por_estrato.filter((/** @type {any} */ a) => a.en_alerta).map((/** @type {any} */ a) => `<tr><td colspan="3"><span class="badge badge-err">Alerta</span> estrato ${esc(a.estrato || '—')}: ${(a.proporcion * 100).toFixed(1)}% de completas derivadas (umbral ${(a.umbral * 100).toFixed(0)}%)</td></tr>`).join('')}
    </tbody></table></div></div>`;
}

// ───────────────────────────── exportar ─────────────────────────────

async function exportar() {
  $('#main').innerHTML = encabezado('Exportar', nombreEstudio(), 'Respuestas con el origen del caso en cada registro y la marca «ponderable» (los fuera de marco quedan fuera del análisis ponderado).') +
    `<div class="card"><div class="card-body"><label class="check-inline mb"><input type="checkbox" id="x-todas" /> Incluir casos no completos</label><div class="big-actions">
      ${[['sav', 'SPSS (.sav)', '📊'], ['xlsx', 'Excel', '📗'], ['csv', 'CSV', '📄']].map(([f, t, i]) => `<button class="big-btn big-btn-out" data-fmt="${f}"><span class="bi">${i}</span>${t}</button>`).join('')}</div>
      <p class="ficha-note">Las variables de respuesta múltiple salen como una variable 0/1 por opción; las grillas, una variable por fila.</p></div></div>`;
  $$('[data-fmt]').forEach((b) => {
    b.onclick = async () => {
      try { await descargar(`/api/estudios/${S.estudio.id}/exportar?formato=${b.dataset.fmt}${$('#x-todas').checked ? '&todas=1' : ''}`, `${S.estudio.codigo}.${b.dataset.fmt}`); }
      catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
    };
  });
}

// ───────────────────────────── usuarios ─────────────────────────────

async function usuarios() {
  const lista = await api('GET', '/api/usuarios');
  $('#main').innerHTML = encabezado('Usuarios', '', 'Firebase Auth autentica; los roles viven en INVOKE 2. Alta de encuestador: usuario, URL, auricular y prueba de audio automatizada.') +
    `<div class="card"><div class="card-body tight"><div class="table-wrap"><table><thead><tr><th>Nombre</th><th>Correo</th><th>Roles</th><th>Estado</th><th></th></tr></thead><tbody>
    ${lista.map((/** @type {any} */ u) => `<tr><td class="td-strong">${esc(u.nombre)}</td><td class="td-muted">${esc(u.email)}</td>
      <td>${u.roles.map((/** @type {string} */ r) => `<span class="badge ${r === 'administrador' ? 'badge-admin' : 'badge-user'}">${esc(r)}</span>`).join(' ')}</td>
      <td><span class="badge ${u.activo ? 'badge-on' : 'badge-off'}">${u.activo ? 'activo' : 'inactivo'}</span></td>
      <td><button class="btn btn-outline btn-sm" data-u="${esc(u.uid)}">Editar</button></td></tr>`).join('')}</tbody></table></div></div></div>`;
  $('#ph-acciones').innerHTML = '<button class="btn btn-orange" id="nuevo">Nuevo usuario</button>';
  $('#nuevo').onclick = () => editarUsuario(null);
  $$('[data-u]').forEach((b) => { b.onclick = () => editarUsuario(lista.find((/** @type {any} */ u) => u.uid === b.dataset.u)); });
}

/** @param {any} u */
async function editarUsuario(u) {
  const roles = ['encuestador', 'supervisor', 'administrador', 'investigador'];
  const d = await modal({ titulo: u ? 'Editar usuario' : 'Nuevo usuario', cuerpo: `<div class="form-group"><label>Nombre</label><input type="text" id="u-nom" value="${esc(u?.nombre ?? '')}" /></div>
      <div class="form-group"><label>Correo</label><input type="email" id="u-mail" value="${esc(u?.email ?? '')}" /></div>
      ${u ? '' : '<div class="form-group"><label>Clave inicial</label><input type="text" id="u-clave" /><div class="field-hint">Si el correo ya existe en Firebase, se usa esa cuenta.</div></div>'}
      <div class="form-group"><label>Roles</label><div class="seg">${roles.map((r) => `<label><input type="checkbox" value="${r}" ${u?.roles.includes(r) ? 'checked' : ''} /><span>${r}</span></label>`).join('')}</div></div>
      <label class="check-inline"><input type="checkbox" id="u-activo" ${u?.activo === false ? '' : 'checked'} /> Activo</label>`,
  pie: '<button class="btn btn-orange" data-ok>Guardar</button>' }, (caja, cerrar) => {
    $('[data-ok]', caja).onclick = () => cerrar({ uid: u?.uid, nombre: $('#u-nom', caja).value.trim(), email: $('#u-mail', caja).value.trim(),
      clave_inicial: $('#u-clave', caja)?.value || undefined, activo: $('#u-activo', caja).checked,
      roles: $$('.seg input', caja).filter((x) => x.checked).map((x) => x.value) });
  });
  if (!d) return;
  try { await api('POST', '/api/usuarios', d); toast('Usuario guardado.', 'ok'); usuarios(); } catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}
