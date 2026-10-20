import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toPng } from "html-to-image";
import Papa from "papaparse";
import { IconCamera, IconDownload, IconExternal, IconPrinter } from "./Icons.jsx";

const LOCAL_CSV_URL =
  typeof __LOCAL_CSV_URL__ === "string" ? __LOCAL_CSV_URL__ : "";

const WORKFLOW_PHASES = [
  "Inception",
  "Planning",
  "Development",
  "Stabilization",
  "Freeze",
  "Ratification-Ready",
  "Specification in Publication",
];

const DISPLAY_PHASES = [
  "Planning",
  "Development",
  "Stabilization",
  "Freeze",
  "Ratification-Ready",
  "Specification in Publication",
];

// CSS class carrying a phase colour (--ph), shared by the table and the summary
// dialog: "Specification in Publication" -> "ph-publication".
function phaseClass(phase) {
  const name = phase === "Specification in Publication" ? "Publication" : String(phase || "");
  return name ? `ph-${name.toLowerCase().replace(/[^a-z]+/g, "-")}` : "";
}

function normalizeStatus(value) {
  if (!value) return "";
  const v = String(value);
  const lower = v.toLowerCase();

  if (lower.includes("ratification-ready") || lower.includes("rat-ready")) {
    return "Ratification-Ready";
  }
  if (lower.includes("specification in publication") || lower.includes("publication")) {
    return "Specification in Publication";
  }
  if (lower.includes("freeze")) return "Freeze";
  if (lower.includes("stabilization")) return "Stabilization";
  if (lower.includes("under development") || lower.includes("development")) {
    return "Development";
  }
  if (lower.includes("planning")) return "Planning";
  if (lower.includes("inception")) return "Inception";
  if (lower.includes("cancelled")) return "Cancelled";

  return v;
}

function calculateProgress(status) {
  if (!status) {
    return { currentPhase: "", nextPhase: "" };
  }

  const normalized = normalizeStatus(status);
  const idx = WORKFLOW_PHASES.indexOf(normalized);
  if (idx === -1) {
    return { currentPhase: normalized, nextPhase: "" };
  }

  const nextPhase = idx + 1 < WORKFLOW_PHASES.length ? WORKFLOW_PHASES[idx + 1] : "Ratified";
  return { currentPhase: normalized, nextPhase };
}

const ARC_REVIEW_APPROVED_STATES = new Set([
  "approved",
  "ar approved",
  "ar review not required",
  "approval not required",
  "not required",
  "done",
]);

const ARC_REVIEW_IN_PROGRESS_STATES = new Set([
  "approval in progress",
  "in progress",
  "in review",
  "under review",
  "ar review in progress",
]);

const PUBLIC_REVIEW_DONE_STATES = new Set([
  "public review done",
  "public review not required",
  "approved",
  "not required",
  "done",
]);

const PUBLIC_REVIEW_IN_PROGRESS_STATES = new Set([
  "public review in progress",
  "in progress",
  "in review",
  "under review",
]);

const TSC_APPROVAL_APPROVED_STATES = new Set([
  "approved",
  "approval not required",
  "not required",
  "done",
]);

const TSC_APPROVAL_IN_PROGRESS_STATES = new Set([
  "approval in progress",
  "in progress",
  "in review",
  "under review",
]);

const BOD_APPROVAL_APPROVED_STATES = new Set([
  "approved",
  "approval not required",
  "not required",
  "done",
]);

const BOD_APPROVAL_IN_PROGRESS_STATES = new Set([
  "approval in progress",
  "in progress",
  "in review",
  "under review",
]);

function getBodApprovalState(row) {
  const raw = String(row.bodApprovalStatus || "").trim();
  const lowered = raw.toLowerCase();

  if (BOD_APPROVAL_APPROVED_STATES.has(lowered)) {
    return { kind: "completed", label: raw };
  }
  if (BOD_APPROVAL_IN_PROGRESS_STATES.has(lowered)) {
    return { kind: "in-progress", label: raw };
  }
  return { kind: "upcoming", label: raw || "Not Started" };
}

function getTscApprovalState(row) {
  const raw = String(row.tscApprovalStatus || "").trim();
  const lowered = raw.toLowerCase();

  if (TSC_APPROVAL_APPROVED_STATES.has(lowered)) {
    return { kind: "completed", label: raw };
  }
  if (TSC_APPROVAL_IN_PROGRESS_STATES.has(lowered)) {
    return { kind: "in-progress", label: raw };
  }
  return { kind: "upcoming", label: raw || "Not Started" };
}

function getPublicReviewState(row) {
  const raw = String(row.publicReviewStatus || "").trim();
  const lowered = raw.toLowerCase();

  if (PUBLIC_REVIEW_DONE_STATES.has(lowered)) {
    return { kind: "completed", label: raw };
  }
  if (PUBLIC_REVIEW_IN_PROGRESS_STATES.has(lowered)) {
    return { kind: "in-progress", label: raw };
  }
  return { kind: "upcoming", label: raw || "Not Started" };
}

function getArcReviewState(row) {
  const raw = String(row.arcReviewStatus || "").trim();
  const lowered = raw.toLowerCase();

  if (ARC_REVIEW_APPROVED_STATES.has(lowered)) {
    return { kind: "completed", label: raw };
  }
  if (ARC_REVIEW_IN_PROGRESS_STATES.has(lowered)) {
    return { kind: "in-progress", label: raw };
  }
  return { kind: "upcoming", label: raw || "Not Started" };
}

// Ordering requested for the dashboard: specs furthest along the lifecycle
// float to the top. Within Ratification-Ready, a completed TSC Approval comes
// first, then a TSC vote in progress, then the same ordering for Public Review
// (completed, in progress, "not started"); within Freeze, the same ordering
// applies to the ARC review. Lower rank sorts first.
function getPhaseSortRank(row) {
  const phase = row.currentPhase;
  if (phase === "Specification in Publication") {
    const bod = getBodApprovalState(row).kind;
    if (bod === "completed") return 0; // Publication with BoD approval
    if (bod === "in-progress") return 1; // Publication with BoD approval running
    return 2; // Publication with pending tasks and BoD approval
  }
  if (phase === "Ratification-Ready") {
    const tsc = getTscApprovalState(row).kind;
    if (tsc === "completed") return 3; // Rat-Ready with TSC approval
    if (tsc === "in-progress") return 4; // Rat-Ready with TSC vote running
    const pr = getPublicReviewState(row).kind;
    if (pr === "completed") return 5; // Rat-Ready with Public Review done
    if (pr === "in-progress") return 6; // Rat-Ready with Public Review running
    return 7; // Rat-Ready with pending tasks and Public Review
  }
  if (phase === "Freeze") {
    const arc = getArcReviewState(row).kind;
    if (arc === "completed") return 8; // Freeze with ARC approval
    if (arc === "in-progress") return 9; // Freeze with pending ARC review
    return 10; // Freeze with pending tasks and ARC review
  }
  if (phase === "Stabilization") return 11;
  if (phase === "Development") return 12;
  if (phase === "Planning") return 13;
  return 14;
}

function isBodReport(value) {
  if (value === null || value === undefined) return false;
  const normalized = String(value).trim().toLowerCase();
  if (!normalized) return false;
  if (["yes", "true", "y", "1"].includes(normalized)) return true;
  if (["no", "false", "n", "0"].includes(normalized)) return false;
  return normalized.includes("yes");
}

function normalizeProgressClass(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/\s+/g, "-");
  if (!normalized) return "";
  return normalized;
}

function parseQuarter(value) {
  if (!value) return { year: 9999, qtr: 9 };
  const text = String(value).trim();
  const match = text.match(/(\d{2,4})\D*([1-4])/);
  if (!match) return { year: 9999, qtr: 9 };
  let year = parseInt(match[1], 10);
  if (year < 100) year += 2000;
  const qtr = parseInt(match[2], 10);
  return { year, qtr };
}

function isSetQuarter(value) {
  const text = String(value || "").trim().toLowerCase();
  return text !== "" && !text.startsWith("not set");
}

