import { MODEL_SIGNATURE } from '../shared/config.js';
import { chunkDocument } from '../shared/text.js';
export async function api(path, body) {
  const response = await fetch(`/api${path}`, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || response.statusText);
  return value;
}
export class Engine {
  constructor(onProgress = () => {}) {
    this.worker = new Worker(new URL('./embedding.worker.js', import.meta.url), { type: 'module' });
    this.pending = new Map(); this.sequence = 0; this.onProgress = onProgress;
    this.worker.onmessage = ({ data }) => {
      if (data.event) { this.onProgress(data.value); return; }
      const pending = this.pending.get(data.id);
      if (!pending) return;
      this.pending.delete(data.id);
      data.error ? pending.reject(new Error(data.error)) : pending.resolve(data.value);
    };
    this.worker.onerror = error => {
      for (const pending of this.pending.values()) pending.reject(new Error(error.message || '模型 Worker 启动失败'));
      this.pending.clear();
    };
  }
  request(data) {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => { this.pending.set(id, { resolve, reject }); this.worker.postMessage({ ...data, id }); });
  }
  async load(profile = 'auto') { this.actual = undefined; this.actual = await this.request({ type: 'load', profile }); return this.actual; }
  embed(texts, options = {}) { return this.request({ type: 'embed', texts, ...options }); }
  async importDocuments(kb, documents, options = {}) {
    const started = performance.now();
    const prepared = documents.map(doc => ({ ...doc, chunks: chunkDocument(doc.text) }));
    if (prepared.some(doc => !doc.chunks.length)) throw new Error('存在空白文档');
    if (prepared.some(doc => doc.chunks.length > 2000)) throw new Error('单份文档超过 2000 个片段，请拆成较小文件后导入');
    const texts = prepared.flatMap(doc => doc.chunks.map(chunk => chunk.text));
    const embedded = await this.embed(texts, options);
    let offset = 0, writeMs = 0, refreshMs = 0;
    const sources = [];
    const deferred = options.deferRefresh !== false;
    for (const doc of prepared) {
      const result = await api(`/collections/${kb}/import`, {
        title: doc.title, text: doc.text, embeddings: embedded.embeddings.slice(offset, offset + doc.chunks.length),
        signature: MODEL_SIGNATURE, precision: embedded.dtype, deferRefresh: deferred, refreshEachBatch: options.refreshEachBatch,
      });
      sources.push(result.sourceId); offset += doc.chunks.length; writeMs += result.writeMs; refreshMs += result.refreshMs;
      this.onProgress({ phase: 'writing', done: sources.length, total: prepared.length });
    }
    if (deferred) {
      const start = performance.now();
      await api(`/collections/${kb}/refresh`, {});
      refreshMs += performance.now() - start;
    }
    return { sources, chunks: texts.length, documents: prepared.length, inferenceMs: embedded.inferenceMs, batches: embedded.batches, writeMs, refreshMs, totalMs: performance.now() - started, dtype: embedded.dtype, device: embedded.device };
  }
  async search(kb, query, options = {}) {
    const started = performance.now();
    const embedded = await this.embed([query], { query: true, batchSize: 1 });
    const database = await api(`/collections/${kb}/search`, { query, embedding: embedded.embeddings[0], signature: MODEL_SIGNATURE, precision: embedded.dtype, k: options.k || 5, mode: options.mode || 'hybrid' });
    return { ...database, queryEmbeddingMs: embedded.inferenceMs, totalMs: performance.now() - started, dtype: embedded.dtype, device: embedded.device };
  }
  dispose() { this.worker.terminate(); for (const p of this.pending.values()) p.reject(new Error('Worker terminated')); this.pending.clear(); }
}
