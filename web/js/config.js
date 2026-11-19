// Configuración pública del cliente web. La config de Firebase NO es secreta
// (identifica el proyecto); los secretos viven en Secret Manager, nunca acá.
// En cada entorno se reemplaza este archivo en el despliegue (ver docs/despliegue.html).
window.INVOKE_CONFIG = window.INVOKE_CONFIG || {
  firebase: {
    apiKey: 'TU_API_KEY',
    authDomain: 'TU_PROYECTO.firebaseapp.com',
    projectId: 'TU_PROYECTO',
    appId: 'TU_APP_ID',
  },
  // URL del gateway de sala (Cloud Run). Vacío = mismo host (desarrollo local).
  salaWs: '',
  zonaHoraria: 'America/Montevideo',
};