// Quarters between the phase plan and the target (positive = behind plan).
function quarterSlip(plan, target) {
  if (!isSetQuarter(plan) || !isSetQuarter(target)) return null;
  const p = parseQuarter(plan);
  const t = parseQuarter(target);
  if (p.year === 9999 || t.year === 9999) return null;
  return (t.year * 4 + t.qtr) - (p.year * 4 + p.qtr);
}

function statusClassName(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "not set" || normalized === "not set yet") {
    return "status-cell not-set";
  }
  const classValue = normalizeProgressClass(normalized);
  return classValue ? `status-cell ${classValue}` : "status-cell";
}

function phaseStatusTitle(row) {
  const slip = quarterSlip(row.planQuarter, row.trendingQuarter);
  if (slip === null) return undefined;
  return slipText(slip, "the start of the current phase");
}

function slipText(slip, reference) {
  if (slip <= 0) return `Projected is on or ahead of ${reference}`;
  return `Projected is ${slip} quarter${slip === 1 ? "" : "s"} behind ${reference}`;
}

// Same rule as Ratification Progress, measured from the original commitment.
function baselineStatus(row) {
  const slip = quarterSlip(row.plannedQuarter, row.trendingQuarter);
  if (slip === null) return { label: "Not Set", className: "status-cell not-set", rowClass: "" };
  const title = slipText(slip, "Baseline");
  if (slip <= 0) return { label: "On Track", className: "status-cell on-track", rowClass: "on-track", title };
  const label = `${slip === 1 ? "Exposed" : "Late"} (${slip}Q)`;
  const rowClass = slip === 1 ? "exposed" : "late";
  return { label, className: `status-cell ${rowClass}`, rowClass, title };
}

const TREND_WINDOW_DAYS = 90;

function AboutLink({ onOpen, label, light = false }) {
  return (
    <button
      type="button"
      className={`about-trend-link${light ? " light" : ""}`}
      aria-label={label}
      title={label}
      onClick={onOpen}
    >
      ⓘ
    </button>
  );
}

// Direction of the last Projected change, if it happened in the last 90 days.
function projectedTrend(row, now = new Date()) {
  const slip = quarterSlip(row.previousTrendingQuarter, row.trendingQuarter);
  const changed = row.targetChanged ? new Date(row.targetChanged) : null;
  const recent = changed && !Number.isNaN(changed.getTime()) &&
    (now - changed) / 86400000 <= TREND_WINDOW_DAYS;
  if (slip === null || slip === 0 || !recent) {
    return { label: "→", name: "Steady", className: "trend-steady", title: "Steady: no change to Projected in the last 90 days" };
  }
  const title = `Projected moved from ${row.previousTrendingQuarter} to ${row.trendingQuarter} on ${row.targetChanged}`;
  if (slip > 0) return { label: "↓", name: "Slipping", className: "trend-slipping", title: `Slipping: ${title}` };
  return { label: "↑", name: "Improving", className: "trend-improving", title: `Improving: ${title}` };
}

const BASELINE_QUARTER_TITLE =
  "Original commitment, set when the Ratification Plan was approved. It never changes.";
const PHASE_START_QUARTER_TITLE =
  "Projected quarter from the first plan made in the current phase. " +
  "It is reset only when the specification moves to a new phase.";
const PROJECTED_QUARTER_TITLE =
  "Today's forecast. It moves every time the specification is replanned.";

function getCurrentQuarter(date = new Date()) {
  const month = date.getMonth() + 1;
  return Math.ceil(month / 3);
}

function getDaysLeftInQuarter(date = new Date()) {
  const currentQuarter = getCurrentQuarter(date);
  let endOfQuarter;
  switch (currentQuarter) {
    case 1:
      endOfQuarter = new Date(date.getFullYear(), 2, 31);
      break;
    case 2:
      endOfQuarter = new Date(date.getFullYear(), 5, 30);
      break;
    case 3:
      endOfQuarter = new Date(date.getFullYear(), 8, 30);
      break;
    case 4:
    default:
      endOfQuarter = new Date(date.getFullYear(), 11, 31);
      break;
  }
  const timeDiff = endOfQuarter - date;
  return Math.ceil(timeDiff / (1000 * 60 * 60 * 24));
}

function getYearProgressFraction(date = new Date()) {
  const startOfYear = new Date(date.getFullYear(), 0, 1);
  const startOfNextYear = new Date(date.getFullYear() + 1, 0, 1);
  const elapsed = date - startOfYear;
  const total = startOfNextYear - startOfYear;
  return Math.min(Math.max(elapsed / total, 0), 1);
}

function getQuarterEndFractionOfYear(quarter, year = new Date().getFullYear()) {
  const startOfYear = new Date(year, 0, 1);
  const startOfNextYear = new Date(year + 1, 0, 1);
  const quarterEndMonths = { 1: 2, 2: 5, 3: 8, 4: 11 };
  const quarterEndDays = { 1: 31, 2: 30, 3: 30, 4: 31 };
  const endOfQuarter = new Date(
    year,
    quarterEndMonths[quarter],
    quarterEndDays[quarter],
    23,
    59,
    59,
    999,
  );
  return (endOfQuarter - startOfYear) / (startOfNextYear - startOfYear);
}

function getDaysLeftInYear(date = new Date()) {
  const endOfYear = new Date(date.getFullYear(), 11, 31);
  const timeDiff = endOfYear - date;
  return Math.max(Math.ceil(timeDiff / (1000 * 60 * 60 * 24)), 0);
}

function getDaysLeftInMonth(date = new Date()) {
  const lastDay = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  return Math.max(lastDay - date.getDate(), 0);
}

function formatMonthDay(date = new Date()) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(date);
}

function formatFilenameTimestamp(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return (
    date.getFullYear() +
    "-" +
    pad(date.getMonth() + 1) +
    "-" +
    pad(date.getDate()) +
    "_" +
    pad(date.getHours()) +
    "-" +
    pad(date.getMinutes()) +
    "-" +
    pad(date.getSeconds())
  );
}

function addResizers(table) {
  if (!table) return;
  const cols = table.querySelectorAll("th");
  cols.forEach((col) => {
    if (col.querySelector(".resizer")) return;
    const resizer = document.createElement("div");
    resizer.className = "resizer";
    col.appendChild(resizer);
    resizer.addEventListener("mousedown", initResize);
  });
}

let startX = 0;
let startWidth = 0;
let activeColumn = null;

function initResize(e) {
  activeColumn = e.target.parentElement;
  startX = e.clientX;
  startWidth = parseInt(window.getComputedStyle(activeColumn).width, 10);
  document.documentElement.addEventListener("mousemove", doResize);
  document.documentElement.addEventListener("mouseup", stopResize);
}

function doResize(e) {
  if (!activeColumn) return;
  activeColumn.style.width = startWidth + e.clientX - startX + "px";
}

function stopResize() {
  document.documentElement.removeEventListener("mousemove", doResize);
  document.documentElement.removeEventListener("mouseup", stopResize);
  activeColumn = null;
}

function normalizeRow(raw) {
  const status = raw["Status"] || "";
  const planned = raw["Baseline Ratification Quarter"] || raw["Planned Ratification Quarter"] || "";
  const trending = raw["Target Ratification Quarter"] || raw["Trending Ratification Quarter"] || "";
  const bodReport = raw["BoD Report"] || "";
  const { currentPhase, nextPhase } = calculateProgress(status);

  return {
    jiraUrl: raw["Jira URL"] || "",
    summary: raw["Summary"] || "",
    status,
    updated: raw["Updated"] || "",
    isaOrNonIsa: raw["ISA or NON-ISA?"] || "",
    plannedQuarter: planned,
    planQuarter: isSetQuarter(raw["Plan Ratification Quarter"]) ? raw["Plan Ratification Quarter"] : "",
    trendingQuarter: trending,
    previousTrendingQuarter: raw["Previous Target Ratification Quarter"] || "",
    targetChanged: raw["Target Changed"] || "",
    ratificationProgress: raw["Ratification Progress"] || "",
    previousRatificationProgress: raw["Previous Ratification Progress"] || "",
    github: raw["GitHub"] || "",
    bodReport,
    bodFlag: isBodReport(bodReport),
    arcReviewStatus: raw["ARC Review Status"] || "",
    publicReviewStatus: raw["Public Review Status"] || "",
    tscApprovalStatus: raw["TSC Approval Status"] || "",
    bodApprovalStatus: raw["BoD Approval Status"] || "",
    phaseTasksStatus: raw["Phase Tasks Status"] || "",
    fastTrack: /^(yes|true|y|1)$/i.test(String(raw["Fast Track"] || "").trim()),
    lastContribution: raw["Last Contribution"] || "",
    lastContributionSource: raw["Last Contribution Source"] || "",
    phaseStarted: raw["Phase Started"] || "",
    developmentStarted: raw["Development Started"] || "",
    nextApproval: raw["Next Approval"] || "",
    nextApprovalDate: raw["Next Approval Date"] || "",
    latestReplanning: raw["Latest Replanning"] || "",
    phaseHistory: parseDatePairs(raw["Phase History"]),
    projectedPhaseEnds: parseDatePairs(raw["Projected Phase Ends"]),
    currentPhase,
    nextPhase,
  };
}

