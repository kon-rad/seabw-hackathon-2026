import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Track ordered/unordered context so listitem can apply the right class
let _ordered = false

const renderer = {
  heading({ tokens, depth }) {
    const text = this.parser.parseInline(tokens)
    // Existing convention: # -> h2.md-h2, ## -> h3.md-h3, etc.
    const level = depth + 1
    return `<h${level} class="md-h${level}">${text}</h${level}>\n`
  },

  paragraph({ tokens }) {
    const body = this.parser.parseInline(tokens)
    return `<p class="md-p">${body}</p>\n`
  },

  list(token) {
    const { ordered, start, items } = token
    _ordered = ordered
    let body = ''
    for (let i = 0; i < items.length; i++) {
      body += this.listitem(items[i])
    }
    _ordered = false

    const tag = ordered ? 'ol' : 'ul'
    const cls = ordered ? 'md-ol' : 'md-ul'
    const startAttr = (ordered && start !== 1) ? ` start="${start}"` : ''
    return `<${tag} class="${cls}"${startAttr}>${body}</${tag}>\n`
  },

  listitem(item) {
    const cls = _ordered ? 'md-oli' : 'md-li'
    const body = this.parser.parse(item.tokens, false)
    return `<li class="${cls}">${body}</li>`
  },

  blockquote({ tokens }) {
    const body = this.parser.parse(tokens)
    return `<blockquote class="md-quote">${body}</blockquote>\n`
  },

  code({ text }) {
    return `<pre class="code-block"><code>${text}</code></pre>\n`
  },

  codespan({ text }) {
    return `<code class="inline-code">${text}</code>`
  },

  hr() {
    return `<hr class="md-hr">\n`
  }
}

marked.use({ renderer, breaks: true })

/**
 * Render markdown to sanitized HTML with CSS classes matching the app's design system.
 *
 * @param {string} content  Raw markdown string
 * @param {Object} [options]
 * @param {boolean} [options.stripLeadingH2=false]  Remove leading ## heading
 *   (used when the section title is displayed externally)
 * @returns {string} Sanitized HTML
 */
export function renderMarkdown(content, { stripLeadingH2 = false } = {}) {
  if (!content) return ''

  let processed = content
  if (stripLeadingH2) {
    processed = processed.replace(/^##\s+.+\n+/, '')
  }

  const html = marked.parse(processed)
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ['start']
  })
}
