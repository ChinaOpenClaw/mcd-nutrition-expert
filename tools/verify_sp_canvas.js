// 前端 SP 矩阵象限一致性验证
// 1) 打开页面，等待 MATRIX 渲染
// 2) 在页面上下文里重放 drawSP() 的坐标换算，逐点判断「落入的底色矩形」是否等于后端 quadrant
// 3) 截取 #spchart 局部图
const { chromium } = require('playwright');
const path = require('path');

const BASE = process.env.MCD_WEB || 'http://127.0.0.1:8899/';
const OUT = path.resolve(__dirname, '..', '.shots');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForFunction('typeof MATRIX !== "undefined" && MATRIX && MATRIX.points && MATRIX.points.length > 0', { timeout: 30000 });
  // 切到 SP 矩阵页签（section id = v-matrix）
  await page.evaluate(() => {
    document.querySelectorAll('section.view').forEach(s => s.classList.remove('on'));
    const sec = document.querySelector('#v-matrix');
    if (sec) sec.classList.add('on');
    const nav = document.querySelectorAll('.nav a, nav a, .tab, .tabs a');
    nav.forEach(a => a.classList.remove('on'));
  });
  await page.waitForTimeout(600);
  await page.evaluate(() => { if (typeof drawSP === 'function') drawSP(); });
  await page.waitForTimeout(600);

  const res = await page.evaluate(() => {
    const cv = document.querySelector('#spchart');
    const dpr = window.devicePixelRatio || 1;
    const W = cv.clientWidth, H = cv.clientHeight;
    const pad = { l: 64, r: 22, t: 22, b: 52 };
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;
    const ax = MATRIX.axes, th = MATRIX.thresholds;
    const maxX = ax.x.max, maxY = ax.y.max;

    const xL = pad.l, xR = pad.l + pw, yT = pad.t, yB = pad.t + ph;
    const xM = pad.l + th.x / maxX * pw, yM = pad.t + ph - (th.y / maxY) * ph;

    // 与 drawSP 中 quads 数组同序：主力/双刃/解馋/填充
    const quads = [
      { q: 'sweet_point', x: xM, y: yM, w: xR - xM, h: yB - yM },
      { q: 'double_edge', x: xM, y: yT, w: xR - xM, h: yM - yT },
      { q: 'treat', x: xL, y: yT, w: xM - xL, h: yM - yT },
      { q: 'filler', x: xL, y: yM, w: xM - xL, h: yB - yM },
    ];

    let bad = 0; const samples = [];
    for (const p of MATRIX.points) {
      const cx = pad.l + (p.x / maxX) * pw;
      const cy = pad.t + ph - (p.y / maxY) * ph;
      let hit = null;
      for (const q of quads) {
        if (cx >= q.x && cx <= q.x + q.w && cy >= q.y && cy <= q.y + q.h) { hit = q.q; break; }
      }
      if (hit !== p.quadrant) {
        bad++;
        if (samples.length < 8) samples.push({ name: p.name, x: p.x, y: p.y, backend: p.quadrant, rect: hit });
      }
    }
    return { total: MATRIX.points.length, bad, samples, th, maxX, maxY };
  });

  console.log('阈值 x=%s y=%s  轴上限 x=%s y=%s', res.th.x.toFixed(2), res.th.y.toFixed(2), res.maxX, res.maxY);
  console.log('散点总数 = %d   底色矩形与后端判定不一致 = %d', res.total, res.bad);
  res.samples.forEach(s => console.log('  MISMATCH %s (%s,%s) backend=%s rect=%s', s.name, s.x, s.y, s.backend, s.rect));
  console.log('结果：%s', res.bad === 0 ? 'PASS 前端象限底色与后端判定完全一致' : 'FAIL');

  const cv = await page.$('#spchart');
  if (cv) {
    await cv.screenshot({ path: path.join(OUT, 'sp-chart-only.png') });
    console.log('已截图 .shots/sp-chart-only.png');
  }
  await page.screenshot({ path: path.join(OUT, '03-sp-matrix.png'), fullPage: true });
  console.log('已截图 .shots/03-sp-matrix.png');

  await browser.close();
  process.exit(res.bad === 0 ? 0 : 1);
})();
