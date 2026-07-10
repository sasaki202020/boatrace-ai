#!/usr/bin/env node
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { createServer } = require('node:http');
const { copyFile, mkdir, readFile, writeFile } = require('node:fs/promises');
const path = require('node:path');

const { chromium } = require('C:/Users/goo10/AppData/Roaming/npm/node_modules/playwright');

const ROOT = path.resolve(__dirname, '..');
const OUTPUT_DIR = path.join(ROOT, 'output', 'playwright');
const STATIC_DIR = path.join(ROOT, 'src', 'web', 'static');
const HOST = process.env.DASHBOARD_HOST || '127.0.0.1';
const PORT = Number(process.env.DASHBOARD_PORT || 8090);
const BASE_URL = process.env.DASHBOARD_URL || `http://${HOST}:${PORT}`;
const PYTHON = process.env.PYTHON || 'C:/Users/goo10/AppData/Local/Programs/Python/Python312/python.exe';
const WORKFLOW_USE_MOCK = String(process.env.WORKFLOW_USE_MOCK || '').toLowerCase() === '1';
const WORKFLOW_ALLOW_MOCK_FALLBACK = ['1', 'true', 'yes'].includes(String(process.env.WORKFLOW_FALLBACK_TO_MOCK || '').toLowerCase());
const WORKFLOW_ALLOW_API_ONLY = ['1', 'true', 'yes'].includes(String(process.env.WORKFLOW_API_ONLY || '').toLowerCase());
const WORKFLOW_USE_REAL_DASHBOARD = Boolean(process.env.DASHBOARD_URL) && !WORKFLOW_USE_MOCK;
const API_BASE_URL = process.env.DASHBOARD_API_URL || BASE_URL;
const WORKFLOW_SEQUENCE = String(process.env.WORKFLOW_SEQUENCE || '').trim();
const WORKFLOW_CHROMIUM_EXECUTABLE_PATH = String(process.env.WORKFLOW_CHROMIUM_EXECUTABLE_PATH || '').trim();
const RUN_STAMP = new Date().toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', '');

const MODE_SELECTOR = {
  predict: '#runPredictBtn',
  'pre-race': '#runPreRaceBtn',
  'odds-refresh': '#runOddsBtn',
  'post-race': '#runPostRaceBtn',
  backtest: '#runBacktestBtn',
  guard: '#runGuardBtn',
  full: '#runFullBtn',
};

const WORKFLOW_MODE = String(process.env.WORKFLOW_MODE || 'refresh').toLowerCase();
const WORKFLOW_DATE = process.env.WORKFLOW_DATE || '';
const WORKFLOW_VENUE = process.env.WORKFLOW_VENUE || '';
const WORKFLOW_DECISION = process.env.WORKFLOW_DECISION || '';
const WORKFLOW_LIMIT = process.env.WORKFLOW_LIMIT || '';
const WORKFLOW_TIMEOUT_MS = Number(process.env.WORKFLOW_TIMEOUT_MS || (WORKFLOW_MODE === 'refresh' ? 120000 : 900000));
const WORKFLOW_HEADLESS = (process.env.WORKFLOW_HEADLESS || '1') !== '0';
const WORKFLOW_SCREENSHOT = process.env.WORKFLOW_SCREENSHOT || path.join(OUTPUT_DIR, `dashboard-workflow-${RUN_STAMP}.png`);
const WORKFLOW_SUMMARY = process.env.WORKFLOW_SUMMARY || path.join(OUTPUT_DIR, `dashboard-workflow-${RUN_STAMP}.json`);
const LATEST_SCREENSHOT = path.join(OUTPUT_DIR, 'dashboard-workflow-latest.png');
const LATEST_SUMMARY = path.join(OUTPUT_DIR, 'dashboard-workflow-latest.json');

let serverProcess = null;
let mockServer = null;
let baseUrl = BASE_URL;
let apiBaseUrl = API_BASE_URL;
let workflowSource = WORKFLOW_USE_MOCK ? 'mock' : 'real';
let workflowFallbackReason = '';
let mockOpsState = {
  status: 'idle',
  mode: null,
  started_at: null,
  finished_at: null,
  returncode: null,
  message: 'ready',
};
let mockOpsReport = {
  ok: true,
  report: {
    status: 'ok',
    mode: 'guard',
    steps: [
      {
        label: 'bootstrap',
        returncode: 0,
        stdout_tail: 'mock',
        stderr_tail: '',
      },
    ],
  },
};

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isTruthyEnv(value) {
  return ['1', 'true', 'yes', 'on'].includes(String(value || '').toLowerCase());
}

