#!/bin/sh
# Passive OWASP ZAP scan of a running install (docker compose --profile https up -d). Writes
# compliance/zap-report.html and prints the summary. Usage: scripts/zap-baseline.sh [project-name]
set -eu
project=${1:-$(basename "$PWD")}
domain=$(grep -E '^DEALFLOW_DOMAIN=' .env | cut -d= -f2)
ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "${project}-caddy-1" | awk '{print $1}')
docker run --rm --network "${project}_default" --add-host "$domain:$ip" -v "$PWD/compliance:/zap/wrk:rw" \
  ghcr.io/zaproxy/zaproxy:2.17.0 zap-baseline.py -t "https://$domain/" -m 3 -r zap-report.html -I
