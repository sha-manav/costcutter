### Conditions on the held-out (EVAL) split

| metric | A: browser only | B: tools + fallback |
| --- | --- | --- |
| runs | 54 | 54 |
| tasks | 18 | 18 |
| success rate | 100% | 100% |
| pass^k across trials | 100% | 100% |
| USD per attempted task | $0.00816 | $0.01221 |
| USD per successful task | $0.00816 | $0.01221 |
| p50 latency (s) | 23.6 | 24.6 |
| p95 latency (s) | 37.3 | 39.3 |
| mean steps | 6.7 | 9.0 |
| actions served by synthesized tools | 0% | 5% |
| tasks finished on tools alone | 0% | 17% |
| tool calls that failed | 0 | 36 |

Cost per successful task: **0.7x** cheaper in B. p95 latency: **0.9x** faster. p50 latency: **1.0x** faster.

### Synthesis ground truth

| measure | value |
| --- | --- |
| documented endpoints in load-bearing calls | 6 |
| endpoint recall over load-bearing calls (unweighted) | 83% |
| endpoint recall over load-bearing calls (weighted) | 99% |
| endpoint recall over every observed API call (unweighted) | 83% |
| parameter typing accuracy | 100% (13/13 scored) |
| from_response bindings induced | 22 |
| tools failing on an unresolved binding at replay | 0 |
| provenance false-positive rate at replay | 0% |

### Induced tools

| tool | support | class | verified | steps | parameters |
| --- | --- | --- | --- | --- | --- |
| `list_records` | 44 | read | yes | 2 | `doctype`, `fields`, `filters`, `order_by` |
| `update_item_price` | 9 | write | yes | 3 | `filters`, `modified`, `price_list_rate` |
| `create_customer` | 7 | write | yes | 2 | `name`, `customer_name` |
| `list_customers` | 6 | read | yes | 2 | `name` |
| `list_records_2` | 5 | read | yes | 1 | `doctype`, `filters` |
| `list_records_3` | 4 | read | yes | 3 | `doctype`, `fields`, `filters`, `order_by`, `filters_2` |
| `update_customer` | 4 | write | yes | 2 | `name`, `customer_name` |
| `create_record` | 3 | write | yes | 2 | `doctype`, `name`, `customer_name`, `name_2`, `customer_name_2`, `customer_group`, `territory`, `creation`, `modified` |

### Per held-out template (condition B)

| template | success | action coverage | task coverage | USD/task | steps |
| --- | --- | --- | --- | --- | --- |
| T03_stock_on_hand | 100% | 0% | 0% | $0.00660 | 6.0 |
| T06_latest_order_total | 100% | 100% | 100% | $0.00289 | 2.0 |
| T09_create_sales_order | 100% | 6% | 0% | $0.01963 | 13.0 |
| T10_create_supplier | 100% | 0% | 0% | $0.01105 | 10.0 |
| T12_create_item | 100% | 0% | 0% | $0.01351 | 11.0 |
| T14_create_sales_invoice | 100% | 3% | 0% | $0.01957 | 12.0 |

### What the router did on each held-out template

| template | tool selected | outcome | first error |
| --- | --- | --- | --- |
| T03_stock_on_hand | `list_records_2` | tool_failed×9 | ERROR: step 0 returned 417: {'exception': 'frappe.exceptions.DataError: Field not permitted in query: item', 'exc_type': |
| T06_latest_order_total | `list_records` | tool×9 | — |
| T09_create_sales_order | `update_item_price`, `create_customer` | tool_failed×3, tool×6 | ERROR: step 0 returned 417: {'exception': 'frappe.exceptions.DataError: Field not permitted in query: item', 'exc_type': |
| T10_create_supplier | `create_record` | tool_failed×9 | ERROR: step 0 returned 500: {'exception': "Error: No module named 'frappe.core.doctype.group'", 'exc_type': 'ImportError |
| T12_create_item | `create_record` | tool_failed×9 | ERROR: step 0 returned 500: {'exception': "Error: No module named 'frappe.core.doctype.code'", 'exc_type': 'ImportError' |
| T14_create_sales_invoice | `create_customer`, `update_item_price` | tool×3, tool_failed×6 | ERROR: step 0 returned 417: {'exception': 'frappe.exceptions.DataError: Field not permitted in query: item', 'exc_type': |

### Attainable coverage (router-independent ceiling)

6/18 held-out tasks (33%) can be completed by some verified synthesized tool when the oracle supplies the arguments.

| template | attainable |
| --- | --- |
| T03_stock_on_hand | 100% |
| T06_latest_order_total | 100% |
| T09_create_sales_order | 0% |
| T10_create_supplier | 0% |
| T12_create_item | 0% |
| T14_create_sales_invoice | 0% |

### Coverage vs observation volume

| sessions | episodes | tools | verified | achieved coverage | attainable |
| --- | --- | --- | --- | --- | --- |
| 1 | 23 | 2 | 1 | 3% | 33% |
| 2 | 46 | 7 | 4 | 3% | 33% |
| 3 | 66 | 7 | 4 | 3% | 33% |
| 4 | 87 | 8 | 4 | 3% | 33% |