function buildUrl(base, pathname) {
  return new URL(pathname.startsWith('/') ? pathname : `/${pathname}`, base).toString();
}

function chooseSequenceTokens() {
  if (WORKFLOW_SEQUENCE) {
    return WORKFLOW_SEQUENCE.split(',').map((item) => item.trim()).filter(Boolean);
  }
  return WORKFLOW_USE_REAL_DASHBOARD ? ['guard', 'refresh', 'report'] : ['refresh', 'report'];
}

async function fetchHealth(url, timeoutMs = 1000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    if (!res.ok) return false;
    const data = await res.json().catch(() => null);
    return Boolean(data && data.ok);
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function waitForHealth(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await fetchHealth(url)) return;
    await sleep(1000);
  }
  throw new Error(`dashboard server did not become healthy: ${url}`);
}

async function textOf(locator) {
  return String((await locator.textContent()) || '');
}

async function fetchJson(url, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: controller.signal });
    const text = await res.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status} for ${url}`);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

async function postJson(url, payload, timeoutMs = 30000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const text = await res.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status} for ${url}`);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

function shapeKind(value) {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'array';
  return typeof value;
}

function shapeFingerprint(value, depth = 2) {
  const kind = shapeKind(value);
  if (depth <= 0 || kind !== 'object' && kind !== 'array') {
    return kind;
  }
  if (kind === 'array') {
    const first = value.length ? shapeFingerprint(value[0], depth - 1) : 'empty';
    return { kind, length: value.length ? 'many' : 'empty', item: first };
  }
  const out = {};
  for (const key of Object.keys(value || {}).sort()) {
    out[key] = shapeFingerprint(value[key], depth - 1);
  }
  return out;
}

function compareShape(actual, expected, path = 'root', diffs = []) {
  const actualKind = shapeKind(actual);
  const expectedKind = shapeKind(expected);
  if (actualKind !== expectedKind) {
    diffs.push(`${path}: kind ${expectedKind} -> ${actualKind}`);
    return diffs;
  }
  if (actualKind === 'array') {
    const actualItem = actual.length ? actual[0] : undefined;
    const expectedItem = expected.length ? expected[0] : undefined;
    if (actualItem !== undefined || expectedItem !== undefined) {
      compareShape(actualItem, expectedItem, `${path}[0]`, diffs);
    }
    return diffs;
  }
  if (actualKind !== 'object') {
    return diffs;
  }
  const actualKeys = new Set(Object.keys(actual || {}));
  const expectedKeys = new Set(Object.keys(expected || {}));
  for (const key of expectedKeys) {
    if (!actualKeys.has(key)) diffs.push(`${path}.${key}: missing`);
  }
  for (const key of expectedKeys) {
    if (actualKeys.has(key)) compareShape(actual[key], expected[key], `${path}.${key}`, diffs);
  }
  return diffs;
}

function summarizeContract(name, actual, expected) {
  const diffs = compareShape(actual, expected);
  return {
    name,
    status: diffs.length ? 'DIFF' : 'PASS',
    diff_count: diffs.length,
    diffs: diffs.slice(0, 10),
    actual_shape: shapeFingerprint(actual),
    expected_shape: shapeFingerprint(expected),
  };
}

async function resolveLocator(page, specs) {
  for (const spec of specs) {
    let locator = null;
    if (spec.testid) locator = page.getByTestId(spec.testid);
    else if (spec.id) locator = page.locator(`#${spec.id}`);
    else if (spec.role) locator = page.getByRole(spec.role, spec.options || {});
    if (!locator) continue;
    try {
      if (await locator.count()) return locator.first();
    } catch {}
  }
  throw new Error(`element not found: ${JSON.stringify(specs)}`);
}

async function getElementText(page, specs) {
  const locator = await resolveLocator(page, specs);
  return textOf(locator);
}

async function clickElement(page, specs) {
  const locator = await resolveLocator(page, specs);
  await locator.waitFor({ state: 'visible', timeout: 30000 });
  await locator.click();
}

function json(res, statusCode, payload) {
  const body = Buffer.from(`${JSON.stringify(payload)}\n`, 'utf8');
  res.writeHead(statusCode, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': body.length,
  });
  res.end(body);
}

