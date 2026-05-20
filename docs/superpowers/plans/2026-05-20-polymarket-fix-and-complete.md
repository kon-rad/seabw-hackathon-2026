# MiroShark Polymarket — Fix & Complete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all deployment-blocking bugs and implement the missing simulation recommendation flow so the app can go live on aum.lol and produce actionable "BET YES/NO" recommendations from MiroShark simulations.

**Architecture:** Single Docker container running Vue.js frontend (Vite, port 3000) + Flask backend (port 5001), reverse-proxied by nginx with HTTPS. Flask already has a `polymarket_bp` blueprint at `/api/polymarket/`. The simulation engine (MiroShark/Wonderwall) runs as a subprocess managed by `SimulationRunner`. Market research writes `seed.md` to `uploads/{market_id}/`; MiroShark projects live under `uploads/projects/{project_id}/`; simulation data (including a SQLite `polymarket.db`) lives under `uploads/simulations/{simulation_id}/`.

**Tech Stack:** Vue 3, Flask, py-clob-client, DuckDuckGo-search, SQLite (MiroShark internal), Docker Compose, nginx/Let's Encrypt.

---

## File Map

| File | Action | What changes |
|------|--------|--------------|
| `docker-compose.yml` | Modify | Fix `VITE_API_BASE_URL` (drop `/api` suffix); add `SIMULATION_PLATFORM=polymarket` |
| `web/Dockerfile` | Modify | Add frontend build stage; run gunicorn + serve static files in production |
| `web/backend/app/api/polymarket.py` | Modify | Fix score threshold; add `POST /simulate/context`; add `GET /simulate/<id>/result` |
| `web/frontend/src/views/BetDiscovery.vue` | Modify | Add `setInterval` for 5-minute auto-refresh |
| `web/frontend/src/views/ResearchView.vue` | Modify | POST to `/api/polymarket/simulate/context` after graph upload; save `project_id` in sessionStorage |
| `web/frontend/src/views/ReportView.vue` | Modify | Fetch and display recommendation banner when `polymarket_context` is in sessionStorage |
| `web/frontend/src/components/BetPanel.vue` | Modify | Show Polymarket link (already present); clarify off-chain order vs tx hash |

---

## Task 1: Fix VITE_API_BASE_URL double-path bug

**Files:**
- Modify: `docker-compose.yml`

This is the most critical fix. `VITE_API_BASE_URL=https://aum.lol/api` causes axios to produce `https://aum.lol/api/api/polymarket/markets` (axios appends the path to the full baseURL). Every API call fails in production.

- [ ] **Step 1: Open docker-compose.yml and fix the env var**

In `docker-compose.yml`, change line:
```yaml
      - VITE_API_BASE_URL=https://aum.lol/api
```
to:
```yaml
      - VITE_API_BASE_URL=https://aum.lol
```

The axios instance uses `baseURL: import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'`. All existing API calls already include the `/api/` prefix (e.g. `api.get('/api/polymarket/markets')`), so removing the suffix from the base URL is the correct fix.

- [ ] **Step 2: Verify the SSE URL in ResearchView.vue is consistent**

Open `web/frontend/src/views/ResearchView.vue` line 142. The SSE URL construction is:
```javascript
const url = `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'}/api/polymarket/research/...`
```
With the fix, `VITE_API_BASE_URL=https://aum.lol`, so URL becomes `https://aum.lol/api/polymarket/research/...` — correct. No code change needed here.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: correct VITE_API_BASE_URL to drop /api suffix — fixes double-path 404"
```

---

## Task 2: Fix Dockerfile for production

**Files:**
- Modify: `web/Dockerfile`

Currently the Dockerfile runs `npm run dev` which starts the Vite dev server with HMR. In production we need a built static bundle served separately from the Flask backend.

- [ ] **Step 1: Write the new Dockerfile**

Replace `web/Dockerfile` with:

```dockerfile
FROM python:3.11-slim AS base

# Install Node.js 20 (LTS) and system deps
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
       curl ca-certificates nodejs npm \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# ── Python deps (cached layer) ────────────────────────────────────────
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --no-dev

# ── Node deps (cached layer) ──────────────────────────────────────────
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci && npm ci --prefix frontend

