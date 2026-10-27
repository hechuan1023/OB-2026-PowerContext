import { pipeline, env } from '@huggingface/transformers';
import { MODEL, MODEL_SIGNATURE, PROFILES, QUERY_PREFIX } from '../shared/config.js';

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.useBrowserCache = true;
env.backends.onnx.wasm.wasmPaths = '/ort/';
// Use the exact bundled ORT binary; no runtime CDN requests or remote inference.
env.backends.onnx.wasm.numThreads = self.crossOriginIsolated ? Math.min(4, navigator.hardwareConcurrency || 1) : 1;
let extractor, actual, profile;
let activeId;
let transfers = [];
const nativeFetch = self.fetch.bind(self);
self.fetch = async (...args) => {
  const start = performance.now();
  const response = await nativeFetch(...args);
  if (String(args[0]).includes('/models/')) {
    const clone = response.clone();
    transfers.push(clone.arrayBuffer().then(bytes => ({ url: String(args[0]), bytes: bytes.byteLength, fetchMs: performance.now() - start })));
  }
  return response;
};
const progress = value => self.postMessage({ id: activeId, event: 'progress', value });
async function load(requested) {
  if (extractor && requested === profile) return { ...actual, reused: true };
  if (extractor) await extractor.dispose();
  extractor = null;
  transfers = [];
  const config = PROFILES[requested];
  if (!config) throw new Error('Unknown profile');
  const started = performance.now();
  let device = config.device, dtype = config.dtype, fallback = '';
  let gpuAdapter = null;
  if (device === 'webgpu') {
    const adapter = await navigator.gpu?.requestAdapter();
    gpuAdapter = adapter?.info ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture, device: adapter.info.device, description: adapter.info.description } : null;
    if (!adapter || !adapter.features.has('shader-f16')) {
      if (requested === 'gpu') throw new Error('WebGPU / shader-f16 不可用；严格 GPU 实验未执行');
      device = 'wasm'; dtype = 'q8'; fallback = 'WebGPU 或 shader-f16 不可用，已自动使用 WASM INT8';
    }
  }
  const create = () => pipeline('feature-extraction', MODEL, {
    device, dtype, local_files_only: true,
    progress_callback: value => progress({ phase: 'model', ...value }),
  });
  try { extractor = await create(); }
  catch (error) {
    if (requested !== 'auto' || device !== 'webgpu') throw error;
    device = 'wasm'; dtype = 'q8'; fallback = `WebGPU 初始化失败，已自动降级 WASM INT8：${error.message}`;
    extractor = await create();
  }
  const loadMs = performance.now() - started;
  const warmupStart = performance.now();
  try { await extractor(['本地推理预热'], { pooling: 'cls', normalize: true, truncation: true, max_length: 256 }); }
  catch (error) {
    await extractor.dispose(); extractor = null;
    if (requested !== 'auto' || device !== 'webgpu') throw error;
    device = 'wasm'; dtype = 'q8'; fallback = `WebGPU 首次推理失败，已降级 WASM INT8：${error.message}`;
    extractor = await create();
    await extractor(['本地推理预热'], { pooling: 'cls', normalize: true, truncation: true, max_length: 256 });
  }
  actual = { device, dtype, fallback, loadMs, warmupMs: performance.now() - warmupStart, gpuAdapter, signature: MODEL_SIGNATURE, threads: env.backends.onnx.wasm.numThreads, transfers: await Promise.all(transfers), reused: false };
  profile = requested;
  progress({ phase: 'ready', ...actual });
  return actual;
}
async function embed({ texts, query = false, batchSize = 8, sort = true }) {
  if (!extractor) throw new Error('请先加载模型');
  if (!Array.isArray(texts) || !texts.length || !Number.isInteger(batchSize) || batchSize < 1 || batchSize > 32) throw new Error('Invalid embedding batch');
  const ordered = texts.map((text, i) => ({ text: query ? QUERY_PREFIX + text : text, i }));
  if (sort) ordered.sort((a, b) => a.text.length - b.text.length);
  const embeddings = new Array(texts.length);
  const batches = [];
  const started = performance.now();
  for (let i = 0; i < ordered.length; i += batchSize) {
    const batch = ordered.slice(i, i + batchSize);
    const batchStart = performance.now();
    const tensor = await extractor(batch.map(item => item.text), { pooling: 'cls', normalize: true, truncation: true, max_length: 256 });
    const vectors = tensor.tolist();
    batch.forEach((item, n) => { embeddings[item.i] = vectors[n]; });
    batches.push({ count: batch.length, ms: performance.now() - batchStart });
    progress({ phase: 'embedding', done: Math.min(i + batchSize, ordered.length), total: ordered.length });
  }
  return { embeddings, inferenceMs: performance.now() - started, batches, ...actual };
}
let tasks = Promise.resolve();
self.onmessage = ({ data }) => {
  tasks = tasks.then(async () => {
    activeId = data.id;
    try {
      const value = data.type === 'load' ? await load(data.profile) : await embed(data);
      self.postMessage({ id: data.id, value });
    } catch (error) {
      self.postMessage({ id: data.id, error: error.message });
    }
  });
};
