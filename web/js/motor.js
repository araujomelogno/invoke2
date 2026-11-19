// @ts-check
/**
 * Motor de cuestionario del puesto — gemelo de invoke/dominio/cuestionario.py.
 * Misma aleatorización reproducible (FNV-1a + mulberry32), mismas validaciones,
 * mismo flujo. tests/compartidos/cuestionario.json corre contra los dos.
 */
import { evaluar, verdadero } from './expresiones.js';

export const COMPLETA = 3;
export const FUERA_DE_CUOTA = 6;

/** @param {string} texto */
export function fnv1a(texto) {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(texto)) {
    h ^= b;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** @param {number} semilla */
export function mulberry32(semilla) {
  let a = semilla >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/**
 * @template T
 * @param {T[]} items @param {number} semilla @param {string} clave
 * @param {(x: T) => boolean} [fijo]
 * @returns {T[]}
 */
export function barajar(items, semilla, clave, fijo = () => false) {
  const rnd = mulberry32(fnv1a(`${semilla}:${clave}`));
  const movibles = items.map((x, i) => (fijo(x) ? -1 : i)).filter((i) => i >= 0);
  const valores = movibles.map((i) => items[i]);
  for (let i = valores.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [valores[i], valores[j]] = [valores[j], valores[i]];
  }
  const res = items.slice();
  movibles.forEach((pos, k) => { res[pos] = valores[k]; });
  return res;
}

/** @param {unknown} v */
const vacio = (v) => v === null || v === undefined || v === '' || (Array.isArray(v) && v.length === 0);
const NUMERICO = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
/** @param {unknown} v */
const numero = (v) => {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string' && NUMERICO.test(v.trim())) { const n = Number(v.trim()); return Number.isFinite(n) ? n : null; }
  return null;
};
/** @param {number} n */
const normal = (n) => (Number.isInteger(n) ? String(n) : String(n));
const PIPE = /\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)?)\}/g;

/**
 * @typedef {{pantalla?: string|null, fin?: number|null}} Resultado
 * @typedef {Record<string, any>} Respuestas
 */

export class Motor {
  /** @param {any} definicion @param {Record<string, unknown>} ficha @param {number} semilla */
  constructor(definicion, ficha = {}, semilla = 0) {
    this.definicion = definicion;
    this.ficha = ficha;
    this.semilla = semilla;
    /** @type {Record<string, any>} */ this.pantallas = {};
    /** @type {Record<string, any>} */ this.bloqueDe = {};
    /** @type {string[]} */ this.secuencia = [];
    for (const b of definicion.bloques) {
      let ps = b.pantallas.slice();
      if (b.aleatorizar) ps = barajar(ps, semilla, b.id);
      for (const p of ps) {
        this.pantallas[p.id] = p;
        this.bloqueDe[p.id] = b;
        this.secuencia.push(p.id);
      }
    }
  }

  /** @param {any} nodo @param {Respuestas} r */
  visible(nodo, r) {
    return !('mostrar_si' in nodo) || verdadero(evaluar(nodo.mostrar_si, r, this.ficha));
  }

  /** @param {any} q @param {Respuestas} r @returns {any[]} */
  opciones(q, r) {
    let ops = (q.opciones ?? []).slice();
    if (q.aleatorizar_opciones) ops = barajar(ops, this.semilla, `${q.id}:opciones`, (/** @type {any} */ o) => !!o.fija);
    return ops.filter((/** @type {any} */ o) => this.visible(o, r));
  }

  /** @param {any} q @param {Respuestas} r @returns {any[]} */
  filas(q, r) {
    let fs = (q.filas ?? []).slice();
    if (q.aleatorizar_filas) fs = barajar(fs, this.semilla, `${q.id}:filas`, (/** @type {any} */ o) => !!o.fija);
    return fs.filter((/** @type {any} */ f) => this.visible(f, r));
  }

  /** @param {string} pid @param {Respuestas} r */
  pantallaVisible(pid, r) {
    return this.visible(this.bloqueDe[pid], r) && this.visible(this.pantallas[pid], r);
  }

  /** @param {string} pid @param {Respuestas} r @returns {any[]} */
  preguntas(pid, r) {
    return this.pantallas[pid].preguntas.filter((/** @type {any} */ q) => this.visible(q, r));
  }

