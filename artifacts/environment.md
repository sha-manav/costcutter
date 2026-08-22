# environment.md — ERP Agent Bench, weeks 1–2

Recorded per SPEC §1.1 and INSTRUCTIONS §1. This container has **no GPUs**;
weeks 1–2 only. Base models are served via OpenRouter for the calibration
gate. Nothing here concerns training or vLLM.

## Hardware

| Fact | Value |
|---|---|
| GPUs | **none** — `nvidia-smi` absent |
| CPU | 4 cores |
| RAM | 15 GB |
| Disk free | 26 GB (SPEC §12.2 floor is 10 GB — **pass**) |

SPEC §1.1's 80GB-vs-40GB and NVLink questions are not answerable here and do
not apply to weeks 1–2. They must be re-run on the rented box in week 3
before any training decision.

## Docker — works, but the registry is blocked

```
docker --version   29.3.1
dockerd            starts, storage-driver=overlayfs, API on /var/run/docker.sock
docker run hello-world
  -> Forbidden: https://production.cloudfront.docker.com/registry-v2/...
```

**Docker-in-Docker itself is fine.** The daemon starts and initialises
buildkit. What fails is pulling an image: the registry blob CDN sits outside
this session's egress allowlist and returns HTTP 403. The proxy's own
documentation says to report policy denials rather than route around them,
so no workaround was attempted.

Consequence: **the `frappe_docker` compose path is unavailable in this
container.** ERPNext runs from the native installer instead
(`infra/setup_erpnext.sh`), which is the same conclusion the prior project
reached against the same egress policy.

## ERPNext — up, deterministic, resettable

Native install, single site `shadow.localhost` on port 8000.

| Check | Result |
|---|---|
| `api/method/ping` | 200 |
| Seed image | `artifacts/seed.sql`, 8.3 MB logical dump |
| **Full reset** | **5.8 s** |
| Determinism | two consecutive resets → identical counts |

Seed state after reset (stable across resets):

| Doctype | Count |
|---|---|
| Customer | 12 |
| Item | 15 |
| Supplier | 5 |
| Sales Order | 10 |
| Sales Invoice | 8 |
| Warehouse | 5 |

Reset drops and reloads the site database from the dump, then flushes both
redis instances. It kills open connections first: an idle pooled connection
from ERPNext's own workers holds a metadata lock and `DROP DATABASE` blocks
behind it with no timeout — a prior run hung 26 minutes at zero CPU on
exactly this.

## Two constraints that block a stated guardrail

The instruction was: *ERPNext goes on a persistent host, not in this
container.* **Neither half of that is reachable from here.**

1. **No egress to arbitrary hosts.** Outbound is an HTTPS-CONNECT proxy with
   an allowlist covering Anthropic, GitHub, npm, PyPI, crates and Go modules
   plus RFC1918 ranges. A non-allowlisted host returns `000`/403; raw TCP to
   port 22 has no route and there is no `ssh` binary. So the harness cannot
   point at an off-box ERPNext, and no VM can be provisioned or reached from
   this session.
2. **No image pulls**, per the Docker section above.

So for weeks 1–2, ERPNext must live in this container. The persistence risk
is real and already realised: this container has restarted five times and
once rolled back to an older snapshot, emptying the site database.

Mitigation, given the constraint:

- the seed dump and the installer are both in the repo, so the environment
  is reconstructible from a clean container by script, not by hand
- restore is 5.8 s once ERPNext is installed
- `scripts/supervise.sh` brings the stack back and re-seeds when a restart
  leaves the database half-written
- every result file is force-added and pushed as it is produced

This must be revisited in week 3: on the rented box, put ERPNext on the
persistent VM as SPEC §1.3 intends.
