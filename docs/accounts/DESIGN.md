# LBPPC accounts: design

Status 2026-11-17: Phases 0, 1, 3 and 4 live (accounts, two-factor, Discord, email, claims and staff pages, levels/profiles/photos/activity, profile editing, hearts, following). Phase 2 built and tested, waiting for the shadNet and Refresh restart (server/linux/restart-day.sh) and the launcher release (then accounts/enable-phase2.sh). Phase 5 (forum) dropped for now. Security review: SECURITY-REVIEW.md.

One LBPPC account for everything: the game, the launcher, lbppc.com (with all of Refresh's features) and
the social features. Players sign in with their existing name and password, or with Discord. They can verify an
email address and apply to claim the levels they made on the original servers, and staff review the claims in an
admin dashboard.

Security is the first requirement. The rules this design keeps:

1. **No homemade security code.** Passwords, sessions, Discord sign-in, email verification, recovery and two-factor
   come from a proven, maintained identity server. We write the glue, never the cryptography.
2. **One place knows passwords.** Today shadNet and Refresh each keep their own. After this, neither does.
3. **Every service checks every request itself.** A token is checked by whoever receives it; nothing is trusted
   because of where it came from (the 2026-11-13 incident came from trust like that).
4. **Staff power is limited and recorded.** Staff need two-factor, see only what their role allows, and every action
   they take is logged with who, what and when.
5. **Nothing goes live without review.** Each phase passes the checklist in [Security review](#security-review)
   before players can use it.

## Where we are today

| Part | What it does now | Problem |
|---|---|---|
| shadNet (VPS, SQLite `shadnet.db`) | Accounts made in the launcher: name and password, for the game's sign-in, friends and co-op | Passwords are 100,000 rounds of plain SHA-256: much faster to guess on a GPU than a modern hash. Session tokens come from a non-cryptographic random generator. The database file is readable by every user on the VPS. |
| Refresh (VPS, PostgreSQL) | Levels, profiles, photos; game login checked against shadNet with a single-use code | Its website login has a **second, separate password** (bcrypt, SHA-512 of the password sent from the browser), only allowed for names that exist on shadNet |
| lbppc.com (SKYLORD-2, static) | Information pages and downloads | No accounts. Runs on the home network. |

Findings to fix now, before any of the new work (Phase 0):

- `chmod 600 /srv/lbppc/shadnet/db/shadnet.db*`. It's `644` today. Only root and postgres can log in, so the risk is
  low, but password hashes must never be world-readable.
- shadNet's `Database::GenerateToken` (account tokens) and `GenerateSalt` use `QRandomGenerator::global()`, which is
  not cryptographically secure: its output can be predicted after enough of it has been seen. Change both to
  `QRandomGenerator::system()`, which the sign-in codes in `auth_code_store.cpp` already use.
- Turn on `LBPPC_REQUIRE_LOGIN_CODE=1` once 0.1.2 and older clients are gone (the address check it replaces can be
  fooled by players sharing an address).

## Architecture

```
                         Discord (OAuth 2)
                               |
 browser --> lbppc.com ----> accounts (Ory Kratos)  <---- password migration hook ---- shadNet's old hashes
   |         (web app)        |  identities, passwords, Discord, email, recovery, 2FA, sessions
   |            |             |
   |            |        Ory Hydra (OAuth 2 / OpenID Connect provider)
   |            |             |  tokens for the launcher, Refresh, shadNet
   |            v             v
   |        Refresh API  <-- checks tokens with Hydra (patched)
   |        shadNet      <-- checks tokens with Hydra (patched)
   |
 launcher --> browser sign-in (OAuth with PKCE) --> tokens --> game --> shadNet / Refresh
```

### Identity: Ory Kratos and Ory Hydra

[Ory Kratos](https://github.com/ory/kratos) (identity) and [Ory Hydra](https://github.com/ory/hydra) (OAuth 2 and
OpenID Connect provider): open source (Apache-2.0), written in Go, widely deployed, OpenID-certified (Hydra), with
security audits and a published vulnerability process. Together they use about 100–200 MB of memory and keep their
data in the PostgreSQL we already run (their own databases and users).

What they give us without writing it:

- Password sign-in with Argon2id hashing, checks against breached-password lists (Have I Been Pwned's k-anonymity
  API: only a hash prefix leaves the server), and protection against guessing and against finding out which names
  exist.
- Discord sign-in (a built-in provider), and linking Discord to an existing account.
- Email verification and account recovery by email, with single-use, expiring codes.
- Two-factor: authenticator apps (TOTP), passkeys/security keys (WebAuthn) and recovery codes.
- Sessions with secure cookies, CSRF protection, and a list of signed-in devices players can sign out of.
- OAuth 2 / OpenID Connect for everything else (Hydra): the launcher, Refresh and shadNet all get tokens
  from one place, and nobody else ever sees a password.

Alternatives considered: Keycloak (proven, but a JVM using about 1 GB of memory, heavy for a 2-core VPS), Authentik (similar weight), and writing our own (ruled out by rule 1).

### Existing accounts keep working

Each shadNet account is imported into Kratos with its name, its shadNet user ID (kept as the link to shadNet and
Refresh) and no password hash, marked to use Kratos's **password migration hook**. The first time a player signs in,
Kratos asks our hook to check the password; the hook checks it against the shadNet hash (100,000 rounds of SHA-256,
`database.cpp HashPassword`), and on a match Kratos stores the password as Argon2id. After that the old hash is
deleted. Nobody has to reset anything, and the weak hashes disappear as players sign in.

The hook:
- Listens on 127.0.0.1 only and requires a shared secret from Kratos.
- Compares hashes in constant time and keeps Kratos's rate limits in front of it.
- Enforces the password rules itself, since Kratos doesn't apply its password policy to migrated passwords.
- Is deleted, along with the remaining old hashes, after a cut-off date. Players who never signed in by then use
  email recovery, or ask staff.

Refresh's separate website passwords are not migrated. The account password replaces them.

### Names

The account service owns names. A name is unique across everything, checked against the blocked-names list
(`/etc/lbppc/blocked-names.txt`), and changed only through the account service, which renames it in shadNet and
Refresh in one step (as `security/rename_account.py` does today). Discord-only accounts pick a name when they sign
up; their Discord name is never used as their LBPPC name automatically.

Every account has a permanent internal ID. shadNet and Refresh store that ID beside their own user, so a rename never
breaks the link.

### The launcher and the game

The launcher signs in through the browser: OAuth 2 authorization code with PKCE, the standard method for desktop apps
([RFC 8252](https://www.rfc-editor.org/rfc/rfc8252)). It opens the sign-in page, the player signs in there (password,
Discord, two-factor), and the launcher receives tokens on a local loopback address. The launcher never sees a password,
which is what makes Discord-only accounts and two-factor work in the game.

- The refresh token is stored with the operating system's credential store: Windows Credential Manager (DPAPI), and
  the Secret Service on Linux. It is not stored in `launcher.json`.
- For each game start, the launcher gets a short-lived access token, valid for minutes, and passes it to the game.
- **shadNet** (patched) accepts that token in place of a password and checks it with Hydra: the token must be valid,
  unexpired and issued to the launcher, and the account must not be banned.
- **Refresh**'s game login keeps the single-use code from the shadNet session, so nothing changes there.

Old launchers (0.1.4 and earlier) keep signing in with name and password until a cut-off date. shadNet checks those
passwords with Kratos's API instead of its own database. After the cut-off, only token sign-in is accepted.

### lbppc.com

lbppc.com becomes a web app: the current pages, plus sign-in, account settings, Refresh's features, claims and the
admin dashboard.

- **It moves to the VPS.** Sign-in pages and session cookies must not be served from the home network: a compromise
  of SKYLORD-2 would then be a compromise of every account. The static pages can stay where they are, served
  from the VPS too.
- **A small backend runs beside it** (a backend-for-frontend): it holds the player's session and talks to Refresh's
  API v3 with the player's token. The browser never holds API tokens. Cookies are `Secure`, `HttpOnly` and
  `SameSite=Lax`, with CSRF tokens on every change.
- **Refresh's features through its API:** levels, search, profiles (bio and avatar, ticket #19), photos, hearts,
  queue, comments, activity, and the player's own settings such as showing re-uploads. Refresh is patched to accept
  Hydra tokens for API v3 and to turn off its own website passwords.
- **Headers:** a strict Content-Security-Policy (no inline scripts, no third-party scripts), HSTS with preload,
  `frame-ancestors 'none'`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Uploads** (avatars, photos) go through Refresh's existing asset checks, which only accept images and block
  dangerous asset types.

Hosts: `lbppc.com` (site and app), `account.lbppc.com` (Kratos's pages and API), `auth.lbppc.com` (Hydra),
`admin.lbppc.com` (dashboard). Cookies are scoped to their own host, never to all of
`.lbppc.com`, so a problem in one can't read another's session.

### Claiming archived levels

Archived levels have no owner in Refresh: `PublisherUserId` is empty, `IsReUpload` is set, and `OriginalPublisher`
holds the PSN name of whoever made them (10.45 million levels by about 2.1 million names, measured 2026-11-16). A claim,
once approved, gives the player's Refresh account every unowned level with that original creator. (The `!name` users in
Refresh are something else, and only 7,881 of them exist.)

**To apply** (`lbppc.com/account/claims`):
1. The player must have a verified email address and two-factor turned on, so a stolen password alone can't make a
   claim.
2. They enter their old PSN name, exactly as written (the lookup is by exact name: the index is case-sensitive, and
   PSN keeps the case of a name). The site checks it made unowned levels and that no live claim exists for it.
3. They get a code (`LBPPC-XXXXXX`, random, valid 14 days) to put in their PSN profile's About Me.
4. They can add a note for staff. Limits: 3 waiting claims per player, 10 a month.

**Staff review** (`lbppc.com/admin`):
- The reviewer sees the claim, a sample of the levels, the code, and a link to the PSN name on PSNProfiles.
- Claims for more than 50 levels, or any team-picked level, need two different staff. Nobody can approve their own.
- Approving or rejecting needs a sign-in from the last 15 minutes (with the second factor).

**Approval** moves the levels in one transaction on Refresh's database, done by a role that can change only a
level's owner and re-upload flag, and records exactly which levels moved (`claim_levels`). The player must have
signed in to the game once (so their Refresh account exists). If saving the decision fails after the move, the
levels are put straight back. Admins can undo an approved claim, which returns exactly those levels to the archive.

Automatic code checking (reading the PSN profile through Sony's unofficial API) is left out on purpose. It needs a
PSN account's session token on our server, and breaks without warning. Staff checking by hand is slower, but safer.

### Admin dashboard

At `admin.lbppc.com`, inside the same web app, with its own checks:

- **Roles:** moderator (claims, reports, hiding content, temporary bans) and admin (undoing claims, unbans, renames,
  deletes accounts). Roles are kept in lbppc.com's own database and given only on the server
  (`staff.py`), never from the website, so a stolen staff session can't make more staff.
- **Every staff session needs two-factor,** passkeys preferred. Staff sessions are shorter, about 8 hours, and
  dangerous actions (bans, deletions, approvals, role changes) ask for two-factor again.
- **Sections:** claims queue, user lookup (name, linked Discord, verified email shown partly hidden, claims,
  sign-in history), moderation (reports from Refresh, hidden content), the audit log, and server status.
- **Audit log:** append-only. The web app's database user can insert into it but not change or delete it. Every entry
  records who, what, target, when, from where, and the reason typed.
- **Never shown:** password hashes, tokens, full email addresses (unless an admin asks, which is itself logged) and
  IP addresses beyond what moderation needs.

### Forum (deferred)

Not planned for now (decision 7). If it's picked up later, it will be part of lbppc.com itself (`lbppc.com/forum`), in the same web app: same design, same sign-in, moderated from the
admin dashboard. No separate forum software.

- **Structure:** categories (staff-managed), threads, replies, reactions, pinned and locked threads, search, and
  notifications for replies and mentions.
- **Posts are Markdown,** rendered on the server and cleaned with `rehype-sanitize` (an allow-list of tags and
  attributes, no HTML from players, no `javascript:` links). Images only from our own upload store, after the
  same image checks as avatars. The site's Content-Security-Policy stops any script that gets through regardless.
- **Spam and abuse:** posting needs a verified email; new accounts have stricter rate limits and can't post links
  for their first day; reports go to the moderation queue; moderators can hide, lock, move and delete, and ban from
  the forum only. Every moderation action goes into the audit log.
- **Data:** its own tables in the web app's database. Deleted posts are kept hidden for 30 days for moderation,
  then removed.

### Social

Built last, from what already exists:
- Friends and presence come from shadNet.
- Hearts, comments and activity come from Refresh.
- Following, a profile page that combines them, notifications, and privacy settings (who can see you online, who
  can send friend requests) are new.

Blocking and reporting work across all of it.

## Email

Verification, recovery and claim emails go through a transactional email service (for example Postmark or Amazon
SES). Their sending key stays on the VPS in a `600` file, the domain has SPF, DKIM and DMARC records, and emails
never contain passwords or long-lived links. Kratos's links and codes expire within an hour.

## Data and privacy

- **Collected:** name, password hash, email (optional, except for claims), Discord ID (if linked), linked PSN name
  (after a claim), staff audit records, and sign-in history (time, rough location, device) kept for 90 days.
- **Deleting an account** deletes the identity and its links, and clears Refresh and shadNet as
  `delete_account.py` does today. Audit entries about staff actions are kept, with the player's name removed.
- **The privacy page** (`privacy.html`) is updated before launch with what's collected, why, and for how long.

## Hosting and operations

- **On the VPS:** Kratos, Hydra, the web app and its backend, and the migration hook. Everything runs as
  its own system user, with secrets in `600` files, and every internal port bound to 127.0.0.1 behind nginx.
- **Databases:** each service gets its own PostgreSQL database and user, with only the rights it needs.
- **Backups:** encrypted (`age`) nightly dumps, copied off the VPS, with a restore test each month.
- **Updates:** Ory and the web app's dependencies pinned to exact versions and updated on purpose. Security advisories
  for each are watched.
- **Monitoring:** failed sign-ins, migration hook use, claim volume, and alerts on spikes.

## Phases

Each phase is usable on its own and goes live only after its security review.

| Phase | What | Needs |
|---|---|---|
| 0 | The fixes above: database permissions, the token random generator, require sign-in codes | A shadNet rebuild |
| 1 | Accounts: Kratos and Hydra; the shadNet import with the migration hook; account.lbppc.com (sign-in, sign-up, Discord, email verification, recovery, 2FA, devices); shadNet and Refresh check tokens; lbppc.com moves to the VPS | shadNet and Refresh patches |
| 2 | Launcher sign-in through the browser | An LBPPC release |
| 3 | Claims and the admin dashboard | The Refresh merge endpoint |
| 4 | Refresh's features on lbppc.com | The web app |
| 5 | Forum, in the web app (deferred, not planned for now) | |
| 6 | Social | |

## Security review

Before each phase goes live:

- [ ] A threat model for its new parts: what an attacker wants, how they'd get it, what stops them.
- [ ] Every endpoint checks the session or token and the role itself, with tests that try each one without, and with
  someone else's.
- [ ] Rate limits on sign-in, sign-up, recovery, verification, claims and staff actions, tested.
- [ ] No secrets in the repo, logs, error pages or client code (a secret scanner in CI).
- [ ] Player content (forum posts, bios, level text) is shown only through the sanitizer, with tests that try script
  injection.
- [ ] Dependencies scanned (`govulncheck`, `npm audit` or the equivalent) and pinned.
- [ ] Headers checked: CSP, HSTS, cookies (Mozilla Observatory).
- [ ] A test restore of the backup.
- [ ] The first launch (phases 1–3): an outside review by someone who didn't write it, ideally a paid penetration
  test, or at least a security-minded developer from the community going through this checklist and the code.

## Decisions (2026-11-16)

1. lbppc.com moves from SKYLORD-2 to the VPS.
2. Email: Amazon SES.
3. Claims require a verified email and two-factor.
4. Claims are reviewed by Ethan and the staff (two approvers for large placeholders).
5. Old launchers' password sign-in and the migration hook end 3 months after Phase 1 goes live.
6. The web app is React, with React Router in framework mode (server rendering and the backend-for-frontend in one
   Node process, about 100 MB), TypeScript throughout.
7. No forum for now. If one is added later, it will be built into lbppc.com, not Discourse.
