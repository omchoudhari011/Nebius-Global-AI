"""Security tests — see docs/PLAN.md (Step 7):
1. A memory containing a raw SQL fragment is written and recalled without breaking the query.
2. A tool argument with a path-traversal payload is rejected before it reaches the sandbox.
3. An attempted outbound HTTP call from inside the sandbox fails or times out.
"""

import pytest


@pytest.mark.skip(reason="Implement in Step 7 — see docs/PLAN.md")
def test_sql_injection_in_memory_content_is_safe():
    ...


@pytest.mark.skip(reason="Implement in Step 7 — see docs/PLAN.md")
def test_path_traversal_tool_arg_is_rejected():
    ...


@pytest.mark.skip(reason="Implement in Step 7 — see docs/PLAN.md")
def test_sandbox_has_no_outbound_network():
    ...
