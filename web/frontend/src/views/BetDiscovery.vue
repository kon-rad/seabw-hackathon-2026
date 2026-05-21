<template>
  <div class="bet-discovery">
    <nav class="navbar">
      <div class="nav-brand">SimulationAgent <span class="nav-sub">× POLYMARKET</span></div>
      <div class="nav-links">
        <a href="https://polymarket.com" target="_blank" class="explore-link">Polymarket ↗</a>
        <a href="https://github.com/aaronjmars/MiroShark" target="_blank" class="github-link">GitHub ↗</a>
      </div>
    </nav>

    <div class="page-content">
      <div class="hero">
        <h1>Find the <span class="highlight">best bets</span> to simulate</h1>
        <p class="sub">AI scouts live Polymarket markets, scores them for research quality, and runs a full swarm simulation to find an edge.</p>
      </div>

      <div v-if="loading" class="loading-state">
        <div class="spinner">◌</div>
        <p>Scanning Polymarket for the best opportunities…</p>
      </div>

      <div v-if="error" class="error-banner">{{ error }}</div>

      <div v-if="!loading && markets.length" class="markets-grid">
        <div
          v-for="m in markets"
          :key="m.id"
          class="market-card"
          @click="selectMarket(m)"
        >
          <div class="card-top">
            <span class="score-badge" :class="scoreBadgeClass(m.score)">{{ m.score }}</span>
            <span v-if="m.category" class="category-tag">{{ m.category }}</span>
          </div>
          <h3 class="card-title">{{ m.title }}</h3>
          <div v-if="m.sim_tags && m.sim_tags.length" class="sim-tags">
            <span
              v-for="tag in m.sim_tags"
              :key="tag"
              class="sim-tag"
              :class="tag.endsWith('⚠') ? 'sim-tag-warn' : 'sim-tag-ok'"
            >{{ tag }}</span>
          </div>
          <p class="card-desc">{{ m.description }}</p>
          <div class="card-meta">
            <div class="meta-item">
              <span class="meta-label">YES</span>
              <span class="meta-value yes">{{ formatPct(m.yes_price) }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Volume</span>
              <span class="meta-value">{{ formatVol(m.volume) }}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Resolves</span>
              <span class="meta-value">{{ formatDate(m.end_date) }}</span>
            </div>
          </div>
          <button class="research-btn">Research &amp; Simulate →</button>
        </div>
      </div>

      <div v-if="!loading && !markets.length && !error" class="empty-state">
        No markets found. Try refreshing.
      </div>

      <button class="refresh-btn" @click="fetchMarkets" :disabled="loading">
        {{ loading ? 'Loading…' : '↻ Refresh Markets' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api/index.js'

const router = useRouter()
const markets = ref([])
const loading = ref(false)
const error = ref('')

async function fetchMarkets() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get('/api/polymarket/markets', { params: { _t: Date.now() } })
    markets.value = res.markets || []
  } catch (e) {
    error.value = e.message || 'Failed to load markets'
  } finally {
    loading.value = false
  }
}

function selectMarket(m) {
  router.push({
    name: 'Research',
    params: { marketId: m.id },
    query: { title: m.title, description: m.description, condition_id: m.condition_id, yes_price: m.yes_price },
  })
}

function formatPct(v) {
  if (v == null) return '—'
  return (parseFloat(v) * 100).toFixed(1) + '%'
}

function formatVol(v) {
  if (!v) return '—'
  const n = parseFloat(v)
  if (n >= 1_000_000) return '$' + (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return '$' + (n / 1_000).toFixed(0) + 'K'
  return '$' + n.toFixed(0)
}

function formatDate(s) {
  if (!s) return '—'
  try {
    return new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch { return s }
}

function scoreBadgeClass(score) {
  if (score >= 80) return 'badge-green'
  if (score >= 50) return 'badge-yellow'
  return 'badge-gray'
}

let refreshTimer = null

onMounted(() => {
  fetchMarkets()
  refreshTimer = setInterval(fetchMarkets, 5 * 60 * 1000)
})

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})
</script>

<style scoped>
.bet-discovery {
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

.nav-brand {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 2px;
  color: #ff6b35;
}

.nav-sub {
  color: #888;
  font-size: 13px;
  font-weight: 400;
}

.nav-links { display: flex; gap: 20px; }
.nav-links a { color: #888; text-decoration: none; font-size: 13px; }
.nav-links a:hover { color: #e8e8e8; }

.page-content {
  max-width: 1100px;
  margin: 0 auto;
  padding: 48px 24px;
}

.hero { margin-bottom: 48px; }
.hero h1 { font-size: 36px; font-weight: 700; margin: 0 0 12px; }
.highlight { color: #ff6b35; }
.sub { color: #888; font-size: 15px; margin: 0; }

.loading-state {
  text-align: center;
  padding: 80px 0;
  color: #888;
}

.spinner {
  font-size: 32px;
  animation: spin 1.5s linear infinite;
  display: inline-block;
  margin-bottom: 16px;
}

@keyframes spin { to { transform: rotate(360deg); } }

.error-banner {
  background: #2a1010;
  border: 1px solid #5a2020;
  color: #ff6b6b;
  padding: 12px 16px;
  border-radius: 4px;
  margin-bottom: 24px;
}

.markets-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 20px;
  margin-bottom: 32px;
}

.market-card {
  background: #111;
  border: 1px solid #222;
  border-radius: 8px;
  padding: 20px;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.15s;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.market-card:hover {
  border-color: #ff6b35;
  transform: translateY(-2px);
}

.card-top {
  display: flex;
  gap: 8px;
  align-items: center;
}

.score-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 12px;
}

.badge-green { background: #0f2a1a; color: #4ade80; }
.badge-yellow { background: #2a2010; color: #facc15; }
.badge-gray { background: #1a1a1a; color: #888; }

.category-tag {
  font-size: 11px;
  color: #888;
  background: #1a1a1a;
  padding: 2px 8px;
  border-radius: 12px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  line-height: 1.4;
  color: #e8e8e8;
}

.sim-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.sim-tag {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.sim-tag-ok {
  background: #0a1f12;
  color: #4ade80;
  border: 1px solid #1a3a22;
}

.sim-tag-warn {
  background: #1f1205;
  color: #fb923c;
  border: 1px solid #3a2010;
}

.card-desc {
  font-size: 12px;
  color: #666;
  margin: 0;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-meta {
  display: flex;
  gap: 16px;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.meta-label {
  font-size: 10px;
  color: #555;
  text-transform: uppercase;
  letter-spacing: 1px;
}

.meta-value {
  font-size: 14px;
  font-weight: 600;
}

.meta-value.yes { color: #4ade80; }

.research-btn {
  margin-top: auto;
  background: #ff6b35;
  color: #000;
  border: none;
  padding: 10px 16px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  text-align: center;
  transition: background 0.2s;
}

.research-btn:hover { background: #ff8c5a; }

.empty-state {
  text-align: center;
  padding: 60px;
  color: #555;
}

.refresh-btn {
  background: transparent;
  border: 1px solid #333;
  color: #888;
  padding: 10px 20px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  border-color: #ff6b35;
  color: #ff6b35;
}

.refresh-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
