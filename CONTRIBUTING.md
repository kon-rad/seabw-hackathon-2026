# Contributing to MiroShark × Polymarket

Thanks for your interest. This is a hackathon project — contributions that improve reliability, extend functionality, or fix bugs are welcome.

---

## Getting Started

### 1. Fork and clone

```bash
git clone https://github.com/YOUR_USERNAME/seabw-2026.git
cd seabw-2026
```

### 2. Set up your environment

```bash
cp web/.env.together-ai web/.env
# Fill in your Together AI key, Neo4j Aura Free credentials, and admin token.
# POLYMARKET_PRIVATE_KEY is only required if you're working on bet placement.

npm run setup --prefix web
cd web/backend && uv sync
```

### 3. Start the dev server

```bash
npm run dev --prefix web
```

- Frontend: http://localhost:3000 (Vite HMR)
- Backend: http://localhost:5001 (Flask with auto-reload)

Vite proxies all `/api/*` requests to Flask, so you only need one terminal and one browser tab.

---

## Project Layout

The two areas where new work happens:

**Backend — `web/backend/app/api/polymarket.py`**
Flask blueprint with three endpoints: market scout, research SSE agent, bet placement. Add new endpoints here or extend existing ones.

**Frontend — `web/frontend/src/`**
- `views/BetDiscovery.vue` — market list
- `views/ResearchView.vue` — research progress + seed doc preview
- `components/BetPanel.vue` — bet confirmation widget

Everything else under `web/` is the upstream MiroShark codebase. Try to avoid modifying upstream files unless necessary — it makes rebasing against upstream easier.

---

## Making a Change

```bash
git checkout -b feat/your-feature-name   # or fix/, docs/, refactor/
```

Keep changes focused. One branch = one logical change.

**Backend changes:** Flask and Python. Run `cd web/backend && uv run python run.py` to test the backend standalone. Flask auto-reloads on file save.

**Frontend changes:** Vue 3 Composition API. Vite HMR means changes appear instantly in the browser.

**Adding a Python dependency:**
```bash
cd web/backend
uv add package-name
# uv.lock is updated automatically — commit both pyproject.toml and uv.lock
```

**Adding a JS dependency:**
```bash
cd web/frontend
npm install package-name
# commit package.json and package-lock.json
```

---

## Pull Requests

1. Push your branch and open a PR against `main`
2. Fill in the PR template (it will appear automatically)
3. PRs must pass the existing test suite — run `cd web/backend && uv run pytest` before pushing
4. Keep the diff focused — unrelated cleanup in the same PR slows review

For larger changes (new features, architectural shifts), open an issue first to discuss the approach.

---

## Code Style

**Python:** no formatter enforced, but follow the style already in `polymarket.py` — type hints where useful, docstrings for non-obvious functions, no unused imports.

**Vue/JS:** no linter enforced, but match the Composition API + `<script setup>` style used in `BetDiscovery.vue` and `ResearchView.vue`.

**No AI-generated filler:** don't add comments that explain what the code does. Only add a comment if the *why* is non-obvious.

---

## Reporting Issues

Use [GitHub Issues](../../issues). Include:
- What you were trying to do
- What happened instead
- Relevant error output (Flask logs, browser console)
- Your OS and Python/Node versions

---

## Areas That Need Work

These are open and ready to pick up:

- **Market scoring** — the scoring heuristic in `polymarket.py:_score_market` is a rough first pass. Improve it with better signals (liquidity depth, historical volatility, source quality).
- **Research agent quality** — the 4 search queries are hardcoded. Make them dynamic based on the market topic and entities found in previous rounds.
- **Simulation → recommendation** — MiroShark's output isn't yet parsed to extract a concrete YES/NO price. The BetPanel currently uses the market's current price as the default. Wire up the actual swarm consensus price.
- **Error handling** — the SSE stream in `ResearchView.vue` doesn't handle DuckDuckGo rate limits gracefully. Add retry logic.
- **Tests** — `web/backend/tests/` has MiroShark's existing tests. Add tests for `polymarket.py` endpoints.

---

## License

By contributing, you agree your changes will be licensed under [AGPL-3.0](web/LICENSE).
