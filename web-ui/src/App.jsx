import { useEffect, useMemo, useRef, useState } from "react";
import { toPng } from "html-to-image";
import Papa from "papaparse";

const REPO = "riscv-admin/bod-report";
const ASSET_PREFIX = "specs_";
const ASSET_SUFFIX = ".csv";
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

function statusClassName(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "not set" || normalized === "not set yet") {
    return "status-cell not-set";
  }
  const classValue = normalizeProgressClass(normalized);
  return classValue ? `status-cell ${classValue}` : "status-cell";
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
    trendingQuarter: trending,
    ratificationProgress: raw["Ratification Progress"] || "",
    previousRatificationProgress: raw["Previous Ratification Progress"] || "",
    github: raw["GitHub"] || "",
    bodReport,
    bodFlag: isBodReport(bodReport),
    arcReviewStatus: raw["ARC Review Status"] || "",
    fastTrack: /^(yes|true|y|1)$/i.test(String(raw["Fast Track"] || "").trim()),
    currentPhase,
    nextPhase,
  };
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
    `- Ratification-Ready: ${phases["Ratification-Ready"] || "N/A"}`,
    `- Planned Ratification Quarter: ${row.plannedQuarter || "N/A"}`,
    `- Target Ratification Quarter: ${row.trendingQuarter || "N/A"}`,
    `- Current Ratification Status: ${row.ratificationProgress || "N/A"}`,
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
  const month = new Intl.DateTimeFormat("en-US", { month: "short" }).format(date);
  const day = date.getDate();
  const year = String(date.getFullYear()).slice(-2);
  return `${month} ${day} ${year}`;
}

function getInitialBodOnly() {
  const params = new URLSearchParams(window.location.search);
  const filter = params.get("filter");
  return filter === "bod";
}

