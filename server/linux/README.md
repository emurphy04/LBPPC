# Hosting an LBPPC server on Linux

Two programs make an LBPPC server:

- **Refresh**: LittleBigPlanet's levels, profiles and matchmaking. The game talks to it over HTTPS, through a
  reverse proxy (Nginx Proxy Manager or plain nginx) that holds the certificate.
- **shadNet**: sign-in, friends and the signaling that connects players for multiplayer. Its own TCP and UDP
  ports, not through the proxy.

Gameplay itself is peer to peer between the players; it does not pass through the server.

Tested target: Ubuntu 24.04 / Linux Mint 22 (glibc 2.38 or newer). Check with `ldd --version`.

Copy this folder (`server/linux`) to the server first; the commands below use it as `$KIT`:

```bash
KIT=~/lbppc-server        # where you copied server/linux
```

## 1. Packages and a user

```bash
sudo apt update
sudo apt install -y postgresql libicu74 unzip curl python3-venv
sudo useradd --system --create-home --home-dir /opt/lbppc --shell /usr/sbin/nologin lbppc
```

## 2. Database

```bash
sudo -u postgres psql -c "CREATE USER refresh WITH PASSWORD 'pick-a-password';"
sudo -u postgres psql -c "CREATE DATABASE refresh OWNER refresh;"
```

Put the same password in `$KIT/refresh/db.json` in place of `CHANGE_ME`.

Check the database's port with `pg_lsclusters`: it is 5432 unless something else (another PostgreSQL, a Supabase
stack in Docker) already used that port at install time, and then usually 5433. Put that port in `db.json` too;
on the wrong one Refresh reaches the other database and stops with `password authentication failed`.