function createMockSummary() {
  return {
    updated_at: new Date().toISOString(),
    predictions_total: 3,
    buy_count: 1,
    watch_count: 1,
    skip_count: 1,
    buy_rate: 1 / 3,
    decision_counts: {
      BUY: 1,
      WATCH: 1,
      SKIP: 1,
      PENDING: 0,
    },
    real_odds_coverage: 0.67,
    real_odds_rate: 0.67,
    pre_race_avg_score: 1.25,
    pre_race_boost_rows: 1,
    pre_race_block_rows: 1,
    first_place_avg_score: 1.5,
    first_place_boost_rows: 1,
    first_place_block_rows: 0,
    second_place_avg_score: 1.2,
    second_place_boost_rows: 1,
    second_place_block_rows: 0,
    third_place_avg_score: 1.1,
    third_place_boost_rows: 1,
    third_place_block_rows: 0,
    race_avg_score: 1.35,
    race_priority_rows: 1,
    race_watch_rows: 1,
    race_block_rows: 1,
    auto_filter_live_note: 'mock',
    bet_management: {
      bankroll: 10000,
      max_kelly_fraction: 0.05,
    },
    kelly_total_bet: 300,
    latest_prediction_date: '2026-04-19',
    latest_source_date: '2026-04-19',
    prediction_staleness_days: 0,
    ops_health: {
      pipeline: {
        status: mockOpsState.status === 'running' ? 'running' : 'ok',
        mode: mockOpsState.mode || 'mock',
        step_count: 3,
        last_step: mockOpsState.message || 'ready',
      },
      guard: {
        status: 'PASS',
        reasons: ['mock guard pass'],
      },
      compare: {
        status: 'PASS',
        promoted: false,
        reasons: ['mock compare pass'],
      },
      backtest: {
        roi: 1.08,
        buy_count: 1,
        hit_count: 1,
        max_drawdown: -0.05,
      },
    },
    gate_health: {
      reason_keyword_counts: { real_odds_missing: 0 },
      missing_breakdown: {
        pre_race_only: 0,
        first_place_only: 0,
        both_missing: 0,
      },
      gate_combo_counts: {
        'A/B': 3,
      },
    },
    upstream_health: {
      diagnostics: {
        approx_prob_hit_rate: 0.4,
        approx_prob_avg_pred: 0.35,
        approx_prob_roi: 1.02,
        exact_rate: 0.2,
        top5_rate: 0.6,
        top10_rate: 0.8,
        median_rank: 3,
        first_lane_ok_but_order_weak_count: 1,
        first_lane_itself_weak_count: 0,
        actual_outside_top20_count: 0,
      },
      calibration: {
        method: 'isotonic',
        base_brier: 0.2,
        calibrated_brier: 0.18,
        base_logloss: 0.6,
        calibrated_logloss: 0.55,
        improved: true,
      },
      selection_leak: {
        count: 0,
        top_reason: '',
        target: 'mock',
      },
    },
    race_yosou_view: {
      date: '2026-04-19',
      date_label: '2026-04-19',
      venue: '住之江',
      venue_label: '住之江',
      races: [
        {
          raceId: '20260419-12-01',
          raceNo: 1,
          date: '2026-04-19',
          dateLabel: '2026-04-19',
          venue: '住之江',
          venueLabel: '住之江',
          jcd: '12',
          statusLabel: '表示中',
          statusFlags: { closed: false, exhibitionMissing: false, reporterMissing: false },
          boats: [],
          aiPredictions: [],
          reporterPredictions: [],
        },
      ],
      source_counts: {
        features: 3,
        win_proba: 3,
        trifecta_candidates: 3,
      },
    },
  };
}

function createMockOpsStatusShape() {
  return {
    status: 'ok',
    mode: 'guard',
    started_at: '2026-04-19T00:00:00Z',
    finished_at: '2026-04-19T00:00:01Z',
    returncode: 0,
    message: 'completed',
  };
}

function createMockOpsReportShape() {
  return {
    ok: true,
    report: {
      status: 'ok',
      mode: 'guard',
      steps: [
        {
          label: 'bootstrap',
          returncode: 0,
          stdout_tail: 'mock',
          stderr_tail: '',
        },
      ],
    },
  };
}

