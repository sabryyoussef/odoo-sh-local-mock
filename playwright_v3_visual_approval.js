const { chromium, devices } = require('playwright');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const RUN_ID = fs.readFileSync('/tmp/p3-helpers-erp-cloud-windows-uat/.run_id.v3.visual', 'utf8').trim();
const EVIDENCE_ROOT = `/tmp/p3-helpers-erp-cloud-windows-uat/docs/reports/evidence/helpers-erp-cloud-manual-uat-v3-playwright/${RUN_ID}`;
const BASE_URL = 'http://100.76.217.35:8001';

const ACCOUNTS = [
  { n: 1, username: 'user1', plan: 'trial', package: 'sales', company: 'User 1 Demo Company', port: 8301, db: 'helpers_demo_user1', menus: ['CRM', 'Sales'], modules: ['crm', 'sale_management', 'account'] },
  { n: 2, username: 'user2', plan: 'starter', package: 'trading', company: 'User 2 Demo Company', port: 8302, db: 'helpers_demo_user2', menus: ['Inventory', 'Purchase', 'Sales'], modules: ['crm', 'sale_management', 'purchase', 'stock', 'account'] },
  { n: 3, username: 'user3', plan: 'business', package: 'operations', company: 'User 3 Demo Company', port: 8303, db: 'helpers_demo_user3', menus: ['Inventory', 'Manufacturing', 'Employees', 'Maintenance'], modules: ['purchase', 'stock', 'maintenance', 'hr', 'mrp', 'account'] },
  { n: 4, username: 'user4', plan: 'enterprise', package: 'full_erp', company: 'User 4 Demo Company', port: 8304, db: 'helpers_demo_user4', menus: ['Project', 'CRM', 'Sales', 'Inventory'], modules: ['crm', 'sale_management', 'purchase', 'stock', 'account', 'hr', 'project', 'maintenance', 'mrp'] },
];

function ensureDir(p) { fs.mkdirSync(p, { recursive: true }); }
ensureDir(path.join(EVIDENCE_ROOT, 'screenshots'));
ensureDir(path.join(EVIDENCE_ROOT, 'html'));
ensureDir(path.join(EVIDENCE_ROOT, 'logs'));

function ts() { return new Date().toISOString(); }
function sha256(file) {
  try { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); } catch (e) { return 'error'; }
}

