// Acceptance against scripts/homepage_fixture.py and production Next servers.
// No production URLs or credentials are accepted. Requires Chrome and Node 22+.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { createServer } from 'node:net';

const api = 'http://127.0.0.1:8097';
const website = 'http://127.0.0.1:3017';
const dashboard = 'http://127.0.0.1:3018';
const artifacts = resolve(process.env.HOMEPAGE_ARTIFACTS || '../../build-artifacts/homepage-browser');
await mkdir(artifacts, { recursive: true });
const delay = ms => new Promise(done => setTimeout(done, ms));
const portReservation = createServer();
await new Promise(done => portReservation.listen(0, '127.0.0.1', done));
const debugPort = portReservation.address().port;
await new Promise(done => portReservation.close(done));
const browser = spawn(process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  `--remote-debugging-port=${debugPort}`, `--user-data-dir=${artifacts}/chrome-${Date.now()}`, 'about:blank',
], { stdio: 'ignore', windowsHide: true });
let socket;
let nextId = 0;
const pending = new Map();
const results = [];
let restoreConfig;
let editorToken;
function send(method, params = {}) {
  const id = ++nextId;
  return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
}
async function evaluate(expression) {
  const packet = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (packet.exceptionDetails) throw new Error(packet.exceptionDetails.exception?.description || packet.exceptionDetails.text);
  return packet.result.value;
}
async function waitFor(expression, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(`Promise.resolve(${expression}).then(Boolean)`).catch(() => false)) return;
    await delay(100);
  }
  throw new Error(`Timed out: ${expression}`);
}
async function navigate(url, allowRedirect = false) {
  const previous = await evaluate('performance.timeOrigin');
  await send('Page.navigate', { url });
  const destination = allowRedirect ? `location.origin === ${JSON.stringify(new URL(url).origin)}` : `location.href === ${JSON.stringify(url)}`;
  await waitFor(`performance.timeOrigin !== ${previous} && ${destination} && document.readyState === 'complete'`);
}
async function clickText(text) {
  await evaluate(`(() => { const button = [...document.querySelectorAll('button')].find(el => el.textContent.trim() === ${JSON.stringify(text)}); if (!button || button.disabled) throw Error('Button unavailable: ' + ${JSON.stringify(text)}); button.click(); })()`);
}
async function setInput(selector, value) {
  await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; setter.call(el, ${JSON.stringify(value)}); el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await delay(100);
}
async function screenshot(name) {
  const { data } = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(`${artifacts}/${name}.png`, Buffer.from(data, 'base64'));
}
async function login(email) {
  const response = await fetch(`${api}/api/accounts/login/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: 'fixture-pass' }) });
  assert.equal(response.status, 200);
  return response.json();
}
async function authenticate(origin, email) {
  const credentials = await login(email);
  await navigate(`${origin}/`, true);
  await evaluate(`localStorage.setItem('access_token', ${JSON.stringify(credentials.access)}); localStorage.setItem('refresh_token', ${JSON.stringify(credentials.refresh)});`);
  await navigate(`${origin}/${origin === dashboard ? 'homepage' : ''}`, true);
  return credentials.access;
}
async function getConfig(token) {
  const response = await fetch(`${api}/api/homepage/manage/`, { headers: { Authorization: `Bearer ${token}` } });
  assert.equal(response.status, 200);
  return response.json();
}
async function putConfig(token, config) {
  const response = await fetch(`${api}/api/homepage/manage/`, { method: 'PUT', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
  assert.equal(response.status, 200, await response.text());
}
try {
  let endpoint;
  for (let i = 0; i < 50; i++) {
    try { endpoint = (await fetch(`http://127.0.0.1:${debugPort}/json/list`).then(r => r.json())).find(tab => tab.type === 'page')?.webSocketDebuggerUrl; } catch { /* Starting Chrome. */ }
    if (endpoint) break;
    await delay(200);
  }
  assert.ok(endpoint, 'Chrome must start');
  socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = ({ data }) => {
    const packet = JSON.parse(data), request = pending.get(packet.id);
    if (!request) return;
    pending.delete(packet.id);
    if (packet.error) request.reject(new Error(packet.error.message)); else request.resolve(packet.result);
  };
  socket.onclose = () => { for (const request of pending.values()) request.reject(new Error('Chrome DevTools connection closed')); pending.clear(); };
  await send('Page.enable'); await send('Runtime.enable');
  const editor = await authenticate(dashboard, 'editor@homepage.test');
  editorToken = editor;
  await waitFor("document.querySelector('input[aria-label=\"Search approved titles\"]')");
  const original = await getConfig(editor);
  restoreConfig = original;
  assert.ok(!await evaluate("document.body.textContent.includes('Add Private fixture title')"));
  assert.ok(!await evaluate("document.body.textContent.includes('Add Inactive fixture title')"));
  await setInput('input[aria-label="Search approved titles"]', 'Title 3');
  await clickText('Add Homepage Title 3');
  await evaluate("document.querySelector('button[aria-label=\"Move Homepage Title 3 up\"]').click()");
  await evaluate("document.querySelector('button[aria-label=\"Move Movies up\"]').click()");
  assert.ok(await evaluate("[...document.querySelectorAll('input[type=checkbox]')].find(el => el.closest('label').textContent.includes('Continue Watching')).disabled"));
  await setInput('#release-duration', '0');
  await clickText('Save changes');
  await waitFor("document.body.textContent.includes('Choose a New Releases duration')");
  assert.deepEqual((await getConfig(editor)).featured_titles, original.featured_titles);
  await setInput('#release-duration', '14');
  await clickText('Preview');
  await waitFor("document.querySelector('[aria-label=\"Homepage preview\"]')");
  assert.deepEqual((await getConfig(editor)).featured_titles, original.featured_titles);
  await clickText('Save changes');
  await waitFor("document.body.textContent.includes('Homepage saved.')");
  const saved = await getConfig(editor);
  assert.equal(saved.featured_titles.length, 3);
  assert.equal(saved.featured_titles[1].detail.title, 'Homepage Title 3');
  assert.equal(saved.new_releases.duration, 14);
  await screenshot('dashboard-saved');
  await evaluate("document.querySelector('button[aria-label=\"Remove Homepage Title 3\"]').click()");
  await clickText('Cancel');
  await waitFor("document.querySelector('button[aria-label=\"Remove Homepage Title 3\"]')");
  results.push({ check: 'Dashboard selection, reordering, protected section, validation, preview, save, cancel', pass: true });

  // A server denial must preserve the draft and show a useful error.
  await send('Fetch.enable', { patterns: [{ urlPattern: '*api/homepage/manage/*', requestStage: 'Request' }] });
  const intercept = async ({ data }) => {
    const message = JSON.parse(data);
    if (message.method !== 'Fetch.requestPaused') return;
    const { requestId, request } = message.params;
    if (request.method === 'PUT') await send('Fetch.fulfillRequest', { requestId, responseCode: 403, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify({ detail: 'Permission revoked during editing.' })).toString('base64') });
    else await send('Fetch.continueRequest', { requestId });
  };
  socket.addEventListener('message', intercept);
  await setInput('#release-duration', '10');
  await clickText('Save changes');
  await waitFor("document.body.textContent.includes('Permission revoked during editing.')");
  assert.equal(await evaluate("document.querySelector('#release-duration').value"), '10');
  assert.equal((await getConfig(editor)).new_releases.duration, 14);
  await send('Fetch.disable'); socket.removeEventListener('message', intercept);
  results.push({ check: 'Dashboard 403 keeps unsaved draft and reports server error', pass: true });

  await navigate(`${website}/`); await evaluate('localStorage.clear()'); await navigate(`${website}/`);
  await waitFor("document.querySelectorAll('.channel-card').length === 8");
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Continue Watching\"]') !== null"), false);
  const measurements = [];
  for (const [width, height] of [[320,740],[390,844],[768,1024],[1440,900]]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 768 });
    await navigate(`${website}/`);
    await waitFor("document.querySelectorAll('.channel-card').length === 8");
    const dimensions = await evaluate(`(() => { const hero = document.querySelector('.home-hero').getBoundingClientRect(); const card = document.querySelector('.channel-card .aspect-video').getBoundingClientRect(); const channels = document.querySelector('[aria-label="Featured Channels"]').getBoundingClientRect(); return {width: innerWidth, documentWidth: document.documentElement.scrollWidth, heroHeight: hero.height, channelsTop: channels.top, ratio: card.width/card.height, searchWidth: document.querySelector('button[aria-label="Search catalogue"]').getBoundingClientRect().width}; })()`);
    assert.ok(dimensions.documentWidth <= width, 'No horizontal page overflow');
    assert.ok(Math.abs(dimensions.ratio - 16/9) < 0.02, 'Channel artwork is 16:9');
    if (width < 768) { assert.ok(dimensions.heroHeight < height * 0.6); assert.ok(dimensions.channelsTop < height); assert.ok(dimensions.searchWidth <= 48); }
    await evaluate("document.querySelector('button[aria-label=\"Scroll channels right\"]').click()");
    await waitFor("document.querySelector('.channel-rail').scrollLeft > 50");
    if (width < 768) {
      await evaluate("document.querySelector('.channel-rail').scrollLeft = 0"); await delay(500);
      await send('Emulation.setTouchEmulationEnabled', { enabled: true });
      const point = await evaluate("(() => {const r=document.querySelector('.channel-rail').getBoundingClientRect();return {x:r.right-20,y:r.top+40};})()");
      await send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [point] });
      for (let i=1;i<=7;i++) { await send('Input.dispatchTouchEvent', { type:'touchMove', touchPoints:[{x:point.x-i*24,y:point.y}] }); await delay(25); }
      await send('Input.dispatchTouchEvent', { type:'touchEnd', touchPoints:[] });
      await waitFor("document.querySelector('.channel-rail').scrollLeft > 30");
    }
    measurements.push(dimensions);
    await screenshot(`website-${width}`);
  }
  assert.match(await evaluate("document.querySelector('.channel-card a').getAttribute('href')"), /^\/channels\/\d+$/);
  results.push({ check: 'Website responsive hero, 16:9 featured channels, mobile search, carousel buttons and touch', pass: true, measurements });

  // Pause the real request to exercise loading, failure, retry and empty states.
  let homepageMode = 'hold';
  const held = [];
  const publicResponse = await fetch(`${api}/api/homepage/`).then(response => response.json());
  const interceptHomepage = async ({ data }) => {
    const message = JSON.parse(data);
    if (message.method !== 'Fetch.requestPaused') return;
    const requestId = message.params.requestId;
    if (homepageMode === 'hold') held.push(requestId);
    else if (homepageMode === 'fail') await send('Fetch.fulfillRequest', { requestId, responseCode:503, responseHeaders:[{name:'Content-Type',value:'application/json'}], body:Buffer.from('{}').toString('base64') });
    else if (homepageMode === 'empty') await send('Fetch.fulfillRequest', { requestId, responseCode: 200, responseHeaders: [{ name:'Content-Type', value:'application/json' }], body: Buffer.from(JSON.stringify({ sections: publicResponse.sections.map(section => ({ ...section, items: [] })) })).toString('base64') });
    else await send('Fetch.continueRequest', { requestId });
  };
  await send('Fetch.enable', { patterns: [{ urlPattern: '*api/homepage/', requestStage:'Request' }] });
  socket.addEventListener('message', interceptHomepage);
  await send('Page.navigate', { url: `${website}/` });
  await waitFor("document.querySelector('section[aria-busy=true]')");
  while (!held.length) await delay(100);
  homepageMode = 'fail';
  for (const requestId of held) await send('Fetch.fulfillRequest', { requestId, responseCode:503, responseHeaders:[{name:'Content-Type',value:'application/json'}], body:Buffer.from('{}').toString('base64') });
  await waitFor("document.body.textContent.includes(\"Couldn't load the homepage.\")");
  homepageMode = 'pass';
  await clickText('Try again');
  await waitFor("document.querySelectorAll('.channel-card').length === 8");
  homepageMode = 'empty';
  await navigate(`${website}/`);
  await waitFor("document.querySelector('.home-hero-fallback') && !document.querySelector('[aria-busy=true]')");
  assert.equal(await evaluate("document.querySelectorAll('.home-catalogue h2').length"), 0);
  await screenshot('website-empty-catalogue');
  await send('Fetch.disable'); socket.removeEventListener('message', interceptHomepage);
  results.push({ check: 'Website loading skeleton, API failure, retry and empty catalogue', pass: true });

  await navigate(`${website}/programs/${original.featured_titles[0].content}?play=1`);
  await waitFor("document.querySelector('video')");
  assert.ok(!await evaluate("document.querySelector('#main-content').textContent.includes('Sign in to watch')"));
  results.push({ check: 'Existing anonymous full-content playback remains available', pass: true });

  const viewerToken = await authenticate(website, 'viewer@homepage.test');
  await waitFor("document.querySelector('[aria-label=\"Continue Watching\"]')");
  const resume = await evaluate("[...document.querySelectorAll('[aria-label=\"Continue Watching\"] a')].map(el => ({href:el.getAttribute('href'),text:el.textContent}))");
  assert.equal(resume.length, 2);
  assert.ok(resume.some(item => item.href.includes('episode=')), 'Completed episode projects next episode');
  assert.ok(resume.some(item => item.text.includes('Homepage Title 1')));
  assert.ok(resume.every(item => !item.text.includes('Homepage Title 2')));
  await screenshot('website-viewer');
  await navigate(`${website}/programs/${original.featured_titles[0].content}?play=1`);
  await waitFor("document.querySelector('video')");
  let progressPosts = 0;
  let refreshCalls = 0;
  await send('Network.enable');
  const watchProgress = ({ data }) => {
    const message = JSON.parse(data);
    if (message.method === 'Network.requestWillBeSent' && message.params.request.method === 'POST' && message.params.request.url.endsWith('/api/library/progress/')) progressPosts++;
    if (message.method === 'Network.requestWillBeSent' && message.params.request.url.endsWith('/api/accounts/token/refresh/')) refreshCalls++;
  };
  socket.addEventListener('message', watchProgress);
  const resetProgress = await fetch(`${api}/api/library/progress/`, { method:'POST', headers:{ Authorization:`Bearer ${viewerToken}`, 'Content-Type':'application/json' }, body:JSON.stringify({content:original.featured_titles[0].content,position_seconds:1,duration_seconds:100}) });
  assert.equal(resetProgress.status, 200);
  // Invalid access with a real owner refresh exercises the same 401 recovery
  // path as expiry, without changing server clocks or production credentials.
  await evaluate("localStorage.setItem('access_token', localStorage.getItem('access_token').split('.').slice(0,2).join('.') + '.invalid')");
  await evaluate("(() => { const video = document.querySelector('video'); Object.defineProperty(video, 'duration', {configurable:true,value:100}); Object.defineProperty(video, 'currentTime', {configurable:true,value:45}); video.dispatchEvent(new Event('pause')); })()");
  await waitFor(`fetch(${JSON.stringify(`${api}/api/library/progress/${original.featured_titles[0].content}/`)}, {headers:{Authorization:${JSON.stringify(`Bearer ${viewerToken}`)}}}).then(response=>response.json()).then(data=>data.position_seconds===45)`);
  const beforeSwitch = progressPosts;
  assert.ok(refreshCalls > 0, 'Owner progress request must refresh invalid access and retry');
  const switched = await login('other@homepage.test');
  await evaluate(`localStorage.setItem('access_token', ${JSON.stringify(switched.access)}); const video = document.querySelector('video'); Object.defineProperty(video, 'currentTime', {configurable:true,value:70}); video.dispatchEvent(new Event('pause'));`);
  await delay(500);
  assert.equal(progressPosts, beforeSwitch, 'Old player must not post under another account token');
  const leaked = await fetch(`${api}/api/library/progress/${original.featured_titles[0].content}/`, { headers:{ Authorization:`Bearer ${switched.access}` } });
  assert.equal(leaked.status, 404);
  socket.removeEventListener('message', watchProgress);
  results.push({ check: 'Player refreshes owner access, records progress, and refuses writes after another tab switches account token', pass: true });
  await authenticate(website, 'other@homepage.test');
  await waitFor("document.querySelector('[aria-label=\"Continue Watching\"]')?.textContent.includes('Homepage Title 2')");
  assert.ok(!await evaluate("document.querySelector('[aria-label=\"Continue Watching\"]').textContent.includes('Homepage Title 1')"));
  await evaluate("document.dispatchEvent(new Event('unauthorized'))");
  await waitFor("!document.querySelector('[aria-label=\"Continue Watching\"]')");
  results.push({ check: 'Viewer isolation, next episode resume link, authentication loss hides progress', pass: true });

  const hidden = structuredClone(saved);
  hidden.sections.find(section => section.id === 'movies').enabled = false;
  hidden.sections = [hidden.sections[0], ...hidden.sections.slice(1).reverse()];
  await putConfig(editor, hidden);
  await navigate(`${website}/`); await evaluate('localStorage.clear()'); await navigate(`${website}/`);
  await waitFor("document.querySelector('[aria-label=\"Featured Channels\"]')");
  const labels = await evaluate("[...document.querySelectorAll('.home-catalogue > section > div > h2, .home-catalogue > section > h2')].map(el=>el.textContent)");
  assert.deepEqual(labels, hidden.sections.filter(section => section.enabled && !['featured_hero','continue_watching'].includes(section.id)).map(section => section.id === 'featured_channels' ? 'Featured Channels' : section.id === 'new_releases' ? 'New Releases' : section.id.replaceAll('_',' ').replace(/\b\w/g,letter=>letter.toUpperCase()).replace('Tv','TV')));
  hidden.featured_titles = []; hidden.featured_channels = [];
  hidden.new_releases = { duration: 1, unit: 'hours' };
  await putConfig(editor, hidden);
  await navigate(`${website}/`);
  await waitFor("document.querySelector('.home-hero-fallback')");
  assert.ok(!await evaluate("document.querySelector('[aria-label=\"Featured Channels\"]')"));
  await screenshot('website-empty-featured');
  await putConfig(editor, original);
  results.push({ check: 'Backend section order, disabled category, empty channel rail, safe hero fallback', pass: true });

  await authenticate(dashboard, 'viewer@homepage.test');
  await waitFor("!location.pathname.startsWith('/homepage')");
  assert.ok(!await evaluate("document.querySelector('input[aria-label=\"Search approved titles\"]')"));
  results.push({ check: 'Dashboard denies viewer homepage management route', pass: true });
  await writeFile(`${artifacts}/results.json`, JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} catch (error) {
  if (socket?.readyState === WebSocket.OPEN) {
    await screenshot('failure').catch(() => {});
    const body = await evaluate('document.body.innerText').catch(() => 'unavailable');
    await writeFile(`${artifacts}/failure.txt`, `${error.stack}\n${body}`);
  }
  throw error;
} finally {
  if (restoreConfig && editorToken) await putConfig(editorToken, restoreConfig).catch(() => {});
  socket?.close(); browser.kill();
}
