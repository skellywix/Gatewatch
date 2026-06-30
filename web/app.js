const TABS = ["roster", "profiles", "activity", "logs", "configuration"];
const ADMIN_TABS = new Set(["logs", "configuration"]);
const SECTION_META = {
  roster: {
    label: "Observation",
    summary: "Monitor roster state, access flow, and pending identity changes.",
  },
  profiles: {
    label: "Identity",
    summary: "Edit employee records, access profile fields, and handoff controls.",
  },
  activity: {
    label: "Audit",
    summary: "Review employee changes and signed actor history.",
  },
  logs: {
    label: "Diagnostics",
    summary: "Inspect service health, storage, configuration, and recent events.",
  },
  configuration: {
    label: "System state",
    summary: "Validate runtime binding, Microsoft SSO, directory sync, and secrets.",
  },
};

const state = {
  employees: [],
  accessFields: [],
  changeRequests: [],
  audit: [],
  auth: null,
  config: null,
  configPreview: null,
  configLoading: false,
  diagnostics: null,
  diagnosticsLoading: false,
  summary: { total: 0, active: 0, disabled: 0, terminated: 0, updatedToday: 0 },
  metricSnapshot: {},
  selectedId: null,
  editingAccessFieldId: null,
  expandedPanels: new Set(),
  expandedActivityId: null,
  search: "",
  activeTab: tabFromLocation(),
};

const form = document.querySelector("#employeeForm");
const accessFieldForm = document.querySelector("#accessFieldForm");
const configForm = document.querySelector("#configForm");
const configTemplate = document.querySelector("#configTemplate");
const table = document.querySelector("#employeeTable");
const profileEmployeeList = document.querySelector("#profileEmployeeList");
const accessFieldList = document.querySelector("#accessFieldList");
const activityList = document.querySelector("#activityList");
const metricStrip = document.querySelector("#metricStrip");
const telemetryStrip = document.querySelector("#telemetryStrip");
const systemStatusRow = document.querySelector("#systemStatusRow");
const statusPopover = document.querySelector("#statusPopover");
const toast = document.querySelector("#toast");

bindSystemInputs(document);

document.querySelector("#searchInput").addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  renderEmployees();
});

document.addEventListener(
  "invalid",
  (event) => {
    if (!isInputControl(event.target)) return;
    event.target.dataset.touched = "true";
    SystemInput(event.target);
  },
  true
);

document.querySelector("#refreshButton").addEventListener("click", () => loadAll(true));
document.querySelector("#backButton").addEventListener("click", () => {
  if (window.history.length > 1) {
    window.history.back();
  } else {
    setActiveTab("roster", { replace: true });
  }
});
document.querySelector("#newEmployeeButton").addEventListener("click", clearForm);
document.querySelector("#profileNewButton").addEventListener("click", clearForm);
document.querySelector("#resetButton").addEventListener("click", clearForm);
document.querySelector("#deleteButton").addEventListener("click", deleteSelectedEmployee);
document.querySelector("#terminateButton").addEventListener("click", terminateSelectedEmployee);
document.querySelector("#syncEntraButton").addEventListener("click", syncEntraDirectory);
document.querySelector("#changeRequestList").addEventListener("click", reviewChangeRequest);
document.querySelectorAll("[data-panel-toggle]").forEach((button) => {
  button.addEventListener("click", () => togglePanel(button.dataset.panelToggle));
});
document.querySelectorAll("[data-status-chip]").forEach((chip) => {
  chip.addEventListener("click", (event) => {
    event.stopPropagation();
    showStatusPopover(chip);
  });
});
accessFieldForm.addEventListener("submit", saveAccessField);
document.querySelector("#cancelAccessFieldEdit").addEventListener("click", resetAccessFieldForm);
accessFieldList.addEventListener("click", handleAccessFieldAction);
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    setActiveTab(button.dataset.tab, { push: true });
  });
});
document.querySelectorAll("[data-step]").forEach((button) => {
  button.addEventListener("click", () => {
    const next = button.getAttribute("aria-pressed") !== "true";
    setStepState(button.dataset.step, next);
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveEmployee();
});
form.elements.employee_id.addEventListener("change", () => loadExistingEmployeeByValue("employee_id", form.elements.employee_id.value));
form.elements.email.addEventListener("change", () => loadExistingEmployeeByValue("email", form.elements.email.value));
form.elements.department.addEventListener("change", autofillFromDepartment);

configForm.addEventListener("submit", validateConfig);
document.querySelector("#refreshLogsButton").addEventListener("click", () => loadDiagnostics(true));
document.querySelector("#refreshConfigButton").addEventListener("click", () => loadConfig(true));
document.querySelector("#copyConfigButton").addEventListener("click", copyConfigTemplate);

table.addEventListener("click", (event) => {
  const row = event.target.closest("[data-employee-id]");
  if (!row) return;
  selectEmployee(Number(row.dataset.employeeId));
});

table.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("[data-employee-id]");
  if (!row) return;
  event.preventDefault();
  selectEmployee(Number(row.dataset.employeeId));
});

profileEmployeeList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-profile-employee-id]");
  if (!button) return;
  selectEmployee(Number(button.dataset.profileEmployeeId));
});

if (activityList) {
  activityList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-activity-id]");
    if (!item) return;
    state.expandedActivityId = state.expandedActivityId === item.dataset.activityId ? null : item.dataset.activityId;
    renderActivity();
  });
  activityList.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const item = event.target.closest("[data-activity-id]");
    if (!item) return;
    event.preventDefault();
    state.expandedActivityId = state.expandedActivityId === item.dataset.activityId ? null : item.dataset.activityId;
    renderActivity();
  });
}

document.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-status-chip]");
  if (chip) {
    event.stopPropagation();
    showStatusPopover(chip);
    return;
  }
  hideStatusPopover();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    hideStatusPopover();
    return;
  }
  if (event.key !== "Enter" && event.key !== " ") return;
  const chip = event.target.closest("[data-status-chip]");
  if (!chip) return;
  event.preventDefault();
  showStatusPopover(chip);
});
window.addEventListener("resize", hideStatusPopover);
window.addEventListener("popstate", syncTabFromLocation);
window.addEventListener("hashchange", syncTabFromLocation);

loadAll(false);

async function loadAll(showSuccess) {
  document.body.classList.add("is-processing");
  const refreshButton = document.querySelector("#refreshButton");
  SystemButton(refreshButton, { loading: true });
  try {
    const data = await api("/api/bootstrap");
    state.summary = data.summary;
    state.employees = data.employees;
    state.accessFields = data.accessFields || [];
    state.changeRequests = data.changeRequests || [];
    state.audit = data.audit;
    state.auth = data.auth;
    if (state.selectedId && !state.employees.some((employee) => employee.id === state.selectedId)) {
      clearForm();
    }
    renderAll();
    if (showSuccess) showToast("Refreshed");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    document.body.classList.remove("is-processing");
    SystemButton(refreshButton, { loading: false });
  }
}

function PulsingStatusDot({ tone = "live", pulse = false, label = "" } = {}) {
  const labelAttr = label ? ` title="${escapeHtml(label)}"` : "";
  return `<span class="pulsing-status-dot ${escapeHtml(tone)} ${pulse ? "is-pulsing" : ""}" aria-hidden="true"${labelAttr}></span>`;
}

function InteractiveStatusChip({
  id = "",
  label,
  tone = "muted",
  chipKey = tone,
  title = label,
  detail = "",
  pulse = false,
  selected = false,
  tag = "button",
  classes = "",
  disabled = false,
} = {}) {
  const safeTag = tag === "span" ? "span" : "button";
  const buttonAttrs = safeTag === "button" ? ` type="button"${disabled ? " disabled" : ""}` : ' role="button" tabindex="0"';
  const state = [selected ? "selected" : "", pulse ? "live" : ""].filter(Boolean).join(" ") || "idle";
  const attrs = [
    id ? `id="${escapeHtml(id)}"` : "",
    `class="interactive-status-chip status-chip ${escapeHtml(tone)} ${pulse ? "is-pulsing" : ""} ${selected ? "is-selected" : ""} ${classes}"`,
    `data-component="InteractiveStatusChip"`,
    `data-chip-state="${escapeHtml(state)}"`,
    `data-status-chip="${escapeHtml(chipKey)}"`,
    `data-status-title="${escapeHtml(title || label || "Status")}"`,
    `data-status-detail="${escapeHtml(detail || "No additional metadata is available for this state.")}"`,
    'aria-expanded="false"',
    buttonAttrs,
  ]
    .filter(Boolean)
    .join(" ");
  return `<${safeTag} ${attrs}>${PulsingStatusDot({ tone, pulse })}<span>${escapeHtml(label || "")}</span></${safeTag}>`;
}