Write `db.json` with a password of letters and digits only (no `;`, `"` or `\`).

## 3. Refresh

**A server other people can reach must run Refresh built with `refresh/lbppc-refresh-security.patch`** (see
[Security](#security)). LBPPC's game makes its own login ticket, which proves nothing about who sent it, and this
configuration has Refresh's ticket check off so that LBPPC can sign in at all; without the patch anyone can sign in
as anyone by typing their name. That is what happened to play.lbppc.com on 2026-11-13. The stock release below is
for a private test server on your own network only.

Built from source (what play.lbppc.com runs: commit `4060ec4`, in `/opt/refresh-src` on the VPS), with the patches in
this order:

1. `refresh/lbppc-refresh.patch`: archived levels' files fetched from the archive's mirrors, and level and user
   lists that stay fast with the archive's millions of levels.
2. `refresh/lbppc-refresh-asset-types.patch`: those files are served only when they are a type the game itself
   shares (the rule `BlockedAssetFlags` applies to uploads).
3. `refresh/lbppc-refresh-security.patch`: a game login only while its account is signed in to shadNet from the
   same address; website sign-up only for names that have an LBPPC (shadNet) account and that the blocked-names
   list allows. Needs the shadNet security patch (section 4) and the settings in [Security](#security).

```bash
git clone https://github.com/LittleBigRefresh/Refresh /opt/refresh-src && cd /opt/refresh-src && git checkout 4060ec4
for p in lbppc-refresh lbppc-refresh-asset-types lbppc-refresh-security; do git apply $KIT/refresh/$p.patch; done
dotnet publish Refresh.GameServer -c Release -o /opt/refresh-build
sudo -u lbppc mkdir -p /opt/lbppc/refresh
sudo cp -r /opt/refresh-build/. /opt/lbppc/refresh/
sudo cp $KIT/refresh/*.json /opt/lbppc/refresh/
sudo chown -R lbppc:lbppc /opt/lbppc/refresh
```

A private test server can use the release instead (`Refresh_for_Linux_x64.zip` from Refresh's v3.7.2 release,
unzipped to `/opt/lbppc/refresh` with the .json files) and `LBPPC_SHADNET_CHECK_URL=off`.

The configuration here keeps the digest key LBPPC's game is patched to (`CustomServerDigest`), and uses
`https://play.lbppc.com` as the public address: change `ExternalUrl`, `WebExternalUrl` and `GameConfigStorageUrl`
for another domain. Refresh listens on port 10061 on every interface so the proxy can reach it; the router does
not forward that port.

## 4. shadNet

The shadNet build needs Qt 6.10, newer than the distribution's, so Qt is installed beside it:

```bash
python3 -m venv /tmp/aqt && /tmp/aqt/bin/pip install aqtinstall
sudo /tmp/aqt/bin/aqt install-qt linux desktop 6.10.0 linux_gcc_64 -m qthttpserver qtwebsockets -O /opt/lbppc/qt
cd /tmp
curl -LO https://github.com/shadps4-emu/shadNet/releases/download/pre-release-shadnet-2026-11-03-1c64b8c8d17c488ddc98308d968e3fa77a9aa65c/shadnet-linux64-2026-11-03-1c64b8c.zip
unzip -o shadnet-linux64-2026-11-03-1c64b8c.zip && tar xzf shadnet-linux64.tar.gz
sudo mkdir -p /opt/lbppc/shadnet
sudo cp shadnet /opt/lbppc/shadnet/ && sudo chmod +x /opt/lbppc/shadnet/shadnet
sudo cp $KIT/shadnet/*.cfg /opt/lbppc/shadnet/
sudo chown -R lbppc:lbppc /opt/lbppc/shadnet /opt/lbppc/qt
```

`shadnet.cfg` listens on every interface (`Host=0.0.0.0`); its admin API stays on 127.0.0.1.

The release build above lacks LBPPC's changes, so **a server other people can reach builds shadNet from source**
at commit 75edbf0 with both patches applied (`git apply`, in this order), and uses that `shadnet` binary instead:

1. `shadnet/patches/lbppc-shadnet.patch`: a relay for players who can't reach each other directly (two players
   behind one router, or a router without UPnP), and LBP3 players counting as being in the same game, which the
   in-game friends list needs. UDP 31314 must be open for the relay.
2. `shadnet/patches/lbppc-shadnet-security.patch`: what Refresh's login check asks (`/admin/v1/sessions/check`,
   `/admin/v1/names/check`, only from this machine with the AdminApiKey), the player's real address through nginx
   (PROXY protocol), the blocked-names list, limits on password guessing and sign-ups, and 8-character passwords.
   See [Security](#security).

## 5. Services

```bash
sudo cp $KIT/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now refresh shadnet
systemctl status refresh shadnet          # both "active (running)"
journalctl -u refresh -f                  # the logs (Ctrl+C to stop)
curl http://localhost:10061/api/v3/instance
```

## 6. Network

| Port | Protocol | To | What |
|---|---|---|---|
| 80, 443 | TCP | the proxy | Refresh over HTTPS, and the certificate's renewal |
| 31313 | TCP | this machine | shadNet |
| 31314 | UDP | this machine | shadNet matching |
| 31315 | TCP | this machine | shadNet web API |

Forward them on the router, and open them in the firewall if it is on (`sudo ufw allow 31313/tcp`,
`31314/udp`, `31315/tcp`).

In Nginx Proxy Manager, the proxy host for `play.lbppc.com` forwards to `http`, this machine's **LAN address**
(not `localhost` when the proxy runs in Docker) and port `10061`, with Websockets Support on.

## 7. Accounts

From a PC with LBPPC (the same online name on both servers):

```bash
python tools/shadnet_account.py <name> --server play.lbppc.com:31313
python tools/refresh_account.py <name> --server https://play.lbppc.com
```

In the launcher: Play online, server `https://play.lbppc.com`, shadNet server `play.lbppc.com:31313`.

## Security

What each server trusts, and why nobody else can send it that. Check this list again before changing either
server's configuration, its patches or its network setup.

| Server | Trusts | Because |
|---|---|---|
| shadNet | a name and password | the password (salted, 100,000 SHA-256 rounds); guessing is limited (below) |
| Refresh, game login | a ticket the game makes itself, with any name | a single-use code from that account's own shadNet session (0.1.3+), or, for older clients, that account signed in to shadNet from the same address (the security patch); RPCN logins are off (`PermitRpcnLogin: false`) |
| Refresh, website | an email address and password | the password (bcrypt); 10 tries per 5 minutes per address |
| Refresh, website sign-up | a name, email and password | only for names with a shadNet account, which the launcher creates first, and that the list allows |
| shadNet admin API | the AdminApiKey | it listens on 127.0.0.1 only; the session and name checks also refuse anything not from this machine |
| shadNet, player address | the PROXY line nginx sends | only from 127.0.0.1 (nginx); port 31316 is closed in the firewall |

### Setting it up

1. Both servers built with their security patches (sections 3 and 4).
2. Refresh's settings (`systemd/refresh-login-check.conf`, `security/refresh-login-check.env.example`):

   ```bash
   sudo mkdir -p /etc/lbppc && sudo chmod 711 /etc/lbppc
   sudo install -m 600 $KIT/security/refresh-login-check.env.example /etc/lbppc/refresh-login-check.env
   sudo nano /etc/lbppc/refresh-login-check.env      # LBPPC_SHADNET_KEY = shadnet.cfg's AdminApiKey
   sudo mkdir -p /etc/systemd/system/refresh.service.d
   sudo cp $KIT/systemd/refresh-login-check.conf /etc/systemd/system/refresh.service.d/login-check.conf
   sudo systemctl daemon-reload
   ```

   `/etc/lbppc` is 711 so that shadNet (user `lbppc`) can read the names list in it but not list the folder; the
   key file stays 600. If shadNet can't read the list, it refuses every new name rather than accept any (its log
   says so).
3. The blocked-names list: `sudo install -m 644 $KIT/security/blocked-names.txt /etc/lbppc/blocked-names.txt`.
   Edit it on the server at any time; both servers use it from the next sign-up. Test a change first:
   `python3 security/name_check.py blocked-names.txt < names.txt` (one name per line) shows what it would block.
   A list word must not overlap an allowed (`+`) word in a way that lets a slur through: `+snigg` would have let
   "snigga" in.
4. If shadNet is behind nginx for TLS, `nginx/lbppc-shadnet-stream.conf` (`proxy_protocol on`). Without it every
   player looks like 127.0.0.1 and **every game login is refused**. Players connecting straight to shadNet (no
   nginx in between) need nothing here.
5. After any change, check from the server:

   ```bash
   KEY=$(sudo sed -n 's/^LBPPC_SHADNET_KEY=//p' /etc/lbppc/refresh-login-check.env)
   curl -s -H "X-Admin-Api-Key: $KEY" 'http://127.0.0.1:31350/admin/v1/names/check?npid=Diddy'   # "blocked":"diddy"
   journalctl -u refresh --since -10min | grep -E 'successfully logged in|Rejecting'               # players get in
   ```

### Limits (in shadNet, `lbppc_guard.h`)

- Wrong passwords within 15 minutes lock logins for 15 minutes: 10 for one account from one address, 20 from one
  address across accounts, 50 for one account from anywhere. One person can't lock someone else out with a few tries.
- 3 new accounts per address per 24 hours. Passwords of at least 8 characters.

### Accounts

- Delete: `delete_account.py NAME` (both servers; the Refresh name is freed for the player to sign up again, or kept
  with `--keep-names`). Refresh's own `./Refresh.GameServer --delete-user --username NAME` deletes on Refresh only
  and keeps the name reserved: right for trolls.
- Rename: `security/rename_account.py OLD NEW` (both servers at once; they must keep the same name, or the player
  can't sign in to the game). The player must be offline. Tell them the new name: they type it in the launcher.
- Admin (moderation on the website): `./Refresh.GameServer --set-admin --username NAME`, run in Refresh's folder as
  the `lbppc` user. Keep admins to people who need it: an admin's password is the most valuable one on the server.

### Sign-in codes (LBPPC 0.1.3 and later)

The game asks its own shadNet session (signed in with the player's password) for a single-use code (client id
`lbppc-refresh`, 6 characters, 5 minutes) and puts it in the login ticket's serial (`lbppc-code:XXXXXX`). Refresh
redeems it with shadNet (`/admin/v1/authcodes/redeem`), which says whether that account's session asked for it; a
code that doesn't check out is refused. This proves who a login is even for players who share an address.

Logins without a code (LBPPC 0.1.2 and earlier) get the address check instead: two players sharing one public
address (the same VPN exit, a mobile network, one household) look the same to it, so while one is signed in the
other could start a game login under their name. Once players have updated, turn that off by adding
`LBPPC_REQUIRE_LOGIN_CODE=1` to `/etc/lbppc/refresh-login-check.env` and restarting Refresh: then every game login
needs a code. Refresh's log shows which kind each refused login was.

## Backups

The data is the PostgreSQL database and the folders under `/opt/lbppc` (uploaded levels and assets in
`refresh/`, shadNet's accounts in `shadnet/db/`):

```bash
sudo -u postgres pg_dump refresh > refresh-$(date +%F).sql
sudo tar czf lbppc-data-$(date +%F).tar.gz /opt/lbppc/refresh /opt/lbppc/shadnet
```
