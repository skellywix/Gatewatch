const TABS = ["overview", "users", "activity", "backend"];
const ADMIN_TABS = new Set(["backend"]);
const FILTERS = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "inProgress", label: "In Progress", tone: "warning" },
  { key: "disabled", label: "Disabled", tone: "warning" },
  { key: "terminated", label: "Terminated", tone: "critical" },
];
const CHECKLIST_FIELDS = ["request_received", "manager_approved", "it_provisioned", "employee_notified"];
const EMPTY_EMPLOYEE = {
  id: "",
  employee_id: "",
  name: "",
  email: "",
  department: "",
  title: "",
  location: "",
  manager: "",
  status: "active",
  request_source: "",
  access_needed: "",
  request_received: 0,
  manager_approved: 0,
  it_provisioned: 0,
  employee_notified: 0,
  access_profile: {},
  notes: "",
};

const state = {
  employees: [],
  accessFields: [],
  changeRequests: [],
  audit: [],
  auth: null,
  config: null,
  diagnostics: null,
  summary: {},
  activeTab: tabFromHash(),
  filter: "all",
  overviewQuery: "",
  userQuery: "",
  loading: true,
  backendLoading: false,
  loadError: "",
  selectedId: null,
  expandedProfileId: null,
  selectedActivityKey: null,
  editingAccessFieldId: null,
  lastFetchedAt: "",
  loadedOnce: false,
  recentUntil: 0,
  metricSnapshot: {},
};

const ui = {
  primaryAction: document.querySelector("#primaryAction"),
  tabs: document.querySelector(".tabs"),
  backendTab: document.querySelector("#backendTab"),
  searchField: document.querySelector("#searchField"),
  searchInput: document.querySelector("#searchInput"),
  searchHelp: document.querySelector("#searchHelp"),
  statusFilters: document.querySelector("#statusFilters"),
  metrics: document.querySelector("#metrics"),
  monitoringList: document.querySelector("#monitoringList"),
  signalCount: document.querySelector("#signalCount"),
  activityFeed: document.querySelector("#activityFeed"),
  activityCount: document.querySelector("#activityCount"),
  detailInspector: document.querySelector("#detailInspector"),
  statusLight: document.querySelector("#overallStatusLight"),
  statusText: document.querySelector("#overallStatusText"),
  lastUpdated: document.querySelector("#lastUpdated"),
  userSearchField: document.querySelector("#userSearchField"),
  userSearchInput: document.querySelector("#userSearchInput"),
  userSearchHelp: document.querySelector("#userSearchHelp"),
  userSearchOptions: document.querySelector("#userSearchOptions"),
  userListCount: document.querySelector("#userListCount"),
  userProfileList: document.querySelector("#userProfileList"),
  newUserButton: document.querySelector("#newUserButton"),
  userForm: document.querySelector("#userForm"),
  userFormTitle: document.querySelector("#userFormTitle"),
  userFormSubtitle: document.querySelector("#userFormSubtitle"),
  formModeBadge: document.querySelector("#formModeBadge"),
  customAccessFields: document.querySelector("#customAccessFields"),
  customFieldCount: document.querySelector("#customFieldCount"),
  deleteUserButton: document.querySelector("#deleteUserButton"),
  clearUserButton: document.querySelector("#clearUserButton"),
  saveUserButton: document.querySelector("#saveUserButton"),
  activityActor: document.querySelector("#activityActor"),
  activityLogList: document.querySelector("#activityLogList"),
  refreshBackendButton: document.querySelector("#refreshBackendButton"),
  syncDirectoryButton: document.querySelector("#syncDirectoryButton"),
  backendConfigSummary: document.querySelector("#backendConfigSummary"),
  backendConfigBody: document.querySelector("#backendConfigBody"),
  adminLogBody: document.querySelector("#adminLogBody"),
  adminLogsSummary: document.querySelector("#adminLogsSummary"),
  toast: document.querySelector("#toast"),
};

ui.primaryAction.addEventListener("click", () => loadAll({ announce: true, delay: 360 }));
ui.tabs.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  if (!tab || tab.disabled) return;
  setActiveTab(tab.dataset.tab);
});
ui.searchInput.addEventListener("input", (event) => {
  state.overviewQuery = event.target.value;
  renderOverview();
});
ui.searchInput.addEventListener("focus", () => renderSearchState(ui.searchField, ui.searchInput, ui.searchHelp, "Filter monitored records."));
ui.searchInput.addEventListener("blur", () => renderSearchState(ui.searchField, ui.searchInput, ui.searchHelp, "Filter monitored records."));
ui.userSearchInput.addEventListener("input", (event) => {
  state.userQuery = event.target.value;
  renderUsers();
});
ui.userSearchInput.addEventListener("change", () => selectEmployeeFromSearch(ui.userSearchInput.value));
ui.userSearchInput.addEventListener("focus", () => renderSearchState(ui.userSearchField, ui.userSearchInput, ui.userSearchHelp, "Search autofills the list from saved users."));
ui.userSearchInput.addEventListener("blur", () => renderSearchState(ui.userSearchField, ui.userSearchInput, ui.userSearchHelp, "Search autofills the list from saved users."));
ui.statusFilters.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-filter]");
  if (!chip || chip.disabled) return;
  state.filter = chip.dataset.filter;
  renderOverview();
});
ui.monitoringList.addEventListener("click", (event) => {
  const item = event.target.closest("[data-signal-id]");
  if (!item) return;
  selectEmployee(Number(item.dataset.signalId), { openUsers: false });
});
ui.monitoringList.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const item = event.target.closest("[data-signal-id]");
  if (!item) return;
  event.preventDefault();
  selectEmployee(Number(item.dataset.signalId), { openUsers: false });
});
ui.detailInspector.addEventListener("click", (event) => {
  if (!event.target.closest("[data-dismiss-inspector]")) return;
  state.selectedId = null;
  state.selectedActivityKey = null;
  renderOverview();
});
ui.activityFeed.addEventListener("click", (event) => selectActivityFromEvent(event));
ui.activityFeed.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  selectActivityFromEvent(event);
});
ui.activityLogList.addEventListener("click", (event) => selectActivityFromEvent(event));
ui.activityLogList.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  selectActivityFromEvent(event);
});
ui.userProfileList.addEventListener("click", (event) => {
  const expand = event.target.closest("[data-expand-profile]");
  if (expand) {
    event.stopPropagation();
    toggleProfileExpansion(Number(expand.dataset.expandProfile));
    return;
  }
  const profile = event.target.closest("[data-profile-id]");
  if (!profile) return;
  selectEmployee(Number(profile.dataset.profileId), { openUsers: false, expand: true });
});
ui.userProfileList.addEventListener("keydown", (event) => {
  const profile = event.target.closest("[data-profile-id]");
  if (!profile) return;
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    selectEmployee(Number(profile.dataset.profileId), { openUsers: false, expand: true });
  }
  if (event.key.toLowerCase() === "e") {
    event.preventDefault();
    toggleProfileExpansion(Number(profile.dataset.profileId));
  }
});
ui.newUserButton.addEventListener("click", () => clearUserForm({ focus: true }));
ui.clearUserButton.addEventListener("click", () => clearUserForm({ focus: true }));
ui.deleteUserButton.addEventListener("click", deleteSelectedEmployee);
ui.userForm.addEventListener("submit", saveUser);
ui.refreshBackendButton.addEventListener("click", () => loadBackend({ announce: true }));
ui.syncDirectoryButton.addEventListener("click", syncDirectory);
ui.backendConfigBody.addEventListener("submit", (event) => {
  if (event.target.closest("#backendConfigForm")) saveBackendConfig(event);
  if (event.target.closest("#accessFieldForm")) saveAccessField(event);
});
ui.backendConfigBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-validate-config]");
  if (button) {
    validateBackendConfig(button);
    return;
  }
  handleAccessFieldAction(event);
});
ui.adminLogBody.addEventListener("click", reviewChangeRequest);
window.addEventListener("hashchange", () => setActiveTab(tabFromHash(), { replace: true }));