function SurveillancePanel({
  tag = "article",
  className = "",
  attrs = "",
  meta = "",
  tone = "",
  active = false,
  expanded = false,
  loading = false,
  warning = false,
  children = "",
} = {}) {
  const stateName = componentState({ active, expanded, loading, warning });
  const metaAttr = meta ? ` data-panel-meta="${escapeHtml(meta)}"` : "";
  const toneAttr = tone ? ` data-panel-tone="${escapeHtml(tone)}"` : "";
  return `<${tag} class="surveillance-panel ${className} ${active ? "is-selected" : ""} ${expanded ? "is-expanded" : ""}" data-component="SurveillancePanel" data-panel-state="${escapeHtml(stateName)}"${toneAttr}${metaAttr} ${attrs}>${children}</${tag}>`;
}

function MetricCard({ key, label, value, detail, tone, meta, live = false, selected = false, updated = false }) {
  const valueIds = {
    total: "metricTotal",
    active: "metricActive",
    progress: "metricProgress",
    updated: "metricUpdated",
  };
  const detailIds = {
    total: "metricTotalDetail",
    active: "metricActiveDetail",
    progress: "metricProgressDetail",
    updated: "metricUpdatedDetail",
  };
  return SurveillancePanel({
    className: `metric-card ${live ? "is-live" : ""} ${updated ? "is-updating" : ""}`,
    meta,
    tone,
    active: selected,
    loading: updated,
    attrs: `data-component-card="MetricCard" data-metric-key="${escapeHtml(key)}" data-metric-tone="${escapeHtml(tone)}" data-metric-state="${updated ? "update" : live ? "live" : "stable"}"`,
    children: `
      ${PulsingStatusDot({ tone, pulse: live })}
      <div class="metric">
        <span>${escapeHtml(label)}</span>
        <strong id="${valueIds[key] || ""}">${escapeHtml(value)}</strong>
        <small id="${detailIds[key] || ""}">${escapeHtml(detail)}</small>
      </div>
    `,
  });
}

function ActivityFeed(entries) {
  if (!entries.length) {
    return `<div class="empty-state"><strong>No activity yet</strong><span>Changes appear here after the first save.</span></div>`;
  }
  return entries.slice(0, 50).map(ActivityFeedItem).join("");
}

function ActivityFeedItem(entry) {
  const key = activityKey(entry);
  const expanded = state.expandedActivityId === key;
  const severity = activitySeverity(entry.action);
  return `
    <article class="activity-item activity-feed-item ${severity} ${expanded ? "is-selected has-detail" : ""}" data-component="ActivityFeed" data-feed-state="${expanded ? "selected detail" : "idle"}" data-activity-id="${escapeHtml(key)}" tabindex="0" aria-selected="${expanded ? "true" : "false"}" aria-expanded="${expanded ? "true" : "false"}">
      ${InteractiveStatusChip({
        label: labelize(entry.action),
        tone: severity,
        chipKey: "activity",
        title: activityStatusLabel(entry.action),
        detail: `${entry.summary || "Audit event"} / ${formatDateTime(entry.created_at) || "--"}`,
        tag: "span",
        classes: "activity-action",
      })}
      <div class="activity-copy">
        <strong>${escapeHtml(entry.summary)}</strong>
        <div class="activity-meta">
          <span>Changed by <b>${escapeHtml(entry.actor || "Local user")}</b></span>
          <span class="timestamp">${formatDateTime(entry.created_at)}</span>
        </div>
      </div>
      <div class="activity-detail" ${expanded ? "" : "hidden"}>
        ${DetailInspector({
          stateName: expanded ? "selected detail" : "idle",
          rows: [
            ["Status", activityStatusLabel(entry.action)],
            ["Object", `${labelize(entry.entity_type || "record")} #${entry.entity_id || "--"}`],
            ["Timestamp", formatDateTime(entry.created_at)],
            ["Source", entry.actor || "Local user"],
          ],
        })}
      </div>
    </article>
  `;
}

function DetailInspector({ stateName = "idle", rows = [] } = {}) {
  return `<section class="detail-inspector" data-component="DetailInspector" data-inspector-state="${escapeHtml(stateName)}">${metadataGrid(rows)}</section>`;
}

function metadataGrid(rows) {
  return `
    <dl class="metadata-grid">
      ${rows
        .map(
          ([label, value]) => `
            <div>
              <dt>${escapeHtml(label)}</dt>
              <dd>${escapeHtml(value ?? "")}</dd>
            </div>
          `
        )
        .join("")}
    </dl>
  `;
}

function SystemButton(button, { loading = false, disabled } = {}) {
  if (!button) return;
  button.classList.add("system-button");
  button.classList.toggle("is-loading", Boolean(loading));
  button.setAttribute("aria-busy", loading ? "true" : "false");
  if (typeof disabled === "boolean") {
    button.disabled = disabled;
  }
}

function SystemInput(control, { warning = false } = {}) {
  if (!isInputControl(control)) return;
  const field = control.closest("label") || control.parentElement;
  if (!field) return;
  field.classList.add("system-input");
  const stateName = systemInputState(control, warning);
  field.dataset.inputState = stateName;
  control.dataset.inputState = stateName;
  control.setAttribute("aria-invalid", stateName === "error" ? "true" : "false");
}

function bindSystemInputs(root = document) {
  root.querySelectorAll("input, select, textarea").forEach((control) => {
    if (!isInputControl(control)) return;
    SystemInput(control);
    if (control.dataset.systemInputBound === "true") return;
    control.dataset.systemInputBound = "true";
    control.addEventListener("focus", () => SystemInput(control));
    control.addEventListener("blur", () => {
      control.dataset.touched = "true";
      SystemInput(control);
    });
    control.addEventListener("input", () => SystemInput(control));
    control.addEventListener("change", () => SystemInput(control));
  });
}

function systemInputState(control, warning) {
  if (document.activeElement === control) return "focus";
  if (control.validity && !control.validity.valid && (control.dataset.touched === "true" || hasInputValue(control))) {
    return "error";
  }
  if (warning || control.dataset.warning === "true" || isSystemInputWarning(control)) return "warning";
  if (control.validity?.valid && hasInputValue(control)) return "valid";
  return "idle";
}

function isSystemInputWarning(control) {
  if (control.name === "allowInsecureNetwork" && control.checked) return true;
  if (control.name === "host") {
    const value = String(control.value || "").trim();
    return Boolean(value && !["127.0.0.1", "localhost", "::1"].includes(value));
  }
  const max = Number(control.getAttribute("maxlength") || 0);
  return Boolean(max && String(control.value || "").length >= Math.floor(max * 0.9));
}

function hasInputValue(control) {
  if (control.type === "checkbox") return control.checked;
  return String(control.value || "").trim().length > 0;
}

function isInputControl(node) {
  return node instanceof HTMLInputElement || node instanceof HTMLSelectElement || node instanceof HTMLTextAreaElement;
}

function setSurveillancePanelState(panel, { active = false, expanded = false, loading = false, warning = false } = {}) {
  if (!panel) return;
  panel.dataset.component = "SurveillancePanel";
  panel.classList.toggle("is-active", active);
  panel.classList.toggle("is-selected", active);
  panel.classList.toggle("is-expanded", expanded);
  panel.classList.toggle("is-collapsed", !expanded);
  panel.classList.toggle("is-loading", loading);
  panel.classList.toggle("is-warning", warning);
  panel.dataset.panelState = componentState({ active, expanded, loading, warning });
}

function componentState({ active = false, expanded = false, loading = false, warning = false } = {}) {
  return [
    active ? "active" : "",
    expanded ? "expanded" : "",
    loading ? "loading" : "",
    warning ? "warning" : "",
  ]
    .filter(Boolean)
    .join(" ") || "idle";
}

function tabFromLocation() {
  const tab = window.location.hash.replace("#", "");
  return TABS.includes(tab) ? tab : "roster";
}

function syncTabFromLocation() {
  state.activeTab = tabFromLocation();
  renderTabs();
}

function setActiveTab(tab, options = {}) {
  hideStatusPopover();
  const requested = TABS.includes(tab) ? tab : "roster";
  state.activeTab = tabIsAllowed(requested) ? requested : "roster";
  updateLocationTab(state.activeTab, options);
  renderTabs();
}

function updateLocationTab(tab, options = {}) {
  const base = `${window.location.pathname}${window.location.search}`;
  const next = tab === "roster" ? base : `${base}#${tab}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next === current) return;
  if (options.replace) {
    window.history.replaceState(null, "", next);
  } else if (options.push) {
    window.history.pushState(null, "", next);
  }
}

function renderAll() {
  renderTabs();
  renderMetrics();
  renderTelemetry();
  renderSystemChips();
  renderPanelStates();
  renderDirectory();
  renderEmployees();
  renderProfileEmployeeList();
  renderAutofillOptions();
  renderAccessProfileFields(selectedEmployee()?.access_profile || {});
  renderAccessFieldCatalog();
  renderChangeRequests();
  renderActivity();
  renderInspectorMeta();
  renderDiagnostics();
  renderConfig();
  updateFormPermissions();
}

