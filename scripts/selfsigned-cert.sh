#!/bin/sh
# Creates a temporary self-signed certificate in the `certs` volume so that nginx can start
# before the real Let's Encrypt certificate exists.  Usage: scripts/selfsigned-cert.sh example.com
set -eu
DOMAIN="${1:-localhost}"
VOLUME="${COMPOSE_PROJECT_NAME:-trailmaps}_certs"
docker volume create "$VOLUME" >/dev/null
docker run --rm -v "$VOLUME":/etc/letsencrypt --entrypoint sh alpine/openssl -c "
  mkdir -p /etc/letsencrypt/live/$DOMAIN &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 30 -subj '/CN=$DOMAIN' \
    -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
    -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem"
echo "Self-signed certificate for $DOMAIN created in volume $VOLUME"