// "Planning=2023-07-07;Development=2024-08-28" -> { Planning: "2023-07-07", ... }
function parseDatePairs(value) {
  const out = {};
  String(value || "")
    .split(";")
    .forEach((pair) => {
      const [name, date] = pair.split("=");
      if (name && date) out[name.trim()] = date.trim();
    });
  return out;
}

// The six phases of the milestone line; each is one sixth of the way.
const TIMELINE_PHASES = [
  { name: "Planning", short: "Plan" },
  { name: "Development", short: "Dev" },
  { name: "Stabilization", short: "Stab." },
  { name: "Freeze", short: "Freeze" },
  { name: "Ratification-Ready", short: "Rat-Ready" },
  { name: "Publication", short: "Publication" },
];

function timelineIndex(row) {
  const phase = row.currentPhase === "Specification in Publication" ? "Publication" : row.currentPhase;
  return TIMELINE_PHASES.findIndex((p) => p.name === phase);
}

// Completion by phase: the phases already passed, out of six.
function phaseCompletion(row) {
  const index = timelineIndex(row);
  if (index < 0) return { percent: 0, label: "Not started (Inception)" };
  return {
    percent: Math.round((index / TIMELINE_PHASES.length) * 100),
    label: `Phase ${index + 1} of ${TIMELINE_PHASES.length}, ${TIMELINE_PHASES[index].name}`,
  };
}

function timelineSteps(row) {
  const current = timelineIndex(row);
  const steps = TIMELINE_PHASES.map((phase, i) => {
    const state = current < 0 || i > current ? "upcoming" : i < current ? "done" : "current";
    if (state !== "upcoming") {
      // A passed phase with no history was skipped (some fast tracks) or predates Jira.
      const date = row.phaseHistory[phase.name] || "";
      return { ...phase, state, date, title: date ? `${phase.name}: started ${date}` : `${phase.name}: no start date in Jira` };
    }
    const previous = TIMELINE_PHASES[i - 1];
    const date = previous ? row.projectedPhaseEnds[previous.name] || "" : "";
    return { ...phase, state, date, projected: true, title: date ? `${phase.name}: projected to start ${date}` : `${phase.name}: no projected date` };
  });
  // A passed phase first entered after a later one was skipped at the time and
  // only visited afterwards (e.g. Development -> Freeze -> back to Stabilization).
  let earliestLater = "";
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (step.projected || !step.date) continue;
    if (earliestLater && step.date > earliestLater) {
      steps[i] = { ...step, date: "", skipped: true, title: `${step.name}: skipped (first entered ${step.date})` };
    } else {
      earliestLater = step.date;
    }
  }
  const ratified = row.projectedPhaseEnds.Publication || "";
  steps.push({
    name: "Ratified",
    short: "Ratified",
    state: "upcoming",
    date: ratified,
    projected: true,
    title: ratified ? `Board ratification projected ${ratified}` : "Board ratification: no projected date",
  });
  return steps;
}

function PhaseTimeline({ row }) {
  const steps = timelineSteps(row);
  const completion = phaseCompletion(row);
  return (
    <div className="phase-timeline">
      <div className="phase-timeline-head">
        <span className="phase-timeline-percent">{completion.percent}%</span>
        <span className="phase-timeline-label">complete · {completion.label}</span>
      </div>
      <ol className="phase-timeline-steps">
        {steps.map((step) => (
          <li key={step.name} className={`phase-step phase-${step.state} ${phaseClass(step.name)}`} title={step.title}>
            <span className="phase-dot" aria-hidden="true">
              {step.state === "done" ? "✓" : ""}
            </span>
            <span className="phase-name">{step.short}</span>
            <span className={`phase-date${step.projected ? " projected" : ""}`}>
              {step.date ? `${step.projected ? "~" : ""}${step.date}` : step.skipped ? "skipped" : "–"}
            </span>
            <span className="visually-hidden">{step.title}</span>
          </li>
        ))}
      </ol>
      <div className="phase-timeline-note">Dates are when a phase started; ~ marks a projected date.</div>
    </div>
  );
}

function specKeyOf(row) {
  return String(row.jiraUrl || "").split("/").pop();
}

// "2y 01m", "4m", "12d": the age of a date, for the summary's key dates.
function formatAge(value) {
  const days = daysSince(value);
  if (days === null) return "";
  if (days < 31) return `${days}d`;
  const years = Math.floor(days / 365);
  const months = Math.floor((days % 365) / 30.44);
  return years ? `${years}y ${String(months).padStart(2, "0")}m` : `${months}m`;
}

function formatAgo(value) {
  return daysSince(value) === 0 ? "today" : `${formatAge(value)} ago`;
}

function summaryKeyDates(row) {
  const items = [];
  if (row.phaseStarted) {
    items.push(["Current phase started", `${row.phaseStarted} · ${formatAgo(row.phaseStarted)}`]);
  }
  if (row.developmentStarted) {
    items.push([
      "Development started",
      `${row.developmentStarted} · ${daysSince(row.developmentStarted)} days (${formatAge(row.developmentStarted)})`,
    ]);
  }
  if (row.nextApproval) {
    items.push(["Next approval", `${row.nextApproval}${row.nextApprovalDate ? `, projected ${row.nextApprovalDate}` : ""}`]);
  }
  if (row.latestReplanning) {
    items.push(["Projected dates last set", `${row.latestReplanning} · ${formatAgo(row.latestReplanning)}`]);
  }
  return items;
}

function summaryShareUrl(key) {
  const url = new URL(window.location.href);
  url.search = "";
  url.hash = "";
  url.searchParams.set("spec", key);
  return url.toString();
}

function summaryShareText(row, entry) {
  const dates = summaryKeyDates(row).map(([label, value]) => `- ${label}: ${value}`);
  const completion = phaseCompletion(row);
  return [
    `${row.summary} (${specKeyOf(row)})`,
    `Progress: ${completion.percent}% complete (${completion.label})`,
    "",
    entry.summary,
    ...(dates.length ? ["", ...dates] : []),
    "",
    `Dashboard: ${summaryShareUrl(specKeyOf(row))}`,
    `Jira: ${row.jiraUrl}`,
  ].join("\n");
}

