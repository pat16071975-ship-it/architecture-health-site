# AZ-BAZE Private Test Contour

Status: PREPARED ONLY. LIVE AZ-BAZE IS NOT MODIFIED.

## Goal

Provide a private test contour where the owner can manually click through and test
the clinic-timezone functionality without changing the working
«Архитектура здоровья».

The test contour is intentionally **not public** and is intentionally **not
available to existing live users**.

## Privacy / owner-only access

The test contour is isolated in two layers.

### Layer 1 — network isolation

The service binds only to:

`127.0.0.1:8002`

It is not added to Nginx, DNS, firewall or any public virtual host.

The systemd service also uses:

- `IPAddressDeny=any`
- `IPAddressAllow=localhost`

The owner accesses it only through an SSH tunnel from their own computer:

`ssh -N -L 18002:127.0.0.1:8002 az-server`

Browser:

`http://127.0.0.1:18002`

### Layer 2 — isolated authentication database

The test contour does **not** copy the live database.

A brand-new test SQLite database is created at:

`/var/lib/az-baze-test/auth.db`

It contains exactly one enabled login:

`owner-private-test@local.invalid`

The password is generated randomly during preparation, printed once to the root
terminal and saved in a root-only file:

`/root/az-baze-test-access.txt`

The test login is:

- non-admin;
- active;
- not forced to change password;
- granted only the explicit permission `structure_manage`.

No live users are copied into the test database. In particular, existing live
accounts — including Zubachev's live account — do not exist in this test
database and therefore cannot log in to the test contour.

## Test-only structure

The test DB applies the same structure foundation migration and then creates only:

- Holding: `Архитектура здоровья — TEST`
- Organization: `Архитектура здоровья — TEST`
- Cluster: `Кластер по умолчанию — TEST`
- Clinic: `Архитектура здоровья — TEST`
- initial timezone: `UTC`

`UTC` is a test-only starting value, not a production assumption. The owner can
then change it through the new clinic-timezone UI.

## Test server wrapper

`az-baze-auth/test_server.py` refuses to start unless
`AZBAZE_TEST_CONTOUR=1`.

It also verifies that all state paths resolve under the dedicated test roots:

- state: `/var/lib/az-baze-test`
- site: `/var/www/az-baze-test`

Test-only environment values redirect:

- SQLite DB;
- credentials key;
- Finrez data/key;
- site root.

The two runtime paths that were hardcoded in legacy modules are overridden inside
the test wrapper:

- economics-control JSON;
- contact-import backup directory.

The wrapper also:

- uses a distinct session cookie name;
- allows non-secure cookies only because access is through the local SSH tunnel;
- injects a visible banner:
  `ПРИВАТНЫЙ TEST-КОНТУР — изменения не влияют на рабочую «Архитектуру здоровья»`;
- returns header `X-AZ-BAZE-Contour: private-test`.

## Filesystem isolation

The test contour uses only:

- `/opt/az-baze-test`
- `/var/lib/az-baze-test`
- `/var/www/az-baze-test`
- `/etc/az-baze-test`
- `/etc/systemd/system/az-baze-test.service`
- `/root/az-baze-test-access.txt`

The live paths remain separate.

## Live preservation

The preparation script checks before any test write:

- hostname must be `az-server`;
- live service `az-baze-auth` must be active;
- live DB file must exist;
- test service must not already be active;
- loopback port 8002 must be free;
- all private-test paths must be absent for a first creation.

The script does not:

- stop/restart live service;
- write live DB;
- copy live DB;
- deploy code into the live service;
- change Nginx;
- change DNS;
- open firewall ports;
- change live clinic timezone;
- apply a live migration;
- run live seed/preflight.

## Test service

Separate systemd unit:

`az-baze-test.service`

Working directory:

`/opt/az-baze-test/repo/az-baze-auth`

Gunicorn binds:

`127.0.0.1:8002`

The service runs as `www-data` and has a separate writable state root.

## Preparation flow

1. Run read-only private-test preflight.
2. Confirm the exact test source commit.
3. Create only the dedicated test directories.
4. Clone the exact repository commit into the test root.
5. Create an isolated Python virtual environment.
6. Create a separate test env file and secret key.
7. Create a fresh test DB.
8. Apply foundation migration to the test DB only.
9. Create exactly one owner-only test user.
10. Grant only `structure_manage`.
11. Seed only TEST structure with initial timezone `UTC`.
12. Create and start `az-baze-test.service`.
13. Verify local health on 127.0.0.1:8002.
14. Print/save owner-only test credentials.
15. Owner opens the service through SSH tunnel.

## Failure behavior

First creation requires all dedicated test paths to be absent.

If preparation fails after creating them, the script stops the test service and
removes only the dedicated test paths/unit created for this contour.

It never removes or modifies live AZ-BAZE paths.

## Manual testing target

Once the tunnel is open, the owner can:

1. Log in with the generated test account.
2. Open `Настройка клиник`.
3. See `Архитектура здоровья — TEST`.
4. Change timezone from `UTC` to another IANA zone.
5. Reopen the page and confirm the saved value.
6. Open two browser tabs and verify stale-update protection.
7. Confirm the visible private-test banner.

## Current authorization boundary

The owner authorized preparation of this separate private test contour while
preserving live `az-baze.ru` and the live DB.

This package does not authorize merge to live code or live deployment.
