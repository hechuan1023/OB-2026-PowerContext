import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

process.chdir(fileURLToPath(new URL('..', import.meta.url)));
try { process.loadEnvFile('.env'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const store = await import('./store.js');
const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'");
  // The application fetches only same-origin static model files and its local database bridge.
  if (req.path.startsWith('/api')) res.setHeader('Cache-Control', 'no-store');
  next();
});
app.use('/api', (req, res, next) => {
  const origin = req.get('origin');
  if (origin && origin !== `http://${req.get('host')}` && origin !== `https://${req.get('host')}`) return res.status(403).json({ error: 'Origin rejected' });
  next();
});
app.use(express.json({ limit: '32mb' }));
app.use('/ort', express.static(path.resolve('node_modules/onnxruntime-web/dist'), { maxAge: '1d' }));
let ready = false;
let initialization;
async function ensureDb() {
  if (ready) return;
  initialization ??= store.serial(store.initialize).then(() => { ready = true; }).catch(error => { initialization = null; throw error; });
  await initialization;
}
const api = handler => async (req, res, next) => {
  try { await ensureDb(); res.json(await store.serial(() => handler(req))); }
  catch (error) { next(error); }
};
app.get('/api/health', api(() => store.health()));
app.get('/api/collections', api(() => store.list()));
app.post('/api/collections', api(req => store.create(req.body.name, req.body.precision)));
app.post('/api/collections/:name/import', api(req => store.ingest(req.params.name, req.body)));
app.post('/api/collections/:name/refresh', api(req => store.refresh(req.params.name)));
app.post('/api/collections/:name/search', api(req => store.search(req.params.name, req.body)));
app.get('/api/collections/:name/sources/:id', api(req => store.source(req.params.name, req.params.id)));
app.use('/api', (req, res) => res.status(404).json({ error: 'API not found' }));
app.use((error, req, res, next) => {
  console.error(error.message);
  res.status(error.status || 503).json({ error: error.message });
});
if (process.argv.includes('--dev')) {
  const { createServer } = await import('vite');
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'spa' });
  app.use(vite.middlewares);
} else {
  app.use(express.static(path.resolve('dist')));
  app.get('/{*path}', (req, res) => res.sendFile(path.resolve('dist/index.html')));
}
const port = Number(process.env.PORT || 3210);
const server = app.listen(port, '127.0.0.1', () => console.log(`Local Atlas: http://localhost:${port}`));
async function shutdown() { server.close(); await store.close(); process.exit(0); }
process.once('SIGINT', shutdown);
process.once('SIGTERM', shutdown);
