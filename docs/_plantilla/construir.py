"""Arma docs/manual.html, docs/despliegue.html y docs/decisiones.html.

Cada documento es autocontenido (estilo, logo y script embebidos) para que se
pueda abrir desde el repositorio, adjuntar o publicar sin archivos sueltos.
El contenido vive en docs/_plantilla/<nombre>.cuerpo.html; acá solo se envuelve.

    python docs/_plantilla/construir.py
"""
import base64
import pathlib
import re

AQUI = pathlib.Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent
LOGO = base64.b64encode((RAIZ / "web/img/logo-horizontal-blanco.png").read_bytes()).decode()
CSS = (AQUI / "estilo.css").read_text()
JS = (AQUI / "script.js").read_text()

DOCS = {
    "manual": ("Manual de uso", "Manual INVOKE 2"),
    "despliegue": ("Guía de despliegue", "Despliegue INVOKE 2"),
    "decisiones": ("Registro de decisiones", "Decisiones INVOKE 2"),
}

for nombre, (tipo, titulo) in DOCS.items():
    cuerpo = (AQUI / f"{nombre}.cuerpo.html").read_text()
    cuerpo = cuerpo.replace("{{arquitectura}}", (AQUI / "arquitectura.svg.html").read_text())

    def imagen(m: re.Match) -> str:
        datos = base64.b64encode((AQUI / "img" / f"{m.group(1)}.jpg").read_bytes()).decode()
        return (f'<figure class="diagrama"><img src="data:image/jpeg;base64,{datos}" alt="{m.group(2)}" '
                f'style="max-width:100%;height:auto;display:block;border-radius:3px" loading="lazy" />'
                f"<figcaption>{m.group(2)}</figcaption></figure>")

    cuerpo = re.sub(r"\{\{img:([\w-]+):([^}]+)\}\}", imagen, cuerpo)
    secciones = re.findall(r'<section id="([^"]+)"[^>]*data-indice="([^"]+)"(?: data-grupo="([^"]+)")?', cuerpo)
    nav, grupo = [], None
    for sid, texto, g in secciones:
        if g and g != grupo:
            grupo = g
            nav.append(f'<div class="sec">{g}</div>')
        nav.append(f'<a href="#{sid}">{texto}</a>')
    cortos = {"manual": "Manual", "despliegue": "Despliegue", "decisiones": "Decisiones"}
    otros = " · ".join(f'<a href="{n}.html">{cortos[n]}</a>' for n in DOCS if n != nombre)
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{titulo}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
<style>
{CSS}</style>
</head>
<body>
<button class="tema" aria-label="Cambiar entre tema claro y oscuro">Claro / oscuro</button>
<div class="app">
<aside class="sidebar">
  <div class="sidebar-top"><img src="data:image/png;base64,{LOGO}" alt="Equipos Consultores" />
    <div class="doc-tipo">INVOKE 2 · {tipo}</div></div>
  <nav>{''.join(nav)}</nav>
  <div class="pie">Otros documentos: {otros}</div>
</aside>
<main class="main"><div class="contenido">
{cuerpo}
</div></main>
</div>
<script>
{JS}</script>
</body>
</html>
"""
    (RAIZ / "docs" / f"{nombre}.html").write_text(html)
    print(f"docs/{nombre}.html ({len(secciones)} secciones)")
