#!/bin/sh
# Regenerate the evidence in compliance/ (decision 63, S7). Needs Docker and the backend's CI tools
# (pip install --require-hashes -r backend/requirements-ci.txt). Run from the repository root after
# `docker build -t dealflow:latest .`.
set -eu
TRIVY=aquasec/trivy:0.67.2@sha256:e2b22eac59c02003d8749f5b8d9bd073b62e30fefaef5b7c8371204e0a4b0c08
out=compliance
pip-audit -r backend/requirements.txt --require-hashes --disable-pip --format json -o "$out/pip-audit.json" || true
(cd frontend && npm audit --json > "../$out/npm-audit.json") || true
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$TRIVY" image --quiet --scanners vuln \
  --severity LOW,MEDIUM,HIGH,CRITICAL dealflow:latest > "$out/trivy-image.txt" || true
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock "$TRIVY" image --quiet --format cyclonedx \
  dealflow:latest > "$out/sbom.cdx.json"
echo "Written to $out/. Add the ZAP baseline with: scripts/zap-baseline.sh"
