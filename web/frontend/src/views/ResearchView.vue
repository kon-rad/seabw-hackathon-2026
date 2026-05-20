<template>
  <div class="research-view">
    <nav class="navbar">
      <div class="nav-brand">MIROSHARK <span class="nav-sub">× POLYMARKET</span></div>
      <button class="back-btn" @click="router.push({ name: 'BetDiscovery' })">← Back</button>
    </nav>

    <div class="page-content">
      <!-- Market header -->
      <div class="market-header">
        <div class="market-title">{{ title }}</div>
        <div class="market-meta">
          <span class="yes-price">YES: {{ formatPct(yesPrice) }}</span>
        </div>
      </div>

      <!-- Status bar -->
      <div class="status-bar">
        <div class="status-indicator" :class="statusClass">{{ statusLabel }}</div>
        <div class="round-dots">
          <span
            v-for="n in 4"
            :key="n"
            class="dot"
            :class="{ done: completedRounds >= n, active: currentRound === n }"
          />
        </div>
      </div>

      <!-- Two-column layout: sources + seed preview -->
      <div class="columns">
        <!-- Sources list -->
        <div class="panel sources-panel">
          <div class="panel-title">Sources Found</div>
          <div v-if="!sources.length" class="panel-empty">Waiting for research to start…</div>
          <div v-for="(s, i) in sources" :key="i" class="source-item">
            <span class="source-round">R{{ s.round }}</span>
            <a :href="s.url" target="_blank" class="source-link">{{ s.title || s.url }}</a>
          </div>
        </div>

        <!-- Seed.md preview -->
        <div class="panel seed-panel">
          <div class="panel-title">Research Document</div>
          <div v-if="!seedText" class="panel-empty">Building seed document…</div>
          <pre class="seed-content">{{ seedText }}</pre>
        </div>
      </div>

      <!-- Round progress -->
      <div v-if="roundMessages.length" class="round-log">
        <div v-for="(msg, i) in roundMessages" :key="i" class="log-entry" :class="msg.type">
          <span class="log-icon">{{ logIcon(msg.type) }}</span>
          <span class="log-text">{{ msg.text }}</span>
        </div>
      </div>

      <!-- Error -->
      <div v-if="researchError" class="error-banner">{{ researchError }}</div>

      <!-- Actions -->
      <div class="actions">
        <button
          v-if="!started && !done"
          class="action-btn primary"
          @click="startResearch"
        >
          Start Research →
        </button>

        <div v-if="started && !done" class="running-state">
          <span class="spinner">◌</span> Researching… ({{ completedRounds }}/4 rounds)
        </div>

        <button
          v-if="done"
          class="action-btn primary"
          @click="runSimulation"
          :disabled="simLoading"
        >
          {{ simLoading ? 'Starting simulation…' : 'Run MiroShark Simulation →' }}
        </button>
      </div>

      <div v-if="simError" class="error-banner">{{ simError }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api/index.js'

const route = useRoute()
const router = useRouter()

const marketId = route.params.marketId
const title = route.query.title || marketId
const description = route.query.description || ''
const conditionId = route.query.condition_id || ''
const yesPrice = route.query.yes_price || null

const sources = ref([])
const seedText = ref('')
const roundMessages = ref([])
const currentRound = ref(0)
const completedRounds = ref(0)
const started = ref(false)
const done = ref(false)
const researchError = ref('')
const simLoading = ref(false)
const simError = ref('')
let eventSource = null

const statusLabel = computed(() => {
  if (done.value) return '✓ Research complete'
  if (currentRound.value === 0 && started.value) return 'Fetching live data…'
  if (started.value) return `Round ${currentRound.value}/4`
  return 'Ready to research'
})

const statusClass = computed(() => {
  if (done.value) return 'status-done'
  if (started.value) return 'status-running'
  return 'status-idle'
})

function formatPct(v) {
  if (v == null) return '—'
  return (parseFloat(v) * 100).toFixed(1) + '%'
}

function logIcon(type) {
  const icons = { round_start: '▶', source_found: '◈', round_complete: '✓', research_complete: '★', error: '✗' }
  return icons[type] || '·'
}

function startResearch() {
  started.value = true
  researchError.value = ''
  const params = new URLSearchParams({ title, description })
  const url = `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:5001'}/api/polymarket/research/${encodeURIComponent(marketId)}?${params}`
  eventSource = new EventSource(url)

  eventSource.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      handleEvent(msg)
    } catch { /* ignore malformed */ }
  }

  eventSource.onerror = () => {
    if (!done.value) {
      researchError.value = 'Connection lost. Research may have completed — check the document above.'
    }
    eventSource?.close()
  }
}

