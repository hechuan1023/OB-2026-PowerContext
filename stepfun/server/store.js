import { SeekdbClient, SeekdbAdminClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';
import { createHash } from 'node:crypto';
import { DIMENSION, MODEL_SIGNATURE } from '../shared/config.js';
import { chunkDocument } from '../shared/text.js';

const config = {
  host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '',
  database: process.env.SEEKDB_DATABASE || 'stepfun', queryTimeout: 60000,
};
let client;
let queue = Promise.resolve();
// seekdb-js hybridSearch uses session variables across multiple statements on one connection.
// Serialize complete operations, including reads, across every collection in this process.
export function serial(operation) {
  const result = queue.then(operation);
  queue = result.catch(() => {});
  return result;
}
export function check(condition, message, status = 400) {
  if (!condition) throw Object.assign(new Error(message), { status });
}
export function validName(name) {
  check(typeof name === 'string' && /^[a-z][a-z0-9_]{2,39}$/.test(name), '知识库名称需为 3–40 位小写字母、数字或下划线，并以字母开头');
  return name;
}
export function validVector(vector) {
  check(Array.isArray(vector) && vector.length === DIMENSION && vector.every(Number.isFinite), `向量必须包含 ${DIMENSION} 个有限数值`);
  const norm = Math.hypot(...vector);
  check(norm > 0.9 && norm < 1.1, '向量必须经过 L2 归一化');
}
export async function initialize() {
  const admin = new SeekdbAdminClient(config);
  try {
    const databases = await admin.listDatabases();
    if (!databases.some(db => db.name === config.database)) await admin.createDatabase(config.database);
  } finally { await admin.close(); }
  client = new SeekdbClient(config);
  await client.execute(`CREATE TABLE IF NOT EXISTS stepfun_kbs (
    name VARCHAR(40) PRIMARY KEY, signature VARCHAR(255) NOT NULL, precision_name VARCHAR(8) NOT NULL,
    state VARCHAR(24) NOT NULL, pending_doc VARCHAR(64), revision BIGINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)`);
  await client.execute(`CREATE TABLE IF NOT EXISTS stepfun_sources (
    kb VARCHAR(40), id VARCHAR(64), title VARCHAR(255), content LONGTEXT,
    PRIMARY KEY (kb, id))`);
}
const collection = name => client.getCollection({ name: `atlas_${validName(name)}`, embeddingFunction: null });
async function info(name) {
  const rows = await client.execute('SELECT * FROM stepfun_kbs WHERE name = ?', [validName(name)]);
  check(rows.length, '知识库不存在', 404);
  check(rows[0].signature === MODEL_SIGNATURE, '模型版本不兼容，请创建新的知识库', 409);
  return rows[0];
}
export async function list() {
  const rows = await client.execute('SELECT * FROM stepfun_kbs ORDER BY created_at, name');
  for (const row of rows) {
    const c = await collection(row.name);
    row.chunks = Number(await c.count());
    const sources = await client.execute('SELECT COUNT(*) AS n FROM stepfun_sources WHERE kb = ?', [row.name]);
    row.documents = Number(sources[0].n);
  }
  return rows;
}
export async function create(name, precision) {
  validName(name);
  check(['fp32', 'fp16', 'q8'].includes(precision), '不支持的模型精度');
  const exists = await client.execute('SELECT name FROM stepfun_kbs WHERE name = ?', [name]);
  check(!exists.length, '知识库名称已存在', 409);
  await client.getOrCreateCollection({
    name: `atlas_${name}`, embeddingFunction: null,
    schema: new Schema({
      vectorIndex: new VectorIndexConfig({ hnsw: { dimension: DIMENSION, distance: 'cosine', m: 16, ef_construction: 200, ef_search: 64 }, embeddingFunction: null }),
      fulltextIndex: new FulltextIndexConfig('ngram', { ngram_token_size: 2 }),
    }),
  });
  await client.execute('INSERT INTO stepfun_kbs (name, signature, precision_name, state) VALUES (?, ?, ?, ?)', [name, MODEL_SIGNATURE, precision, 'ready']);
  return info(name);
}
async function ensurePrecision(kb, signature, precision) {
  check(signature === MODEL_SIGNATURE && kb.precision_name === precision, '当前模型精度或版本与知识库不一致，请切换模型或新建知识库', 409);
}
export async function ingest(name, body) {
  const kb = await info(name);
  await ensurePrecision(kb, body.signature, body.precision);
  const { title, text, embeddings } = body;
  check(typeof title === 'string' && title.length > 0 && title.length <= 255, '文件名无效');
  check(typeof text === 'string' && text.trim() && Buffer.byteLength(text) <= 1024 * 1024, '单份文档需为非空 UTF-8 文本，大小不超过 1 MiB');
  const chunks = chunkDocument(text);
  check(chunks.length <= 2000 && Array.isArray(embeddings) && embeddings.length === chunks.length, '分块数量与向量数量不一致或超过 2000 块限制');
  embeddings.forEach(validVector);
  const sourceId = createHash('sha256').update(title + '\0' + text).digest('hex');
  check(kb.state !== 'writing' || kb.pending_doc === sourceId, '上次导入未完成，请先重新导入相同文件以恢复', 409);
  const c = await collection(name);
  const writeStart = performance.now();
  // Persist the recovery marker before any writes. A crash never exposes a partly imported KB.
  await client.execute('UPDATE stepfun_kbs SET state = ?, pending_doc = ? WHERE name = ?', ['writing', sourceId, name]);
  await client.execute('REPLACE INTO stepfun_sources (kb, id, title, content) VALUES (?, ?, ?, ?)', [name, sourceId, title, text]);
  const writeBatchSize = body.refreshEachBatch === true ? 1 : 64;
  for (let i = 0; i < chunks.length; i += writeBatchSize) {
    const batch = chunks.slice(i, i + writeBatchSize);
    await c.upsert({
      ids: batch.map(chunk => `${sourceId}_${chunk.ordinal}`),
      documents: batch.map(chunk => chunk.text), embeddings: embeddings.slice(i, i + writeBatchSize),
      metadatas: batch.map(chunk => ({ sourceId, title, ordinal: chunk.ordinal, start: chunk.start, end: chunk.end, signature: MODEL_SIGNATURE, precision: body.precision })),
    });
    // Only used by the explicitly selected unoptimized experiment.
    if (body.refreshEachBatch === true) await c.refresh_index();
  }
  await client.execute('UPDATE stepfun_kbs SET state = ? WHERE name = ?', ['pending_refresh', name]);
  const writeMs = performance.now() - writeStart;
  if (body.deferRefresh === true) return { sourceId, chunks: chunks.length, writeMs, refreshMs: 0, state: 'pending_refresh' };
  const refreshStart = performance.now();
  await refresh(name);
  return { sourceId, chunks: chunks.length, writeMs, refreshMs: performance.now() - refreshStart };
}
export async function refresh(name) {
  const kb = await info(name);
  check(kb.state !== 'writing', '上次导入未完成，请重新导入相同文件，刷新不能替代缺失的数据写入', 409);
  const c = await collection(name);
  await c.refresh_index();
  await client.execute('UPDATE stepfun_kbs SET state = ?, pending_doc = NULL, revision = revision + 1 WHERE name = ?', ['ready', name]);
  return info(name);
}
export async function search(name, body) {
  const kb = await info(name);
  check(kb.state === 'ready', '索引尚未就绪，请刷新或重试未完成的导入', 409);
  await ensurePrecision(kb, body.signature, body.precision);
  validVector(body.embedding);
  check(typeof body.query === 'string' && body.query.trim().length > 0 && body.query.length <= 500, '查询长度需为 1–500 个字符');
  const k = body.k ?? 5;
  check(Number.isInteger(k) && k >= 1 && k <= 20, 'K 必须为 1–20');
  check(['hybrid', 'vector'].includes(body.mode || 'hybrid'), '不支持的检索模式');
  const c = await collection(name);
  const started = performance.now();
  const result = body.mode === 'vector'
    ? await c.query({ queryEmbeddings: [body.embedding], nResults: k, include: ['documents', 'metadatas', 'distances'] })
    : await c.hybridSearch({
      query: { whereDocument: { $contains: body.query }, nResults: Math.max(20, k) },
      knn: { queryEmbeddings: [body.embedding], nResults: Math.max(20, k) },
      rank: { rrf: { rankConstant: 60, rankWindowSize: Math.max(20, k) } }, nResults: k,
      include: ['documents', 'metadatas', 'distances'],
    });
  return {
    revision: Number(kb.revision), seekdbMs: performance.now() - started,
    results: result.ids[0].map((id, i) => ({ id, text: result.documents?.[0]?.[i], metadata: result.metadatas?.[0]?.[i], score: result.distances?.[0]?.[i] })),
  };
}
export async function source(name, id) {
  await info(name);
  const rows = await client.execute('SELECT id, title, content FROM stepfun_sources WHERE kb = ? AND id = ?', [name, id]);
  check(rows.length, '原文不存在', 404);
  return rows[0];
}
export async function health() {
  const rows = await client.execute('SELECT VERSION() AS version');
  return { database: config.database, version: rows[0].version };
}
export async function close() { if (client) await client.close(); }