  /** @param {Respuestas} [r] @returns {Resultado} */
  primera(r = {}) { return this.desdeIndice(0, r); }

  /** @param {number} i @param {Respuestas} r @returns {Resultado} */
  desdeIndice(i, r) {
    for (; i < this.secuencia.length; i++) {
      if (this.pantallaVisible(this.secuencia[i], r)) return { pantalla: this.secuencia[i] };
    }
    return { fin: COMPLETA };
  }

  /** @param {string|undefined} plantilla @param {Respuestas} r */
  texto(plantilla, r) {
    if (!plantilla) return '';
    return plantilla.replace(PIPE, (_, clave) => {
      if (clave.startsWith('ficha.')) { const v = this.ficha[clave.slice(6)]; return v == null ? '' : String(v); }
      const [v0, suf] = clave.split('.');
      if (suf === 'texto') return this.etiqueta(v0, r);
      const v = r[clave];
      if (Array.isArray(v)) return v.join(', ');
      return v == null ? '' : String(v);
    });
  }

  /** @param {string} variable @param {Respuestas} r */
  etiqueta(variable, r) {
    const v = r[variable];
    for (const p of Object.values(this.pantallas)) {
      for (const q of p.preguntas) {
        if (q.id === variable) {
          /** @type {Record<string,string>} */ const textos = {};
          for (const o of q.opciones ?? []) textos[o.codigo] = o.texto;
          const vals = Array.isArray(v) ? v : v == null ? [] : [v];
          return vals.map((x) => textos[String(x)] ?? String(x)).join(', ');
        }
      }
    }
    return '';
  }

  /** @param {string} pid @param {Respuestas} r */
  calcularFormulas(pid, r) {
    const res = { ...r };
    for (const q of this.preguntas(pid, res)) if (q.tipo === 'formula') res[q.id] = evaluar(q.expresion, res, this.ficha);
    return res;
  }

  /** @param {string} pid @param {Respuestas} r @returns {Record<string,string>} */
  validarPantalla(pid, r) {
    /** @type {Record<string,string>} */ const errores = {};
    for (const q of this.preguntas(pid, r)) {
      const e = this.validarPregunta(q, r);
      if (e) errores[q.id] = e;
    }
    if (!Object.keys(errores).length) {
      for (const v of this.pantallas[pid].validaciones ?? []) {
        if (verdadero(evaluar(v.si, r, this.ficha))) { errores['*'] = v.error; break; }
      }
    }
    return errores;
  }

  /** @param {any} q @param {Respuestas} r @returns {string|null} */
  validarPregunta(q, r) {
    const obligatoria = q.obligatoria ?? true;
    const msg = q.error;
    if (q.tipo === 'formula') return null;
    if (q.tipo === 'grilla') {
      const celdas = new Set(this.opciones(q, r).map((o) => o.codigo));
      for (const f of this.filas(q, r)) {
        const v = r[`${q.id}.${f.codigo}`];
        if (vacio(v)) { if (obligatoria) return msg || `Falta responder la fila «${f.texto}»`; continue; }
        if ((q.tipo_celda ?? 'simple') === 'simple' && !celdas.has(String(v))) return msg || `Respuesta inválida en «${f.texto}»`;
        if (q.tipo_celda === 'numero' && Motor.numeroInvalido(q, v)) return msg || `Valor fuera de rango en «${f.texto}»`;
      }
      return null;
    }
    const v = r[q.id];
    if (vacio(v)) return obligatoria ? msg || 'Respuesta obligatoria' : null;
    switch (q.tipo) {
      case 'simple': {
        const ops = Object.fromEntries(this.opciones(q, r).map((o) => [o.codigo, o]));
        if (Array.isArray(v) || !(String(v) in ops)) return msg || 'Opción inválida';
        if (ops[String(v)].otro && vacio(r[`${q.id}.otro`])) return 'Especificá la opción «otro»';
        return null;
      }
      case 'multiple': {
        const ops = Object.fromEntries(this.opciones(q, r).map((o) => [o.codigo, o]));
        if (!Array.isArray(v)) return msg || 'Se esperaba una lista de opciones';
        const vals = v.map(String);
        if (new Set(vals).size !== vals.length || vals.some((x) => !(x in ops))) return msg || 'Opción inválida';
        const minimo = q.min_respuestas ?? (obligatoria ? 1 : 0);
        const maximo = q.max_respuestas ?? Object.keys(ops).length;
        if (vals.length < minimo || vals.length > maximo) return msg || `Elegí entre ${minimo} y ${maximo} opciones`;
        if (vals.length > 1 && vals.some((x) => ops[x].exclusiva)) return 'Una opción exclusiva no se combina con otras';
        if (vals.some((x) => ops[x].otro) && vacio(r[`${q.id}.otro`])) return 'Especificá la opción «otro»';
        return null;
      }
      case 'texto':
        if (typeof v !== 'string') return msg || 'Se esperaba texto';
        if (q.largo_maximo && v.length > q.largo_maximo) return msg || `Máximo ${q.largo_maximo} caracteres`;
        if (q.patron && !new RegExp(`^(?:${q.patron})$`).test(v)) return msg || 'Formato inválido';
        return null;
      case 'numero':
      case 'codigo':
        if (q.tipo === 'codigo' && !/^-?\d+$/.test(String(v).trim())) return msg || 'Se esperaba un código entero';
        if (Motor.numeroInvalido(q, v)) return msg || Motor.rango(q);
        return null;
      case 'fecha': {
        if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return msg || 'Fecha inválida (AAAA-MM-DD)';
        const [a, m, d] = v.split('-').map(Number);
        const f = new Date(Date.UTC(a, m - 1, d));
        if (f.getUTCFullYear() !== a || f.getUTCMonth() !== m - 1 || f.getUTCDate() !== d || a < 1) return msg || 'Fecha inválida';
        return null;
      }
      case 'hora':
        if (typeof v !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d$/.test(v)) return msg || 'Hora inválida (HH:MM)';
        return null;
      default:
        return null;
    }
  }

