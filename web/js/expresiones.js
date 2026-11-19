// @ts-check
/**
 * Evaluador del lenguaje de expresiones del cuestionario — gemelo de
 * invoke/dominio/expresiones.py. La semántica tiene que ser idéntica: la batería
 * tests/compartidos/expresiones.json corre contra las dos implementaciones.
 * Ver la gramática en el docstring del módulo Python.
 */

const MAX_NODOS = 500;
const NUMERICO = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

export class ErrorExpresion extends Error {}

/** @param {unknown} v @returns {number|null} */
function numero(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  if (typeof v === 'string') {
    const t = v.trim();
    if (!NUMERICO.test(t)) return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** @param {unknown} a @param {unknown} b @returns {boolean} */
function igual(a, b) {
  const na = numero(a), nb = numero(b);
  if (na !== null && nb !== null) return na === nb;
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => igual(x, b[i]));
  }
  if (typeof a === 'boolean' || typeof b === 'boolean') return a === b;
  if (a === undefined) a = null;
  if (b === undefined) b = null;
  return a === b;
}

/** @param {unknown} v */
export function verdadero(v) {
  if (v === null || v === undefined || v === false) return false;
  if (typeof v === 'number') return v !== 0;
  if (typeof v === 'string' || Array.isArray(v)) return v.length > 0;
  return Boolean(v);
}

/** @param {number|null} n */
function normalizar(n) {
  return n === null || !Number.isFinite(n) ? null : n;
}

/** @param {number} x @param {number} d */
function redondear(x, d) {
  const f = 10 ** d;
  return Math.sign(x) * Math.floor(Math.abs(x) * f + 0.5) / f || 0;
}

/** @param {unknown} v @returns {string} */
function texto(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (Array.isArray(v)) return v.map(texto).join(',');
  return String(v);
}

/**
 * @param {unknown} expr
 * @param {Record<string, unknown>} [respuestas]
 * @param {Record<string, unknown>} [ficha]
 * @returns {unknown}
 */
export function evaluar(expr, respuestas = {}, ficha = {}) {
  let contador = 0;

  /** @param {unknown[]} args @param {number} n @param {string} op */
  const aridad = (args, n, op) => {
    if (args.length !== n) throw new ErrorExpresion(`${op} espera ${n} argumentos`);
  };

  /** @param {any} e @returns {unknown} */
  const ev = (e) => {
    contador += 1;
    if (contador > MAX_NODOS) throw new ErrorExpresion('expresión demasiado grande');
    if (e === null || e === undefined) return null;
    if (typeof e === 'boolean' || typeof e === 'number' || typeof e === 'string') return e;
    if (Array.isArray(e)) throw new ErrorExpresion('las listas solo van como argumentos de un operador');
    if (typeof e !== 'object') throw new ErrorExpresion('nodo inválido');
    if ('var' in e) return respuestas[e.var] ?? null;
    if ('ficha' in e) return ficha[e.ficha] ?? null;
    const op = e.op;
    const args = e.args ?? [];
    if (!Array.isArray(args)) throw new ErrorExpresion('args debe ser una lista');
    switch (op) {
      case '==': aridad(args, 2, op); return igual(ev(args[0]), ev(args[1]));
      case '!=': aridad(args, 2, op); return !igual(ev(args[0]), ev(args[1]));
      case '<': case '<=': case '>': case '>=': {
        aridad(args, 2, op);
        const a = numero(ev(args[0])), b = numero(ev(args[1]));
        if (a === null || b === null) return false;
        return op === '<' ? a < b : op === '<=' ? a <= b : op === '>' ? a > b : a >= b;
      }
      case 'and': return args.every((a) => verdadero(ev(a)));
      case 'or': return args.some((a) => verdadero(ev(a)));
      case 'not': aridad(args, 1, op); return !verdadero(ev(args[0]));
      case '+': case '-': case '*': case '/': {
        aridad(args, 2, op);
        const a = numero(ev(args[0])), b = numero(ev(args[1]));
        if (a === null || b === null) return null;
        if (op === '/') return b === 0 ? null : normalizar(a / b);
        return normalizar(op === '+' ? a + b : op === '-' ? a - b : a * b);
      }
      case 'in': {
        if (args.length < 2) throw new ErrorExpresion('in espera al menos 2 argumentos');
        const v = ev(args[0]);
        return args.slice(1).some((x) => igual(v, ev(x)));
      }
      case 'contiene': {
        aridad(args, 2, op);
        const lista = ev(args[0]), v = ev(args[1]);
        if (!Array.isArray(lista)) return lista !== null && igual(lista, v);
        return lista.some((x) => igual(x, v));
      }
      case 'vacio': {
        aridad(args, 1, op);
        const v = ev(args[0]);
        return v === null || v === '' || (Array.isArray(v) && v.length === 0);
      }
      case 'cantidad': {
        aridad(args, 1, op);
        const v = ev(args[0]);
        if (v === null) return 0;
        return Array.isArray(v) ? v.length : 1;
      }
      case 'si': aridad(args, 3, op); return verdadero(ev(args[0])) ? ev(args[1]) : ev(args[2]);
      case 'min': case 'max': {
        const nums = args.map((a) => numero(ev(a))).filter((n) => n !== null);
        if (!nums.length) return null;
        return normalizar(op === 'min' ? Math.min(...nums) : Math.max(...nums));
      }
      case 'redondear': {
        if (args.length < 1 || args.length > 2) throw new ErrorExpresion('redondear espera 1 o 2 argumentos');
        const x = numero(ev(args[0]));
        const d = args.length === 2 ? numero(ev(args[1])) : 0;
        if (x === null || d === null) return null;
        return normalizar(redondear(x, Math.trunc(d)));
      }
      case 'concat': return args.map((a) => texto(ev(a))).join('');
      default: throw new ErrorExpresion(`operador desconocido: ${JSON.stringify(op)}`);
    }
  };

  return ev(expr);
}

/** @param {unknown} expr @returns {Set<string>} */
export function variables(expr) {
  const s = new Set();
  /** @param {any} e */
  const rec = (e) => {
    if (e && typeof e === 'object' && !Array.isArray(e)) {
      if ('var' in e) s.add(e.var);
      for (const a of e.args ?? []) rec(a);
    }
  };
  rec(expr);
  return s;
}
