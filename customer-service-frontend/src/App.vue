<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'

const senderId = ref('u1001')
const draftMessage = ref('')
const isSending = ref(false)
const errorMessage = ref('')
const messages = ref([])
const messagesContainer = ref(null)
const loadedHistoryCount = ref(0)
const currentPageDividerInserted = ref(false)

const orders = ref([])
const products = ref([])
const isLoadingSidebar = ref(false)
const sidebarError = ref('')
const activeTab = ref('orders')

// Copy state
const copyState = ref({})

/* 封面图来自业务中台, 是外部地址, 可能加载失败。
   记下失败的 URL, 让 v-if 落到占位块——只把 <img> 隐藏会留下一个空壳。 */
const failedImages = reactive(new Set())

function imageLoaded(url) {
  return Boolean(url) && !failedImages.has(url)
}

function markImageFailed(url) {
  if (url) {
    failedImages.add(url)
  }
}

/* 头像用内联 SVG data URI, 不依赖任何外部图床。
   原来的客服头像指向教程项目的阿里云 OSS, 已经返回 403;
   用户头像走 dicebear API, 同样是外部依赖。
   色值取自附录 D.1: --brand / --surface-secondary / --on-solid / --text-secondary */
function initialAvatar(initial, background, foreground) {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">' +
    `<circle cx="32" cy="32" r="32" fill="${background}"/>` +
    '<text x="32" y="32" text-anchor="middle" dominant-baseline="central" ' +
    'font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif" ' +
    `font-size="28" font-weight="600" fill="${foreground}">${initial}</text></svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}

// 客服配置
const customerService = {
  name: 'Iris',
  title: 'Senior Support',
  avatar: initialAvatar('I', '#1877f2', '#ffffff'),
  status: 'Online'
}

// 用户配置
const userProfile = {
  name: 'You',
  avatar: initialAvatar('Y', '#f0f2f5', '#65676b')
}

/* ── 控制台导航（附录 D.3）──────────────────────────────────
   「对话」是唯一已实现的页面；其余三项属于第二/第三档，
   这里只做占位入口：可见、可点、点了显示「敬请期待」。 */
const NAV_ITEMS = [
  {
    key: 'chat',
    label: 'Chat',
    ready: true,
    icon: 'M10 2C5.6 2 2 5.1 2 8.9c0 2.2 1.2 4.1 3 5.3V18l2.9-1.6c.7.2 1.4.2 2.1.2 4.4 0 8-3.1 8-6.9S14.4 2 10 2z',
  },
  {
    key: 'knowledge',
    label: 'Knowledge',
    ready: false,
    icon: 'M4 3h4.5A2.5 2.5 0 0 1 11 5.5V17a2.5 2.5 0 0 0-2.5-2H4V3zm12 0h-4.5A2.5 2.5 0 0 0 9 5.5V17a2.5 2.5 0 0 1 2.5-2H16V3z',
  },
  {
    key: 'skills',
    label: 'Skills',
    ready: false,
    icon: 'M11 2 4 11h4l-1 7 7-9h-4l1-7z',
  },
  {
    key: 'handover',
    label: 'Handoff',
    ready: false,
    icon: 'M10 2a7 7 0 0 0-7 7v5a2 2 0 0 0 2 2h1a1 1 0 0 0 1-1v-4a1 1 0 0 0-1-1H5V9a5 5 0 0 1 10 0v1h-1a1 1 0 0 0-1 1v4a1 1 0 0 0 1 1h1a2 2 0 0 0 2-2V9a7 7 0 0 0-7-7z',
  },
]

const activeNav = ref('chat')

const activeNavItem = computed(
  () => NAV_ITEMS.find((item) => item.key === activeNav.value) ?? NAV_ITEMS[0]
)

/* 调试开关（附录 D.5）：恢复完整 Turn 卡片。默认关闭。 */
const showTurnCards = ref(false)

/* ── 控制权（附录 E.2 第 3 条 / 需求 3.3.4）───────────────── */
const CONTROL_OWNERS = {
  AGENT: {
    label: 'Agent handling',
    tone: 'control-agent',
    notice: '',
  },
  PENDING_HUMAN: {
    label: 'Waiting for a human agent',
    tone: 'control-pending',
    notice: 'Handed off to a human agent. You can keep sending messages while you wait.',
  },
  HUMAN: {
    label: 'Human agent handling',
    tone: 'control-human',
    notice: 'A human agent has taken over this conversation. Input is locked until they reply.',
  },
}

// control_owner 是会话级字段，挂在响应顶层；后端尚未下发时缺省 AGENT
const controlOwner = ref('AGENT')

const controlOwnerMeta = computed(
  () => CONTROL_OWNERS[controlOwner.value] ?? CONTROL_OWNERS.AGENT
)
const isHumanControlled = computed(() => controlOwner.value === 'HUMAN')
const isComposerLocked = computed(() => isSending.value || isHumanControlled.value)

function normalizeControlOwner(value) {
  if (typeof value !== 'string') {
    return 'AGENT'
  }
  const upper = value.toUpperCase()
  return upper in CONTROL_OWNERS ? upper : 'AGENT'
}

function applyControlOwner(payload) {
  controlOwner.value = normalizeControlOwner(payload?.control_owner)
}

// 将消息分组为 Turn 结构
const turns = computed(() => {
  const result = []
  let currentTurn = null
  let turnIndex = 0

  for (const message of messages.value) {
    if (message.type === 'divider') {
      if (currentTurn) {
        result.push(currentTurn)
        currentTurn = null
      }
      result.push({
        type: 'divider',
        text: message.text
      })
      continue
    }

    if (message.role === 'user') {
      // 如果有待处理的当前 turn，先保存
      if (currentTurn) {
        result.push(currentTurn)
      }
      // 创建新的 turn
      turnIndex++
      currentTurn = {
        type: 'turn',
        id: `turn-${turnIndex}`,
        index: turnIndex,
        userMessage: message,
        botMessages: []
      }
    } else if (message.role === 'bot') {
      if (!currentTurn) {
        // 如果没有当前 turn，创建一个（可能是因为历史消息）
        turnIndex++
        currentTurn = {
          type: 'turn',
          id: `turn-${turnIndex}`,
          index: turnIndex,
          userMessage: null,
          botMessages: []
        }
      }
      currentTurn.botMessages.push(message)
    }
  }

  if (currentTurn) {
    result.push(currentTurn)
  }

  return result
})

// 一个 turn 内的消息按时间顺序摊平，用户与客服共用同一套渲染路径
function turnMessages(turn) {
  return turn.userMessage ? [turn.userMessage, ...turn.botMessages] : turn.botMessages
}

function messageAvatar(message) {
  return message.role === 'user' ? userProfile.avatar : customerService.avatar
}

function messageName(message) {
  return message.role === 'user' ? userProfile.name : customerService.name
}

function messageRoleLabel(message) {
  return message.role === 'user' ? 'You' : customerService.title
}

const chatEndpoint = computed(() => '/api/chat')
const chatHistoryEndpoint = computed(
  () => `/api/chat/history?sender_id=${encodeURIComponent(senderId.value.trim())}`
)
const commerceOrdersEndpoint = computed(
  () => `/commerce/users/${encodeURIComponent(senderId.value.trim())}/orders`
)
const commerceProductsEndpoint = computed(
  () => `/commerce/users/${encodeURIComponent(senderId.value.trim())}/products`
)

function createBaseMessage(role) {
  return {
    id: crypto.randomUUID(),
    role,
  }
}

/**
 * 附录 E.2 第 2 条：object 与 cards 不并存。
 * 后端要么发 cards（列表，新路径），要么发 object（单个，旧路径）。
 * 归一化到统一的 cards 列表后，前端只有一套渲染路径。
 */
function normalizeCards(message) {
  const cards = Array.isArray(message.cards) ? message.cards : []
  if (cards.length > 0) {
    return cards
  }
  return message.object ? [message.object] : []
}

/* 快捷回复归一化。协议形态是 { label, value }：label 显示，value 是点击后
   发出去的内容。两者允许不同，因为按钮文案同时是回传给规划器的用户输入，
   而有些东西不该印在按钮上——写着 "Track this order" 的按钮要把订单号一起
   带过去，否则物流流程会再问一遍机器人上一句刚说出口的订单号。

   同时兼容裸字符串：本次改动之前落库的历史消息里 suggestions 是 string[]，
   老会话读回来不该变成一排空按钮。缺 label 或缺 value 时互相回退，
   配置写了一半也退化成一个能用的按钮而不是空白。 */
function normalizeSuggestions(message) {
  if (!Array.isArray(message.suggestions)) return []
  return message.suggestions
    .map((item) => {
      if (item && typeof item === 'object') {
        const label = item.label ?? item.value ?? ''
        const value = item.value ?? item.label ?? ''
        return { label: String(label), value: String(value) }
      }
      const text = String(item ?? '')
      return { label: text, value: text }
    })
    .filter((item) => item.label !== '')
}

function insertCurrentPageDividerIfNeeded() {
  if (currentPageDividerInserted.value || loadedHistoryCount.value === 0) {
    return
  }

  appendMessage('divider', { text: 'End of earlier messages' })
  currentPageDividerInserted.value = true
}

function appendUserText(text) {
  insertCurrentPageDividerIfNeeded()
  messages.value.push({
    ...createBaseMessage('user'),
    type: 'message',
    text,
    cards: [],
    suggestions: [],
  })
}

function appendUserCard(card) {
  insertCurrentPageDividerIfNeeded()
  messages.value.push({
    ...createBaseMessage('user'),
    type: 'message',
    text: '',
    cards: [card],
    suggestions: [],
  })
}

function appendBotMessages(botMessages) {
  for (const message of botMessages) {
    appendMessage('bot', message)
  }
}

/**
 * 一条消息可以同时有 text、cards、suggestions（附录 E.2 第 1 条）。
 * 旧的二选一写法会在带卡片时丢掉快捷回复，这里改为三段共存。
 */
function appendMessage(role, message) {
  if (role === 'divider') {
    messages.value.push({
      ...createBaseMessage('divider'),
      type: 'divider',
      text: message.text ?? 'End of earlier messages',
    })
    return
  }

  messages.value.push({
    ...createBaseMessage(role),
    type: 'message',
    text: message.text ?? '',
    cards: normalizeCards(message),
    suggestions: normalizeSuggestions(message),
  })
}

function setHistoryMessages(historyMessages) {
  messages.value = []
  currentPageDividerInserted.value = false
  for (const message of historyMessages) {
    if (message.role === 'divider') {
      continue
    }
    const role = ['user', 'bot'].includes(message.role) ? message.role : 'bot'
    appendMessage(role, message)
  }
  loadedHistoryCount.value = messages.value.length
}

async function scrollToBottom() {
  await nextTick()
  const container = messagesContainer.value
  if (!container) {
    return
  }
  container.scrollTop = container.scrollHeight
}

watch(
  () => messages.value.length,
  async () => {
    await scrollToBottom()
  }
)

function resetConversation() {
  messages.value = []
  loadedHistoryCount.value = 0
  currentPageDividerInserted.value = false
  errorMessage.value = ''
  controlOwner.value = 'AGENT'
}

function formatAmount(amount) {
  const numericAmount = Number(amount)
  if (Number.isNaN(numericAmount)) {
    return '￥0.00'
  }
  return `￥${numericAmount.toFixed(2)}`
}

const ORDER_STATUS_CLASS = {
  '待支付': 'status-warning',
  '待发货': 'status-warning',
  '待揽收': 'status-warning',
  '运输中': 'status-info',
  '派送中': 'status-info',
  '已完成': 'status-success',
  '已签收': 'status-success',
  '已取消': 'status-muted',
  '退款中': 'status-danger',
  '已退款': 'status-muted',
}

function getStatusClass(status) {
  return ORDER_STATUS_CLASS[status] || 'status-muted'
}

/* 订单状态由业务中台返回, 值是中文。这里只做展示层翻译,
   查表的键必须保持中文原值, 未知状态原样显示。 */
const ORDER_STATUS_LABEL = {
  '待支付': 'Awaiting payment',
  '待发货': 'Awaiting shipment',
  '待揽收': 'Awaiting pickup',
  '运输中': 'In transit',
  '派送中': 'Out for delivery',
  '已完成': 'Completed',
  '已签收': 'Delivered',
  '已取消': 'Cancelled',
  '退款中': 'Refunding',
  '已退款': 'Refunded',
}

function statusLabel(status) {
  return ORDER_STATUS_LABEL[status] || status || ''
}

/* ── ChatObject 读取（附录 E.1）────────────────────────────
   规范形态是 { id, title, type, attributes }；这里同时兼容
   属性被摊平到顶层的写法，历史数据不至于渲染成空白。 */
function cardType(card) {
  return card?.type === 'order' ? 'order' : 'product'
}

function cardAttribute(card, key) {
  return card?.attributes?.[key] ?? card?.[key]
}

function getCardTitle(card) {
  if (card?.title) {
    return card.title
  }
  return cardType(card) === 'order' ? 'Order' : 'Product'
}

function getCardBadge(card) {
  return cardType(card) === 'order' ? 'ORDER' : 'PRODUCT'
}

function getCardIdentifier(card) {
  const isOrder = cardType(card) === 'order'
  const id = card?.id ?? cardAttribute(card, isOrder ? 'order_id' : 'product_id')
  const label = isOrder ? 'Order No.' : 'Product ID'
  return id ? `${label} ${id}` : label
}

function getCardStatus(card) {
  return cardType(card) === 'order' ? cardAttribute(card, 'status') : ''
}

function getCardSummary(card) {
  if (cardType(card) === 'order') {
    const status = getCardStatus(card)
    return status ? `Status: ${statusLabel(status)}` : 'Order'
  }
  const description = cardAttribute(card, 'description')
  if (description) {
    return description
  }
  const price = cardAttribute(card, 'price')
  if (price !== undefined && price !== null) {
    return `Price: ${formatAmount(price)}`
  }
  return 'Product'
}

function getCardAmount(card) {
  const amount = cardType(card) === 'order'
    ? cardAttribute(card, 'amount')
    : cardAttribute(card, 'price')
  return formatAmount(amount)
}

function getCardCover(card) {
  return cardAttribute(card, 'cover_url')
}

// 侧边栏的原始订单/商品行 → 附录 E.1 的 ChatObject
function toOrderCard(order) {
  return {
    type: 'order',
    id: order.order_id,
    title: order.title,
    attributes: {
      status: order.status,
      amount: order.amount,
      created_at: order.created_at,
      cover_url: order.cover_url,
    },
  }
}

function toProductCard(product) {
  return {
    type: 'product',
    id: product.product_id,
    title: product.title,
    attributes: {
      price: product.price,
      cover_url: product.cover_url,
      description: product.description,
    },
  }
}

async function fetchSidebarData() {
  const currentSenderId = senderId.value.trim()
  orders.value = []
  products.value = []
  sidebarError.value = ''

  if (!currentSenderId) {
    return
  }

  isLoadingSidebar.value = true
  try {
    const [ordersResponse, productsResponse] = await Promise.all([
      fetch(commerceOrdersEndpoint.value),
      fetch(commerceProductsEndpoint.value),
    ])

    const [ordersPayload, productsPayload] = await Promise.all([
      ordersResponse.json(),
      productsResponse.json(),
    ])

    if (!ordersResponse.ok) {
      throw new Error(ordersPayload.detail || 'Failed to load orders.')
    }
    if (!productsResponse.ok) {
      throw new Error(productsPayload.detail || 'Failed to load products.')
    }

    orders.value = Array.isArray(ordersPayload?.data?.orders) ? ordersPayload.data.orders : []
    products.value = Array.isArray(productsPayload?.data?.products) ? productsPayload.data.products : []
  } catch (error) {
    sidebarError.value = error instanceof Error ? error.message : 'Failed to load the object list.'
  } finally {
    isLoadingSidebar.value = false
  }
}

async function fetchChatHistory() {
  const currentSenderId = senderId.value.trim()
  if (!currentSenderId) {
    messages.value = []
    return
  }

  try {
    const response = await fetch(chatHistoryEndpoint.value)
    const data = await response.json()
    if (!response.ok) {
      throw new Error(data.detail || 'Failed to load history.')
    }
    if (currentSenderId === senderId.value.trim()) {
      applyControlOwner(data)
      setHistoryMessages(Array.isArray(data?.messages) ? data.messages : [])
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Failed to load history.'
  }
}

async function sendPayload(payload) {
  if (isSending.value || isHumanControlled.value) {
    return
  }

  errorMessage.value = ''
  isSending.value = true

  try {
    await sendPayloadHttp(payload)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'Request failed.'
  } finally {
    isSending.value = false
  }
}

async function sendPayloadHttp(payload) {
  const response = await fetch(chatEndpoint.value, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      sender_id: senderId.value.trim(),
      ...payload,
    }),
  })

  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.detail || 'Request failed.')
  }

  applyControlOwner(data)
  appendBotMessages(data.messages ?? [])
}

async function sendSuggestion(text) {
  if (isHumanControlled.value) {
    return
  }
  appendUserText(text)
  await sendPayload({ text })
}

async function sendQuickText(text) {
  draftMessage.value = text
  await sendTextMessage()
}

async function sendTextMessage() {
  const text = draftMessage.value.trim()
  const currentSenderId = senderId.value.trim()

  if (isHumanControlled.value) {
    return
  }
  if (!currentSenderId) {
    errorMessage.value = 'Enter a sender_id first.'
    return
  }
  if (!text) {
    return
  }

  draftMessage.value = ''
  appendUserText(text)
  await sendPayload({ text })
}

async function sendOrder(order) {
  const currentSenderId = senderId.value.trim()
  if (isHumanControlled.value) {
    return
  }
  if (!currentSenderId) {
    errorMessage.value = 'Enter a sender_id first.'
    return
  }

  const card = toOrderCard(order)
  appendUserCard(card)
  await sendPayload({ object: card })
}

async function sendProduct(product) {
  const currentSenderId = senderId.value.trim()
  if (isHumanControlled.value) {
    return
  }
  if (!currentSenderId) {
    errorMessage.value = 'Enter a sender_id first.'
    return
  }

  const card = toProductCard(product)
  appendUserCard(card)
  await sendPayload({ object: card })
}

function selectNav(item) {
  activeNav.value = item.key
}

watch(
  () => senderId.value.trim(),
  async (value, previousValue) => {
    if (value === previousValue) {
      return
    }

    // 切换 sender_id: 重置会话, 重新拉取历史与侧边栏
    resetConversation()
    if (!value) {
      orders.value = []
      products.value = []
      return
    }
    await Promise.all([fetchSidebarData(), fetchChatHistory()])
  }
)

onMounted(async () => {
  await Promise.all([fetchSidebarData(), fetchChatHistory()])
})

async function copyText(text, key) {
  if (!text || !key) return
  try {
    await navigator.clipboard.writeText(text)
    copyState.value[key] = true
    setTimeout(() => { copyState.value[key] = false }, 1800)
  } catch (error) {
    console.error('Copy failed:', error)
  }
}
</script>

<template>
  <div class="app-shell">
    <div class="workspace">
      <!-- 左栏: 控制台导航 (附录 D.3) -->
      <nav class="console-nav">
        <div class="nav-brand">
          <span class="nav-brand-mark">BA</span>
          <span class="nav-brand-text">Business Agent</span>
        </div>

        <ul class="nav-list">
          <li v-for="item in NAV_ITEMS" :key="item.key">
            <button
              type="button"
              class="nav-item"
              :class="{ active: activeNav === item.key }"
              @click="selectNav(item)"
            >
              <svg class="nav-icon" viewBox="0 0 20 20" aria-hidden="true">
                <path :d="item.icon" fill="currentColor" />
              </svg>
              <span class="nav-item-label">{{ item.label }}</span>
              <span v-if="!item.ready" class="nav-item-tag">Soon</span>
            </button>
          </li>
        </ul>

        <div class="nav-footer">
          <label class="field">
            <span>sender_id</span>
            <input v-model="senderId" type="text" placeholder="u1001" />
          </label>
          <button
            type="button"
            class="secondary-button full-width"
            :disabled="isLoadingSidebar"
            @click="fetchSidebarData"
          >
            {{ isLoadingSidebar ? 'Loading…' : 'Refresh objects' }}
          </button>
          <label class="debug-toggle">
            <input v-model="showTurnCards" type="checkbox" />
            <span>Debug: show turn cards</span>
          </label>
        </div>
      </nav>

      <!-- 中栏: 对话窗 -->
      <section v-show="activeNav === 'chat'" class="chat-card">
        <header class="chat-header">
          <div class="service-info">
            <div class="service-avatar-wrapper">
              <img :src="customerService.avatar" class="service-avatar" alt="" />
              <span class="status-indicator"></span>
            </div>
            <div class="service-details">
              <span class="service-name">{{ customerService.name }}</span>
              <span class="service-status">{{ customerService.title }} · {{ customerService.status }}</span>
            </div>
          </div>

          <div class="header-actions">
            <span class="control-pill" :class="controlOwnerMeta.tone">{{ controlOwnerMeta.label }}</span>
            <button type="button" class="secondary-button" title="Clear conversation" @click="resetConversation">
              New chat
            </button>
          </div>
        </header>

        <p v-if="controlOwnerMeta.notice" class="control-banner" :class="controlOwnerMeta.tone">
          {{ controlOwnerMeta.notice }}
        </p>

        <section ref="messagesContainer" class="messages">
          <div v-if="turns.length === 0" class="welcome">
            <div class="welcome-card">
              <img :src="customerService.avatar" class="welcome-avatar" alt="" />
              <h2 class="welcome-greeting">Hi, I'm {{ customerService.name }}</h2>
              <p class="welcome-subtitle">Your dedicated commerce assistant, here whenever you need.</p>
              <div class="welcome-chips">
                <button
                  v-for="chip in ['Request a refund', 'Check order status', 'Recommend products', 'Return policy']"
                  :key="chip"
                  type="button"
                  class="pill-button"
                  :disabled="isComposerLocked"
                  @click="sendQuickText(chip)"
                >{{ chip }}</button>
              </div>
              <p class="welcome-features">
                <span>Text chat</span>
                <span>Order lookup</span>
                <span>Product advice</span>
              </p>
            </div>
          </div>

          <!-- Turn 结构展示 -->
          <template v-for="(item, index) in turns" :key="item.id || index">
            <!-- 分隔线 -->
            <div v-if="item.type === 'divider'" class="history-divider">
              <span>{{ item.text }}</span>
            </div>

            <!-- 轮次: 默认弱化为浅灰小字, 调试开关打开时恢复完整卡片 (附录 D.5) -->
            <div v-else class="turn" :class="{ 'turn-boxed': showTurnCards }">
              <div class="turn-header">
                <span class="turn-badge">Turn {{ item.index }}</span>
                <span v-if="showTurnCards" class="turn-label">Turn</span>
              </div>

              <!-- 用户与客服消息走同一套渲染路径 -->
              <div
                v-for="message in turnMessages(item)"
                :key="message.id"
                class="msg-row"
                :class="`msg-row-${message.role}`"
              >
                <img :src="messageAvatar(message)" class="msg-avatar" alt="" />

                <div class="msg-stack">
                  <div class="msg-meta">
                    <span class="msg-name">{{ messageName(message) }}</span>
                    <span class="msg-role">{{ messageRoleLabel(message) }}</span>
                  </div>

                  <div v-if="message.text" class="bubble">
                    <p>{{ message.text }}</p>
                    <button
                      v-if="message.role === 'user'"
                      type="button"
                      class="copy-button"
                      :class="{ 'copy-done': copyState[message.id] }"
                      :title="copyState[message.id] ? 'Copied' : 'Copy text'"
                      @click.stop="copyText(message.text, message.id)"
                    >
                      <span v-if="copyState[message.id]">✓</span>
                      <span v-else>Copy</span>
                    </button>
                  </div>

                  <!-- 卡片列表: 单卡片与多卡片同一路径, 多卡片横向滚动 (附录 E.2) -->
                  <div
                    v-if="message.cards.length > 0"
                    class="card-list"
                    :class="{ 'card-list-multi': message.cards.length > 1 }"
                  >
                    <article
                      v-for="(card, cardIndex) in message.cards"
                      :key="card.id || cardIndex"
                      class="object-card"
                    >
                      <img
                        v-if="imageLoaded(getCardCover(card))"
                        :src="getCardCover(card)"
                        :alt="getCardTitle(card)"
                        class="object-card-image"
                        @error="markImageFailed(getCardCover(card))"
                      />
                      <div v-else-if="getCardCover(card)" class="object-card-image-placeholder">
                        {{ getCardBadge(card) }}
                      </div>
                      <div class="object-card-badge">{{ getCardBadge(card) }}</div>
                      <div class="object-card-title">{{ getCardTitle(card) }}</div>
                      <div class="object-card-meta">{{ getCardIdentifier(card) }}</div>
                      <div class="object-card-meta">
                        <span
                          v-if="getCardStatus(card)"
                          class="status-badge"
                          :class="getStatusClass(getCardStatus(card))"
                        >{{ statusLabel(getCardStatus(card)) }}</span>
                        <span v-else>{{ getCardSummary(card) }}</span>
                      </div>
                      <div class="object-card-price">{{ getCardAmount(card) }}</div>
                    </article>
                  </div>

                  <!-- 快捷回复 -->
                  <div v-if="message.suggestions.length > 0" class="suggestion-chips">
                    <button
                      v-for="(sug, sugIndex) in message.suggestions"
                      :key="`${sug.value}-${sugIndex}`"
                      type="button"
                      class="pill-button"
                      :disabled="isComposerLocked"
                      @click.stop="sendSuggestion(sug.value)"
                    >{{ sug.label }}</button>
                  </div>
                </div>
              </div>
            </div>
          </template>
        </section>

        <div v-if="isSending" class="typing-indicator">
          <img :src="customerService.avatar" class="avatar-small" alt="" />
          <div class="typing-bubble">
            <span class="typing-dots">
              <span></span><span></span><span></span>
            </span>
            <span class="typing-label">{{ customerService.name }} is typing…</span>
          </div>
        </div>

        <p v-if="errorMessage" class="error-message">
          {{ errorMessage }}
        </p>

        <form class="composer" @submit.prevent="sendTextMessage">
          <input
            v-model="draftMessage"
            type="text"
            :placeholder="isHumanControlled ? 'A human agent has taken over — input locked' : 'Ask me anything…'"
            :disabled="isComposerLocked"
          />
          <button type="submit" class="primary-button" :disabled="isComposerLocked || !draftMessage.trim()">
            {{ isSending ? 'Sending…' : 'Send' }}
          </button>
        </form>
      </section>

      <!-- 中栏占位: 后续档位的页面入口 -->
      <section v-if="activeNav !== 'chat'" class="chat-card">
        <header class="chat-header">
          <h1 class="placeholder-title">{{ activeNavItem.label }}</h1>
        </header>
        <div class="placeholder-body">
          <p class="placeholder-headline">Coming soon</p>
          <p class="placeholder-text">
            {{ activeNavItem.label }} belongs to a later delivery tier. This is a placeholder entry point.
          </p>
          <button type="button" class="secondary-button" @click="activeNav = 'chat'">
            Back to chat
          </button>
        </div>
      </section>

      <!-- 右栏: 业务对象 -->
      <aside class="sidebar">
        <div class="sidebar-header">
          <h2>Business objects</h2>
        </div>

        <div class="tabs">
          <button
            type="button"
            class="tab-button"
            :class="{ active: activeTab === 'orders' }"
            @click="activeTab = 'orders'"
          >
            Orders
          </button>
          <button
            type="button"
            class="tab-button"
            :class="{ active: activeTab === 'products' }"
            @click="activeTab = 'products'"
          >
            Products
          </button>
        </div>

        <p v-if="sidebarError" class="sidebar-error">{{ sidebarError }}</p>

        <div v-if="activeTab === 'orders'" class="sidebar-list">
          <div v-if="!orders.length && !isLoadingSidebar" class="sidebar-empty">
            No orders yet
          </div>

          <article v-for="order in orders" :key="order.order_id" class="sidebar-card">
            <div class="card-image-wrapper">
              <img
                v-if="imageLoaded(order.cover_url)"
                :src="order.cover_url"
                :alt="order.title"
                class="card-image"
                @error="markImageFailed(order.cover_url)"
              />
              <div v-else class="card-image-placeholder">Order</div>
            </div>
            <div class="card-top">
              <div class="card-title">{{ order.title }}</div>
              <div class="card-amount">{{ formatAmount(order.amount) }}</div>
            </div>
            <div class="card-meta">Order No. {{ order.order_id }}</div>
            <div class="card-meta">
              <span class="status-badge" :class="getStatusClass(order.status)">{{ statusLabel(order.status) }}</span>
            </div>
            <button
              type="button"
              class="secondary-button full-width"
              :disabled="isComposerLocked"
              @click="sendOrder(order)"
            >
              Send order
            </button>
          </article>
        </div>

        <div v-else class="sidebar-list">
          <div v-if="!products.length && !isLoadingSidebar" class="sidebar-empty">
            No products yet
          </div>

          <article v-for="product in products" :key="product.product_id" class="sidebar-card">
            <div class="card-image-wrapper">
              <img
                v-if="imageLoaded(product.cover_url)"
                :src="product.cover_url"
                :alt="product.title"
                class="card-image"
                @error="markImageFailed(product.cover_url)"
              />
              <div v-else class="card-image-placeholder">Product</div>
            </div>
            <div class="card-top">
              <div class="card-title">{{ product.title }}</div>
              <div class="card-amount">{{ formatAmount(product.price) }}</div>
            </div>
            <div class="card-meta">Product ID {{ product.product_id }}</div>
            <div class="card-meta">Recently viewed / purchased</div>
            <button
              type="button"
              class="secondary-button full-width"
              :disabled="isComposerLocked"
              @click="sendProduct(product)"
            >
              Send product
            </button>
          </article>
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>

:global(*) {
  box-sizing: border-box;
}

/* ── 设计 token (附录 D.1) ────────────────────────────────── */
:global(:root) {
  /* 页底 */
  --page-gradient: linear-gradient(135deg, #f6f2fa, #eef3fc 55%, #e9f1fb);
  /* 面板 / 卡片 */
  --surface: #ffffff;
  /* 次级面底: 消息气泡、输入框 */
  --surface-secondary: #f0f2f5;
  /* 主色 */
  --brand: #1877f2;
  --brand-strong: #166fe5;
  --brand-soft: #e7f0fd;
  --brand-text: #1155cc;
  /* 导航选中态 */
  --nav-active: #1c2b33;
  /* 边框 */
  --border: #dadde1;
  --border-pill: #dddfe2;
  /* 文字 */
  --text-primary: #050505;
  --text-secondary: #65676b;
  --text-placeholder: #8a8d91;
  /* 状态色: 浅色底 + 同色系深字 */
  --tone-success-bg: #e3f4e7;
  --tone-success-text: #1a7f37;
  --tone-warning-bg: #fdf1dd;
  --tone-warning-text: #8a5300;
  --tone-info-bg: #e7f0fd;
  --tone-info-text: #1155cc;
  --tone-muted-bg: #f0f2f5;
  --tone-muted-text: #65676b;
  --tone-danger-bg: #fdeaea;
  --tone-danger-text: #b02525;
  /* 深色/主色底上的文字与描边 */
  --on-solid: #ffffff;
  --on-solid-soft: rgba(255, 255, 255, 0.16);
  --on-solid-line: rgba(255, 255, 255, 0.45);
  --status-online: #31a24c;
  /* 形状 (附录 D.2) */
  --radius-panel: 8px;
  --radius-card: 10px;
  --radius-button: 6px;
  --radius-pill: 99px;
  --radius-bubble: 16px;
  /* 动效: 仅 hover 底色变化与 150ms 过渡 */
  --duration: 150ms;
  --font-stack: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica,
    Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}

:global(body) {
  margin: 0;
  font-family: var(--font-stack);
  background: var(--page-gradient);
  background-attachment: fixed;
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

:global(button),
:global(input) {
  font: inherit;
}

:global(#app) {
  min-height: 100vh;
}

.app-shell {
  min-height: 100vh;
  padding: 16px;
}

/* ── 三栏布局 (附录 D.3) ──────────────────────────────────── */
.workspace {
  width: min(1600px, 100%);
  margin: 0 auto;
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr) 320px;
  gap: 16px;
  align-items: start;
}

.console-nav,
.chat-card,
.sidebar {
  height: calc(100vh - 32px);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-panel);
  overflow: hidden;
}

/* ── 左栏: 控制台导航 ─────────────────────────────────────── */
.console-nav {
  display: flex;
  flex-direction: column;
  padding: 12px;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px 14px;
  border-bottom: 1px solid var(--border);
}

.nav-brand-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-button);
  background: var(--brand);
  color: var(--on-solid);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.nav-brand-text {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.nav-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nav-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 10px;
  border: none;
  border-radius: var(--radius-panel);
  background: transparent;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--duration) linear, color var(--duration) linear;
}

.nav-item:hover {
  background: var(--surface-secondary);
}

.nav-item.active {
  background: var(--nav-active);
  color: var(--on-solid);
}

.nav-icon {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.nav-item.active .nav-icon {
  color: var(--on-solid);
}

.nav-item-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-item-tag {
  flex-shrink: 0;
  white-space: nowrap;
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: var(--tone-muted-bg);
  color: var(--tone-muted-text);
  font-size: 11px;
  font-weight: 500;
}

.nav-item.active .nav-item-tag {
  background: var(--on-solid-soft);
  color: var(--on-solid);
}

.nav-footer {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 12px;
  margin-top: 12px;
  border-top: 1px solid var(--border);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field span {
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}

.field input {
  width: 100%;
  min-width: 0;
  min-height: 36px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-button);
  background: var(--surface-secondary);
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.4;
  transition: border-color var(--duration) linear, background-color var(--duration) linear;
}

.field input::placeholder {
  color: var(--text-placeholder);
}

.field input:focus {
  outline: none;
  border-color: var(--brand);
  background: var(--surface);
}

.debug-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.debug-toggle input {
  margin: 0;
  accent-color: var(--brand);
  cursor: pointer;
}

/* ── 中栏: 对话窗 ─────────────────────────────────────────── */
.chat-card {
  display: flex;
  flex-direction: column;
}

.chat-header,
.sidebar-header {
  flex-shrink: 0;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.sidebar-header h2,
.placeholder-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--text-primary);
}

.service-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.service-avatar-wrapper {
  position: relative;
  flex-shrink: 0;
}

.service-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  object-fit: cover;
  background: var(--surface-secondary);
}

.status-indicator {
  position: absolute;
  bottom: 0;
  right: 0;
  width: 10px;
  height: 10px;
  background: var(--status-online);
  border: 2px solid var(--surface);
  border-radius: 50%;
}

.service-details {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.service-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.service-status {
  font-size: 12px;
  color: var(--text-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

/* ── 控制权 (需求 3.3.4 / 附录 E.2 第 3 条) ──────────────── */
.control-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.control-agent {
  background: var(--tone-info-bg);
  color: var(--tone-info-text);
}

.control-pending {
  background: var(--tone-warning-bg);
  color: var(--tone-warning-text);
}

.control-human {
  background: var(--tone-success-bg);
  color: var(--tone-success-text);
}

.control-banner {
  flex-shrink: 0;
  margin: 0;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  line-height: 1.5;
}

.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-gutter: stable;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

/* ── 轮次: 默认弱化, 调试开关恢复卡片 (附录 D.5) ─────────── */
.turn {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.turn-boxed {
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  background: var(--surface);
}

.turn-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.turn-badge {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-placeholder);
  letter-spacing: 0.02em;
}

.turn-boxed .turn-header {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
}

/* 调试模式下把徽章底色也还回来 */
.turn-boxed .turn-badge {
  padding: 3px 10px;
  border-radius: var(--radius-button);
  background: var(--surface-secondary);
  color: var(--text-secondary);
  font-weight: 600;
}

.turn-label {
  font-size: 11px;
  color: var(--text-placeholder);
}

/* ── 消息行: 用户右、客服左 ───────────────────────────────── */
.msg-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 100%;
}

.msg-row-user {
  flex-direction: row-reverse;
}

.msg-avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  object-fit: cover;
  background: var(--surface-secondary);
}

.msg-stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  max-width: calc(100% - 40px);
}

.msg-row-user .msg-stack {
  align-items: flex-end;
}

.msg-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
}

.msg-name {
  font-weight: 600;
  color: var(--text-secondary);
}

.msg-role {
  color: var(--text-placeholder);
}

.bubble {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 100%;
  padding: 10px 14px;
  border-radius: var(--radius-bubble);
  background: var(--surface-secondary);
  color: var(--text-primary);
}

.msg-row-user .bubble {
  background: var(--brand);
  color: var(--on-solid);
}

.bubble p {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 15px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── 卡片列表 (附录 E.2): 单卡片与多卡片同一路径 ─────────── */
.card-list {
  display: flex;
  gap: 10px;
  max-width: 100%;
}

.card-list-multi {
  overflow-x: auto;
  padding-bottom: 4px;
}

.object-card {
  flex: 0 0 auto;
  width: 216px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  background: var(--surface);
}

.object-card-image {
  width: 100%;
  height: 128px;
  object-fit: cover;
  border-radius: var(--radius-button);
  margin-bottom: 4px;
  background: var(--surface-secondary);
}

.object-card-image-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 128px;
  margin-bottom: 4px;
  border-radius: var(--radius-button);
  background: var(--surface-secondary);
  color: var(--text-secondary);
  font-size: 12px;
}

.object-card-badge {
  align-self: flex-start;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--brand-soft);
  color: var(--brand-text);
  font-size: 11px;
  font-weight: 600;
}

.object-card-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text-primary);
}

.object-card-meta {
  font-size: 13px;
  color: var(--text-secondary);
}

.object-card-price {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

/* ── 快捷回复 ─────────────────────────────────────────────── */
.suggestion-chips,
.welcome-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-width: 100%;
}

.pill-button {
  padding: 7px 14px;
  border: 1px solid var(--border-pill);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color var(--duration) linear, border-color var(--duration) linear;
}

.pill-button:hover:not(:disabled) {
  background: var(--surface-secondary);
}

.pill-button:disabled {
  color: var(--text-placeholder);
  cursor: not-allowed;
}

.copy-button {
  flex-shrink: 0;
  padding: 2px 8px;
  border: 1px solid var(--border-pill);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
  cursor: pointer;
  transition: background-color var(--duration) linear, color var(--duration) linear;
}

.copy-button:hover {
  background: var(--surface-secondary);
}

.copy-button.copy-done {
  background: var(--tone-success-bg);
  border-color: var(--tone-success-bg);
  color: var(--tone-success-text);
}

.msg-row-user .copy-button {
  border-color: var(--on-solid-line);
  background: transparent;
  color: var(--on-solid);
}

.msg-row-user .copy-button:hover {
  background: var(--on-solid-soft);
}

/* ── 历史消息分隔线 ───────────────────────────────────────── */
.history-divider {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--text-placeholder);
  font-size: 12px;
}

.history-divider::before,
.history-divider::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--border);
}

/* ── 欢迎区 ───────────────────────────────────────────────── */
.welcome {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.welcome-card {
  max-width: 440px;
  width: 100%;
  padding: 28px 24px;
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  background: var(--surface);
  text-align: center;
}

.welcome-avatar {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  object-fit: cover;
  background: var(--surface-secondary);
}

.welcome-greeting {
  margin: 14px 0 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.welcome-subtitle {
  margin: 0 0 20px;
  color: var(--text-secondary);
  font-size: 14px;
}

.welcome-chips {
  justify-content: center;
}

.welcome-features {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin: 18px 0 0;
}

.welcome-features span {
  padding: 4px 12px;
  border-radius: var(--radius-pill);
  background: var(--surface-secondary);
  color: var(--text-secondary);
  font-size: 12px;
}

/* ── typing 指示 ──────────────────────────────────────────── */
.typing-indicator {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px 12px;
}

.avatar-small {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
  background: var(--surface-secondary);
}

.typing-bubble {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: var(--radius-bubble);
  background: var(--surface-secondary);
}

.typing-dots {
  display: flex;
  gap: 4px;
  align-items: center;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-placeholder);
  animation: dotFade 1.2s ease-in-out infinite;
}

.typing-dots span:nth-child(1) { animation-delay: 0s; }
.typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.typing-dots span:nth-child(3) { animation-delay: 0.4s; }

/* 仅透明度变化, 无位移无缩放 */
@keyframes dotFade {
  0%, 60%, 100% { opacity: 0.3; }
  30% { opacity: 1; }
}

.typing-label {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ── 状态徽章 (附录 D.2: 胶囊, 浅色底 + 同色系深字) ───────── */
.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 600;
}

.status-warning {
  background: var(--tone-warning-bg);
  color: var(--tone-warning-text);
}

.status-info {
  background: var(--tone-info-bg);
  color: var(--tone-info-text);
}

.status-success {
  background: var(--tone-success-bg);
  color: var(--tone-success-text);
}

.status-muted {
  background: var(--tone-muted-bg);
  color: var(--tone-muted-text);
}

.status-danger {
  background: var(--tone-danger-bg);
  color: var(--tone-danger-text);
}

/* ── 按钮 ─────────────────────────────────────────────────── */
.primary-button {
  min-height: 36px;
  padding: 8px 18px;
  border: 1px solid transparent;
  border-radius: var(--radius-button);
  background: var(--brand);
  color: var(--on-solid);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color var(--duration) linear;
}

.primary-button:hover:not(:disabled) {
  background: var(--brand-strong);
}

.primary-button:disabled {
  background: var(--surface-secondary);
  color: var(--text-placeholder);
  cursor: not-allowed;
}

.secondary-button,
.tab-button {
  min-height: 34px;
  padding: 7px 14px;
  border: 1px solid var(--border-pill);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color var(--duration) linear, border-color var(--duration) linear,
    color var(--duration) linear;
}

.secondary-button:hover:not(:disabled),
.tab-button:hover:not(:disabled) {
  background: var(--surface-secondary);
}

.secondary-button:disabled {
  color: var(--text-placeholder);
  cursor: not-allowed;
}

.full-width {
  width: 100%;
}

.error-message,
.sidebar-error {
  flex-shrink: 0;
  margin: 0;
  padding: 0 16px 10px;
  color: var(--tone-danger-text);
  font-size: 13px;
}

/* ── 输入区 ───────────────────────────────────────────────── */
.composer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.composer input {
  flex: 1;
  min-width: 0;
  min-height: 38px;
  padding: 9px 16px;
  border: 1px solid transparent;
  border-radius: var(--radius-pill);
  background: var(--surface-secondary);
  color: var(--text-primary);
  font-size: 15px;
  line-height: 1.4;
  transition: border-color var(--duration) linear, background-color var(--duration) linear;
}

.composer input::placeholder {
  color: var(--text-placeholder);
}

.composer input:focus {
  outline: none;
  border-color: var(--brand);
  background: var(--surface);
}

.composer input:disabled {
  color: var(--text-placeholder);
  cursor: not-allowed;
}

/* ── 占位页 ───────────────────────────────────────────────── */
.placeholder-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  text-align: center;
}

.placeholder-headline {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.placeholder-text {
  margin: 0 0 8px;
  max-width: 380px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.6;
}

/* ── 右栏: 业务对象 ───────────────────────────────────────── */
.sidebar {
  display: flex;
  flex-direction: column;
}

.tabs {
  flex-shrink: 0;
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.tab-button {
  min-width: 72px;
}

.tab-button.active {
  background: var(--brand-soft);
  border-color: var(--brand-soft);
  color: var(--brand-text);
}

.sidebar-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sidebar-card {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: background-color var(--duration) linear;
}

.sidebar-card:hover {
  background: var(--surface-secondary);
}

.card-image-wrapper {
  width: 100%;
  height: 128px;
  border-radius: var(--radius-button);
  overflow: hidden;
  background: var(--surface-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-image-placeholder {
  color: var(--text-placeholder);
  font-size: 13px;
}

.card-top {
  display: flex;
  gap: 12px;
  justify-content: space-between;
  align-items: flex-start;
}

.card-title {
  font-size: 14px;
  line-height: 1.4;
  color: var(--text-primary);
  font-weight: 600;
}

.card-amount {
  flex-shrink: 0;
  color: var(--text-primary);
  font-weight: 700;
}

.card-meta {
  font-size: 13px;
  color: var(--text-secondary);
}

.sidebar-empty {
  margin: auto;
  max-width: 420px;
  color: var(--text-placeholder);
  text-align: center;
  font-size: 14px;
  line-height: 1.7;
}

/* ── 响应式 ───────────────────────────────────────────────── */
@media (max-width: 1180px) {
  .workspace {
    grid-template-columns: 200px minmax(0, 1fr);
  }

  .sidebar {
    grid-column: 1 / -1;
    height: auto;
    max-height: 70vh;
  }
}

@media (max-width: 860px) {
  .app-shell {
    padding: 0;
  }

  .workspace {
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
  }

  .console-nav,
  .chat-card,
  .sidebar {
    height: auto;
    border-radius: 0;
    border-left: none;
    border-right: none;
  }

  .chat-card {
    min-height: 70vh;
  }

  .console-nav {
    flex-direction: column;
  }

  .nav-list {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .nav-item-tag {
    display: none;
  }

  .composer {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
