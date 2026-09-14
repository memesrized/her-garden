"""Persistent memory for one household's plants and supplies.

Receives validated MCP commands and returns compact records or plant context.
PostgreSQL stores immutable events and transactional current-state projections.
Plant-care reasoning remains the responsibility of the connected LLM.
"""
