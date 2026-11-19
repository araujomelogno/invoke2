"""Datos de prueba compartidos."""

CUESTIONARIO_MINIMO = {
    "formato": 1,
    "id": "minimo",
    "nombre": "Mínimo",
    "bloques": [{
        "id": "B",
        "pantallas": [
            {"id": "S1", "preguntas": [
                {"id": "SEXO", "tipo": "simple", "texto": "Sexo",
                 "opciones": [{"codigo": "1", "texto": "Hombre"}, {"codigo": "2", "texto": "Mujer"}]},
            ], "siguiente": [{"si": {"op": "==", "args": [{"ficha": "excluir"}, "si"]}, "fin": 20}]},
            {"id": "S2", "preguntas": [
                {"id": "NOTA", "tipo": "numero", "texto": "Nota", "minimo": 1, "maximo": 10},
            ]},
        ],
    }],
}
