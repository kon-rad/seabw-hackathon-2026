<template>
  <div v-if="context" class="bet-panel">
    <div class="bet-panel-header">
      <span class="bet-label">POLYMARKET BET</span>
      <button class="close-btn" @click="dismiss">×</button>
    </div>

    <div class="bet-title">{{ context.title }}</div>

    <div v-if="!placed" class="bet-form">
      <div class="outcome-row">
        <button
          class="outcome-btn"
          :class="{ active: outcome === 'YES' }"
          @click="outcome = 'YES'"
        >
          YES {{ formatPct(context.yes_price) }}
        </button>
        <button
          class="outcome-btn no"
          :class="{ active: outcome === 'NO' }"
          @click="outcome = 'NO'"
        >
          NO {{ formatPct(1 - parseFloat(context.yes_price || 0.5)) }}
        </button>
      </div>

      <div class="amount-row">
        <label class="amount-label">USDC Amount</label>
        <input
          v-model.number="amount"
          type="number"
          min="1"
          class="amount-input"
          placeholder="10"
        />
      </div>

      <div class="price-row">
        <label class="amount-label">Limit Price</label>
        <input
          v-model.number="limitPrice"
          type="number"
          min="0.01"
          max="0.99"
          step="0.01"
          class="amount-input"
          placeholder="0.61"
        />
      </div>

      <div class="summary">
        Buying ~{{ estimatedShares }} shares at {{ formatPct(limitPrice) }}
      </div>

      <div v-if="betError" class="bet-error">{{ betError }}</div>

      <button class="place-btn" :disabled="loading" @click="placeBet">
        {{ loading ? 'Placing bet…' : `Place ${outcome} Bet →` }}
      </button>
    </div>

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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api/index.js'

const context = ref(null)
const outcome = ref('YES')
const amount = ref(10)
const limitPrice = ref(0.5)
const loading = ref(false)
const betError = ref('')
const placed = ref(false)
const orderId = ref('')

const estimatedShares = computed(() => {
  if (!limitPrice.value || !amount.value) return '—'
  return (amount.value / limitPrice.value).toFixed(2)
})

function formatPct(v) {
  if (v == null) return '—'
  return (parseFloat(v) * 100).toFixed(1) + '%'
}

function dismiss() {
  context.value = null
  sessionStorage.removeItem('polymarket_context')
}

async function placeBet() {
  betError.value = ''
  if (!amount.value || amount.value <= 0) { betError.value = 'Enter a valid amount'; return }
  if (!limitPrice.value || limitPrice.value <= 0 || limitPrice.value >= 1) { betError.value = 'Price must be between 0.01 and 0.99'; return }

  loading.value = true
  try {
    const res = await api.post('/api/polymarket/bet', {
      condition_id: context.value.condition_id,
      outcome: outcome.value,
      usdc_amount: amount.value,
      price: limitPrice.value,
    })
    if (res.data.success) {
      placed.value = true
      orderId.value = res.data.order_id
    } else {
      betError.value = res.data.error || 'Bet failed'
    }
  } catch (e) {
    betError.value = e.message || 'Bet failed'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  try {
    const raw = sessionStorage.getItem('polymarket_context')
    if (raw) {
      context.value = JSON.parse(raw)
      limitPrice.value = parseFloat(context.value.yes_price || 0.5)
    }
  } catch { /* ignore */ }
})
</script>

<style scoped>
.bet-panel {
  position: fixed;
  bottom: 24px;
  right: 24px;
  width: 320px;
  background: #111;
  border: 1px solid #ff6b35;
  border-radius: 8px;
  padding: 16px;
  z-index: 1000;
  font-family: 'Courier New', monospace;
  box-shadow: 0 0 24px rgba(255, 107, 53, 0.2);
}

.bet-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.bet-label {
  font-size: 10px;
  letter-spacing: 2px;
  color: #ff6b35;
  font-weight: 700;
}

.close-btn {
  background: transparent;
  border: none;
  color: #555;
  font-size: 18px;
  cursor: pointer;
  line-height: 1;
  padding: 0;
}
.close-btn:hover { color: #e8e8e8; }

.bet-title {
  font-size: 13px;
  color: #aaa;
  margin-bottom: 14px;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.bet-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.outcome-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.outcome-btn {
  padding: 8px;
  border: 1px solid #333;
  background: #1a1a1a;
  color: #888;
  border-radius: 4px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.outcome-btn.active { border-color: #4ade80; color: #4ade80; background: #0f2a1a; }
.outcome-btn.no.active { border-color: #f87171; color: #f87171; background: #2a0f0f; }

.amount-label {
  font-size: 10px;
  letter-spacing: 1px;
  color: #555;
  text-transform: uppercase;
  display: block;
  margin-bottom: 4px;
}

.amount-input {
  width: 100%;
  background: #1a1a1a;
  border: 1px solid #333;
  color: #e8e8e8;
  padding: 8px 10px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 14px;
  box-sizing: border-box;
}
.amount-input:focus { outline: none; border-color: #ff6b35; }

.summary {
  font-size: 11px;
  color: #555;
}

.bet-error {
  font-size: 12px;
  color: #f87171;
  background: #2a0f0f;
  padding: 6px 10px;
  border-radius: 4px;
}

.place-btn {
  background: #ff6b35;
  color: #000;
  border: none;
  padding: 11px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.2s, opacity 0.2s;
}
.place-btn:hover:not(:disabled) { background: #ff8c5a; }
.place-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.bet-success {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px 0 8px;
}

.success-icon {
  font-size: 32px;
  color: #4ade80;
}

.success-title {
  font-size: 15px;
  font-weight: 700;
  color: #4ade80;
}

.success-detail {
  font-size: 11px;
  color: #555;
}

.poly-link {
  font-size: 12px;
  color: #ff6b35;
  text-decoration: none;
  margin-top: 4px;
}
.poly-link:hover { text-decoration: underline; }
</style>
