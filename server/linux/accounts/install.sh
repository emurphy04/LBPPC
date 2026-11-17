#!/bin/bash
# Installs LBPPC accounts' services on the VPS from this folder (docs/accounts/DESIGN.md, Phase 1): Kratos, Hydra and
# the password migration hook, all on 127.0.0.1. Run as root from server/linux/accounts after:
#   - kratos and hydra (v26.2.0, checksums verified) in /usr/local/bin, users ory-kratos and ory-hydra;
#   - the kratos and hydra PostgreSQL roles and databases, passwords in /etc/lbppc/accounts/<name>-db-password.
# Secrets are made here with openssl, written straight into root-owned files, and never printed. Running it again
# keeps existing secrets and replaces only the non-secret files.
set -euo pipefail
umask 077
here=$(cd -- "$(dirname -- "$0")" && pwd)
conf=/etc/lbppc/accounts

install -d -m 711 -o root -g root "$conf"
install -d -m 750 -o root -g ory-kratos "$conf/kratos"
install -d -m 750 -o root -g ory-hydra "$conf/hydra"
for f in kratos.yml identity.schema.json migrate-body.jsonnet discord-mapper.jsonnet registration-body.jsonnet          password-body.jsonnet; do
    install -m 640 -o root -g ory-kratos "$here/kratos/$f" "$conf/kratos/$f"
done
install -m 640 -o root -g ory-hydra "$here/hydra/hydra.yml" "$conf/hydra/hydra.yml"

hex() { openssl rand -hex "$1"; }

if [[ ! -f $conf/hook.env ]]; then
    printf 'HOOK_KEY=%s\n' "$(hex 32)" > "$conf/hook.env"
    chmod 600 "$conf/hook.env"
fi
hook_key=$(sed -n 's/^HOOK_KEY=//p' "$conf/hook.env")

if [[ ! -f $conf/kratos/secrets.yml ]]; then
    cat > "$conf/kratos/secrets.yml" <<EOF
dsn: postgres://kratos:$(cat "$conf/kratos-db-password")@127.0.0.1:5432/kratos?sslmode=disable&max_conns=10
secrets:
  default: ["$(hex 32)"]
  cookie: ["$(hex 32)"]
  cipher: ["$(hex 16)"]
selfservice:
  methods:
    password:
      config:
        migrate_hook:
          config:
            auth:
              type: api_key
              config:
                name: Authorization
                value: "Bearer $hook_key"
                in: header
courier:
  smtp:
    # Amazon SES goes here (secrets.example.yml). Until then nothing listens on this address and no email is sent.
    connection_uri: smtp://127.0.0.1:2525/?disable_starttls=true
EOF
    chown root:ory-kratos "$conf/kratos/secrets.yml"
    chmod 640 "$conf/kratos/secrets.yml"
fi

# Discord sign-in: off until set-discord.sh writes its client (the secret is typed in, never stored in git).
if [[ ! -f $conf/kratos/discord.yml ]]; then
    echo '{}' > "$conf/kratos/discord.yml"
    chown root:ory-kratos "$conf/kratos/discord.yml"
    chmod 640 "$conf/kratos/discord.yml"
fi

# Email (Resend): no sending until set-email.sh writes its key.
if [[ ! -f $conf/kratos/email.yml ]]; then
    echo '{}' > "$conf/kratos/email.yml"
    chown root:ory-kratos "$conf/kratos/email.yml"
    chmod 640 "$conf/kratos/email.yml"
fi

# Phase 2 (sign-up, password changes): empty until enable-phase2.sh, on release day.
if [[ ! -f $conf/kratos/phase2.yml ]]; then
    echo '{}' > "$conf/kratos/phase2.yml"
    chown root:ory-kratos "$conf/kratos/phase2.yml"
    chmod 640 "$conf/kratos/phase2.yml"
fi
# The account hooks' keys: one for Kratos to call them, and shadNet's AdminApiKey for them to call shadNet.
id lbppc-hooks >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin lbppc-hooks
if [[ ! -f $conf/hooks.env ]]; then
    shadnet_key=$(sed -n 's/^LBPPC_SHADNET_KEY=//p' /etc/lbppc/refresh-login-check.env)
    printf 'HOOKS_KEY=%s\nSHADNET_ADMIN_KEY=%s\n' "$(hex 32)" "$shadnet_key" > "$conf/hooks.env"
    chmod 600 "$conf/hooks.env"
fi

if [[ ! -f $conf/hydra/secrets.yml ]]; then
    cat > "$conf/hydra/secrets.yml" <<EOF
dsn: postgres://hydra:$(cat "$conf/hydra-db-password")@127.0.0.1:5432/hydra?sslmode=disable&max_conns=10
secrets:
  system: ["$(hex 32)"]
  cookie: ["$(hex 32)"]
EOF
    chown root:ory-hydra "$conf/hydra/secrets.yml"
    chmod 640 "$conf/hydra/secrets.yml"
fi

install -d -m 755 -o root -g root /opt/lbppc-accounts
install -m 644 -o root -g root "$here/password_migration_hook.py" "$here/import_shadnet_accounts.py"     "$here/test_signin.py" /opt/lbppc-accounts/
install -m 700 -o root -g root "$here/set-discord.sh" "$here/set-email.sh" "$here/enable-phase2.sh" /opt/lbppc-accounts/
install -m 644 -o root -g root "$here/systemd/lbppc-account-hooks.service" /etc/systemd/system/   # started by enable-phase2.sh
for unit in lbppc-kratos lbppc-hydra lbppc-password-migration; do
    install -m 644 -o root -g root "$here/systemd/$unit.service" /etc/systemd/system/
done
install -m 644 -o root -g root "$here/systemd/lbppc-account-import.service" "$here/systemd/lbppc-account-import.timer"     /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lbppc-password-migration lbppc-hydra lbppc-kratos
systemctl restart lbppc-password-migration lbppc-hydra lbppc-kratos   # pick up changed files
systemctl enable --now lbppc-account-import.timer
sleep 5
for unit in lbppc-password-migration lbppc-hydra lbppc-kratos; do
    printf '%s: %s\n' "$unit" "$(systemctl is-active "$unit")"
done
ss -ltnp | awk '$4 ~ /:(4433|4434|4444|4445|4480)$/ {print "listening", $4}'
