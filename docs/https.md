# HTTPS

Dealflow never serves plain http to a network. Without a domain it answers only on the computer it
runs on (`http://localhost:8000`, which browsers treat as secure). For a team:

1. Point a name at the server (`crm.yourfirm.com`) and open ports 80 and 443 to it.
2. In `.env`: `DEALFLOW_DOMAIN=crm.yourfirm.com` (and optionally `DEALFLOW_ACME_EMAIL`).
3. `docker compose --profile https up -d`

Caddy (`deploy/caddy/Caddyfile`) gets and renews a Let's Encrypt certificate, redirects http to https,
offers TLS 1.2 and 1.3 only, HTTP/2 and HTTP/3, and hides its version. The app then sets `__Host-`
cookies that only travel over https, HSTS for a year (preload is your choice: `DEALFLOW_HSTS_PRELOAD=1`),
refuses other host names, and reads the client's address from Caddy's `X-Forwarded-For`.

## A name only your office network knows

Let's Encrypt can't reach it, so use Caddy's own certificate authority: `DEALFLOW_TLS=internal`. Copy its
root certificate out and install it on each computer (and phone) as trusted:

```sh
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt dealflow-root.crt
```

## Your own proxy

Set `DEALFLOW_DOMAIN` without the https profile and forward to the app's port. Your proxy must set
`X-Forwarded-Proto: https` and add the client's address to `X-Forwarded-For`; with more than one proxy in
front, set `DEALFLOW_PROXY_HOPS` to how many. Passkeys need https (or localhost).
