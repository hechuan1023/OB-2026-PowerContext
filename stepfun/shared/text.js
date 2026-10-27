// Offsets are UTF-16 offsets into the unchanged source. Limit chunks well below 512 tokens.
export function chunkDocument(text, size = 220, overlap = 40) {
  if (!Number.isInteger(size) || size < 2 || overlap < 0 || overlap >= size) throw new Error('Invalid chunk size');
  const chunks = [];
  for (let start = 0; start < text.length;) {
    let end = Math.min(start + size, text.length);
    if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1])) end--;
    const part = text.slice(start, end);
    if (part.trim()) chunks.push({ text: part, start, end, ordinal: chunks.length });
    if (end === text.length) break;
    start = end - overlap;
    if (/[\uDC00-\uDFFF]/.test(text[start])) start++;
  }
  return chunks;
}

export function retrievalMetrics(ids, relevant, k = 5) {
  const top = [...new Set(ids)].slice(0, k);
  const gold = new Set(relevant);
  const hits = top.filter(id => gold.has(id)).length;
  const dcg = top.reduce((sum, id, i) => sum + (gold.has(id) ? 1 / Math.log2(i + 2) : 0), 0);
  const ideal = Array.from({ length: Math.min(k, gold.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
  return { recall: gold.size ? hits / gold.size : 0, ndcg: ideal ? dcg / ideal : 0 };
}

export function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const n = sorted.length;
  return n ? (sorted[Math.floor(n / 2)] + sorted[Math.floor((n - 1) / 2)]) / 2 : null;
}