function renderTabs() {
  const adminAllowed = canModifyEmployees();
  const tabs = document.querySelector(".workspace-tabs");
  if (tabs) {
    tabs.classList.toggle("admin-tabs", adminAllowed);
  }
  const logsTab = document.querySelector("#logsTab");
  if (logsTab) {
    logsTab.hidden = !adminAllowed;
    logsTab.disabled = !adminAllowed;
  }
  const configTab = document.querySelector("#configurationTab");
  if (configTab) {
    configTab.hidden = !adminAllowed;
    configTab.disabled = !adminAllowed;
  }
  if (!tabIsAllowed(state.activeTab)) {
    state.activeTab = "roster";
    updateLocationTab("roster", { replace: true });
  }
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === state.activeTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-view]").forEach((panel) => {
    const allowed = tabIsAllowed(panel.dataset.view);
    const active = allowed && panel.dataset.view === state.activeTab;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  renderSectionMeta();
  if (state.activeTab === "logs" && adminAllowed && !state.diagnostics && !state.diagnosticsLoading) {
    loadDiagnostics(false);
  }
  if (state.activeTab === "configuration" && adminAllowed && !state.config && !state.configLoading) {
    loadConfig(false);
  }
}

function renderSectionMeta() {
  const meta = SECTION_META[state.activeTab] || SECTION_META.roster;
  const label = document.querySelector("#currentSectionLabel");
  const summary = document.querySelector("#currentSectionSummary");
  if (label) label.textContent = meta.label;
  if (summary) summary.textContent = meta.summary;
}

function renderMetrics() {
  if (!metricStrip) return;
  const updated = latestTimestamp();
  const active = Number(state.summary.active || 0);
  const total = Number(state.summary.total || 0);
  const metrics = [
    {
      key: "total",
      label: "Roster Total",
      value: total,
      detail: total ? "DATA VERIFIED" : "AWAITING RECORDS",
      tone: "live",
      meta: "ROSTER FEED",
      live: true,
      selected: state.activeTab === "roster",
    },
    {
      key: "active",
      label: "Active",
      value: active,
      detail: active ? "IDENTITY VERIFIED" : "NO ACTIVE IDENTITIES",
      tone: "secure",
      meta: "IDENTITY STATE",
      selected: state.activeTab === "profiles",
    },
    {
      key: "progress",
      label: "In Process",
      value: state.summary.inProgress ?? 0,
      detail: state.summary.inProgress ? "MONITORING" : "QUEUE CLEAR",
      tone: state.summary.inProgress ? "warning" : "secure",
      meta: "WORKFLOW QUEUE",
      live: Boolean(state.summary.inProgress),
      selected: state.activeTab === "activity",
    },
    {
      key: "updated",
      label: "Updated Today",
      value: state.summary.updatedToday ?? 0,
      detail: updated ? `LAST UPDATED ${formatCompactDateTime(updated)}` : "LAST UPDATED --",
      tone: "synced",
      meta: "LAST UPDATED",
      selected: false,
    },
  ];
  metricStrip.innerHTML = metrics
    .map((metric) =>
      MetricCard({
        ...metric,
        updated: Object.prototype.hasOwnProperty.call(state.metricSnapshot, metric.key) && state.metricSnapshot[metric.key] !== metric.value,
      })
    )
    .join("");
  state.metricSnapshot = Object.fromEntries(metrics.map((metric) => [metric.key, metric.value]));
}

function updateMetric(selector, value) {
  const node = document.querySelector(selector);
  if (!node) return;
  const text = String(value);
  if (node.textContent === text) return;
  node.textContent = text;
  node.classList.remove("metric-flash");
  void node.offsetWidth;
  node.classList.add("metric-flash");
}

function renderTelemetry() {
  if (!telemetryStrip) return;
  const auth = state.auth || {};
  const updated = latestTimestamp();
  const queueCount = (state.changeRequests || []).length;
  const user = auth.user;
  const items = [
    ["SESSION ACTIVE", user?.email || user?.name || "LOCAL", "live", true, "telemetrySession"],
    ["QUEUE", String(queueCount), queueCount ? "warning" : "secure", Boolean(queueCount), "telemetryQueue"],
    ["SIGNAL STABLE", auth.graphConfigured ? "Graph + SQLite" : "SQLite", auth.graphConfigured ? "secure" : "warning", false, "telemetrySignal"],
    ["LAST UPDATED", updated ? formatCompactDateTime(updated) : "--", "synced", false, "telemetryUpdated"],
  ];
  telemetryStrip.innerHTML = items
    .map(
      ([label, value, tone, pulse, id]) => `
        <div class="telemetry-item" data-component="PulsingStatusDot" data-telemetry-state="${pulse ? "live" : "stable"}">
          ${PulsingStatusDot({ tone, pulse })}
          <span>${escapeHtml(label)}</span>
          <strong id="${escapeHtml(id)}">${escapeHtml(value)}</strong>
        </div>
      `
    )
    .join("");
}

function renderSystemChips() {
  if (!systemStatusRow) return;
  const auth = state.auth || {};
  const chips = [
    {
      label: "LIVE",
      tone: "live",
      chipKey: "live",
      title: "LIVE",
      detail: `${state.employees.length} roster source ${state.employees.length === 1 ? "record" : "records"} loaded from SQLite.`,
      pulse: true,
    },
    {
      label: auth.user ? "IDENTITY VERIFIED" : "SECURE",
      tone: auth.user ? "secure" : "synced",
      chipKey: "session",
      title: auth.user ? "IDENTITY VERIFIED" : "SECURE SESSION",
      detail: auth.user
        ? `Signed in as ${auth.user.email || auth.user.name}. Permission level: ${canModifyEmployees() ? "Domain Admin" : "Viewer"}.`
        : `Local loopback session. Permission level: ${canModifyEmployees() ? "Domain Admin" : "Viewer"}.`,
    },
    {
      label: "SYNCED",
      tone: "synced",
      chipKey: "storage",
      title: "SYNCED",
      detail: latestTimestamp()
        ? `Last application update ${formatCompactDateTime(latestTimestamp())}.`
        : "SQLite is reachable. No employee updates have been recorded yet.",
    },
  ];
  systemStatusRow.innerHTML = chips.map(InteractiveStatusChip).join("");
}

function togglePanel(key) {
  if (!key) return;
  hideStatusPopover();
  if (state.expandedPanels.has(key)) {
    state.expandedPanels.delete(key);
  } else {
    state.expandedPanels.add(key);
  }
  renderPanelStates();
}

function renderPanelStates() {
  document.querySelectorAll("[data-panel-key]").forEach((panel) => {
    const key = panel.dataset.panelKey;
    const expanded = state.expandedPanels.has(key);
    setSurveillancePanelState(panel, {
      active: panel.classList.contains("is-active") || panel.classList.contains("is-selected"),
      expanded,
      loading: panel.classList.contains("is-loading"),
      warning: panel.classList.contains("is-warning") || panel.classList.contains("is-degraded"),
    });
  });
  document.querySelectorAll("[data-panel-toggle]").forEach((button) => {
    const expanded = state.expandedPanels.has(button.dataset.panelToggle);
    button.setAttribute("aria-expanded", expanded ? "true" : "false");
    button.textContent = expanded ? "HIDE" : "DETAILS";
  });
  document.querySelectorAll("[data-panel-details]").forEach((details) => {
    details.hidden = !state.expandedPanels.has(details.dataset.panelDetails);
  });
}

function showStatusPopover(chip) {
  if (!statusPopover || !chip) return;
  const title = chip.dataset.statusTitle || chip.textContent.trim() || "STATUS";
  const detail = chip.dataset.statusDetail || "No additional metadata is available for this state.";
  statusPopover.innerHTML = `
    <strong>${escapeHtml(title)}</strong>
    <span>${escapeHtml(detail)}</span>
  `;
  statusPopover.hidden = false;
  document.querySelectorAll("[data-status-chip]").forEach((item) => item.setAttribute("aria-expanded", "false"));
  chip.setAttribute("aria-expanded", "true");
  const rect = chip.getBoundingClientRect();
  const width = Math.min(320, window.innerWidth - 24);
  const left = Math.min(Math.max(12, rect.left), window.innerWidth - width - 12);
  const top = Math.min(rect.bottom + 8, window.innerHeight - 96);
  statusPopover.style.width = `${width}px`;
  statusPopover.style.left = `${left}px`;
  statusPopover.style.top = `${Math.max(12, top)}px`;
}

function hideStatusPopover() {
  if (!statusPopover) return;
  statusPopover.hidden = true;
  document.querySelectorAll("[data-status-chip]").forEach((item) => item.setAttribute("aria-expanded", "false"));
}

function latestTimestamp() {
  const values = [
    ...state.employees.map((employee) => employee.updated_at),
    ...state.audit.map((entry) => entry.created_at),
    ...state.changeRequests.map((request) => request.reviewed_at || request.requested_at),
  ]
    .filter(Boolean)
    .sort();
  return values.at(-1) || "";
}

