# LBPPC accounts (server side)

Phase 1 of [docs/accounts/DESIGN.md](../../../docs/accounts/DESIGN.md): one LBPPC account, kept by Ory Kratos, with
Ory Hydra for tokens. Installed on the VPS 2026-11-16. Everything listens on 127.0.0.1 only; nothing is public until
nginx is set up for it (below).

| Service | Port (127.0.0.1) | Runs as | What |
|---|---|---|---|
| `lbppc-kratos` | 4433 public API, 4434 admin API | `ory-kratos` | Accounts, passwords (Argon2id), email, two-factor, sessions |
| `lbppc-hydra` | 4444 public, 4445 admin | `ory-hydra` | OAuth 2 / OpenID Connect tokens for the launcher, Refresh, shadNet |
| `lbppc-password-migration` | 4480 | `lbppc` | Checks old shadNet passwords on a player's first sign-in (until the cut-off) |

## Files

- `/usr/local/bin/kratos`, `/usr/local/bin/hydra`: v26.2.0, from the GitHub releases, checksums verified.
- `/etc/lbppc/accounts/` (`711`): this folder's `kratos/` and `hydra/` configs (`640`, root and the service's group),
  their `secrets.yml` (made by `install.sh` with random values), `hook.env`, and the database passwords. None of the
  secrets are in git; `*/secrets.example.yml` shows their shape.
- `/opt/lbppc-accounts/`: the hook, `import_shadnet_accounts.py` and `test_signin.py`.
- PostgreSQL databases `kratos` and `hydra`, each owned by its own role, with no access for others.

## Install or update

```bash
scp -r server/linux/accounts root@VPS:/root/accounts-kit
ssh root@VPS 'cd /root/accounts-kit && find . -type f -exec sed -i "s/\r$//" {} + && bash install.sh'
```

`install.sh` keeps existing secrets and replaces only the other files. Kratos and Hydra apply their database
migrations each time they start.

## Accounts from shadNet

`python3 /opt/lbppc-accounts/import_shadnet_accounts.py` copies shadNet accounts into Kratos: name and shadNet user
ID only, no password hash and no email. Run it again at any time to bring over accounts made in the launcher since;
`--dry-run` only counts. 602 were imported on 2026-11-16.

The first time an imported account signs in, Kratos asks the hook to check the password against shadNet and then
keeps it as Argon2id. The hook's log (`journalctl -u lbppc-password-migration`) says `user N: migrated`, `wrong
password` or `locked`. Check a sign-in end to end with your own account:

```bash
python3 /opt/lbppc-accounts/test_signin.py NAME     # asks for the password
```

Until the game signs in with tokens (DESIGN.md Phase 2), **the game still uses shadNet's own password**. So sign-up,
password changes and password recovery are off (`kratos.yml`; lbppc.com hides password changes too): a change made in
Kratos wouldn't reach the game. Accounts made in the launcher arrive in Kratos within 10 minutes
(`lbppc-account-import.timer`).

The launcher is registered with Hydra as `lbppc-launcher`: a public client (no secret), PKCE required, redirects only to
`http://127.0.0.1:47613-47615/callback`, scopes `openid offline_access game`, marked first-party (lbppc.com's consent
step accepts it without asking). Access tokens carry the player's name and shadNet ID (`ext`, seen by introspection
on 127.0.0.1:4445).

## Before anything is public

- [x] nginx (lbppc-web repo, `deploy/nginx-lbppc-web.conf`; self-signed placeholder until the DNS move): `lbppc.com/.ory/` to 127.0.0.1:4433 (public API only, never 4434), `auth.lbppc.com` to 4444 (never 4445),
  with TLS, HSTS and **rate limits on sign-in, sign-up, recovery and verification** (self-hosted Kratos has none).
- [ ] Amazon SES in `kratos/secrets.yml` (`courier.smtp.connection_uri`), with SPF, DKIM and DMARC for lbppc.com.
- [ ] Discord application registered; `oidc` in `kratos/secrets.yml` (see `secrets.example.yml`).
- [x] The web app's sign-in, settings, verification, claims and staff pages, and Hydra's login and consent pages
  (IdeaProjects\lbppc-web). Sign-up and recovery pages come with Phase 2.
- [ ] lbppc.com, www and auth DNS to the VPS, then certbot (lbppc-web README, "Going live").
- [ ] The security review in DESIGN.md.

## Removing it

```bash
systemctl disable --now lbppc-kratos lbppc-hydra lbppc-password-migration
sudo -u postgres dropdb kratos && sudo -u postgres dropdb hydra
```

shadNet and Refresh don't depend on any of it yet.
