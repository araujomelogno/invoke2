# PBX de INVOKE 2: Asterisk 20 (Ubuntu 24.04) con chan_pjsip, ARI y realtime en Postgres.
# Los .conf de este directorio son plantillas: entrypoint.sh las renderiza con envsubst
# (los secretos llegan como variables de entorno desde Secret Manager, nunca en el repo).
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      asterisk asterisk-modules asterisk-core-sounds-es gettext-base ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY *.conf /etc/asterisk/plantillas/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
EXPOSE 5060/udp 8088 8089 10000-20000/udp
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
