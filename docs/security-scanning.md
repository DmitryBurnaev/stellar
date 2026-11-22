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
  locations, and severity. CodeQL runs Python analysis in its own workflow.
  [Opengrep 1.33.1](https://github.com/opengrep/opengrep/releases/tag/v1.33.1)

The workflows pin action commits and verify scanner binary hashes from upstream
release metadata. Trivy refreshes its vulnerability database when needed.
Container base tags and the vulnerability database change independently of the
source commit. Review findings against the actual artifact and scan date before
adding an exception.

Run `bash scripts/verify-containers.sh --scan` to build and scan in a separate
Compose project. It requires Docker, Trivy 0.76.2, and jq. Reports and exit status
are saved under `.local/verification/`.