function renderDirectory() {
  const auth = state.auth || {};
  const status = document.querySelector("#ssoStatus");
  const login = document.querySelector("#loginLink");
  const logout = document.querySelector("#logoutLink");
  const sync = document.querySelector("#syncEntraButton");
  const result = document.querySelector("#entraSyncResult");
  const permission = document.querySelector("#permissionStatus");
  const user = auth.user;
  const permissions = auth.permissions || {};
  login.classList.toggle("hidden", !auth.ssoConfigured || Boolean(user));
  logout.classList.toggle("hidden", !user);
  sync.disabled = !auth.graphConfigured || !canModifyEmployees();
  if (user) {
    status.textContent = user.email ? `Signed in as ${user.email}` : `Signed in as ${user.name}`;
  } else if (auth.configured) {
    status.textContent = auth.ssoConfigured ? "Ready for sign-in and sync" : "Ready for directory sync";
  } else {
    status.textContent = "Not configured";
  }
  if (!auth.graphConfigured && !result.textContent) {
    result.textContent = "Set tenant, client, and secret on the server.";
  }
  permission.textContent = permissions.reason || "Sign in with Microsoft Entra ID to unlock edit and delete controls.";
  permission.classList.toggle("allowed", canModifyEmployees());
  setText("#directoryPermissionLevel", canModifyEmployees() ? "DOMAIN ADMIN" : "VIEWER");
  setText("#directoryGraphState", auth.graphConfigured ? "ONLINE" : "DEGRADED");
  setText("#directorySourceCount", `${state.employees.length} ${state.employees.length === 1 ? "employee" : "employees"}`);
  const directory = document.querySelector('[data-panel-key="directory"]');
  if (directory) {
    directory.classList.toggle("is-degraded", !auth.graphConfigured);
    setSurveillancePanelState(directory, {
      active: Boolean(user || auth.configured),
      expanded: state.expandedPanels.has("directory"),
      warning: !auth.graphConfigured,
    });
  }
}

function filteredEmployees() {
  const query = state.search.toLowerCase();
  if (!query) return state.employees;
  return state.employees.filter((employee) =>
    [
      employee.employee_id,
      employee.name,
      employee.email,
      employee.department,
      employee.title,
      employee.location,
      employee.manager,
      employee.entra_user_principal_name,
      employee.request_source,
      employee.access_needed,
      employee.status,
    ]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(query))
  );
}

function selectedEmployee() {
  return state.employees.find((item) => item.id === state.selectedId) || null;
}

function renderEmployees() {
  const employees = filteredEmployees();
  const searchInput = document.querySelector("#searchInput");
  if (searchInput) {
    searchInput.dataset.warning = state.search && !employees.length ? "true" : "false";
    SystemInput(searchInput);
  }
  document.querySelector("#rosterCount").textContent = `${employees.length} ${employees.length === 1 ? "record" : "records"}`;
  if (!employees.length) {
    table.innerHTML = `
      <tr>
        <td colspan="6">
          <div class="empty-state">
            <strong>No employees found</strong>
            <span>Create a record or clear the search.</span>
          </div>
        </td>
      </tr>
    `;
    return;
  }
  table.innerHTML = employees
    .map(
      (employee) => `
        <tr data-employee-id="${employee.id}" class="${employee.id === state.selectedId ? "selected" : ""}" tabindex="0" aria-label="Inspect ${escapeHtml(employee.name)}">
          <td>
            <div class="employee-cell">
              <span class="avatar">${escapeHtml(initials(employee.name))}</span>
              <span>
                <strong>${escapeHtml(employee.name)}</strong>
                <small>Key fob ${escapeHtml(employee.employee_id)} / ${escapeHtml(employee.email)}</small>
              </span>
            </div>
          </td>
          <td>${escapeHtml(employee.department || "Unassigned")}</td>
          <td>${escapeHtml(employee.location || "No location")}</td>
          <td>${progressPill(employee)}</td>
          <td>${statusBadge(employee.status, "", employee)}</td>
          <td><span class="timestamp">${formatDateTime(employee.updated_at)}</span><span class="row-affordance">VIEW</span></td>
        </tr>
      `
    )
    .join("");
}

function renderProfileEmployeeList() {
  const employees = filteredEmployees();
  const count = document.querySelector("#profileIndexCount");
  if (count) {
    count.textContent = `${employees.length} ${employees.length === 1 ? "available" : "available"}`;
  }
  if (!profileEmployeeList) return;
  if (!employees.length) {
    profileEmployeeList.innerHTML = `<div class="empty-state"><strong>No profiles</strong><span>Create a profile to start tracking access.</span></div>`;
    return;
  }
  profileEmployeeList.innerHTML = employees
    .map(
      (employee) => `
        <button class="profile-list-item ${employee.id === state.selectedId ? "selected" : ""}" type="button" data-profile-employee-id="${employee.id}">
          <span class="avatar">${escapeHtml(initials(employee.name))}</span>
          <span>
            <strong>${escapeHtml(employee.name)}</strong>
            <small>${escapeHtml(employee.title || employee.department || "Employee profile")}</small>
          </span>
          ${statusBadge(employee.status, "", employee)}
        </button>
      `
    )
    .join("");
}

function renderAutofillOptions() {
  setDatalistOptions("#keyFobOptions", state.employees.map((employee) => employee.employee_id));
  setDatalistOptions("#emailOptions", state.employees.map((employee) => employee.email));
  setDatalistOptions("#departmentOptions", state.employees.map((employee) => employee.department));
  setDatalistOptions("#titleOptions", state.employees.map((employee) => employee.title));
  setDatalistOptions("#locationOptions", state.employees.map((employee) => employee.location));
  setDatalistOptions("#managerOptions", state.employees.map((employee) => employee.manager));
  setDatalistOptions("#accessNeededOptions", state.employees.map((employee) => employee.access_needed));
  setDatalistOptions("#accessSectionOptions", state.accessFields.map((field) => field.section));
}

function setDatalistOptions(selector, values) {
  const list = document.querySelector(selector);
  if (!list) return;
  const unique = [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right))
    .slice(0, 200);
  list.innerHTML = unique.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
}

function activeAccessFields() {
  return state.accessFields
    .filter((field) => field.active)
    .sort((left, right) => (left.sort_order ?? 0) - (right.sort_order ?? 0) || left.section.localeCompare(right.section) || left.label.localeCompare(right.label));
}

function groupedAccessFields() {
  const groups = new Map();
  for (const field of activeAccessFields()) {
    const section = field.section || "Access";
    if (!groups.has(section)) groups.set(section, []);
    groups.get(section).push(field);
  }
  return [...groups.entries()];
}

function renderAccessProfileFields(values = {}) {
  const container = document.querySelector("#accessProfileFields");
  if (!container) return;
  const groups = groupedAccessFields();
  if (!groups.length) {
    container.innerHTML = `<div class="mini-empty">No active access fields. Domain Admins can add fields in Custom Fields.</div>`;
    return;
  }
  container.innerHTML = groups
    .map(
      ([section, fields]) => `
        <section class="access-section">
          <h3>${escapeHtml(section)}</h3>
          <div class="field-grid">
            ${fields.map((field) => renderAccessProfileInput(field, values[field.key])).join("")}
          </div>
        </section>
      `
    )
    .join("");
  bindSystemInputs(container);
}

function renderAccessProfileInput(field, value) {
  const name = accessProfileInputName(field.key);
  const required = field.required ? " required" : "";
  const label = `<span>${escapeHtml(field.label)}</span>`;
  if (field.field_type === "checkbox") {
    return `
      <label class="toggle-row access-toggle system-input">
        <input name="${escapeHtml(name)}" type="checkbox" ${value ? "checked" : ""}${required} />
        <span>${escapeHtml(field.label)}</span>
      </label>
    `;
  }
  if (field.field_type === "textarea") {
    return `
      <label class="span-2 system-input">
        ${label}
        <textarea name="${escapeHtml(name)}" maxlength="2000" placeholder="${escapeHtml(field.label)}"${required}>${escapeHtml(value || "")}</textarea>
      </label>
    `;
  }
  if (field.field_type === "select") {
    return `
      <label class="system-input">
        ${label}
        <select name="${escapeHtml(name)}"${required}>
          <option value="">Not set</option>
          ${(field.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(value || "") === option ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
        </select>
      </label>
    `;
  }
  return `
    <label class="system-input">
      ${label}
      <input name="${escapeHtml(name)}" type="${field.field_type === "date" ? "date" : "text"}" maxlength="2000" value="${escapeHtml(value || "")}" placeholder="${escapeHtml(field.label)}"${required} />
    </label>
  `;
}

function accessProfileInputName(key) {
  return `access_profile.${key}`;
}

