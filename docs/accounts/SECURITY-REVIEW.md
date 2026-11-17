# LBPPC accounts and lbppc.com: security review

Status 2026-11-17. What has been checked, how, and what's left for an outside reviewer. The design is
[DESIGN.md](DESIGN.md); the server kit is `server/linux/accounts`; the web app is `web/` in the lbppc.com repo
(emurphy04/LBPPC-Site, private).

## What's running (VPS)

| Part | Listens on | Runs as |
|---|---|---|
| nginx (TLS for lbppc.com, auth.lbppc.com, play/api/shadnet) | 80, 443, 31313 (all addresses) | www-data |
| lbppc-web (React Router, Node 24) | 127.0.0.1:3000 | lbppc-web |
| Ory Kratos (accounts) | 127.0.0.1:4433 public, :4434 admin | ory-kratos |
| Ory Hydra (OAuth) | 127.0.0.1:4444 public, :4445 admin | ory-hydra |
| Password migration hook | 127.0.0.1:4480 | lbppc |
| Account hooks (Phase 2, not started) | 127.0.0.1:4481 | lbppc-hooks |
| PostgreSQL | local only | postgres |
| shadNet | 31316, 31315, 31320 on all addresses, but the firewall allows only 31313 (nginx TLS) and 31314/udp | lbppc |
| Refresh | 10061 (firewalled; reached through nginx at play.lbppc.com) | lbppc |

Firewall (ufw): 22, 80, 443, 31313/tcp and 31314/udp only. SSH: keys only (no passwords), fail2ban running, no
failed logins in the last day. Automatic security updates on, none pending. **A reboot is pending** for an installed
update (do it at a quiet moment).

## Verified (by test, 2026-11-16/17)

Sign-in and accounts:
- Old shadNet passwords: migrated on first sign-in through the hook, which refuses requests without its key, wrong
  paths and oversized bodies, never matches banned accounts, and locks an account after 10 wrong guesses in 15 minutes.
- Sign-in through nginx: wrong passwords refused, a sign-in without the CSRF token refused, Kratos's session and admin
  routes unreachable from outside (404), Hydra's admin API unreachable from outside.
- Rate limits: Kratos form posts are limited per address (a burst of 5, then 429), as are Hydra's token endpoint and
  pages. Self-hosted Kratos has no limits of its own: nginx provides them.
- The launcher's OAuth sign-in: PKCE is enforced (no PKCE: no code; wrong verifier: the code is refused), tokens carry
  the right name and shadNet ID, revoked tokens are inactive, refresh tokens rotate, and a signed-out session can't be
  refreshed.
- shadNet token sign-in (patch built and tested on a copy, not live): a valid token signs in; another account's token,
  a made-up token and a revoked token are refused; passwords are compared in constant time.
- Site sign-up and password-change hooks (built and tested on a copy, off until Phase 2): taken and blocked names are
  refused with a message; a password change turns the old shadNet password off.

The website:
- Every page: a Content-Security-Policy with a per-request nonce (no inline scripts or third-party scripts),
  `frame-ancestors 'none'`, HSTS, nosniff, `Referrer-Policy`, `Cache-Control: no-store` on pages.
- Forms: a forged cross-site post is refused (React Router's own origin check, and `assertSameOrigin`); posts without
  an Origin header are refused.
- Redirects after sign-in only go to paths on lbppc.com (`//evil.com` is replaced).
- Player-made text (level titles, descriptions, bios, claim notes) is rendered as text: a `<script>` in a note shows
  as text.
- Images come only through `/img/<sha1>` (hash-checked, image types only, sandboxed CSP, 8 MB cap).

Databases:
- lbppc_web: the app's role can add to and read the audit log but not change or delete it; can't add staff (staff are
  added only on the server, `staff.py`); can't change the schema.
- Refresh: the app's role (`lbppc_web_refresh`) can read a few columns and change only a level's owner and re-upload
  flag (claims); it can't read emails, password hashes or token secrets, change titles or roles, or delete levels. Other
  database users can't connect to Refresh's database at all.
- Refresh tokens made for players by the website (row-level security): website tokens only; 2 hours at most for
  players; 10 minutes at most for staff, and only for a staff session with two-factor and a sign-in within 15 minutes,
  used only to save their own profile; none for deleted accounts.

Claims: two approvers for large claims, nobody approves their own, decisions need a recent sign-in, the level move is
one transaction that records which levels moved (and is put back if recording fails), admins can undo.

Secrets: no keys, tokens, passwords or private keys in either repository's history (pattern scan). Server secrets live
in root-owned files (`/etc/lbppc/...`, mode 600/640). Backups are encrypted to a key only on Ethan's PC.

## Accepted risks

- **Staff tokens.** Whoever controls the web app could make a 10-minute Refresh token for a staff account. Accepted by
  Ethan for staff profile editing, with the limits above.
- **The web app is trusted with claims**: it can move unowned archived levels to a player (only those, by design).
- **Old shadNet password hashes** (100,000 rounds of SHA-256) remain until each player signs in to the site once, or
  until the cut-off (3 months after Phase 2), when the rest are deleted.
- **Refresh trusts `X-Forwarded-For`** (`UseForwardedIp`). Safe while port 10061 is firewalled and nginx overwrites the
  header; keep it that way.
- **Backups exist only on the VPS** until there's somewhere to copy them.
- **Passkeys and security keys** are off until the site's pages support them.

## For an outside reviewer

Please try, against lbppc.com (ask Ethan for a test account rather than using your own):
1. Signing in as someone else; reaching `/admin` or `/admin/claims/<id>` without staff rights or without two-factor.
2. CSRF on every form: settings, claims, profile, heart/queue, follow, the staff decisions.
3. Open redirects through `return_to` on `/account/login`, and through the OAuth flow at auth.lbppc.com.
4. Stored XSS through anything a player can write that the site shows: level titles and descriptions (from the game),
   bios, claim notes, profile pictures (try SVG and polyglot files on `/account/profile`).
5. The OAuth client: redirect URI tricks, PKCE downgrade, code reuse, refresh token reuse.
6. Rate limits: sign-in guessing, claim spam, follow spam.
7. Anything reachable on the VPS's address beyond 22, 80, 443, 31313 and 31314/udp.

Report privately to Ethan (Discord DM), not in public channels.
