# Reproducing the benchmark

Everything below assumes a clean checkout and a machine that can run Docker
*or* a native frappe bench.

## 1. Bring up ERPNext

Preferred (official compose setup):

```bash
docker compose -f infra/pwd.yml up -d
```

If container registries are unreachable (as in the sandbox this was built in,
where the registry blob CDN is blocked by egress policy), use the native
path, which installs the same ERPNext version from source:

```bash
sudo useradd -m frappe            # bench refuses to run as root
sudo -u frappe bash infra/setup_erpnext.sh
sudo bash infra/start_erpnext.sh
```

## 2. Seed and snapshot

```bash
python -m shadow.cli seed
```

Creates the deterministic company, customers, items, suppliers, orders and
invoices, then writes `artifacts/seed.sql` — the image every reset restores.
A reset takes about six seconds.

## 3. Observe (OBSERVE templates only)

```bash
python -m shadow.cli observe --sessions 3 --fresh
```

Drives the OBSERVE templates through the UI behind the capture proxy. The
generator raises `HeldOutViolation` if asked for an EVAL template.

## 4. Synthesize

```bash
python -m shadow.cli distill --show-dataflow
python -m shadow.cli trace ep0003        # audit one episode's dataflow
python -m shadow.cli verify              # reads only
python -m shadow.cli verify --allow-writes   # resets the DB around each write
```

## 5. Benchmark and report

```bash
python -m shadow.cli bench --trials 3 --fresh
python -m shadow.bench.attainable
python -m shadow.bench.sweep --verify --bench
python -m shadow.cli report
```

`report` writes `artifacts/metrics.json` and the three charts under
`artifacts/charts/`.

## Running with a real model

Set `models.provider: litellm` in `config.yaml` (or leave it on `auto`) and
export credentials for the model named in `models.agent`:

```bash
export ANTHROPIC_API_KEY=...
python -m shadow.cli bench --trials 3 --fresh
```

With no credentials present the pipeline runs on the deterministic providers
described in `FINDINGS.md`, and every result row is tagged
`usage.simulated: true`.
