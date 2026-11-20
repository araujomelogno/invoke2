# PBX de INVOKE 2: Asterisk 22 LTS (22.5.2) de Ubuntu 26.04 LTS con chan_pjsip, ARI,
# AMD, Opus de código abierto (codec_opus_open_source, incluido en el paquete) y
# realtime en Postgres. Verificado módulo por módulo y con scripts/probar_pbx.py
# contra este mismo paquete (docs/decisiones.md, ADR-013).
#
# Los .conf de este directorio son plantillas: entrypoint.sh las renderiza con las
# variables de entorno (los secretos llegan desde Secret Manager, nunca en la imagen).
FROM ubuntu:26.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      asterisk asterisk-modules asterisk-core-sounds-es gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    # users.conf de ejemplo del paquete: obsoleto en Asterisk 22 y sin uso acá
    && rm -f /etc/asterisk/users.conf
COPY *.conf /etc/asterisk/plantillas/
COPY laboratorio/ /etc/asterisk/plantillas/laboratorio/
COPY entrypoint.sh instalar_archivos.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/instalar_archivos.sh && mkdir -p /etc/asterisk/keys
# Las grabaciones esperan acá a que services/ari las suba al bucket: va en un volumen
# para no perderlas si el contenedor se recrea antes de la subida.
VOLUME ["/var/spool/asterisk/recording"]
EXPOSE 5060/udp 8088/tcp 8089/tcp 10000-20000/udp
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
