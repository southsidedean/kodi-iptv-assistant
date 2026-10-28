# One login for the stack

A full stack has a lot of pages that each want a password: kia's Dashboard, Grafana, Threadfin.
The front door puts one login in front of them. You sign in once, and the pages know who you
are without asking again. It's optional, off until you turn it on, and works on your own network
with no accounts in the cloud.

Two pieces make it work, both already in kia's stack:

- A small identity provider ([Authelia](https://www.authelia.com)) that holds your household's
  accounts and checks passwords. It's the only part that ever handles a password.
- The gateway (the same one that sits in front of kia's MCP server), which asks you to log in
  and then tells each page who you are.

## What you need

- The `gateway` part, which the front door turns on for you.
- Two host names under one parent domain: one for kia's pages and one for the login, such as
  `kia.my-server.lan` and `auth.my-server.lan`. They can point at the same computer. The second
  name is for the login itself, which needs a steady address of its own.
- A way for your devices to reach those names: an entry on your network's DNS, or a line in each
  device's `hosts` file. kia's certificate has to cover both names, which `kia tls` handles.

## Turn it on

1. Put the two host names in `.env`:

   ```sh
   KIA_FRONTDOOR_HOST=kia.my-server.lan
   KIA_FRONTDOOR_AUTH_HOST=auth.my-server.lan
   ```

2. Add `frontdoor` to `KIA_STACK` in `.env` (it brings the gateway with it), then apply it:

   ```sh
   scripts/stack.sh apply
   ```

3. Set an account. It asks for a password, which it never shows and never keeps:

   ```sh
   scripts/frontdoor.sh account dad
   ```

4. Point `kia.my-server.lan` and `auth.my-server.lan` at the computer running kia (on your DNS,
   or in each device's `hosts` file), and trust kia's certificate the same way you do for the
   Dashboard.

Now open `https://kia.my-server.lan:8444`. It sends you to the login, and after you sign in, the
page opens. `scripts/frontdoor.sh list` shows the account that can log in.

## How the login works

When you open a page behind the front door, the gateway checks whether you're signed in. If you
aren't, it sends you to Authelia's login. Authelia checks your password, and the gateway reads
back who you are and adds that to the request as a header the page can trust (`X-User-Id` and
`X-User-Email`). The login token itself is stripped before the page sees it, so a page only ever
learns your name, never a credential.

By default the Dashboard still asks for its own password behind the front door, so there are two
logins. To make one login enough, set `KIA_TRUST_FRONTDOOR=true` in `.env`: then a person in the
`admins` group edits on the Dashboard without kia's own password, since the front door already
checked who they are. kia trusts that only because the gateway sends its own token with the
request, which proves it came through the front door; a direct connection to the web server,
which could set the same `X-User-*` headers itself, still needs the password. People who aren't
in `admins` can read the Dashboard but not change it. Leave it off and the Dashboard keeps its
own password as before. Bringing the stack's other pages (Grafana, Threadfin) behind the same
login comes next.

kia never hashes a password itself. When you set an account, kia asks Authelia's own tool to turn
the password into a hash, and keeps only the hash. The same goes for the gateway's own secret for
Authelia: Authelia makes the hash, and the plaintext stays in `frontdoor/`, readable only by you.

kia's own pages already set a strict content-security-policy and frame headers of their own, so
the gateway leaves those alone for the Dashboard. For other pages that don't set their own, a
later release has the gateway add them.

## Certificates

By default the front door uses kia's own self-signed certificate, so it works on a network with
no public name and nothing in the cloud. You trust that certificate once on each device, as you
already do for the Dashboard. A clean certificate from a public authority (Let's Encrypt or
ZeroSSL), so browsers trust it with no warning, is an optional upgrade covered later.

## Accounts

- `scripts/frontdoor.sh account NAME` sets the account's password. Run it again to change the
  password.
- `scripts/frontdoor.sh list` shows the account.

For now there's one shared household account. Separate accounts for each person, with roles, come
next.

## What's under the hood

Everything the front door needs is written into `frontdoor/` by `scripts/frontdoor.sh`: Authelia's
settings, the account (with its password as a hash Authelia made), and the secrets, all readable
only by you. The gateway and Authelia sit on the gateway's own network, which the rest of the
stack can't reach, so the only way in is through the front door's one port.
