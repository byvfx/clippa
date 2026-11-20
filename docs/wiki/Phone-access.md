The war room listens on localhost only. To open it from the phone anywhere, put it on your
tailnet with `tailscale serve`: HTTPS at the machine's tailnet name, reachable only by your
own Tailscale devices, nothing on the LAN or the internet, no port forwarding.

1. Install Tailscale on the machine that runs `clipper serve` (https://tailscale.com/download) and
   on the phone (App Store / Play Store), and sign in to the same account on both.
2. In the Tailscale admin console, DNS page: turn on MagicDNS and HTTPS certificates
   (`tailscale serve` asks for this the first time if it is off).
   The first `on` may also print a link to enable Serve for the tailnet: open it, allow, and
   the command carries on.
3. `clipper warroom --tailnet on`. It makes a war room token in `secrets/warroom_token.txt`
   if `[warroom] token` is empty, starts `tailscale serve` (it persists across reboots), and
   prints the link to open once on the phone (`https://<name>.ts.net/?key=...`). The key
   is printed only there; the phone keeps a cookie for a year.
4. Restart serve (`clipper services restart serve`) so the war room asks for the
   token, and set `[warroom] tailnet = true` in clipper.toml.
5. On the phone, open the link, then Share / menu → Add to Home Screen.

Check: `clipper doctor --role serve` (fetches the war room through the tailnet name), or
`clipper warroom --tailnet status`. Off again: `clipper warroom --tailnet off`.

## On the NAS

With serve on the NAS ([NAS hub](NAS-hub)), the `tailscale` container is the tailnet machine
(`clipper.<tailnet>.ts.net`, `TS_HOSTNAME` in `deploy/hub/env.local`). It needs signing in
once; the login lives in the `tailscale-state` volume after that. The easy way is an auth key
in `TS_AUTHKEY` before deploying. Without one, the container's own sign-in link is no use: its
startup gives up after about a minute and restarts with a new link. Sign in from a one-off
daemon on the same volumes instead, then start the service:

```bash
ssh nas 'cd clipper-hub && docker compose stop tailscale && docker run -d --name ts-login --network host -v clipper-hub_tailscale-state:/var/lib/tailscale -v clipper-hub_tailscale-socket:/var/run/tailscale --entrypoint tailscaled tailscale/tailscale:v1.104.4 --tun=userspace-networking --statedir=/var/lib/tailscale --socket=/var/run/tailscale/tailscaled.sock'
```

```bash
ssh nas 'docker exec ts-login tailscale --socket=/var/run/tailscale/tailscaled.sock up --hostname=clipper --timeout=0'
```

Open the link it prints and approve. If it still says logged out afterwards, restart `ts-login`
and run `up` again (the first approval here didn't reach it). Then:

```bash
ssh nas 'docker rm -f ts-login && cd clipper-hub && docker compose up -d tailscale && docker compose restart serve'
```

Restart serve so its health and the bot's links show the tailnet address. The container serves the war room itself
from `deploy/hub/tailscale-serve.json`, so `clipper warroom --tailnet on` isn't used there (the
clipper containers aren't root and can't change Tailscale's settings; `--tailnet status` works).
Open `https://clipper.<tailnet>.ts.net/?key=<token>` once on the phone, with the token from
`secrets/warroom_token.txt` in the NAS folder. Turn the laptop's off (`clipper warroom --tailnet
off` there).

## Keep the machine's key from expiring

Tailscale expires a machine's node key after 180 days by default. When it does, the machine drops
off the tailnet and the war room stops answering on the phone until you sign in again. The
`clipper` machine is meant to run unattended, so turn expiry off for it once:

1. Open the admin console's Machines page (https://login.tailscale.com/admin/machines).
2. Find `clipper` (the NAS container; the laptop and phone are separate rows), click its `...`
   menu at the right of the row, and choose **Disable key expiry**.
3. Check the row: it now shows an **Expiry disabled** badge under the machine's name.

Do this once after the first sign-in; it survives restarts and updates, and sticks to the machine
for as long as it stays registered. If you ever remove the machine and sign in again (a lost
`tailscale-state` volume, say), it is a new machine and the setting needs doing again.

An auth key (`TS_AUTHKEY`) has its own expiry, but that only matters for the first sign-in: once
the machine has logged in, the key is no longer used. Disabling key expiry on the machine is the
step that keeps it signed in.
