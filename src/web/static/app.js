const kpiCards = document.getElementById("kpiCards");
const statusBoard = document.getElementById("statusBoard");
const spotlightCards = document.getElementById("spotlightCards");
const raceYosouBody = document.getElementById("raceYosouBody");
const raceYosouMeta = document.getElementById("raceYosouMeta");
const predBody = document.querySelector("#predTable tbody");
const expBody = document.querySelector("#expTable tbody");
const heroHit = document.getElementById("heroHit");
const heroHitMeta = document.getElementById("heroHitMeta");
const heroRoi = document.getElementById("heroRoi");
const heroRoiMeta = document.getElementById("heroRoiMeta");
const heroBuyCount = document.getElementById("heroBuyCount");
const heroWatchCount = document.getElementById("heroWatchCount");
const heroOddsRate = document.getElementById("heroOddsRate");
const opsRunDate = document.getElementById("opsRunDate");
const modeBanner = document.getElementById("modeBanner");
const gateHealthPanel = document.getElementById("gateHealthPanel");
const opsHealthPanel = document.getElementById("opsHealthPanel");
const upstreamHealthPanel = document.getElementById("upstreamHealthPanel");
const modeNormalBtn = document.getElementById("modeNormalBtn");
const modeWinrateBtn = document.getElementById("modeWinrateBtn");
const modeRoiFilterBtn = document.getElementById("modeRoiFilterBtn");
const modeAutoFilterBtn = document.getElementById("modeAutoFilterBtn");
const opsRunStatus = document.getElementById("opsRunStatus");
const opsRunDetail = document.getElementById("opsRunDetail");
const runPredictBtn = document.getElementById("runPredictBtn");
const runPreRaceBtn = document.getElementById("runPreRaceBtn");
const runOddsBtn = document.getElementById("runOddsBtn");
const runPostRaceBtn = document.getElementById("runPostRaceBtn");
const runBacktestBtn = document.getElementById("runBacktestBtn");
const runGuardBtn = document.getElementById("runGuardBtn");
const runFullBtn = document.getElementById("runFullBtn");
const oddsUploadInput = document.getElementById("oddsUploadInput");
const oddsUploadBtn = document.getElementById("oddsUploadBtn");
const opsRunButtons = [
  runPredictBtn,
  runPreRaceBtn,
  runOddsBtn,
  runPostRaceBtn,
  runBacktestBtn,
  runGuardBtn,
  runFullBtn,
].filter(Boolean);
const contextSummary = document.getElementById("contextSummary");
const venueFilter = document.getElementById("venueFilter");
const decisionFilter = document.getElementById("decisionFilter");
const dateFrom = document.getElementById("dateFrom");
const todayBtn = document.getElementById("todayBtn");
const limitInput = document.getElementById("limit");
const applyBtn = document.getElementById("applyBtn");
const refreshBtn = document.getElementById("refreshBtn");
const predictionFreshnessBanner = document.getElementById("predictionFreshnessBanner");
const venueWindow = document.getElementById("venueWindow");
const venueSummaryBody = document.getElementById("venueSummaryBody");
const decisionPerfBody = document.getElementById("decisionPerfBody");
const oddsBandBody = document.getElementById("oddsBandBody");
let filterToday = true;
let usedTodayFallback = false;
let lastSummary = null;
let preferredStrategyMode = localStorage.getItem("preferredStrategyMode") || "";
let opsPollTimer = null;
let opsLastState = { status: "idle", mode: null, message: "ready" };
let raceYosouActiveRaceNo = null;
let raceYosouAutoScrolled = false;

/**
 * @typedef {Object} RaceYosouBoat
 * @property {number} lane
 * @property {string} label
 * @property {number|null} avgSt
 * @property {number|null} nationalWinRate
 * @property {number|null} national2RenRate
 * @property {number|null} local2RenRate
 * @property {number|null} motor2RenRate
 * @property {number|null} boat2RenRate
 * @property {number|null} raceNo
 * @property {number|null} exhibitionTime
 * @property {number|null} exhibitionTimeRank
 * @property {number|null} startTiming
 * @property {boolean} insideCourseFlag
 * @property {number|null} laneWinRatePrior
 * @property {boolean} lowMotorFlag
 * @property {boolean} lowBoatFlag
 * @property {boolean} jcdLowMotorFlag
 * @property {boolean} jcdLowBoatFlag
 * @property {number|null} national2RenRank
 * @property {number|null} local2RenRank
 * @property {number|null} avgStRank
 * @property {number|null} avgStAdvantage
 */

/**
 * @typedef {Object} RaceYosouPrediction
 * @property {number} rank
 * @property {number|null} lane
 * @property {string} label
 * @property {number|null} winProbaRaw
 * @property {number|null} winProbaNorm
 * @property {string|undefined} trifecta
 * @property {number|null} firstLane
 * @property {number|null} secondLane
 * @property {number|null} thirdLane
 * @property {number|null} approxProb
 * @property {number|null} approxProbRaw
 * @property {number|null} mainScore
 * @property {number|null} winScoreScaled
 * @property {number|null} placeScoreScaled
 * @property {boolean|undefined} conditionalMode
 * @property {number|string|null} [stake]
 * @property {string|null} [stopReason]
 * @property {string|null} [stop_reason]
 * @property {number|string|null} [bet_amount]
 * @property {number|string|null} [betAmount]
 */

/**
 * @typedef {Object} RaceYosouRace
 * @property {string} raceId
 * @property {number|null} raceNo
 * @property {string} date
 * @property {string} dateLabel
 * @property {string} venue
 * @property {string} venueLabel
 * @property {string} jcd
 * @property {string} statusLabel
 * @property {{closed: boolean, exhibitionMissing: boolean, reporterMissing: boolean}} statusFlags
 * @property {string[]|undefined} [warnings]
 * @property {string|undefined} [dataStatus]
 * @property {RaceYosouBoat[]} boats
 * @property {RaceYosouPrediction[]} aiPredictions
 * @property {RaceYosouPrediction[]} reporterPredictions
 */

/**
 * @typedef {Object} RaceYosouViewModel
 * @property {string} date
 * @property {string} date_label
 * @property {string} venue
 * @property {string} venue_label
 * @property {string|undefined} [event]
 * @property {string|undefined} [generatedAt]
 * @property {string|undefined} [dataStatus]
 * @property {string[]|undefined} [warnings]
 * @property {RaceYosouRace[]} races
 * @property {{features?: number, win_proba?: number, trifecta_candidates?: number}} source_counts
 */

const JCD_TO_VENUE = {
  "01": "桐生",
  "02": "戸田",
  "03": "江戸川",
  "04": "平和島",
  "05": "多摩川",
  "06": "浜名湖",
  "07": "蒲郡",
  "08": "常滑",
  "09": "津",
  "10": "三国",
  "11": "びわこ",
  "12": "住之江",
  "13": "尼崎",
  "14": "鳴門",
  "15": "丸亀",
  "16": "児島",
  "17": "宮島",
  "18": "徳山",
  "19": "下関",
  "20": "若松",
  "21": "芦屋",
  "22": "福岡",
  "23": "唐津",
  "24": "大村",
};

function fmt(v, digits = 4) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toFixed(digits);
}

function fmtYen(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `${Number(v).toLocaleString("ja-JP", { maximumFractionDigits: 0 })}円`;
}

function fmtOrNA(v, digits = 4) {
  if (v === null || v === undefined || Number.isNaN(v)) return "データなし";
  return Number(v).toFixed(digits);
}

function fmtPct(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return "データなし";
  return `${fmt(Number(v) * 100, digits)}%`;
}

function fmtIsoDateTime(value) {
  if (!value) return "-";
  const text = String(value);
  if (!text) return "-";
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return text;
  return dt.toLocaleString("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtIsoDateParts(value) {
  if (!value) return { date: "-", time: "-" };
  const text = String(value);
  if (!text) return { date: "-", time: "-" };
  const dt = new Date(text);
  if (Number.isNaN(dt.getTime())) return { date: text, time: "-" };
  return {
    date: dt.toLocaleDateString("ja-JP", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }),
    time: dt.toLocaleTimeString("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
    }),
  };
}

function modeLabel(mode) {
  const key = String(mode || "").toUpperCase();
  if (key === "AUTO_FILTER") return "自動条件";
  if (key === "ROI_FILTER") return "ROIフィルタ";
  if (key === "WINRATE") return "勝率優先";
  return "通常";
}

function dayModeLabel(mode) {
  const key = String(mode || "").toLowerCase();
  if (key === "stop") return "停止";
  if (key === "reduced") return "縮小";
  if (key === "normal") return "通常";
  return "未判定";
}

function summarizeOperationalState(summary) {
  const ops = summary?.ops_health || {};
  const gate = summary?.gate_health || {};
  const pipeline = ops.pipeline || {};
  const guard = ops.guard || {};
  const compare = ops.compare || {};
  const reasonCounts = gate.reason_keyword_counts || {};
  const missing = gate.missing_breakdown || {};
  const recent30 = summary?.recent30_trifecta || {};
  const effectiveMode = modeLabel(summary?.effective_strategy_mode || summary?.strategy_mode);
  const pipelineStatus = String(pipeline.status || "unknown").toUpperCase();
  const guardStatus = String(guard.status || "UNKNOWN").toUpperCase();
  const compareStatus = String(compare.status || "UNKNOWN").toUpperCase();
  const dayMode = String(summary?.day_mode || "").toLowerCase();
  const dayModeMetrics = summary?.day_mode_metrics || {};
  const dayModeNotes = [
    dayModeMetrics.real_odds_available_rate != null ? `実オッズ ${fmt(Number(dayModeMetrics.real_odds_available_rate) * 100, 1)}%` : "",
    dayModeMetrics.missing_feature_rate != null ? `欠損 ${fmt(Number(dayModeMetrics.missing_feature_rate) * 100, 1)}%` : "",
    dayModeMetrics.today_races != null ? `本日 ${fmt(Number(dayModeMetrics.today_races), 0)}R` : "",
    dayModeMetrics.predicted_race_count != null ? `予測 ${fmt(Number(dayModeMetrics.predicted_race_count), 0)}R` : "",
    dayModeMetrics.race_coverage != null ? `網羅 ${fmt(Number(dayModeMetrics.race_coverage) * 100, 1)}%` : "",
  ].filter(Boolean).join(" / ");
  const latestRefresh = summary?.latest_refresh || summary?.updated_at || "";
  const latestGuard = guard.generated_at || "";
  const latestErrorReason = summary?.latest_error_reason || (compare.reasons || [])[0] || (guard.reasons || [])[0] || "";

  let bottleneck = "大きな詰まりは見当たりません";
  if (Number(reasonCounts.real_odds_missing || 0) > 0) {
    bottleneck = "実オッズ未取得";
  }
  else if (Number(missing.both_missing || 0) > 0) {
    bottleneck = "前提情報の不足";
  }
  else if (compareStatus !== "PASS") {
    bottleneck = `候補比較 ${compareStatus}`;
  }
  else if (guardStatus !== "PASS") {
    bottleneck = `採用ガード ${guardStatus}`;
  }

  const executed = [
    pipeline.last_step ? `最終 ${pipeline.last_step}` : "",
    pipeline.mode ? `モード ${pipeline.mode}` : "",
    pipelineStatus !== "UNKNOWN" ? `状態 ${pipelineStatus}` : "",
  ].filter(Boolean).join(" / ") || "実行履歴なし";

  const result = recent30?.hit_rate == null
    ? "直近30R の結果はまだありません"
    : `直近30R 的中見込み ${fmt(Number(recent30.hit_rate) * 100, 1)}% / 参考ROI ${recent30.roi == null ? "-" : fmt(Number(recent30.roi) * 100, 1)}%`;

  let nextAction = "次の TARGET 日で再現確認";
  if (summary?.prediction_staleness_days != null && Number(summary.prediction_staleness_days) > 0) {
    nextAction = "pre-race を再生成";
  }
  else if (Number(reasonCounts.real_odds_missing || 0) > 0) {
    nextAction = "実オッズの再取得";
  }
  else if (compareStatus !== "PASS") {
    nextAction = "compare 前提を再確認";
  }

  return [
    {
      label: "現在の状態",
      value: `${dayModeLabel(dayMode)}`,
      note: dayModeNotes || `戦略 ${effectiveMode} / パイプライン ${pipelineStatus}`,
      tone: dayMode === "normal" ? "success" : (dayMode === "reduced" || dayMode === "stop" ? "warning" : "neutral"),
    },
    {
      label: "最新更新",
      value: latestRefresh ? fmtIsoDateTime(latestRefresh) : "-",
      note: summary?.latest_source_date ? `元データ ${summary.latest_source_date}` : "refresh 情報なし",
      tone: "neutral",
    },
    {
      label: "最新ガード",
      value: guardStatus,
      note: latestGuard ? fmtIsoDateTime(latestGuard) : "generated_at なし",
      tone: "neutral",
    },
    {
      label: "最新エラー",
      value: latestErrorReason ? shortReason(latestErrorReason) : "なし",
      note: latestErrorReason ? "直近の失敗理由" : "エラーなし",
      tone: "neutral",
    },
    {
      label: "最大の詰まり",
      value: bottleneck,
      note: `ガード ${guardStatus}`,
      tone: bottleneck.includes("不足") || bottleneck.includes("未取得") ? "warning" : "neutral",
    },
    {
      label: "実行内容",
      value: executed,
      note: summary?.ops_health?.pipeline?.message || "最新パイプライン状態",
      tone: "neutral",
    },
    {
      label: "結果",
      value: result,
      note: `購入候補 ${summary?.buy_count ?? 0} / 様子見 ${summary?.watch_count ?? 0} / 見送り ${summary?.skip_count ?? 0}`,
      tone: (summary?.buy_count ?? 0) > 0 ? "success" : "warning",
    },
    {
      label: "次の一手",
      value: nextAction,
      note: summary?.latest_prediction_date ? `予測 ${summary.latest_prediction_date}` : "次の候補を確認",
      tone: nextAction.includes("再取得") || nextAction.includes("再生成") ? "warning" : "neutral",
    },
  ];
}