function handleEvent(msg) {
  switch (msg.type) {
    case 'start':
      roundMessages.value.push({ type: 'start', text: `Started researching: ${msg.title}` })
      break
    case 'round_start':
      currentRound.value = msg.round
      roundMessages.value.push({
        type: 'round_start',
        text: msg.round === 0
          ? `Fetching live data — "${msg.query}"`
          : `Round ${msg.round}: ${msg.label} — "${msg.query}"`,
      })
      break
    case 'source_found':
      sources.value.push({ url: msg.url, title: msg.title, round: msg.round })
      break
    case 'round_complete':
      if (msg.round > 0) completedRounds.value = msg.round
      if (msg.facts) {
        const label = msg.round === 0 ? 'Live Market Data' : `Round ${msg.round}`
        seedText.value += (seedText.value ? '\n\n' : '') + `## ${label}\n${msg.facts}`
      }
      roundMessages.value.push({
        type: 'round_complete',
        text: msg.round === 0 ? 'Live data fetched' : `Round ${msg.round} complete`,
      })
      break
    case 'research_complete':
      done.value = true
      if (msg.seed_md) seedText.value = msg.seed_md
      roundMessages.value.push({ type: 'research_complete', text: `Research complete — ${msg.source_count} sources gathered` })
      eventSource?.close()
      break
  }
}

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

    const res = await api.post('/api/graph/ontology/generate', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    if (res.data?.project_id) {
      const projectId = res.data.project_id

      // 2. Store Polymarket market context alongside the project for result computation
      try {
        await api.post('/api/polymarket/simulate/context', {
          project_id: projectId,
          market_id: marketId,
          condition_id: conditionId,
          title,
          yes_price: parseFloat(yesPrice || 0.5),
          resolution_date: '',
        })
      } catch (ctxErr) {
        console.warn('Could not save polymarket context:', ctxErr)
      }

      // 3. Save context to sessionStorage so ReportView can fetch the recommendation
      sessionStorage.setItem('polymarket_context', JSON.stringify({
        condition_id: conditionId,
        title,
        yes_price: yesPrice,
        project_id: projectId,
      }))

      // 4. Navigate to MiroShark simulation flow
      router.push({ name: 'Process', params: { projectId } })
    } else {
      throw new Error(res.error || 'Failed to create project — no project_id returned')
    }
  } catch (e) {
    simError.value = e.message || 'Failed to start simulation'
    simLoading.value = false
  }
}

import { onBeforeUnmount } from 'vue'
onBeforeUnmount(() => eventSource?.close())
</script>

<style scoped>
.research-view {
  min-height: 100vh;
  background: #0a0a0a;
  color: #e8e8e8;
  font-family: 'Courier New', monospace;
}

.navbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 32px;
  border-bottom: 1px solid #1e1e1e;
}

.nav-brand { font-size: 18px; font-weight: 700; letter-spacing: 2px; color: #ff6b35; }
.nav-sub { color: #888; font-size: 13px; font-weight: 400; }
.back-btn {
  background: transparent;
  border: 1px solid #333;
  color: #888;
  padding: 6px 14px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.back-btn:hover { color: #e8e8e8; border-color: #555; }

.page-content {
  max-width: 1100px;
  margin: 0 auto;
  padding: 40px 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.market-header {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  justify-content: space-between;
}

.market-title {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.4;
  flex: 1;
}

.yes-price {
  font-size: 20px;
  font-weight: 700;
  color: #4ade80;
  white-space: nowrap;
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  background: #111;
  border: 1px solid #222;
  border-radius: 6px;
}

.status-indicator {
  font-size: 13px;
  font-weight: 600;
}
.status-idle { color: #666; }
.status-running { color: #facc15; }
.status-done { color: #4ade80; }

.round-dots { display: flex; gap: 8px; margin-left: auto; }
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #222;
  border: 1px solid #333;
  transition: background 0.3s;
}
.dot.done { background: #4ade80; border-color: #4ade80; }
.dot.active { background: #facc15; border-color: #facc15; }

.columns {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 16px;
  min-height: 320px;
}

.panel {
  background: #111;
  border: 1px solid #222;
  border-radius: 6px;
  padding: 16px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.panel-title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 2px;
  color: #555;
  border-bottom: 1px solid #1e1e1e;
  padding-bottom: 8px;
}

.panel-empty { color: #444; font-size: 13px; }

.source-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
}

.source-round {
  font-size: 10px;
  color: #ff6b35;
  background: #2a1a10;
  padding: 1px 5px;
  border-radius: 3px;
  white-space: nowrap;
  margin-top: 1px;
}

.source-link {
  color: #888;
  text-decoration: none;
  word-break: break-all;
  line-height: 1.4;
}
.source-link:hover { color: #e8e8e8; }

.seed-content {
  font-family: inherit;
  font-size: 12px;
  color: #aaa;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-y: auto;
  flex: 1;
  margin: 0;
  max-height: 400px;
}

.round-log {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 200px;
  overflow-y: auto;
}

.log-entry {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  color: #666;
  padding: 4px 0;
  border-bottom: 1px solid #111;
}

.log-icon { color: #ff6b35; width: 14px; flex-shrink: 0; }
.log-entry.research_complete { color: #4ade80; }
.log-entry.round_complete { color: #888; }
.log-entry.round_start { color: #facc15; }
.log-entry.source_found { color: #555; }

.error-banner {
  background: #2a1010;
  border: 1px solid #5a2020;
  color: #ff6b6b;
  padding: 12px 16px;
  border-radius: 4px;
  font-size: 13px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.action-btn {
  padding: 12px 28px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  border: none;
  transition: background 0.2s, opacity 0.2s;
}

.action-btn.primary {
  background: #ff6b35;
  color: #000;
}
.action-btn.primary:hover:not(:disabled) { background: #ff8c5a; }
.action-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.running-state {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #facc15;
  font-size: 14px;
}

.spinner {
  animation: spin 1.5s linear infinite;
  display: inline-block;
  font-size: 18px;
}

@keyframes spin { to { transform: rotate(360deg); } }

.sources-panel { overflow-y: auto; }
</style>