  /** @param {any} q */
  static rango(q) {
    return 'minimo' in q && 'maximo' in q ? `Ingresá un valor entre ${normal(q.minimo)} y ${normal(q.maximo)}` : 'Valor numérico inválido';
  }

  /** @param {any} q @param {unknown} v */
  static numeroInvalido(q, v) {
    const n = numero(v);
    if (n === null) return true;
    if ('minimo' in q && n < q.minimo) return true;
    if ('maximo' in q && n > q.maximo) return true;
    const t = String(v).trim();
    if (t.includes('.') && t.split('.', 2)[1].replace(/0+$/, '').length > (q.decimales ?? 0)) return true;
    return false;
  }

  /** @param {string} pid @param {Respuestas} r @returns {Resultado} */
  siguiente(pid, r) {
    const p = this.pantallas[pid];
    for (const q of this.preguntas(pid, r)) {
      if (q.tipo === 'simple') {
        const o = (q.opciones ?? []).find((/** @type {any} */ o) => o.codigo === String(r[q.id]) && o.ir_a);
        if (o) return { pantalla: o.ir_a };
      }
    }
    for (const regla of p.siguiente ?? []) {
      if ('si' in regla && !verdadero(evaluar(regla.si, r, this.ficha))) continue;
      if ('fin' in regla) return { fin: Number(regla.fin) };
      if (this.pantallaVisible(regla.ir_a, r)) return { pantalla: regla.ir_a };
      return this.desdeIndice(this.secuencia.indexOf(regla.ir_a) + 1, r);
    }
    return this.desdeIndice(this.secuencia.indexOf(pid) + 1, r);
  }

  /** @param {Respuestas} respuestas */
  recorrer(respuestas) {
    let r = { ...respuestas };
    /** @type {string[]} */ const visitadas = [];
    /** @type {Record<string, Record<string,string>>} */ const errores = {};
    let paso = this.primera(r);
    while (paso.pantalla) {
      const pid = paso.pantalla;
      if (visitadas.includes(pid)) {
        errores[pid] = { '*': 'el flujo vuelve a una pantalla ya visitada (ciclo)' };
        return { visitadas, fin: null, errores };
      }
      visitadas.push(pid);
      r = this.calcularFormulas(pid, r);
      const e = this.validarPantalla(pid, r);
      if (Object.keys(e).length) { errores[pid] = e; return { visitadas, fin: null, errores }; }
      paso = this.siguiente(pid, r);
    }
    return { visitadas, fin: paso.fin ?? null, errores, respuestas: r };
  }
}
