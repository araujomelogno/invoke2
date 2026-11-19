// @ts-check
/**
 * Editor gráfico de cuestionarios (ADR-006). Produce y consume el mismo JSON
 * que ejecuta el puesto: no hay un formato intermedio. La vista previa corre
 * el motor real (motor.js), así que lo que se ve acá es lo que ve el encuestador.
 */
import { $, $$, api, barraLateral, descargar, encabezado, esc, exigir, fechaHora, modal, salir, toast } from './comun.js';
import { Motor } from './motor.js';
import { variables } from './expresiones.js';

/** @type {any} */
const S = { yo: null, estudios: [], estudio: null, def: null, sel: null, versiones: [], previa: { respuestas: {}, pantalla: null } };
S.yo = await exigir(['administrador', 'investigador']);
S.estudios = await api('GET', '/api/estudios');
pintarMarco();

function nuevoCuestionario() {
  return { formato: 1, id: 'cuestionario', nombre: 'Nuevo cuestionario', presentacion: { texto: 'Buenas {ficha.saludo}, le llamo de Equipos Consultores…' },
    bloques: [{ id: 'B1', pantallas: [{ id: 'S1', preguntas: [{ id: 'P1', tipo: 'simple', texto: 'Pregunta', opciones: [{ codigo: '1', texto: 'Sí' }, { codigo: '2', texto: 'No' }] }] }] }] };
}

function pintarMarco() {
  $('#app').innerHTML = barraLateral(S.yo, [{ id: 'editor', icono: '✏️', texto: 'Editor', seccion: 'Cuestionario' }], 'editor') + '<main class="main" id="main"></main>';
  $('#salir').onclick = salir;
  $('#main').innerHTML = encabezado('Editor', 'de cuestionario', 'El JSON que se guarda acá es la fuente de verdad y el formato de ejecución.') +
    `<div class="card mb"><div class="card-body row between">
      <div class="row"><select class="fselect" id="estudio" style="width:auto"><option value="">Elegí un estudio…</option>${S.estudios.map((/** @type {any} */ e) =>
        `<option value="${e.id}">${esc(e.codigo)} · ${esc(e.nombre)}</option>`).join('')}</select><span id="version" class="small muted"></span></div>
      <div class="row"><button class="btn btn-outline btn-sm" id="importar">Importar JSON</button><button class="btn btn-outline btn-sm" id="exportar">Exportar JSON</button>
        <button class="btn btn-outline btn-sm" id="json">Ver JSON</button><button class="btn btn-dark btn-sm" id="guardar" disabled>Guardar borrador</button>
        <button class="btn btn-orange btn-sm" id="publicar" disabled>Publicar</button></div></div></div>
    <div id="errores"></div><div class="editor" id="editor"></div>`;
  $('#estudio').onchange = () => cargar(Number($('#estudio').value));
  $('#guardar').onclick = guardar;
  $('#publicar').onclick = publicar;
  $('#json').onclick = verJson;
  $('#exportar').onclick = () => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(S.def, null, 2)], { type: 'application/json' }));
    a.download = `${S.def?.id || 'cuestionario'}.json`;
    a.click();
  };
  $('#importar').onclick = () => {
    const i = document.createElement('input');
    i.type = 'file'; i.accept = '.json,application/json';
    i.onchange = async () => { try { S.def = JSON.parse(await /** @type {any} */ (i.files)[0].text()); S.sel = null; pintarEditor(); } catch { toast('JSON inválido.', 'err'); } };
    i.click();
  };
}

/** @param {number} estudioId */
async function cargar(estudioId) {
  S.estudio = estudioId;
  if (!estudioId) return;
  S.versiones = await api('GET', `/api/estudios/${estudioId}/cuestionarios`);
  const ultima = S.versiones[0];
  S.def = ultima ? (await api('GET', `/api/cuestionarios/${ultima.id}`)).definicion : nuevoCuestionario();
  $('#version').textContent = ultima ? `v${ultima.version} · ${ultima.publicado_en ? 'publicada ' + fechaHora(ultima.publicado_en) : 'borrador'}` : 'sin versiones';
  $('#guardar').disabled = false;
  $('#publicar').disabled = false;
  S.sel = { tipo: 'cuestionario' };
  pintarEditor();
}