function collectAccessProfile() {
  const values = {};
  for (const field of activeAccessFields()) {
    const element = form.elements[accessProfileInputName(field.key)];
    if (!element) continue;
    if (field.field_type === "checkbox") {
      values[field.key] = Boolean(element.checked);
    } else {
      values[field.key] = String(element.value || "").trim();
    }
  }
  return values;
}

function renderAccessFieldCatalog() {
  const panel = document.querySelector("#accessCatalogPanel");
  if (!panel || !accessFieldList) return;
  panel.classList.toggle("locked", !canModifyEmployees());
  if (!canModifyEmployees()) {
    accessFieldForm.querySelectorAll("input, select, textarea, button").forEach((field) => {
      field.disabled = true;
    });
    accessFieldList.innerHTML = `<div class="empty-state"><strong>Domain Admin only</strong><span>Only members of ${escapeHtml(state.auth?.permissions?.adminGroup || "the configured admin group")} can change profile fields.</span></div>`;
    return;
  }
  accessFieldForm.querySelectorAll("input, select, textarea, button").forEach((field) => {
    field.disabled = false;
  });
  if (!state.accessFields.length) {
    accessFieldList.innerHTML = `<div class="empty-state"><strong>No fields configured</strong><span>Add the first access field.</span></div>`;
    return;
  }
  accessFieldList.innerHTML = state.accessFields
    .map(
      (field) => `
        <article class="access-field-item ${field.active ? "" : "inactive"}">
          <div>
            <strong>${escapeHtml(field.label)}</strong>
            <small>${escapeHtml(field.section)} / ${escapeHtml(labelize(field.field_type))}${field.required ? " / Required" : ""}</small>
          </div>
          <div class="field-actions">
            <button class="secondary-button system-button" type="button" data-access-field-action="edit" data-access-field-id="${field.id}">Edit</button>
            <button class="danger-button system-button" type="button" data-access-field-action="delete" data-access-field-id="${field.id}" ${field.active ? "" : "disabled"}>Remove</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderActivity() {
  const list = activityList || document.querySelector("#activityList");
  if (!list) return;
  list.innerHTML = ActivityFeed(state.audit);
}

function renderChangeRequests() {
  const list = document.querySelector("#changeRequestList");
  const summary = document.querySelector("#changeRequestSummary");
  const title = document.querySelector("#changeRequestTitle");
  if (!list || !summary) return;
  const pending = state.changeRequests || [];
  if (title) {
    title.textContent = canModifyEmployees() ? "Change Requests" : "My Change Requests";
  }
  const requestLabel = canModifyEmployees() ? "pending" : "submitted";
  summary.textContent = pending.length
    ? `${pending.length} ${requestLabel} ${pending.length === 1 ? "request" : "requests"}.`
    : canModifyEmployees()
      ? "No pending requests."
      : "No submitted requests.";
  setText("#changeQueueCount", String(pending.length));
  setText("#changeVerificationState", pending.length ? "MONITORING" : "DATA VERIFIED");
  setSurveillancePanelState(document.querySelector('[data-panel-key="change-requests"]'), {
    active: Boolean(pending.length),
    expanded: state.expandedPanels.has("change-requests"),
    warning: Boolean(pending.length),
  });
  if (!pending.length) {
    list.innerHTML = `<div class="mini-empty">${canModifyEmployees() ? "Nothing waiting for approval." : "Your submitted edits will appear here."}</div>`;
    return;
  }
  list.innerHTML = pending
    .map((request) => {
      const fields = Object.entries(request.payload || {})
        .map(([key, value]) => `${requestFieldLabel(key)}: ${formatRequestValue(value)}`)
        .join(" / ");
      const employeeName = request.employee_name || `Employee #${request.employee_id}`;
      const keyFob = request.employee_key_fob_id ? `Key fob ${request.employee_key_fob_id}` : "Record deleted";
      const actions = canModifyEmployees()
        ? `
          <div class="request-actions">
            <button class="rail-action system-button approve-action" type="button" data-request-action="approve" data-request-id="${request.id}">Approve</button>
            <button class="rail-action system-button muted-link" type="button" data-request-action="reject" data-request-id="${request.id}">Reject</button>
          </div>
        `
        : `<small>Waiting for Domain Admin approval.</small>`;
      return `
        <article class="change-request-item is-active">
          <strong>${escapeHtml(employeeName)}</strong>
          <small>${escapeHtml(keyFob)}</small>
          <span class="field-chip">${escapeHtml(fields || "No fields")}</span>
          <small>Requested by ${escapeHtml(request.requested_by || "Local user")} / ${escapeHtml(formatDateTime(request.requested_at))}</small>
          ${actions}
        </article>
      `;
    })
    .join("");
}

function renderInspectorMeta() {
  const panel = document.querySelector("#employeeInspectorMeta");
  const handoffState = document.querySelector("#handoffCompletionState");
  if (!panel) return;
  const employee = selectedEmployee();
  if (!employee) {
    panel.innerHTML = DetailInspector({
      stateName: "empty",
      rows: [
        ["Object", "New employee"],
        ["Status", "PROCESSING READY"],
        ["Source", "Local SQLite"],
      ],
    });
    if (handoffState) handoffState.textContent = "Select a profile";
    setSurveillancePanelState(document.querySelector('[data-panel-key="handoff"]'), {
      expanded: state.expandedPanels.has("handoff"),
    });
    return;
  }
  const completed = completedStepCount(employee);
  panel.innerHTML = DetailInspector({
    stateName: "selected detail",
    rows: [
      ["Object", employee.name],
      ["Status", activityStatusLabel(employee.status)],
      ["Owner", employee.manager || employee.department || "Unassigned"],
      ["Updated", formatDateTime(employee.updated_at)],
      ["Permission", canModifyEmployees() ? "DIRECT EDIT" : "REQUEST APPROVAL"],
      ["Access flow", `${completed}/4 steps`],
    ],
  });
  if (handoffState) {
    handoffState.textContent = completed === 4 ? "ACCESS GRANTED" : `${completed}/4 STEPS VERIFIED`;
  }
  setSurveillancePanelState(document.querySelector('[data-panel-key="handoff"]'), {
    active: Boolean(completed),
    expanded: state.expandedPanels.has("handoff"),
    warning: Boolean(completed && completed < 4),
  });
}

function formatRequestValue(value) {
  if (value && typeof value === "object") {
    return (
      Object.entries(value)
        .filter(([, item]) => item !== "" && item !== false && item != null)
        .map(([key, item]) => `${accessFieldLabel(key)} ${formatRequestValue(item)}`)
        .join(", ") || "(blank)"
    );
  }
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === 1) return "yes";
  if (value === 0) return "no";
  const text = String(value ?? "").trim();
  return text || "(blank)";
}

function requestFieldLabel(key) {
  if (key === "access_profile") return "Access Profile";
  return labelize(key);
}

function accessFieldLabel(key) {
  const field = state.accessFields.find((item) => item.key === key);
  return field?.label || labelize(key);
}

async function loadDiagnostics(showSuccess) {
  if (!canModifyEmployees() || state.diagnosticsLoading) return;
  state.diagnosticsLoading = true;
  SystemButton(document.querySelector("#refreshLogsButton"), { loading: true, disabled: true });
  try {
    const data = await api("/api/admin/diagnostics");
    state.diagnostics = data.diagnostics;
    renderDiagnostics();
    if (showSuccess) showToast("Logs refreshed");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.diagnosticsLoading = false;
    SystemButton(document.querySelector("#refreshLogsButton"), { loading: false, disabled: false });
  }
}

