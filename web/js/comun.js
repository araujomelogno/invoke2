// @ts-check
/**
 * Utilidades compartidas por todas las pantallas: sesión (Firebase Auth o modo
 * desarrollo), llamadas a la API, WebSocket de sala y piezas de interfaz con la
 * estética de Equipos (toast, modal, encabezados).
 */

const CFG = /** @type {any} */ (window).INVOKE_CONFIG || {};
const FB_VER = '10.12.2';

/** @param {string} sel @param {ParentNode} [ctx] @returns {any} */
export const $ = (sel, ctx = document) => ctx.querySelector(sel);
/** @param {string} sel @param {ParentNode} [ctx] @returns {any[]} */
export const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

/** @param {unknown} s */
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c);
}

/** @param {string} msg @param {''|'ok'|'err'} [tipo] */
export function toast(msg, tipo = '') {
  let w = document.getElementById('toast-wrap');
  if (!w) { w = document.createElement('div'); w.id = 'toast-wrap'; document.body.appendChild(w); }
  const t = document.createElement('div');
  t.className = 'toast ' + tipo;
  t.innerHTML = `<span>${tipo === 'ok' ? '✅' : tipo === 'err' ? '⚠️' : 'ℹ️'}</span><span>${esc(msg)}</span>`;
  w.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'all .25s'; setTimeout(() => t.remove(), 260); }, 4200);
}

/**
 * Modal con la estética del sistema. Devuelve una promesa que resuelve con el
 * valor que pase `cerrar(valor)`, o null si se cierra con la X.
 * @param {{titulo: string, cuerpo: string, pie?: string, ancho?: number, sinCerrar?: boolean}} o
 * @param {(caja: HTMLElement, cerrar: (v: any) => void) => void} [alAbrir]
 * @returns {Promise<any>}
 */
export function modal(o, alAbrir) {
  return new Promise((resolver) => {
    const fondo = document.createElement('div');
    fondo.className = 'modal-backdrop';
    fondo.innerHTML = `<div class="modal-box" style="${o.ancho ? `max-width:${o.ancho}px` : ''}">
      <div class="modal-head"><div class="modal-title">${esc(o.titulo)}</div>
      ${o.sinCerrar ? '' : '<button class="modal-close" aria-label="Cerrar">×</button>'}</div>
      <div class="modal-body">${o.cuerpo}</div>${o.pie ? `<div class="modal-foot">${o.pie}</div>` : ''}</div>`;
    document.body.appendChild(fondo);
    /** @param {any} v */
    const cerrar = (v) => { fondo.remove(); resolver(v); };
    $('.modal-close', fondo)?.addEventListener('click', () => cerrar(null));
    alAbrir?.(/** @type {HTMLElement} */ ($('.modal-box', fondo)), cerrar);
  });
}

/** @param {string} titulo @param {string} acento @param {string} sub */
export function encabezado(titulo, acento, sub) {
  return `<div class="page-header"><div class="accent-bar"></div><div class="header-row"><div>
    <h2>${esc(titulo)} <span class="accent">${esc(acento)}</span></h2><p>${esc(sub)}</p></div>
    <div id="ph-acciones" class="row"></div></div></div>`;
}

