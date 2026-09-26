"""Execution engines behind the ``app.modules.work`` port.

``dbos_engine`` is the production engine; ``inline`` is the deterministic
engine the fast test tiers use. No other module may import ``dbos``.
"""
