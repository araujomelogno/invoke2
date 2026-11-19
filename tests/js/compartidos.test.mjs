// Corre las baterías compartidas contra la implementación JS del navegador.
// La misma batería corre contra Python en tests/test_compartidos.py.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { evaluar } from '../../web/js/expresiones.js';
import { Motor, barajar } from '../../web/js/motor.js';

const raiz = new URL('../../', import.meta.url);
const leer = (p) => JSON.parse(readFileSync(new URL(p, raiz), 'utf8'));

const expr = leer('tests/compartidos/expresiones.json');
for (const [i, c] of expr.casos.entries()) {
  test(`expresión #${i} ${c.nota ?? JSON.stringify(c.expr)}`, () => {
    const { respuestas, ficha } = expr.contexto;
    if (c.error) {
      assert.throws(() => evaluar(c.expr, respuestas, ficha));
    } else {
      assert.deepEqual(evaluar(c.expr, respuestas, ficha), c.esperado);
    }
  });
}

const cq = leer('tests/compartidos/cuestionario.json');
const def = leer(cq.definicion);
for (const c of cq.casos) {
  test(`motor: ${c.nombre}`, () => {
    const m = new Motor(def, cq.ficha, c.semilla);
    const res = m.recorrer(c.respuestas);
    delete res.respuestas;
    assert.deepEqual(res.visitadas, c.esperado.visitadas);
    assert.deepEqual(res.fin, c.esperado.fin);
    assert.deepEqual(res.errores, c.esperado.errores);
    assert.deepEqual(m.secuencia, c.esperado.secuencia);
    assert.deepEqual(m.opciones(def.bloques[1].pantallas[0].preguntas[0], {}).map((o) => o.codigo), c.esperado.opciones_P_SUC);
    assert.equal(m.texto('Sucursal: {P_SUC.texto}; canales {CANALES}; {ficha.nombre}', c.respuestas), c.esperado.texto_canales);
    assert.equal(m.primera(c.respuestas).pantalla ?? null, c.esperado.primera);
  });
}
for (const b of cq.barajar) {
  test(`barajar ${b.semilla} ${b.clave}`, () => {
    assert.deepEqual(barajar([...Array(10).keys()], b.semilla, b.clave), b.orden);
  });
}