// ───────────────────────────── estructura ─────────────────────────────

/** Ubica pantalla y pregunta por id. @param {string} pid */
function pantallaPorId(pid) {
  for (const b of S.def.bloques) for (const p of b.pantallas) if (p.id === pid) return { b, p };
  return null;
}

function pintarEditor() {
  const d = S.def;
  $('#editor').innerHTML = `<div class="card"><div class="card-header"><span class="card-header-title">Estructura</span>
      <button class="btn btn-outline btn-sm" id="mas-bloque">+ Bloque</button></div><div class="card-body arbol" id="arbol"></div></div>
    <div class="card"><div class="card-body" id="props"></div></div>
    <div class="card ed-preview"><div class="card-header"><span class="card-header-title">Vista previa</span><button class="btn btn-outline btn-sm" id="reiniciar">Reiniciar</button></div>
      <div class="card-body" id="previa"></div></div>`;
  $('#arbol').innerHTML = `<button class="item ${S.sel?.tipo === 'cuestionario' ? 'activo' : ''}" data-sel="cuestionario">📄 ${esc(d.nombre)}</button>` +
    d.bloques.map((/** @type {any} */ b, bi) => `<div class="bloque"><div class="b-titulo"><span>Bloque ${esc(b.id)}${b.aleatorizar ? ' 🔀' : ''}</span>
      <span><button class="btn btn-outline btn-sm" data-bloque="${bi}">⚙</button> <button class="btn btn-outline btn-sm" data-mas-pantalla="${bi}">+ Pantalla</button></span></div>
      ${b.pantallas.map((/** @type {any} */ p) => `<button class="item ${S.sel?.pantalla === p.id && !S.sel?.pregunta ? 'activo' : ''}" data-pantalla="${esc(p.id)}">▸ ${esc(p.id)}${p.mostrar_si ? ' ⓘ' : ''}</button>
        ${p.preguntas.map((/** @type {any} */ q) => `<button class="item preg ${S.sel?.pregunta === q.id ? 'activo' : ''}" data-pantalla="${esc(p.id)}" data-pregunta="${esc(q.id)}">${esc(q.id)} · ${esc(q.tipo)}</button>`).join('')}`).join('')}</div>`).join('');
  $('[data-sel=cuestionario]').onclick = () => { S.sel = { tipo: 'cuestionario' }; pintarEditor(); };
  $$('[data-pantalla]').forEach((b) => { b.onclick = () => { S.sel = { tipo: b.dataset.pregunta ? 'pregunta' : 'pantalla', pantalla: b.dataset.pantalla, pregunta: b.dataset.pregunta }; pintarEditor(); }; });
  $$('[data-bloque]').forEach((b) => { b.onclick = () => { S.sel = { tipo: 'bloque', bloque: Number(b.dataset.bloque) }; pintarEditor(); }; });
  $$('[data-mas-pantalla]').forEach((b) => {
    b.onclick = () => {
      const id = idLibre('S');
      S.def.bloques[Number(b.dataset.masPantalla)].pantallas.push({ id, preguntas: [] });
      S.sel = { tipo: 'pantalla', pantalla: id };
      pintarEditor();
    };
  });
  $('#mas-bloque').onclick = () => { S.def.bloques.push({ id: idLibre('B'), pantallas: [{ id: idLibre('S'), preguntas: [] }] }); pintarEditor(); };
  $('#reiniciar').onclick = () => { S.previa = { respuestas: {}, pantalla: null }; pintarPrevia(); };
  pintarPropiedades();
  pintarPrevia();
}

