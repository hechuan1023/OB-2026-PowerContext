import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { browser, pageFor } from './browser-utils.js';
import { SeekdbClient } from 'seekdb';
try { process.loadEnvFile('.env'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
await mkdir('results', { recursive: true });
const report = { startedAt: new Date().toISOString(), tests: [] };
const resultPrefix = process.env.RESULT_PREFIX ? `${process.env.RESULT_PREFIX}-` : '';
for (const fallback of [false, true]) {
  const instance = await browser({ fallback });
  try {
    const { page, failures, environmentRequests } = await pageFor(instance);
    page.on('console', message => { if (message.type() === 'error') console.log('Browser:', message.text().slice(0, 300)); });
    const result = await page.evaluate(async fallback => {
      const { Engine, api, dataset } = window.atlas;
      const engine = new Engine();
      const runtime = await engine.load('auto');
      const prefix = `it_${Date.now().toString(36)}`;
      const kb = `${prefix}_a`, other = `${prefix}_b`;
      await api('/collections', { name: kb, precision: runtime.dtype });
      await api('/collections', { name: other, precision: runtime.dtype });
      const docs = dataset.documents.slice(0, 10).map(doc => ({ title: doc.title, text: doc.text }));
      const imported = await engine.importDocuments(kb, docs);
      const count = (await api('/collections')).find(row => row.name === kb).chunks;
      await engine.importDocuments(kb, docs);
      const countAfterRetry = (await api('/collections')).find(row => row.name === kb).chunks;
      const found = await engine.search(kb, '没有显卡支持时如何切换到其他计算后端？');
      const empty = await engine.search(other, '浏览器');
      const extra = { title: '增量校验.txt', text: '月光档案的专属代号是青瓷信标，只有追加资料中包含这个关键术语。' };
      await engine.importDocuments(kb, [extra]);
      const incremental = await engine.search(kb, '青瓷信标');
      const original = await api(`/collections/${kb}/sources/${incremental.results[0].metadata.sourceId}`);
      const deferredText = '索引屏障验证：琥珀航标属于刷新之后才能检索的新文档。';
      const deferredVectors = await engine.embed([deferredText]);
      await api(`/collections/${kb}/import`, { title: '刷新屏障.txt', text: deferredText, embeddings: deferredVectors.embeddings, signature: runtime.signature, precision: runtime.dtype, deferRefresh: true });
      let pendingRefreshRejected = false;
      try { await engine.search(kb, '琥珀航标'); } catch { pendingRefreshRejected = true; }
      await api(`/collections/${kb}/refresh`, {});
      const afterRefresh = await engine.search(kb, '琥珀航标');
      const concurrent = await Promise.all([engine.search(kb, '青瓷信标'), engine.search(other, '青瓷信标')]);
      // Precision mismatches are rejected by the persisted KB contract.
      const encoded = await engine.embed(['浏览器']);
      let precisionRejected = false;
      try { await api(`/collections/${kb}/search`, { query: '浏览器', precision: runtime.dtype === 'q8' ? 'fp32' : 'q8', signature: runtime.signature, embedding: encoded.embeddings[0] }); }
      catch { precisionRejected = true; }
      engine.dispose();
      return { kb, fallback, runtime, imported, count, countAfterRetry, found: found.results.map(item => item.metadata.title), empty: empty.results.length, incremental: incremental.results[0].metadata.title, original: original.content, concurrentCounts: concurrent.map(row => row.results.length), precisionRejected, pendingRefreshRejected, afterRefresh: afterRefresh.results[0].metadata.title, userAgent: navigator.userAgent, crossOriginIsolated, gpuExposed: !!navigator.gpu };
    }, fallback);
    assert.equal(result.count, result.countAfterRetry, '重复导入不得增加文档数');
    assert.equal(result.empty, 0, '知识库隔离');
    assert(result.found.includes('WebGPU 降级.txt'), '语义召回');
    assert.equal(result.incremental, '增量校验.txt', '增量写入后混合检索');
    assert.match(result.original, /青瓷信标/);
    assert.equal(result.concurrentCounts[1], 0, '并发会话隔离');
    assert(result.precisionRejected, '模型精度不一致必须拒绝');
    assert(result.pendingRefreshRejected, '未刷新时拒绝搜索');
    assert.equal(result.afterRefresh, '刷新屏障.txt');
    if (fallback) { assert.equal(result.runtime.device, 'wasm'); assert(result.runtime.fallback); }
    assert.deepEqual(failures, [], '禁止外部网络请求与页面错误');
    // Simulate the persisted marker left by an interrupted write; this is a state-recovery test,
    // not a claim of forcibly terminating a process mid-SQL statement.
    const db = new SeekdbClient({ host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881), user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '', database: process.env.SEEKDB_DATABASE || 'stepfun' });
    try {
      const c = await db.getCollection({ name: `atlas_${result.kb}`, embeddingFunction: null });
      const fulltext = await c.hybridSearch({ query: { whereDocument: { $contains: '琥珀航标' } }, nResults: 5, include: ['documents', 'metadatas'] });
      result.fulltextAfterRefresh = fulltext.metadatas[0].map(meta => meta.title);
      assert(result.fulltextAfterRefresh.includes('刷新屏障.txt'), '新增内容在全文索引也可见');
      await db.execute('UPDATE stepfun_kbs SET state = ?, pending_doc = ? WHERE name = ?', ['writing', result.imported.sources[0], result.kb]);
    }
    finally { await db.close(); }
    result.recovery = await page.evaluate(async ({ kb }) => {
      const { Engine, api, dataset } = window.atlas;
      const engine = new Engine(); await engine.load('auto');
      let searchRejected = false, refreshRejected = false;
      try { await engine.search(kb, '浏览器'); } catch { searchRejected = true; }
      try { await api(`/collections/${kb}/refresh`, {}); } catch { refreshRejected = true; }
      const doc = dataset.documents[0];
      await engine.importDocuments(kb, [{ title: doc.title, text: doc.text }]);
      const recovered = (await api('/collections')).find(row => row.name === kb).state;
      engine.dispose(); return { searchRejected, refreshRejected, recovered };
    }, { kb: result.kb });
    assert(result.recovery.searchRejected && result.recovery.refreshRejected);
    assert.equal(result.recovery.recovered, 'ready');
    // Verify the actual user-facing import/search controls and backend indicator.
    await page.locator('#profile').selectOption(fallback ? 'wasm' : 'auto');
    await page.locator('#load-model').click();
    await page.waitForFunction(() => window.atlas.getState().actual && !window.atlas.getState().busy, undefined, { timeout: 180000 });
    await page.getByRole('button', { name: '新建知识库', exact: true }).click();
    const uiName = `ui_${Date.now().toString(36)}`;
    await page.locator('#kb-name').fill(uiName);
    await page.locator('#create-kb').click();
    await page.waitForFunction(name => window.atlas.getState().current === name && !window.atlas.getState().busy, uiName);
    await page.locator('#sample').click();
    await page.waitForFunction(() => !window.atlas.getState().busy, undefined, { timeout: 180000 });
    await page.locator('#query').fill('新文档写进去之后为什么搜不到？');
    await page.locator('#search').click();
    await page.waitForSelector('.result-card');
    await page.waitForFunction(() => !window.atlas.getState().busy);
    result.uiBackend = await page.locator('#backend').innerText();
    result.environmentRequests = environmentRequests;
    result.uiResults = await page.locator('.result-title').allTextContents();
    assert(result.uiResults.includes('索引刷新.txt'));
    assert.match(result.uiBackend, fallback ? /WASM/ : /WebGPU|WASM/);
    await page.screenshot({ path: `results/${resultPrefix}${fallback ? 'wasm' : 'webgpu'}-application.png`, fullPage: true });
    await page.locator('.source-button').first().click();
    await page.waitForSelector('.source-text');
    result.uiSourceVisible = await page.locator('.source-text').isVisible();
    assert(result.uiSourceVisible);
    assert.deepEqual(failures, []);
    report.tests.push(result);
    console.log(`PASS ${fallback ? 'WebGPU unavailable → WASM' : 'WebGPU preferred'}: ${result.runtime.device}/${result.runtime.dtype}, import + retry + incremental + isolation + UI`);
  } finally { await instance.close(); }
}
report.finishedAt = new Date().toISOString();
await writeFile(`results/${resultPrefix}integration.json`, JSON.stringify(report, null, 2) + '\n');