loadAll();

async function loadAll({ announce = false, delay = 0 } = {}) {
  if (state.loading && announce) return;
  state.loading = true;
  state.loadError = "";
  render();
  try {
    const [data] = await Promise.all([api("/api/bootstrap"), wait(delay)]);
    state.summary = data.summary || {};
    state.employees = data.employees || [];
    state.accessFields = data.accessFields || [];
    state.changeRequests = data.changeRequests || [];
    state.audit = data.audit || [];
    state.auth = data.auth || null;
    state.lastFetchedAt = new Date().toISOString();
    state.recentUntil = Date.now() + 4200;
    clearInvalidSelection();
    if (!state.selectedId && state.employees.length) {
      state.selectedId = state.employees[0].id;
      fillUserForm(selectedEmployee());
    } else if (state.selectedId) {
      fillUserForm(selectedEmployee());
    } else {
      fillUserForm(null);
    }
    if (announce) showToast("Updated");
  } catch (error) {
    state.loadError = error.message || "Unable to load Gatewatch data.";
    showToast(state.loadError, true);
  } finally {
    state.loading = false;
    if (!state.loadedOnce) {
      const requestedTab = tabFromHash();
      if (tabAllowed(requestedTab)) state.activeTab = requestedTab;
      state.loadedOnce = true;
    }
    if (!tabAllowed(state.activeTab)) state.activeTab = "overview";
    render();
  }
}

function render() {
  renderHeader();
  renderTabs();
  renderOverview();
  renderUsers();
  renderActivity();
  renderBackend();
  renderBusyState();
}

function renderHeader() {
  const status = overallStatus();
  ui.statusLight.className = `status-light status-light--${status.key} ${status.pulse ? "is-pulsing" : ""}`;
  ui.statusLight.setAttribute("aria-label", `System status ${status.label.toLowerCase()}`);
  ui.statusText.textContent = status.label;
  ui.lastUpdated.textContent = `Last updated ${state.lastFetchedAt ? formatTime(state.lastFetchedAt) : "--"}`;
  setButtonLoading(ui.primaryAction, state.loading);
  if (ui.syncDirectoryButton) ui.syncDirectoryButton.disabled = !isAdmin();
}

function renderTabs() {
  ui.backendTab.hidden = !isAdmin();
  ui.backendTab.disabled = !isAdmin();
  if (!tabAllowed(state.activeTab)) state.activeTab = "overview";
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === state.activeTab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    const active = panel.dataset.panel === state.activeTab;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
  if (state.activeTab === "backend" && isAdmin() && !state.diagnostics && !state.backendLoading) {
    loadBackend();
  }
}

function renderOverview() {
  renderSearchState(ui.searchField, ui.searchInput, ui.searchHelp, "Filter monitored records.");
  renderFilters();
  renderMetrics();
  renderMonitoringItems();
  renderOverviewActivity();
  renderInspector();
}

function renderUsers() {
  renderSearchState(ui.userSearchField, ui.userSearchInput, ui.userSearchHelp, "Search autofills the list from saved users.");
  setDatalistOptions(ui.userSearchOptions, state.employees.flatMap((employee) => [employee.name, employee.employee_id, employee.email]));
  renderProfileList();
  renderCustomFields(selectedEmployee()?.access_profile || {});
  updateFormState();
}

function renderActivity() {
  const actor = state.auth?.permissions?.actor || "Local user";
  const entries = currentActorAudit();
  ui.activityActor.textContent = `Current actor: ${actor}.`;
  if (!entries.length) {
    ui.activityLogList.innerHTML = emptyState("No activity", "No changes recorded for this actor.");
    return;
  }
  ui.activityLogList.innerHTML = entries.slice(0, 100).map((entry) => renderActivityRow(entry, { long: true })).join("");
}