function renderDiagnostics() {
  const health = document.querySelector("#diagnosticHealth");
  const runtime = document.querySelector("#diagnosticRuntime");
  const storage = document.querySelector("#diagnosticStorage");
  const checks = document.querySelector("#diagnosticChecks");
  const database = document.querySelector("#diagnosticDatabase");
  const audit = document.querySelector("#diagnosticAudit");
  const requests = document.querySelector("#diagnosticRequests");
  if (!health || !runtime || !storage || !checks || !database || !audit || !requests) return;
  if (!canModifyEmployees()) {
    for (const node of [health, runtime, storage, checks, database, audit, requests]) {
      node.innerHTML = "";
    }
    return;
  }
  const diagnostics = state.diagnostics;
  if (!diagnostics) {
    health.innerHTML = `<div class="empty-state"><strong>No logs loaded</strong><span>Open Logs or refresh diagnostics.</span></div>`;
    runtime.innerHTML = "";
    storage.innerHTML = "";
    checks.innerHTML = "";
    database.innerHTML = "";
    audit.innerHTML = "";
    requests.innerHTML = "";
    return;
  }

  health.innerHTML = diagnosticCards([
    ["Service", diagnostics.health?.status || "unknown", diagnostics.generatedAt],
    ["Database", diagnostics.health?.database || "unknown", diagnostics.health?.checked_at],
    ["Network", diagnostics.network?.isLoopback ? "loopback" : "non-loopback", `Port ${diagnostics.network?.port || ""}`],
    ["Admin Gate", diagnostics.auth?.permissions?.canModifyEmployees ? "unlocked" : "locked", diagnostics.auth?.adminGroup],
  ]);
  runtime.innerHTML = diagnosticList([
    ["Server", diagnostics.runtime?.serverVersion],
    ["Python", diagnostics.runtime?.pythonVersion],
    ["Platform", diagnostics.runtime?.platform],
    ["Process", diagnostics.runtime?.processId],
    ["Working directory", diagnostics.runtime?.workingDirectory],
    ["Static directory", diagnostics.runtime?.staticDirectory],
  ]);
  storage.innerHTML = diagnosticList([
    ["Database path", diagnostics.storage?.path],
    ["Database exists", yesNo(diagnostics.storage?.exists)],
    ["Database size", formatBytes(diagnostics.storage?.sizeBytes)],
    ["Parent path", diagnostics.storage?.parent],
    ["Parent writable", yesNo(diagnostics.storage?.parentWritable)],
    ["Session secret", diagnostics.auth?.sessionPersistent ? "persistent" : "generated at startup"],
    ["Microsoft SSO", diagnostics.auth?.ssoConfigured ? "configured" : "not configured"],
    ["Microsoft Graph", diagnostics.auth?.graphConfigured ? "configured" : "not configured"],
  ]);
  checks.innerHTML = (diagnostics.checks || [])
    .map(
      (check) => `
        <article class="config-check ${escapeHtml(check.status)}">
          <strong>${escapeHtml(check.label)}</strong>
          <span>${escapeHtml(check.status)}</span>
          <p>${escapeHtml(check.message)}</p>
        </article>
      `
    )
    .join("");
  database.innerHTML = renderDatabaseDiagnostics(diagnostics.database || {});
  audit.innerHTML = renderAuditLogs(diagnostics.recentAudit || []);
  requests.innerHTML = renderRequestLogs(diagnostics.recentChangeRequests || []);
}

function diagnosticCards(items) {
  return items
    .map(
      ([label, value, detail]) => `
        <article class="diagnostic-stat">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value || "unknown")}</strong>
          <small>${escapeHtml(detail || "")}</small>
        </article>
      `
    )
    .join("");
}

function diagnosticList(items) {
  return items
    .map(
      ([label, value]) => `
        <div class="diagnostic-row">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value ?? "")}</strong>
        </div>
      `
    )
    .join("");
}

function renderDatabaseDiagnostics(database) {
  const counts = Object.entries(database.rowCounts || {});
  const rows = [
    ["Quick check", database.quickCheck || "unknown"],
    ["Journal mode", database.journalMode || "unknown"],
    ["Foreign keys", yesNo(database.foreignKeys)],
    ["Estimated size", formatBytes(database.estimatedBytes)],
    ...counts.map(([table, count]) => [`Rows in ${table}`, count]),
  ];
  return diagnosticList(rows);
}

function renderAuditLogs(logs) {
  if (!logs.length) {
    return `<div class="empty-state"><strong>No audit events</strong><span>Employee and admin actions will appear here.</span></div>`;
  }
  return logs
    .slice(0, 12)
    .map(
      (entry) => `
        <article class="activity-item compact-log">
          <span class="activity-action">${escapeHtml(labelize(entry.action))}</span>
          <div class="activity-copy">
            <strong>${escapeHtml(entry.summary)}</strong>
            <div class="activity-meta">
              <span>${escapeHtml(entry.actor || "Local user")}</span>
              <span>${formatDateTime(entry.created_at)}</span>
              <span>${escapeHtml(entry.entity_type || "")} #${escapeHtml(entry.entity_id ?? "")}</span>
            </div>
          </div>
        </article>
      `
    )
    .join("");
}

