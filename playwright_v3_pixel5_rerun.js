const { chromium, devices } = require('playwright');
const fs = require('fs');
const path = require('path');
const RUN_ID = fs.readFileSync('/tmp/p3-helpers-erp-cloud-windows-uat/.run_id.v3.visual', 'utf8').trim();
const EVIDENCE_ROOT = `/tmp/p3-helpers-erp-cloud-windows-uat/docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/${RUN_ID}`;
const BASE_URL = 'http://100.76.217.35:8001';
const ACCOUNTS = [
  { n: 1, username: 'user1', company: 'User 1 Demo Company' },
  { n: 2, username: 'user2', company: 'User 2 Demo Company' },
  { n: 3, username: 'user3', company: 'User 3 Demo Company' },
  { n: 4, username: 'user4', company: 'User 4 Demo Company' },
];

(async () => {
  const browser = await chromium.launch({ headless: true });
  const out = [];
  for (const acc of ACCOUNTS) {
    const context = await browser.newContext({ ...devices['Pixel 5'] });
    const page = await context.newPage();
    const rec = { user: acc.username, urlAfter: null, company: false };
    try {
      await page.goto(`${BASE_URL}/cloud/login`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(400);
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'screenshots', `user${acc.n}-pixel5-01-portal-login.png`) });
      await page.locator('form input[name="email"]').fill(acc.username);
      await page.locator('form input[name="password"]').fill('123');
      await page.locator('form button.btn-hero[type="submit"]').click();
      await page.waitForURL(u => !String(u).includes('/cloud/login'), { timeout: 15000 });
      if (!page.url().includes('/cloud/instances')) {
        await page.goto(`${BASE_URL}/cloud/instances`, { waitUntil: 'domcontentloaded' });
      }
      await page.waitForTimeout(800);
      rec.urlAfter = page.url();
      const body = await page.locator('body').innerText();
      rec.company = body.includes(acc.company);
      rec.ready = /ready/i.test(body);
      rec.isolationFail = ACCOUNTS.filter(a => a.n !== acc.n).some(a => body.includes(a.company));
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'screenshots', `user${acc.n}-pixel5-02-portal-instance.png`) });
    } catch (e) {
      rec.error = String(e.message || e);
      rec.urlAfter = page.url();
      await page.screenshot({ path: path.join(EVIDENCE_ROOT, 'screenshots', `user${acc.n}-pixel5-02-portal-instance.png`) }).catch(() => {});
    }
    out.push(rec);
    console.log(JSON.stringify(rec));
    await context.close();
  }
  await browser.close();
  fs.writeFileSync(path.join(EVIDENCE_ROOT, 'logs', 'pixel5-rerun.json'), JSON.stringify(out, null, 2));
})();
