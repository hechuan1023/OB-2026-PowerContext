import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import { browser, pageFor } from './browser-utils.js';
const instance = await browser();
await mkdir('results', { recursive: true });
try {
  const { page, failures, environmentRequests } = await pageFor(instance);
  page.on('console', message => { if (message.text().startsWith('BENCH ')) console.log(message.text()); });
  const profiles = process.argv.slice(2).length ? process.argv.slice(2) : ['fp32', 'gpu', 'wasm'];
  const report = await page.evaluate(async profiles => {
    let last = '';
    return window.atlas.runBenchmark(message => {
      const stage = message.split(' · ')[0] + (message.includes('加载') ? ' load' : message.includes('导入') ? ' import' : ' query');
      if (stage !== last) { console.log(`BENCH ${message}`); last = stage; }
    }, profiles, 3);
  }, profiles);
  assert.deepEqual(failures, [], '运行时只能访问本地资源');
  report.environmentRequests = environmentRequests;
  await writeFile('results/benchmark.json', JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report.rows.map(row => ({ label: row.label, runtime: row.runtime && { device: row.runtime.device, dtype: row.runtime.dtype }, summary: row.summary, error: row.error })), null, 2));
} finally { await instance.close(); }
