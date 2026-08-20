# Environment notes

Two things about the machine this was built on affect how the results should
be read. Both are recorded here rather than buried in the report.

## ERPNext was installed natively, not from a container

`infra/pwd.yml` is the official `frappe_docker` compose file and is the
preferred path. It could not be used here: the sandbox's egress policy allows
container registry *manifests* but blocks the blob CDNs they redirect to
(`production.cloudfront.docker.com`, and the equivalent for public ECR), so
`docker pull` fails partway through every image, on Docker Hub and on
mirrors alike.

`infra/setup_erpnext.sh` installs the same ERPNext version (v15) from source
with `frappe-bench` and produces the same site: MariaDB, three Redis
instances, the desk UI on port 8000, Administrator/admin. Getting there
needed four things the stock instructions do not mention on a bare image:

* `bench` refuses to run as root, so a `frappe` user is created
* `uv`, which `bench init` uses for the app virtualenv, needs
  `UV_NATIVE_TLS=1` to trust the sandbox's TLS-terminating proxy
* `yarn` and `node` must be on the PATH of the `frappe` user
* `crontab` must exist — `bench init` reads the user crontab unconditionally

One ERPNext-specific gap is worth noting because it silently breaks queries:
`new-site --install-app erpnext` does not always run ERPNext's
`after_install` hook, which creates custom fields such as
`Contact.is_billing_contact`. Without it, several ERPNext queries reference
columns that do not exist. `infra/setup_erpnext.sh` runs the hook and then
`bench migrate` explicitly.

## Two virtualenvs, neither committed

`bash infra/setup_python.sh` creates both:

* `.venv` — everything except mitmproxy
* `.venv-capture` — mitmproxy only, referenced by `proxy.mitmdump` in
  `config.yaml`

They are separate because mitmproxy pins a dependency set that, resolved
together with litellm and fastmcp, silently downgrades mitmproxy to a
decade-old release. The capture addon runs under the capture interpreter and
nothing else does. Neither directory is tracked; `.venv-capture/` was
committed by mistake early on and untracked in "Untrack committed
virtualenv".

## No model credentials were available

The sandbox has no API key for any LLM provider, so both benchmark
conditions ran on the deterministic providers in `shadow/llm.py` rather than
against a model. This was rechecked at the start of v2, since putting a real
model in the loop was its largest task:

* `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and friends: unset.
* A direct call to `api.anthropic.com` returns
  `authentication_error: x-api-key header is required`.
* `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` are present but are the
  proxy's placeholders; Bedrock rejects them with
  `UnrecognizedClientException` in both `us-east-1` and `us-west-2`.
* No credential file under `~/.claude`, `~/.config/anthropic` or `/root/.ccr`
  carries a usable key.

So the litellm path is built and tested against a stub provider
(`tests/test_llm.py`) but its network leg is unexercised, and the numbers in
`FINDINGS.md` remain offline-provider numbers. `bench --require-model`
refuses to run rather than produce numbers that look like model numbers. `FINDINGS.md` states exactly which numbers this affects and
in which direction; the short version is that success is oracle-checked and
real, token counts are measured from the real prompts, latency excludes
inference, and both stand-in policies are biased against the result the
project is trying to demonstrate.

Every result row carries `usage.simulated: true`, and
`metrics.json → headline.policy_simulated` is true, so no downstream reader
can mistake these for LLM-in-the-loop numbers. Switching to a real model is
one config field (`models.provider: litellm`) plus credentials.
