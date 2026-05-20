/**
 * Shared text helpers.
 */

/**
 * Truncate a string to `maxLength`, appending an ellipsis if cut.
 *
 * Returns '' for falsy input so it's safe to use directly in templates.
 *
 * @param {string} text
 * @param {number} [maxLength=100]
 * @returns {string}
 */
export function truncate(text, maxLength = 100) {
  if (!text) return ''
  return text.length > maxLength ? text.slice(0, maxLength) + '...' : text
}
