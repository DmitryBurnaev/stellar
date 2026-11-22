# HTTP end-to-end tests

The suite creates a unique `stellar_e2e_*` PostgreSQL database, applies the
Stellar baseline, starts Uvicorn, and provisions users and tokens through
SQLAdmin. It exercises browser session isolation, direct admin access, live
permission changes, and API token expiry, revocation, and one-time display.
The harness drops its database on teardown.

Use a dedicated local PostgreSQL instance with a role allowed to create and drop
databases. Set `E2E_POSTGRES_HOST`, `E2E_POSTGRES_PORT`,
`E2E_POSTGRES_USER`, and `E2E_POSTGRES_PASSWORD`, then run `make test-e2e`.
The HTTP test client uses local, ephemeral ports. The run writes a JUnit report
and status metadata under `.local/test-results/http-e2e/`.

`bash scripts/verify-containers.sh` runs the ordinary suite, PostgreSQL
migration checks, and HTTP tests in a separate Compose project. The HTTP suite
uses `--confcutdir=src/tests/e2e` to avoid the ordinary suite's fixtures.
