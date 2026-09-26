"""A revoked scope must block the tool call on every attempt in a batch.
This is the test the Privacy differentiator gets checked against — see docs/PLAN.md.
Step 7 — see docs/PLAN.md.
"""

import pytest


@pytest.mark.skip(reason="Implement in Step 7 — see docs/PLAN.md")
@pytest.mark.parametrize("tool_id,scope", [])
def test_permission_gate_blocks_when_revoked(tool_id, scope):
    ...