function renderRequestLogs(requests) {
  if (!requests.length) {
    return `<div class="empty-state"><strong>No change requests</strong><span>Submitted edits will appear here.</span></div>`;
  }
  return requests
    .slice(0, 12)
    .map((request) => {
      const fields = Object.entries(request.payload || {})
        .map(([key, value]) => `${requestFieldLabel(key)}: ${formatRequestValue(value)}`)
        .join(" / ");
      return `
        <article class="change-request-item compact-log">
          <strong>${escapeHtml(request.employee_name || `Employee #${request.employee_id}`)}</strong>
          <small>${escapeHtml(labelize(request.status))} / requested ${formatDateTime(request.requested_at)}</small>
          <span class="field-chip">${escapeHtml(fields || "No fields")}</span>
          <small>Requested by ${escapeHtml(request.requested_by || "Local user")}</small>
          ${request.reviewed_by ? `<small>Reviewed by ${escapeHtml(request.reviewed_by)} ${formatDateTime(request.reviewed_at)}</small>` : ""}
        </article>
      `;
    })
    .join("");
}

function loadExistingEmployeeByValue(field, value) {
  if (state.selectedId) return;
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return;
  const employee = state.employees.find((item) => String(item[field] || "").trim().toLowerCase() === normalized);
  if (!employee) return;
  selectEmployee(employee.id);
  showToast("Loaded existing employee from the database");
}

function autofillFromDepartment() {
  if (state.selectedId) return;
  const department = form.elements.department.value.trim().toLowerCase();
  if (!department) return;
  const match = [...state.employees]
    .reverse()
    .find((employee) => String(employee.department || "").trim().toLowerCase() === department);
  if (!match) return;
  let filled = false;
  for (const name of ["location", "manager"]) {
    if (!form.elements[name].value.trim() && match[name]) {
      form.elements[name].value = match[name];
      filled = true;
    }
  }
  if (filled) {
    showToast("Autofilled common fields from existing records");
  }
}

function selectEmployee(employeeId) {
  const employee = state.employees.find((item) => item.id === employeeId);
  if (!employee) return;
  if (state.activeTab !== "profiles") {
    setActiveTab("profiles", { push: true });
  }
  state.selectedId = employeeId;
  for (const [key, value] of Object.entries(employee)) {
    const field = form.elements[key];
    if (!field) continue;
    field.value = value ?? "";
  }
  renderAccessProfileFields(employee.access_profile || {});
  syncStepToggles(employee);
  document.querySelector("#formTitle").textContent = "Edit Employee";
  document.querySelector("#formSubtitle").textContent = canModifyEmployees()
    ? `Last saved ${formatDateTime(employee.updated_at)}.`
    : "Submit edits for Domain Admin approval.";
  document.querySelector("#selectedBadge").outerHTML = statusBadge(employee.status, "selectedBadge", employee);
  updateFormPermissions();
  renderEmployees();
  renderProfileEmployeeList();
  renderInspectorMeta();
}

function clearForm() {
  if (state.activeTab !== "profiles") {
    setActiveTab("profiles", { push: true });
  }
  state.selectedId = null;
  form.reset();
  for (const name of [
    "id",
    "employee_id",
    "name",
    "email",
    "department",
    "title",
    "location",
    "manager",
    "request_source",
    "access_needed",
    "notes",
  ]) {
    if (form.elements[name]) form.elements[name].value = "";
  }
  form.elements.status.value = "active";
  renderAccessProfileFields({});
  syncStepToggles({});
  document.querySelector("#formTitle").textContent = "Create Employee";
  document.querySelector("#formSubtitle").textContent = "Saved to SQLite immediately.";
  document.querySelector("#selectedBadge").outerHTML = InteractiveStatusChip({
    id: "selectedBadge",
    label: "New",
    tone: "muted",
    chipKey: "new",
    title: "NEW EMPLOYEE",
    detail: "No SQLite row exists until the form is saved.",
    tag: "span",
    classes: "status-badge",
  });
  setSaveButtonLabel("Create employee");
  updateFormPermissions();
  renderEmployees();
  renderProfileEmployeeList();
  renderInspectorMeta();
  form.elements.employee_id.focus();
}

async function saveEmployee() {
  const payload = formPayload();
  const id = state.selectedId;
  const path = id ? `/api/employees/${id}` : "/api/employees";
  const method = id ? "PATCH" : "POST";
  const saveButton = document.querySelector("#saveButton");
  SystemButton(saveButton, { loading: true, disabled: true });
  try {
    const result = await api(path, { method, body: payload });
    if (result.changeRequest) {
      await loadAll(false);
      if (id) selectEmployee(id);
      showToast("Change request submitted for admin approval");
      return;
    }
    state.selectedId = result.employee.id;
    await loadAll(false);
    selectEmployee(result.employee.id);
    showToast(id ? "Employee updated" : "Employee created");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    SystemButton(saveButton, { loading: false, disabled: false });
    updateFormPermissions();
  }
}

async function terminateSelectedEmployee() {
  if (!state.selectedId) return;
  const employee = selectedEmployee();
  if (!employee || employee.status === "terminated") return;
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const confirmed = window.confirm(`Mark ${employee.name} as terminated? Their profile stays in the database.`);
  if (!confirmed) return;
  const terminateButton = document.querySelector("#terminateButton");
  SystemButton(terminateButton, { loading: true, disabled: true });
  try {
    const result = await api(`/api/employees/${state.selectedId}`, {
      method: "PATCH",
      body: { status: "terminated", employee_notified: true },
    });
    await loadAll(false);
    selectEmployee(result.employee.id);
    showToast("Employee marked terminated");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    SystemButton(terminateButton, { loading: false });
    updateFormPermissions();
  }
}

async function reviewChangeRequest(event) {
  const button = event.target.closest("[data-request-action]");
  if (!button) return;
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const requestId = Number(button.dataset.requestId);
  const action = button.dataset.requestAction;
  if (!requestId || !["approve", "reject"].includes(action)) return;
  SystemButton(button, { loading: true, disabled: true });
  try {
    await api(`/api/change-requests/${requestId}/${action}`, { method: "POST", body: {} });
    await loadAll(false);
    showToast(action === "approve" ? "Change request approved" : "Change request rejected");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    SystemButton(button, { loading: false, disabled: false });
  }
}

async function deleteSelectedEmployee() {
  if (!state.selectedId) return;
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const employee = state.employees.find((item) => item.id === state.selectedId);
  if (!employee) return;
  const confirmed = window.confirm(`Delete ${employee.name}? This removes the employee record from the database.`);
  if (!confirmed) return;
  const deleteButton = document.querySelector("#deleteButton");
  SystemButton(deleteButton, { loading: true, disabled: true });
  try {
    await api(`/api/employees/${state.selectedId}`, { method: "DELETE" });
    clearForm();
    await loadAll(false);
    showToast("Employee deleted");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    SystemButton(deleteButton, { loading: false });
    updateFormPermissions();
  }
}

async function saveAccessField(event) {
  event.preventDefault();
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const id = state.editingAccessFieldId;
  const path = id ? `/api/access-fields/${id}` : "/api/access-fields";
  const method = id ? "PATCH" : "POST";
  const saveFieldButton = accessFieldForm.querySelector("button.primary-button");
  SystemButton(saveFieldButton, { loading: true, disabled: true });
  try {
    const data = await api(path, { method, body: accessFieldPayload() });
    await reloadAccessFields();
    resetAccessFieldForm();
    showToast(id ? "Access field updated" : "Access field added");
    renderAccessProfileFields(selectedEmployee()?.access_profile || {});
    renderAccessFieldCatalog();
    return data.accessField;
  } catch (error) {
    showToast(error.message, true);
  } finally {
    SystemButton(saveFieldButton, { loading: false });
    renderAccessFieldCatalog();
  }
}

async function reloadAccessFields() {
  const data = await api("/api/access-fields");
  state.accessFields = data.accessFields || [];
}

function accessFieldPayload() {
  return {
    label: accessFieldForm.elements.label.value.trim(),
    section: accessFieldForm.elements.section.value.trim(),
    fieldType: accessFieldForm.elements.fieldType.value,
    options: accessFieldForm.elements.options.value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean),
    required: accessFieldForm.elements.required.checked,
    sortOrder: accessFieldForm.elements.sortOrder.value.trim(),
  };
}

function resetAccessFieldForm() {
  state.editingAccessFieldId = null;
  accessFieldForm.reset();
  accessFieldForm.elements.id.value = "";
  accessFieldForm.elements.fieldType.value = "text";
  accessFieldForm.querySelector("button.primary-button").textContent = "Save field";
}

async function handleAccessFieldAction(event) {
  const button = event.target.closest("[data-access-field-action]");
  if (!button) return;
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const id = Number(button.dataset.accessFieldId);
  const action = button.dataset.accessFieldAction;
  const field = state.accessFields.find((item) => item.id === id);
  if (!field) return;
  if (action === "edit") {
    state.editingAccessFieldId = id;
    accessFieldForm.elements.id.value = field.id;
    accessFieldForm.elements.label.value = field.label || "";
    accessFieldForm.elements.section.value = field.section || "";
    accessFieldForm.elements.fieldType.value = field.field_type || "text";
    accessFieldForm.elements.sortOrder.value = field.sort_order ?? "";
    accessFieldForm.elements.required.checked = Boolean(field.required);
    accessFieldForm.elements.options.value = (field.options || []).join("\n");
    accessFieldForm.querySelector("button.primary-button").textContent = "Update field";
    accessFieldForm.elements.label.focus();
    return;
  }
  if (action === "delete") {
    const confirmed = window.confirm(`Remove ${field.label} from future profiles? Existing saved values stay in audit history.`);
    if (!confirmed) return;
    try {
      await api(`/api/access-fields/${id}`, { method: "DELETE" });
      await reloadAccessFields();
      if (state.editingAccessFieldId === id) resetAccessFieldForm();
      renderAccessProfileFields(selectedEmployee()?.access_profile || {});
      renderAccessFieldCatalog();
      showToast("Access field removed");
    } catch (error) {
      showToast(error.message, true);
    }
  }
}

async function syncEntraDirectory() {
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const button = document.querySelector("#syncEntraButton");
  const result = document.querySelector("#entraSyncResult");
  SystemButton(button, { loading: true, disabled: true });
  result.classList.add("is-processing-label");
  result.textContent = "SYNCING";
  try {
    const data = await api("/api/entra/sync", { method: "POST" });
    const sync = data.sync;
    result.textContent = `${sync.created} created / ${sync.updated} updated / ${sync.disabled} disabled`;
    await loadAll(false);
    showToast("Directory sync complete");
  } catch (error) {
    result.textContent = error.message;
    showToast(error.message, true);
  } finally {
    result.classList.remove("is-processing-label");
    SystemButton(button, { loading: false, disabled: !(state.auth && state.auth.graphConfigured && canModifyEmployees()) });
  }
}

async function loadConfig(showSuccess) {
  if (!canModifyEmployees() || state.configLoading) return;
  state.configLoading = true;
  SystemButton(document.querySelector("#refreshConfigButton"), { loading: true, disabled: true });
  try {
    const data = await api("/api/admin/config");
    state.config = data.config;
    state.configPreview = null;
    fillConfigForm(data.config);
    renderConfig();
    if (showSuccess) showToast("Configuration checks refreshed");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.configLoading = false;
    SystemButton(document.querySelector("#refreshConfigButton"), { loading: false, disabled: false });
  }
}

function fillConfigForm(config) {
  if (!configForm || !config) return;
  const runtime = config.runtime || {};
  configForm.elements.host.value = runtime.host || "127.0.0.1";
  configForm.elements.port.value = runtime.port || "8087";
  configForm.elements.databasePath.value = runtime.databasePath || "";
  configForm.elements.adminGroupCanonical.value = runtime.adminGroupCanonical || "";
  configForm.elements.tenantId.value = runtime.tenantId || "";
  configForm.elements.clientId.value = runtime.clientId || "";
  configForm.elements.redirectUri.value = runtime.redirectUri || "";
  configForm.elements.allowInsecureNetwork.checked = Boolean(runtime.allowInsecureNetwork);
  configForm.elements.sessionSecret.value = "";
  configForm.elements.clientSecret.value = "";
  const sessionMessage = config.secrets?.sessionSecret?.message || "";
  const clientMessage = config.secrets?.entraClientSecret?.message || "";
  document.querySelector("#secretStatus").textContent = [sessionMessage, clientMessage].filter(Boolean).join(" ");
  renderConfigSaveStatus(config);
}

function renderConfig() {
  const checks = document.querySelector("#configChecks");
  if (!checks || !configTemplate) return;
  if (!canModifyEmployees()) {
    checks.innerHTML = "";
    configTemplate.textContent = "";
    renderConfigSaveStatus(null);
    return;
  }
  const config = state.configPreview || state.config;
  if (!config) {
    checks.innerHTML = `<div class="empty-state"><strong>No checks loaded</strong><span>Open Configuration or refresh checks.</span></div>`;
    configTemplate.textContent = "";
    renderConfigSaveStatus(null);
    return;
  }
  checks.innerHTML = (config.checks || [])
    .map(
      (check) => `
        <article class="config-check ${escapeHtml(check.status)}">
          <strong>${escapeHtml(check.label)}</strong>
          <span>${escapeHtml(check.status)}</span>
          <p>${escapeHtml(check.message)}</p>
        </article>
      `
    )
    .join("");
  configTemplate.textContent = config.envTemplate || "";
  renderConfigSaveStatus(config);
}

function renderConfigSaveStatus(config) {
  const status = document.querySelector("#configSaveStatus");
  if (!status) return;
  if (!config) {
    status.textContent = "Saved settings are written to the server configuration file for verification.";
    return;
  }
  const destination = config.configFile?.path ? `Destination: ${config.configFile.path}.` : "";
  const writable = config.configFile?.writable === false ? " The current process cannot write there." : "";
  const save = config.saveStatus?.message || "Save to upload this configuration to the server env file.";
  const restart = config.saveStatus?.restartRequired
    ? " Restart Gatewatch for host, port, database, or session-secret changes to take full effect."
    : "";
  status.textContent = [save, destination + writable, restart].filter(Boolean).join(" ");
}

async function validateConfig(event) {
  event.preventDefault();
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  const submitButton = event.submitter || configForm.querySelector('button[type="submit"]');
  SystemButton(submitButton, { loading: true, disabled: true });
  try {
    const data = await api("/api/admin/config", {
      method: "POST",
      body: configPayload(),
    });
    state.config = data.config;
    state.configPreview = null;
    fillConfigForm(data.config);
    renderConfig();
    showToast("Configuration saved and verified");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    SystemButton(submitButton, { loading: false, disabled: false });
  }
}

function configPayload() {
  return {
    host: configForm.elements.host.value.trim(),
    port: configForm.elements.port.value.trim(),
    databasePath: configForm.elements.databasePath.value.trim(),
    adminGroupCanonical: configForm.elements.adminGroupCanonical.value.trim(),
    tenantId: configForm.elements.tenantId.value.trim(),
    clientId: configForm.elements.clientId.value.trim(),
    redirectUri: configForm.elements.redirectUri.value.trim(),
    sessionSecret: configForm.elements.sessionSecret.value,
    clientSecret: configForm.elements.clientSecret.value,
    allowInsecureNetwork: configForm.elements.allowInsecureNetwork.checked,
  };
}

async function copyConfigTemplate() {
  const text = configTemplate?.textContent?.trim();
  if (!text) {
    showToast("No configuration template to copy", true);
    return;
  }
  const copyButton = document.querySelector("#copyConfigButton");
  SystemButton(copyButton, { loading: true, disabled: true });
  try {
    await navigator.clipboard.writeText(text);
    showToast("Environment template copied");
  } catch (error) {
    showToast("Clipboard access was blocked by the browser", true);
  } finally {
    SystemButton(copyButton, { loading: false, disabled: false });
  }
}

function formPayload() {
  return {
    employee_id: form.elements.employee_id.value.trim(),
    name: form.elements.name.value.trim(),
    email: form.elements.email.value.trim(),
    department: form.elements.department.value.trim(),
    title: form.elements.title.value.trim(),
    location: form.elements.location.value.trim(),
    manager: form.elements.manager.value.trim(),
    status: form.elements.status.value,
    request_source: form.elements.request_source.value.trim(),
    access_needed: form.elements.access_needed.value.trim(),
    request_received: stepIsPressed("request_received"),
    manager_approved: stepIsPressed("manager_approved"),
    it_provisioned: stepIsPressed("it_provisioned"),
    employee_notified: stepIsPressed("employee_notified"),
    access_profile: collectAccessProfile(),
    notes: form.elements.notes.value.trim(),
  };
}

async function api(path, options = {}) {
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  const fetchOptions = { method: options.method || "GET", headers };
  if (options.body) {
    headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, fetchOptions);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Request failed with HTTP ${response.status}`);
  }
  return body;
}