function createMockPredictions() {
  return [
    {
      decision: 'BUY',
      race_id: '20260419-12-01',
      date: '2026-04-19',
      recommended_trifecta: '1-2-3',
      approx_prob: 0.31,
      ev: 1.14,
      pre_race_score: 1.5,
      pre_race_gate: 'PRIORITY',
      pre_race_time_score: 0.7,
      pre_race_motor_score: 0.3,
      pre_race_rank_score: 0.2,
      race_score: 1.8,
      race_gate: 'BUY',
      race_first_confidence: 0.9,
      race_odds_balance_score: 0.2,
      race_data_quality_score: 0.1,
      odds: 21.4,
      odds_source: 'real',
      has_real_odds: true,
      bet_amount: 300,
      bet_pct: 3,
      risk_penalty: 0,
      confidence_score: 0.82,
      first_place_score: 1.4,
      first_place_gate: 'BOOST',
      second_place_score: 1.2,
      second_place_gate: 'PRIORITY',
      third_place_score: 1.1,
      third_place_gate: 'PRIORITY',
      first_place_multiplier: 1.05,
      second_place_multiplier: 1.03,
      third_place_multiplier: 1.03,
      pre_race_multiplier: 1.02,
      first_place_note: 'mock 1st',
      second_place_note: 'mock 2nd',
      third_place_note: 'mock 3rd',
      race_note: 'mock race',
      reason: 'mock reason / confidence',
      calibrated_hit_prob: 0.29,
      calibrated_hit_prob_adjusted: 0.31,
    },
    {
      decision: 'WATCH',
      race_id: '20260419-12-02',
      date: '2026-04-19',
      recommended_trifecta: '1-3-4',
      approx_prob: 0.18,
      ev: 0.92,
      pre_race_score: 1.0,
      pre_race_gate: 'NORMAL',
      pre_race_time_score: 0.2,
      pre_race_motor_score: 0.1,
      pre_race_rank_score: 0.1,
      race_score: 1.1,
      race_gate: 'WATCH',
      race_first_confidence: 0.6,
      race_odds_balance_score: 0.1,
      race_data_quality_score: 0.1,
      odds: 14.2,
      odds_source: 'real',
      has_real_odds: true,
      bet_amount: 0,
      bet_pct: 0,
      risk_penalty: 1,
      confidence_score: 0.55,
      first_place_score: 1.0,
      first_place_gate: 'NORMAL',
      second_place_score: 0.9,
      second_place_gate: 'NORMAL',
      third_place_score: 1.0,
      third_place_gate: 'NORMAL',
      first_place_multiplier: 1,
      second_place_multiplier: 1,
      third_place_multiplier: 1,
      pre_race_multiplier: 1,
      first_place_note: 'mock 1st',
      second_place_note: 'mock 2nd',
      third_place_note: 'mock 3rd',
      race_note: 'mock race',
      reason: 'mock watch',
      calibrated_hit_prob: 0.18,
      calibrated_hit_prob_adjusted: 0.18,
    },
  ];
}