/** @param {string|Date|null|undefined} t */
export function hora(t) {
  if (!t) return '—';
  return new Date(t).toLocaleTimeString('es-UY', { hour: '2-digit', minute: '2-digit', timeZone: CFG.zonaHoraria });
}
/** @param {string|Date|null|undefined} t */
export function fechaHora(t) {
  if (!t) return '—';
  return new Date(t).toLocaleString('es-UY', { dateStyle: 'short', timeStyle: 'short', timeZone: CFG.zonaHoraria });
}
/** @param {number} s */
export function duracion(s) {
  s = Math.max(0, Math.floor(s));
  const m = Math.floor(s / 60), r = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`;
}

// ───────────────────────────── sesión ─────────────────────────────

/** @type {{auth: any, usuario: any, token: () => Promise<string|null>, devUid: string|null, yo?: any}} */
export const sesion = { auth: null, usuario: null, token: async () => null, devUid: null };

function devUid() {
  const q = new URLSearchParams(location.search).get('dev_uid');
  try { if (q) localStorage.setItem('invoke_dev_uid', q); } catch { /* sin storage */ }
  try { return q || localStorage.getItem('invoke_dev_uid'); } catch { return q; }
}

/**
 * Inicia Firebase Auth (o el modo de desarrollo). Resuelve con el usuario de
 * INVOKE (/api/yo) o null si no hay sesión.
 */
export async function iniciarSesion() {
  sesion.devUid = devUid();
  if (sesion.devUid) {
    sesion.yo = await api('GET', '/api/yo').catch(() => null);
    return sesion.yo;
  }
  const { initializeApp } = await import(`https://www.gstatic.com/firebasejs/${FB_VER}/firebase-app.js`);
  const fbAuth = await import(`https://www.gstatic.com/firebasejs/${FB_VER}/firebase-auth.js`);
  const app = initializeApp(CFG.firebase);
  sesion.auth = { ...fbAuth, instancia: fbAuth.getAuth(app) };
  sesion.token = async () => (sesion.usuario ? sesion.usuario.getIdToken() : null);
  return new Promise((resolver) => {
    fbAuth.onAuthStateChanged(sesion.auth.instancia, async (/** @type {any} */ u) => {
      sesion.usuario = u;
      sesion.yo = u ? await api('GET', '/api/yo').catch(() => null) : null;
      resolver(sesion.yo);
    });
  });
}

/** @param {string} email @param {string} clave */
export async function entrar(email, clave) {
  const { signInWithEmailAndPassword, instancia } = sesion.auth;
  const cred = await signInWithEmailAndPassword(instancia, email, clave);
  sesion.usuario = cred.user;
  sesion.yo = await api('GET', '/api/yo');
  return sesion.yo;
}

export async function salir() {
  try { localStorage.removeItem('invoke_dev_uid'); } catch { /* sin storage */ }
  if (sesion.auth) await sesion.auth.signOut(sesion.auth.instancia);
  location.href = '/';
}

/** Redirige a la pantalla del rol principal. @param {any} yo */
export function pantallaDe(yo) {
  if (yo.roles.includes('administrador') || yo.roles.includes('investigador')) return '/admin.html';
  if (yo.roles.includes('supervisor')) return '/supervision.html';
  return '/estacion.html';
}

/** Exige sesión con alguno de los roles; si no, vuelve al login. @param {string[]} roles */
export async function exigir(roles) {
  const yo = await iniciarSesion();
  if (!yo) { location.href = '/'; throw new Error('sin sesión'); }
  if (roles.length && !roles.some((r) => yo.roles.includes(r))) { location.href = pantallaDe(yo); throw new Error('rol'); }
  return yo;
}

// ───────────────────────────── API ─────────────────────────────

export class ErrorApi extends Error {
  /** @param {string} m @param {number} estado */
  constructor(m, estado) { super(m); this.estado = estado; }
}

/**
 * @param {string} metodo @param {string} ruta @param {any} [cuerpo]
 * @returns {Promise<any>}
 */
export async function api(metodo, ruta, cuerpo) {
  /** @type {Record<string,string>} */
  const cab = { 'Content-Type': 'application/json' };
  if (sesion.devUid) cab['X-Dev-Uid'] = sesion.devUid;
  else { const t = await sesion.token(); if (t) cab.Authorization = `Bearer ${t}`; }
  const r = await fetch(ruta, { method: metodo, headers: cab, body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo) });
  const tipo = r.headers.get('content-type') || '';
  if (!tipo.includes('application/json')) {
    if (!r.ok) throw new ErrorApi(`Error ${r.status}`, r.status);
    return r.blob();
  }
  const datos = await r.json();
  if (!r.ok) throw new ErrorApi(datos.error || `Error ${r.status}`, r.status);
  return datos;
}

