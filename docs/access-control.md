# Feature access control

The permission catalog in `src/modules/auth/permissions.py` describes each
feature flag and drives the user form in SQLAdmin.

| Permission | User flag | Protected functionality |
| --- | --- | --- |
| `ADMIN_PANEL` | `can_access_admin` | Admin access and management of users, permissions, and tokens |
| `SYSTEM_API` | `can_access_system_api` | `GET /api/system/health/` and visibility of its dashboard instructions |
| `INVENTORY` | `can_view_inventory` | View server inventory |
| `INVENTORY_ADMIN` | `can_manage_inventory` | Manage inventory in SQLAdmin and favorites |
| `SERVER_CHECKS` | `can_run_server_checks` | Run server and external port checks |

Inventory SQLAdmin views require both `ADMIN_PANEL` and `INVENTORY_ADMIN`.
Browser server checks require both `INVENTORY` and `SERVER_CHECKS`, and favorite
changes require both `INVENTORY` and `INVENTORY_ADMIN`.

An active superuser (`is_admin`) has every permission. Other active users need
each explicit grant. The admin grant permits its holder to manage users and
permissions. New accounts and new feature flags default to false. All product
authorization reads the current user on each request, so a grant change applies
to existing sessions and tokens on their next request.

## Sessions and tokens

The web application and SQLAdmin use separate signed cookies and independent
logout. Browser mutations require a same-origin Origin or Referer, including
SQLAdmin GET actions and logout. API authentication uses the Authorization
header.

SQLAdmin creates a signed token and displays it in that creation response. Its
lookup hash is persisted. Authentication verifies the signature and expiry,
loads the token and its user, and checks both active flags. Invalid credentials
return 401. An authenticated API caller missing a required grant receives 403.
Feature policies do not replace record ownership checks.

## Add a feature

1. List its API, browser, and admin entry points. Reuse the feature's existing
   permission for additional operations.
2. For a new feature, add a `Permission` and its catalog metadata. Add a typed,
   non-null `User` boolean and a new Alembic migration with a false default. Keep
   historical migrations independent of the live model and catalog.
3. Register standard `APIRouter` routes with explicit
   `Depends(APIAccess(Permission.NEW_FEATURE))` or
   `Depends(WebAccess(Permission.NEW_FEATURE))` policies. A view can inject its
   own `WebAccess` dependency to use the authenticated user.
4. Declare `required_permission` on every SQLAdmin view. Its authorization backend
   translates current grants into SQLAdmin permissions for navigation, CRUD, and
   custom actions.
5. Filter navigation and controls by grant. Fetch restricted data only after
   checking the grant.
6. Test denial, the correct grant, superuser access, inactive credentials, and
   revocation through existing credentials. Test the migration against
   PostgreSQL and update this table.

The authentication-only dashboard is `GET /`. The login form, login submission,
logout submission, static assets, and FastAPI documentation are infrastructure
exceptions in the route validator. The validator inspects the effective routes
and dependencies at construction and after mounting SQLAdmin. Tests verify
catalog and route-policy coverage, the baseline schema, and behavior through
actual HTTP. See the [HTTP test guide](../src/tests/e2e/README.md).
