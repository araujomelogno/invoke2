// Índice activo al hacer scroll y selector de tema (preferencia guardada por visitante).
(function () {
  const links = Array.from(document.querySelectorAll('.sidebar nav a'));
  const obs = new IntersectionObserver((es) => {
    es.forEach((e) => {
      if (e.isIntersecting) links.forEach((a) => a.classList.toggle('activo', a.getAttribute('href') === '#' + e.target.id));
    });
  }, { rootMargin: '-10% 0px -80% 0px' });
  document.querySelectorAll('section[id]').forEach((s) => obs.observe(s));
  const raiz = document.documentElement;
  try { const t = localStorage.getItem('invoke-docs-tema'); if (t) raiz.dataset.theme = t; } catch (e) { /* sin storage */ }
  const b = document.querySelector('.tema');
  if (b) b.onclick = () => {
    const oscuro = raiz.dataset.theme ? raiz.dataset.theme !== 'dark' : !matchMedia('(prefers-color-scheme: dark)').matches;
    raiz.dataset.theme = oscuro ? 'dark' : 'light';
    try { localStorage.setItem('invoke-docs-tema', raiz.dataset.theme); } catch (e) { /* sin storage */ }
  };
})();