function renderBackend() {
  if (!isAdmin()) {
    ui.backendConfigBody.innerHTML = emptyState("Admin only", "Sign in as a configured admin to view backend configuration.");
    ui.adminLogBody.innerHTML = "";
    return;
  }
  if (state.backendLoading) {
    ui.backendConfigBody.innerHTML = emptyState("Loading", "Gathering backend configuration and diagnostics.");
    ui.adminLogBody.innerHTML = "";
    return;
  }
  if (!state.config && !state.diagnostics) {
    ui.backendConfigBody.innerHTML = emptyState("Not loaded", "Open this tab or refresh logs.");
    ui.adminLogBody.innerHTML = "";
    return;
  }
  const config = state.config || {};
  const diagnostics = state.diagnostics || {};
  ui.backendConfigSummary.textContent = config.saveStatus?.message || "Admin-only runtime checks.";
  ui.adminLogsSummary.textContent = diagnostics.generatedAt ? `Generated ${formatDateTime(diagnostics.generatedAt)}.` : "Runtime, auth, storage, database, audit, and change queue evidence.";
  ui.backendConfigBody.innerHTML = `
    ${renderBackendConfigForm(config)}
    ${renderAccessFieldManager()}
    <div class="diagnostic-grid">
      ${renderConfigChecks(config.checks || [])}
    </div>
    <section class="log-card">
      <h3>Runtime</h3>
      ${metadataList([
        ["Host", diagnostics.network?.host || config.runtime?.host],
        ["Port", diagnostics.network?.port || config.runtime?.port],
        ["Auth mode", config.runtime?.authMode || diagnostics.auth?.provider],
        ["Database", config.runtime?.databasePath || diagnostics.storage?.path],
        ["Config file", config.configFile?.path],
      ])}
    </section>
  `;
  ui.adminLogBody.innerHTML = `
    <section class="log-card">
      <h3>Service</h3>
      ${metadataList([
        ["Health", diagnostics.health?.status],
        ["Python", diagnostics.runtime?.pythonVersion],
        ["Platform", diagnostics.runtime?.platform],
        ["Process", diagnostics.runtime?.processId],
        ["Working dir", diagnostics.runtime?.workingDirectory],
      ])}
    </section>
    <section class="log-card">
      <h3>Auth</h3>
      ${metadataList([
        ["SSO", yesNo(diagnostics.auth?.ssoConfigured)],
        ["Graph", yesNo(diagnostics.auth?.graphConfigured)],
        ["Admin group", diagnostics.auth?.adminGroup],
        ["Permission", diagnostics.auth?.permissions?.canModifyEmployees ? "admin" : "viewer"],
        ["Actor", diagnostics.auth?.permissions?.actor],
      ])}
    </section>
    <section class="log-card">
      <h3>Storage</h3>
      ${metadataList([
        ["Path", diagnostics.storage?.path],
        ["Exists", yesNo(diagnostics.storage?.exists)],
        ["Parent writable", yesNo(diagnostics.storage?.parentWritable)],
        ["Size", formatBytes(diagnostics.storage?.sizeBytes)],
        ["SQLite", diagnostics.database?.quickCheck],
      ])}
    </section>
    <section class="log-card">
      <h3>Database Rows</h3>
      ${metadataList(Object.entries(diagnostics.database?.rowCounts || {}).map(([key, value]) => [labelize(key), value]))}
    </section>
    <section class="log-card span-2">
      <h3>Recent Audit</h3>
      ${renderAdminEvents(diagnostics.recentAudit || [])}
    </section>
    <section class="log-card span-2">
      <h3>Change Requests</h3>
      ${renderAdminRequests(diagnostics.recentChangeRequests || [])}
    </section>
  `;
}

function renderFilters() {
  const counts = filterCounts();
  ui.statusFilters.innerHTML = FILTERS.map((filter) => {
    const count = filter.key === "all" ? state.employees.length : counts[filter.key] || 0;
    const selected = state.filter === filter.key;
    const disabled = filter.key !== "all" && count === 0;
    const classes = ["chip", filter.tone ? `chip--${filter.tone}` : ""].filter(Boolean).join(" ");
    return `
      <button class="${classes}" type="button" data-filter="${escapeHtml(filter.key)}" aria-pressed="${selected ? "true" : "false"}" ${disabled ? "disabled" : ""}>
        <span>${escapeHtml(filter.label)}</span>
        <span class="chip-count">${count}</span>
      </button>
    `;
  }).join("");
}

function renderMetrics() {
  const counts = filterCounts();
  const metrics = [
    ["Total Users", state.summary.total ?? state.employees.length, state.employees.length ? "SQLite" : "Idle", "default"],
    ["Active", state.summary.active ?? counts.active, "Enabled profiles", "online"],
    ["In Progress", state.summary.inProgress ?? counts.inProgress, counts.inProgress ? "Handoff pending" : "Clear", counts.inProgress ? "warning" : "default"],
    ["Terminated", state.summary.terminated ?? counts.terminated, counts.terminated ? "Review access" : "Clear", counts.terminated ? "critical" : "default"],
  ];
  ui.metrics.innerHTML = metrics
    .map(([label, value, detail, tone]) => {
      const classes = [
        "metric-card",
        state.loading ? "is-loading" : "",
        tone === "warning" ? "is-warning" : "",
        tone === "critical" ? "is-critical" : "",
      ].filter(Boolean).join(" ");
      return `<article class="${classes}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></article>`;
    })
    .join("");
}

function renderMonitoringItems() {
  const items = visibleOverviewEmployees();
  ui.signalCount.textContent = `${items.length} ${items.length === 1 ? "record" : "records"}`;
  if (!items.length) {
    ui.monitoringList.innerHTML = emptyState(state.employees.length ? "No matching users" : "No users", state.employees.length ? "Adjust search or filters." : "Add the first user on the Users tab.");
    return;
  }
  ui.monitoringList.innerHTML = items.map((employee, index) => renderSignalItem(employee, index)).join("");
}

function renderSignalItem(employee, index) {
  const status = signalStatus(employee);
  const selected = state.selectedId === employee.id;
  const recent = index === 0 && isRecentlyUpdated();
  const pulse = shouldPulse(status.key) || recent;
  return `
    <article class="signal-item is-${escapeHtml(status.key)} ${selected ? "is-selected" : ""} ${recent ? "is-recent" : ""}" role="option" tabindex="0" aria-selected="${selected ? "true" : "false"}" data-signal-id="${employee.id}">
      <span class="status-light status-light--${escapeHtml(status.key)} ${pulse ? "is-pulsing" : ""}" role="img" aria-label="${escapeHtml(`${employee.name} ${status.label}`)}"></span>
      <div class="signal-copy">
        <strong>${escapeHtml(employee.name || "Unnamed user")}</strong>
        <span>${escapeHtml(employee.employee_id || "No ID")} / ${escapeHtml(employee.department || employee.location || "SQLite")}</span>
        <div class="signal-meta">
          <span class="severity severity--${escapeHtml(status.key)}">${escapeHtml(status.label)}</span>
          <span class="mono">${escapeHtml(formatCompactDate(employee.updated_at))}</span>
          <span class="mono">${healthValue(employee)}%</span>
        </div>
      </div>
    </article>
  `;
}

function renderOverviewActivity() {
  const entries = state.audit.slice(0, 8);
  ui.activityCount.textContent = `${entries.length} ${entries.length === 1 ? "event" : "events"}`;
  if (!entries.length) {
    ui.activityFeed.innerHTML = emptyState("No activity", "Changes appear here after the first save.");
    return;
  }
  ui.activityFeed.innerHTML = entries.map((entry) => renderActivityRow(entry)).join("");
}

