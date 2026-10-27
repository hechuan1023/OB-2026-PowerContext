import dataset from '../data/evaluation.json';
import { Engine, api } from './engine.js';
import { median, retrievalMetrics } from '../shared/text.js';
import { MODEL, REVISION } from '../shared/config.js';

export async function runBenchmark(notify = () => {}, selected = ['fp32', 'gpu', 'wasm'], repeats = 3) {
  const manifest = await fetch('/models/manifest.json').then(r => r.json());
  const report = {
    startedAt: new Date().toISOString(), userAgent: navigator.userAgent,
    hardwareConcurrency: navigator.hardwareConcurrency, deviceMemory: navigator.deviceMemory,
    model: MODEL, revision: REVISION, corpus: dataset.version,
    documents: dataset.documents.length, queries: dataset.queries.length,
    repeats, k: 5, manifest, rows: [],
    notes: '自编中文微型集，二值相关性；文档 ID 去重后计算 Recall@5/nDCG@5。查询前预热一次；禁用查询向量缓存。所有查询经过真实 seekdb。',
  };
  const configs = selected.map(profile => ({ profile, batchSize: 8, deferRefresh: true }));
  if (selected.includes('wasm')) {
    configs.push({ profile: 'wasm', batchSize: 1, deferRefresh: true });
    configs.push({ profile: 'wasm', batchSize: 8, deferRefresh: false });
  }
  for (const config of configs) {
    const { profile, batchSize, deferRefresh } = config;
    const label = `${profile}_batch${batchSize}${deferRefresh ? '' : '_refresh_each'}`;
    const engine = new Engine(value => { if (value.phase === 'model') notify(`${label} · 加载模型`); });
    const row = { ...config, label, imports: [], queries: [] };
    report.rows.push(row);
    try {
      notify(`${label} · 加载模型`);
      row.runtime = await engine.load(profile);
      row.modelBytes = manifest.totals[row.runtime.dtype];
      const kb = `bench_${Date.now().toString(36)}_${profile}_b${batchSize}`;
      row.collection = kb;
      await api('/collections', { name: kb, precision: row.runtime.dtype });
      const docs = dataset.documents.map(doc => ({ title: doc.title, text: doc.text }));
      const sourceMap = new Map();
      for (let repeat = 0; repeat < repeats; repeat++) {
        notify(`${label} · 导入 ${repeat + 1}/${repeats}`);
        const imported = await engine.importDocuments(kb, docs, { batchSize, sort: true, deferRefresh });
        imported.sources.forEach((sourceId, i) => sourceMap.set(sourceId, dataset.documents[i].id));
        row.imports.push({ repeat, ...imported, docsPerSecond: docs.length * 1000 / imported.inferenceMs });
      }
      for (let repeat = 0; repeat < repeats; repeat++) {
        for (const [index, query] of dataset.queries.entries()) {
          notify(`${label} · 查询 ${repeat * dataset.queries.length + index + 1}/${repeats * dataset.queries.length}`);
          const submittedAt = performance.now();
          const result = await engine.search(kb, query.text);
          // Display real database results, then wait for the browser to paint before ending E2E.
          const container = document.getElementById('results');
          if (container) {
            const cards = result.results.map(item => {
              const card = document.createElement('article'); card.className = 'result-card';
              const title = document.createElement('div'); title.className = 'result-title'; title.textContent = item.metadata.title;
              const text = document.createElement('p'); text.textContent = item.text;
              card.append(title, text); return card;
            });
            container.replaceChildren(...cards);
          }
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const displayTotalMs = performance.now() - submittedAt;
          const ids = result.results.map(item => sourceMap.get(item.metadata.sourceId));
          row.queries.push({ repeat, query: query.text, relevant: query.relevant, ids, ...retrievalMetrics(ids, query.relevant, 5), queryEmbeddingMs: result.queryEmbeddingMs, seekdbMs: result.seekdbMs, receivedMs: result.totalMs, totalMs: displayTotalMs });
        }
      }
      row.summary = {
        docsPerSecond: median(row.imports.map(item => item.docsPerSecond)),
        documentEmbeddingMs: median(row.imports.map(item => item.inferenceMs)),
        writeMs: median(row.imports.map(item => item.writeMs)), refreshMs: median(row.imports.map(item => item.refreshMs)),
        importTotalMs: median(row.imports.map(item => item.totalMs)),
        queryEmbeddingMs: median(row.queries.map(item => item.queryEmbeddingMs)),
        seekdbMs: median(row.queries.map(item => item.seekdbMs)), totalMs: median(row.queries.map(item => item.totalMs)),
        recall: row.queries.reduce((sum, item) => sum + item.recall, 0) / row.queries.length,
        ndcg: row.queries.reduce((sum, item) => sum + item.ndcg, 0) / row.queries.length,
        firstQuery: row.queries[0],
      };
      engine.dispose();
      const cachedEngine = new Engine();
      try { row.cachedRuntime = await cachedEngine.load(profile); }
      finally { cachedEngine.dispose(); }
    } catch (error) { row.error = error.message; }
    finally { engine.dispose(); }
  }
  report.finishedAt = new Date().toISOString();
  report.seekdb = await api('/health');
  return report;
}
