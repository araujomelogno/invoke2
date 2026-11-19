// @ts-check
/**
 * Calidad de red del puesto remoto (ADR-005 y consola de supervisión): pérdida
 * de paquetes, jitter, RTT y si la llamada va por TURN, leídos de getStats()
 * cada pocos segundos y enviados a la API en lotes.
 */

/**
 * @param {RTCPeerConnection} pc
 * @param {{perdidos: number, recibidos: number}} previo
 */
export async function medir(pc, previo) {
  const stats = await pc.getStats();
  /** @type {any} */ let entrada = null;
  /** @type {any} */ let par = null;
  /** @type {Record<string, any>} */ const porId = {};
  stats.forEach((s) => {
    porId[s.id] = s;
    if (s.type === 'inbound-rtp' && s.kind === 'audio') entrada = s;
    if (s.type === 'candidate-pair' && (s.nominated || s.selected) && s.state === 'succeeded') par = s;
  });
  if (!entrada) return null;
  const perdidos = entrada.packetsLost ?? 0;
  const recibidos = entrada.packetsReceived ?? 0;
  const dP = Math.max(0, perdidos - previo.perdidos);
  const dR = Math.max(0, recibidos - previo.recibidos);
  previo.perdidos = perdidos;
  previo.recibidos = recibidos;
  const local = par ? porId[par.localCandidateId] : null;
  const codec = entrada.codecId ? porId[entrada.codecId]?.mimeType : null;
  return {
    perdida_pct: dP + dR > 0 ? Math.round((dP / (dP + dR)) * 10000) / 100 : 0,
    jitter_ms: Math.round((entrada.jitter ?? 0) * 100000) / 100,
    rtt_ms: par?.currentRoundTripTime != null ? Math.round(par.currentRoundTripTime * 100000) / 100 : null,
    relay: local ? local.candidateType === 'relay' : null,
    codec: codec ? String(codec).replace('audio/', '') : null,
  };
}

/**
 * Toma una muestra cada `cada` ms y las envía cada `lote` muestras.
 * @param {() => RTCPeerConnection|undefined} conexion
 * @param {(m: any[]) => Promise<void>} enviar
 * @param {(m: any) => void} [alMedir]
 */
export function vigilar(conexion, enviar, alMedir, cada = 5000, lote = 3) {
  const previo = { perdidos: 0, recibidos: 0 };
  /** @type {any[]} */ let pendientes = [];
  const id = setInterval(async () => {
    const pc = conexion();
    if (!pc) return;
    const m = await medir(pc, previo).catch(() => null);
    if (!m) return;
    alMedir?.(m);
    pendientes.push(m);
    if (pendientes.length >= lote) {
      const envio = pendientes;
      pendientes = [];
      enviar(envio).catch(() => { /* se pierde la muestra, no el turno */ });
    }
  }, cada);
  return () => clearInterval(id);
}
