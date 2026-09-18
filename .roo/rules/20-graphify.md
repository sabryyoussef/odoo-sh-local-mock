# Graphify code-navigation policy

For codebase discovery, use the local Graphify index before broad file searches.

- Run Graphify commands from the repository root.
- Start with a narrow symbol, function, service, or relationship.
- Prefer:
  - `graphify explain "ExactSymbol"`
  - `graphify path "SymbolA" "SymbolB"`
  - `graphify query "specific relationship question"`
- If a query returns more than 200 nodes or reports truncation, narrow it immediately.
- Never read `graphify-out/graph.json` or `graphify-out/graph.html` into model context.
- Do not load the complete `GRAPH_REPORT.md` unless performing an architecture review.
- Graphify is a navigation index, not authoritative evidence.
- Before editing, read the exact source file and relevant function.
- Before modifying a function or contract, find its callers and related tests.
- If Graphify is stale, incomplete, or unavailable, fall back to targeted grep/find.
- Do not run semantic/LLM extraction or configure paid API keys.
- After completing code changes, update the local AST index with:
  `graphify update . --no-cluster`
- Never sacrifice validation, security, tests, or acceptance evidence to save tokens.
