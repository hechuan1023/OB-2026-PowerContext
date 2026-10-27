import { SeekdbClient } from 'seekdb';
import { writeFile } from 'node:fs/promises';
try { process.loadEnvFile('.env'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const db = new SeekdbClient({ host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881), user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '', database: process.env.SEEKDB_DATABASE || 'stepfun' });
try {
  const rows = await db.execute('SHOW TABLES');
  const physical = rows.map(row => Object.values(row)[0]).find(name => /^c\$v2\$[a-f0-9]+$/.test(name));
  if (!physical) throw new Error('请先创建知识库');
  const statements = ['-- 实际运行后的 SHOW CREATE TABLE；第一张表为代表性 SDK 集合，不用于手动替代 SDK 建表。'];
  for (const table of [physical, 'stepfun_kbs', 'stepfun_sources']) {
    const ddl = await db.execute(`SHOW CREATE TABLE \`${table}\``);
    statements.push(ddl[0]['Create Table'] + ';');
  }
  await writeFile('results/schema.sql', statements.join('\n\n') + '\n');
} finally { await db.close(); }