# ── Source ────────────────────────────────────────────────────────────
COPY . .

# ── Build Vue frontend ────────────────────────────────────────────────
# VITE_API_BASE_URL injected at build time from docker-compose env
ARG VITE_API_BASE_URL=https://aum.lol
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build           # → frontend/dist/

EXPOSE 3000 5001

# ── Start: gunicorn (backend) + static file server (frontend) ─────────
CMD ["sh", "-c", \
  "cd backend && uv run gunicorn 'app:create_app()' \
     --bind 0.0.0.0:5001 \
     --workers 2 \
     --timeout 600 \
     --worker-class sync \
     & npx --yes serve frontend/dist --listen tcp://0.0.0.0:3000 --no-clipboard"]
```

- [ ] **Step 2: Verify `web/backend/` has a `run.py` or Flask entry point**

```bash
ls web/backend/
```

Expected output includes `run.py`. If it exists, `app:create_app()` in gunicorn refers to `app/__init__.py`'s `create_app`. If the entry module is named differently, adjust `'app:create_app()'` accordingly (the colon separates module from callable).

- [ ] **Step 3: Test the build locally (optional but recommended)**

```bash
cd web
docker build -t miroshark-test --build-arg VITE_API_BASE_URL=https://aum.lol .
```

Expected: build completes, no errors in `npm run build` stage.

- [ ] **Step 4: Commit**

```bash
git add web/Dockerfile
git commit -m "fix: replace dev server with gunicorn + built static bundle in Dockerfile"
```

---

## Task 3: Fix market score edge threshold

**Files:**
- Modify: `web/backend/app/api/polymarket.py`

The spec says `|price - 0.5| > 0.1` = full 15 points. The code uses `> 0.15` making it stricter than spec and causing some markets with 10–15% edge to score only 5 instead of 15.

- [ ] **Step 1: Open polymarket.py and find `_score_market`**

In `web/backend/app/api/polymarket.py`, find the block (around line 100):

```python
    # Price edge (not near 50/50)
    try:
        best_ask = float((m.get("bestAsk") or m.get("best_ask") or 0.5))
        if abs(best_ask - 0.5) > 0.15:
            score += 15
        elif abs(best_ask - 0.5) > 0.05:
            score += 5
    except Exception:
        pass
```

Change to:

```python
    # Price edge (not near 50/50) — spec: |price - 0.5| > 0.1 = full score
    try:
        best_ask = float((m.get("bestAsk") or m.get("best_ask") or 0.5))
        if abs(best_ask - 0.5) > 0.1:
            score += 15
        elif abs(best_ask - 0.5) > 0.05:
            score += 5
    except Exception:
        pass
```

- [ ] **Step 2: Commit**

```bash
git add web/backend/app/api/polymarket.py
git commit -m "fix: correct market score edge threshold from 0.15 to 0.1 per spec"
```

---

## Task 4: Add 5-minute auto-refresh to BetDiscovery

**Files:**
- Modify: `web/frontend/src/views/BetDiscovery.vue`

The spec requires markets to auto-refresh every 5 minutes. Currently only a manual refresh button exists.

- [ ] **Step 1: Add the auto-refresh timer**

In `web/frontend/src/views/BetDiscovery.vue`, find the `<script setup>` section. The current `onMounted`:

```javascript
onMounted(fetchMarkets)
```

Replace with:

```javascript
import { ref, onMounted, onBeforeUnmount } from 'vue'

let refreshTimer = null

