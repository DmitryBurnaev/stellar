# Security scans

The Scan workflow runs for pull requests, pushes to main, the weekly schedule,
and release tags through the Release workflow. Each scanner has a separate job.

- Trivy analyzes the lockfile and Dockerfile, including development dependencies.
  A separate job builds the AMD64 service image, validates the report's image
  ID and revision, and checks vulnerabilities in the runtime layers. Both jobs
  save JSON before failing on configured severity levels through Low, including
  findings without a fix. [Trivy 0.76.2](https://github.com/aquasecurity/trivy/releases/tag/v0.76.2)
- Gitleaks checks full Git history and redacts findings. TruffleHog scans the
  checkout with verification disabled and saves only finding locations and
  detector names. [Gitleaks 8.28.0](https://github.com/gitleaks/gitleaks/releases/tag/v8.28.0)
  · [TruffleHog 3.90.12](https://github.com/trufflesecurity/trufflehog/releases/tag/v3.90.12)
- Opengrep checks Python using a pinned rule revision and publishes rule IDs,
  locations, and severity in the job log and JSON artifact. CodeQL runs Python
  analysis in its own workflow.
  [Opengrep 1.33.1](https://github.com/opengrep/opengrep/releases/tag/v1.33.1)

The workflows pin action commits and verify scanner binary hashes from upstream
release metadata. Trivy refreshes its vulnerability database when needed.
Container base tags and the vulnerability database change independently of the
source commit. Review findings against the actual artifact and scan date before
adding an exception.

The image build requires patched Alpine packages for OpenSSL and zlib. The
service image removes pip and its bundled bootstrap wheel after the locked
application dependencies are installed. This keeps pip's vendored components
and their SBOM out of the runtime filesystem.

## Opengrep rule exclusions

Opengrep 1.33.1 reported 19 false positives from two maintainability rules in the
pinned Python ruleset. The workflow excludes these exact IDs.

- `is-function-without-parentheses` matched 15 legitimate references to boolean
  model attributes such as `User.is_active` and `Token.is_active`.
  [Rule source](https://github.com/opengrep/opengrep-rules/blob/f1d2b562b414783763fd02a6ed2736eaed622efa/python/lang/maintainability/is-function-without-parentheses.yaml)
- `return-not-in-function` matched four valid constructs, including SQLAdmin
  formatter lambdas and the return inside `DBSettings.database_dsn`.
  [Rule source](https://github.com/opengrep/opengrep-rules/blob/f1d2b562b414783763fd02a6ed2736eaed622efa/python/lang/maintainability/return.yaml)

Ruff and mypy check Python syntax and types. Retest these examples before
changing the exclusions when the Opengrep binary or rule revision changes.

## Run Trivy locally

Run `bash scripts/verify-containers.sh --scan` to build and scan in a separate
Compose project. It requires Docker, Trivy 0.76.2, jq, and OpenSSL. The scan
validates the report against the saved image's config digest and architecture.
Reports and exit status are saved under `.local/verification/`.