function renderActivityRow(entry, { long = false } = {}) {
  const key = activityKey(entry);
  const selected = state.selectedActivityKey === key;
  const severity = activitySeverity(entry);
  const title = entry.summary || labelize(entry.action || "event");
  return `
    <button class="activity-row is-${escapeHtml(severity.key)} ${selected ? "is-selected" : ""}" type="button" role="listitem" data-activity-key="${escapeHtml(key)}" aria-selected="${selected ? "true" : "false"}">
      <span class="activity-time">${escapeHtml(long ? formatDateTime(entry.created_at) : formatCompactTime(entry.created_at))}</span>
      <span class="activity-copy">
        <strong><span class="severity severity--${escapeHtml(severity.key)}">${escapeHtml(severity.label)}</span> / ${escapeHtml(title)}</strong>
        <span>${escapeHtml(entry.actor || "Local user")}${long ? ` / ${escapeHtml(labelize(entry.entity_type || "record"))} ${escapeHtml(entry.entity_id || "")}` : ""}</span>
      </span>
    </button>
  `;
}

function renderInspector() {
  ui.detailInspector.setAttribute("aria-busy", state.loading ? "true" : "false");
  const activity = state.audit.find((entry) => activityKey(entry) === state.selectedActivityKey);
  if (activity) {
    const severity = activitySeverity(activity);
    setInspector(severity.key, "Inspector", activity.summary || labelize(activity.action), "Audit event selected.", [
      ["ID", activity.id],
      ["Severity", severity.label],
      ["Actor", activity.actor],
      ["Timestamp", formatDateTime(activity.created_at)],
      ["Object", `${labelize(activity.entity_type || "record")} ${activity.entity_id || ""}`.trim()],
    ]);
    return;
  }
  const employee = selectedEmployee();
  if (employee) {
    const status = signalStatus(employee);
    setInspector(status.key, "Inspector", employee.name || "User", employee.access_needed || "User profile selected.", [
      ["ID", employee.employee_id],
      ["Status", status.label],
      ["Email", employee.email],
      ["Source", employee.department || employee.location || "SQLite"],
      ["Updated", formatDateTime(employee.updated_at)],
      ["Flow", `${completedStepCount(employee)}/4`],
    ]);
    return;
  }
  ui.detailInspector.dataset.state = "empty";
  ui.detailInspector.innerHTML = `<div class="inspector-empty"><h2 id="inspectorTitle">Inspector</h2><p>Select a user or activity row.</p></div>`;
}

function renderProfileList() {
  const users = visibleUserEmployees();
  ui.userListCount.textContent = `${users.length} ${users.length === 1 ? "user" : "users"}`;
  if (!users.length) {
    ui.userProfileList.innerHTML = emptyState(state.employees.length ? "No matching users" : "No users", state.employees.length ? "Refine the database search." : "Create a user with the form.");
    return;
  }
  ui.userProfileList.innerHTML = users.map(renderProfileCard).join("");
}

function renderProfileCard(employee) {
  const status = signalStatus(employee);
  const selected = state.selectedId === employee.id;
  const expanded = state.expandedProfileId === employee.id;
  return `
    <article class="profile-card is-${escapeHtml(status.key)} ${selected ? "is-selected" : ""} ${expanded ? "is-expanded" : ""}" role="option" tabindex="0" aria-selected="${selected ? "true" : "false"}" aria-expanded="${expanded ? "true" : "false"}" data-profile-id="${employee.id}">
      <span class="status-light status-light--${escapeHtml(status.key)} ${shouldPulse(status.key) ? "is-pulsing" : ""}" aria-hidden="true"></span>
      <div class="profile-copy">
        <strong>${escapeHtml(employee.name || "Unnamed user")}</strong>
        <span>${escapeHtml(employee.employee_id || "No ID")} / ${escapeHtml(employee.email || "No email")}</span>
        <small>${escapeHtml(employee.department || "Unassigned")} / ${escapeHtml(status.label)} / ${completedStepCount(employee)}/4</small>
      </div>
      <button class="signal-expand" type="button" data-expand-profile="${employee.id}" aria-expanded="${expanded ? "true" : "false"}" aria-label="${expanded ? "Collapse" : "Expand"} ${escapeHtml(employee.name || "user")}">${expanded ? "-" : "+"}</button>
      <div class="profile-details">
        ${metadataList([
          ["Title", employee.title || "--"],
          ["Location", employee.location || "--"],
          ["Manager", employee.manager || "--"],
          ["Access", employee.access_needed || "--"],
          ["Updated", formatDateTime(employee.updated_at)],
          ["Notes", employee.notes || "--"],
        ])}
      </div>
    </article>
  `;
}

function renderCustomFields(values = {}) {
  const fields = activeAccessFields();
  ui.customFieldCount.textContent = `${fields.length} ${fields.length === 1 ? "field" : "fields"}`;
  if (!fields.length) {
    ui.customAccessFields.innerHTML = `<div class="empty-state"><div><strong>No custom fields</strong><span>Admins can configure form fields from the backend.</span></div></div>`;
    return;
  }
  ui.customAccessFields.innerHTML = fields.map((field) => renderCustomField(field, values[field.key])).join("");
}

function renderCustomField(field, value) {
  const name = `access_profile.${field.key}`;
  const required = field.required ? " required" : "";
  const label = `<span>${escapeHtml(field.label)}</span>`;
  if (field.field_type === "checkbox") {
    return `<label class="toggle-field"><input name="${escapeHtml(name)}" type="checkbox" ${value ? "checked" : ""}${required} /> ${escapeHtml(field.label)}</label>`;
  }
  if (field.field_type === "textarea") {
    return `<label class="span-2">${label}<textarea name="${escapeHtml(name)}" maxlength="2000"${required}>${escapeHtml(value || "")}</textarea></label>`;
  }
  if (field.field_type === "select") {
    return `
      <label>${label}<select name="${escapeHtml(name)}"${required}>
        <option value="">Not set</option>
        ${(field.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(value || "") === option ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select></label>
    `;
  }
  return `<label>${label}<input name="${escapeHtml(name)}" type="${field.field_type === "date" ? "date" : "text"}" maxlength="2000" value="${escapeHtml(value || "")}"${required} /></label>`;
}

