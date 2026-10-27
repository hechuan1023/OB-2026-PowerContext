import { mkdir, writeFile, readFile, rename } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { MODEL, REVISION, MODEL_FILES } from '../shared/config.js';

const root = path.resolve('public/models', MODEL);
const lock = JSON.parse(await readFile(new URL('../data/model-lock.json', import.meta.url), 'utf8'));
const force = process.argv.includes('--force');
const names = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'quantize_config.json', ...Object.values(MODEL_FILES)];
await mkdir(path.join(root, 'onnx'), { recursive: true });
const manifest = { model: MODEL, revision: REVISION, generatedAt: new Date().toISOString(), files: [] };
for (const name of names) {
  const target = path.join(root, name);
  let bytes, downloadMs = 0, reused = false;
  try { if (force) throw new Error('Force download'); bytes = await readFile(target); reused = true; } catch {
    const url = `https://huggingface.co/${MODEL}/resolve/${REVISION}/${name}`;
    console.log(`Downloading ${name}`);
    const started = performance.now();
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(300000) });
        if (!response.ok) throw new Error(`${response.status}: ${url}`);
        await pipeline(Readable.fromWeb(response.body), createWriteStream(`${target}.part`));
        bytes = await readFile(`${target}.part`);
        if (createHash('sha256').update(bytes).digest('hex') !== lock.files.find(file => file.name === name)?.sha256) throw new Error(`Downloaded hash mismatch: ${name}`);
        await rename(`${target}.part`, target);
        break;
      } catch (error) {
        if (attempt === 3) throw error;
        console.log(`Retry ${attempt}/3: ${name} (${error.message})`);
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
    downloadMs = performance.now() - started;
  }
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  if (sha256 !== lock.files.find(file => file.name === name)?.sha256) throw new Error(`Hash mismatch: ${name}. Remove the corrupted file and retry.`);
  manifest.files.push({ name, bytes: bytes.length, sha256, downloadMs, reused });
  console.log(`${name}: ${bytes.length} bytes`);
}
const common = manifest.files.filter(f => !f.name.startsWith('onnx/')).reduce((sum, f) => sum + f.bytes, 0);
manifest.totals = Object.fromEntries(Object.entries(MODEL_FILES).map(([dtype, name]) => [dtype, common + manifest.files.find(f => f.name === name).bytes]));
manifest.compression = { fp16: manifest.totals.fp32 / manifest.totals.fp16, q8: manifest.totals.fp32 / manifest.totals.q8 };
await mkdir('results', { recursive: true });
await writeFile('results/model-manifest.json', JSON.stringify(manifest, null, 2) + '\n');
await writeFile('public/models/manifest.json', JSON.stringify(manifest, null, 2) + '\n');
console.log(manifest.totals, manifest.compression);
