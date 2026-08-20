# Prior art

Notes kept here so `FINDINGS.md` can state each one plainly.

**mitmproxy2swagger.** Reads a mitmproxy capture and emits an OpenAPI
document: paths, methods, parameter names, example schemas. It documents an
endpoint surface. It does not infer where a parameter's value came from, does
not group requests into user tasks, and does not emit anything executable.

**mitmproxy-mcp.** Exposes captured flows to a model over MCP so it can
inspect and query traffic — a debugging surface over the capture itself. The
tools it serves are "search flows", "read a flow"; the app's operations are
data, not tools.

**WALT (Web Agents that Learn Tools).** Reverse-engineers a website's
functionality into reusable tools an agent can call, discovered by an agent
exploring the site. The signal is agent exploration; the artifact is a tool
library.

**SkillWeaver.** Web agents explore a site, synthesize reusable skills as
callable APIs, and self-verify them by execution, improving over time. Again
exploration-driven, and the skill is an agent-authored program.

**UiPath Task Mining.** Enterprise product that records what users actually
do across desktop applications and mines it for automation candidates,
producing process documentation and RPA workflow suggestions for a human to
implement. Passive observation, but the output is a process document /
RPA candidate rather than a typed, executable API-level tool.

**Where Shadow differs.** The input is passively observed HTTP traffic from
real usage — no exploration, no agent in the loop during capture, no crawler.
The output is executable typed tools with inferred *dataflow* between steps,
so a tool can chain calls whose arguments come from earlier responses. And
the evaluation is held out over task *templates*: tools are synthesized from
one set of task types and measured on a disjoint set, which is what separates
generalisation from memorisation.
