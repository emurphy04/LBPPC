#!/bin/bash
# Restart day: swaps in the new shadNet and Refresh builds together, at a quiet moment (players are dropped for a few
# seconds and reconnect on their own). Run as root on the VPS. Settings and data are never touched.
#
#   shadNet: /opt/shadnet-src/build/shadnet, with lbppc-shadnet-random, -tokens and -accounts patches (sign-in with
#            LBPPC account tokens from the launcher; the account endpoints the website's hooks use)
#   Refresh: /opt/refresh-build-next (self-contained), with lbppc-refresh-search (search in one database query) and
#            lbppc-refresh-activity (activity pages no longer fail on events about deleted things)
#
# The programs being replaced are kept, and the rollback commands are printed at the end. This does NOT turn on
# sign-up, password changes or recovery on the website: that's accounts/enable-phase2.sh, after the launcher release.
set -euo pipefail
stamp=$(date -u +%Y%m%d-%H%M)
shadnet_new=/opt/shadnet-src/build/shadnet
refresh_new=/opt/refresh-build-next
[[ -x $shadnet_new && -x $refresh_new/Refresh.GameServer ]] || { echo "a new build is missing" >&2; exit 1; }

echo "players online now: $(ss -tn state established '( sport = :31316 )' | tail -n +2 | wc -l) shadNet connections"
read -r -p "Restart shadNet and Refresh now? [y/N] " answer
[[ $answer == [yY] ]] || { echo "not restarted"; exit 0; }

# Keep what's running now.
cp -p /srv/lbppc/shadnet/shadnet "/srv/lbppc/shadnet/shadnet.before-$stamp"
backup=/srv/lbppc/refresh-binaries.before-$stamp.tgz
(cd /srv/lbppc/refresh && tar -czf "$backup" $(cd "$refresh_new" && ls))
echo "kept: /srv/lbppc/shadnet/shadnet.before-$stamp and $backup"

# Swap: the program files only (the new build has no settings or data in it).
install -o lbppc -g lbppc -m 755 "$shadnet_new" /srv/lbppc/shadnet/shadnet.new
mv /srv/lbppc/shadnet/shadnet.new /srv/lbppc/shadnet/shadnet
rsync -a --chown=lbppc:lbppc "$refresh_new"/ /srv/lbppc/refresh/
systemctl restart lbppc-shadnet lbppc-refresh

echo "waiting for both to come back..."
for i in $(seq 1 30); do
    sleep 2
    if systemctl is-active -q lbppc-shadnet && curl -sf -o /dev/null http://127.0.0.1:10061/api/v3/instance; then break; fi
done
systemctl is-active lbppc-shadnet lbppc-refresh
curl -s -o /dev/null -w "Refresh API: %{http_code}\n" http://127.0.0.1:10061/api/v3/instance
curl -s -o /dev/null -w "search 'shark': %{time_total}s\n" "http://127.0.0.1:10061/api/v3/levels/search?query=shark&count=24"
sleep 20
echo "shadNet sign-ins since restart: $(journalctl -u lbppc-shadnet --since "-1min" --no-pager | grep -c 'Authenticated:')"
echo "Refresh errors since restart: $(journalctl -u lbppc-refresh --since "-1min" --no-pager | grep -c -i 'exception')"
cat <<EOF

Rollback (if anything is wrong):
  cp -p /srv/lbppc/shadnet/shadnet.before-$stamp /srv/lbppc/shadnet/shadnet
  tar -xzf $backup -C /srv/lbppc/refresh
  systemctl restart lbppc-shadnet lbppc-refresh
EOF
