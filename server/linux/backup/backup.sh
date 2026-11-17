#!/bin/bash
# Nightly backup of LBPPC's account data: the Kratos, Hydra and lbppc_web databases (accounts, two-factor, tokens,
# claims, staff, audit log) and shadNet's account database. Each file is encrypted with age to the public key in
# /etc/lbppc/backup/recipient.txt; only the private key (kept off this server, on Ethan's PC) can read them, so a
# break-in here doesn't expose the backups. Kept 14 days in /var/backups/lbppc/<date>/.
# Refresh's database (6 GB, nearly all the level archive) is not in here: see server/linux/README.md "Backups".
set -euo pipefail
umask 077
recipient=/etc/lbppc/backup/recipient.txt
root=/var/backups/lbppc
dest=$root/$(date -u +%Y%m%d-%H%M)
mkdir -p "$dest"
cd /tmp
for db in kratos hydra lbppc_web; do
    sudo -u postgres pg_dump --format=custom "$db" | age -R "$recipient" > "$dest/$db.dump.age"
done
# shadNet (SQLite): a consistent copy through SQLite's own backup, while it runs.
tmp=$(mktemp -d)
python3 -I -c 'import sqlite3, sys
source = sqlite3.connect("file:/srv/lbppc/shadnet/db/shadnet.db?mode=ro", uri=True)
copy = sqlite3.connect(sys.argv[1]); source.backup(copy); copy.close()' "$tmp/shadnet.db"
age -R "$recipient" < "$tmp/shadnet.db" > "$dest/shadnet.db.age"
rm -rf "$tmp"
(cd "$dest" && sha256sum -- *.age > SHA256SUMS)
find "$root" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
echo "backup: $dest ($(du -sh "$dest" | cut -f1))"
