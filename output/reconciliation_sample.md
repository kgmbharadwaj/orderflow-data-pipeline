# Reconciliation report

Overall: **PASS**

| Check | Expected | Actual | Result |
|---|---|---|---|
| file records == history rows + rejects | 300 | 300 | PASS |
| fact_orders rows == distinct orders in history | 271 | 271 | PASS |
| sum(line_total) of latest events == sum(fact_orders.line_total) | 145879910 | 145879910 | PASS |
| fact_orders.last_updated_at == max(history.updated_at) for every order | 0 | 0 | PASS |
| orphan customer keys in fact_orders | 0 | 0 | PASS |
| orphan product keys in fact_orders | 0 | 0 | PASS |
| duplicate (order_id, updated_at) in history | 0 | 0 | PASS |
| rejects without source traceability | 0 | 0 | PASS |
| distinct valid orders vs generator expectation | 271 | 271 | PASS |
| sum of final line totals (cents) vs generator expectation | 145879910 | 145879910 | PASS |
| per-line outcome mismatches vs labels file | 0 | 0 | PASS |
