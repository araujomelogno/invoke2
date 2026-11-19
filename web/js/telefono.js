// @ts-check
/**
 * Audio del puesto: SIP.js embebido (ADR-004), sin softphone empaquetado.
 *
 * El navegador abre UNA llamada SIP sobre WSS hacia Asterisk al empezar el turno
 * (sip:sala-<participante>@dominio) y la mantiene abierta: es la "pata
 * permanente" del puesto (ADR-011). Cada llamada a un respondente se puentea a
 * esa pata desde ARI, así que el audio ya está establecido cuando alguien
 * atiende y el traspaso en power/predictivo entra en el presupuesto de 1 s.
 * Colgar, mudo y DTMF se piden a la API; la pata permanente no se corta.
 */
import { Web } from '/vendor/sip.js/index.js';

export class Telefono {
  /**
   * @param {{uri: string, usuario: string, clave: string, wss: string, destino_sala: string}} sip
   * @param {RTCIceServer[]} ice
   * @param {HTMLAudioElement} audio
   * @param {{alConectar?: () => void, alCaer?: () => void}} [avisos]
   */
  constructor(sip, ice, audio, avisos = {}) {
    this.sip = sip;
    this.avisos = avisos;
    this.conectado = false;
    this.usuario = new Web.SimpleUser(sip.wss, {
      aor: sip.uri,
      media: { constraints: { audio: true, video: false }, remote: { audio } },
      userAgentOptions: {
        authorizationUsername: sip.usuario,
        authorizationPassword: sip.clave,
        displayName: sip.usuario,
        sessionDescriptionHandlerFactoryOptions: { peerConnectionConfiguration: { iceServers: ice } },
      },
      delegate: {
        onCallAnswered: () => { this.conectado = true; avisos.alConectar?.(); },
        onCallHangup: () => { const antes = this.conectado; this.conectado = false; if (antes) avisos.alCaer?.(); },
        onServerDisconnect: () => { this.conectado = false; avisos.alCaer?.(); },
      },
    });
  }

  async conectar() {
    await this.usuario.connect();
    await this.usuario.call(this.sip.destino_sala);
  }

  async desconectar() {
    try { await this.usuario.hangup(); } catch { /* ya colgado */ }
    try { await this.usuario.disconnect(); } catch { /* ya desconectado */ }
  }

  /** @returns {RTCPeerConnection|undefined} */
  get conexion() {
    const s = /** @type {any} */ (this.usuario).session;
    return s?.sessionDescriptionHandler?.peerConnection;
  }
}

/**
 * Prueba de audio previa al turno: micrófono, nivel de entrada y salida.
 * Sin visita técnica (PRD): el encuestador la hace solo desde el navegador.
 * @returns {Promise<{microfono: boolean, nivel_max: number, dispositivo: string, error?: string}>}
 */
export async function probarMicrofono(segundos = 3) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const ctx = new AudioContext();
    const fuente = ctx.createMediaStreamSource(stream);
    const analizador = ctx.createAnalyser();
    analizador.fftSize = 512;
    fuente.connect(analizador);
    const datos = new Uint8Array(analizador.fftSize);
    let max = 0;
    const fin = performance.now() + segundos * 1000;
    while (performance.now() < fin) {
      analizador.getByteTimeDomainData(datos);
      for (const v of datos) max = Math.max(max, Math.abs(v - 128) / 128);
      await new Promise((r) => setTimeout(r, 50));
    }
    const pista = stream.getAudioTracks()[0];
    const dispositivo = pista?.label || 'micrófono';
    stream.getTracks().forEach((t) => t.stop());
    await ctx.close();
    return { microfono: true, nivel_max: Math.round(max * 100) / 100, dispositivo };
  } catch (e) {
    return { microfono: false, nivel_max: 0, dispositivo: '', error: String(e) };
  }
}

/** Tono de prueba por el auricular. */
export async function tonoDePrueba() {
  const ctx = new AudioContext();
  const o = ctx.createOscillator();
  const g = ctx.createGain();
  o.frequency.value = 660;
  g.gain.value = 0.08;
  o.connect(g).connect(ctx.destination);
  o.start();
  await new Promise((r) => setTimeout(r, 700));
  o.stop();
  await ctx.close();
}