function setInspector(stateName, title, heading, description, rows) {
  ui.detailInspector.dataset.state = stateName;
  ui.detailInspector.innerHTML = `
    <div class="inspector-top">
      <h2 id="inspectorTitle">${escapeHtml(title)}</h2>
      <button class="button button--ghost" type="button" data-dismiss-inspector>Close</button>
    </div>
    <div class="inspector-body">
      <div class="inspector-summary"><strong>${escapeHtml(heading)}</strong><p>${escapeHtml(description)}</p></div>
      ${metadataList(rows)}
    </div>
  `;
}

function updateFormState() {
  const employee = selectedEmployee();
  ui.userFormTitle.textContent = employee ? "Modify User" : "Add User";
  ui.userFormSubtitle.textContent = employee
    ? isAdmin()
      ? `Last saved ${formatDateTime(employee.updated_at)}.`
      : "Edits submit as a change request until an admin approves them."
    : "Create a user in SQLite.";
  ui.formModeBadge.textContent = employee ? "Selected" : "New";
  ui.deleteUserButton.disabled = !employee || !isAdmin();
  ui.deleteUserButton.title = !employee ? "" : isAdmin() ? "" : "Only admins can delete users.";
  ui.saveUserButton.textContent = employee && !isAdmin() ? "Request Change" : employee ? "Save User" : "Add User";
}

async function saveUser(event) {
  event.preventDefault();
  const payload = formPayload();
  const employee = selectedEmployee();
  const path = employee ? `/api/employees/${employee.id}` : "/api/employees";
  const method = employee ? "PATCH" : "POST";
  setButtonLoading(ui.saveUserButton, true);
  try {
    const result = await api(path, { method, body: payload });
    if (result.changeRequest) {
      showToast("Change request submitted");
      await loadAll();
      return;
    }
    state.selectedId = result.employee.id;
    state.expandedProfileId = result.employee.id;
    await loadAll();
    showToast(employee ? "User saved" : "User added");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(ui.saveUserButton, false);
    updateFormState();
  }
}

