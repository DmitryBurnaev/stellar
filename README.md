# Stellar

Stellar is a control panel for managing server groups, monitoring availability, and
coordinating updates. Version 0.1.0 provides the web dashboard, user and API token
administration, server inventory, on-demand checks, and a protected system health endpoint.

The shared application infrastructure and regression tests originate from
[code-agent main at 2a3c50d](https://github.com/DmitryBurnaev/code-agent/tree/2a3c50db63ad0827f5ece322e85a802bbbe9ee66).

## Run locally

Install uv and Python 3.14. For a containerized app and PostgreSQL 17, install
Docker with Compose v2. Run these commands from the repository root.

```sh
make install
make secrets
docker compose up --build -d app
```

`make secrets` creates a private `.env` with a generated database password,
bootstrap admin password, and signing key. Compose waits for PostgreSQL. The app
container applies the Alembic migrations before starting Uvicorn. The first
migration creates the active superuser specified by `ADMIN_USERNAME` and
`ADMIN_PASSWORD` (the generated username is `admin`).

Sign in at [the dashboard](http://127.0.0.1:8003/login) and
[SQLAdmin](http://127.0.0.1:8003/admin/login) with the same initial credentials.
Each interface has its own session. The local template permits HTTP on loopback.
For an HTTPS deployment, set `WEB_SESSION_HTTPS_ONLY=true` and configure
Uvicorn's trusted proxy addresses with `FORWARDED_ALLOW_IPS` if TLS terminates at a proxy.

For a host install, provision an empty PostgreSQL database and set
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` to match it.
The database role needs to be able to create the application tables.

```sh
make migrate
make run
```

For Compose with an external database, clear `COMPOSE_PROFILES` and supply its
reachable hostname in `DB_HOST`. The baseline reads `ADMIN_PASSWORD` once.
Change the stored password with `make change-admin-password` on the host, or run
`docker compose exec app python -m src.modules.cli.management` for the Compose app.
The command prompts for the new password.

## Call the system health endpoint

1. In SQLAdmin, create an active user and select the **System** permission.
2. Create an **API Token** for that user. Set an expiration in UTC if needed.
3. Copy the token from the creation response and save it securely.

```sh
curl -H "Authorization: Bearer ${STELLAR_TOKEN}" http://127.0.0.1:8003/api/system/health/
```

The endpoint returns `200` with the status and UTC timestamp. A missing,
invalid, expired, or disabled token, or a disabled user, returns `401`. An
active user without the System permission receives `403`.
See [feature access control](docs/access-control.md) for the permission model.

## Server inventory and checks

An administrator with the Admin and Manage inventory permissions creates providers,
applications, groups, servers, and server domains in SQLAdmin. Each server uses an
SSH host alias from the OpenSSH configuration available to Stellar. Users with
Inventory can view and search servers. Server checks additionally require Server
checks, and editing favorites requires Manage inventory.
Set a separate external scan address for a server behind NAT. Otherwise Stellar
sends the server's inventory IP address to the scanner.

Set `SSH_CONFIG_FILE` and `SSH_KNOWN_HOSTS_FILE` to files readable by the Stellar
process. Configure the SSH user, port, host name, and `IdentityFile` in the SSH
configuration. Populate known hosts with verified host keys before checking. For a
container deployment, mount those files and private keys read-only at the absolute
paths used in the configuration and make them readable by the container user.
Set `SSH_CONNECT_TIMEOUT` and `SSH_COMMAND_TIMEOUT` in seconds if needed.

The check reads OS release information, installed dpkg packages, available APT
updates, the APT update timestamp and the images of running and stopped Docker
containers. It uses the server's existing APT indexes. Configure the SSH account
to read Docker metadata where needed. Stellar retains the last successful data
for each section and shows the collection time and partial failures. Mark a
package or image as a favorite in the server detail page. The recorded last manual
update is entered in SQLAdmin and is separate from a check.

For external ports, configure `PORT_SCANNER_URL` with an HTTPS base URL and
`PORT_SCANNER_TOKEN` with the bearer token. The scanner implements the proposed
[API contract](docs/port-scanner-api.md). Start a scan from the server page. The
page refreshes its status automatically and provides a retry button if needed.
The scanner is deployed separately.

To exercise the SSH collector against a disposable test host, set
`STELLAR_TEST_SSH_CONFIG`, `STELLAR_TEST_SSH_KNOWN_HOSTS`,
`STELLAR_TEST_SSH_ALIAS` and `STELLAR_TEST_SSH_EXPECTED_OS`, then run
`uv run pytest src/tests/units/test_server_checks.py::test_real_ssh_collection`.
The test reads host metadata and verifies host-key rejection.

## Verify

Run `make lint` and `make test` for the local Python checks.
Use the following command to run the full container checks from CI.

```sh
bash scripts/verify-containers.sh
```

This builds the service and test images and runs the linters, PostgreSQL
migrations, pytest, and real HTTP tests with disposable storage. Logs are saved
under `.local/verification/`. For host PostgreSQL e2e setup, see the
[HTTP test guide](src/tests/e2e/README.md). See [security scans](docs/security-scanning.md)
for scan coverage and reporting.

## Release

After the checks pass on `main`, push a numeric semantic-version tag from that
commit. The first release uses `0.1.0`.

```sh
git tag -a 0.1.0 -m "Stellar 0.1.0"
git push origin refs/tags/0.1.0
```

The Release workflow runs the checks and scans on the tagged commit before GHCR
publication. It builds the native AMD64 and ARM64 images, verifies both platforms
in the versioned index, and then points `latest` to the same index. It creates
the GitHub Release after verification.

Set the GHCR package visibility to public to enable anonymous pulls after the
first publication. For a private package, authenticate to GHCR before pulling.
Use the versioned image with the PostgreSQL configuration above.

```sh
docker pull ghcr.io/dmitryburnaev/stellar:0.1.0
DOCKER_IMAGE=ghcr.io/dmitryburnaev/stellar:0.1.0 docker compose up --no-build -d app
```