function renderStatusBoard(summary) {
  if (!statusBoard) return;
  const cards = summarizeOperationalState(summary || {});
  statusBoard.innerHTML = cards.map((card) => `
    <article class="status-card ${card.tone}">
      <div class="status-label">${esc(card.label)}</div>
      <div class="status-value">${esc(card.value)}</div>
      <div class="status-note">${esc(card.note)}</div>
    </article>
  `).join("");
}

function syncModeUi(summary) {
  const currentMode = String(summary?.strategy_mode || "NORMAL").toUpperCase();
  const effectiveMode = String(summary?.effective_strategy_mode || currentMode).toUpperCase();
  const preferred = String(preferredStrategyMode || currentMode).toUpperCase();
  if (modeBanner) {
    modeBanner.textContent = effectiveMode !== currentMode
      ? `運用状態：${modeLabel(currentMode)} / 実効：${modeLabel(effectiveMode)}`
      : `運用状態：${modeLabel(currentMode)}`;
    modeBanner.title = preferred !== currentMode
      ? `表示設定は ${modeLabel(preferred)} です`
      : `表示設定と一致しています`;
  }
  if (modeNormalBtn) modeNormalBtn.classList.toggle("active", preferred === "NORMAL");
  if (modeWinrateBtn) modeWinrateBtn.classList.toggle("active", preferred === "WINRATE");
  if (modeRoiFilterBtn) modeRoiFilterBtn.classList.toggle("active", preferred === "ROI_FILTER");
  if (modeAutoFilterBtn) modeAutoFilterBtn.classList.toggle("active", preferred === "AUTO_FILTER");
}

function setPreferredStrategyMode(mode, summary = lastSummary) {
  preferredStrategyMode = String(mode || "NORMAL").toUpperCase();
  localStorage.setItem("preferredStrategyMode", preferredStrategyMode);
  syncModeUi(summary || {});
}

function pickProbabilityRow(row) {
  return {
    firstPlaceProb: Number.isFinite(Number(row.first_place_prob)) ? Number(row.first_place_prob) : row.first_win_proba,
    hitProb: Number.isFinite(Number(row.calibrated_hit_prob)) ? Number(row.calibrated_hit_prob) : row.approx_prob,
  };
}

function formatYmd(yyyymmdd) {
  const s = String(yyyymmdd || "");
  if (!/^\d{8}$/.test(s)) return s;
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}

function formatKyoteiCombo(combo) {
  const text = normalizeComboText(combo);
  if (!text) return "-";
  const pieces = [];
  for (let c of text) {
    if (["1", "2", "3", "4", "5", "6"].includes(c)) {
      pieces.push(`<span class="boat-n boat-${c}">${c}</span>`);
    } else {
      pieces.push(`<span class="boat-separator">${c}</span>`);
    }
  }
  return `<div class="boat-combo-wrap">${pieces.join("")}</div>`;
}

function comboPlainText(combo) {
  return normalizeComboText(combo) || "-";
}

function formatRunName(runId) {
  const raw = String(runId || "").trim();
  if (!raw) return "実験";
  let s = raw.replaceAll("_", " ");
  s = s.replace(/\b(\d{8})\b/g, (_, ymd) => formatYmd(ymd));
  s = s.replace(/\bfinalfix\b/gi, "本番調整");
  s = s.replace(/\bimprovement\b/gi, "改善");
  s = s.replace(/\bexacta\b/gi, "2連単");
  s = s.replace(/\btrifecta\b/gi, "3連単");
  s = s.replace(/\brecent30\b/gi, "直近30R");
    s = s.replace(/\ball\b/gi, "すべて");
  s = s.replace(/\s+/g, " ").trim();
  return s || "実験";
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function safeObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function safeArray(value) {
  return Array.isArray(value) ? value : [];
}

function statusText(value) {
  if (typeof value === "string") return value || "missing";
  const data = safeObject(value);
  if (!Object.keys(data).length) return "missing";
  const keys = ["racelist", "odds3t", "beforeinfo", "result"];
  const values = keys.map((key) => String(data[key] || "").toLowerCase());
  if (values.some((v) => ["ok", "available", "ready"].includes(v))) return "available";
  if (values.some((v) => v === "pending")) return "pending";
  if (values.some((v) => v === "unavailable")) return "unavailable";
  return values.find(Boolean) || "missing";
}

function normalizeComboText(combo) {
  if (Array.isArray(combo)) {
    return combo.map((item) => String(item).trim()).filter(Boolean).join("-");
  }
  if (combo && typeof combo === "object") {
    return normalizeComboText(combo.combo || combo.trifecta || combo.buy_combo || combo.label || "");
  }
  const text = String(combo ?? "").trim();
  if (!text) return "";
  const normalized = text.replace(/[^\d\-]/g, "");
  if (/^\d-\d-\d$/.test(normalized)) return normalized;
  return text;
}

function venueLabel(jcd) {
  const code = String(jcd || "").padStart(2, "0");
  return JCD_TO_VENUE[code] || code || "-";
}

function deriveVenueFromRaceId(raceId) {
  const text = String(raceId || "").trim();
  if (!text.includes("-")) return "";
  const parts = text.split("-").filter(Boolean);
  if (parts.length < 2) return "";
  const mid = parts[1];
  if (/^\d{1,2}$/.test(mid)) return venueLabel(mid);
  const prefix = mid.slice(0, 1).toUpperCase();
  if (prefix === "B") return "大村";
  if (prefix === "K") return "唐津";
  if (prefix === "S") return "下関";
  return "";
}

function displayVenueName(row) {
  return row.venue_name || deriveVenueFromRaceId(row.race_id) || venueLabel(row.jcd) || "開催不明";
}

function displayRaceLabel(row) {
  if (row.venue_race_label) return row.venue_race_label;
  const venue = displayVenueName(row);
  const raceNo = Number(row.race_no);
  if (Number.isFinite(raceNo) && raceNo > 0) return `${venue} ${raceNo}R`;
  const text = String(row.race_id || "");
  const parts = text.split("-").filter(Boolean);
  const tail = Number(parts[parts.length - 1]);
  if (Number.isFinite(tail) && tail > 0) return `${venue} ${tail}R`;
  return venue;
}

function shortReason(reason) {
  const text = String(reason || "").replaceAll(" / ", " ・ ");
  return text.length > 68 ? `${text.slice(0, 66)}…` : text;
}

function reasonTags(reason) {
  return String(reason || "")
    .split(" / ")
    .filter(Boolean)
    .slice(0, 3)
    .map((part) => {
      let cls = "tag-neutral";
      if (
        part.includes("未取得") ||
        part.includes("欠損") ||
        part.includes("見送り") ||
        part.includes("低") ||
        part.includes("ブレ大")
      ) {
        cls = "tag-warn";
      }
      else if (part.includes("EV")) cls = "tag-ev";
      else if (part.includes("オッズ")) cls = "tag-odds";
      return `<span class="reason-tag ${cls}">${esc(part)}</span>`;
    })
    .join("");
}

function decisionPriority(decision) {
  const key = String(decision || "").toUpperCase();
  if (key === "BUY") return 0;
  if (key === "WATCH") return 1;
  if (key === "SKIP") return 2;
  return 3;
}

function sortPredictionsLatestFirst(a, b) {
  const dateA = Date.parse(a.date || "");
  const dateB = Date.parse(b.date || "");
  const safeDateA = Number.isNaN(dateA) ? -Infinity : dateA;
  const safeDateB = Number.isNaN(dateB) ? -Infinity : dateB;
  if (safeDateA !== safeDateB) return safeDateB - safeDateA;

  const priA = decisionPriority(a.decision);
  const priB = decisionPriority(b.decision);
  if (priA !== priB) return priA - priB;

  const scoreA = Number.isFinite(Number(a.decision_score)) ? Number(a.decision_score) : -Infinity;
  const scoreB = Number.isFinite(Number(b.decision_score)) ? Number(b.decision_score) : -Infinity;
  if (scoreA !== scoreB) return scoreB - scoreA;

  const fpA = Number.isFinite(Number(a.first_place_score)) ? Number(a.first_place_score) : -Infinity;
  const fpB = Number.isFinite(Number(b.first_place_score)) ? Number(b.first_place_score) : -Infinity;
  if (fpA !== fpB) return fpB - fpA;

  const spA = Number.isFinite(Number(a.second_place_score)) ? Number(a.second_place_score) : -Infinity;
  const spB = Number.isFinite(Number(b.second_place_score)) ? Number(b.second_place_score) : -Infinity;
  if (spA !== spB) return spB - spA;

  const tpA = Number.isFinite(Number(a.third_place_score)) ? Number(a.third_place_score) : -Infinity;
  const tpB = Number.isFinite(Number(b.third_place_score)) ? Number(b.third_place_score) : -Infinity;
  if (tpA !== tpB) return tpB - tpA;

  const seqA = Number.isFinite(Number(a.race_seq)) ? Number(a.race_seq) : null;
  const seqB = Number.isFinite(Number(b.race_seq)) ? Number(b.race_seq) : null;
  if (seqA !== null && seqB !== null && seqA !== seqB) return seqB - seqA;

  const raceA = Number.isFinite(Number(a.race_no)) ? Number(a.race_no) : null;
  const raceB = Number.isFinite(Number(b.race_no)) ? Number(b.race_no) : null;
  if (raceA !== null && raceB !== null && raceA !== raceB) return raceB - raceA;

  const evA = Number.isFinite(a.ev) ? a.ev : -Infinity;
  const evB = Number.isFinite(b.ev) ? b.ev : -Infinity;
  if (evA !== evB) return evB - evA;

  return String(b.race_id || "").localeCompare(String(a.race_id || ""));
}

async function getJson(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function postJson(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
    cache: "no-store",
  });
  if (!r.ok) {
    let detail = "";
    try {
      detail = (await r.json())?.error || "";
    } catch (_) {
      detail = "";
    }
    throw new Error(`HTTP ${r.status}${detail ? `: ${detail}` : ""}`);
  }
  return r.json();
}