function App() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [bodOnly, setBodOnly] = useState(getInitialBodOnly);
  const [showBackToTop, setShowBackToTop] = useState(false);

  const tableRef = useRef(null);
  const tableContainerRef = useRef(null);
  const assetBase = import.meta.env.BASE_URL || "/";
  const latestCsvUrl = `${assetBase}latest.csv`;

  useEffect(() => {
    let isMounted = true;

    async function fetchData() {
      try {
        setLoading(true);
        setError("");

        async function fetchCsvFromRelease() {
          const releaseResponse = await fetch(
            `https://api.github.com/repos/${REPO}/releases/latest`,
            {
              headers: { Accept: "application/vnd.github+json" },
              cache: "no-store",
            }
          );

          if (!releaseResponse.ok) {
            throw new Error(`Failed to fetch release: ${releaseResponse.status}`);
          }

          const release = await releaseResponse.json();
          const assets = Array.isArray(release.assets) ? release.assets : [];
          const asset = assets.find(
            (item) =>
              item.name &&
              item.name.startsWith(ASSET_PREFIX) &&
              item.name.endsWith(ASSET_SUFFIX)
          );

          if (!asset || !asset.id) {
            throw new Error("No CSV assets found in the latest release.");
          }

          const csvResponse = await fetch(
            `https://api.github.com/repos/${REPO}/releases/assets/${asset.id}`,
            {
              headers: { Accept: "application/octet-stream" },
              cache: "no-store",
            }
          );

          if (!csvResponse.ok) {
            throw new Error(`Failed to download CSV: ${csvResponse.status}`);
          }

          return csvResponse.text();
        }

        async function tryFetchCsv(url, label) {
          if (!url) return "";
          try {
            const response = await fetch(url, { cache: "no-store" });
            if (response.ok) {
              return await response.text();
            }
            console.warn(`${label} not found (${response.status}); trying next source`);
          } catch (err) {
            console.warn(`${label} fetch failed; trying next source`, err);
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
          csvText = await fetchCsvFromRelease();
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
    window.history.replaceState({}, "", url);
  }, [bodOnly]);

  const displayRows = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return rows.filter((row) => {
      const summary = String(row.summary || "").toLowerCase();
      const matchesSearch = !term || summary.includes(term);
      const matchesBod = !bodOnly || row.bodFlag;
      return matchesSearch && matchesBod;
    });
  }, [rows, searchTerm, bodOnly]);

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
      backgroundColor: "#fff",
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
    const arc = getArcReviewState(row);
    const arcLabel =
      arc.kind === "completed"
        ? `\u2713${arc.label ? ` (${arc.label})` : ""}`
        : arc.kind === "in-progress"
          ? `In Progress${arc.label ? ` (${arc.label})` : ""}`
          : "...";
    const body = buildEmailBody(row, {
      "Planning": phases["Planning"],
      "Development": phases["Development"],
      "Stabilization": phases["Stabilization"],
      "ARC Review": arcLabel,
      "Freeze": phases["Freeze"],
      "Ratification-Ready": phases["Ratification-Ready"],
    });

    const mailtoLink = `mailto:?subject=${encodeURIComponent(
      subject
    )}&body=${encodeURIComponent(body)}`;
    window.location.href = mailtoLink;
  };

  const ratificationForecast = useMemo(() => {
    const currentYear = new Date().getFullYear();
    const forecast = {
      year: currentYear,
      total: 0,
      q1: 0,
      q2: 0,
      q3: 0,
      q4: 0,
    };

    rows.forEach((row) => {
      const { year, qtr } = parseQuarter(row.trendingQuarter);
      if (year === currentYear) {
        forecast.total++;
        if (qtr === 1) forecast.q1++;
        else if (qtr === 2) forecast.q2++;
        else if (qtr === 3) forecast.q3++;
        else if (qtr === 4) forecast.q4++;
      }
    });

    return forecast;
  }, [rows]);

  return (
    <div className="container">
      <div className="sticky-shell">
        <div id="header">
          <button id="save-button" className="btn btn-primary" onClick={handleSaveImage}>
            Save as Image
          </button>
        </div>
        <h2 className="page-title">
          <img src={`${assetBase}riscv.png`} alt="RISC-V logo" className="title-logo" />
          <span>Specification Development Dashboard</span>
        </h2>

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
            >
              <div className="timeline-label">
                <div>
                  Q{quarter}
                  {quarter === currentQuarter ? ` (${daysLeft} days left)` : ""}
                </div>
                {quarter === currentQuarter ? (
                  <div className="timeline-subtext">
                    {monthLabel} · {daysLeftInMonth} days left
                    <div className="timeline-subtext-secondary">
                      {daysLeftInYear} days left in year
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
            );
          })}
        </div>

        <div id="searchContainer">
          <input
            type="text"
            id="searchInput"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="Search for specifications..."
          />
        </div>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}
      {loading ? <div className="loading">Loading data...</div> : null}

      <div className="table-container" ref={tableContainerRef}>
        <div className="table-toolbar">
          <div className="ratification-forecast">
            {ratificationForecast.year} Ratification Forecast: {ratificationForecast.total} (Q1: {ratificationForecast.q1} | Q2: {ratificationForecast.q2} | Q3: {ratificationForecast.q3} | Q4: {ratificationForecast.q4})
          </div>
          <label className="bod-toggle">
            <input
              type="checkbox"
              checked={bodOnly}
              onChange={(event) => setBodOnly(event.target.checked)}
            />
            <span className="bod-text">Show BoD Report</span>
          </label>
          <span className="toolbar-divider" aria-hidden="true">|</span>
          <a
            href="https://riscv.github.io/adm-riscv-sde/"
            target="_blank"
            rel="noreferrer"
            className="toolbar-link"
          >
            <span className="toolbar-text">Specification Development Explorer</span>
          </a>
        </div>

        <div className="status-legend">
          <span className="legend-label">Target Ratification Quarter:</span>
          <div className="legend-item">
            <span className="legend-color status-cell on-track">On Track</span>
            <span className="legend-text">Likely to meet.</span>
          </div>
          <div className="legend-item">
            <span className="legend-color status-cell exposed">Exposed</span>
            <span className="legend-text">At risk.</span>
          </div>
          <div className="legend-item">
            <span className="legend-color status-cell late">Late</span>
            <span className="legend-text">Will miss, replan required.</span>
          </div>
          <div className="legend-item">
            <span className="legend-color status-cell not-set">Not Yet Defined</span>
            <span className="legend-text">Undefined.</span>
          </div>
        </div>

        <div className="table-scroll">
        <table className={`table table-bordered${bodOnly ? " bod-view" : ""}`} ref={tableRef}>
          <thead>
            <tr>
              <th className="specification-column" rowSpan={2}>Specification</th>
              <th className="narrow-column" rowSpan={2}>ISA?</th>
              <th className="narrow-column" rowSpan={2}>Planning</th>
              <th className="narrow-column" rowSpan={2}>Dev</th>
              <th className="narrow-column" rowSpan={2}>Stabilization</th>
              <th className="narrow-column freeze-group-header" colSpan={2}>Freeze</th>
              <th className="narrow-column" rowSpan={2}>Ratification-Ready</th>
              <th className="narrow-column publication-header" rowSpan={2}>Publication</th>
              <th className="narrow-column" rowSpan={2}>Planned Ratification Quarter</th>
              <th className="narrow-column" rowSpan={2}>Target Ratification Quarter</th>
              <th className="narrow-column" rowSpan={2}>Current Status</th>
              <th className="github-column" rowSpan={2}>GitHub</th>
              {!bodOnly && <th className="share-column" rowSpan={2}>Share</th>}
            </tr>
            <tr>
              <th className="narrow-column freeze-subheader">ARC Approval</th>
              <th className="narrow-column freeze-subheader">Tasks</th>
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

              return (
                <tr key={`${row.jiraUrl || row.summary}-${index}`} className={progressClass}>
                  <td className="specification-column">
                    <a href={row.jiraUrl} target="_blank" rel="noreferrer">
                      {row.summary}
                    </a>
                    {!bodOnly && row.updated ? (
                      <div className="spec-updated">
                        Last Update . {formatUpdateDate(row.updated)}
                      </div>
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
                  <td className="narrow-column">{row.isaOrNonIsa}</td>
                  {DISPLAY_PHASES.map((phase) => {
                    const phaseIndex = WORKFLOW_PHASES.indexOf(phase);
                    let content = "...";
                    let className = "bg-upcoming";
                    let title = `Upcoming Phase: ${phase}`;

                    if (phase === row.currentPhase) {
                      content = "\u23F3";
                      className = "in-progress";
                      title = `In Progress: ${phase}`;
                    } else if (currentPhaseIndex >= 0 && phaseIndex < currentPhaseIndex) {
                      content = "\u2713";
                      className = "bg-completed";
                      title = `Completed Phase: ${phase}`;
                    }

                    const cell = (
                      <td className="text-center" key={`${row.summary}-${phase}`}>
                        <span className={className} title={title} style={{ whiteSpace: "nowrap" }}>
                          {content}
                        </span>
                      </td>
                    );

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
                      <td className="text-center" key={`${row.summary}-arc-review`}>
                        <span className={arcClass} title={arcTitle} style={{ whiteSpace: "nowrap" }}>
                          {arcContent}
                        </span>
                      </td>
                    );

                    return [cell, arcCell];
                  })}
                  <td className="narrow-column">{row.plannedQuarter}</td>
                  <td className="narrow-column">{row.trendingQuarter}</td>
                  <td className={`narrow-column ${statusClassName(row.ratificationProgress)}`}>
                    {row.ratificationProgress}
                  </td>
                  <td>
                    {hasGithub ? (
                      <div className="icon-group">
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
                      </div>
                    ) : (
                      "N/A"
                    )}
                  </td>
                  {!bodOnly && (
                    <td className="narrow-column">
                      <button
                        type="button"
                        className="icon-button"
                        onClick={() => handleShare(row)}
                        title="Share"
                        style={{ background: "none", border: "none", padding: 0 }}
                      >
                        <img
                          src={`${assetBase}paper-plane-2563.svg`}
                          alt="Share"
                          width="16"
                          height="16"
                          className="icon-img"
                        />
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
        </div>
      </div>

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
