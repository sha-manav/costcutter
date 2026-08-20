# Prior art

Notes kept here so `FINDINGS.md` can state each one plainly.

**mitmproxy2swagger.** Reads a mitmproxy capture and emits an OpenAPI
document: paths, methods, parameter names, example schemas. It documents an
endpoint surface. It does not infer where a parameter's value came from, does
not group requests into user tasks, and does not emit anything executable.

**mitmproxy-mcp.** Exposes captured flows to a model over MCP so it can
inspect and query traffic — a debugging surface over the capture itself. The
tools it serves are "search flows", "read a flow"; the application's
operations remain data, not tools.

**WALT — Web Agents that Learn Tools** (Salesforce AI Research, arXiv
[2510.01524](https://arxiv.org/abs/2510.01524), Oct 2025). Reverse-engineers
functionality already built into a website — search, filter, sort, post,
comment, create, edit, delete — into deterministic high-level tool calls,
discovered and validated by an agent driving the site. Reports 52.9% success
on VisualWebArena and 50.1% on WebArena, with roughly 1.4x fewer steps and
21.3% fewer actions than untooled agents. The signal is agent exploration;
the artifact is a validated tool library.

**SkillWeaver** (arXiv [2504.07079](https://arxiv.org/abs/2504.07079), Apr
2025). A web agent explores a site under an LLM-generated curriculum,
practises candidate skills, and distils the successful ones into reusable
plug-and-play APIs. Reports relative success-rate improvements of 31.8% on
WebArena and 39.8% on real sites, and shows skills synthesized by a strong
agent lifting a weaker one by up to 54.3%. Again exploration-driven, and the
skill is an agent-authored program.

**UiPath Task Mining.** Enterprise product that records what users actually
do across desktop applications and mines the recordings for automation
candidates, producing process documentation and RPA workflow suggestions for
a human to implement. Passive observation, but the output is a process
document or an RPA candidate rather than a typed, executable API-level tool.

## Where Shadow differs

* **The input is passive.** No exploration, no crawler, no agent in the loop
  during capture — only HTTP traffic from someone doing their job. Nothing is
  clicked that the user did not click, which matters when the app is a
  production ERP where exploration means creating real invoices.
* **The output carries dataflow.** A tool is a sequence of calls with typed
  bindings, including `from_response` bindings that lift a value out of an
  earlier step. That is what lets a synthesized tool do a
  list-then-load-then-save update rather than replay a single request.
* **The evaluation is held out over task templates.** Tools are synthesized
  from one set of task types and measured on a disjoint set. Coverage
  measured on the templates you observed is memorisation; this is the
  distinction the whole design turns on.
