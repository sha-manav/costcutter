# oracle/ — ground truth only

Everything in this directory reads the **documented ERPNext REST API**. It
exists for two jobs:

1. verifying that a benchmark task actually happened (post-condition checks
   against the database through the public API), and
2. scoring synthesis quality — which endpoints the demonstrations touched,
   and whether the induced parameter types match reality.

**It is never an input to synthesis.** `shadow/distill/` must not import
anything from here; `tests/test_isolation.py` fails the build if it does.
The synthesis pipeline is only ever allowed to see captured traffic.
