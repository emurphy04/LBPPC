#!/bin/bash
# Release day for LBPPC accounts Phase 2 (docs/accounts/DESIGN.md). Run as root on the VPS ONLY after:
#   1. shadNet runs with lbppc-shadnet-tokens.patch and lbppc-shadnet-accounts.patch (server/linux/README.md), and
#   2. the launcher with "Sign in with lbppc.com" is released.
# It turns on, together: sign-up on lbppc.com (also with Discord; account_hooks.py makes the shadNet account),
# password changes and recovery on lbppc.com (the old shadNet password is turned off after either), and the account
# hooks service. Undo: echo '{}' > /etc/lbppc/accounts/kratos/phase2.yml, remove the lbppc-web drop-in, restart both.
set -euo pipefail
umask 077
conf=/etc/lbppc/accounts
key=$(sed -n 's/^HOOKS_KEY=//p' "$conf/hooks.env")
[[ ${#key} -ge 32 ]] || { echo "no HOOKS_KEY in $conf/hooks.env (run install.sh)" >&2; exit 1; }
# shadNet must have lbppc-shadnet-accounts.patch: its password-off endpoint answers 404 for account 0.
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:31350/admin/v1/lbppc/accounts/0/password-off \
       -H "X-Admin-Api-Key: $(sed -n 's/^SHADNET_ADMIN_KEY=//p' "$conf/hooks.env")")
[[ $code == 404 ]] || { echo "shadNet doesn't have lbppc-shadnet-accounts.patch yet (answered $code): not enabling" >&2; exit 1; }
hook() {  # url body-file
cat <<YAML
            - hook: web_hook
              config:
                url: $1
                method: POST
                body: file://$conf/kratos/$2
                response:
                  parse: $3
                auth:
                  type: api_key
                  config:
                    name: Authorization
                    value: "Bearer $key"
                    in: header
YAML
}
f=$conf/kratos/phase2.yml
{
cat <<YAML
selfservice:
  flows:
    registration:
      enabled: true
      after:
        password:
          hooks:
YAML
hook http://127.0.0.1:4481/registration registration-body.jsonnet true
echo "            - hook: session"
echo "        oidc:"
echo "          hooks:"
hook http://127.0.0.1:4481/registration registration-body.jsonnet true
echo "            - hook: session"
cat <<YAML
    recovery:
      enabled: true
    settings:
      after:
        password:
          hooks:
YAML
hook http://127.0.0.1:4481/password-changed password-body.jsonnet false
} > "$f.new"
chown root:ory-kratos "$f.new" && chmod 640 "$f.new" && mv "$f.new" "$f"
mkdir -p /etc/systemd/system/lbppc-web.service.d
printf '[Service]\nEnvironment=LBPPC_PHASE2=1\n' > /etc/systemd/system/lbppc-web.service.d/phase2.conf
systemctl daemon-reload
systemctl enable --now lbppc-account-hooks
systemctl restart lbppc-kratos lbppc-web
sleep 4
systemctl is-active lbppc-account-hooks lbppc-kratos lbppc-web