function statusBadge(status, id = "", employee = null) {
  const safe = status === "terminated" || status === "disabled" ? status : "active";
  const title = safe === "active" ? "ACCESS GRANTED" : safe === "disabled" ? "DEGRADED" : "DENIED";
  const detail = employee
    ? `${employee.name || "Employee"} / ${employee.department || "Unassigned"} / updated ${formatDateTime(employee.updated_at) || "--"}`
    : "Employee status is stored in SQLite.";
  return InteractiveStatusChip({
    id,
    label: labelize(safe),
    tone: safe,
    chipKey: safe,
    title,
    detail,
    tag: "span",
    classes: "status-badge",
  });
}

function progressPill(employee) {
  const complete = completedStepCount(employee);
  const label = complete === 4 ? "ACCESS GRANTED" : complete === 0 ? "Not started" : `${complete}/4 verified`;
  const tone = complete === 4 ? "complete" : complete === 0 ? "muted" : "working";
  const source = employee.request_source ? ` by ${employee.request_source}` : "";
  const needed = employee.access_needed ? ` - ${employee.access_needed}` : "";
  return `
    ${InteractiveStatusChip({
      label,
      tone,
      chipKey: "handoff",
      title: complete === 4 ? "ACCESS GRANTED" : "MONITORING",
      detail: `${complete} of 4 access request handoff steps are verified.`,
      pulse: complete > 0 && complete < 4,
      tag: "span",
      classes: "progress-pill",
    })}
    <small class="progress-note">${escapeHtml(`${source}${needed}`.trim())}</small>
  `;
}

function completedStepCount(employee) {
  return [
    employee?.request_received,
    employee?.manager_approved,
    employee?.it_provisioned,
    employee?.employee_notified,
  ].filter(Boolean).length;
}

function syncStepToggles(employee) {
  document.querySelectorAll("[data-step]").forEach((button) => {
    setStepState(button.dataset.step, Boolean(employee[button.dataset.step]), { silent: true });
  });
}

function setStepState(step, active, options = {}) {
  const button = document.querySelector(`[data-step="${step}"]`);
  if (!button) return;
  button.setAttribute("aria-pressed", active ? "true" : "false");
  button.classList.toggle("complete", active);
  if (!options.silent) {
    button.blur();
    setText("#handoffCompletionState", `${currentStepCountFromButtons()}/4 STEPS SELECTED`);
  }
}

function stepIsPressed(step) {
  return document.querySelector(`[data-step="${step}"]`)?.getAttribute("aria-pressed") === "true";
}

function currentStepCountFromButtons() {
  return ["request_received", "manager_approved", "it_provisioned", "employee_notified"].filter(stepIsPressed).length;
}

function canModifyEmployees() {
  return Boolean(state.auth?.permissions?.canModifyEmployees);
}

function tabIsAllowed(tab) {
  return !ADMIN_TABS.has(tab) || canModifyEmployees();
}

function requiredGroupMessage() {
  const group = state.auth?.permissions?.adminGroup || "the configured admin group";
  return `Only members of ${group} can approve changes, delete, sync, view logs, or view configuration.`;
}

function updateFormPermissions() {
  form.querySelectorAll("input, select, textarea").forEach((field) => {
    if (field.type === "hidden") return;
    field.disabled = false;
  });
  document.querySelectorAll("[data-step]").forEach((button) => {
    button.disabled = false;
  });
  const deleteButton = document.querySelector("#deleteButton");
  const terminateButton = document.querySelector("#terminateButton");
  const saveButton = document.querySelector("#saveButton");
  deleteButton.disabled = !state.selectedId || !canModifyEmployees();
  terminateButton.disabled = !state.selectedId || !canModifyEmployees() || selectedEmployee()?.status === "terminated";
  saveButton.disabled = false;
  if (state.selectedId && !canModifyEmployees()) {
    setSaveButtonLabel("Request changes");
    saveButton.title = "Submit a change request for Domain Admin approval.";
  } else {
    setSaveButtonLabel(state.selectedId ? "Save changes" : "Create employee");
    saveButton.title = "";
  }
  deleteButton.title = state.selectedId && !canModifyEmployees() ? requiredGroupMessage() : "";
  terminateButton.title = state.selectedId && !canModifyEmployees() ? requiredGroupMessage() : "";
  renderAccessFieldCatalog();
}

function setSaveButtonLabel(label) {
  const labelNode = document.querySelector("#saveButton span");
  if (labelNode) {
    labelNode.textContent = label;
  }
}

function labelize(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value;
}

function activityKey(entry) {
  return String(entry.id || `${entry.created_at || "event"}-${entry.action || "change"}-${entry.entity_id || ""}`);
}

function activitySeverity(action) {
  const normalized = String(action || "").toLowerCase();
  if (normalized.includes("delete") || normalized.includes("reject") || normalized.includes("terminate")) return "critical";
  if (normalized.includes("request") || normalized.includes("disable")) return "warning";
  return "secure";
}

function activityStatusLabel(action) {
  const normalized = String(action || "").toLowerCase();
  if (normalized === "active" || normalized.includes("create") || normalized.includes("approve") || normalized.includes("sync")) return "DATA VERIFIED";
  if (normalized === "disabled" || normalized.includes("request") || normalized.includes("update")) return "MONITORING";
  if (normalized === "terminated" || normalized.includes("delete") || normalized.includes("reject")) return "ACCESS DENIED";
  return labelize(action || "verified");
}

function initials(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return `${parts[0]?.[0] || "?"}${parts.length > 1 ? parts[parts.length - 1][0] : ""}`;
}

function formatDateTime(value) {
  if (!value) return "";
  return String(value).replace("T", " ").replace("Z", "");
}

function formatCompactDateTime(value) {
  const text = formatDateTime(value);
  const match = text.match(/^\d{4}-(\d{2}-\d{2})\s+(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : text;
}

function yesNo(value) {
  return value ? "yes" : "no";
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove("show"), 3200);
}
