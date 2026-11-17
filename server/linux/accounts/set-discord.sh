#!/bin/bash
# Turns on "Sign in with Discord" for LBPPC accounts. Run as root on the VPS, in a terminal (it asks for the secret):
#   bash /opt/lbppc-accounts/set-discord.sh
# The client secret is read without being shown, written only to /etc/lbppc/accounts/kratos/discord.yml
# (root:ory-kratos, 640), and Kratos is restarted. In Discord's Developer Portal (OAuth2), the redirect must be:
#   https://lbppc.com/.ory/self-service/methods/oidc/callback/discord
set -euo pipefail
umask 077
client_id=${DISCORD_CLIENT_ID:-1569417825045712926}
read -r -s -p "Discord client secret for application $client_id (not shown): " secret; echo
[[ $secret =~ ^[A-Za-z0-9_-]{16,128}$ ]] || { echo "That doesn't look like a Discord client secret." >&2; exit 1; }
f=/etc/lbppc/accounts/kratos/discord.yml
cat > "$f.new" <<YAML
selfservice:
  methods:
    oidc:
      enabled: true
      config:
        providers:
          - id: discord
            provider: discord
            label: Discord
            client_id: "$client_id"
            client_secret: "$secret"
            scope: [identify]
            mapper_url: file:///etc/lbppc/accounts/kratos/discord-mapper.jsonnet
YAML
chown root:ory-kratos "$f.new" && chmod 640 "$f.new" && mv "$f.new" "$f"
unset secret
systemctl restart lbppc-kratos
sleep 4
systemctl is-active lbppc-kratos
