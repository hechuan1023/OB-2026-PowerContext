import { chromium } from '@playwright/test';
import { existsSync } from 'node:fs';
export async function browser({ fallback = false } = {}) {
  const executablePath = process.env.CHROME_PATH || [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium',
  ].find(existsSync);
  return chromium.launch({ executablePath, headless: true,
    args: fallback ? ['--disable-gpu', '--disable-software-rasterizer', '--disable-features=WebGPU'] : [],
  });
}
export async function pageFor(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const page = await context.newPage();
  const failures = [];
  const environmentRequests = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (['localhost', '127.0.0.1'].includes(url.hostname)) return route.continue();
    // This Windows host injects Kaspersky traffic into fresh Chrome profiles.
    // Block it, record it separately, and continue to reject any app-originated external call.
    if (['gc.kis.v2.scr.kaspersky-labs.com', 'me.kis.v2.scr.kaspersky-labs.com'].includes(url.hostname)) {
      environmentRequests.push({ origin: url.origin, type: route.request().resourceType(), action: 'blocked system antivirus injection' });
      return route.abort();
    }
    failures.push(`External request: ${url.origin}`); return route.abort();
  });
  page.on('pageerror', error => failures.push(error.message));
  await page.goto(process.env.APP_URL || 'http://localhost:3210');
  await page.waitForFunction(() => !!window.atlas);
  return { page, failures, context, environmentRequests };
}