function redact(s) {
  return String(s || '')
    .replace(/password[=:]\s*[^&\s"']+/gi, 'password=***REDACTED***')
    .replace(/admin_passwd\s*=\s*\S+/gi, 'admin_passwd=***REDACTED***')
    .replace(/db_password\s*=\s*\S+/gi, 'db_password=***REDACTED***')
    .replace(/pbkdf2_[a-z0-9$]+/gi, '***HASH***')
    .replace(/token_urlsafe|SESSION_SECRET|GITHUB_CLIENT_SECRET/gi, '***REDACTED***');
}

async function shot(page, name) {
  const file = path.join(EVIDENCE_ROOT, 'screenshots', name);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`[${ts()}] SCREENSHOT ${name} -> ${page.url()}`);
  return file;
}

async function saveHtml(page, name) {
  const html = redact(await page.content());
  const file = path.join(EVIDENCE_ROOT, 'html', name);
  fs.writeFileSync(file, html);
  return file;
}

function attachCollectors(page) {
  const consoleLogs = [];
  const pageErrors = [];
  const failedRequests = [];
  const urlTransitions = [];
  const responses = [];
  page.on('console', msg => consoleLogs.push(redact(`[${ts()}] ${msg.type()} ${msg.text()}`)));
  page.on('pageerror', err => pageErrors.push(redact(`[${ts()}] ${err.message}`)));
  page.on('requestfailed', req => failedRequests.push(redact(`[${ts()}] ${req.method()} ${req.url()} ${req.failure() && req.failure().errorText}`)));
  page.on('response', res => {
    if (res.status() >= 400) responses.push(redact(`${res.status()} ${res.request().method()} ${res.url()}`));
  });
  page.on('framenavigated', frame => {
    if (frame === page.mainFrame()) urlTransitions.push(`[${ts()}] -> ${frame.url()}`);
  });
  return { consoleLogs, pageErrors, failedRequests, urlTransitions, responses };
}

function isolationFailures(bodyText, acc) {
  const fails = [];
  for (const other of ACCOUNTS.filter(a => a.n !== acc.n)) {
    if (bodyText.includes(other.company)) fails.push(`sees ${other.company}`);
    if (bodyText.includes(other.db)) fails.push(`sees ${other.db}`);
  }
  return fails;
}

function localhostHits(text) {
  const hits = [];
  const re = /https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?[^\s"'<>]*/gi;
  let m;
  while ((m = re.exec(text))) hits.push(m[0]);
  return [...new Set(hits)];
}

async function openOdooAppMenu(page) {
  const candidates = [
    page.locator('button.o_menu_toggle'),
    page.locator('.o_navbar_apps_menu button'),
    page.locator('button[title="Home Menu"]'),
    page.locator('a.o_menu_toggle'),
    page.getByRole('button', { name: /Home Menu|Apps Menu/i }),
  ];
  for (const loc of candidates) {
    if (await loc.count()) {
      try {
        await loc.first().click({ timeout: 3000 });
        await page.waitForTimeout(800);
        return true;
      } catch (e) { /* try next */ }
    }
  }
  return false;
}

async function clickNamedMenu(page, names) {
  for (const name of names) {
    const loc = page.getByRole('a', { name: new RegExp(`^${name}$`, 'i') }).or(page.getByRole('button', { name: new RegExp(`^${name}$`, 'i') }));
    if (await loc.count()) {
      try {
        await loc.first().click({ timeout: 4000 });
        await page.waitForTimeout(1200);
        return name;
      } catch (e) { /* continue */ }
    }
    const textLoc = page.locator(`.o_app, .o_menuitem, .o_caption, a`).filter({ hasText: new RegExp(`^${name}$`, 'i') });
    if (await textLoc.count()) {
      try {
        await textLoc.first().click({ timeout: 4000 });
        await page.waitForTimeout(1200);
        return name;
      } catch (e) { /* continue */ }
    }
  }
  return null;
}

async function runUserDesktop(browser, acc) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const logs = attachCollectors(page);
  const prefix = `user${acc.n}`;
  const result = {
    user: acc.username,
    plan: acc.plan,
    package: acc.package,
    company: acc.company,
    port: acc.port,
    db: acc.db,
    portalLogin: false,
    planPackageReady: false,
    openOdooHostOk: false,
    openOdooHref: null,
    odooLogin: false,
    odooHome: false,
    whiteScreen: false,
    companyOk: false,
    appsOk: false,
    menuOpened: null,
    modulesVisible: [],
    isolation: [],
    localhost: [],
    screenshots: [],
    errors: [],
  };

  try {
    await page.goto(`${BASE_URL}/cloud/login`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(600);
    result.screenshots.push(await shot(page, `${prefix}-01-portal-login.png`));
    await saveHtml(page, `${prefix}-01-portal-login.html`);

    await page.locator('input[name="email"]').fill(acc.username);
    await page.locator('input[name="password"]').fill('123');
    await page.getByRole('button', { name: /Sign in/i }).click();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1200);
    if (page.url().includes('/cloud/login')) throw new Error('portal login failed');
    result.portalLogin = true;

    if (!page.url().includes('/cloud/instances')) {
      await page.goto(`${BASE_URL}/cloud/instances`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(800);
    }
    let body = await page.locator('body').innerText();
    const hrefs = await page.locator('a').evaluateAll(as => as.map(a => a.getAttribute('href') || ''));
    const openLink = page.locator('a.btn-hero', { hasText: /Open Odoo/i }).first();
    const href = (await openLink.count()) ? await openLink.getAttribute('href') : null;
    result.openOdooHref = href;
    result.openOdooHostOk = !!(href && href.includes('100.76.217.35') && !/localhost|127\.0\.0\.1/i.test(href) && href.includes(String(acc.port)) && href.includes(acc.db));
    result.planPackageReady = (
      body.toLowerCase().includes(acc.plan.toLowerCase()) &&
      body.toLowerCase().includes(acc.package.toLowerCase()) &&
      /ready/i.test(body) &&
      body.includes(acc.company)
    );
    result.isolation.push(...isolationFailures(body, acc).map(x => 'portal:' + x));
    result.localhost.push(...localhostHits(body + ' ' + hrefs.join(' ')).map(x => 'portal:' + x));
    result.screenshots.push(await shot(page, `${prefix}-02-portal-instance.png`));
    await saveHtml(page, `${prefix}-02-portal-instance.html`);

    if (!await openLink.count()) throw new Error('Open Odoo control missing');
    const popupPromise = page.waitForEvent('popup', { timeout: 8000 }).catch(() => null);
    await openLink.click();
    const popup = await popupPromise;
    const odooPage = popup || page;
    if (popup) attachCollectors(odooPage);
    await odooPage.waitForLoadState('domcontentloaded');
    await odooPage.waitForTimeout(1500);

    if (!odooPage.url().includes(`100.76.217.35:${acc.port}`)) {
      await odooPage.goto(`http://100.76.217.35:${acc.port}/web/login?db=${acc.db}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await odooPage.waitForTimeout(1000);
    }
    result.localhost.push(...localhostHits(odooPage.url()).map(x => 'odoo-url:' + x));

    const loginInput = odooPage.locator('input[name="login"]');
    if (await loginInput.count()) {
      await loginInput.fill(acc.username);
      await odooPage.locator('input[name="password"]').fill('123');
      const dbSel = odooPage.locator('select[name="db"], input[name="db"]');
      if (await dbSel.count()) {
        try { await dbSel.first().fill(acc.db); } catch (e) { try { await dbSel.first().selectOption(acc.db); } catch (e2) {} }
      }
      result.screenshots.push(await shot(odooPage, `${prefix}-03-odoo-login.png`));
      await odooPage.getByRole('button', { name: /^Log in$/i }).click();
      await odooPage.waitForTimeout(5000);
    } else {
      result.screenshots.push(await shot(odooPage, `${prefix}-03-odoo-login.png`));
    }

    try {
      await odooPage.waitForSelector('.o_web_client, .o_navbar, nav.o_main_navbar, .o_action_manager', { timeout: 25000 });
    } catch (e) { /* continue */ }
    try {
      await odooPage.waitForURL(/\/odoo|\/web(?!\/login)/, { timeout: 15000 });
    } catch (e) { /* continue */ }
    await odooPage.waitForTimeout(2000);
    const odooUrl = odooPage.url();
    result.odooLogin = /\/odoo|\/web/.test(odooUrl) && !/\/web\/login/.test(odooUrl);
    if (!result.odooLogin && /\/web\/login/.test(odooUrl)) {
      result.errors.push('still on Odoo login after submit');
    }

    const webClient = odooPage.locator('.o_web_client, .o_action_manager, nav.o_main_navbar, .o_navbar');
    const hasClient = await webClient.count() > 0;
    const odooBody = await odooPage.locator('body').innerText().catch(() => '');
    result.whiteScreen = !hasClient || odooBody.trim().length < 20;
    result.odooHome = hasClient && !result.whiteScreen;
    result.companyOk = odooBody.includes(acc.company);
    result.isolation.push(...isolationFailures(odooBody, acc).map(x => 'odoo-home:' + x));
    result.localhost.push(...localhostHits(odooBody + ' ' + odooUrl).map(x => 'odoo-home:' + x));
    result.screenshots.push(await shot(odooPage, `${prefix}-04-odoo-home.png`));
    await saveHtml(odooPage, `${prefix}-04-odoo-home.html`);

    // Company: user menu / settings if not already visible
    if (!result.companyOk) {
      const userMenu = odooPage.locator('.o_user_menu, .o_avatar, button:has(.o_user_avatar)').first();
      if (await userMenu.count()) {
        try { await userMenu.click({ timeout: 3000 }); await odooPage.waitForTimeout(600); } catch (e) {}
      }
      const body2 = await odooPage.locator('body').innerText();
      result.companyOk = body2.includes(acc.company);
    }
    result.screenshots.push(await shot(odooPage, `${prefix}-05-company.png`));

    await openOdooAppMenu(odooPage);
    await odooPage.waitForTimeout(500);
    result.screenshots.push(await shot(odooPage, `${prefix}-06-apps.png`));
    const appsBody = await odooPage.locator('body').innerText();
    result.appsOk = /CRM|Sales|Inventory|Purchase|Accounting|Project|Employees|Manufacturing|Apps|Discuss/i.test(appsBody);
    for (const mod of acc.modules) {
      const human = {
        crm: /CRM/i, sale_management: /Sales/i, account: /Invoic|Accounting/i,
        purchase: /Purchase/i, stock: /Inventory/i, maintenance: /Maintenance/i,
        hr: /Employees|HR/i, mrp: /Manufactur/i, project: /Project/i,
      }[mod];
      if (human && human.test(appsBody)) result.modulesVisible.push(mod);
    }
    result.isolation.push(...isolationFailures(appsBody, acc).map(x => 'apps:' + x));

    result.menuOpened = await clickNamedMenu(odooPage, acc.menus);
    await odooPage.waitForTimeout(1500);
    result.screenshots.push(await shot(odooPage, `${prefix}-07-functional-menu.png`));
    await saveHtml(odooPage, `${prefix}-07-functional-menu.html`);
    const menuBody = await odooPage.locator('body').innerText();
    result.isolation.push(...isolationFailures(menuBody, acc).map(x => 'menu:' + x));
    result.localhost.push(...localhostHits(menuBody).map(x => 'menu:' + x));
  } catch (err) {
    result.errors.push(String(err.message || err));
    try { result.screenshots.push(await shot(page, `${prefix}-99-error.png`)); } catch (e) {}
  } finally {
    const logFile = path.join(EVIDENCE_ROOT, 'logs', `${prefix}-desktop.json`);
    fs.writeFileSync(logFile, JSON.stringify({
      user: acc.username,
      console: logs.consoleLogs,
      pageErrors: logs.pageErrors,
      failedRequests: logs.failedRequests,
      httpErrors: logs.responses,
      urls: logs.urlTransitions,
    }, null, 2));
    await context.close();
  }
  return result;
}

async function runUserPixel5(browser, acc) {
  const context = await browser.newContext({ ...devices['Pixel 5'] });
  const page = await context.newPage();
  const logs = attachCollectors(page);
  const prefix = `user${acc.n}-pixel5`;
  const result = { user: acc.username, screenshots: [], errors: [], isolation: [], localhost: [] };
  try {
    await page.goto(`${BASE_URL}/cloud/login`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(500);
    result.screenshots.push(await shot(page, `${prefix}-01-portal-login.png`));
    await page.locator('form input[name="email"]').fill(acc.username);
    await page.locator('form input[name="password"]').fill('123');
    await page.locator('form button.btn-hero[type="submit"]').click();
    try {
      await page.waitForURL(url => !String(url).includes('/cloud/login'), { timeout: 15000 });
    } catch (e) { /* continue */ }
    if (!page.url().includes('/cloud/instances')) {
      await page.goto(`${BASE_URL}/cloud/instances`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(800);
    }
    const body = await page.locator('body').innerText();
    result.isolation.push(...isolationFailures(body, acc));
    result.localhost.push(...localhostHits(body));
    result.screenshots.push(await shot(page, `${prefix}-02-portal-instance.png`));
  } catch (err) {
    result.errors.push(String(err.message || err));
    try { result.screenshots.push(await shot(page, `${prefix}-99-error.png`)); } catch (e) {}
  } finally {
    fs.writeFileSync(path.join(EVIDENCE_ROOT, 'logs', `${prefix}.json`), JSON.stringify({
      user: acc.username, console: logs.consoleLogs, pageErrors: logs.pageErrors,
      failedRequests: logs.failedRequests, httpErrors: logs.responses,
    }, null, 2));
    await context.close();
  }
  return result;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const desktop = [];
  const mobile = [];
  try {
    for (const acc of ACCOUNTS) {
      console.log(`\n=== DESKTOP ${acc.username} ===`);
      desktop.push(await runUserDesktop(browser, acc));
    }
    for (const acc of ACCOUNTS) {
      console.log(`\n=== PIXEL5 ${acc.username} ===`);
      mobile.push(await runUserPixel5(browser, acc));
    }
  } finally {
    await browser.close();
  }
  const summary = { runId: RUN_ID, generatedAt: ts(), desktop, mobile };
  fs.writeFileSync(path.join(EVIDENCE_ROOT, 'visual_run_summary.json'), JSON.stringify(summary, null, 2));
  console.log('\n=== SUMMARY ===');
  console.log(JSON.stringify(summary, null, 2));
  const failed = desktop.some(r => r.errors.length || !r.portalLogin || !r.odooHome);
  process.exit(failed ? 2 : 0);
})();