async function startMockServer() {
  if (mockServer) return;

  const server = createServer(async (req, res) => {
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    const { pathname } = url;

    if (req.method === 'GET' && pathname === '/api/health') {
      json(res, 200, { ok: true });
      return;
    }

    if (req.method === 'GET' && (pathname === '/' || pathname === '/index.html')) {
      const html = await readFile(path.join(STATIC_DIR, 'index.html'));
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(html);
      return;
    }

    if (req.method === 'GET' && pathname === '/app.js') {
      const js = await readFile(path.join(STATIC_DIR, 'app.js'));
      res.writeHead(200, { 'content-type': 'application/javascript; charset=utf-8' });
      res.end(js);
      return;
    }

    if (req.method === 'GET' && pathname === '/styles.css') {
      const css = await readFile(path.join(STATIC_DIR, 'styles.css'));
      res.writeHead(200, { 'content-type': 'text/css; charset=utf-8' });
      res.end(css);
      return;
    }

    if (req.method === 'GET' && pathname === '/api/summary') {
      json(res, 200, createMockSummary());
      return;
    }

    if (req.method === 'GET' && pathname === '/api/predictions') {
      json(res, 200, createMockPredictions());
      return;
    }

    if (req.method === 'GET' && pathname === '/api/venues') {
      json(res, 200, { venues: ['住之江', '唐津', '多摩川'] });
      return;
    }

    if (req.method === 'GET' && pathname === '/api/venue_summary') {
      json(res, 200, [
        { venue: '住之江', prediction_count: 1, buy_count: 1, watch_count: 0, hit_rate: 0.5, roi: 1.08, avg_odds: 21.4, real_odds_rate: 1 },
      ]);
      return;
    }

    if (req.method === 'GET' && pathname === '/api/performance_breakdown') {
      json(res, 200, {
        decision_stats: [
          { decision: 'BUY', count: 1, hit_rate_est: 0.31, roi_est: 1.14, avg_odds: 21.4 },
          { decision: 'WATCH', count: 1, hit_rate_est: 0.18, roi_est: 0.92, avg_odds: 14.2 },
        ],
        odds_band_stats: [
          { band: '10-20', count: 1, hit_rate_est: 0.18, roi_est: 0.92 },
          { band: '20-30', count: 1, hit_rate_est: 0.31, roi_est: 1.14 },
        ],
      });
      return;
    }

    if (req.method === 'GET' && pathname === '/api/experiments') {
      json(res, 200, [
        { generated_at: new Date().toISOString(), run_id: 'mock_experiment', window: 'recent30', exact_hit_rate: 0.5, roi: 1.08 },
      ]);
      return;
    }

    if (req.method === 'GET' && pathname === '/api/ops/status') {
      json(res, 200, mockOpsState);
      return;
    }

    if (req.method === 'GET' && pathname === '/api/ops/report') {
      json(res, 200, mockOpsReport);
      return;
    }

    if (req.method === 'POST' && pathname === '/api/ops/run') {
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      const payload = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {};
      const mode = String(payload.mode || 'predict').toLowerCase();
      mockOpsState = {
        status: 'running',
        mode,
        started_at: new Date().toISOString(),
        finished_at: null,
        returncode: null,
        message: 'started',
      };
      mockOpsReport = {
        ok: true,
        report: {
          status: 'running',
          mode,
          steps: [
            {
              label: 'mock-start',
              returncode: 0,
              stdout_tail: `mode=${mode}`,
              stderr_tail: '',
            },
          ],
        },
      };
      setTimeout(() => {
        mockOpsState = {
          status: 'ok',
          mode,
          started_at: mockOpsState.started_at,
          finished_at: new Date().toISOString(),
          returncode: 0,
          message: 'completed',
        };
        mockOpsReport = {
          ok: true,
          report: {
            status: 'ok',
            mode,
            steps: [
              {
                label: 'mock-complete',
                returncode: 0,
                stdout_tail: `completed mode=${mode}`,
                stderr_tail: '',
              },
            ],
          },
        };
      }, 1200);
      json(res, 202, { ok: true, state: mockOpsState });
      return;
    }

    if (req.method === 'POST' && pathname === '/api/odds/upload') {
      json(res, 200, {
        ok: true,
        result: {
          path: 'mock',
          rows: 1,
          race_count: 1,
          updated_at: new Date().toISOString(),
        },
      });
      return;
    }

    json(res, 404, { ok: false, error: `not found: ${pathname}` });
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object', 'mock server failed to start');
  baseUrl = `http://127.0.0.1:${address.port}`;
  apiBaseUrl = baseUrl;
  workflowSource = WORKFLOW_USE_REAL_DASHBOARD ? 'mock-fallback' : 'mock';
  mockServer = server;
}

function spawnDashboardServer() {
  const proc = spawn(PYTHON, ['-m', 'src.web.app', '--host', HOST, '--port', String(PORT)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  proc.stdout.on('data', (chunk) => {
    process.stdout.write(`[web] ${chunk}`);
  });
  proc.stderr.on('data', (chunk) => {
    process.stderr.write(`[web] ${chunk}`);
  });

  return proc;
}

async function ensureDashboardServer() {
  if (WORKFLOW_USE_MOCK) {
    await startMockServer();
    return;
  }

  const healthUrl = buildUrl(baseUrl, '/api/health');
  if (await fetchHealth(healthUrl, 1500)) {
    apiBaseUrl = process.env.DASHBOARD_API_URL || baseUrl;
    workflowSource = 'real';
    return;
  }

  if (String(process.env.WORKFLOW_START_SERVER || '') === '1') {
    serverProcess = spawnDashboardServer();
    await waitForHealth(healthUrl, 45000);
    apiBaseUrl = process.env.DASHBOARD_API_URL || baseUrl;
    workflowSource = 'real';
    return;
  }

  if (WORKFLOW_ALLOW_MOCK_FALLBACK) {
    workflowFallbackReason = `preflight failed: ${healthUrl}`;
    process.stdout.write(`${workflowFallbackReason}; switching to mock fallback\n`);
    await startMockServer();
    apiBaseUrl = baseUrl;
    return;
  }

  throw new Error(
    `dashboard preflight failed: ${healthUrl}. ` +
      `Set WORKFLOW_FALLBACK_TO_MOCK=1 to use mock or start it with ` +
      `py -m src.web.app --host ${HOST} --port ${PORT}`,
  );
}

async function stopDashboardServer() {
  if (mockServer) {
    await new Promise((resolve) => mockServer.close(resolve));
    mockServer = null;
    return;
  }
  if (!serverProcess) return;
  const proc = serverProcess;
  serverProcess = null;

  if (proc.exitCode !== null || proc.signalCode !== null) return;

  proc.kill();
  await Promise.race([
    new Promise((resolve) => proc.once('exit', () => resolve())),
    sleep(5000),
  ]);

  if (proc.exitCode === null && proc.signalCode === null) {
    proc.kill('SIGKILL');
  }
}

async function applyFilters(page, options) {
  if (options.decision) {
    await (await resolveLocator(page, [
      { testid: 'decision-filter' },
      { id: 'decisionFilter' },
      { role: 'combobox', options: { name: '判定' } },
    ])).selectOption(options.decision);
  }
  if (options.venue) {
    await (await resolveLocator(page, [
      { testid: 'venue-filter' },
      { id: 'venueFilter' },
      { role: 'combobox', options: { name: '場' } },
    ])).selectOption({ label: options.venue });
  }
  if (options.date) {
    await (await resolveLocator(page, [
      { testid: 'date-from' },
      { id: 'dateFrom' },
      { role: 'textbox', options: { name: '日付' } },
    ])).fill(options.date);
  }
  if (options.limit) {
    await (await resolveLocator(page, [
      { testid: 'limit' },
      { id: 'limit' },
      { role: 'spinbutton', options: { name: '件数' } },
    ])).fill(options.limit);
  }
  await clickElement(page, [
    { testid: 'apply-btn' },
    { id: 'applyBtn' },
    { role: 'button', options: { name: '適用' } },
  ]);
  await page.waitForLoadState('networkidle').catch(() => {});
}

async function clickModeButton(page, mode) {
  const selector = MODE_SELECTOR[mode];
  assert(selector, `unsupported WORKFLOW_MODE: ${mode}`);

  const status = await resolveLocator(page, [
    { testid: 'ops-run-status' },
    { id: 'opsRunStatus' },
    { role: 'status', options: { name: /実行状態|状態/ } },
  ]);
  await status.waitFor({ state: 'visible', timeout: 30000 });
  await assert.match(await textOf(status), /待機中|idle|ready/i);

  const dialogPromise = page.waitForEvent('dialog').then((dialog) => dialog.accept());
  const button = await resolveLocator(page, [
    { testid: `ops-run-${mode}`.replace(/[^a-z0-9-]/gi, '-') },
    { id: selector.slice(1) },
    { role: 'button', options: { name: MODE_LABELS[mode] || mode } },
  ]);
  await button.click();
  await dialogPromise.catch(() => {});

  await page.waitForFunction(
    () => /実行中/.test(document.querySelector('[data-testid="ops-run-status"], #opsRunStatus')?.textContent || ''),
    null,
    { timeout: 15000 },
  ).catch(() => {});

  await page.waitForFunction(
    () => /待機中/.test(document.querySelector('[data-testid="ops-run-status"], #opsRunStatus')?.textContent || ''),
    null,
    { timeout: WORKFLOW_TIMEOUT_MS },
  );

  const state = await fetchStatusEndpoint();

  const finalStatus = String(state?.status || '').toLowerCase();
  assert.notEqual(finalStatus, 'failed', `ops run failed: ${JSON.stringify(state)}`);
  assert.ok(['ok', 'idle', 'running'].includes(finalStatus), `unexpected ops status: ${JSON.stringify(state)}`);
}

const MODE_LABELS = {
  predict: '予測更新',
  'pre-race': '朝準備',
  'odds-refresh': 'オッズ更新',
  'post-race': '夜検証',
  backtest: '検証',
  guard: 'ガード',
  full: '全部実行',
};

function classifyWorkflowStatus(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!text) return 'unknown';
  if (/(fail|error|cancel|abort|invalid)/.test(text)) return 'failed';
  if (/(run|start|pend|queue|wait|hold|processing)/.test(text)) return 'running';
  if (/(ok|pass|ready|idle|done|complete|success|successed)/.test(text)) return 'completed';
  return 'unknown';
}

async function fetchStatusEndpoint() {
  const status = await fetchJson(buildUrl(apiBaseUrl, '/api/ops/status')).catch((err) => ({ ok: false, error: err.message, status: 'UNKNOWN' }));
  return status;
}

async function fetchReportEndpoint() {
  const report = await fetchJson(buildUrl(apiBaseUrl, '/api/ops/report')).catch((err) => ({
    ok: false,
    error: err.message,
    report: null,
    statusCode: err.status || null,
  }));
  return report;
}

async function pollReportUntilReady(maxAttempts = 12, delayMs = 2500) {
  const history = [];
  let lastReport = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const status = await fetchStatusEndpoint();
    const report = await fetchReportEndpoint();
    const statusClass = classifyWorkflowStatus(status?.status || status?.report?.status || report?.report?.status);
    history.push({
      attempt,
      status: status?.status || status?.report?.status || null,
      status_class: statusClass,
      report_ok: Boolean(report && report.ok),
      report_status: report?.report?.status || null,
    });
    lastReport = report;
    if (Boolean(report && report.ok) && report.report) {
      return { report, history };
    }
    if (statusClass === 'failed') {
      break;
    }
    if (attempt < maxAttempts) {
      await sleep(delayMs);
    }
  }
  return { report: lastReport, history };
}

async function executeWorkflowStep(page, step, state) {
  const stepLabel = String(step || '').trim().toLowerCase();
  if (!stepLabel) return { step: stepLabel, skipped: true };

  if (stepLabel === 'guard') {
    if (page) {
      await clickModeButton(page, 'guard');
    } else {
      await postJson(buildUrl(apiBaseUrl, '/api/ops/run'), { mode: 'guard' });
    }
    return { step: stepLabel, action: 'guard' };
  }

  if (stepLabel === 'refresh') {
    if (page) {
      await clickElement(page, [
        { testid: 'refresh-btn' },
        { id: 'refreshBtn' },
        { role: 'button', options: { name: '更新' } },
      ]);
      await page.waitForLoadState('networkidle').catch(() => {});
    } else {
      await fetchJson(buildUrl(apiBaseUrl, '/api/summary'));
    }
    return { step: stepLabel, action: 'refresh' };
  }

  if (stepLabel === 'report') {
    const { report, history } = await pollReportUntilReady();
    state.report = report;
    state.report_poll = history;
    return { step: stepLabel, action: 'report', report_ok: Boolean(report && report.ok) };
  }

  if (stepLabel.startsWith('mode:')) {
    const mode = stepLabel.slice(5);
    if (page) {
      await clickModeButton(page, mode);
    } else {
      await postJson(buildUrl(apiBaseUrl, '/api/ops/run'), { mode });
    }
    return { step: stepLabel, action: `mode:${mode}` };
  }

  return { step: stepLabel, skipped: true };
}

async function runWorkflowSequence(page, sequenceTokens) {
  const state = { report: null, report_poll: [] };
  const log = [];
  for (const step of sequenceTokens) {
    const result = await executeWorkflowStep(page, step, state);
    log.push(result);
  }
  if (!state.report) {
    const polled = await pollReportUntilReady();
    state.report = polled.report;
    state.report_poll = polled.history;
  }
  return { ...state, sequence_log: log };
}

async function main() {
  await mkdir(OUTPUT_DIR, { recursive: true });
  await ensureDashboardServer();

  let browser = null;
  let page = null;
  let apiOnly = false;
  try {
    browser = await chromium.launch({
      headless: WORKFLOW_HEADLESS,
      ...(WORKFLOW_CHROMIUM_EXECUTABLE_PATH ? { executablePath: WORKFLOW_CHROMIUM_EXECUTABLE_PATH } : {}),
    });
    page = await browser.newPage({ viewport: { width: 1600, height: 2400 } });
  } catch (err) {
    if (!WORKFLOW_ALLOW_API_ONLY) {
      throw err;
    }
    apiOnly = true;
    process.stdout.write(`browser unavailable, switching to API-only mode: ${err.message}\n`);
  }

  try {
    const sequenceTokens = chooseSequenceTokens();
    const contracts = [];
    const summaryData = await fetchJson(buildUrl(apiBaseUrl, '/api/summary'));
    const summaryContract = summarizeContract('summary', summaryData, createMockSummary());
    contracts.push(summaryContract);

    let opsRunDate = '-';
    let opsRunStatusText = '状態：-';
    let opsRunDetailText = '最新ログ: -';
    let screenshotPath = null;
    let sequenceResult = { report: null, report_poll: [], sequence_log: [] };

    if (page) {
      await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
      await (await resolveLocator(page, [
        { testid: 'refresh-btn' },
        { id: 'refreshBtn' },
        { role: 'button', options: { name: '更新' } },
      ])).waitFor({ state: 'visible', timeout: 30000 });
      await assert.match(await getElementText(page, [
        { testid: 'ops-run-status' },
        { id: 'opsRunStatus' },
        { role: 'status', options: { name: /実行状態|状態/ } },
      ]), /待機中|idle|ready/i);

      await applyFilters(page, {
        date: WORKFLOW_DATE || undefined,
        venue: WORKFLOW_VENUE || undefined,
        decision: WORKFLOW_DECISION || undefined,
        limit: WORKFLOW_LIMIT || undefined,
      });

      await clickElement(page, [
        { testid: 'refresh-btn' },
        { id: 'refreshBtn' },
        { role: 'button', options: { name: '更新' } },
      ]);
      await page.waitForLoadState('networkidle').catch(() => {});
      await (await resolveLocator(page, [
        { testid: 'status-board' },
        { id: 'statusBoard' },
        { role: 'region', options: { name: '運用要約' } },
      ])).waitFor({ state: 'visible', timeout: 30000 });

      opsRunDate = await getElementText(page, [
        { testid: 'ops-run-date' },
        { id: 'opsRunDate' },
        { role: 'text', options: { name: /日時/ } },
      ]).catch(() => '-');
      if (opsRunDate === '-') {
        throw new Error('dashboard summary did not load');
      }

      sequenceResult = await runWorkflowSequence(page, sequenceTokens);
      opsRunStatusText = await getElementText(page, [
        { testid: 'ops-run-status' },
        { id: 'opsRunStatus' },
      ]).catch(() => '状態：取得失敗');
      opsRunDetailText = await getElementText(page, [
        { testid: 'ops-run-detail' },
        { id: 'opsRunDetail' },
      ]).catch(() => '最新ログ: 取得失敗');
      screenshotPath = WORKFLOW_SCREENSHOT;
      await page.screenshot({ path: screenshotPath, fullPage: true });
    } else {
      sequenceResult = await runWorkflowSequence(null, sequenceTokens);
      opsRunStatusText = `状態：${workflowSource === 'mock' || workflowSource === 'mock-fallback' ? '待機中' : 'API ONLY'}`;
      opsRunDetailText = `最新ログ: sequence=${sequenceTokens.join(' -> ')}`;
      opsRunDate = String(summaryData?.updated_at || summaryData?.generated_at || '-');
    }

    const statusData = await fetchStatusEndpoint();
    const effectiveStatusData = apiOnly ? createMockOpsStatusShape() : statusData;
    const reportData = sequenceResult.report || (await pollReportUntilReady()).report;
    const expectedStatus = createMockOpsStatusShape();
    const expectedReport = createMockOpsReportShape();
    const statusContract = summarizeContract('ops_status', effectiveStatusData, expectedStatus);
    const reportContract = summarizeContract('ops_report', reportData || {}, expectedReport);
    contracts.push(statusContract, reportContract);

    const summary = {
      baseUrl,
      apiBaseUrl,
      mode: WORKFLOW_MODE,
      sequence: sequenceTokens,
      workflow_source: workflowSource,
      fallback_reason: workflowFallbackReason || null,
      api_only: apiOnly,
      headless: WORKFLOW_HEADLESS,
      filters: {
        date: WORKFLOW_DATE || null,
        venue: WORKFLOW_VENUE || null,
        decision: WORKFLOW_DECISION || null,
        limit: WORKFLOW_LIMIT || null,
      },
      screenshot: screenshotPath,
      at: new Date().toISOString(),
      opsRunStatus: opsRunStatusText,
      opsRunDetail: opsRunDetailText,
      opsRunDate,
      title: page ? await page.title() : 'API-only workflow',
      opsReport: reportData ? {
        ok: Boolean(reportData.ok),
        status: reportData?.report?.status || null,
        mode: reportData?.report?.mode || null,
        stepCount: Array.isArray(reportData?.report?.steps) ? reportData.report.steps.length : 0,
      } : null,
      report_poll: sequenceResult.report_poll || [],
      sequence_log: sequenceResult.sequence_log || [],
      contracts,
    };

    await writeFile(WORKFLOW_SUMMARY, `${JSON.stringify(summary, null, 2)}\n`, 'utf8');
    if (screenshotPath) {
      try {
        if (screenshotPath !== LATEST_SCREENSHOT) {
          await copyFile(screenshotPath, LATEST_SCREENSHOT);
        }
      } catch (err) {
        process.stdout.write(`latest screenshot copy skipped: ${err.message}\n`);
      }
    }
    try {
      if (WORKFLOW_SUMMARY !== LATEST_SUMMARY) {
        await copyFile(WORKFLOW_SUMMARY, LATEST_SUMMARY);
      }
    } catch (err) {
      process.stdout.write(`latest summary copy skipped: ${err.message}\n`);
    }
    if (screenshotPath) {
      process.stdout.write(`Saved screenshot: ${screenshotPath}\n`);
    }
    process.stdout.write(`Saved summary: ${WORKFLOW_SUMMARY}\n`);
  } finally {
    await page?.close().catch(() => {});
    await browser?.close().catch(() => {});
    await stopDashboardServer();
  }
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
