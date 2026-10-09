"""Feasibility research (Phase 2).

Import rule: this package's __init__ and the pure modules (fields, evaluate, review, plan, io,
evidence) never import ADK, so `haggler site-data` can evaluate facts without the agent stack.
Only agents.py and nodes.py import google.adk.
"""