onMounted(() => {
  fetchMarkets()
  refreshTimer = setInterval(fetchMarkets, 5 * 60 * 1000)  // every 5 minutes
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
```

The `ref` import is already there — only add the `onBeforeUnmount` import and the timer logic.

- [ ] **Step 2: Verify the import line**

The current import at the top of the script block is:
```javascript
import { ref, onMounted } from 'vue'
```
Change it to:
```javascript
import { ref, onMounted, onBeforeUnmount } from 'vue'
```

- [ ] **Step 3: Commit**

```bash
git add web/frontend/src/views/BetDiscovery.vue
git commit -m "feat: add 5-minute auto-refresh to BetDiscovery per spec"
```

---

## Task 5: Add `POST /api/polymarket/simulate/context` backend endpoint

**Files:**
- Modify: `web/backend/app/api/polymarket.py`

This endpoint stores the Polymarket market context (current YES price, condition ID) alongside a MiroShark project so the result endpoint can compute edge later. The research → graph upload flow already creates the project; this endpoint just writes the metadata.

- [ ] **Step 1: Add the endpoint to polymarket.py**

Add this route at the end of `web/backend/app/api/polymarket.py`, before the final EOF:

```python
@polymarket_bp.route("/simulate/context", methods=["POST"])
def save_simulation_context():
    """
    Store Polymarket market context alongside a MiroShark project_id so
    the /result endpoint can compute swarm vs market edge after the simulation.

    Body: { project_id, market_id, condition_id, yes_price, title, resolution_date }
    """
    data = request.get_json(force=True)
    project_id = data.get("project_id")
    if not project_id:
        return jsonify({"success": False, "error": "project_id required"}), 400

    from ..models.project import ProjectManager
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({"success": False, "error": f"Project not found: {project_id}"}), 404

    ctx = {
        "market_id": data.get("market_id", ""),
        "condition_id": data.get("condition_id", ""),
        "title": data.get("title", ""),
        "yes_price": float(data.get("yes_price", 0.5)),
        "resolution_date": data.get("resolution_date", ""),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    project_dir = Path(ProjectManager._get_project_dir(project_id))
    (project_dir / "polymarket_context.json").write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"Saved polymarket context for project {project_id}: {ctx['title']}")
    return jsonify({"success": True, "project_id": project_id})
```

- [ ] **Step 2: Confirm `Path` and `ProjectManager` are imported**

`Path` is imported at the top of `polymarket.py` via `from pathlib import Path`. `ProjectManager` is imported inline inside the function (pattern already used by `/bet`). The `datetime` and `timezone` imports are already present. No new imports needed.

- [ ] **Step 3: Commit**

```bash
git add web/backend/app/api/polymarket.py
git commit -m "feat: add POST /api/polymarket/simulate/context to store market metadata"
```

---

## Task 6: Add `GET /api/polymarket/simulate/<project_id>/result` backend endpoint

**Files:**
- Modify: `web/backend/app/api/polymarket.py`

This is the core of the missing recommendation feature. After the simulation runs, this endpoint:
1. Loads the stored Polymarket context (current market YES price)
2. Finds the simulation associated with the project
3. Reads the final YES price from MiroShark's internal `polymarket.db` SQLite file
4. Computes edge and returns a structured recommendation

- [ ] **Step 1: Add the result endpoint**

Add this route immediately after `save_simulation_context` in `web/backend/app/api/polymarket.py`:

```python
@polymarket_bp.route("/simulate/<project_id>/result", methods=["GET"])
def get_simulation_result(project_id: str):
    """
    Compute and return the Polymarket recommendation for a completed simulation.

    Reads the final YES price from MiroShark's polymarket.db, compares against
    the stored market YES price, and returns edge + BET YES/NO recommendation.

    Returns 200 with status="pending" if simulation is still running.
    """
    from ..models.project import ProjectManager
    from ..services.simulation_manager import SimulationManager
    from ..services.simulation_runner import SimulationRunner

    # Load stored Polymarket context
    try:
        project_dir = Path(ProjectManager._get_project_dir(project_id))
    except Exception:
        return jsonify({"success": False, "error": f"Project not found: {project_id}"}), 404

    ctx_path = project_dir / "polymarket_context.json"
    if not ctx_path.exists():
        return jsonify({"success": False, "error": "No Polymarket context saved for this project"}), 404

    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    market_yes_price = float(ctx.get("yes_price", 0.5))

    # Find the most recent simulation for this project
    manager = SimulationManager()
    simulations = manager.list_simulations(project_id=project_id)
    if not simulations:
        return jsonify({"success": True, "status": "no_simulation",
                        "message": "No simulation found for this project yet"}), 200

    sim = sorted(simulations, key=lambda s: s.created_at, reverse=True)[0]
    simulation_id = sim.simulation_id

    # Check run status
    run_state = SimulationRunner.get_run_state(simulation_id)
    if not run_state:
        return jsonify({"success": True, "status": "not_started",
                        "simulation_id": simulation_id}), 200

    if run_state.runner_status.value not in ("completed", "stopped"):
        return jsonify({
            "success": True,
            "status": run_state.runner_status.value,
            "simulation_id": simulation_id,
            "progress_percent": run_state.to_dict().get("progress_percent", 0),
        }), 200

    # Read final swarm price from polymarket.db
    db_path = Path(SimulationRunner.RUN_STATE_DIR) / simulation_id / "polymarket.db"
    swarm_price = None

    if db_path.exists():
        import sqlite3
        try:
            with sqlite3.connect(str(db_path)) as conn:
                # Market 1 is always the primary market in Polymarket simulations.
                # price_yes = reserve_b / (reserve_a + reserve_b) — AMM formula.
                row = conn.execute(
                    "SELECT reserve_a, reserve_b FROM market WHERE market_id = 1"
                ).fetchone()
                if row:
                    ra, rb = float(row[0] or 0), float(row[1] or 0)
                    total = ra + rb
                    swarm_price = round(rb / total, 4) if total > 0 else 0.5
        except sqlite3.Error as e:
            logger.warning(f"Could not read polymarket.db for {simulation_id}: {e}")

    if swarm_price is None:
        # Simulation ran but no polymarket markets were generated.
        # This happens when the seed doc didn't trigger market creation.
        return jsonify({
            "success": True,
            "status": "completed_no_markets",
            "simulation_id": simulation_id,
            "message": "Simulation completed but no prediction markets were generated. "
                       "Try enabling Polymarket in the simulation config.",
        }), 200

    edge = round(swarm_price - market_yes_price, 4)
    abs_edge = abs(edge)

    recommendation = "YES" if edge > 0 else "NO"
    if abs_edge >= 0.10:
        confidence = "HIGH"
    elif abs_edge >= 0.04:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    direction = "above" if edge > 0 else "below"
    reasoning = (
        f"The swarm ({swarm_price * 100:.1f}%) settled {direction} the Polymarket price "
        f"({market_yes_price * 100:.1f}%) by {abs_edge * 100:.1f} pp. "
        + ("Strong edge — likely tradeable." if abs_edge >= 0.10
           else "Moderate edge — check liquidity before sizing." if abs_edge >= 0.04
           else "Weak edge — fees may eat the spread.")
    )

    result = {
        "success": True,
        "status": "completed",
        "simulation_id": simulation_id,
        "recommendation": recommendation,
        "swarm_price": swarm_price,
        "market_price": market_yes_price,
        "edge": edge,
        "confidence": confidence,
        "reasoning": reasoning,
        "condition_id": ctx.get("condition_id", ""),
        "title": ctx.get("title", ""),
    }

    # Persist result.json to /uploads/simulations/{sim_id}/ for later inspection
    out_dir = UPLOADS_DIR / "simulations" / simulation_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return jsonify(result)
```

- [ ] **Step 2: Confirm `SimulationRunner.RUN_STATE_DIR` is accessible as a Path**

Open `web/backend/app/services/simulation_runner.py` and check:

```python
RUN_STATE_DIR = os.path.join(
    os.path.dirname(__file__),
    '../../uploads/simulations'
)
```

In the result endpoint, `Path(SimulationRunner.RUN_STATE_DIR)` will resolve this correctly. No changes needed in `simulation_runner.py`.

- [ ] **Step 3: Test the endpoint manually**

Start the backend:
```bash
cd web && npm run backend
```

With a completed simulation (sim_xxxx) for a project (proj_xxxx), run:
```bash
curl http://localhost:5001/api/polymarket/simulate/proj_xxxx/result
```

Expected response (if simulation is still running):
```json
{"success": true, "status": "running", "simulation_id": "sim_xxxx", "progress_percent": 45.0}
```

Expected response (if completed with markets):
```json
{
  "success": true,
  "status": "completed",
  "recommendation": "YES",
  "swarm_price": 0.73,
  "market_price": 0.61,
  "edge": 0.12,
  "confidence": "HIGH",
  "reasoning": "The swarm (73.0%) settled above the Polymarket price (61.0%) by 12.0 pp. Strong edge — likely tradeable."
}
```

- [ ] **Step 4: Commit**

```bash
git add web/backend/app/api/polymarket.py
git commit -m "feat: add GET /api/polymarket/simulate/<id>/result with swarm vs market edge"
```

---

## Task 7: Update ResearchView.vue to save context after graph upload

**Files:**
- Modify: `web/frontend/src/views/ResearchView.vue`

Currently `runSimulation()` uploads seed.md, gets a `project_id`, and navigates to `/process/:projectId`. We need to also POST the Polymarket market context to the new `/simulate/context` endpoint so the result endpoint can compute edge later.

- [ ] **Step 1: Update `runSimulation()` in ResearchView.vue**

Find the `runSimulation` function (around line 196 in `ResearchView.vue`) and replace it entirely:

```javascript
async function runSimulation() {
  simLoading.value = true
  simError.value = ''
  try {
    // 1. Upload seed.md to create a MiroShark project
    const blob = new Blob([seedText.value], { type: 'text/markdown' })
    const formData = new FormData()
    formData.append('files', blob, 'seed.md')
    formData.append('project_name', title)
    formData.append('simulation_requirement', `Polymarket prediction market simulation for: ${title}`)

    const uploadRes = await api.post('/api/graph/project/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    if (!uploadRes.data?.success || !uploadRes.data?.data?.project_id) {
      throw new Error(uploadRes.data?.error || 'Failed to create project')
    }

    const projectId = uploadRes.data.data.project_id

    // 2. Store Polymarket market context alongside the project for result computation
    await api.post('/api/polymarket/simulate/context', {
      project_id: projectId,
      market_id: marketId,
      condition_id: conditionId,
      title,
      yes_price: parseFloat(yesPrice || 0.5),
      resolution_date: '',
    })

    // 3. Save context to sessionStorage so BetPanel and ReportView can use it
    sessionStorage.setItem('polymarket_context', JSON.stringify({
      condition_id: conditionId,
      title,
      yes_price: yesPrice,
      project_id: projectId,
    }))

    // 4. Navigate to MiroShark simulation flow
    router.push({ name: 'Process', params: { projectId } })
  } catch (e) {
    simError.value = e.message || 'Failed to start simulation'
    simLoading.value = false
  }
}
```

Note: the current code has `res.data.success` — the axios response interceptor in `api/index.js` unwraps `response.data` automatically, so `uploadRes` is already the parsed body (`{ success, data: { project_id } }`). Adjust to `uploadRes.success` and `uploadRes.data?.project_id` if that's the case. Check by looking at the existing working call above it — it uses `res.data.success`, so the interceptor does NOT unwrap in this case (it only unwraps on `response.data.success !== undefined`). Keep `uploadRes.data.success` as written.

- [ ] **Step 2: Commit**

```bash
git add web/frontend/src/views/ResearchView.vue
git commit -m "feat: save polymarket context to backend after graph upload in ResearchView"
```

---

## Task 8: Add recommendation banner to ReportView.vue

**Files:**
- Modify: `web/frontend/src/views/ReportView.vue`

The simulation report page already shows `BetPanel`. We need to add a recommendation banner above it that shows the swarm edge calculation. It fetches from `/api/polymarket/simulate/<project_id>/result` when `polymarket_context` is in sessionStorage.

- [ ] **Step 1: Read ReportView.vue to understand its current structure**

```bash
head -100 web/frontend/src/views/ReportView.vue
```

Note where `<BetPanel />` is in the template (line 71). The banner goes above it.

- [ ] **Step 2: Add the recommendation banner to the template**

In `web/frontend/src/views/ReportView.vue`, find the `<BetPanel />` line in the template and add the banner before it:

```html
    <!-- Polymarket recommendation banner (only when coming from Polymarket flow) -->
    <div v-if="pmResult && pmResult.status === 'completed'" class="pm-recommendation-banner" :class="'banner-' + pmResult.recommendation.toLowerCase()">
      <div class="banner-verdict">
        BET {{ pmResult.recommendation }}
        <span class="banner-confidence">{{ pmResult.confidence }}</span>
      </div>
      <div class="banner-prices">
        <span>Swarm: {{ (pmResult.swarm_price * 100).toFixed(1) }}%</span>
        <span class="banner-sep">·</span>
        <span>Market: {{ (pmResult.market_price * 100).toFixed(1) }}%</span>
        <span class="banner-sep">·</span>
        <span :class="pmResult.edge > 0 ? 'edge-pos' : 'edge-neg'">
          Edge: {{ pmResult.edge > 0 ? '+' : '' }}{{ (pmResult.edge * 100).toFixed(1) }}%
        </span>
      </div>
      <div class="banner-reasoning">{{ pmResult.reasoning }}</div>
    </div>
    <div v-else-if="pmResult && pmResult.status !== 'completed'" class="pm-recommendation-banner banner-pending">
      <div class="banner-verdict">Simulation {{ pmResult.status }}…</div>
      <div v-if="pmResult.progress_percent" class="banner-prices">
        Progress: {{ pmResult.progress_percent }}%
      </div>
    </div>

    <BetPanel />
```

- [ ] **Step 3: Add the script logic to ReportView.vue**

In the `<script setup>` section of `ReportView.vue`, add:

```javascript
import { ref, onMounted } from 'vue'
import api from '../api/index.js'

const pmResult = ref(null)

onMounted(async () => {
  try {
    const raw = sessionStorage.getItem('polymarket_context')
    if (!raw) return
    const ctx = JSON.parse(raw)
    if (!ctx.project_id) return
    const res = await api.get(`/api/polymarket/simulate/${ctx.project_id}/result`)
    pmResult.value = res   // axios interceptor unwraps response.data
  } catch (e) {
    console.warn('Could not load Polymarket result:', e)
  }
})
```

If `ReportView.vue` already has an `onMounted`, add this logic inside the existing one instead of creating a duplicate.

- [ ] **Step 4: Add the banner styles**

In the `<style scoped>` section of `ReportView.vue`, add:

```css
.pm-recommendation-banner {
  margin: 16px 24px;
  padding: 16px 20px;
  border-radius: 8px;
  border: 2px solid;
  font-family: 'Courier New', monospace;
}

.banner-yes { border-color: #4ade80; background: #0f2a1a; }
.banner-no  { border-color: #f87171; background: #2a0f0f; }
.banner-pending { border-color: #facc15; background: #2a2010; }

.banner-verdict {
  font-size: 24px;
  font-weight: 900;
  letter-spacing: 2px;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.banner-yes .banner-verdict { color: #4ade80; }
.banner-no  .banner-verdict { color: #f87171; }
.banner-pending .banner-verdict { color: #facc15; }

.banner-confidence {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 12px;
  background: rgba(255,255,255,0.1);
  letter-spacing: 1px;
}

.banner-prices {
  font-size: 14px;
  color: #aaa;
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}

.banner-sep { color: #444; }
.edge-pos { color: #4ade80; font-weight: 700; }
.edge-neg { color: #f87171; font-weight: 700; }

.banner-reasoning {
  font-size: 13px;
  color: #666;
  line-height: 1.5;
}
```

- [ ] **Step 5: Commit**

```bash
git add web/frontend/src/views/ReportView.vue
git commit -m "feat: add swarm vs market recommendation banner to ReportView"
```

---

## Task 9: Fix BetPanel bet confirmation message

**Files:**
- Modify: `web/frontend/src/components/BetPanel.vue`

The spec says show "Tx hash + Polygonscan link" but Polymarket CLOB orders are off-chain limit orders — they have an `orderId`, not a `txHash`. The fix is to show the `orderId` clearly and link to the Polymarket event page (already done), with a clarifying label.

- [ ] **Step 1: Update the bet success section in BetPanel.vue**

Find the `v-if="placed"` section in `BetPanel.vue` (around line 60–77) and replace:

```html
    <div v-if="placed" class="bet-success">
      <div class="success-icon">✓</div>
      <div class="success-title">Bet placed on Polygon</div>
      <div class="success-detail">Order ID: {{ orderId }}</div>
      <a
        :href="`https://polymarket.com/event/${context.condition_id}`"
        target="_blank"
        class="poly-link"
      >
        View on Polymarket ↗
      </a>
    </div>
```

with:

```html
    <div v-if="placed" class="bet-success">
      <div class="success-icon">✓</div>
      <div class="success-title">Limit order submitted</div>
      <div class="success-detail">Order ID: {{ orderId }}</div>
      <div class="success-note">Off-chain CLOB order — fills when market crosses your price.</div>
      <a
        :href="`https://polymarket.com/event/${context.condition_id}`"
        target="_blank"
        class="poly-link"
      >
        Track on Polymarket ↗
      </a>
    </div>
```

Also add the style for `.success-note`:

```css
.success-note {
  font-size: 10px;
  color: #555;
  text-align: center;
  line-height: 1.4;
}
```

- [ ] **Step 2: Commit**

```bash
git add web/frontend/src/components/BetPanel.vue
git commit -m "fix: clarify off-chain CLOB order vs tx hash in BetPanel success state"
```

---

## Task 10: Add SIMULATION_PLATFORM env var and fix volume path label

**Files:**
- Modify: `docker-compose.yml`

Two remaining docker-compose issues: `SIMULATION_PLATFORM=polymarket` is absent (enables the Polymarket simulation module in MiroShark), and the `research_data` volume mounts to `/app/backend/uploads` but the spec describes `/data/research/`.

- [ ] **Step 1: Add SIMULATION_PLATFORM to docker-compose.yml**

In `docker-compose.yml`, in the `web` service `environment` block, add:

```yaml
      - SIMULATION_PLATFORM=polymarket
```

Place it after the existing `POLYMARKET_PRIVATE_KEY` line.

- [ ] **Step 2: The volume path is functionally correct — add a clarifying comment**

The volume `research_data:/app/backend/uploads` is correct because `UPLOADS_DIR` in `polymarket.py` and `Config.UPLOAD_FOLDER` in the backend both resolve to `/app/backend/uploads/`. The spec document referenced `/data/research/` as an example path, not a hardcoded requirement. Add a comment to the docker-compose volume section:

```yaml
volumes:
  research_data:       # mounted at /app/backend/uploads/ in the web container
  certbot_www:
  certbot_certs:
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: add SIMULATION_PLATFORM=polymarket env var; clarify volume mount path"
```

---

## Self-Review: Spec Coverage Check

| Spec Section | Covered by Task | Status |
|---|---|---|
| 5.1 Polymarket Scout — `/api/markets` | Already implemented | ✓ |
| 5.1 Score threshold `> 0.1` | Task 3 | ✓ |
| 5.2 Research Agent SSE | Already implemented | ✓ |
| 5.3 `/simulate` endpoint | Task 5 (context) + Task 6 (result) | ✓ |
| 5.3 `/simulate/{id}/result` — recommendation | Task 6 | ✓ |
| 5.3 `/bet` — CLOB order | Already implemented | ✓ |
| 5.4 `/` BetDiscovery | Already implemented | ✓ |
| 5.4 5-minute auto-refresh | Task 4 | ✓ |
| 5.4 Research progress page | Already implemented | ✓ |
| 5.4 Simulation dashboard | Existing MiroShark flow (unchanged) | ✓ |
| 5.4 Recommendation banner | Task 8 | ✓ |
| 5.4 Place Bet button | BetPanel in ReportView (already implemented) | ✓ |
| 6. User journey — bet placed shows link | Task 9 | ✓ |
| 7. `/data/simulations/{id}/result.json` | Task 6 (writes `uploads/simulations/{id}/result.json`) | ✓ |
| 8. Docker Compose single service | Already implemented | ✓ |
| 9. `SIMULATION_PLATFORM=polymarket` | Task 10 | ✓ |
| Prod URL bug (`/api/api/`) | Task 1 | ✓ |
| Dockerfile dev-in-prod | Task 2 | ✓ |

**Not in this plan (out of scope per spec section 13):**
- User auth, portfolio tracking, concurrent simulations, payment rails

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-05-20-polymarket-fix-and-complete.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Use `superpowers:subagent-driven-development`.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans` with checkpoints.

Tasks 1–4 are pure bug fixes and take under 5 minutes each. Tasks 5–6 (backend) can be done together. Tasks 7–9 (frontend) can be done together. Task 10 is a 2-line config change.

**Which approach?**
