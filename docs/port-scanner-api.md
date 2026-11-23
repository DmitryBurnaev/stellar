# External TCP port scanner API proposal

Stellar needs an independently deployed service that scans a server's public IP
address from the internet. The API below is a proposed contract for that service.

## Authentication and transport

Use HTTPS and a bearer token configured on the Stellar deployment. The scanner
must only accept targets explicitly permitted by its operator. It should enforce
request and concurrency limits and retain an audit record of requested targets.

## Submit a scan

`POST /v1/scans` accepts JSON containing `address`, a literal IPv4 or IPv6
address. The scanner checks TCP ports 1 through 65535. It returns HTTP 202 and
JSON containing `id`, an opaque scan identifier. The request should support an
idempotency key so a retry does not start a duplicate scan.

## Read a scan

`GET /v1/scans/{id}` returns JSON containing `id`, `address`, `status`,
`requested_at`, and, once complete, `finished_at`. Timestamps use ISO 8601 UTC.
The status is `queued`, `running`, `succeeded`, or `failed`.

A successful result contains `open_tcp_ports`, an array of distinct integers
from 1 through 65535. An empty array means the complete scan found no open TCP
ports. The result also contains a `scanner` identifier describing the external
vantage point. A failed result contains a stable `error_code` and a safe
`message`. Partial scans must be reported as failed, with an explanation.

The service should document scan retention, expected duration, supported IP
families, rate limits and its error responses. Stellar must show the last
successful result with its timestamp separately from the latest attempt.