/** Descarga un archivo de la API. @param {string} ruta @param {string} nombre */
export async function descargar(ruta, nombre) {
  const blob = await api('GET', ruta);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = nombre;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// ───────────────────────────── sala (WebSocket) ─────────────────────────────

/**
 * Conexión con reconexión automática al gateway de sala.
 * @param {{rol?: string, estudio?: number|null}} hola
 * @param {(m: any) => void} alMensaje
 * @param {(vivo: boolean) => void} [alEstado]
 */
export function conectarSala(hola, alMensaje, alEstado) {
  let ws = /** @type {WebSocket|null} */ (null);
  let cerrado = false;
  let espera = 1000;
  const url = CFG.salaWs || `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/sala`;
  const abrir = async () => {
    ws = new WebSocket(url);
    ws.onopen = async () => {
      espera = 1000;
      const token = sesion.devUid ? null : await sesion.token();
      ws?.send(JSON.stringify({ ...hola, token, dev_uid: sesion.devUid }));
      alEstado?.(true);
    };
    ws.onmessage = (e) => { try { alMensaje(JSON.parse(e.data)); } catch { /* mensaje inválido */ } };
    ws.onclose = () => {
      alEstado?.(false);
      if (!cerrado) setTimeout(abrir, espera = Math.min(espera * 2, 15000));
    };
  };
  abrir();
  const ping = setInterval(() => ws?.readyState === 1 && ws.send('{"tipo":"ping"}'), 25000);
  return {
    /** @param {any} m */
    enviar: (m) => ws?.readyState === 1 && ws.send(JSON.stringify(m)),
    cerrar: () => { cerrado = true; clearInterval(ping); ws?.close(); },
  };
}

/** Barra lateral oscura con logo, secciones y usuario. */
/** @param {any} yo @param {{id: string, icono: string, texto: string, seccion?: string}[]} items @param {string} activo */
export function barraLateral(yo, items, activo) {
  let seccion = '';
  const nav = items.map((i) => {
    let h = '';
    if (i.seccion && i.seccion !== seccion) { seccion = i.seccion; h += `<div class="sidebar-section-label">${esc(seccion)}</div>`; }
    return h + `<button class="nav-item ${i.id === activo ? 'active' : ''}" data-nav="${esc(i.id)}"><span class="nav-icon">${i.icono}</span>${esc(i.texto)}</button>`;
  }).join('');
  return `<aside class="sidebar"><div class="sidebar-top"><img class="logo-horizontal-white" src="/img/logo-horizontal-blanco.png" alt="Equipos Consultores"></div>
    ${nav}
    <div class="sidebar-footer"><div class="user-pill"><div class="user-avatar">${esc((yo.nombre || '?')[0])}</div>
    <div class="user-info-wrap"><div class="user-email">${esc(yo.nombre)}</div><div class="user-role">${esc(yo.roles.join(' · '))}</div></div></div>
    <div class="row">${otrasPantallas(yo)}</div>
    <button class="btn btn-outline btn-full btn-sm mt" id="salir" style="border-color:rgba(255,255,255,0.2); color:rgba(255,255,255,0.85);">Cerrar sesión</button></div></aside>`;
}

/** @param {any} yo */
function otrasPantallas(yo) {
  const l = [];
  if (yo.roles.includes('administrador') || yo.roles.includes('investigador')) l.push(['/admin.html', 'Admin']);
  if (yo.roles.some((/** @type {string} */ r) => r === 'supervisor' || r === 'administrador')) l.push(['/supervision.html', 'Sala']);
  if (yo.roles.includes('administrador') || yo.roles.includes('investigador')) l.push(['/editor.html', 'Editor']);
  if (yo.roles.includes('encuestador') || yo.roles.includes('supervisor')) l.push(['/estacion.html', 'Puesto']);
  return l.filter(([u]) => u !== location.pathname).map(([u, t]) => `<a class="badge badge-user" style="text-decoration:none" href="${u}">${t}</a>`).join('');
}
