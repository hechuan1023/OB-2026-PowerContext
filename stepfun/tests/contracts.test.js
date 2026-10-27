import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chunkDocument, retrievalMetrics, median } from '../shared/text.js';
import { validVector, validName, serial } from '../server/store.js';
test('分块保留原文偏移、连续覆盖和代理对', () => {
  const text = '甲乙丙丁😀天地玄黄'.repeat(100);
  const chunks = chunkDocument(text);
  assert.equal(chunks[0].start, 0);
  assert.equal(chunks.at(-1).end, text.length);
  for (const [index, chunk] of chunks.entries()) {
    assert.equal(chunk.text, text.slice(chunk.start, chunk.end));
    assert(!/^[\uDC00-\uDFFF]|[\uD800-\uDBFF]$/.test(chunk.text));
    if (index) assert(chunk.start < chunks[index - 1].end);
  }
  assert.deepEqual(chunkDocument('  \n '), []);
});
test('质量指标按来源去重，且遵循位置折扣', () => {
  assert.deepEqual(retrievalMetrics(['a', 'a', 'b'], ['a', 'b'], 2), { recall: 1, ndcg: 1 });
  assert.equal(retrievalMetrics(['x', 'a'], ['a'], 2).ndcg, 1 / Math.log2(3));
  assert.equal(retrievalMetrics(['x'], ['a']).recall, 0);
  assert.equal(median([9, 1, 3, 5]), 4);
});
test('拒绝无效向量和集合名', () => {
  assert.throws(() => validVector(Array(512).fill(0)));
  assert.throws(() => validVector([NaN]));
  const vector = Array(512).fill(1 / Math.sqrt(512));
  validVector(vector);
  assert.throws(() => validName("abc'; DROP TABLE x"));
  assert.equal(validName('my_notes'), 'my_notes');
});
test('完整数据库操作串行化，单次失败不阻塞后续请求', async () => {
  const events = [];
  const a = serial(async () => { events.push('a1'); await new Promise(resolve => setTimeout(resolve, 10)); events.push('a2'); });
  const b = serial(async () => { events.push('b'); throw new Error('expected'); });
  const c = serial(async () => { events.push('c'); });
  await Promise.allSettled([a, b, c]);
  assert.deepEqual(events, ['a1', 'a2', 'b', 'c']);
});