async function validateBackendConfig(button) {
  const form = button.closest("#backendConfigForm");
  if (!form || !isAdmin()) return;
  setButtonLoading(button, true);
  try {
    const result = await api("/api/admin/config/validate", { method: "POST", body: backendConfigPayload(form) });
    const preview = form.querySelector("#configPreview");
    if (preview) preview.innerHTML = renderConfigPreview(result.preview);
    showToast("Configuration validated");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function saveBackendConfig(event) {
  event.preventDefault();
  const form = event.target.closest("#backendConfigForm");
  if (!form || !isAdmin()) return;
  const button = form.querySelector("[type='submit']");
  setButtonLoading(button, true);
  try {
    const result = await api("/api/admin/config", { method: "POST", body: backendConfigPayload(form) });
    state.config = result.config || null;
    await loadBackend();
    showToast("Backend configuration saved");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function deleteSelectedEmployee() {
  const employee = selectedEmployee();
  if (!employee || !isAdmin()) return;
  const confirmed = window.confirm(`Delete ${employee.name}? This removes the user record from Gatewatch.`);
  if (!confirmed) return;
  setButtonLoading(ui.deleteUserButton, true);
  try {
    await api(`/api/employees/${employee.id}`, { method: "DELETE" });
    clearUserForm();
    await loadAll();
    showToast("User deleted");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(ui.deleteUserButton, false);
  }
}

async function loadBackend({ announce = false } = {}) {
  if (!isAdmin()) return;
  state.backendLoading = true;
  renderBackend();
  setButtonLoading(ui.refreshBackendButton, true);
  try {
    const [config, diagnostics] = await Promise.all([api("/api/admin/config"), api("/api/admin/diagnostics")]);
    state.config = config.config || null;
    state.diagnostics = diagnostics.diagnostics || null;
    if (announce) showToast("Backend logs refreshed");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.backendLoading = false;
    setButtonLoading(ui.refreshBackendButton, false);
    renderBackend();
  }
}

async function syncDirectory() {
  if (!isAdmin()) return;
  setButtonLoading(ui.syncDirectoryButton, true);
  try {
    const result = await api("/api/entra/sync", { method: "POST" });
    await loadAll();
    await loadBackend();
    const sync = result.sync || {};
    showToast(`Directory synced: ${sync.created || 0} created, ${sync.updated || 0} updated`);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(ui.syncDirectoryButton, false);
  }
}

async function reviewChangeRequest(event) {
  const action = event.target.closest("[data-review-request]");
  if (!action || !isAdmin()) return;
  const requestId = Number(action.dataset.requestId);
  const decision = action.dataset.reviewRequest;
  if (!requestId || !["approve", "reject"].includes(decision)) return;
  setButtonLoading(action, true);
  try {
    await api(`/api/change-requests/${requestId}/${decision}`, { method: "POST", body: {} });
    await loadAll();
    await loadBackend();
    showToast(decision === "approve" ? "Change approved" : "Change rejected");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(action, false);
  }
}

async function saveAccessField(event) {
  event.preventDefault();
  if (!isAdmin()) return;
  const form = event.target.closest("#accessFieldForm");
  const button = form?.querySelector("[type='submit']");
  if (!form) return;
  const payload = accessFieldPayload(form);
  const editingId = state.editingAccessFieldId;
  const path = editingId ? `/api/access-fields/${editingId}` : "/api/access-fields";
  const method = editingId ? "PATCH" : "POST";
  setButtonLoading(button, true);
  try {
    await api(path, { method, body: payload });
    state.editingAccessFieldId = null;
    await loadAll();
    showToast(editingId ? "Custom field saved" : "Custom field added");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function handleAccessFieldAction(event) {
  const edit = event.target.closest("[data-edit-access-field]");
  if (edit) {
    state.editingAccessFieldId = Number(edit.dataset.editAccessField);
    renderBackend();
    document.querySelector("#accessFieldForm")?.scrollIntoView({ block: "nearest" });
    return;
  }
  const cancel = event.target.closest("[data-cancel-access-field]");
  if (cancel) {
    state.editingAccessFieldId = null;
    renderBackend();
    return;
  }
  const remove = event.target.closest("[data-delete-access-field]");
  if (!remove || !isAdmin()) return;
  const field = state.accessFields.find((item) => item.id === Number(remove.dataset.deleteAccessField));
  if (!field) return;
  const confirmed = window.confirm(`Remove custom field "${field.label}" from the active form?`);
  if (!confirmed) return;
  setButtonLoading(remove, true);
  try {
    await api(`/api/access-fields/${field.id}`, { method: "DELETE" });
    if (state.editingAccessFieldId === field.id) state.editingAccessFieldId = null;
    await loadAll();
    showToast("Custom field removed");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(remove, false);
  }
}

function formPayload() {
  const form = ui.userForm;
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
    request_received: form.elements.request_received.checked,
    manager_approved: form.elements.manager_approved.checked,
    it_provisioned: form.elements.it_provisioned.checked,
    employee_notified: form.elements.employee_notified.checked,
    access_profile: collectAccessProfile(),
    notes: form.elements.notes.value.trim(),
  };
}

function backendConfigPayload(form) {
  return {
    host: form.elements.host.value.trim(),
    port: form.elements.port.value.trim(),
    databasePath: form.elements.databasePath.value.trim(),
    tenantId: form.elements.tenantId.value.trim(),
    clientId: form.elements.clientId.value.trim(),
    redirectUri: form.elements.redirectUri.value.trim(),
    adminGroupCanonical: form.elements.adminGroupCanonical.value.trim(),
    sessionSecret: form.elements.sessionSecret.value.trim(),
    clientSecret: form.elements.clientSecret.value.trim(),
    allowInsecureNetwork: form.elements.allowInsecureNetwork.checked,
  };
}

function accessFieldPayload(form) {
  return {
    label: form.elements.label.value.trim(),
    section: form.elements.section.value.trim(),
    fieldType: form.elements.fieldType.value,
    options: form.elements.options.value
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean),
    required: form.elements.required.checked,
    sort_order: Number(form.elements.sort_order.value || 0),
  };
}

function collectAccessProfile() {
  const values = {};
  for (const field of activeAccessFields()) {
    const element = ui.userForm.elements[`access_profile.${field.key}`];
    if (!element) continue;
    values[field.key] = field.field_type === "checkbox" ? Boolean(element.checked) : String(element.value || "").trim();
  }
  return values;
}

function fillUserForm(employee) {
  const data = employee || EMPTY_EMPLOYEE;
  ui.userForm.elements.id.value = data.id || "";
  for (const key of ["employee_id", "name", "email", "department", "title", "location", "manager", "status", "request_source", "access_needed", "notes"]) {
    if (ui.userForm.elements[key]) ui.userForm.elements[key].value = data[key] || "";
  }
  for (const key of CHECKLIST_FIELDS) {
    ui.userForm.elements[key].checked = Boolean(data[key]);
  }
  renderCustomFields(data.access_profile || {});
  updateFormState();
}

function clearUserForm({ focus = false } = {}) {
  state.selectedId = null;
  state.expandedProfileId = null;
  ui.userForm.reset();
  fillUserForm(null);
  renderUsers();
  renderOverview();
  if (focus) ui.userForm.elements.employee_id.focus();
}

function selectEmployee(id, { openUsers = false, expand = false } = {}) {
  const employee = state.employees.find((item) => item.id === id);
  if (!employee) return;
  state.selectedId = id;
  state.selectedActivityKey = null;
  if (expand) state.expandedProfileId = id;
  fillUserForm(employee);
  if (openUsers) setActiveTab("users");
  renderOverview();
  renderUsers();
}

function selectEmployeeFromSearch(value) {
  const query = String(value || "").trim().toLowerCase();
  if (!query) return;
  const match = state.employees.find((employee) => [employee.name, employee.employee_id, employee.email].some((item) => String(item || "").toLowerCase() === query));
  if (match) selectEmployee(match.id, { expand: true });
}

function toggleProfileExpansion(id) {
  state.expandedProfileId = state.expandedProfileId === id ? null : id;
  if (state.expandedProfileId) selectEmployee(id);
  renderUsers();
}

function selectActivityFromEvent(event) {
  const row = event.target.closest("[data-activity-key]");
  if (!row) return;
  state.selectedActivityKey = row.dataset.activityKey;
  renderOverview();
  renderActivity();
}

function setActiveTab(tab, { replace = false } = {}) {
  state.activeTab = tabAllowed(tab) ? tab : "overview";
  const nextHash = state.activeTab === "overview" ? "" : `#${state.activeTab}`;
  if (replace) {
    history.replaceState(null, "", `${location.pathname}${location.search}${nextHash}`);
  } else if (location.hash !== nextHash) {
    history.pushState(null, "", `${location.pathname}${location.search}${nextHash}`);
  }
  renderTabs();
}

function tabAllowed(tab) {
  return TABS.includes(tab) && (!ADMIN_TABS.has(tab) || isAdmin());
}

function isAdmin() {
  return Boolean(state.auth?.permissions?.canModifyEmployees);
}

function selectedEmployee() {
  return state.employees.find((employee) => employee.id === state.selectedId) || null;
}

function visibleOverviewEmployees() {
  const query = state.overviewQuery.trim().toLowerCase();
  const validation = validateSearch(state.overviewQuery);
  return state.employees
    .filter((employee) => matchesFilter(employee, state.filter))
    .filter((employee) => matchesSearch(employee, query, validation))
    .sort(sortEmployees);
}

function visibleUserEmployees() {
  const query = state.userQuery.trim().toLowerCase();
  const validation = validateSearch(state.userQuery);
  return state.employees
    .filter((employee) => matchesSearch(employee, query, validation))
    .sort(sortEmployees);
}

function matchesSearch(employee, query, validation) {
  if (!query) return true;
  if (validation.state === "error") return false;
  return searchText(employee).includes(query);
}

function currentActorAudit() {
  const actor = state.auth?.permissions?.actor || "Local user";
  return state.audit.filter((entry) => String(entry.actor || "") === actor);
}

function sortEmployees(left, right) {
  return String(left.name || "").localeCompare(String(right.name || "")) || Number(left.id || 0) - Number(right.id || 0);
}

function filterCounts() {
  const counts = { active: 0, inProgress: 0, disabled: 0, terminated: 0 };
  for (const employee of state.employees) {
    if (employee.status === "active") counts.active += 1;
    if (employee.status === "disabled") counts.disabled += 1;
    if (employee.status === "terminated") counts.terminated += 1;
    if ((employee.access_needed || employee.request_received) && !employee.employee_notified) counts.inProgress += 1;
  }
  return counts;
}

function matchesFilter(employee, filter) {
  if (filter === "all") return true;
  if (filter === "active") return employee.status === "active";
  if (filter === "disabled") return employee.status === "disabled";
  if (filter === "terminated") return employee.status === "terminated";
  if (filter === "inProgress") return Boolean((employee.access_needed || employee.request_received) && !employee.employee_notified);
  return true;
}

function filterKey(employee) {
  if (employee.status === "terminated") return "terminated";
  if (employee.status === "disabled") return "disabled";
  if (completedStepCount(employee) > 0 && completedStepCount(employee) < 4) return "inProgress";
  return "active";
}

function signalStatus(employee) {
  const key = filterKey(employee);
  if (key === "terminated") return { key: "critical", label: "Terminated" };
  if (key === "disabled") return { key: "offline", label: "Disabled" };
  if (key === "inProgress") return { key: "warning", label: "In Progress" };
  return { key: "online", label: "Active" };
}

function overallStatus() {
  if (state.loading) return { key: "loading", label: "Syncing", pulse: true };
  if (state.loadError) return { key: "critical", label: "Critical", pulse: true };
  const counts = filterCounts();
  if (counts.terminated) return { key: "critical", label: "Critical", pulse: true };
  if (counts.inProgress || counts.disabled) return { key: "warning", label: "Review", pulse: true };
  if (state.employees.length) return { key: "online", label: "Online", pulse: true };
  return { key: "idle", label: "Idle", pulse: false };
}

function shouldPulse(statusKey) {
  return ["online", "warning", "critical", "loading"].includes(statusKey);
}

function isRecentlyUpdated() {
  return Date.now() < state.recentUntil;
}

function healthValue(employee) {
  const key = filterKey(employee);
  if (key === "terminated") return 18;
  if (key === "disabled") return 34;
  if (key === "inProgress") return Math.max(45, completedStepCount(employee) * 22);
  return 96;
}

function completedStepCount(employee) {
  return CHECKLIST_FIELDS.filter((key) => Boolean(employee?.[key])).length;
}

function activeAccessFields() {
  return state.accessFields
    .filter((field) => field.active)
    .sort((left, right) => (left.sort_order ?? 0) - (right.sort_order ?? 0) || String(left.label).localeCompare(String(right.label)));
}

function renderSearchState(field, input, help, idleMessage) {
  const result = validateSearch(input.value);
  const visual = document.activeElement === input && result.state === "idle" ? "focus" : result.state;
  field.dataset.state = visual;
  input.setAttribute("aria-invalid", result.state === "error" ? "true" : "false");
  help.textContent = result.state === "idle" ? idleMessage : result.message;
}

function validateSearch(value) {
  const text = String(value || "");
  const trimmed = text.trim();
  if (!trimmed) return { state: "idle", message: "" };
  if (/[<>{}[\]\\]/.test(trimmed)) return { state: "error", message: "Remove unsupported characters." };
  if (trimmed.length > 60) return { state: "error", message: "Query is too long." };
  if (trimmed.length === 1) return { state: "warning", message: "Keep typing to narrow results." };
  return { state: "valid", message: "Search active." };
}

function searchText(employee) {
  return [
    employee.employee_id,
    employee.name,
    employee.email,
    employee.department,
    employee.title,
    employee.location,
    employee.manager,
    employee.request_source,
    employee.access_needed,
    employee.status,
    JSON.stringify(employee.access_profile || {}),
  ].filter(Boolean).join(" ").toLowerCase();
}

function activitySeverity(entry) {
  const normalized = String(entry?.action || "").toLowerCase();
  const payload = `${entry?.summary || ""} ${entry?.after_json || ""}`.toLowerCase();
  if (payload.includes('"status": "terminated"') || payload.includes("terminated") || normalized.includes("delete") || normalized.includes("reject")) return { key: "critical", label: "Critical" };
  if (payload.includes('"status": "disabled"') || payload.includes("disabled") || normalized.includes("request") || normalized.includes("update")) return { key: "warning", label: "Warning" };
  return { key: "online", label: "Recorded" };
}

function renderConfigChecks(checks) {
  if (!checks.length) return emptyState("No checks", "Configuration checks are not available.");
  return checks.map((check) => `<article class="check-card is-${escapeHtml(check.status)}"><strong>${escapeHtml(check.label)}</strong><span>${escapeHtml(check.status)}</span><p>${escapeHtml(check.message)}</p></article>`).join("");
}

function renderBackendConfigForm(config) {
  const runtime = config.runtime || {};
  const secrets = config.secrets || {};
  return `
    <form id="backendConfigForm" class="backend-config-form">
      <div class="field-grid">
        ${configInput("Host", "host", runtime.host || "127.0.0.1", "127.0.0.1")}
        ${configInput("Port", "port", runtime.port || "8087", "8087")}
        ${configInput("Database path", "databasePath", runtime.databasePath || "", "/var/lib/gatewatch/gatewatch.db", "span-2")}
        ${configInput("Admin group", "adminGroupCanonical", runtime.adminGroupCanonical || "", "gcefcu.org/Users/Domain Admins", "span-2")}
        ${configInput("Tenant ID", "tenantId", runtime.tenantId || "", "Microsoft tenant ID")}
        ${configInput("Client ID", "clientId", runtime.clientId || "", "Microsoft app client ID")}
        ${configInput("Redirect URI", "redirectUri", runtime.redirectUri || "", "http://127.0.0.1:8087/auth/entra/callback", "span-2")}
        ${configInput("Session secret", "sessionSecret", "", secrets.sessionSecret?.configured ? "Already configured" : "Paste server secret", "", "password")}
        ${configInput("Entra client secret", "clientSecret", "", secrets.entraClientSecret?.configured ? "Already configured" : "Paste client secret", "", "password")}
      </div>
      <label class="toggle-field config-toggle">
        <input name="allowInsecureNetwork" type="checkbox" ${runtime.allowInsecureNetwork ? "checked" : ""} />
        Allow non-loopback bind
      </label>
      <div class="config-status">
        ${metadataList([
          ["Config file", config.configFile?.path],
          ["Auth mode", runtime.authMode],
          ["Session secret", secrets.sessionSecret?.configured ? "configured" : "missing"],
          ["Client secret", secrets.entraClientSecret?.configured ? "configured" : "missing"],
          ["Proxy secret", secrets.proxySecret?.configured ? "configured" : "missing"],
        ])}
      </div>
      <div id="configPreview" class="config-preview" aria-live="polite"></div>
      <div class="form-actions">
        <button class="button button--secondary" type="button" data-validate-config>Validate</button>
        <button class="button button--primary" type="submit">Save Config</button>
      </div>
    </form>
  `;
}

function renderAccessFieldManager() {
  const editing = state.accessFields.find((field) => field.id === state.editingAccessFieldId) || null;
  return `
    <section class="log-card access-field-manager" aria-labelledby="customFormAdminTitle">
      <div class="section-row">
        <h3 id="customFormAdminTitle">Custom Form Fields</h3>
        <span class="muted-label">${activeAccessFields().length} active</span>
      </div>
      <form id="accessFieldForm" class="access-field-form">
        <input name="id" type="hidden" value="${escapeHtml(editing?.id || "")}" />
        <div class="field-grid">
          ${configInput("Label", "label", editing?.label || "", "Core banking role")}
          ${configInput("Section", "section", editing?.section || "Systems Access", "Systems Access")}
          <label>
            <span>Type</span>
            <select name="fieldType">
              ${["text", "textarea", "select", "checkbox", "date"].map((type) => `<option value="${type}" ${editing?.field_type === type ? "selected" : ""}>${labelize(type)}</option>`).join("")}
            </select>
          </label>
          ${configInput("Sort", "sort_order", editing?.sort_order ?? "", "200", "", "number")}
          <label class="span-2">
            <span>Options</span>
            <textarea name="options" placeholder="One option per line">${escapeHtml((editing?.options || []).join("\n"))}</textarea>
          </label>
        </div>
        <label class="toggle-field config-toggle">
          <input name="required" type="checkbox" ${editing?.required ? "checked" : ""} />
          Required field
        </label>
        <div class="form-actions">
          <button class="button button--ghost" type="button" data-cancel-access-field ${editing ? "" : "disabled"}>Cancel</button>
          <button class="button button--primary" type="submit">${editing ? "Save Field" : "Add Field"}</button>
        </div>
      </form>
      <div id="accessProfileFields" class="access-field-list">
        ${activeAccessFields().length ? activeAccessFields().map(renderAccessFieldRow).join("") : emptyState("No custom fields", "Add fields to shape the user form.")}
      </div>
    </section>
  `;
}

function renderAccessFieldRow(field) {
  return `
    <article class="access-field-row">
      <div>
        <strong>${escapeHtml(field.label)}</strong>
        <span>${escapeHtml(field.section)} / ${escapeHtml(labelize(field.field_type))}${field.required ? " / Required" : ""}</span>
      </div>
      <div class="row-actions">
        <button class="button button--ghost" type="button" data-edit-access-field="${field.id}">Edit</button>
        <button class="button button--danger" type="button" data-delete-access-field="${field.id}">Remove</button>
      </div>
    </article>
  `;
}

function configInput(label, name, value, placeholder, className = "", type = "text") {
  return `
    <label class="${escapeHtml(className)}">
      <span>${escapeHtml(label)}</span>
      <input name="${escapeHtml(name)}" type="${escapeHtml(type)}" value="${escapeHtml(value || "")}" placeholder="${escapeHtml(placeholder || "")}" autocomplete="off" />
    </label>
  `;
}

function renderConfigPreview(preview = {}) {
  return `
    <section class="log-card config-preview-card">
      <h3>Validation</h3>
      <div class="diagnostic-grid">${renderConfigChecks(preview.checks || [])}</div>
      <pre>${escapeHtml(preview.envTemplate || "")}</pre>
    </section>
  `;
}

function renderAdminEvents(events) {
  if (!events.length) return emptyState("No audit events", "Recent audit records will appear here.");
  return `<div class="compact-log">${events.slice(0, 12).map((event) => `<div><strong>${escapeHtml(event.summary)}</strong><span>${escapeHtml(formatDateTime(event.created_at))} / ${escapeHtml(event.actor || "")}</span></div>`).join("")}</div>`;
}

function renderAdminRequests(requests) {
  if (!requests.length) return emptyState("No change requests", "Pending and reviewed requests appear here.");
  return `<div class="compact-log request-log">${requests.slice(0, 12).map(renderAdminRequest).join("")}</div>`;
}

function renderAdminRequest(request) {
  const pending = request.status === "pending";
  return `
    <div class="request-row">
      <div>
        <strong>${escapeHtml(request.status)} / ${escapeHtml(request.employee_name || `Employee ${request.employee_id}`)}</strong>
        <span>${escapeHtml(formatDateTime(request.requested_at))} / ${escapeHtml(request.requested_by || "")}</span>
      </div>
      ${pending ? `<div class="row-actions">
        <button class="button button--ghost" type="button" data-review-request="reject" data-request-id="${request.id}">Reject</button>
        <button class="button button--secondary" type="button" data-review-request="approve" data-request-id="${request.id}">Approve</button>
      </div>` : ""}
    </div>
  `;
}

function metadataList(rows) {
  if (!rows.length) return "";
  return `<dl class="metadata">${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value ?? "--")}</dd></div>`).join("")}</dl>`;
}

function renderBusyState() {
  ui.metrics.setAttribute("aria-busy", state.loading ? "true" : "false");
  ui.monitoringList.setAttribute("aria-busy", state.loading ? "true" : "false");
  ui.activityFeed.setAttribute("aria-busy", state.loading ? "true" : "false");
}

function clearInvalidSelection() {
  if (state.selectedId && !state.employees.some((employee) => employee.id === state.selectedId)) {
    state.selectedId = null;
  }
}

function setButtonLoading(button, loading) {
  button.classList.toggle("is-loading", Boolean(loading));
  button.disabled = Boolean(loading);
  button.setAttribute("aria-busy", loading ? "true" : "false");
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  const request = { method: options.method || "GET", headers };
  if (options.body) {
    headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, request);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Request failed with HTTP ${response.status}`);
  return body;
}

function tabFromHash() {
  const tab = location.hash.replace("#", "");
  return TABS.includes(tab) ? tab : "overview";
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setDatalistOptions(list, values) {
  list.innerHTML = [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b))
    .slice(0, 200)
    .map((value) => `<option value="${escapeHtml(value)}"></option>`)
    .join("");
}

function emptyState(title, detail) {
  return `<div class="empty-state"><div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span></div></div>`;
}

function showToast(message, isError = false) {
  ui.toast.textContent = message;
  ui.toast.classList.toggle("error", isError);
  ui.toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => ui.toast.classList.remove("show"), 2600);
}

function activityKey(entry) {
  return String(entry.id || `${entry.created_at || "event"}-${entry.action || "change"}-${entry.entity_id || ""}`);
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

function formatCompactTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatCompactDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "--";
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function yesNo(value) {
  if (value === undefined || value === null) return "--";
  return value ? "yes" : "no";
}

function labelize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
