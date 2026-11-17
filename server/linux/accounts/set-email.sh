#!/bin/bash
# Turns on email for LBPPC accounts (verification codes; later recovery) through Resend's SMTP. Run as root on the
# VPS, in a terminal (it asks for the key):
#   bash /opt/lbppc-accounts/set-email.sh
# Use a Resend API key with sending access only. It's read without being shown, written only to
# /etc/lbppc/accounts/kratos/email.yml (root:ory-kratos, 640), and Kratos is restarted. Mail comes from
# noreply@lbppc.com, so lbppc.com must be verified in Resend.
set -euo pipefail
umask 077
read -r -s -p "Resend API key (sending access; not shown): " key; echo
[[ $key =~ ^re_[A-Za-z0-9_]{16,128}$ ]] || { echo "That doesn't look like a Resend API key (re_...)." >&2; exit 1; }
f=/etc/lbppc/accounts/kratos/email.yml
cat > "$f.new" <<YAML
courier:
  smtp:
    connection_uri: "smtps://resend:$key@smtp.resend.com:465/"
    from_address: noreply@lbppc.com
    from_name: LBPPC
YAML
chown root:ory-kratos "$f.new" && chmod 640 "$f.new" && mv "$f.new" "$f"
unset key
systemctl restart lbppc-kratos
sleep 4
systemctl is-active lbppc-kratos