/** @param {string} prefijo */
function idLibre(prefijo) {
  const usados = new Set(JSON.stringify(S.def).match(/"id":"[^"]+"/g)?.map((x) => x.slice(6, -1)) ?? []);
  for (let i = 1; ; i++) if (!usados.has(`${prefijo}${i}`)) return `${prefijo}${i}`;
}

// ───────────────────────────── propiedades ─────────────────────────────

/** @param {string} k @param {string} etq @param {any} v @param {string} [tipo] */
const inp = (k, etq, v, tipo = 'text') => `<div class="form-group"><label>${etq}</label>${tipo === 'area'
  ? `<textarea class="finput" data-k="${k}">${esc(v ?? '')}</textarea>` : `<input type="${tipo}" data-k="${k}" value="${esc(v ?? '')}" />`}</div>`;
/** @param {string} k @param {string} etq @param {boolean} v */
const chk = (k, etq, v) => `<label class="check-inline mb"><input type="checkbox" data-k="${k}" ${v ? 'checked' : ''} /> ${etq}</label> `;

/** Constructor de condición: «VAR operador valor», o JSON libre para lo complejo. @param {string} k @param {any} expr */
function condicion(k, expr) {
  const simple = expr && expr.op && ['==', '!=', '>', '<', '>=', '<=', 'contiene'].includes(expr.op) && expr.args?.[0]?.var !== undefined && typeof expr.args?.[1] !== 'object';
  return `<div class="form-group"><label>${k === 'mostrar_si' ? 'Mostrar solo si' : 'Condición'}</label>
    <div class="row" style="flex-wrap:nowrap"><input type="text" data-c="var" placeholder="VARIABLE" value="${esc(simple ? expr.args[0].var : '')}" style="width:120px" />
      <select class="fselect" data-c="op" style="width:110px">${['', '==', '!=', '>', '<', '>=', '<=', 'contiene'].map((o) => `<option ${simple && o === expr.op ? 'selected' : ''}>${o}</option>`).join('')}</select>
      <input type="text" data-c="val" placeholder="valor" value="${esc(simple ? expr.args[1] : '')}" /></div>
    <textarea class="finput json-area mt" data-k="${k}" style="min-height:70px !important" placeholder='{"op": "and", "args": [...]}'>${expr && !simple ? esc(JSON.stringify(expr)) : ''}</textarea>
    <div class="field-hint">Para condiciones compuestas escribí el AST en JSON (operadores: ==, !=, &lt;, &gt;, and, or, not, in, contiene, vacio, cantidad, si…). Usá {"ficha": "prop"} para la ficha del contacto.</div></div>`;
}

/** @param {HTMLElement} caja @param {any} obj @param {string} k */
function leerCondicion(caja, obj, k) {
  const v = $('[data-c=var]', caja)?.value.trim(), op = $('[data-c=op]', caja)?.value, val = $('[data-c=val]', caja)?.value;
  const libre = $(`[data-k=${k}]`, caja)?.value.trim();
  if (libre) { obj[k] = JSON.parse(libre); return; }
  if (v && op) obj[k] = { op, args: [{ var: v }, val] };
  else delete obj[k];
}

function pintarPropiedades() {
  const c = $('#props');
  const s = S.sel;
  if (!s) { c.innerHTML = '<div class="empty-state">Elegí un elemento de la estructura.</div>'; return; }
  if (s.tipo === 'cuestionario') {
    c.innerHTML = `<div class="card-header-title mb">Cuestionario</div>${inp('id', 'Identificador', S.def.id)}${inp('nombre', 'Nombre', S.def.nombre)}
      ${inp('presentacion.texto', 'Presentación (speech inicial)', S.def.presentacion?.texto, 'area')}${inp('presentacion.ayuda', 'Ayuda de la presentación', S.def.presentacion?.ayuda, 'area')}
      <div class="field-hint">Sustitución: {ficha.propiedad}, {VARIABLE}, {VARIABLE.texto}.</div><button class="btn btn-dark btn-sm mt" id="aplicar">Aplicar</button>`;
    $('#aplicar').onclick = () => {
      S.def.id = $('[data-k=id]', c).value.trim(); S.def.nombre = $('[data-k=nombre]', c).value.trim();
      S.def.presentacion = { texto: $('[data-k="presentacion.texto"]', c).value, ayuda: $('[data-k="presentacion.ayuda"]', c).value || undefined };
      pintarEditor();
    };
    return;
  }
  if (s.tipo === 'bloque') {
    const b = S.def.bloques[s.bloque];
    c.innerHTML = `<div class="card-header-title mb">Bloque</div>${inp('id', 'Identificador', b.id)}${chk('aleatorizar', 'Aleatorizar el orden de sus pantallas', b.aleatorizar)}
      ${condicion('mostrar_si', b.mostrar_si)}<div class="row"><button class="btn btn-dark btn-sm" id="aplicar">Aplicar</button><button class="btn btn-outline btn-sm btn-del" id="borrar">Borrar bloque</button></div>`;
    $('#aplicar').onclick = () => intentar(() => { b.id = $('[data-k=id]', c).value.trim(); b.aleatorizar = $('[data-k=aleatorizar]', c).checked || undefined; leerCondicion(c, b, 'mostrar_si'); });
    $('#borrar').onclick = () => { if (S.def.bloques.length > 1) { S.def.bloques.splice(s.bloque, 1); S.sel = null; pintarEditor(); } };
    return;
  }
  const loc = pantallaPorId(s.pantalla);
  if (!loc) return;
  const p = loc.p;
  if (s.tipo === 'pantalla') {
    c.innerHTML = `<div class="card-header-title mb">Pantalla</div>${inp('id', 'Identificador', p.id)}${inp('texto', 'Speech', p.texto, 'area')}${inp('ayuda', 'Ayuda', p.ayuda, 'area')}
      ${condicion('mostrar_si', p.mostrar_si)}
      <div class="form-group"><label>Flujo (reglas en orden; la primera que aplica gana)</label><textarea class="finput json-area" data-k="siguiente">${esc(JSON.stringify(p.siguiente ?? [], null, 1))}</textarea>
        <div class="field-hint">[{"si": {...}, "ir_a": "S5"}, {"si": {...}, "fin": 20}]. Sin regla: pantalla siguiente.</div></div>
      <div class="form-group"><label>Validaciones de pantalla</label><textarea class="finput json-area" data-k="validaciones">${esc(JSON.stringify(p.validaciones ?? [], null, 1))}</textarea></div>
      <div class="row"><button class="btn btn-dark btn-sm" id="aplicar">Aplicar</button><button class="btn btn-outline btn-sm" id="mas-preg">+ Pregunta</button>
        <button class="btn btn-outline btn-sm btn-del" id="borrar">Borrar pantalla</button></div>`;
    $('#aplicar').onclick = () => intentar(() => {
      const viejo = p.id;
      p.id = $('[data-k=id]', c).value.trim(); p.texto = $('[data-k=texto]', c).value || undefined; p.ayuda = $('[data-k=ayuda]', c).value || undefined;
      leerCondicion(c, p, 'mostrar_si');
      const sig = JSON.parse($('[data-k=siguiente]', c).value || '[]'); if (sig.length) p.siguiente = sig; else delete p.siguiente;
      const val = JSON.parse($('[data-k=validaciones]', c).value || '[]'); if (val.length) p.validaciones = val; else delete p.validaciones;
      if (viejo !== p.id) S.sel.pantalla = p.id;
    });
    $('#mas-preg').onclick = () => { const id = idLibre('P'); p.preguntas.push({ id, tipo: 'simple', texto: '', opciones: [{ codigo: '1', texto: 'Opción 1' }] }); S.sel = { tipo: 'pregunta', pantalla: p.id, pregunta: id }; pintarEditor(); };
    $('#borrar').onclick = () => { if (loc.b.pantallas.length > 1) { loc.b.pantallas.splice(loc.b.pantallas.indexOf(p), 1); S.sel = null; pintarEditor(); } };
    return;
  }
  const q = p.preguntas.find((/** @type {any} */ x) => x.id === s.pregunta);
  if (!q) return;
  const conOpciones = ['simple', 'multiple', 'grilla'].includes(q.tipo);
  c.innerHTML = `<div class="card-header-title mb">Pregunta</div><div class="form-row">${inp('id', 'Variable', q.id)}
      <div class="form-group"><label>Tipo</label><select class="fselect" data-k="tipo">${['simple', 'multiple', 'grilla', 'texto', 'numero', 'fecha', 'hora', 'codigo', 'formula']
        .map((t) => `<option ${t === q.tipo ? 'selected' : ''}>${t}</option>`).join('')}</select></div></div>
    ${inp('texto', 'Texto', q.texto, 'area')}${chk('obligatoria', 'Obligatoria', q.obligatoria !== false)}
    ${conOpciones ? `${chk('aleatorizar_opciones', 'Aleatorizar opciones', q.aleatorizar_opciones)}<div class="form-group"><label>${q.tipo === 'grilla' ? 'Columnas' : 'Opciones'} (código · texto · otro · exclusiva · fija)</label><div id="ops">
      ${(q.opciones ?? []).map((/** @type {any} */ o) => filaOpcion(o)).join('')}</div><button class="btn btn-outline btn-sm" id="mas-op">+ Opción</button></div>` : ''}
    ${q.tipo === 'multiple' ? `<div class="form-row">${inp('min_respuestas', 'Mínimo', q.min_respuestas, 'number')}${inp('max_respuestas', 'Máximo', q.max_respuestas, 'number')}</div>` : ''}
    ${q.tipo === 'grilla' ? `${chk('aleatorizar_filas', 'Aleatorizar filas', q.aleatorizar_filas)}<div class="form-group"><label>Filas (código: texto, una por línea)</label>
      <textarea class="finput" data-k="filas">${esc((q.filas ?? []).map((/** @type {any} */ f) => `${f.codigo}: ${f.texto}`).join('\n'))}</textarea></div>` : ''}
    ${['numero', 'codigo'].includes(q.tipo) ? `<div class="form-row">${inp('minimo', 'Mínimo', q.minimo, 'number')}${inp('maximo', 'Máximo', q.maximo, 'number')}</div>${inp('decimales', 'Decimales', q.decimales ?? 0, 'number')}` : ''}
    ${q.tipo === 'texto' ? `<div class="form-row">${inp('largo_maximo', 'Largo máximo', q.largo_maximo, 'number')}${inp('patron', 'Patrón (regex)', q.patron)}</div>` : ''}
    ${q.tipo === 'formula' ? `<div class="form-group"><label>Expresión</label><textarea class="finput json-area" data-k="expresion">${esc(JSON.stringify(q.expresion ?? null))}</textarea></div>` : ''}
    ${inp('error', 'Mensaje de error propio', q.error)}${condicion('mostrar_si', q.mostrar_si)}
    <div class="row"><button class="btn btn-dark btn-sm" id="aplicar">Aplicar</button><button class="btn btn-outline btn-sm btn-del" id="borrar">Borrar pregunta</button></div>`;
  $('#mas-op') && ($('#mas-op').onclick = () => $('#ops').insertAdjacentHTML('beforeend', filaOpcion({ codigo: String((q.opciones?.length ?? 0) + 1), texto: '' })));
  $('[data-k=tipo]', c).onchange = () => { q.tipo = $('[data-k=tipo]', c).value; if (['simple', 'multiple', 'grilla'].includes(q.tipo) && !q.opciones) q.opciones = [{ codigo: '1', texto: 'Opción 1' }]; if (q.tipo === 'grilla' && !q.filas) q.filas = [{ codigo: 'a', texto: 'Fila a' }]; if (q.tipo === 'formula' && !q.expresion) q.expresion = 0; pintarEditor(); };
  $('#borrar').onclick = () => { p.preguntas.splice(p.preguntas.indexOf(q), 1); S.sel = { tipo: 'pantalla', pantalla: p.id }; pintarEditor(); };
  $('#aplicar').onclick = () => intentar(() => {
    /** @param {string} k */ const val = (k) => $(`[data-k="${k}"]`, c)?.value;
    /** @param {string} k */ const num = (k) => { const v = val(k); if (v === undefined) return; if (v === '' || v === null) delete q[k]; else q[k] = Number(v); };
    /** @param {string} k */ const bool = (k) => { const e = $(`[data-k="${k}"]`, c); if (e) { if (e.checked) q[k] = true; else delete q[k]; } };
    q.id = val('id').trim(); q.texto = val('texto');
    if (!$('[data-k=obligatoria]', c).checked) q.obligatoria = false; else delete q.obligatoria;
    bool('aleatorizar_opciones'); bool('aleatorizar_filas');
    ['min_respuestas', 'max_respuestas', 'minimo', 'maximo', 'decimales', 'largo_maximo'].forEach(num);
    if (val('patron') !== undefined) { if (val('patron')) q.patron = val('patron'); else delete q.patron; }
    if (val('error')) q.error = val('error'); else delete q.error;
    if (val('expresion') !== undefined) q.expresion = JSON.parse(val('expresion'));
    if ($('#ops')) q.opciones = $$('.op-fila', c).map((f) => {
      /** @type {any} */ const o = { codigo: $('[data-o=codigo]', f).value.trim(), texto: $('[data-o=texto]', f).value };
      for (const k of ['otro', 'exclusiva', 'fija']) if ($(`[data-o=${k}]`, f).checked) o[k] = true;
      return o;
    }).filter((/** @type {any} */ o) => o.codigo);
    if (val('filas') !== undefined) q.filas = val('filas').split('\n').map((/** @type {string} */ l) => l.trim()).filter(Boolean)
      .map((/** @type {string} */ l) => { const [cod, ...t] = l.split(':'); return { codigo: cod.trim(), texto: t.join(':').trim() || cod.trim() }; });
    leerCondicion(c, q, 'mostrar_si');
    S.sel.pregunta = q.id;
  });
}

/** @param {any} o */
function filaOpcion(o) {
  return `<div class="op-fila"><input type="text" data-o="codigo" value="${esc(o.codigo)}" /><input type="text" data-o="texto" value="${esc(o.texto)}" />
    <span class="row" style="gap:.3rem;flex-wrap:nowrap">${['otro', 'exclusiva', 'fija'].map((k) => `<label class="check-inline" title="${k}"><input type="checkbox" data-o="${k}" ${o[k] ? 'checked' : ''}/>${k[0]}</label>`).join('')}</span>
    <button class="btn btn-outline btn-sm btn-del" onclick="this.parentElement.remove()">×</button></div>`;
}

/** @param {() => void} f */
function intentar(f) {
  try { f(); pintarEditor(); validarLocal(); }
  catch (e) { toast(`No se aplicó: ${/** @type {any} */ (e).message}`, 'err'); }
}

function validarLocal() {
  const errores = [];
  const vars = new Set();
  const pantallas = new Set();
  for (const b of S.def.bloques) for (const p of b.pantallas) { pantallas.add(p.id); for (const q of p.preguntas) { if (vars.has(q.id)) errores.push(`Variable repetida: ${q.id}`); vars.add(q.id); } }
  for (const b of S.def.bloques) for (const p of b.pantallas) {
    for (const r of p.siguiente ?? []) if (r.ir_a && !pantallas.has(r.ir_a)) errores.push(`${p.id}: salta a ${r.ir_a}, que no existe`);
    for (const q of p.preguntas) for (const v of variables(q.mostrar_si)) if (!vars.has(v) && !v.includes('.')) errores.push(`${q.id}: la condición usa ${v}, que no existe`);
  }
  $('#errores').innerHTML = errores.length ? `<div class="alert alert-warn">${errores.map(esc).join('<br>')}</div>` : '';
}

// ───────────────────────────── vista previa ─────────────────────────────

function pintarPrevia() {
  const caja = $('#previa');
  let m;
  try { m = new Motor(S.def, { nombre: 'Ana Pérez', saludo: 'tardes', empresa: 'Cliente' }, 1); }
  catch (e) { caja.innerHTML = `<div class="alert alert-error">${esc(/** @type {any} */ (e).message)}</div>`; return; }
  const pr = S.previa;
  if (!pr.pantalla) { const r = m.primera(pr.respuestas); pr.pantalla = r.pantalla; pr.fin = r.fin; }
  if (!pr.pantalla) { caja.innerHTML = `<div class="alert alert-success">Fin con disposición ${pr.fin}.</div>`; return; }
  const p = m.pantallas[pr.pantalla];
  caja.innerHTML = `<div class="small muted mb">${esc(pr.pantalla)}</div>${p.texto ? `<div class="speech">${esc(m.texto(p.texto, pr.respuestas))}</div>` : ''}
    ${m.preguntas(pr.pantalla, pr.respuestas).filter((/** @type {any} */ q) => q.tipo !== 'formula').map((/** @type {any} */ q) => `<div class="pregunta"><div class="q-id">${esc(q.id)}</div>
      <div class="q-texto">${esc(m.texto(q.texto, pr.respuestas))}</div>
      ${['simple', 'multiple'].includes(q.tipo) ? `<div class="opciones">${m.opciones(q, pr.respuestas).map((/** @type {any} */ o) => `<label class="opcion"><input type="${q.tipo === 'simple' ? 'radio' : 'checkbox'}" name="pv-${esc(q.id)}" value="${esc(o.codigo)}" />${esc(o.texto)}</label>`).join('')}</div>`
        : q.tipo === 'grilla' ? m.filas(q, pr.respuestas).map((/** @type {any} */ f) => `<div class="small mt"><b>${esc(f.texto)}</b> <select class="fselect" name="pv-${esc(q.id)}.${esc(f.codigo)}"><option value=""></option>${m.opciones(q, pr.respuestas).map((/** @type {any} */ o) => `<option value="${esc(o.codigo)}">${esc(o.texto)}</option>`).join('')}</select></div>`).join('')
        : `<input type="text" name="pv-${esc(q.id)}" />`}<div class="q-error" data-pv-err="${esc(q.id)}"></div></div>`).join('')}
    <div id="pv-err"></div><button class="btn btn-orange btn-sm mt" id="pv-sig">Siguiente</button>`;
  $('#pv-sig').onclick = () => {
    for (const q of m.preguntas(pr.pantalla, pr.respuestas)) {
      if (q.tipo === 'simple') pr.respuestas[q.id] = $(`[name="pv-${CSS.escape(q.id)}"]:checked`, caja)?.value ?? null;
      else if (q.tipo === 'multiple') pr.respuestas[q.id] = $$(`[name="pv-${CSS.escape(q.id)}"]:checked`, caja).map((x) => x.value);
      else if (q.tipo === 'grilla') for (const f of m.filas(q, pr.respuestas)) pr.respuestas[`${q.id}.${f.codigo}`] = $(`[name="pv-${CSS.escape(q.id + '.' + f.codigo)}"]`, caja).value || null;
      else if (q.tipo !== 'formula') pr.respuestas[q.id] = $(`[name="pv-${CSS.escape(q.id)}"]`, caja).value || null;
    }
    pr.respuestas = m.calcularFormulas(pr.pantalla, pr.respuestas);
    const err = m.validarPantalla(pr.pantalla, pr.respuestas);
    if (Object.keys(err).length) {
      for (const [k, v] of Object.entries(err)) { const e = k === '*' ? $('#pv-err', caja) : $(`[data-pv-err="${CSS.escape(k)}"]`, caja); if (e) e.textContent = v; }
      return;
    }
    const r = m.siguiente(pr.pantalla, pr.respuestas);
    pr.pantalla = r.pantalla ?? null; pr.fin = r.fin;
    pintarPrevia();
  };
}

// ───────────────────────────── guardar y publicar ─────────────────────────────

async function guardar() {
  try { const r = await api('PUT', `/api/estudios/${S.estudio}/cuestionario`, S.def); $('#version').textContent = `v${r.version} · borrador guardado`; toast('Borrador guardado.', 'ok'); return r; }
  catch (e) { $('#errores').innerHTML = `<div class="alert alert-error">${esc(/** @type {any} */ (e).message)}</div>`; return null; }
}

async function publicar() {
  const ok = await modal({ titulo: 'Publicar versión', cuerpo: '<p>Una versión publicada no se modifica: las entrevistas quedan atadas a ella. Los cambios posteriores abren una versión nueva.</p>',
    pie: '<button class="btn btn-orange" data-ok>Publicar</button>' }, (caja, cerrar) => { $('[data-ok]', caja).onclick = () => cerrar(true); });
  if (!ok) return;
  const r = await guardar();
  if (!r) return;
  try { await api('POST', `/api/cuestionarios/${r.id}/publicar`); toast(`Versión ${r.version} publicada.`, 'ok'); await cargar(S.estudio); }
  catch (e) { toast(/** @type {any} */ (e).message, 'err'); }
}

async function verJson() {
  await modal({ titulo: 'JSON del cuestionario', ancho: 760, cuerpo: `<textarea class="finput json-area" id="j" style="min-height:420px !important">${esc(JSON.stringify(S.def, null, 2))}</textarea>`,
    pie: '<button class="btn btn-dark" data-ok>Aplicar cambios</button>' }, (caja, cerrar) => {
    $('[data-ok]', caja).onclick = () => { try { S.def = JSON.parse($('#j', caja).value); cerrar(true); pintarEditor(); } catch { toast('JSON inválido.', 'err'); } };
  });
}

void descargar;