async function uploadOddsCsv(file) {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch("/api/odds/upload", {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  const payload = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
  if (!r.ok || !payload?.ok) {
    throw new Error(payload?.error || `HTTP ${r.status}`);
  }
  return payload;
}

function updateOpsRunStatus(state) {
  if (!opsRunStatus) return;
  opsLastState = {
    status: String(state?.status || "idle"),
    mode: state?.mode || null,
    message: state?.message || "ready",
  };
  const status = String(state?.status || "idle").toUpperCase();
  const mode = String(state?.mode || "-");
  const msg = String(state?.message || "");
  const running = status === "RUNNING";
  opsRunStatus.textContent = `状態：${running ? "実行中" : "待機中"}`;
  opsRunStatus.title = msg ? `最新ログ: ${msg}` : "自動実行の状態";
  opsRunStatus.classList.toggle("active", status === "RUNNING");
  opsRunStatus.classList.toggle("running", running);
  for (const btn of opsRunButtons) {
    btn.disabled = running;
    btn.setAttribute("aria-disabled", running ? "true" : "false");
  }
  if (oddsUploadBtn) {
    oddsUploadBtn.disabled = running;
    oddsUploadBtn.setAttribute("aria-disabled", running ? "true" : "false");
  }
  if (oddsUploadInput) oddsUploadInput.disabled = running;
}

function updateOpsRunDetail(report) {
  if (!opsRunDetail) return;
  const r = report?.report || {};
  const status = String(r.status || "-").toUpperCase();
  const mode = String(r.mode || "-");
  const steps = Array.isArray(r.steps) ? r.steps : [];
  const last = steps.length ? steps[steps.length - 1] : null;
  if (!last) {
    opsRunDetail.textContent = `最新ログ: mode=${mode} / status=${status} / step=-`;
    return;
  }
  const label = String(last.label || "-");
  const code = Number.isFinite(Number(last.returncode)) ? Number(last.returncode) : "-";
  const out = String(last.stdout_tail || "").trim();
  const err = String(last.stderr_tail || "").trim();
  const outLine = out ? out.split(/\r?\n/).filter(Boolean).slice(-1)[0] : "";
  const errLine = err ? err.split(/\r?\n/).filter(Boolean).slice(-1)[0] : "";
  if (status === "FAILED" && errLine) {
    opsRunDetail.textContent = `最新ログ: mode=${mode} / status=${status} / step=${label} / rc=${code} / err=${errLine.slice(0, 140)}`;
    return;
  }
  opsRunDetail.textContent = `最新ログ: mode=${mode} / status=${status} / step=${label} / rc=${code}${outLine ? ` / ${outLine.slice(0, 120)}` : ""}`;
}

async function refreshOpsRunReport() {
  try {
    const report = await getJson("/api/ops/report");
    updateOpsRunDetail(report);
  } catch (_) {
    if (opsRunDetail) opsRunDetail.textContent = "最新ログ: まだ実行レポートがありません";
  }
}

async function refreshOpsRunStatus() {
  try {
    const state = await getJson("/api/ops/status");
    updateOpsRunStatus(state);
    const running = String(state?.status || "").toLowerCase() === "running";
    if (running && !opsPollTimer) {
      opsPollTimer = setInterval(async () => {
        try {
          const next = await getJson("/api/ops/status");
          updateOpsRunStatus(next);
          const stillRunning = String(next?.status || "").toLowerCase() === "running";
          if (!stillRunning && opsPollTimer) {
            clearInterval(opsPollTimer);
            opsPollTimer = null;
            await refreshOpsRunReport();
            await loadAll();
          }
        } catch (_) {}
      }, 3000);
    }
    if (!running && opsPollTimer) {
      clearInterval(opsPollTimer);
      opsPollTimer = null;
    }
  } catch (e) {
    if (opsRunStatus) opsRunStatus.textContent = `実行状態: 取得失敗 (${e.message})`;
  }
}

async function runOpsMode(mode) {
  const labelMap = {
    predict: "予測更新",
    "pre-race": "朝準備",
    "odds-refresh": "オッズ更新",
    "post-race": "夜検証",
    backtest: "検証",
    guard: "ガード",
    full: "全部実行",
  };
  const label = labelMap[mode] || mode;
  const ok = window.confirm(`${label}を実行します。続けますか？`);
  if (!ok) return;
  try {
    const res = await postJson("/api/ops/run", { mode });
    updateOpsRunStatus(res?.state || { status: "running", mode, message: "started" });
    await refreshOpsRunStatus();
  } catch (e) {
    alert(`実行できませんでした: ${e.message}`);
    await refreshOpsRunStatus();
  }
}

function getJstYmd(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${map.year}-${map.month}-${map.day}`;
}

function getTodayIso() {
  return getJstYmd();
}

function formatJstWeekdayLabel(dateText) {
  if (!dateText) return "";
  const parts = String(dateText).split("-").map((value) => Number(value));
  if (parts.length !== 3 || parts.some((value) => !Number.isFinite(value))) return "";
  const [year, month, day] = parts;
  const dt = new Date(Date.UTC(year, month - 1, day, 0, 0, 0));
  if (Number.isNaN(dt.getTime())) return "";
  return new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo",
    weekday: "short",
  }).format(dt);
}

function getDefaultDate(rows) {
  const today = getTodayIso();
  const dates = [...new Set((rows || []).map((r) => String(r.date || "")).filter(Boolean))].sort();
  if (dates.includes(today)) return today;
  return dates.length ? dates[dates.length - 1] : today;
}

async function applyTodayOrLatestDate() {
  dateFrom.value = getTodayIso();
  usedTodayFallback = false;
}

function renderKpi(summary) {
  const recent30 = summary.recent30_trifecta || {};
  const recent30Exacta = summary.recent30_exacta || {};
  const decisionCounts = summary.decision_counts || {};
  const buyCount = summary.buy_count ?? 0;
  const watchCount = summary.watch_count ?? 0;
  const skipCount = summary.skip_count ?? 0;
  const targetRaces = summary.target_races ?? summary.predictions_total ?? 0;
  const resultAvailable = summary.result_available_races ?? 0;
  const realOddsAvailable = summary.real_odds_available_races ?? 0;
  const pendingUnpublished = summary.pending_unpublished_races ?? 0;
  const realOddsRate = Number.isFinite(summary.real_odds_rate) ? summary.real_odds_rate : summary.real_odds_coverage;
  const hitRate = Number.isFinite(recent30.hit_rate) ? Number(recent30.hit_rate) : null;
  const n = Number.isFinite(recent30.buy) ? Number(recent30.buy) : 0;
  const hits = Number.isFinite(recent30.hits) ? Number(recent30.hits) : (hitRate == null ? null : Math.round(hitRate * n));
  const ciLow = Array.isArray(recent30.hit_rate_ci) ? recent30.hit_rate_ci[0] : null;
  const ciHigh = Array.isArray(recent30.hit_rate_ci) ? recent30.hit_rate_ci[1] : null;
  const confidence = recent30.confidence || "-";
  const windowRaces = Number.isFinite(recent30.window_races) ? Number(recent30.window_races) : 0;
  const windowDays = Number.isFinite(recent30.window_days) ? Number(recent30.window_days) : 0;
  const sampleNote = recent30.sample_note || "";
  const currentMode = modeLabel(summary.strategy_mode);
  const effectiveMode = modeLabel(summary.effective_strategy_mode || summary.strategy_mode);
  const skipReasons = Array.isArray(summary.top_skip_reasons) ? summary.top_skip_reasons : [];
  const skipReasonsText = skipReasons.length
    ? skipReasons.slice(0, 3).map((item) => `${shortReason(item.reason)}:${Number(item.count || 0)}`).join(" / ")
    : "なし";

  heroHit.textContent = hitRate == null ? "-" : `${fmt(hitRate * 100, 1)}%`;
  heroHitMeta.textContent = hitRate == null
    ? `直近${windowDays || 30}日 / 対象${windowRaces}R / BUY ${n}件 / データなし`
    : `直近${windowDays || 30}日 / 対象${windowRaces}R / ${hits}/${n}件的中 / 95%CI ${fmt(ciLow * 100, 1)}-${fmt(ciHigh * 100, 1)}% / 信頼度${confidence}${sampleNote ? ` / ${sampleNote}` : ""}`;
  heroRoi.textContent = recent30.roi == null ? "-" : `${fmt(Number(recent30.roi) * 100, 1)}%`;
  heroRoiMeta.textContent = recent30.roi == null
    ? `直近${windowDays || 30}日 / 対象${windowRaces}R / 参考値`
    : `直近${windowDays || 30}日 / 対象${windowRaces}R / BUY ${n}件の参考値`;
  heroBuyCount.textContent = String(buyCount);
  heroWatchCount.textContent = String(watchCount);
  heroOddsRate.textContent = realOddsRate == null ? "-" : `${fmt(realOddsRate * 100, 1)}%`;
  if (opsRunDate) {
    opsRunDate.textContent = summary.updated_at ? fmtIsoDateTime(summary.updated_at) : "-";
  }
  renderStatusBoard(summary);
  syncModeUi(summary);

  const exactaHitRate = Number.isFinite(recent30Exacta.hit_rate) ? recent30Exacta.hit_rate : null;
  const exactaRoi = Number.isFinite(recent30Exacta.roi) ? recent30Exacta.roi : null;

  const cards = [
    ["対象レース", targetRaces || "-", "neutral", "今日の運用判断対象レース"],
    ["結果あり", resultAvailable || "-", "neutral", "実結果を持つレース数"],
    ["実オッズあり", realOddsAvailable || "-", "odds", "実オッズが取れたレース数"],
    ["未公表待ち", pendingUnpublished || "-", "watch", "未公表のため再取得待ちのレース数"],
    ["BUY count", buyCount ?? "-", "buy", "購入候補になった件数"],
    ["見送り主因", skipReasonsText, "neutral", "見送り理由の上位"],
    ["購入候補の割合", summary.buy_rate == null ? "-" : `${fmt(summary.buy_rate * 100, 2)}%`, "neutral", "判定レースのうち購入候補になった割合"],
    ["判定内訳", `購入候補:${decisionCounts.BUY ?? buyCount} / 様子見:${decisionCounts.WATCH ?? watchCount} / 見送り:${decisionCounts.SKIP ?? skipCount}`, "neutral", "判定の内訳"],
    ["現在モード", effectiveMode !== currentMode ? `${currentMode} / 実効:${effectiveMode}` : currentMode, "neutral", "現在の運用モード"],
    ["オッズ取得率", summary.real_odds_coverage == null ? "-" : `${fmt(summary.real_odds_coverage * 100, 1)}%`, "odds", "必要なオッズを取得できた割合"],
  ];
  kpiCards.innerHTML = cards.map(([k, v, tone, help]) => `<article class="card ${tone}" title="${esc(help)}"><div class="k" title="${esc(help)}">${esc(k)}</div><div class="v">${esc(v)}</div></article>`).join("");
}

function renderGateHealth(summary) {
  if (!gateHealthPanel) return;
  const gh = summary?.gate_health || {};
  const missingBreakdown = gh.missing_breakdown || {};
  const reasonCounts = gh.reason_keyword_counts || {};
  const gateCombos = gh.gate_combo_counts || {};
  const topCombo = Object.entries(gateCombos).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0];
  const cards = [
    {
      label: "PENDING主因",
      value: `${Number(reasonCounts.real_odds_missing || 0)}件`,
      note: "実オッズ未取得",
      tone: "warn",
    },
    {
      label: "直前MISSING",
      value: `${Number(missingBreakdown.pre_race_only || 0)}件`,
      note: "直前情報だけ不足",
      tone: "warn",
    },
    {
      label: "1着MISSING",
      value: `${Number(missingBreakdown.first_place_only || 0)}件`,
      note: "1着情報だけ不足",
      tone: "safe",
    },
    {
      label: "両方MISSING",
      value: `${Number(missingBreakdown.both_missing || 0)}件`,
      note: "1着+直前とも不足",
      tone: Number(missingBreakdown.both_missing || 0) > 0 ? "danger" : "safe",
    },
    {
      label: "最多パターン",
      value: topCombo ? topCombo[0] : "-",
      note: topCombo ? `${topCombo[1]}件` : "データなし",
      tone: "neutral",
    },
  ];
  gateHealthPanel.innerHTML = cards.map((card) => `
    <article class="gate-card ${card.tone}">
      <div class="gate-k">${esc(card.label)}</div>
      <div class="gate-v">${esc(card.value)}</div>
      <div class="gate-note">${esc(card.note)}</div>
    </article>
  `).join("");
}

function renderOpsHealth(summary) {
  if (!opsHealthPanel) return;
  const ops = summary?.ops_health || {};
  const backtest = ops.backtest || {};
  const guard = ops.guard || {};
  const compare = ops.compare || {};
  const pipeline = ops.pipeline || {};
  const guardReasons = Array.isArray(guard.reasons) ? guard.reasons : [];
  const compareReasons = Array.isArray(compare.reasons) ? compare.reasons : [];

  const cards = [
    {
      label: "採用ガード",
      value: String(guard.status || "UNKNOWN"),
      note: guardReasons.length ? guardReasons[0] : "最新判定",
      tone: String(guard.status || "").toUpperCase() === "PASS" ? "safe" : "danger",
    },
    {
      label: "最新バックテスト",
      value: backtest.roi == null ? "ROI -" : `ROI ${fmt(backtest.roi, 3)}`,
      note: `BUY ${Number(backtest.buy_count || 0)} / hit ${Number(backtest.hit_count || 0)} / DD ${backtest.max_drawdown == null ? "-" : fmt(backtest.max_drawdown, 3)}`,
      tone: backtest.roi != null && Number(backtest.roi) >= 1 ? "safe" : "warn",
    },
    {
      label: "候補モデル比較",
      value: String(compare.status || "UNKNOWN"),
      note: compare.promoted
        ? "candidate採用済み"
        : (compareReasons.length
            ? compareReasons[0]
            : `候補ROI ${compare.candidate_roi == null ? "-" : fmt(compare.candidate_roi, 3)} / BUY ${Number(compare.candidate_buy_count || 0)}`),
      tone: String(compare.status || "").toUpperCase() === "PASS" ? "safe" : "neutral",
    },
    {
      label: "運用パイプライン",
      value: String(pipeline.status || "unknown").toUpperCase(),
      note: `mode:${pipeline.mode || "-"} / steps:${Number(pipeline.step_count || 0)} / last:${pipeline.last_step || "-"}`,
      tone: String(pipeline.status || "").toLowerCase() === "ok" ? "safe" : "warn",
    },
    {
      label: "現在の材料",
      value: `BUY ${summary?.buy_count ?? 0}`,
      note: `PENDING ${summary?.decision_counts?.PENDING ?? 0} / 実オッズ率 ${summary?.real_odds_rate == null ? "-" : fmt(summary.real_odds_rate * 100, 1)}%`,
      tone: (summary?.buy_count ?? 0) > 0 ? "safe" : "warn",
    },
  ];

  opsHealthPanel.innerHTML = cards.map((card) => `
    <article class="gate-card ${card.tone}">
      <div class="gate-k">${esc(card.label)}</div>
      <div class="gate-v">${esc(card.value)}</div>
      <div class="gate-note">${esc(card.note)}</div>
    </article>
  `).join("");
}

function renderUpstreamHealth(summary) {
  if (!upstreamHealthPanel) return;
  const upstream = summary?.upstream_health || {};
  const diagnostics = upstream.diagnostics || {};
  const calibration = upstream.calibration || {};
  const calibrationCompare = upstream.calibration_compare || {};
  const selectionLeak = upstream.selection_leak || {};
  const cards = [
    {
      label: "approx_prob整合",
      value: diagnostics.approx_prob_hit_rate == null
        ? "-"
        : `${fmtPct(diagnostics.approx_prob_hit_rate, 1)} / 予測${fmtPct(diagnostics.approx_prob_avg_pred, 1)}`,
      note: diagnostics.approx_prob_roi == null ? "ROI -" : `ROI ${fmt(diagnostics.approx_prob_roi, 3)}`,
      tone: diagnostics.approx_prob_roi != null && Number(diagnostics.approx_prob_roi) >= 1 ? "safe" : "warn",
    },
    {
      label: "三連単順位",
      value: diagnostics.exact_rate == null
        ? "-"
        : `exact ${fmtPct(diagnostics.exact_rate, 1)}`,
      note: diagnostics.top5_rate == null
        ? "top5 - / top10 -"
        : `top5 ${fmtPct(diagnostics.top5_rate, 1)} / top10 ${fmtPct(diagnostics.top10_rate, 1)} / 中央${diagnostics.median_rank ?? "-"}`,
      tone: diagnostics.top5_rate != null && Number(diagnostics.top5_rate) >= 0.5 ? "safe" : "neutral",
    },
    {
      label: "順序ズレ主因",
      value: `${Number(diagnostics.first_lane_ok_but_order_weak_count || 0)}件`,
      note: `1着自体弱い ${Number(diagnostics.first_lane_itself_weak_count || 0)} / top20外 ${Number(diagnostics.actual_outside_top20_count || 0)}`,
      tone: Number(diagnostics.first_lane_ok_but_order_weak_count || 0) > 0 ? "warn" : "safe",
    },
    {
      label: "確率校正",
      value: calibration.method ? String(calibration.method).toUpperCase() : "-",
      note: calibration.base_brier == null
        ? "データなし"
        : `Brier ${fmt(calibration.base_brier, 4)} → ${fmt(calibration.calibrated_brier, 4)} / LogLoss ${fmt(calibration.base_logloss, 4)} → ${fmt(calibration.calibrated_logloss, 4)}`,
      tone: calibration.improved ? "safe" : "neutral",
    },
    {
      label: "raw→calib差",
      value: calibrationCompare.top_feature_abs_gap_improvement == null
        ? "-"
        : `Δabs ${fmt(calibrationCompare.top_feature_abs_gap_improvement, 4)}`,
      note: calibrationCompare.top_feature
        ? `${calibrationCompare.top_feature} / ${calibrationCompare.raw_source || "raw"}→${calibrationCompare.selected_source || "selected"} / races ${calibrationCompare.result_available_races ?? "-"}`
        : "比較データなし",
      tone: calibrationCompare.top_feature_abs_gap_improvement != null && Number(calibrationCompare.top_feature_abs_gap_improvement) > 0 ? "safe" : "neutral",
    },
    {
      label: "選別漏れ",
      value: `${Number(selectionLeak.count || 0)}件`,
      note: selectionLeak.top_reason ? `主因: ${selectionLeak.top_reason}` : (selectionLeak.target || "データなし"),
      tone: Number(selectionLeak.count || 0) > 0 ? "warn" : "safe",
    },
  ];
  upstreamHealthPanel.innerHTML = cards.map((card) => `
    <article class="gate-card ${card.tone}">
      <div class="gate-k">${esc(card.label)}</div>
      <div class="gate-v">${esc(card.value)}</div>
      <div class="gate-note">${esc(card.note)}</div>
    </article>
  `).join("");
}

function deriveRaceYosouMeta(viewModel, races) {
  const meta = { ...(viewModel?.meta || {}) };
  if (meta.brand == null) meta.brand = "日刊スポーツ風レイアウト";
  const safeRaces = Array.isArray(races) ? races : [];
  if (meta.updatedAt == null && safeRaces.length) {
    const maxRaceNo = safeRaces.reduce((max, race) => {
      const n = Number(race?.raceNo ?? race?.race_no ?? race?.roundNo ?? 0);
      return Number.isFinite(n) && n > max ? n : max;
    }, 0);
    meta.updatedAt = maxRaceNo > 0 ? `${maxRaceNo}R時点` : "";
  }

  if (meta.hitRate == null || meta.recoveryRate == null || meta.recent30WindowRaces == null) {
    const summary = viewModel?.summary || {};
    if (meta.hitRate == null && Number.isFinite(Number(summary?.recent30_trifecta?.hit_rate))) {
      meta.hitRate = Number(summary.recent30_trifecta.hit_rate) * 100;
    }
    if (meta.recoveryRate == null && Number.isFinite(Number(summary?.recent30_trifecta?.roi))) {
      meta.recoveryRate = Number(summary.recent30_trifecta.roi) * 100;
    }
    if (meta.recent30WindowRaces == null) {
      const n = Number(summary?.recent30_trifecta?.window_races);
      meta.recent30WindowRaces = Number.isFinite(n) ? n : safeRaces.length || 0;
    }
  }

  if (meta.compiLeader == null || meta.compiLeaderValue == null || meta.exhibitionFastest == null || meta.exhibitionFastestValue == null) {
    const boatRows = [];
    for (const race of safeRaces) {
      const boats = Array.isArray(race?.boats) ? race.boats : [];
      for (const boat of boats) {
        const lane = Number(boat?.lane ?? boat?.no ?? boat?.boatNo ?? boat?.boat_no);
        const compi = Number(boat?.compi ?? boat?.confidence_score);
        const ex = Number(boat?.exhibitionTime ?? boat?.exhibition_time ?? boat?.startExhibitionTime ?? boat?.start_exhibition_time);
        if (Number.isFinite(lane)) {
          boatRows.push({
            lane,
            label: `${lane}号艇`,
            compi,
            ex,
          });
        }
      }
    }
    if (meta.compiLeader == null) {
      const topCompi = boatRows.filter((row) => Number.isFinite(row.compi)).sort((a, b) => b.compi - a.compi)[0];
    if (topCompi) {
      meta.compiLeader = topCompi.label;
        meta.compiLeaderValue = Number(topCompi.compi.toFixed(2));
      }
    }
    if (meta.exhibitionFastest == null) {
      const fastest = boatRows.filter((row) => Number.isFinite(row.ex)).sort((a, b) => a.ex - b.ex)[0];
      if (fastest) {
        meta.exhibitionFastest = fastest.label;
        meta.exhibitionFastestValue = Number(fastest.ex.toFixed(2));
      }
    }
  }

  return meta;
}

function todayDigits() {
  const now = new Date();
  const jst = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Tokyo" }));
  const y = jst.getFullYear();
  const m = String(jst.getMonth() + 1).padStart(2, "0");
  const d = String(jst.getDate()).padStart(2, "0");
  return `${y}${m}${d}`;
}

function flattenRaceyosouRows(viewModel) {
  const rows = [];
  const races = safeArray(viewModel?.races);
  for (const race of races) {
    const raceNo = Number(race?.raceNumber || race?.raceNo || race?.race_no || 0);
    const venue = viewModel?.venue || race?.venue || "";
    const aiPredictions = safeArray(race?.aiPredictions);
    for (const pred of aiPredictions) {
      const combo = comboPlainText(pred?.combo ?? pred?.trifecta ?? pred?.buy_combo ?? pred?.label ?? "");
      const expectedValue = pred?.expectedValue ?? pred?.expected_value ?? pred?.ev ?? null;
      const prob = pred?.prob ?? pred?.approxProb ?? pred?.winProbaNorm ?? pred?.winProbaRaw ?? null;
      rows.push({
        date: viewModel?.date || "",
        venue,
        race_id: race?.raceId || "",
        raceId: race?.raceId || "",
        race_no: raceNo,
        raceNo,
        decision: String(pred?.decision || "WATCH").toUpperCase(),
        recommended_trifecta: combo,
        trifecta: combo,
        combo,
        odds: pred?.odds,
        ev: expectedValue,
        expectedValue,
        edge: pred?.edge ?? null,
        probRank: pred?.probRank ?? pred?.prob_rank ?? null,
        evRank: pred?.evRank ?? pred?.ev_rank ?? null,
        approxProb: prob,
        confidence: prob,
        reason: pred?.reason || "",
        grade: pred?.grade || "C",
        odds_source: pred?.odds == null ? "missing" : "real",
        bet_amount: pred?.decision === "BUY" ? 100 : 0,
        bet_pct: pred?.decision === "BUY" ? 1 : 0,
        result: race?.result || {},
        data_status: statusText(race?.dataStatus || viewModel?.dataStatus || "missing"),
        statusFlags: {
          closed: String(race?.status || "").toLowerCase() === "complete",
          exhibitionMissing: !safeArray(race?.startExhibition).length,
          reporterMissing: true,
        },
      });
    }
  }
  return rows;
}

function raceRoundLabel(race) {
  const raceNo = Number(race?.raceNo ?? race?.race_no ?? race?.roundNo);
  const title = race?.roundLabel || race?.raceTitle || race?.title || race?.race_name || race?.raceName || race?.grade || "";
  if (Number.isFinite(raceNo) && title) return `${raceNo}R ${title}`;
  if (Number.isFinite(raceNo)) return `${raceNo}R`;
  return title || "-";
}

function raceResultText(race) {
  const text = String(race?.resultText || race?.result_text || race?.statusText || "").trim();
  if (text) return text;
  const closed = Boolean(race?.statusFlags?.closed);
  if (closed) {
    const result = String(race?.result || race?.officialResult || race?.oddsResult || "").trim();
    return result ? `的中！ ${result}` : "締切後";
  }
  return "締切前 / 発売中";
}

function predictionTitle(item) {
  return String(
    item?.title
      || item?.name
      || item?.label
      || item?.betLabel
      || item?.comboLabel
      || item?.trifectaLabel
      || (item?.trifecta ? formatKyoteiCombo(item.trifecta) : "")
      || (item?.buy_combo ? formatKyoteiCombo(item.buy_combo) : "")
      || "-"
  ).trim();
}

function predictionConfidenceText(item, fallbackScale = 100) {
  const candidates = [
    item?.confidence,
    item?.confidencePct,
    item?.score,
    item?.mainScore,
    item?.approxProb,
    item?.winProbaNorm,
    item?.winProbaRaw,
  ];
  for (const value of candidates) {
    const num = Number(value);
    if (!Number.isFinite(num)) continue;
    if (num <= 1) return `${fmt(num * 100, 1)}%`;
    if (num <= fallbackScale) return `${fmt(num, 1)}%`;
    return `${fmt(num, 1)}`;
  }
  return "-";
}

function metricTone(value, high = null, low = null) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "neutral";
  if (high != null && n >= high) return "good";
  if (low != null && n <= low) return "warn";
  return "neutral";
}

function renderBoatMetric(label, value, tone, suffix = "") {
  return `<div class="race-yosou-boat-metric ${tone || "neutral"}">
    <div class="label">${esc(label)}</div>
    <div class="value">${esc(value == null ? "-" : `${value}${suffix}`)}</div>
  </div>`;
}

function toFiniteNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function normalizeTextList(value) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item ?? "").trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    const text = value.trim();
    return text ? [text] : [];
  }
  if (value == null) return [];
  const text = String(value).trim();
  return text ? [text] : [];
}

function normalizeDecision(value) {
  const text = String(value || "").trim().toUpperCase();
  return text || "-";
}

function formatProbDisplay(value) {
  const n = toFiniteNumber(value);
  if (n == null) return "-";
  const pct = n <= 1 ? n * 100 : n;
  return `的中確率 ${fmt(pct, 1)}%`;
}

function formatOddsDisplay(value) {
  const n = toFiniteNumber(value);
  if (n == null) return "-";
  return `オッズ ${fmt(n, 1)}`;
}

function formatEvDisplay(value) {
  const n = toFiniteNumber(value);
  if (n == null) return "-";
  return `EV ${fmt(n, 2)}`;
}

function formatStakeDisplay(value) {
  const n = toFiniteNumber(value);
  if (n == null) return "-";
  return `推奨 ${fmtYen(n)}`;
}

function summarizeRaceYosouPredictions(race) {
  const predictions = safeArray(race?.aiPredictions);
  const buyCount = predictions.filter((pred) => normalizeDecision(pred?.decision) === "BUY").length;
  const evValues = predictions
    .map((pred) => toFiniteNumber(pred?.ev ?? pred?.expectedValue ?? pred?.expected_value))
    .filter((n) => n != null);
  const probValues = predictions
    .map((pred) => toFiniteNumber(pred?.prob ?? pred?.approxProb ?? pred?.winProbaNorm ?? pred?.winProbaRaw))
    .filter((n) => n != null)
    .map((n) => (n <= 1 ? n * 100 : n));
  const warnings = normalizeTextList(race?.warnings);
  return {
    buyCount,
    maxEv: evValues.length ? Math.max(...evValues) : null,
    maxProb: probValues.length ? Math.max(...probValues) : null,
    warnings,
    warningCount: warnings.length,
    warningText: warnings.length ? warnings.slice(0, 2).join(" / ") : "",
  };
}

/**
 * @param {RaceYosouViewModel|null|undefined} viewModel
 */
function renderRaceYosouView(viewModel) {
  if (!raceYosouBody) return;
  const races = safeArray(viewModel?.races);
  const dateLabel = viewModel?.date_label || viewModel?.date || "-";
  const venueLabel = viewModel?.venue_label || viewModel?.venue || "-";
  const eventLabel = viewModel?.event_label || viewModel?.eventName || viewModel?.event_name || viewModel?.event || "-";
  const generatedAt = viewModel?.generatedAt || viewModel?.generated_at || viewModel?.updatedAt || viewModel?.updated_at || "";
  const safeDate = String(viewModel?.date || dateLabel || "-").slice(0, 10);
  const safeDateLabel = `${safeDate.replace(/-/g, "/")}${formatJstWeekdayLabel(safeDate) ? `（${formatJstWeekdayLabel(safeDate)}）` : ""}`;
  const sourceCounts = safeObject(viewModel?.source_counts);
  const dataStatus = statusText(viewModel?.dataStatus || viewModel?.data_status || "missing");
  const viewWarnings = normalizeTextList(viewModel?.warnings || viewModel?.warning || viewModel?.warningList);
  const meta = deriveRaceYosouMeta(viewModel, races);

  if (raceYosouMeta) {
    const generatedAtText = generatedAt ? fmtIsoDateTime(generatedAt) : "-";
    const warningText = viewWarnings.length ? ` ・ 警告 ${viewWarnings.length}件` : "";
    raceYosouMeta.textContent = `表示: ${dateLabel} / ${venueLabel} ・ ${dataStatus} ・ 生成 ${generatedAtText}${warningText} ・ features ${Number(sourceCounts.features || 0)} rows / win ${Number(sourceCounts.win_proba || 0)} rows / candidates ${Number(sourceCounts.trifecta_candidates || 0)} rows`;
  }

  if (!races.length) {
    const statusSummary = [
      `レースデータ: ${dataStatus === "available" ? "取得済み" : dataStatus === "pending" ? "未反映" : "未取得"}`,
      `予想: ${viewModel?.races?.length ? "あり" : "未生成"}`,
      generatedAt ? `生成: ${fmtIsoDateTime(generatedAt)}` : "",
    ].filter(Boolean).join(" / ");
    const warningChips = viewWarnings.length
      ? `<div class="race-yosou-chip-row">${viewWarnings.map((item) => `<span class="race-yosou-chip warn">${esc(item)}</span>`).join("")}</div>`
      : "";
    raceYosouBody.innerHTML = `
      <section class="race-yosou-dashboard">
        <div class="race-yosou-dashboard-head">
          <div class="race-yosou-dashboard-kicker">${esc(meta.brand || "レース予想")}</div>
          <div class="race-yosou-dashboard-title">${esc(venueLabel)} / ${esc(dateLabel)}</div>
          <div class="race-yosou-dashboard-sub">${esc(eventLabel)}${meta.updatedAt ? ` ・ ${esc(meta.updatedAt)}` : ""}</div>
        </div>
        ${warningChips}
        <div class="race-yosou-dashboard-grid">
          <article class="race-yosou-summary-card"><div class="k">状態</div><div class="v">${esc(statusSummary)}</div><div class="note">${esc(viewModel?.source?.modelVersion || viewModel?.modelVersion || "baseline_rule_v1")}</div></article>
          <article class="race-yosou-summary-card"><div class="k">AI的中率</div><div class="v">${meta.hitRate == null ? "-" : `${fmt(meta.hitRate, 1)}%`}</div><div class="note">モデルの直近サマリー</div></article>
          <article class="race-yosou-summary-card"><div class="k">AI回収率</div><div class="v">${meta.recoveryRate == null ? "-" : `${fmt(meta.recoveryRate, 1)}%`}</div><div class="note">参考回収率</div></article>
          <article class="race-yosou-summary-card"><div class="k">コンピ首位</div><div class="v">${esc(meta.compiLeader || "-")}</div><div class="note">${meta.compiLeaderValue == null ? "データなし" : `指数 ${fmt(meta.compiLeaderValue, 2)}`}</div></article>
          <article class="race-yosou-summary-card"><div class="k">展示最速</div><div class="v">${esc(meta.exhibitionFastest || "-")}</div><div class="note">${meta.exhibitionFastestValue == null ? "データなし" : `展示 ${fmt(meta.exhibitionFastestValue, 2)}秒`}</div></article>
      </div>
    </section>
    <div class="race-yosou-empty-state">${dataStatus === "unavailable" ? "レースデータ未取得" : "予想未生成"}</div>`;
    return;
  }

  const topStatsHtml = `
    <div class="race-yosou-hero-stats">
      <span class="race-yosou-stat-pill"><b>AI的中率</b>${meta.hitRate == null ? "-" : `${fmt(meta.hitRate, 1)}%`}</span>
      <span class="race-yosou-stat-pill"><b>AI回収率</b>${meta.recoveryRate == null ? "-" : `${fmt(meta.recoveryRate, 1)}%`}</span>
      <span class="race-yosou-stat-pill"><b>コンピ首位</b>${esc(meta.compiLeader || "-")}${meta.compiLeaderValue == null ? "" : ` / ${fmt(meta.compiLeaderValue, 2)}`}</span>
      <span class="race-yosou-stat-pill"><b>展示最速</b>${esc(meta.exhibitionFastest || "-")}${meta.exhibitionFastestValue == null ? "" : ` / ${fmt(meta.exhibitionFastestValue, 2)}秒`}</span>
    </div>`;
  const topWarningsHtml = viewWarnings.length
    ? `<div class="race-yosou-chip-row">${viewWarnings.map((item) => `<span class="race-yosou-chip warn">${esc(item)}</span>`).join("")}</div>`
    : "";

  const tabsHtml = `
    <div class="race-yosou-tabs" role="tablist" aria-label="レース切替">
      ${Array.from({ length: 12 }, (_, i) => i + 1).map((raceNo) => {
        const activeNo = Number(raceYosouActiveRaceNo || races[0]?.raceNo || 1);
        const active = activeNo === raceNo;
        const exists = races.some((race) => Number(race?.raceNo || 0) === raceNo);
        return `<button type="button" class="race-yosou-tab ${active ? "active" : ""}" data-raceyosou-race="${esc(String(raceNo))}" ${exists ? "" : "disabled"}>${esc(`${raceNo}R`)}</button>`;
      }).join("")}
    </div>`;

  const activeRaceNo = Number(raceYosouActiveRaceNo || races[0]?.raceNo || 1);
  let race = races.find((item) => Number(item?.raceNo || 0) === activeRaceNo) || races[0];
  raceYosouActiveRaceNo = Number(race?.raceNo || activeRaceNo || 1);

  const chips = [];
  chips.push(`<span class="race-yosou-chip neutral">${esc(dataStatus === "available" ? "データ取得済み" : dataStatus === "pending" ? "展示未反映" : "データ未取得")}</span>`);
  if (race?.statusFlags?.closed) chips.push('<span class="race-yosou-chip neutral">締切済み</span>');
  if (race?.statusFlags?.exhibitionMissing) chips.push('<span class="race-yosou-chip warn">展示未取得</span>');
  if (race?.statusFlags?.reporterMissing) chips.push('<span class="race-yosou-chip warn">記者予想なし</span>');
  if (dataStatus === "pending") chips.push('<span class="race-yosou-chip warn">オッズ未取得</span>');
  if (!chips.length) chips.push('<span class="race-yosou-chip">発売中</span>');

  const raceDataStatus = statusText(race?.dataStatus || viewModel?.dataStatus || "missing");
  const raceDataStatusReason = safeArray(race?.dataStatusReason || viewModel?.dataStatusReason || viewModel?.source?.data_status_reason);
  const raceSource = safeObject(race?.source || viewModel?.source);
  const raceSummary = summarizeRaceYosouPredictions(race);
  const raceWarnings = normalizeTextList(race?.warnings);
  const raceStatusFlags = safeObject(race?.statusFlags);
  const raceStatusTags = [];
  if (raceStatusFlags.closed) raceStatusTags.push("締切済み");
  if (raceStatusFlags.exhibitionMissing) raceStatusTags.push("展示未取得");
  if (raceStatusFlags.reporterMissing) raceStatusTags.push("記者予想なし");
  if (raceDataStatus === "pending") raceStatusTags.push("オッズ未取得");
  const raceSummaryWarningsHtml = raceWarnings.length
    ? `<div class="race-yosou-chip-row">${raceWarnings.map((item) => `<span class="race-yosou-chip warn">${esc(item)}</span>`).join("")}</div>`
    : "";
  const sourceMetaHtml = `
    <div class="race-yosou-source-meta">
      <div><b>racelist</b>: ${esc(raceSource.racelistHttpStatus || raceSource.racelistStatus || "missing")} ${raceSource.racelistUrl ? `・<a href="${esc(raceSource.racelistUrl)}" target="_blank" rel="noopener">URL</a>` : ""}</div>
      <div><b>odds3t</b>: ${esc(raceSource.odds3tHttpStatus || raceSource.odds3tStatus || "missing")} ${raceSource.odds3tUrl ? `・<a href="${esc(raceSource.odds3tUrl)}" target="_blank" rel="noopener">URL</a>` : ""}</div>
      <div><b>beforeinfo</b>: ${esc(raceSource.beforeinfoHttpStatus || raceSource.beforeinfoStatus || "missing")} ${raceSource.beforeinfoUrl ? `・<a href="${esc(raceSource.beforeinfoUrl)}" target="_blank" rel="noopener">URL</a>` : ""}</div>
      <div><b>result</b>: ${esc(raceSource.resultHttpStatus || raceSource.resultStatus || "pending")} ${raceSource.resultUrl ? `・<a href="${esc(raceSource.resultUrl)}" target="_blank" rel="noopener">URL</a>` : ""}</div>
      <div><b>updatedAt</b>: ${esc(race?.updatedAt || viewModel?.updatedAt || "-")}</div>
      <div><b>modelVersion</b>: ${esc(raceSource.modelVersion || viewModel?.modelVersion || "baseline_rule_v1")}</div>
      <div><b>dataStatusReason</b>: ${raceDataStatusReason.length ? raceDataStatusReason.map((item) => `<span class="reason-tag tag-neutral">${esc(String(item))}</span>`).join(" ") : '<span class="reason-tag tag-neutral">なし</span>'}</div>
    </div>`;

  const aiPredictions = Array.isArray(race?.aiPredictions) ? race.aiPredictions : [];
  const reporterPredictions = Array.isArray(race?.reporterPredictions) ? race.reporterPredictions : [];
  const boats = Array.isArray(race?.boats) ? race.boats : [];
  const roundLabel = raceRoundLabel(race);
  const resultText = raceResultText(race);
  const reporterHeadline = race?.reporterHeadline || (reporterPredictions[0]?.trifecta ? `本線 ${reporterPredictions[0].trifecta}` : "本線候補");

  const weather = safeObject(race?.weather);
  const weatherHtml = Object.keys(weather).length
    ? `<div class="race-yosou-weather-bar">
        <span class="race-yosou-weather-icon">${esc(weather.sky === "晴れ" ? "☀" : weather.sky === "雨" ? "🌧" : weather.sky === "小雨" ? "🌦" : "☁")}</span>
        <span class="race-yosou-weather-text">${esc(weather.sky || "-")}</span>
        <span class="race-yosou-weather-chip">風向 ${esc(weather.windDirection ?? weather.wind_direction ?? "-")}</span>
        <span class="race-yosou-weather-chip">風速 ${esc(weather.windSpeed ?? weather.wind_speed ?? weather.wind ?? "-")}m</span>
        <span class="race-yosou-weather-chip">水面 ${esc(weather.waterSurface ?? weather.water_surface ?? weather.surface ?? "-")}</span>
        <span class="race-yosou-weather-chip">気温 ${esc(weather.temperature ?? weather.temp ?? "-")}℃</span>
      </div>`
    : "";

  const aiHtml = aiPredictions.length
    ? `<div class="race-yosou-table">
        <div class="race-yosou-table-head"><span>順</span><span>買い目</span><span>判定</span><span>級</span><span>prob</span><span>odds</span><span>EV</span><span>stake</span><span>stopReason</span><span>edge</span><span>reason</span></div>
        ${aiPredictions.map((item, index) => {
          const isTop = index === 0;
          const comboText = comboPlainText(item?.combo ?? item?.trifecta ?? item?.buy_combo ?? item?.label ?? "");
          const decision = normalizeDecision(item?.decision);
          const expectedValue = item?.expectedValue ?? item?.expected_value ?? item?.ev ?? null;
          const edge = item?.edge ?? null;
          const prob = item?.prob ?? item?.approxProb ?? item?.winProbaNorm ?? item?.winProbaRaw ?? null;
          const odds = item?.odds;
          const stake = item?.stake ?? item?.bet_amount ?? item?.betAmount ?? item?.recommendedStake ?? null;
          const stopReason = item?.stopReason ?? item?.stop_reason ?? item?.skipReason ?? item?.skip_reason ?? "";
          const reasonText = String(item?.reason || "-");
          const decisionClass = decision === "BUY" ? "buy" : decision === "WATCH" ? "watch" : decision === "SKIP" ? "skip" : "neutral";
          const evWarn = decision === "BUY" && expectedValue == null ? '<div class="race-yosou-warning">EV未算出</div>' : "";
          const oddsText = formatOddsDisplay(odds);
          const evText = formatEvDisplay(expectedValue);
          const stakeText = formatStakeDisplay(stake);
          const edgeText = edge == null ? "-" : fmt(Number(edge), 3);
          const probText = formatProbDisplay(prob);
          const stopReasonText = stopReason ? String(stopReason) : "-";
          return `<div class="race-yosou-table-row ${isTop ? "top" : ""} ${decisionClass}">
            <span class="rank">${esc(String(item.rank || index + 1))}</span>
            <span class="title">${formatKyoteiCombo(comboText)}</span>
            <span><span class="pill ${decisionClass}">${esc(decision)}</span>${evWarn}</span>
            <span><span class="grade-pill ${String(item.grade || "C").toLowerCase()}">${esc(item.grade || "C")}</span></span>
            <span>${esc(probText)}</span>
            <span>${esc(oddsText)}</span>
            <span>${esc(evText)}</span>
            <span>${esc(stakeText)}</span>
            <span class="stop-reason">${esc(stopReasonText)}</span>
            <span>${esc(edgeText)}</span>
            <span class="meta">${esc(reasonText)}</span>
          </div>`;
        }).join("")}
      </div>`
    : '<div class="race-yosou-empty">予想未生成</div>';

  const reporterHtml = reporterPredictions.length
      ? `<div class="race-yosou-reporter">
            <div class="race-yosou-reporter-headline">${esc(reporterHeadline)}</div>
            <div class="race-yosou-reporter-comment">${esc(race?.reporterComment || "コメントなし")}</div>
            <div class="race-yosou-bets">${reporterPredictions.map((item, index) => `<div class="race-yosou-bet ${index === 0 ? "top" : ""}">${formatKyoteiCombo(item?.trifecta || item?.buy_combo || item?.combo || item?.label || "")}</div>`).join("")}</div>
         </div>`
    : '<div class="race-yosou-empty">記者予想なし</div>';

  const startExhibition = Array.isArray(race?.startExhibition) ? race.startExhibition : [];
  const fastest = startExhibition
    .map((item) => Number(item?.time))
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b)[0];
  const startHtml = startExhibition.length
    ? `<div class="race-yosou-start-grid">${startExhibition.map((item) => {
        const time = Number(item?.time);
        const lane = Number(item?.no);
        const isFastest = Number.isFinite(fastest) && Number.isFinite(time) && time === fastest;
        return `<div class="race-yosou-start-card ${isFastest ? "fastest" : ""}">
          <div class="race-yosou-start-badge ${String(item?.type || "").toUpperCase() === "S" ? "s" : "d"}">${esc(String(item?.type || "-"))}</div>
          <div class="race-yosou-start-lane">${esc(Number.isFinite(lane) ? `${lane}号艇` : "-")}</div>
          <div class="race-yosou-start-time">${Number.isFinite(time) ? fmt(time, 2) : "-"}</div>
        </div>`;
      }).join("")}</div>`
    : "";

  const boatHtml = boats.length
    ? `<div class="race-yosou-boat-scroll">
        <table class="race-yosou-boat-table">
          <tbody>
            <tr>
              <th class="race-yosou-boat-th"></th>
              ${boats.map((boat, index) => {
                const lane = Number(boat.lane ?? boat.no ?? index + 1);
                const regNo = boat.regNo || boat.racerNo || boat.mbrNo || boat.mbr_no || "";
                return `<td class="race-yosou-boat-td race-yosou-boat-headcell">
                  <div class="race-yosou-boat-headstack">
                    <span class="boat-n boat-${lane}">${lane}</span>
                    ${regNo ? `<span class="race-yosou-boat-reg">${esc(String(regNo))}</span>` : ""}
                  </div>
                </td>`;
              }).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">直前気配</th>
              ${boats.map((boat) => `<td class="race-yosou-boat-td">${boat.confidence ? `<span class="confidence-badge">${esc(boat.confidence)}</span>` : '<span class="race-yosou-empty-inline">-</span>'}</td>`).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">コンピ指数</th>
              ${boats.map((boat) => {
                const compi = Number(boat.compi ?? boat.confidence_score);
                const maxCompi = Math.max(...boats.map((b) => Number(b.compi ?? b.confidence_score)).filter(Number.isFinite));
                const tone = Number.isFinite(compi) && Number.isFinite(maxCompi) && compi === maxCompi ? "race-yosou-boat-strong" : "";
                return `<td class="race-yosou-boat-td ${tone}">${Number.isFinite(compi) ? fmt(compi, 0) : "-"}</td>`;
              }).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">選手名</th>
              ${boats.map((boat, index) => {
                const lane = Number(boat.lane ?? boat.no ?? index + 1);
                const name = boat.name || boat.racer_name || boat.label || `${lane}号艇`;
                return `<td class="race-yosou-boat-td race-yosou-boat-namecell">${esc(name)}</td>`;
              }).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">級別</th>
              ${boats.map((boat) => `<td class="race-yosou-boat-td">${boat.rank ? `<span class="grade-pill ${String(boat.rank).toLowerCase().startsWith("a") ? "a" : "b"}">${esc(boat.rank)}</span>` : '<span class="race-yosou-empty-inline">-</span>'}</td>`).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">支部</th>
              ${boats.map((boat) => `<td class="race-yosou-boat-td">${esc(boat.branch || boat.branch_name || "-")}</td>`).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">年齢</th>
              ${boats.map((boat) => `<td class="race-yosou-boat-td">${boat.age != null ? `${esc(String(boat.age))}歳` : "-"}</td>`).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">体重</th>
              ${boats.map((boat) => {
                const weight = Number(boat.weight);
                return `<td class="race-yosou-boat-td">${Number.isFinite(weight) ? `${fmt(weight, 1)}kg` : "-"}</td>`;
              }).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">F / L</th>
              ${boats.map((boat) => {
                const foul = boat.foul || {};
                const flText = boat.fl || boat.FL || `F${Number(foul.f || 0)}L${Number(foul.l || 0)}`;
                const hasFlag = /F1|L1/.test(String(flText));
                return `<td class="race-yosou-boat-td ${hasFlag ? "race-yosou-boat-danger" : ""}">${esc(String(flText).replace(/([FL])/g, "$1 "))}</td>`;
              }).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">平均ST</th>
              ${boats.map((boat) => {
                const avgSt = Number(boat.avgSt ?? boat.avg_st);
                const minSt = Math.min(...boats.map((b) => Number(b.avgSt ?? b.avg_st)).filter(Number.isFinite));
                const tone = Number.isFinite(avgSt) && Number.isFinite(minSt) && avgSt === minSt ? "race-yosou-boat-strong" : "";
                return `<td class="race-yosou-boat-td ${tone}">${Number.isFinite(avgSt) ? fmt(avgSt, 2) : "-"}</td>`;
              }).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">全国勝率</th>
              ${boats.map((boat) => {
                const nat = Number(boat.nationalWinRate ?? boat.national_win_rate ?? boat.natRate);
                return `<td class="race-yosou-boat-td">${Number.isFinite(nat) ? fmt(nat, 2) : "-"}</td>`;
              }).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">当地勝率</th>
              ${boats.map((boat) => {
                const local = Number(boat.localWinRate ?? boat.local_win_rate ?? boat.localRate);
                return `<td class="race-yosou-boat-td">${Number.isFinite(local) ? fmt(local, 2) : "-"}</td>`;
              }).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">モーター</th>
              ${boats.map((boat) => {
                const motorNo = boat.motorNo ?? boat.motor_no;
                const motorRate = Number(boat.motor2RenRate ?? boat.motor_2ren_rate ?? boat.motorRate);
                return `<td class="race-yosou-boat-td">${motorNo != null ? `#${esc(String(motorNo))}` : "-"}<div class="race-yosou-boat-subline">${Number.isFinite(motorRate) ? `${fmt(motorRate, 1)}%` : "-"}</div></td>`;
              }).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">ボート</th>
              ${boats.map((boat) => {
                const boatNo = boat.boatNo ?? boat.boat_no;
                const boatRate = Number(boat.boat2RenRate ?? boat.boat_2ren_rate ?? boat.boatRate);
                return `<td class="race-yosou-boat-td">${boatNo != null ? `#${esc(String(boatNo))}` : "-"}<div class="race-yosou-boat-subline">${Number.isFinite(boatRate) ? `${fmt(boatRate, 1)}%` : "-"}</div></td>`;
              }).join("")}
            </tr>
            <tr style="background:#fafafa">
              <th class="race-yosou-boat-th">展示</th>
              ${boats.map((boat) => {
                const ex = Number(boat.exhibitionTime ?? boat.exhibition_time ?? boat.startTiming);
                const exRank = boat.exhibitionTimeRank ?? boat.exhibition_time_rank;
                return `<td class="race-yosou-boat-td">${Number.isFinite(ex) ? fmt(ex, 2) : "-"}${exRank != null ? `<div class="race-yosou-boat-subline">R${esc(String(exRank))}</div>` : ""}</td>`;
              }).join("")}
            </tr>
            <tr>
              <th class="race-yosou-boat-th">早見</th>
              ${boats.map((boat) => `<td class="race-yosou-boat-td">${boat.nextRace ? `<span class="race-yosou-next-link">${esc(String(boat.nextRace))}</span>` : "—"}</td>`).join("")}
            </tr>
          </tbody>
        </table>
        <div class="race-yosou-legend">※赤文字：6艇内で1位</div>
      </div>`
    : '<div class="race-yosou-empty">選手情報がありません</div>';

  const raceHtml = `<article class="race-yosou-race">
    <div class="race-yosou-head race-yosou-race-header">
      <div class="race-yosou-race-header-main">
        <div class="race-yosou-race-title-row">
          <h3 class="race-yosou-title">${esc(race.raceNo ? `${race.raceNo}R` : "-")}</h3>
          <span class="race-yosou-round">${esc(roundLabel)}</span>
        </div>
        <div class="race-yosou-subline">
          <span>${esc(race.dateLabel || race.date || "-")}</span>
          <span>${esc(race.jcd || "")}</span>
          <span>${esc(race.deadline || "締切未定")}</span>
          <span>${esc(resultText)}</span>
        </div>
      </div>
      <div class="race-yosou-chip-row">${chips.join("")}</div>
    </div>
    <section class="race-yosou-panel-box race-yosou-race-summary">
      <h3>レースサマリー</h3>
      <div class="race-yosou-race-summary-grid">
        <article class="race-yosou-summary-card"><div class="k">BUY件数</div><div class="v">${raceSummary.buyCount}件</div><div class="note">AI予想のBUY判定数</div></article>
        <article class="race-yosou-summary-card"><div class="k">最高EV</div><div class="v">${raceSummary.maxEv == null ? "-" : fmt(raceSummary.maxEv, 2)}</div><div class="note">AI予想の最高EV</div></article>
        <article class="race-yosou-summary-card"><div class="k">最高prob</div><div class="v">${raceSummary.maxProb == null ? "-" : `${fmt(raceSummary.maxProb, 1)}%`}</div><div class="note">AI予想の最高的中確率</div></article>
        <article class="race-yosou-summary-card"><div class="k">dataStatus</div><div class="v">${esc(raceDataStatus)}</div><div class="note">${raceStatusTags.length ? raceStatusTags.join(" / ") : "状態正常"}</div></article>
        <article class="race-yosou-summary-card"><div class="k">warnings</div><div class="v">${raceSummary.warningCount}件</div><div class="note">${raceSummary.warningText ? esc(raceSummary.warningText) : "なし"}</div></article>
      </div>
      ${raceSummaryWarningsHtml}
    </section>
    ${sourceMetaHtml}
    ${weatherHtml}
    <div class="race-yosou-panels">
      <section class="race-yosou-panel-box">
        <h3>AI予想</h3>
        ${aiHtml}
      </section>
      <section class="race-yosou-panel-box">
        <h3>記者予想</h3>
        ${reporterHtml}
      </section>
    </div>
    ${startHtml ? `<section class="race-yosou-panel-box race-yosou-start-panel"><h3>スタート展示</h3>${startHtml}</section>` : ""}
    <section class="race-yosou-panel-box">
      <h3>選手比較</h3>
      ${boatHtml}
    </section>
  </article>`;

  raceYosouBody.innerHTML = `
    <section class="race-yosou-dashboard">
      <div class="race-yosou-dashboard-head">
        <div class="race-yosou-dashboard-brand-row">
          <span class="race-yosou-dashboard-brand">${esc(venueLabel)}</span>
          <span class="race-yosou-dashboard-brand-sub">${esc(meta.brand || "日刊スポーツ風レイアウト")}</span>
        </div>
        <div class="race-yosou-dashboard-title">${esc(safeDateLabel)}${eventLabel && eventLabel !== "-" ? `　${esc(eventLabel)}` : ""}</div>
        <div class="race-yosou-dashboard-sub">${generatedAt ? `生成 ${esc(fmtIsoDateTime(generatedAt))}` : meta.updatedAt ? `${esc(meta.updatedAt)}` : "更新情報なし"}${dataStatus ? ` ・ ${esc(dataStatus)}` : ""}${meta.recent30WindowRaces ? ` ・ 直近${esc(meta.recent30WindowRaces)}R` : ""}</div>
      </div>
      ${topWarningsHtml}
      ${topStatsHtml}
    </section>
    <div class="race-yosou-tabs-wrap">
      ${tabsHtml}
    </div>
    ${raceHtml}
  `;

  raceYosouBody.querySelectorAll("[data-raceyosou-race]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const next = Number(btn.getAttribute("data-raceyosou-race"));
      if (!Number.isFinite(next)) return;
      raceYosouActiveRaceNo = next;
      renderRaceYosouView(viewModel);
    });
  });

  if (!raceYosouAutoScrolled && typeof raceYosouBody.scrollIntoView === "function") {
    raceYosouAutoScrolled = true;
    window.setTimeout(() => {
      const panel = document.getElementById("raceYosouPanel");
      if (panel && typeof panel.scrollIntoView === "function") {
        panel.scrollIntoView({ block: "start", behavior: "auto" });
      }
    }, 0);
  }
}

function oddsBand(odds) {
  const v = Number(odds);
  if (!Number.isFinite(v)) return { cls: "odds-unknown", label: "-" };
  if (v <= 100) return { cls: "odds-low", label: `${fmt(v, 1)}（<=100）` };
  if (v <= 500) return { cls: "odds-mid", label: `${fmt(v, 1)}（101-500）` };
  return { cls: "odds-high", label: `${fmt(v, 1)}（500+）` };
}

function oddsSourceBadge(source) {
  const s = String(source || "");
  if (s === "real" || s === "file" || s === "official_result_odds") {
    return '<span class="source-badge source-official">実オッズ</span>';
  }
  if (s === "estimated" || s === "fallback_fixed") {
    return '<span class="source-badge source-fallback">暫定オッズ</span>';
  }
  if (s === "missing") {
    return '<span class="source-badge source-unknown">欠損</span>';
  }
  return '<span class="source-badge source-unknown">欠損</span>';
}

function kellyBadge(row) {
  const amount = Number(row?.bet_amount);
  const pct = Number(row?.bet_pct);
  if (!Number.isFinite(amount) || amount <= 0) return '<span class="source-badge source-unknown">0円</span>';
  const pctText = Number.isFinite(pct) ? `${fmt(pct, 2)}%` : "-";
  return `<span class="source-badge source-official">${fmtYen(amount)} / ${pctText}</span>`;
}

function preRaceBadge(row) {
  const score = Number(row?.pre_race_score);
  const gate = String(row?.pre_race_gate || "").toUpperCase();
  const source = String(row?.pre_race_source || "");
  if (!Number.isFinite(score)) {
    return '<span class="source-badge source-unknown">直前: -</span>';
  }
  let cls = "source-unknown";
  let label = "NORMAL";
  if (gate === "BLOCK" || score <= -1) {
    cls = "source-block";
    label = "BUY禁止";
  } else if (gate === "PRIORITY" || score >= 2) {
    cls = "source-priority";
    label = "優先";
  } else if (gate === "BOOST" || score >= 1) {
    cls = "source-boost";
    label = "補正";
  }
  const src = source ? ` / ${source.includes("start_timing") ? "ST proxy" : "直前"}` : "";
  return `<span class="source-badge ${cls}">直前 ${fmt(score, 2)} / ${label}${src}</span>`;
}

function roleBadge(label, score, gate, note) {
  const numScore = Number(score);
  const gateKey = String(gate || "").toUpperCase();
  const noteText = String(note || "");
  if (!Number.isFinite(numScore)) {
    return `<span class="source-badge source-unknown">${esc(label)}: -</span>`;
  }
  let cls = "source-unknown";
  let text = "NORMAL";
  if (gateKey === "BLOCK" || numScore < 1) {
    cls = "source-block";
    text = "BUY禁止";
  } else if (gateKey === "PRIORITY" || numScore >= 2) {
    cls = "source-priority";
    text = "優先";
  } else if (gateKey === "BOOST" || numScore >= 1) {
    cls = "source-boost";
    text = "強め";
  }
  const extra = noteText ? ` / ${esc(noteText)}` : "";
  return `<span class="source-badge ${cls}">${esc(label)} ${fmt(numScore, 2)} / ${text}${extra}</span>`;
}

function firstPlaceBadge(row) {
  return roleBadge("1着", row?.first_place_score, row?.first_place_gate, row?.first_place_note);
}

function secondPlaceBadge(row) {
  return roleBadge("2着", row?.second_place_score, row?.second_place_gate, row?.second_place_note);
}

function thirdPlaceBadge(row) {
  return roleBadge("3着", row?.third_place_score, row?.third_place_gate, row?.third_place_note);
}

function roleMiniText(label, score, gate) {
  const numScore = Number(score);
  if (!Number.isFinite(numScore)) return `${label} -`;
  return `${label} ${fmt(numScore, 2)} / ${String(gate || "MISSING")}`;
}

function renderSpotlight(rows) {
  const summaryCandidates = Array.isArray(lastSummary?.buy_candidates_top) ? lastSummary.buy_candidates_top : [];
  const sourceRows = summaryCandidates.length ? summaryCandidates : rows.filter((r) => String(r.decision || "").toUpperCase() === "BUY");
  const baseRows = sourceRows.length ? sourceRows : rows;
  const sorted = [...baseRows].sort((a, b) => {
    const scoreA = Number(a?.decision_score);
    const scoreB = Number(b?.decision_score);
    if (Number.isFinite(scoreA) && Number.isFinite(scoreB) && scoreA !== scoreB) return scoreB - scoreA;
    if (Number.isFinite(scoreA) && !Number.isFinite(scoreB)) return -1;
    if (!Number.isFinite(scoreA) && Number.isFinite(scoreB)) return 1;
    return sortPredictionsLatestFirst(a, b);
  });
  const picks = [];
  const usedVenues = new Set();

  for (const r of sorted) {
    if (picks.length >= 3) break;
    const venue = displayVenueName(r);
    if (usedVenues.has(venue)) continue;
    picks.push(r);
    usedVenues.add(venue);
  }
  for (const r of sorted) {
    if (picks.length >= 3) break;
    if (!picks.includes(r)) picks.push(r);
  }

  spotlightCards.innerHTML = picks
    .map((r, index) => {
      const raceLabel = displayRaceLabel(r);
      const cls = r.decision === "BUY" ? "buy" : (r.decision === "WATCH" ? "watch" : "skip");
      const seq = r.race_seq && Number(r.race_seq) > 12 ? `通番 ${Number(r.race_seq)}` : "";
      const probs = pickProbabilityRow(r);
      return `
        <article class="spot-card ${cls}">
          <div class="spot-head">
            <span class="spot-rank">#${index + 1}</span>
            <span class="pill ${cls}">${esc(r.decision)}</span>
          </div>
          <div class="spot-title">${esc(raceLabel)}</div>
          <div class="spot-tri">${formatKyoteiCombo(r.recommended_trifecta)}</div>
          <div class="spot-meta">
            <span title="複数の条件をまとめた判定用の指標">総合評価 ${fmt(r.decision_score, 2)}</span>
            <span title="長期的に見た買う価値の目安">期待値 ${fmt(r.ev, 3)}</span>
            <span title="必要なオッズを取得できた割合">オッズ ${fmt(r.odds, 1)}</span>
            <span title="この買い目が当たると予測された確率">的中見込み ${fmt(probs.hitProb, 4)}</span>
            <span title="1着になる確率の目安">1着 ${fmt(probs.firstPlaceProb, 4)}</span>
            ${firstPlaceBadge(r)}
            ${secondPlaceBadge(r)}
            ${thirdPlaceBadge(r)}
            ${preRaceBadge(r)}
            ${kellyBadge(r)}
            ${oddsSourceBadge(r.odds_source)}
            ${seq ? `<span>${esc(seq)}</span>` : ""}
          </div>
          <div class="spot-reason">${reasonTags(r.reason)}</div>
        </article>`;
    })
    .join("");
}

function renderPredictions(rows) {
  const venueCounter = new Map();
  rows.forEach((r) => {
    const key = displayVenueName(r);
    venueCounter.set(key, (venueCounter.get(key) || 0) + 1);
  });
  const venueText = [...venueCounter.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([k, v]) => `${k}: ${v}件`)
    .join(" / ");
  if (contextSummary) {
    const summaryBits = lastSummary
      ? ` ・ 購入候補:${lastSummary.buy_count ?? 0} / 様子見:${lastSummary.watch_count ?? 0} / 見送り:${lastSummary.skip_count ?? 0} / オッズ取得率:${fmt((lastSummary.real_odds_rate ?? 0) * 100, 1)}%`
      : "";
    const skipReasonBits = Array.isArray(lastSummary?.top_skip_reasons) && lastSummary.top_skip_reasons.length
      ? ` ・ 見送り主因:${lastSummary.top_skip_reasons.slice(0, 3).map((item) => `${shortReason(item.reason)}(${Number(item.count || 0)})`).join(" / ")}`
      : "";
    const preRaceBits = lastSummary?.pre_race_avg_score != null
      ? ` ・ 直前総合評価平均:${fmt(lastSummary.pre_race_avg_score, 2)} / 購入候補:${lastSummary.pre_race_boost_rows ?? 0} / 見送り:${lastSummary.pre_race_block_rows ?? 0}`
      : "";
    const firstPlaceBits = lastSummary?.first_place_avg_score != null
      ? ` ・ 1着総合評価平均:${fmt(lastSummary.first_place_avg_score, 2)} / 購入候補:${lastSummary.first_place_boost_rows ?? 0} / 見送り:${lastSummary.first_place_block_rows ?? 0}`
      : "";
    const secondPlaceBits = lastSummary?.second_place_avg_score != null
      ? ` ・ 2着総合評価平均:${fmt(lastSummary.second_place_avg_score, 2)} / 購入候補:${lastSummary.second_place_boost_rows ?? 0} / 見送り:${lastSummary.second_place_block_rows ?? 0}`
      : "";
    const thirdPlaceBits = lastSummary?.third_place_avg_score != null
      ? ` ・ 3着総合評価平均:${fmt(lastSummary.third_place_avg_score, 2)} / 購入候補:${lastSummary.third_place_boost_rows ?? 0} / 見送り:${lastSummary.third_place_block_rows ?? 0}`
      : "";
    const raceBits = lastSummary?.race_avg_score != null
      ? ` ・ 総合評価平均:${fmt(lastSummary.race_avg_score, 2)} / 購入候補:${lastSummary.race_priority_rows ?? 0} / 様子見:${lastSummary.race_watch_rows ?? 0} / 見送り:${lastSummary.race_block_rows ?? 0}`
      : "";
    const autoFilterLiveNote = lastSummary?.auto_filter_live_note
      ? ` ・ 自動条件:${lastSummary.auto_filter_live_note}`
      : "";
    const kellyText = lastSummary?.bet_management?.bankroll
      ? ` ・ 推奨比率:${fmtYen(lastSummary.bet_management.bankroll)} / 上限:${fmt(Number(lastSummary.bet_management.max_kelly_fraction || 0) * 100, 1)}% / 推奨総額:${fmtYen(lastSummary.kelly_total_bet)}`
      : "";
    const latestBits = lastSummary
      ? ` ・ 更新:${lastSummary.latest_refresh ? fmtIsoDateTime(lastSummary.latest_refresh) : "-"} / ガード:${lastSummary.latest_guard ? fmtIsoDateTime(lastSummary.latest_guard) : "-"} / エラー:${shortReason(lastSummary.latest_error_reason || "なし")}`
      : "";
    const fallbackText = "";
    const selectedVenueText = venueFilter?.value ? ` ・ 選択場:${venueFilter.value}` : "";
    contextSummary.textContent = `表示: ${rows.length}件${selectedVenueText}${venueText ? ` ・ 場別: ${venueText}` : ""}${summaryBits}${skipReasonBits}${preRaceBits}${firstPlaceBits}${secondPlaceBits}${thirdPlaceBits}${raceBits}${autoFilterLiveNote}${kellyText}${latestBits}${fallbackText}`;
  }

  if (predictionFreshnessBanner) {
    const latestPredictionDate = lastSummary?.latest_prediction_date;
    const latestSourceDate = lastSummary?.latest_source_date;
    const stalenessDays = Number(lastSummary?.prediction_staleness_days ?? 0);
    if (latestPredictionDate) {
      const isStale = stalenessDays >= 1;
      predictionFreshnessBanner.hidden = false;
      predictionFreshnessBanner.classList.toggle("is-stale", isStale);
      predictionFreshnessBanner.innerHTML = `
        <span class="freshness-title">予測データが最新ではありません。</span>
        予測データが最新ではありません。表示中の予測日は <b>${esc(latestPredictionDate)}</b> です。今日との差は <b>${stalenessDays}日</b> あります。最新データを取り込むまで、この画面は <b>${esc(latestPredictionDate)}</b> 時点の内容を表示します。
        ${latestSourceDate ? ` 元データの最終日も <b>${esc(latestSourceDate)}</b> です。` : ""}
      `;
    } else {
      predictionFreshnessBanner.hidden = true;
      predictionFreshnessBanner.textContent = "";
      predictionFreshnessBanner.classList.remove("is-stale");
    }
  }

  const ordered = [...rows].sort((a, b) => sortPredictionsLatestFirst(a, b));

  predBody.innerHTML = ordered
    .map((r, idx) => {
      const cls = r.decision === "BUY" ? "buy" : (r.decision === "WATCH" ? "watch" : "skip");
      const raceLabel = displayRaceLabel(r);
      const reason = shortReason(r.reason);
      const seq = r.race_seq && Number(r.race_seq) > 12 ? ` / 通番:${Number(r.race_seq)}` : "";
      const odds = oddsBand(r.odds);
      const probs = pickProbabilityRow(r);
      const rowId = `detail-${idx}`;
      return `
      <tr class="main-row" data-detail="${rowId}">
        <td><span class="pill ${cls}" title="${r.decision === "BUY" ? "購入候補" : (r.decision === "WATCH" ? "様子見" : "見送り")}">${esc(r.decision)}</span></td>
        <td>
          <div class="race-title">${esc(raceLabel)}</div>
          <div class="race-sub">${esc(r.date)}</div>
          <div class="race-sub" style="display:flex;align-items:center;gap:8px;">買い目: ${formatKyoteiCombo(r.recommended_trifecta)} <button class="copy-btn inline" data-tri="${esc(r.recommended_trifecta)}">コピー</button></div>
          <div class="race-sub">1着 ${fmt(probs.firstPlaceProb, 4)} / 的中見込み ${fmt(probs.hitProb, 4)}</div>
          <div class="race-sub">${esc(roleMiniText("1着", r.first_place_score, r.first_place_gate))} ・ ${esc(roleMiniText("2着", r.second_place_score, r.second_place_gate))} ・ ${esc(roleMiniText("3着", r.third_place_score, r.third_place_gate))} ・ ${esc(roleMiniText("総合評価", r.race_score, r.race_gate))}</div>
          <div class="race-sub">race_id: ${esc(r.race_id)}${esc(seq)}</div>
        </td>
        <td>
          <div><span class="odds-chip ${odds.cls}">${esc(odds.label)}</span></div>
          <div class="source-inline">${oddsSourceBadge(r.odds_source)}</div>
        </td>
        <td>${fmt(r.approx_prob, 4)}</td>
        <td>${fmt(r.ev, 3)}</td>
        <td>${preRaceBadge(r)}</td>
        <td>${kellyBadge(r)}</td>
        <td>${fmt(r.risk_penalty, 0)}</td>
        <td title="${esc(r.reason)}" class="reason-cell"><div class="reason-line">${esc(reason)}</div></td>
      </tr>
      <tr id="${rowId}" class="detail-row hidden">
        <td colspan="9">
          <div class="detail-grid">
            <div><b>1着確率</b>: ${fmt(probs.firstPlaceProb, 4)}</div>
            <div><b>1着総合評価</b>: ${fmt(r.first_place_score, 2)}</div>
            <div><b>1着判定</b>: ${esc(r.first_place_gate || "MISSING")}</div>
            <div><b>2着総合評価</b>: ${fmt(r.second_place_score, 2)}</div>
            <div><b>2着判定</b>: ${esc(r.second_place_gate || "MISSING")}</div>
            <div><b>2着補正</b>: ${fmt(r.second_place_multiplier, 2)}x</div>
            <div><b>2着ソース</b>: ${esc(r.second_place_note || "不明")}</div>
            <div><b>3着総合評価</b>: ${fmt(r.third_place_score, 2)}</div>
            <div><b>3着判定</b>: ${esc(r.third_place_gate || "MISSING")}</div>
            <div><b>3着補正</b>: ${fmt(r.third_place_multiplier, 2)}x</div>
            <div><b>3着ソース</b>: ${esc(r.third_place_note || "不明")}</div>
            <div><b>総合評価</b>: ${fmt(r.race_score, 2)}</div>
            <div><b>レース判定</b>: ${esc(r.race_gate || "MISSING")}</div>
            <div><b>レース内訳</b>: ${fmt(r.race_first_confidence, 2)} / ${fmt(r.race_odds_balance_score, 2)} / ${fmt(r.race_data_quality_score, 2)}</div>
            <div><b>レースソース</b>: ${esc(r.race_note || "不明")}</div>
            <div><b>的中見込み</b>: ${fmt(probs.hitProb, 4)}</div>
            <div><b>校正後的中見込み</b>: ${fmt(r.calibrated_hit_prob_adjusted ?? r.calibrated_hit_prob, 4)}</div>
            <div><b>odds</b>: ${fmt(r.odds, 1)}</div>
            <div><b>odds_source</b>: ${esc(r.odds_source || "不明")}</div>
            <div><b>has_real_odds</b>: ${r.has_real_odds ? "true" : "false"}</div>
            <div><b>期待値</b>: ${fmt(r.ev, 3)}</div>
    <div><b>推奨比率</b>: ${r.bet_pct == null ? "0.00" : fmt(r.bet_pct, 2)}%</div>
            <div><b>推奨購入額</b>: ${fmtYen(r.bet_amount)}</div>
            <div><b>直前総合評価</b>: ${fmt(r.pre_race_score, 2)} (${esc(r.pre_race_gate || "MISSING")})</div>
            <div><b>直前内訳</b>: T${fmt(r.pre_race_time_score, 0)} / M${fmt(r.pre_race_motor_score, 0)} / R${fmt(r.pre_race_rank_score, 0)}</div>
            <div><b>直前補正</b>: ${fmt(r.pre_race_multiplier, 2)}x</div>
            <div><b>1着補正</b>: ${fmt(r.first_place_multiplier, 2)}x</div>
            <div><b>risk_penalty</b>: ${fmt(r.risk_penalty, 0)}</div>
            <div><b>confidence</b>: ${fmt(r.confidence_score, 3)}</div>
            <div><b>直前ソース</b>: ${esc(r.pre_race_source || "不明")}</div>
            <div><b>1着ソース</b>: ${esc(r.first_place_note || "不明")}</div>
            <div class="detail-reason"><b>理由</b>: ${esc(String(r.reason || "").replaceAll(" / ", " ・ "))}</div>
            <div><button class="copy-btn" data-tri="${esc(r.recommended_trifecta)}">買い目をコピー</button></div>
          </div>
        </td>
      </tr>`;
    })
    .join("");

  document.querySelectorAll(".main-row").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.classList.contains("copy-btn")) return;
      const id = row.getAttribute("data-detail");
      const detail = document.getElementById(id);
      if (detail) detail.classList.toggle("hidden");
    });
  });

  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const tri = btn.getAttribute("data-tri") || "";
      const original = btn.textContent || "コピー";
      try {
        await navigator.clipboard.writeText(tri);
        btn.textContent = "コピー済み";
        setTimeout(() => {
          btn.textContent = original;
        }, 1200);
      } catch {
        btn.textContent = "コピー失敗";
        setTimeout(() => {
          btn.textContent = original;
        }, 1200);
      }
    });
  });
}

function renderVenueSummary(rows) {
  if (!venueSummaryBody) return;
  if (!rows || rows.length === 0) {
    venueSummaryBody.innerHTML = `<tr><td colspan="8" class="muted">データなし</td></tr>`;
    return;
  }
  venueSummaryBody.innerHTML = rows
    .map((r) => {
      const hitText = r.buy_hit_rate != null
        ? `${fmtPct(r.buy_hit_rate, 1)} <span class="muted">(${r.buy_hits}/${r.buy_settled_n})</span>`
        : (r.buy_hit_rate_est != null ? `${fmtPct(r.buy_hit_rate_est, 1)} <span class="muted">(推定)</span>` : "データなし");
      const roiText = r.buy_roi != null
        ? fmt(r.buy_roi, 4)
        : (r.buy_roi_est != null ? `${fmt(r.buy_roi_est, 4)} <span class="muted">(推定)</span>` : "データなし");
      return `
      <tr>
        <td>${esc(r.venue_name || "-")}</td>
        <td>${esc(r.pred_count ?? "-")}</td>
        <td>${esc(r.buy_count ?? "-")}</td>
        <td>${esc(r.watch_count ?? "-")}</td>
        <td>${hitText}</td>
        <td>${roiText}</td>
        <td>${r.avg_buy_odds == null ? "データなし" : fmt(r.avg_buy_odds, 1)}</td>
        <td>${fmtPct(r.real_odds_rate, 1)}</td>
      </tr>
    `;
    })
    .join("");
}

function renderPerformanceBreakdown(data) {
  if (!decisionPerfBody || !oddsBandBody) return;
  const drows = data?.decision_stats || [];
  const orows = data?.odds_band_stats || [];

  decisionPerfBody.innerHTML = drows.length
    ? drows.map((r) => `
      <tr>
        <td>${esc(r.decision || "-")}</td>
        <td>${esc(r.count ?? "-")}</td>
        <td>${fmtPct(r.hit_rate_est, 1)}</td>
        <td>${r.roi_est == null ? "データなし" : fmt(r.roi_est, 4)}</td>
        <td>${r.avg_odds == null ? "データなし" : fmt(r.avg_odds, 1)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="5" class="muted">データなし</td></tr>`;

  oddsBandBody.innerHTML = orows.length
    ? orows.map((r) => `
      <tr>
        <td>${esc(r.band || "-")}</td>
        <td>${esc(r.count ?? "-")}</td>
        <td>${fmtPct(r.hit_rate_est, 1)}</td>
        <td>${r.roi_est == null ? "データなし" : fmt(r.roi_est, 4)}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="4" class="muted">データなし</td></tr>`;
}

async function loadVenueOptions() {
  if (!venueFilter) return;
  try {
    const res = await getJson("/api/venues");
    const venues = (res.venues || []).filter(Boolean);
    const current = venueFilter.value || "";
    venueFilter.innerHTML = `<option value="">全場</option>` + venues.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
    if (current && venues.includes(current)) venueFilter.value = current;
  } catch (e) {
    console.warn("venue options load failed", e);
  }
}

function renderExperiments(rows) {
  const cleaned = rows
    .map((r) => {
      const readable = formatRunName(r.run_id);
      const ts = fmtIsoDateParts(r.generated_at);
      return { ...r, readable, ts };
    });

  expBody.innerHTML = cleaned.length
    ? cleaned
        .map(
          (r) => `
          <tr>
            <td>
              <div class="exp-datetime">
                <span class="exp-date">${esc(r.ts.date)}</span>
                <span class="exp-time">${esc(r.ts.time)}</span>
              </div>
            </td>
            <td>
              <div class="exp-name">${esc(r.readable)}</div>
              <div class="exp-window">対象: ${esc(r.window || "-")}</div>
            </td>
            <td>${fmtOrNA(r.exact_hit_rate, 4)}</td>
            <td>${fmtOrNA(r.roi, 4)}</td>
          </tr>`
        )
        .join("")
    : `<tr><td colspan="4" class="muted">実験ログはまだありません</td></tr>`;
}

function wireModeToggle() {
  modeNormalBtn?.addEventListener("click", () => {
    setPreferredStrategyMode("NORMAL");
  });
  modeWinrateBtn?.addEventListener("click", () => {
    setPreferredStrategyMode("WINRATE");
  });
  modeRoiFilterBtn?.addEventListener("click", () => {
    setPreferredStrategyMode("ROI_FILTER");
  });
  modeAutoFilterBtn?.addEventListener("click", () => {
    setPreferredStrategyMode("AUTO_FILTER");
  });
}

async function loadAll() {
  await loadVenueOptions();
  const dateDigits = (dateFrom?.value || "").replace(/-/g, "") || todayDigits();
  if (dateFrom && !dateFrom.value) {
    dateFrom.value = `${dateDigits.slice(0, 4)}-${dateDigits.slice(4, 6)}-${dateDigits.slice(6, 8)}`;
  }
  const jcd = String(venueFilter?.value || "01").padStart(2, "0");
  const viewModel = await getJson(`/api/raceyosou?date=${encodeURIComponent(dateDigits)}&jcd=${encodeURIComponent(jcd)}`);
  lastSummary = {
    date: viewModel?.date || "",
    updated_at: viewModel?.updatedAt || "",
    target_races: Array.isArray(viewModel?.races) ? viewModel.races.length : 0,
    predictions_total: flattenRaceyosouRows(viewModel).length,
    buy_count: flattenRaceyosouRows(viewModel).filter((r) => r.decision === "BUY").length,
    watch_count: flattenRaceyosouRows(viewModel).filter((r) => r.decision === "WATCH").length,
    skip_count: flattenRaceyosouRows(viewModel).filter((r) => r.decision === "SKIP").length,
    real_odds_rate: null,
    real_odds_coverage: null,
    decision_counts: {
      BUY: flattenRaceyosouRows(viewModel).filter((r) => r.decision === "BUY").length,
      WATCH: flattenRaceyosouRows(viewModel).filter((r) => r.decision === "WATCH").length,
      SKIP: flattenRaceyosouRows(viewModel).filter((r) => r.decision === "SKIP").length,
    },
    recent30_trifecta: { buy: 0, hits: 0, hit_rate: null, hit_rate_ci: [null, null], roi: null, confidence: "-", window_races: 0, window_days: 0, sample_note: "" },
    recent30_exacta: { buy: 0, hits: 0, hit_rate: null, hit_rate_ci: [null, null], roi: null, confidence: "-" },
    strategy_mode: "MVP",
    effective_strategy_mode: "MVP",
    race_yosou_view: viewModel,
    ops_health: {},
    gate_health: {},
    upstream_health: {},
  };
  renderKpi(lastSummary);
  renderGateHealth(lastSummary);
  renderOpsHealth(lastSummary);
  renderUpstreamHealth(lastSummary);
  renderRaceYosouView(viewModel);

  const rows = flattenRaceyosouRows(viewModel);
  renderSpotlight(rows);
  renderPredictions(rows);
  renderVenueSummary([]);
  renderPerformanceBreakdown({});
  renderExperiments([]);
}

async function initDefaultTodayFilter() {
  if (!filterToday) return;
  await applyTodayOrLatestDate();
}

applyBtn.addEventListener("click", loadAll);
refreshBtn.addEventListener("click", loadAll);
runPredictBtn?.addEventListener("click", () => runOpsMode("predict"));
runPreRaceBtn?.addEventListener("click", () => runOpsMode("pre-race"));
runOddsBtn?.addEventListener("click", () => runOpsMode("odds-refresh"));
runPostRaceBtn?.addEventListener("click", () => runOpsMode("post-race"));
runBacktestBtn?.addEventListener("click", () => runOpsMode("backtest"));
runGuardBtn?.addEventListener("click", () => runOpsMode("guard"));
runFullBtn?.addEventListener("click", () => runOpsMode("full"));
oddsUploadBtn?.addEventListener("click", async () => {
  const file = oddsUploadInput?.files?.[0];
  if (!file) {
    window.alert("先に実オッズCSVを選んでください。");
    return;
  }
  if (!window.confirm(`実オッズCSVを反映しますか？\n${file.name}`)) return;
  const previous = opsRunDetail?.textContent || "最新ログ: -";
  if (opsRunDetail) opsRunDetail.textContent = "最新ログ: 実オッズCSVを反映中...";
  if (oddsUploadBtn) oddsUploadBtn.disabled = true;
  if (oddsUploadInput) oddsUploadInput.disabled = true;
  try {
    const payload = await uploadOddsCsv(file);
    const result = payload?.result || {};
    if (opsRunDetail) {
      opsRunDetail.textContent = `最新ログ: 実オッズCSV反映 rows=${result.rows ?? "-"} / races=${result.race_count ?? "-"} / updated=${result.updated_at ?? "-"}`;
    }
    if (oddsUploadInput) oddsUploadInput.value = "";
    await loadAll();
  } catch (e) {
    const message = e?.message || String(e);
    window.alert(`実オッズCSVの反映に失敗しました。\n${message}`);
    if (opsRunDetail) opsRunDetail.textContent = previous;
  } finally {
    if (oddsUploadBtn) oddsUploadBtn.disabled = false;
    if (oddsUploadInput) oddsUploadInput.disabled = false;
  }
});
venueFilter?.addEventListener("change", async () => {
  if (filterToday) await applyTodayOrLatestDate();
  await loadAll();
});
venueWindow?.addEventListener("change", loadAll);
todayBtn.addEventListener("click", () => {
  filterToday = true;
  applyTodayOrLatestDate()
    .then(() => loadAll())
    .catch((e) => {
      console.error(e);
      alert(`読み込み失敗: ${e.message}`);
    });
});

initDefaultTodayFilter()
  .then(() => loadAll())
  .then(() => refreshOpsRunStatus())
  .then(() => refreshOpsRunReport())
  .catch((e) => {
    console.error(e);
    alert(`読み込み失敗: ${e.message}`);
  });

wireModeToggle();

if (opsRunStatus) {
  opsRunStatus.title = "操作中はボタンが無効になります";
}
