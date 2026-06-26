"""Execution & Risk Service (ERS) -- the deterministic hands + sole key-holder.

"Hermes proposes; the ERS disposes." Every proposed field is an untrusted hint; the
ERS re-fetches live state, recomputes size itself, runs every guardrail, and either
signs+submits or vetoes with a reason code. See docs/DESIGN-S3-ERS.md.
"""
