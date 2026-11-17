#!/bin/bash
# Proves the latest backup can be restored: checks its checksums, decrypts each file here with the private key on this
# PC, restores each database into a scratch database on the VPS, counts what came back, then deletes the scratch
# databases and every decrypted copy. Nothing live is touched.
# usage (Git Bash on Windows): bash restore-test.sh <key file, as WSL sees it> [ssh target]
#   or set LBPPC_BACKUP_KEY to the key file instead of passing it
set -euo pipefail
key=${1:-${LBPPC_BACKUP_KEY:-}}
[[ -n $key ]] || { echo 'usage: bash restore-test.sh <key file, as WSL sees it (/mnt/c/...)> [ssh target]' >&2; exit 2; }
vps=${2:-root@179.236.82.47}
age() { MSYS_NO_PATHCONV=1 wsl -d Ubuntu-24.04 -- age "$@" < /dev/null; }   # files only: no pipes across WSL
work=$(mktemp -d)
trap 'rm -rf "$work"; ssh -o BatchMode=yes "$vps" "rm -rf /tmp/restoretest" < /dev/null' EXIT
latest=$(ssh -o BatchMode=yes "$vps" 'ls -1d /var/backups/lbppc/*/ | tail -1' < /dev/null)
echo "latest backup: $latest"
ssh -o BatchMode=yes "$vps" "cd $latest && sha256sum --quiet -c SHA256SUMS" < /dev/null && echo "checksums: ok"
scp -q "$vps:$latest*.age" "$work/"
wwork=$(MSYS_NO_PATHCONV=1 wsl -d Ubuntu-24.04 -- wslpath -a "$(cygpath -m "$work")" < /dev/null)
for f in kratos.dump hydra.dump lbppc_web.dump shadnet.db; do
    age -d -i "$key" -o "$wwork/$f" "$wwork/$f.age"
done
ssh -o BatchMode=yes "$vps" 'install -d -m 700 /tmp/restoretest && chown postgres /tmp/restoretest' < /dev/null
scp -q "$work"/kratos.dump "$work"/hydra.dump "$work"/lbppc_web.dump "$vps:/tmp/restoretest/"
for db in kratos hydra lbppc_web; do
    ssh -o BatchMode=yes "$vps" "cd /tmp && chown postgres /tmp/restoretest/$db.dump && s=restoretest_$db &&
        sudo -u postgres dropdb --if-exists \$s && sudo -u postgres createdb \$s &&
        sudo -u postgres pg_restore --no-owner --no-privileges -d \$s /tmp/restoretest/$db.dump 2>/dev/null;
        echo \"   $db: \$(sudo -u postgres psql -d \$s -tAc \"SELECT count(*) FROM pg_tables WHERE schemaname='public'\") tables\" &&
        sudo -u postgres dropdb \$s" < /dev/null
done
python3 -c 'import sqlite3,sys; print("   shadnet:", sqlite3.connect(sys.argv[1]).execute("select count(*) from account").fetchone()[0], "accounts")' "$(cygpath -w "$work/shadnet.db")"
ssh -o BatchMode=yes "$vps" 'cd /tmp && sudo -u postgres dropdb --if-exists restoretest_kratos' < /dev/null
echo "decrypted copies deleted"