function SummaryIcon() {
  return (
    <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true">
      <circle cx="10" cy="10" r="10" className="summary-icon-bg" />
      <path
        d="M6 6.5h8M6 10h8M6 13.5h5"
        stroke="#fff"
        strokeWidth="1.6"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

function SummaryDialog({ row, entry, onClose }) {
  const [copied, setCopied] = useState("");
  const dialogRef = useRef(null);
  const key = specKeyOf(row);

  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = async (text, label) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
    } catch {
      setCopied("Copy failed");
    }
    setTimeout(() => setCopied(""), 2000);
  };

  const text = summaryShareText(row, entry);
  const share = async () => {
    try {
      await navigator.share({ title: row.summary, text, url: summaryShareUrl(key) });
    } catch {
      // Dismissed by the viewer; nothing to do.
    }
  };
  const mailto = `mailto:?subject=${encodeURIComponent(`Specification summary: ${row.summary}`)}&body=${encodeURIComponent(text)}`;
  const keyDates = summaryKeyDates(row);

  return (
    <div className="summary-overlay" onClick={onClose}>
      <div
        className="summary-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="summary-dialog-title"
        tabIndex={-1}
        ref={dialogRef}
        onClick={(event) => event.stopPropagation()}
      >
        <button type="button" className="summary-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2 id="summary-dialog-title">
          <a href={row.jiraUrl} target="_blank" rel="noreferrer">
            {row.summary}
          </a>{" "}
          <span className="summary-key">{key}</span>
        </h2>
        <PhaseTimeline row={row} />
        <p className="summary-text">{entry.summary}</p>
        {keyDates.length ? (
          <dl className="summary-dates">
            {keyDates.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
        <div className="summary-meta">
          {entry.source === "ai"
            ? `AI generated from Jira data on ${entry.generated}. The text only restates Jira facts.`
            : `Built from Jira data on ${entry.generated}.`}
        </div>
        <div className="summary-actions">
          <button type="button" onClick={() => copy(summaryShareUrl(key), "Link copied")}>
            Copy link
          </button>
          <button type="button" onClick={() => copy(text, "Text copied")}>
            Copy text
          </button>
          <a className="summary-action-link" href={mailto}>
            Email
          </a>
          {typeof navigator.share === "function" ? (
            <button type="button" onClick={share}>
              Share…
            </button>
          ) : null}
          <span className="summary-copied" aria-live="polite">
            {copied}
          </span>
        </div>
      </div>
    </div>
  );
}

function daysSince(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.floor((Date.now() - date.getTime()) / (1000 * 60 * 60 * 24));
}

function formatRelativeAge(value) {
  const days = daysSince(value);
  if (days === null) return "";
  if (days <= 0) return "today";
  if (days < 31) return `${days}d ago`;
  if (days < 365) {
    const months = Math.round(days / 30);
    return `${months} mo ago`;
  }
  const years = Math.floor(days / 365);
  const months = Math.round((days % 365) / 30);
  return months ? `${years}y ${months}mo ago` : `${years}y ago`;
}

// Recency bucket driving the activity dot. Post-Freeze phases legitimately
// have little code activity, so they are marked neutral rather than "stale".
function activityRecencyClass(row) {
  const postFreeze =
    row.currentPhase === "Ratification-Ready" ||
    row.currentPhase === "Specification in Publication";
  const days = daysSince(row.lastContribution);
  if (days === null) return "activity-none";
  if (postFreeze) return "activity-neutral";
  if (days <= 14) return "activity-fresh";
  if (days <= 45) return "activity-slowing";
  return "activity-stale";
}

function getLatestReleaseUrl(rawUrl) {
  if (!rawUrl) return "";
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return "";
  }

  if (!["github.com", "www.github.com"].includes(parsed.hostname)) {
    return "";
  }

  const parts = parsed.pathname.split("/").filter(Boolean);
  if (parts.length < 2) return "";

  const owner = parts[0];
  const repo = parts[1].replace(/\.git$/, "");
  return `https://github.com/${owner}/${repo}/releases/latest`;
}

function getPhaseDisplay(row) {
  const currentIndex = WORKFLOW_PHASES.indexOf(row.currentPhase);
  const phaseStates = {};
  DISPLAY_PHASES.forEach((phase) => {
    const phaseIndex = WORKFLOW_PHASES.indexOf(phase);
    if (phase === row.currentPhase) {
      phaseStates[phase] = "In Progress";
    } else if (currentIndex >= 0 && phaseIndex < currentIndex) {
      phaseStates[phase] = "\u2713";
    } else {
      phaseStates[phase] = "...";
    }
  });
  return phaseStates;
}

function buildEmailBody(row, phases) {
  const lines = [
    "Specification Details:",
    "",
    `- Specification: ${row.summary || "N/A"}`,
    `- Jira Link: ${row.jiraUrl || "N/A"}`,
    `- ISA or NON-ISA?: ${row.isaOrNonIsa || "N/A"}`,
    `- Planning: ${phases["Planning"] || "N/A"}`,
    `- Development: ${phases["Development"] || "N/A"}`,
    `- Stabilization: ${phases["Stabilization"] || "N/A"}`,
    `- Freeze - ARC Approval: ${phases["ARC Review"] || "N/A"}`,
    `- Freeze - Tasks: ${phases["Freeze"] || "N/A"}`,
    `- Ratification-Ready - Public Review: ${phases["Public Review"] || "N/A"}`,
    `- Ratification-Ready - TSC Approval: ${phases["TSC Approval"] || "N/A"}`,
    `- Ratification-Ready - Tasks: ${phases["Ratification-Ready"] || "N/A"}`,
    `- Publication - BoD Approval: ${phases["BoD Approval"] || "N/A"}`,
    `- Publication - Tasks: ${phases["Specification in Publication"] || "N/A"}`,
    `- Baseline Quarter: ${row.plannedQuarter || "N/A"}`,
    `- Phase Start Quarter: ${row.planQuarter || "N/A"}`,
    `- Projected Quarter: ${row.trendingQuarter || "N/A"}`,
    `- Status vs Baseline: ${baselineStatus(row).label.replace(/ /g, " ")}`,
    `- Status vs Phase Start: ${row.ratificationProgress || "N/A"}`,
    `- Trend: ${projectedTrend(row).name}`,
    `- Previous Ratification Status: ${row.previousRatificationProgress || "N/A"}`,
    `- GitHub Link: ${row.github || "N/A"}`,
    "",
    "This email was generated using the RISC-V Specification Dashboard. For more information, visit the dashboard at: https://tech.riscv.org/bod.",
    "",
    "---",
  ];

  return lines.join("\n");
}

function formatUpdateDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// "NON-ISA" wraps at its hyphen in the narrow ISA? column. Title case with a
// non-breaking hyphen keeps it on one line; "ISA + Non-ISA" still wraps at spaces.
function formatIsaLabel(value) {
  return String(value || "").replace(/non-isa/gi, "Non\u2011ISA");
}

function getInitialBodOnly() {
  const params = new URLSearchParams(window.location.search);
  const filter = params.get("filter");
  return filter === "bod";
}

// Days from today to a YYYY-MM-DD date, in local time (negative = past).
function daysUntil(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return null;
  const target = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((target - today) / 86400000);
}

const STALE_DAYS = 90;

// Quick filters; they combine with AND, and with the search and BoD toggle.
const QUICK_FILTERS = [
  {
    id: "late",
    label: "Late vs baseline",
    title: "Projected is 2 or more quarters behind Baseline",
    test: (row) => baselineStatus(row).label.startsWith("Late"),
  },
  {
    id: "approval30",
    label: "Next approval \u2264 30 days",
    title: "Next approval projected within 30 days, or already overdue",
    test: (row) => {
      const days = daysUntil(row.nextApprovalDate);
      return days !== null && days <= 30;
    },
  },
  {
    id: "stale",
    label: `No activity ${STALE_DAYS}+ days`,
    title: `No contribution found in the last ${STALE_DAYS} days`,
    test: (row) => {
      const days = daysSince(row.lastContribution);
      return days === null || days > STALE_DAYS;
    },
  },
];

function getInitialQuickFilters() {
  const ids = (new URLSearchParams(window.location.search).get("f") || "").split(",");
  return QUICK_FILTERS.map((filter) => filter.id).filter((id) => ids.includes(id));
}

const APPROVAL_WINDOW_DAYS = 90;
const APPROVAL_GROUPS = [
  { id: "overdue", label: "Overdue", test: (days) => days < 0 },
  { id: "30", label: "0-30 days", test: (days) => days >= 0 && days <= 30 },
  { id: "60", label: "31-60 days", test: (days) => days > 30 && days <= 60 },
  { id: "90", label: "61-90 days", test: (days) => days > 60 && days <= APPROVAL_WINDOW_DAYS },
];

function groupUpcomingApprovals(rows) {
  const items = rows
    .map((row) => ({ row, days: daysUntil(row.nextApprovalDate) }))
    .filter((item) => item.days !== null && item.days <= APPROVAL_WINDOW_DAYS)
    .sort((a, b) => a.days - b.days || a.row.summary.localeCompare(b.row.summary));
  return APPROVAL_GROUPS.map((group) => ({
    ...group,
    items: items.filter((item) => group.test(item.days)),
  })).filter((group) => group.items.length);
}

const EXPORT_COLUMNS = [
  ["Key", (row) => specKeyOf(row)],
  ["Specification", (row) => row.summary],
  ["Jira URL", (row) => row.jiraUrl],
  ["ISA or NON-ISA", (row) => row.isaOrNonIsa],
  ["Status", (row) => row.status],
  ["Phase Tasks Status", (row) => row.phaseTasksStatus],
  ["ARC Review Status", (row) => row.arcReviewStatus],
  ["Public Review Status", (row) => row.publicReviewStatus],
  ["TSC Approval Status", (row) => row.tscApprovalStatus],
  ["BoD Approval Status", (row) => row.bodApprovalStatus],
  ["Baseline Quarter", (row) => row.plannedQuarter],
  ["Phase Start Quarter", (row) => row.planQuarter],
  ["Projected Quarter", (row) => row.trendingQuarter],
  ["Status vs Baseline", (row) => baselineStatus(row).label],
  ["Status vs Phase Start", (row) => row.ratificationProgress],
  ["Trend", (row) => projectedTrend(row).name],
  ["Next Approval", (row) => row.nextApproval],
  ["Next Approval Date", (row) => row.nextApprovalDate],
  ["Phase Started", (row) => row.phaseStarted],
  ["Development Started", (row) => row.developmentStarted],
  ["Latest Replanning", (row) => row.latestReplanning],
  ["Last Contribution", (row) => formatUpdateDate(row.lastContribution)],
  ["Fast Track", (row) => (row.fastTrack ? "Yes" : "No")],
  ["BoD Report", (row) => (row.bodFlag ? "Yes" : "No")],
  ["GitHub", (row) => row.github],
];

function downloadCsv(rows) {
  const csv = Papa.unparse({
    fields: EXPORT_COLUMNS.map(([name]) => name),
    data: rows.map((row) => EXPORT_COLUMNS.map(([, value]) => value(row) ?? "")),
  }, { escapeFormulae: true }); // a Summary starting with = + - @ must not run as a formula
  // The BOM makes Excel read the file as UTF-8.
  const blob = new Blob(["\uFEFF", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `RISC-V_Specification_Dashboard_${formatFilenameTimestamp(new Date())}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function App() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [aboutTrendOpen, setAboutTrendOpen] = useState(false);
  // The About section sits below the table: open it, then scroll to it and
  // move the keyboard focus there once it has rendered open.
  const openAbout = () => {
    setAboutTrendOpen(true);
    requestAnimationFrame(() => {
      const about = document.getElementById("about-ratification");
      about?.scrollIntoView({ behavior: "smooth", block: "start" });
      about?.querySelector("summary")?.focus({ preventScroll: true });
    });
  };
  const [error, setError] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [bodOnly, setBodOnly] = useState(getInitialBodOnly);
  const [quickFilters, setQuickFilters] = useState(getInitialQuickFilters);
  const toggleQuickFilter = (id) =>
    setQuickFilters((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id]
    );
  const [showBackToTop, setShowBackToTop] = useState(false);
  const [summaries, setSummaries] = useState({});
  // Key of the spec whose summary dialog is open; ?spec=RVS-123 opens it on load.
  const [summaryKey, setSummaryKey] = useState(
    () => new URLSearchParams(window.location.search).get("spec") || ""
  );
  const closeSummary = useCallback(() => {
    setSummaryKey("");
    const url = new URL(window.location.href);
    if (url.searchParams.has("spec")) {
      url.searchParams.delete("spec");
      window.history.replaceState(null, "", url);
    }
  }, []);

  const tableRef = useRef(null);
  const tableContainerRef = useRef(null);
  const assetBase = import.meta.env.BASE_URL || "/";
  const latestCsvUrl = `${assetBase}latest.csv`;

  // Optional: written by ai-summaries.py in the Daily Data Fetch. The table
  // works without it, so a missing or bad file is ignored.
  useEffect(() => {
    let isMounted = true;
    fetch(`${assetBase}summaries.json`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : {}))
      .then((data) => {
        if (isMounted && data && typeof data === "object") setSummaries(data);
      })
      .catch(() => {});
    return () => {
      isMounted = false;
    };
  }, [assetBase]);

  // Optional: specs ratified this year, written by get-specs-data.py (the CSV
  // only holds specs still in progress). Hidden when missing.
  const [ratified, setRatified] = useState(null);
  useEffect(() => {
    let isMounted = true;
    fetch(`${assetBase}ratified.json`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (isMounted && data && Array.isArray(data.specs) && data.year === new Date().getFullYear()) {
          setRatified(data);
        }
      })
      .catch(() => {});
    return () => {
      isMounted = false;
    };
  }, [assetBase]);

  useEffect(() => {
    let isMounted = true;

    async function fetchData() {
      try {
        setLoading(true);
        setError("");

        async function tryFetchCsv(url, label) {
          if (!url) return "";
          try {
            const response = await fetch(url, { cache: "no-store" });
            if (response.ok) {
              return await response.text();
            }
            console.warn(`${label} not found (${response.status})`);
          } catch (err) {
            console.warn(`${label} fetch failed`, err);
          }
          return "";
        }

        let csvText = "";
        if (import.meta.env.DEV && LOCAL_CSV_URL) {
          csvText = await tryFetchCsv(LOCAL_CSV_URL, "Local CSV");
        }
        if (!csvText) {
          csvText = await tryFetchCsv(latestCsvUrl, "Deployed CSV");
        }
        if (!csvText) {
          throw new Error("Could not load the specification data (latest.csv).");
        }
        const parsed = Papa.parse(csvText, {
          header: true,
          skipEmptyLines: true,
          quoteChar: "'",
        });

        if (parsed.errors && parsed.errors.length) {
          console.warn("CSV parse warnings:", parsed.errors);
        }

        const normalized = (parsed.data || []).map(normalizeRow);
        const withoutCancelled = normalized.filter(
          (row) => !String(row.status || "").toLowerCase().includes("cancelled")
        );

        const progressOrder = {
          late: 0,
          exposed: 1,
          "on track": 2,
          completed: 3,
        };

        const sorted = withoutCancelled.sort((a, b) => {
          const phaseRankA = getPhaseSortRank(a);
          const phaseRankB = getPhaseSortRank(b);
          if (phaseRankA !== phaseRankB) return phaseRankA - phaseRankB;

          const progressAKey = String(a.ratificationProgress || "").toLowerCase();
          const progressBKey = String(b.ratificationProgress || "").toLowerCase();
          const progressA = progressOrder[progressAKey] ?? 99;
          const progressB = progressOrder[progressBKey] ?? 99;

          if (progressA !== progressB) return progressA - progressB;

          const quarterA = parseQuarter(a.trendingQuarter);
          const quarterB = parseQuarter(b.trendingQuarter);

          if (quarterA.year !== quarterB.year) return quarterA.year - quarterB.year;
          return quarterA.qtr - quarterB.qtr;
        });

        if (isMounted) {
          setRows(sorted);
        }
      } catch (err) {
        if (isMounted) {
          setError(err.message || "Failed to load data.");
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    }

    fetchData();

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => window.location.reload(), 3600 * 1000);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    function handleScroll() {
      const scrollPosition = window.scrollY + window.innerHeight;
      const pageHeight = document.documentElement.scrollHeight;
      setShowBackToTop(scrollPosition >= pageHeight - 240);
    }

    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (tableRef.current) {
      addResizers(tableRef.current);
    }
  }, [rows]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (bodOnly) {
      url.searchParams.set("filter", "bod");
    } else {
      url.searchParams.delete("filter");
    }
    if (quickFilters.length) {
      // Keep the commas readable in a shared link.
      url.searchParams.set("f", "__F__");
      url.search = url.search.replace("__F__", quickFilters.join(","));
    } else {
      url.searchParams.delete("f");
    }
    window.history.replaceState({}, "", url);
  }, [bodOnly, quickFilters]);

  const displayRows = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return rows.filter((row) => {
      const summary = String(row.summary || "").toLowerCase();
      const matchesSearch = !term || summary.includes(term);
      const matchesBod = !bodOnly || row.bodFlag;
      const matchesQuick = QUICK_FILTERS.every(
        (filter) => !quickFilters.includes(filter.id) || filter.test(row)
      );
      return matchesSearch && matchesBod && matchesQuick;
    });
  }, [rows, searchTerm, bodOnly, quickFilters]);

  const upcomingApprovals = useMemo(() => groupUpcomingApprovals(rows), [rows]);
  const upcomingCount = upcomingApprovals.reduce((total, group) => total + group.items.length, 0);

  const currentQuarter = getCurrentQuarter();
  const daysLeft = getDaysLeftInQuarter();
  const now = new Date();
  const monthLabel = formatMonthDay(now);
  const daysLeftInMonth = getDaysLeftInMonth(now);
  const daysLeftInYear = getDaysLeftInYear(now);
  const yearProgress = getYearProgressFraction() * 100;

  const handleSaveImage = () => {
    if (!tableContainerRef.current) return;
    const now = new Date();
    const fileName = `RISC-V_Specification_Dashboard_${formatFilenameTimestamp(
      now
    )}.png`;

    toPng(tableContainerRef.current, {
      quality: 1,
      pixelRatio: 4,
      backgroundColor: "#f3f5fa",
    })
      .then((dataUrl) => {
        const link = document.createElement("a");
        link.href = dataUrl;
        link.download = fileName;
        link.click();
      })
      .catch((err) => {
        console.error("Error generating image:", err);
      });
  };

  const handleShare = (row) => {
    const phases = getPhaseDisplay(row);
    const subject = `Specification Details: ${row.summary || "N/A"}`;
    const gateLabel = (state) =>
      state.kind === "completed"
        ? `\u2713${state.label ? ` (${state.label})` : ""}`
        : state.kind === "in-progress"
          ? `In Progress${state.label ? ` (${state.label})` : ""}`
          : "...";
    const arcLabel = gateLabel(getArcReviewState(row));
    const publicReviewLabel = gateLabel(getPublicReviewState(row));
    const tscApprovalLabel = gateLabel(getTscApprovalState(row));
    const bodApprovalLabel = gateLabel(getBodApprovalState(row));
    const body = buildEmailBody(row, {
      "Planning": phases["Planning"],
      "Development": phases["Development"],
      "Stabilization": phases["Stabilization"],
      "ARC Review": arcLabel,
      "Freeze": phases["Freeze"],
      "Public Review": publicReviewLabel,
      "TSC Approval": tscApprovalLabel,
      "Ratification-Ready": phases["Ratification-Ready"],
      "BoD Approval": bodApprovalLabel,
      "Specification in Publication": phases["Specification in Publication"],
    });

    const mailtoLink = `mailto:?subject=${encodeURIComponent(
      subject
    )}&body=${encodeURIComponent(body)}`;
    window.location.href = mailtoLink;
  };

  return (
    <div className="container">
      <header className="app-header">
        <div className="brand">
          <img src={`${assetBase}riscv.png`} alt="RISC-V logo" className="title-logo" />
          <span className="brand-divider" aria-hidden="true" />
          <div className="brand-text">
            <h1 className="page-title">RISC-V Specification Dashboard</h1>
            <p className="app-subtitle">
              Status of every RISC-V specification, from planning to ratification.
            </p>
          </div>
        </div>
        <div id="header" className="header-actions">
          <button id="save-button" type="button" className="btn" onClick={handleSaveImage}>
            <IconCamera />
            Save as Image
          </button>
        </div>
      </header>

      <section className="card year-card">
        <div className="card-header">
          <h2 className="card-title">{now.getFullYear()} progress</h2>
          <div className="card-actions">
            {ratified ? (
              <span
                className="count-chip strong"
                title={ratified.specs.map((spec) => `${spec.key} ${spec.summary} (${spec.date})`).join("\n")}
              >
                {ratified.count} ratified in {ratified.year}
              </span>
            ) : null}
            <span className="count-chip">
              Q{currentQuarter} · {daysLeft} days left
            </span>
            <span className="count-chip">
              {monthLabel} · {daysLeftInMonth} days left
            </span>
            <span className="count-chip">{daysLeftInYear} days left in year</span>
          </div>
        </div>
        <div className="card-body">
          <div className="timeline">
            <div className="timeline-fill">
              <div className="timeline-fill-past" style={{ width: `${yearProgress}%` }}></div>
            </div>
            {[1, 2, 3, 4].map((quarter) => {
              let pointState = "";
              if (quarter < currentQuarter) pointState = " completed";
              else if (quarter === currentQuarter) pointState = " current";
              return (
                <div
                  key={quarter}
                  className={`timeline-point${pointState}`}
                  style={{ left: `${getQuarterEndFractionOfYear(quarter) * 100}%` }}
                  title={quarter === currentQuarter ? `Q${quarter}: ${daysLeft} days left` : `Q${quarter}`}
                >
                  <div className="timeline-label">Q{quarter}</div>
                </div>
              );
            })}
          </div>
        </div>
      </section>


      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? <div className="loading">Loading data...</div> : null}

      <div className="table-container" ref={tableContainerRef}>

        <section className="card table-card">
          <div className="card-header">
            <h2 className="card-title">
              Specifications{" "}
              <span className="card-hint filter-count" aria-live="polite">
                {quickFilters.length || searchTerm.trim() || bodOnly
                  ? `${displayRows.length} of ${rows.length}`
                  : `${rows.length}`}
              </span>
            </h2>
            <div className="card-actions">
              <span className="card-hint phase-key ph-key" aria-hidden="true">
                <span className="bg-completed">{"✓"}</span> Done
                <span className="in-progress" /> In progress
                <span className="bg-upcoming" /> Upcoming
              </span>
              <a
                href="https://riscv.github.io/adm-riscv-sde/"
                target="_blank"
                rel="noreferrer"
                className="btn sm toolbar-link"
              >
                <IconExternal />
                <span className="toolbar-text">Specification Development Explorer</span>
              </a>
              <button
                type="button"
                className="btn sm export-button"
                onClick={() => downloadCsv(displayRows)}
                disabled={!displayRows.length}
                title="Download the rows shown below as CSV"
              >
                <IconDownload />
                Export CSV
              </button>
              <button
                type="button"
                className="btn sm export-button"
                onClick={() => window.print()}
                title="Print the table, or save it as PDF"
              >
                <IconPrinter />
                Print / PDF
              </button>
            </div>
          </div>

          <div className="table-search">
            <div className="field field-search" id="searchContainer">
              <label className="field-label" htmlFor="searchInput">
                Search
              </label>
              <input
                type="text"
                id="searchInput"
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="Search for specifications..."
              />
            </div>
              <div className="field filter-bar">
                <span className="field-label" id="quick-filters-label">
                  Quick filters
                </span>
                <div className="filter-chips" role="group" aria-labelledby="quick-filters-label">
                  {QUICK_FILTERS.map((filter) => {
                    const active = quickFilters.includes(filter.id);
                    return (
                      <button
                        key={filter.id}
                        type="button"
                        className={`filter-chip${active ? " active" : ""}`}
                        aria-pressed={active}
                        title={filter.title}
                        onClick={() => toggleQuickFilter(filter.id)}
                      >
                        {filter.label}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="field">
                <span className="field-label">View</span>
                <label className="bod-toggle">
                  <input
                    type="checkbox"
                    checked={bodOnly}
                    onChange={(event) => setBodOnly(event.target.checked)}
                  />
                  <span className="bod-text">Show BoD Report</span>
                </label>
              </div>
          </div>

        <div className="table-scroll">
        <table className={`spec-table${bodOnly ? " bod-view" : ""}`} ref={tableRef}>
          <thead>
            <tr>
              <th className="specification-column" rowSpan={2}>Specification</th>
              <th className="narrow-column" rowSpan={2}>ISA?</th>
              <th className="narrow-column phase-header ph-planning" rowSpan={2}>Planning</th>
              <th className="narrow-column phase-header ph-development" rowSpan={2}>Dev</th>
              <th className="narrow-column phase-header ph-stabilization" rowSpan={2}>Stabilization</th>
              <th className="narrow-column freeze-group-header ph-freeze" colSpan={2}>Freeze</th>
              <th
                className="narrow-column ratready-group-header ratification-divider ph-ratification-ready"
                colSpan={3}
              >
                Ratification-Ready
              </th>
              <th
                className="narrow-column publication-group-header publication-header ph-publication"
                colSpan={2}
              >
                Publication
              </th>
              <th className="narrow-column quarter-group-header progress-divider" colSpan={3}>
                Ratification Quarter{" "}
                <AboutLink onOpen={openAbout} label="About Ratification Quarter" />
              </th>
              <th className="narrow-column status-group-header status-divider" colSpan={3}>
                Ratification Progress{" "}
                <AboutLink onOpen={openAbout} label="About Ratification Progress" />
              </th>
              <th className="links-column" rowSpan={2}>Resources</th>
            </tr>
            <tr>
              <th className="narrow-column freeze-subheader">ARC Approval</th>
              <th className="narrow-column freeze-subheader">Tasks</th>
              <th className="narrow-column ratready-subheader ratification-divider">
                Public Review
              </th>
              <th className="narrow-column ratready-subheader">TSC Approval</th>
              <th className="narrow-column ratready-subheader">Tasks</th>
              <th className="narrow-column publication-subheader">BoD Approval</th>
              <th className="narrow-column publication-subheader">Tasks</th>
              <th className="narrow-column quarter-subheader progress-divider" title={BASELINE_QUARTER_TITLE}>
                Baseline
              </th>
              <th className="narrow-column quarter-subheader" title={PHASE_START_QUARTER_TITLE}>
                Phase Start
              </th>
              <th className="narrow-column quarter-subheader" title={PROJECTED_QUARTER_TITLE}>
                Projected
              </th>
              <th className="narrow-column status-subheader status-divider" title="Projected vs Baseline: 1 quarter behind is Exposed, 2 or more is Late">
                vs Baseline
              </th>
              <th className="narrow-column status-subheader" title="Projected vs Phase Start (the current phase): 1 quarter behind is Exposed, 2 or more is Late">
                vs Phase Start
              </th>
              <th className="trend-column status-subheader" title="Direction of the last change to Projected in the last 90 days: ↓ slipping, → steady, ↑ improving">
                Trend{" "}
                <AboutLink onOpen={openAbout} label="About Trend" />
              </th>
            </tr>
          </thead>
          <tbody>
            {displayRows.map((row, index) => {
              const progressClass = normalizeProgressClass(row.ratificationProgress);
              const currentPhaseIndex = WORKFLOW_PHASES.indexOf(row.currentPhase);
              const githubValue = String(row.github || "").trim();
              const hasGithub =
                githubValue &&
                githubValue.toLowerCase() !== "not set yet" &&
                githubValue.toLowerCase() !== "n/a";
              const latestReleaseUrl = hasGithub ? getLatestReleaseUrl(githubValue) : "";
              const specKey = specKeyOf(row);
              const specSummary = summaries[specKey];

              return (
                <tr
                  key={`${row.jiraUrl || row.summary}-${index}`}
                  className={`${progressClass} ${phaseClass(row.currentPhase)}`.trim()}
                >
                  <td className="specification-column">
                    <a href={row.jiraUrl} target="_blank" rel="noreferrer">
                      {row.summary}
                    </a>
                    {!bodOnly ? (
                      row.lastContribution ? (
                        <div
                          className="spec-updated"
                          title={`Last contribution${
                            row.lastContributionSource
                              ? ` (source: ${row.lastContributionSource})`
                              : ""
                          }`}
                        >
                          <span
                            className={`activity-dot ${activityRecencyClass(row)}`}
                            aria-hidden="true"
                          />
                          {formatUpdateDate(row.lastContribution)} · {formatRelativeAge(row.lastContribution)}
                        </div>
                      ) : (
                        <div className="spec-updated no-activity">
                          <span className="activity-dot activity-none" aria-hidden="true" />
                          No recent contribution found
                        </div>
                      )
                    ) : null}
                    <div className="tooltip-text">
                      {row.summary}
                      <br />
                      <a href={row.jiraUrl} target="_blank" rel="noreferrer">
                        View in Jira
                      </a>
                    </div>
                    {row.fastTrack ? (
                      <span
                        className="fast-track-badge"
                        title="Fast-Track Specification"
                        aria-label="Fast-Track"
                      >
                        <span className="ft-label">FT</span>
                      </span>
                    ) : null}
                  </td>
                  <td className="narrow-column">{formatIsaLabel(row.isaOrNonIsa)}</td>
                  {DISPLAY_PHASES.map((phase) => {
                    const phaseIndex = WORKFLOW_PHASES.indexOf(phase);
                    let content = "...";
                    let className = "bg-upcoming";
                    let title = `Upcoming Phase: ${phase}`;

                    if (phase === row.currentPhase && row.phaseTasksStatus === "Done") {
                      // Every task of the phase is done; only its gate is pending.
                      content = "\u2713";
                      className = "bg-completed";
                      title = `Tasks Complete: ${phase}`;
                    } else if (phase === row.currentPhase) {
                      content = "\u23F3";
                      className = "in-progress";
                      title = `In Progress: ${phase}`;
                    } else if (currentPhaseIndex >= 0 && phaseIndex < currentPhaseIndex) {
                      content = "\u2713";
                      className = "bg-completed";
                      title = `Completed Phase: ${phase}`;
                    }

                    // The Ratification-Ready divider now sits on the Public
                    // Review cell, which is the first column of that group.
                    const cell = (
                      <td className={`text-center phase-cell ${phaseClass(phase)}`} key={`${row.summary}-${phase}`}>
                        <span className={className} title={title} style={{ whiteSpace: "nowrap" }}>
                          {content}
                        </span>
                      </td>
                    );

                    if (phase === "Specification in Publication") {
                      const bod = getBodApprovalState(row);
                      let bodContent = "...";
                      let bodClass = "bg-upcoming";
                      let bodTitle = `Upcoming: BoD Approval${
                        bod.label ? ` (${bod.label})` : ""
                      }`;
                      if (bod.kind === "completed") {
                        bodContent = "\u2713";
                        bodClass = "bg-completed";
                        bodTitle = `BoD Approval Complete${
                          bod.label ? `: ${bod.label}` : ""
                        }`;
                      } else if (bod.kind === "in-progress") {
                        bodContent = "\u23F3";
                        bodClass = "in-progress";
                        bodTitle = `BoD Approval In Progress${
                          bod.label ? `: ${bod.label}` : ""
                        }`;
                      }

                      const bodCell = (
                        <td className="text-center phase-cell ph-publication" key={`${row.summary}-bod-approval`}>
                          <span
                            className={bodClass}
                            title={bodTitle}
                            style={{ whiteSpace: "nowrap" }}
                          >
                            {bodContent}
                          </span>
                        </td>
                      );

                      return [bodCell, cell];
                    }

                    if (phase === "Ratification-Ready") {
                      const publicReview = getPublicReviewState(row);
                      let prContent = "...";
                      let prClass = "bg-upcoming";
                      let prTitle = `Upcoming: Public Review${
                        publicReview.label ? ` (${publicReview.label})` : ""
                      }`;
                      if (publicReview.kind === "completed") {
                        prContent = "\u2713";
                        prClass = "bg-completed";
                        prTitle = `Public Review Complete${
                          publicReview.label ? `: ${publicReview.label}` : ""
                        }`;
                      } else if (publicReview.kind === "in-progress") {
                        prContent = "\u23F3";
                        prClass = "in-progress";
                        prTitle = `Public Review In Progress${
                          publicReview.label ? `: ${publicReview.label}` : ""
                        }`;
                      }

                      const publicReviewCell = (
                        <td
                          className="text-center phase-cell ph-ratification-ready ratification-divider"
                          key={`${row.summary}-public-review`}
                        >
                          <span
                            className={prClass}
                            title={prTitle}
                            style={{ whiteSpace: "nowrap" }}
                          >
                            {prContent}
                          </span>
                        </td>
                      );

                      const tscApproval = getTscApprovalState(row);
                      let tscContent = "...";
                      let tscClass = "bg-upcoming";
                      let tscTitle = `Upcoming: TSC Approval${
                        tscApproval.label ? ` (${tscApproval.label})` : ""
                      }`;
                      if (tscApproval.kind === "completed") {
                        tscContent = "\u2713";
                        tscClass = "bg-completed";
                        tscTitle = `TSC Approval Complete${
                          tscApproval.label ? `: ${tscApproval.label}` : ""
                        }`;
                      } else if (tscApproval.kind === "in-progress") {
                        tscContent = "\u23F3";
                        tscClass = "in-progress";
                        tscTitle = `TSC Approval In Progress${
                          tscApproval.label ? `: ${tscApproval.label}` : ""
                        }`;
                      }

                      const tscApprovalCell = (
                        <td className="text-center phase-cell ph-ratification-ready" key={`${row.summary}-tsc-approval`}>
                          <span
                            className={tscClass}
                            title={tscTitle}
                            style={{ whiteSpace: "nowrap" }}
                          >
                            {tscContent}
                          </span>
                        </td>
                      );

                      return [publicReviewCell, tscApprovalCell, cell];
                    }

                    if (phase !== "Stabilization") {
                      return cell;
                    }

                    const arc = getArcReviewState(row);
                    let arcContent = "...";
                    let arcClass = "bg-upcoming";
                    let arcTitle = `Upcoming: ARC Approval${arc.label ? ` (${arc.label})` : ""}`;
                    if (arc.kind === "completed") {
                      arcContent = "\u2713";
                      arcClass = "bg-completed";
                      arcTitle = `ARC Approval Complete${arc.label ? `: ${arc.label}` : ""}`;
                    } else if (arc.kind === "in-progress") {
                      arcContent = "\u23F3";
                      arcClass = "in-progress";
                      arcTitle = `ARC Approval In Progress${arc.label ? `: ${arc.label}` : ""}`;
                    }

                    const arcCell = (
                      <td className="text-center phase-cell ph-freeze" key={`${row.summary}-arc-review`}>
                        <span className={arcClass} title={arcTitle} style={{ whiteSpace: "nowrap" }}>
                          {arcContent}
                        </span>
                      </td>
                    );

                    return [cell, arcCell];
                  })}
                  <td className="narrow-column quarter-cell progress-divider">{row.plannedQuarter}</td>
                  <td className="narrow-column quarter-cell">{row.planQuarter || "Not Set Yet"}</td>
                  <td className="narrow-column quarter-cell">{row.trendingQuarter}</td>
                  {(() => {
                    const vsBaseline = baselineStatus(row);
                    return (
                      <td className="narrow-column status-col status-divider" title={vsBaseline.title}>
                        <span className={`status-chip ${vsBaseline.className}`}>{vsBaseline.label}</span>
                      </td>
                    );
                  })()}
                  <td className="narrow-column status-col" title={phaseStatusTitle(row)}>
                    {row.ratificationProgress ? (
                      <span className={`status-chip ${statusClassName(row.ratificationProgress)}`}>
                        {String(row.ratificationProgress).replace(/^on track$/i, "On\u00A0Track")}
                      </span>
                    ) : null}
                  </td>
                  {(() => {
                    const trend = projectedTrend(row);
                    return (
                      <td className={`trend-column ${trend.className}`} title={trend.title} aria-label={trend.name}>
                        {trend.label}
                      </td>
                    );
                  })()}
                  <td className="links-column">
                    <div className="icon-group">
                      {specSummary?.summary ? (
                        <button
                          type="button"
                          className="icon-button summary-button"
                          onClick={() => setSummaryKey(specKey)}
                          title="Summary"
                          aria-label={`Summary of ${row.summary}`}
                        >
                          <SummaryIcon />
                        </button>
                      ) : (
                        <span className="icon-disabled" title="No summary yet">
                          <SummaryIcon />
                        </span>
                      )}
                      {hasGithub ? (
                        <a
                          href={githubValue}
                          target="_blank"
                          rel="noreferrer"
                          className="icon-link"
                          title="GitHub Repository"
                        >
                          <img
                            src={`${assetBase}github-mark.svg`}
                            alt="GitHub Logo"
                            width="20"
                            height="20"
                            className="icon-img"
                          />
                        </a>
                      ) : (
                        <span className="icon-disabled" title="No GitHub repository set">
                          <img
                            src={`${assetBase}github-mark.svg`}
                            alt="No GitHub repository set"
                            width="20"
                            height="20"
                            className="icon-img"
                          />
                        </span>
                      )}
                      {latestReleaseUrl ? (
                        <a
                          href={latestReleaseUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="icon-link"
                          title="Latest Release"
                        >
                          <img
                            src={`${assetBase}release-tag.svg`}
                            alt="Latest release"
                            width="18"
                            height="18"
                            className="icon-img"
                          />
                        </a>
                      ) : (
                        <span className="icon-disabled" title="No releases available">
                          <img
                            src={`${assetBase}release-tag.svg`}
                            alt="No releases available"
                            width="18"
                            height="18"
                            className="icon-img"
                          />
                        </span>
                      )}
                      {!bodOnly && (
                        <button
                          type="button"
                          className="icon-button"
                          onClick={() => handleShare(row)}
                          title="Share"
                        >
                          <img
                            src={`${assetBase}paper-plane-2563.svg`}
                            alt="Share"
                            width="16"
                            height="16"
                            className="icon-img"
                          />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
        </section>

        {/* Upcoming approvals is hidden for now (to be reworked); its data is still computed. */}
        <details
          id="about-ratification"
          className="card about-trend"
          open={aboutTrendOpen}
          onToggle={(event) => setAboutTrendOpen(event.currentTarget.open)}
        >
          <summary>About Ratification Quarter and Progress</summary>
          <div className="about-trend-body">
            <h4>Ratification Quarter</h4>
            <ul>
              <li><strong>Baseline</strong>: the original commitment, set when the Ratification Plan
                was approved. It never changes.</li>
              <li><strong>Phase Start</strong>: the Projected quarter from the first plan made in the
                current phase. It is reset only when the specification moves to a new phase.</li>
              <li><strong>Projected</strong>: today's forecast, from the latest replan. It moves every
                time the specification is replanned.</li>
            </ul>
            <h4>Ratification Progress</h4>
            <ul>
              <li><strong>vs Baseline</strong>: Projected compared with Baseline. On Track if it is the
                same quarter or earlier, Exposed if 1 quarter later, Late if 2 or more quarters
                later; the number of quarters is shown, e.g. Late (3Q). This is the long-term view:
                are we late against what we committed?</li>
              <li><strong>vs Phase Start</strong>: Projected compared with Phase Start, using the same
                rule. It shows whether the specification has slipped since its current phase began,
                and it sets the row colour. Awaiting Vote, Watch and Stalled are set by hand and
                shown here when they apply.</li>
              <li><strong>Trend</strong>: the direction of the <strong>last change to Projected</strong>,
                read from the Jira history each time the dashboard data is refreshed.
                <ul>
                  <li><span className="trend-slipping">↓</span> <strong>Slipping</strong>: moved to a later quarter.</li>
                  <li><span className="trend-improving">↑</span> <strong>Improving</strong>: moved to an earlier quarter.</li>
                  <li><span className="trend-steady">→</span> <strong>Steady</strong>: no change in the last {TREND_WINDOW_DAYS} days, or no history yet.</li>
                </ul>
                Only changes from the last {TREND_WINDOW_DAYS} days count, so an old slip fades to
                Steady. Trend does not show how far a specification moved: hover the arrow to see
                the old and new quarter and the date of the change.</li>
            </ul>
            <p>Hover any header or cell in these columns for details.</p>
          </div>
        </details>
      </div>
      {(() => {
        const summaryRow = summaryKey ? rows.find((row) => specKeyOf(row) === summaryKey) : null;
        const entry = summaryKey ? summaries[summaryKey] : null;
        return summaryRow && entry?.summary ? (
          <SummaryDialog row={summaryRow} entry={entry} onClose={closeSummary} />
        ) : null;
      })()}

      {showBackToTop ? (
        <button
          className="back-to-top"
          type="button"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        >
          Back to top
        </button>
      ) : null}
    </div>
  );
}

export default App;
