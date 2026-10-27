import './style.css';
import { Engine, api } from './engine.js';
import { runBenchmark } from './benchmark.js';
import dataset from '../data/evaluation.json';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const ms = value => value == null ? '—' : `${value.toFixed(0)} ms`;
let collections = [], current = localStorage.getItem('atlas-collection') || '', busy = false, generation = 0;
let measurements = { sessionStartedAt: new Date().toISOString(), imports: [], searches: [], loads: [] };
let benchmarkReport;
function status(message, error = false) { $('activity').textContent = message; $('activity').classList.toggle('error', error); }
function progress(value) {
  if (value.phase === 'model') {
    status(`加载本地模型 · ${value.file || value.status || '初始化'}${value.progress ? ` · ${value.progress.toFixed(0)}%` : ''}`);
  } else if (value.phase === 'embedding' || value.phase === 'writing') {
    $('progress').hidden = false; $('progress').value = value.done / value.total * 100;
    status(`${value.phase === 'embedding' ? '浏览器本地向量化' : '写入 seekdb'} · ${value.done} / ${value.total}`);
  }
}
let engine = new Engine(progress);
function controls() {
  document.querySelectorAll('button:not(.dialog-close), input, select, textarea').forEach(el => { el.disabled = busy; });
  const compatible = engine.actual && collections.find(kb => kb.name === current)?.precision_name === engine.actual.dtype;
  $('search').disabled = busy || !compatible;
}
async function task(work) {
  if (busy) return;
  busy = true; controls();
  try { await work(); }
  catch (error) { status(error.message, true); }
  finally { busy = false; $('progress').hidden = true; controls(); }
}
function drawRuntime(actual) {
  $('backend').innerHTML = `${actual.device === 'webgpu' ? 'WebGPU' : 'WASM'} <small style="font-size:13px">${actual.dtype === 'q8' ? 'INT8' : actual.dtype.toUpperCase()}</small><span>本机推理 · ${actual.device === 'wasm' ? `${actual.threads} 线程` : 'GPU 加速'}</span>`;
  $('fallback').hidden = !actual.fallback;
  $('fallback').textContent = actual.fallback;
  $('load-ms').textContent = ms(actual.loadMs);
  $('load-model').textContent = '重新加载 / 切换模型';
}
async function loadModel() {
  let actual;
  try { actual = await engine.load($('profile').value); }
  catch (error) { $('backend').innerHTML = '未加载<span>请尝试自动模式或 WASM</span>'; throw error; }
  measurements.loads.push({ at: new Date().toISOString(), ...actual });
  drawRuntime(actual);
  status(`模型就绪 · ${actual.device.toUpperCase()} / ${actual.dtype} · 预热 ${ms(actual.warmupMs)}${actual.fallback ? ` · ${actual.fallback}` : ''}`);
  return actual;
}
function select(name) {
  current = name; generation++;
  localStorage.setItem('atlas-collection', current);
  $('current-name').textContent = name || '尚未选择知识库';
  const kb = collections.find(kb => kb.name === current);
  $('doc-count').textContent = kb ? `${kb.documents} / ${kb.chunks}` : '0 / 0';
  $('result-count').textContent = kb ? `${kb.documents} 份文档 · ${kb.chunks} 个片段 · ${kb.state}` : '等待导入';
  $('results-heading').textContent = '知识库内容';
  $('results').innerHTML = `<div class="empty-state"><div class="empty-symbol">⌘</div><h3>${kb?.chunks ? '知识已连接，开始探索' : '从一份文档开始'}</h3><p>${kb?.chunks ? '在上方输入问题，检索库中的原文片段。' : '导入你的笔记与资料，或试试内置的中文语料。'}</p></div>`;
  for (const id of ['embed-ms', 'write-ms', 'query-ms', 'seek-ms', 'total-ms']) $(id).textContent = '—';
  renderCollections(); controls();
  if (kb && engine.actual && kb.precision_name !== engine.actual.dtype) status(`当前库使用 ${kb.precision_name}，请加载相同精度的模型，或创建新的知识库。`);
  if (kb?.state === 'writing') status('检测到未完成导入，请重新导入上次的相同文件以恢复。', true);
  if (kb?.state === 'pending_refresh') status('数据已写入，等待索引刷新。点击“刷新索引”后即可查询。');
}
function renderCollections() {
  $('collections').innerHTML = collections.map(kb => `<button class="kb-item ${kb.name === current ? 'active' : ''}" data-kb="${escape(kb.name)}"><span class="kb-icon">▤</span><span>${escape(kb.name)}</span><small>${escape(kb.precision_name)}</small></button>`).join('') || '<p style="font-size:11px;color:#97ad9b;padding:12px">加载模型后，点击 ＋ 新建</p>';
  $('collections').querySelectorAll('button').forEach(button => { button.disabled = busy; button.onclick = () => select(button.dataset.kb); });
}
async function refreshCollections(preserve = true) {
  collections = await api('/collections');
  if (!collections.some(kb => kb.name === current)) current = collections[0]?.name || '';
  if (!preserve) select(current);
  else {
    renderCollections();
    const kb = collections.find(kb => kb.name === current);
    $('current-name').textContent = current || '尚未选择知识库';
    $('doc-count').textContent = kb ? `${kb.documents} / ${kb.chunks}` : '0 / 0';
  }
}
async function readyKb() {
  if (!engine.actual) await loadModel();
  if (!current) {
    const name = `notes_${engine.actual.dtype}_${Date.now().toString(36)}`;
    await api('/collections', { name, precision: engine.actual.dtype });
    current = name; await refreshCollections(false);
  }
  const kb = collections.find(kb => kb.name === current);
  if (kb?.precision_name !== engine.actual.dtype) throw new Error(`知识库精度为 ${kb.precision_name}，当前模型为 ${engine.actual.dtype}。请切换模型或新建知识库。`);
  return current;
}
async function importDocs(docs) {
  if (!docs.length || docs.length > 40) throw new Error('每批请选择 1–40 份文档');
  if (docs.some(doc => !doc.text.trim() || new TextEncoder().encode(doc.text).length > 1048576 || doc.title.length > 255)) throw new Error('文件需为非空 UTF-8 文本，文件名不超过 255 字符，单文件不超过 1 MiB');
  const bytes = docs.reduce((sum, doc) => sum + new TextEncoder().encode(doc.text).length, 0);
  if (bytes > 2097152) throw new Error('每批文本总量不超过 2 MiB，请分批导入');
  const kb = await readyKb();
  let result;
  try { result = await engine.importDocuments(kb, docs, { batchSize: 8 }); }
  finally { await refreshCollections(); }
  measurements.imports.push({ at: new Date().toISOString(), collection: kb, ...result });
  $('embed-ms').textContent = ms(result.inferenceMs);
  $('write-ms').textContent = `${ms(result.writeMs)} / ${ms(result.refreshMs)}`;
  $('result-count').textContent = `${collections.find(item => item.name === kb).chunks} 个片段 · 索引已就绪`;
  status(`导入完成 · ${result.documents} 份文档 / ${result.chunks} 个片段 · 端侧编码 ${ms(result.inferenceMs)} · 索引刷新完成，可立即检索`);
}
async function readFiles(files) {
  const documents = [];
  for (const file of files) {
    if (!/\.(txt|md)$/i.test(file.name) || file.size > 1048576) throw new Error('仅支持不超过 1 MiB 的 .txt / .md 文件');
    const text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    documents.push({ title: file.name, text });
  }
  return documents;
}
function modal(html) { $('modal-content').innerHTML = html; $('modal').showModal(); controls(); }
function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('load-model').onclick = () => task(loadModel);
$('new-kb').onclick = () => {
  modal(`<h2 class="dialog-title">创建知识库</h2><p class="dialog-note">知识库与当前模型精度绑定。请先加载需要的模型，再创建。</p><label class="dialog-field">名称<input id="kb-name" placeholder="例如 my_notes" value="my_notes" maxlength="40"></label><button id="create-kb" class="primary">创建知识库</button>`);
  $('create-kb').onclick = () => task(async () => {
    if (!engine.actual) await loadModel();
    const name = $('kb-name').value.trim();
    await api('/collections', { name, precision: engine.actual.dtype });
    $('modal').close(); current = name; await refreshCollections(false); status(`已创建 ${name} · ${engine.actual.dtype}`);
  });
};
$('paste').onclick = () => {
  modal(`<h2 class="dialog-title">粘贴一段知识</h2><label class="dialog-field">文档标题<input id="paste-title" value="我的笔记.txt" maxlength="255"></label><label class="dialog-field">原文<textarea id="paste-text" placeholder="在这里粘贴文字…"></textarea></label><button id="import-paste" class="primary">本地向量化并导入</button>`);
  $('import-paste').onclick = () => task(async () => { const docs = [{ title: $('paste-title').value, text: $('paste-text').value }]; $('modal').close(); await importDocs(docs); });
};
$('pick-files').onclick = () => $('files').click();
$('files').onchange = () => task(async () => { try { await importDocs(await readFiles($('files').files)); } finally { $('files').value = ''; } });
const importSample = () => task(() => importDocs(dataset.documents.map(doc => ({ title: doc.title, text: doc.text }))));
$('sample').onclick = importSample;
$('empty-sample')?.addEventListener('click', importSample);
$('refresh').onclick = () => task(async () => { if (!current) throw new Error('请先选择知识库'); await api(`/collections/${current}/refresh`, {}); await refreshCollections(); status('索引刷新完成。'); });
const dropzone = $('dropzone');
dropzone.ondragover = event => { event.preventDefault(); if (!busy) dropzone.classList.add('dragover'); };
dropzone.ondragleave = () => dropzone.classList.remove('dragover');
dropzone.ondrop = event => { event.preventDefault(); dropzone.classList.remove('dragover'); if (!busy) task(async () => importDocs(await readFiles(event.dataTransfer.files))); };
dropzone.onkeydown = event => { if (event.target === dropzone && ['Enter', ' '].includes(event.key) && !busy) { event.preventDefault(); $('files').click(); } };
$('search-form').onsubmit = event => {
  event.preventDefault();
  task(async () => {
    const query = $('query').value.trim(); if (!query) throw new Error('请输入查询');
    const kb = await readyKb(), requestGeneration = generation;
    const submittedAt = performance.now();
    const result = await engine.search(kb, query, { mode: $('search-mode').value, k: Number($('top-k').value) });
    if (kb !== current || requestGeneration !== generation) return;
    $('results-heading').textContent = '检索结果'; $('result-count').textContent = `${result.results.length} 个片段 · 修订 ${result.revision}`;
    $('results').innerHTML = result.results.length ? result.results.map((item, index) => `<article class="result-card"><div class="result-top"><span class="result-title">${escape(item.metadata?.title)}</span><span class="result-rank">${String(index + 1).padStart(2, '0')}</span></div><p>${escape(item.text)}</p><div class="result-bottom"><span>片段 ${Number(item.metadata?.ordinal) + 1} · 字符 ${item.metadata?.start}–${item.metadata?.end} · ${escape($('search-mode').value.toUpperCase())}</span><button class="text-button source-button" data-source="${escape(item.metadata?.sourceId)}">查看原文 ↗</button></div></article>`).join('') : '<div class="empty-state"><h3>没有找到结果</h3><p>试试其他描述，或先向知识库导入文档。</p></div>';
    $('results').querySelectorAll('.source-button').forEach(button => { button.onclick = () => task(async () => { const source = await api(`/collections/${current}/sources/${button.dataset.source}`); modal(`<h2 class="dialog-title">${escape(source.title)}</h2><div class="source-text">${escape(source.content)}</div>`); }); });
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    result.displayTotalMs = performance.now() - submittedAt;
    $('query-ms').textContent = ms(result.queryEmbeddingMs); $('seek-ms').textContent = ms(result.seekdbMs); $('total-ms').textContent = ms(result.displayTotalMs);
    measurements.searches.push({ at: new Date().toISOString(), collection: kb, query, mode: $('search-mode').value, ...result });
    status(`找到 ${result.results.length} 个片段 · ${result.device.toUpperCase()} 本地编码 · seekdb ${$('search-mode').value === 'hybrid' ? '数据库端 RRF 混合排序' : '向量检索'}`);
  });
};
$('benchmark').onclick = () => {
  modal(`<h2 class="dialog-title">性能与质量实验</h2><p class="dialog-note">使用固定 24 份中文文档、21 条标注问题，每项重复 3 次。依次执行 FP32 WASM、FP16 WebGPU、INT8 WASM，以及 INT8 单条编码和逐文档刷新对照。实验会创建独立 bench 知识库；可能需要数分钟。GPU 不可用时如实记录失败。</p><button id="start-benchmark" class="primary">开始真实测量</button><p id="bench-progress" class="bench-progress"></p><div id="bench-output"></div>`);
  $('start-benchmark').onclick = () => task(async () => {
    engine.dispose(); engine = new Engine(progress);
    benchmarkReport = await runBenchmark(message => { if ($('bench-progress')) $('bench-progress').textContent = message; status(message); });
    if ($('bench-output')) {
      $('bench-output').innerHTML = `<table class="bench-table"><thead><tr><th>配置</th><th>文档/s</th><th>检索 ms</th><th>E2E ms</th><th>Recall / nDCG</th></tr></thead><tbody>${benchmarkReport.rows.map(row => row.error ? `<tr><td>${escape(row.label)}</td><td colspan="4">${escape(row.error)}</td></tr>` : `<tr><td>${escape(row.label)}</td><td>${row.summary.docsPerSecond.toFixed(1)}</td><td>${row.summary.seekdbMs.toFixed(1)}</td><td>${row.summary.totalMs.toFixed(1)}</td><td>${row.summary.recall.toFixed(3)} / ${row.summary.ndcg.toFixed(3)}</td></tr>`).join('')}</tbody></table><button id="download-benchmark" class="secondary">下载全部原始记录</button>`;
      $('download-benchmark').onclick = () => download(benchmarkReport, 'atlas-benchmark.json');
      $('bench-progress').textContent = '测量完成 · 报告中位数 · 包含全部原始记录';
    }
    $('backend').innerHTML = '未加载<span>实验结束，请重新加载工作模型</span>';
    await refreshCollections(); status('实验完成，可下载原始记录。请重新加载模型后继续检索。');
  });
};
$('export').onclick = () => download({ ...measurements, benchmark: benchmarkReport }, 'atlas-session.json');
// A browser-only integration entry point uses the same Engine and database APIs as the UI.
window.atlas = { Engine, api, runBenchmark, dataset, getState: () => ({ current, actual: engine.actual, busy }), engine: () => engine };
try {
  const health = await api('/health'); $('db-status').textContent = `● seekdb 已连接 · ${health.version.match(/seekdb-v([\d.]+)/)?.[1] || 'local'}`;
  await refreshCollections(false);
} catch (error) { $('db-status').textContent = 'seekdb 未连接'; status(`数据库连接失败：${error.message}。请启动 seekdb 后刷新页面。`, true); }
controls();
