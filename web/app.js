const TABS = ["overview", "users", "templates", "activity", "backend"];
const ADMIN_TABS = new Set(["backend"]);
const THEME_STORAGE_KEY = "gatewatch-theme";
const FILTER_STORAGE_KEY = "gatewatch-status-filter";
const CSRF_HEADER = "X-Gatewatch-CSRF";
const THEMES = new Set(["light", "dark"]);
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
  phone: "",
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
  deletedEmployees: [],
  accessFields: [],
  accessTemplates: [],
  changeRequests: [],
  audit: [],
  auth: null,
  config: null,
  diagnostics: null,
  update: null,
  summary: {},
  activeTab: tabFromHash(),
  theme: initialTheme(),
  filter: initialFilter(),
  overviewQuery: "",
  userQuery: "",
  loading: true,
  backendLoading: false,
  loadError: "",
  selectedId: null,
  expandedProfileId: null,
  selectedActivityKey: null,
  activityScope: "all",
  editingAccessFieldId: null,
  editingTemplateId: null,
  formAccessProfileOverride: null,
  lastFetchedAt: "",
  loadedOnce: false,
  recentUntil: 0,
  metricSnapshot: {},
};

const ui = {
  primaryAction: document.querySelector("#primaryAction"),
  themeToggle: document.querySelector("#themeToggle"),
  themeLightButton: document.querySelector("#themeLightButton"),
  themeDarkButton: document.querySelector("#themeDarkButton"),
  tabs: document.querySelector(".tabs"),
  backendTab: document.querySelector("#backendTab"),
  templatesTab: document.querySelector("#templatesTab"),
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
  signedInIdentity: document.querySelector("#signedInIdentity"),
  userSearchField: document.querySelector("#userSearchField"),
  userSearchInput: document.querySelector("#userSearchInput"),
  userSearchHelp: document.querySelector("#userSearchHelp"),
  userSearchOptions: document.querySelector("#userSearchOptions"),
  userListCount: document.querySelector("#userListCount"),
  userProfileList: document.querySelector("#userProfileList"),
  deletedUserBox: document.querySelector("#deletedUserBox"),
  deletedUserCount: document.querySelector("#deletedUserCount"),
  deletedUserList: document.querySelector("#deletedUserList"),
  newUserButton: document.querySelector("#newUserButton"),
  userForm: document.querySelector("#userForm"),
  userFormTitle: document.querySelector("#userFormTitle"),
  userFormSubtitle: document.querySelector("#userFormSubtitle"),
  formModeBadge: document.querySelector("#formModeBadge"),
  customAccessFields: document.querySelector("#customAccessFields"),
  customFieldCount: document.querySelector("#customFieldCount"),
  viewUserActivityButton: document.querySelector("#viewUserActivityButton"),
  copyUserButton: document.querySelector("#copyUserButton"),
  userToTemplateButton: document.querySelector("#userToTemplateButton"),
  deleteUserButton: document.querySelector("#deleteUserButton"),
  clearUserButton: document.querySelector("#clearUserButton"),
  saveUserButton: document.querySelector("#saveUserButton"),
  userTemplateSelect: document.querySelector("#userTemplateSelect"),
  applyTemplateButton: document.querySelector("#applyTemplateButton"),
  newTemplateButton: document.querySelector("#newTemplateButton"),
  templateCount: document.querySelector("#templateCount"),
  templateList: document.querySelector("#templateList"),
  templateForm: document.querySelector("#templateForm"),
  templateFormTitle: document.querySelector("#templateFormTitle"),
  templateFormSubtitle: document.querySelector("#templateFormSubtitle"),
  templateModeBadge: document.querySelector("#templateModeBadge"),
  templateAccessFields: document.querySelector("#templateAccessFields"),
  templateAccessFieldCount: document.querySelector("#templateAccessFieldCount"),
  deleteTemplateButton: document.querySelector("#deleteTemplateButton"),
  clearTemplateButton: document.querySelector("#clearTemplateButton"),
  saveTemplateButton: document.querySelector("#saveTemplateButton"),
  configuredFieldsDisclosure: document.querySelector("#configuredFieldsDisclosure"),
  activityActor: document.querySelector("#activityActor"),
  activityExportLink: document.querySelector("#activityExportLink"),
  activityLogList: document.querySelector("#activityLogList"),
  refreshBackendButton: document.querySelector("#refreshBackendButton"),
  syncDirectoryButton: document.querySelector("#syncDirectoryButton"),
  backendConfigSummary: document.querySelector("#backendConfigSummary"),
  backendConfigBody: document.querySelector("#backendConfigBody"),
  adminLogBody: document.querySelector("#adminLogBody"),
  adminLogsSummary: document.querySelector("#adminLogsSummary"),
  toast: document.querySelector("#toast"),
};

applyTheme(state.theme);

ui.primaryAction.addEventListener("click", () => loadAll({ announce: true, delay: 360 }));
ui.themeToggle.addEventListener("click", (event) => {
  const option = event.target.closest("[data-theme-choice]");
  if (!option || option.disabled) return;
  setTheme(option.dataset.themeChoice);
});
ui.tabs.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  if (!tab || tab.disabled) return;
  setActiveTab(tab.dataset.tab);
});
ui.tabs.addEventListener("keydown", handleTabKeydown);
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
  setFilter(chip.dataset.filter);
  renderOverview();
});
ui.metrics.addEventListener("click", (event) => {
  const card = event.target.closest("[data-metric-filter]");
  if (!card || card.disabled) return;
  setFilter(card.dataset.metricFilter);
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
ui.activityFeed.addEventListener("click", (event) => {
  if (event.target.closest("[data-review-request]")) {
    reviewChangeRequest(event);
    return;
  }
  selectActivityFromEvent(event);
});
ui.activityFeed.addEventListener("keydown", (event) => {
  if (event.target.closest("[data-review-request]")) return;
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  selectActivityFromEvent(event);
});
ui.activityLogList.addEventListener("click", (event) => {
  const scope = event.target.closest("[data-activity-scope]");
  if (scope) {
    state.activityScope = scope.dataset.activityScope;
    state.selectedActivityKey = null;
    renderActivity();
    return;
  }
  selectActivityFromEvent(event);
});
ui.activityLogList.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  selectActivityFromEvent(event);
});
ui.userProfileList.addEventListener("click", (event) => {
  const activity = event.target.closest("[data-profile-activity]");
  if (activity) {
    event.stopPropagation();
    selectEmployee(Number(activity.dataset.profileActivity), { openUsers: false, expand: true });
    state.activityScope = "selected";
    state.selectedActivityKey = null;
    setActiveTab("activity");
    renderActivity();
    return;
  }
  const template = event.target.closest("[data-profile-template]");
  if (template) {
    event.stopPropagation();
    focusUserTemplatePicker(Number(template.dataset.profileTemplate));
    return;
  }
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
ui.deletedUserList.addEventListener("click", restoreDeletedEmployee);
ui.newUserButton.addEventListener("click", () => clearUserForm({ focus: true }));
ui.clearUserButton.addEventListener("click", () => clearUserForm({ focus: true }));
ui.viewUserActivityButton.addEventListener("click", () => {
  const employee = selectedEmployee();
  if (!employee) return;
  state.activityScope = "selected";
  state.selectedActivityKey = null;
  setActiveTab("activity");
  renderActivity();
});
ui.copyUserButton.addEventListener("click", copySelectedEmployee);
ui.userToTemplateButton.addEventListener("click", createTemplateDraftFromSelectedUser);
ui.applyTemplateButton.addEventListener("click", applySelectedTemplateToUserForm);
ui.deleteUserButton.addEventListener("click", deleteSelectedEmployee);
ui.userForm.addEventListener("submit", saveUser);
ui.newTemplateButton.addEventListener("click", () => clearTemplateForm({ focus: true }));
ui.clearTemplateButton.addEventListener("click", () => clearTemplateForm({ focus: true }));
ui.deleteTemplateButton.addEventListener("click", deleteSelectedTemplate);
ui.templateForm.addEventListener("submit", saveTemplate);
ui.templateList.addEventListener("click", (event) => {
  const template = event.target.closest("[data-template-id]");
  if (!template) return;
  selectTemplate(Number(template.dataset.templateId));
});
ui.templateList.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const template = event.target.closest("[data-template-id]");
  if (!template) return;
  event.preventDefault();
  selectTemplate(Number(template.dataset.templateId));
});
ui.refreshBackendButton.addEventListener("click", () => loadBackend({ announce: true }));
ui.syncDirectoryButton.addEventListener("click", syncDirectory);
ui.backendConfigBody.addEventListener("submit", (event) => {
  if (event.target.closest("#backendConfigForm")) saveBackendConfig(event);
  if (event.target.closest("#accessFieldForm")) saveAccessField(event);
});
ui.backendConfigBody.addEventListener("click", (event) => {
  const validateConfigButton = event.target.closest("[data-validate-config]");
  if (validateConfigButton) {
    validateBackendConfig(validateConfigButton);
    return;
  }
  const validateUpdateButton = event.target.closest("[data-validate-update]");
  if (validateUpdateButton) {
    validateUpdateConfig(validateUpdateButton);
    return;
  }
  const applyUpdateButton = event.target.closest("[data-apply-update]");
  if (applyUpdateButton) {
    applyUpdate(applyUpdateButton);
    return;
  }
  handleAccessFieldAction(event);
});
ui.adminLogBody.addEventListener("click", reviewChangeRequest);
window.addEventListener("hashchange", () => setActiveTab(tabFromHash(), { replace: true }));
window.addEventListener("popstate", () => setActiveTab(tabFromHash(), { replace: true }));

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
    state.deletedEmployees = data.deletedEmployees || [];
    state.accessFields = data.accessFields || [];
    state.accessTemplates = data.accessTemplates || [];
    state.changeRequests = data.changeRequests || [];
    state.audit = data.audit || [];
    state.auth = data.auth || null;
    state.lastFetchedAt = new Date().toISOString();
    state.recentUntil = Date.now() + 4200;
    clearInvalidSelection();
    clearInvalidTemplateSelection();
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
  renderTemplates();
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
  if (ui.signedInIdentity) {
    const actor = currentActor();
    const identityLabel = ui.signedInIdentity.querySelector("span");
    if (identityLabel) identityLabel.textContent = actor;
    else ui.signedInIdentity.textContent = actor;
    ui.signedInIdentity.title = `Signed in as ${actor}`;
  }
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
  renderDeletedUsers();
  renderTemplatePicker();
  renderCustomFields(state.formAccessProfileOverride || selectedEmployee()?.access_profile || {});
  updateFormState();
}

function renderTemplates() {
  renderTemplateList();
  renderTemplateFields(selectedTemplate()?.access_profile || {});
  updateTemplateFormState();
}

function renderActivity() {
  const actor = state.auth?.permissions?.actor || "Local user";
  const employee = selectedEmployee();
  const entries = activityEntries();
  const selectedLabel = employee ? employee.name || employee.email || `User ${employee.id}` : "No user selected";
  ui.activityActor.textContent = state.activityScope === "selected" && employee
    ? `Showing activity for ${selectedLabel}.`
    : `Current actor: ${actor}.`;
  ui.activityExportLink.hidden = !isAdmin();
  const scopeControl = renderActivityScopeControl(employee);
  if (!entries.length) {
    ui.activityLogList.innerHTML = `${scopeControl}${emptyState("No activity", state.activityScope === "selected" && employee ? "No changes recorded for this user." : "No changes recorded for this actor.")}`;
    return;
  }
  ui.activityLogList.innerHTML = `${scopeControl}${entries.slice(0, 100).map((entry) => renderActivityRow(entry, { long: true })).join("")}`;
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
    ${renderUpdatePanel(state.update)}
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
        ["Supervisor group", diagnostics.auth?.supervisorGroup],
        ["Permission", diagnostics.auth?.permissions?.role || (diagnostics.auth?.permissions?.canModifyEmployees ? "admin" : "user")],
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
    ["Total Users", state.summary.total ?? state.employees.length, state.employees.length ? "SQLite" : "Idle", "default", "all", "users"],
    ["Active", state.summary.active ?? counts.active, "Enabled profiles", "online", "active", "check"],
    ["In Progress", state.summary.inProgress ?? counts.inProgress, counts.inProgress ? "Handoff pending" : "Clear", counts.inProgress ? "warning" : "default", "inProgress", "clock"],
    ["Terminated", state.summary.terminated ?? counts.terminated, counts.terminated ? "Review access" : "Clear", counts.terminated ? "critical" : "default", "terminated", "alert"],
  ];
  ui.metrics.innerHTML = metrics
    .map(([label, value, detail, tone, filter, icon]) => {
      const classes = [
        "metric-card",
        state.loading ? "is-loading" : "",
        tone === "warning" ? "is-warning" : "",
        tone === "critical" ? "is-critical" : "",
      ].filter(Boolean).join(" ");
      return `<button class="${classes}" type="button" data-metric-filter="${escapeHtml(filter)}" aria-label="Show ${escapeHtml(label)} users">${iconSvg(icon, "metric-icon")}<span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></button>`;
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
        <span>${escapeHtml(employee.email || "No email")} / ${escapeHtml(employee.phone || "No phone")}</span>
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
  const workQueue = renderWorkQueue();
  const activity = entries.length
    ? `<div class="feed-section"><h3>Recent Changes</h3>${entries.map((entry) => renderActivityRow(entry)).join("")}</div>`
    : `<div class="feed-section"><h3>Recent Changes</h3>${emptyState("No activity", "Changes appear here after the first save.")}</div>`;
  ui.activityFeed.innerHTML = `${workQueue}${activity}`;
}

function renderWorkQueue() {
  const requests = state.changeRequests.filter((request) => request.status === "pending");
  if (isAdmin()) {
    if (!requests.length) {
      return `<div class="feed-section">${emptyState("Admin todo clear", "No pending user changes need review.")}</div>`;
    }
    return `<div class="feed-section"><h3>Admin Todo</h3>${requests.slice(0, 6).map(renderQueueRequest).join("")}</div>`;
  }
  if (!requests.length) {
    return `<div class="feed-section">${emptyState("No waiting changes", "Your submitted edits will appear here until an admin reviews them.")}</div>`;
  }
  return `<div class="feed-section"><h3>Waiting Flow</h3>${requests.slice(0, 6).map(renderWaitingRequest).join("")}</div>`;
}

function renderQueueRequest(request) {
  return `
    <article class="request-row request-row--queue">
      <div>
        <strong>${escapeHtml(request.employee_name || `Employee ${request.employee_id}`)}</strong>
        <span>${escapeHtml(request.requested_by || "Requester")} / ${escapeHtml(formatDateTime(request.requested_at))}</span>
      </div>
      <div class="row-actions">
        <button class="button button--ghost" type="button" data-review-request="reject" data-request-id="${request.id}">Reject</button>
        <button class="button button--secondary" type="button" data-review-request="approve" data-request-id="${request.id}">Approve</button>
      </div>
    </article>
  `;
}

function renderWaitingRequest(request) {
  return `
    <article class="request-row request-row--waiting">
      <div>
        <strong>${escapeHtml(request.employee_name || `Employee ${request.employee_id}`)}</strong>
        <span>Waiting for admin review / ${escapeHtml(formatDateTime(request.requested_at))}</span>
      </div>
      <span class="severity severity--warning">Pending</span>
    </article>
  `;
}

function renderActivityRow(entry, { long = false } = {}) {
  const key = activityKey(entry);
  const selected = state.selectedActivityKey === key;
  const severity = activitySeverity(entry);
  const title = entry.summary || labelize(entry.action || "event");
  const details = selected ? renderActivityDetails(entry) : "";
  return `
    <article class="activity-row is-${escapeHtml(severity.key)} ${selected ? "is-selected" : ""}" role="listitem" tabindex="0" data-activity-key="${escapeHtml(key)}" aria-selected="${selected ? "true" : "false"}" aria-expanded="${selected ? "true" : "false"}">
      <span class="activity-time">${escapeHtml(long ? formatDateTime(entry.created_at) : formatCompactTime(entry.created_at))}</span>
      <span class="activity-copy">
        <strong><span class="severity severity--${escapeHtml(severity.key)}">${escapeHtml(severity.label)}</span> / ${escapeHtml(title)}</strong>
        <span>${escapeHtml(entry.actor || "Local user")}${long ? ` / ${escapeHtml(labelize(entry.entity_type || "record"))} ${escapeHtml(entry.entity_id || "")}` : ""}</span>
      </span>
      ${details}
    </article>
  `;
}

function renderActivityScopeControl(employee) {
  const selectedDisabled = !employee;
  const selectedActive = state.activityScope === "selected" && employee;
  return `
    <div class="activity-scope" role="toolbar" aria-label="Activity scope">
      <button class="chip" type="button" data-activity-scope="all" aria-pressed="${selectedActive ? "false" : "true"}">
        All visible
      </button>
      <button class="chip" type="button" data-activity-scope="selected" aria-pressed="${selectedActive ? "true" : "false"}" ${selectedDisabled ? "disabled" : ""}>
        ${employee ? `Selected: ${escapeHtml(employee.name || employee.email || `User ${employee.id}`)}` : "Select a user"}
      </button>
    </div>
  `;
}

function renderActivityDetails(entry) {
  const before = parseAuditJson(entry.before_json);
  const after = parseAuditJson(entry.after_json);
  const changes = auditChanges(before, after);
  const rows = [
    ["Action", labelize(entry.action)],
    ["Actor", entry.actor || "Local user"],
    ["Recorded", formatDateTime(entry.created_at)],
    ["Object", `${labelize(entry.entity_type || "record")} ${entry.entity_id || ""}`.trim()],
  ];
  const changeMarkup = changes.length
    ? `<div class="activity-change-grid">${changes.slice(0, 12).map(renderActivityChange).join("")}</div>`
    : `<p class="activity-detail-note">No field-level difference was recorded for this event.</p>`;
  return `
    <div class="activity-details">
      ${metadataList(rows)}
      ${changeMarkup}
    </div>
  `;
}

function renderActivityChange(change) {
  return `
    <div>
      <strong>${escapeHtml(change.label)}</strong>
      <span><em>Before</em>${escapeHtml(change.before)}</span>
      <span><em>After</em>${escapeHtml(change.after)}</span>
    </div>
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
      ["Created By", employee.created_by || "Unknown"],
      ["Email", employee.email],
      ["Phone", employee.phone || "--"],
      ["Notes", employee.notes || "--"],
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

function renderDeletedUsers() {
  const deleted = state.deletedEmployees || [];
  ui.deletedUserBox.hidden = !isAdmin();
  if (!isAdmin()) {
    ui.deletedUserList.innerHTML = "";
    return;
  }
  ui.deletedUserCount.textContent = `${deleted.length} stored`;
  if (!deleted.length) {
    ui.deletedUserList.innerHTML = emptyState("Deleted box empty", "Deleted users will be available here for restore.");
    return;
  }
  ui.deletedUserList.innerHTML = deleted.slice(0, 20).map(renderDeletedUser).join("");
}

function renderDeletedUser(employee) {
  return `
    <article class="deleted-user-row" role="listitem">
      <div>
        <strong>${escapeHtml(employee.name || "Unnamed user")}</strong>
        <span>${escapeHtml(employee.email || "No email")} / Deleted ${escapeHtml(formatCompactDate(employee.deleted_at))}</span>
      </div>
      <button class="button button--secondary" type="button" data-restore-employee="${employee.id}">Restore</button>
    </article>
  `;
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
        <span>${escapeHtml(employee.email || "No email")} / ${escapeHtml(employee.phone || "No phone")}</span>
        <small>${escapeHtml(employee.employee_id || "Auto ID")} / ${escapeHtml(status.label)} / ${completedStepCount(employee)}/4</small>
        <small>Created by ${escapeHtml(employee.created_by || "Unknown")}</small>
      </div>
      <button class="signal-expand" type="button" data-expand-profile="${employee.id}" aria-expanded="${expanded ? "true" : "false"}" aria-label="${expanded ? "Collapse" : "Expand"} ${escapeHtml(employee.name || "user")}">${expanded ? "-" : "+"}</button>
      <div class="profile-details">
        ${metadataList([
          ["Phone", employee.phone || "--"],
          ["Email", employee.email || "--"],
          ["Access Notes", employee.notes || employee.access_needed || "--"],
          ["Updated", formatDateTime(employee.updated_at)],
        ])}
        <div class="row-actions profile-actions">
          <button class="button button--ghost" type="button" data-profile-template="${employee.id}" title="Open this user's access template picker">Apply Template</button>
          <button class="button button--ghost" type="button" data-profile-activity="${employee.id}" title="Show changes recorded for this user">View Activity</button>
        </div>
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

function renderTemplatePicker() {
  const options = state.accessTemplates.filter((template) => template.active);
  const current = ui.userTemplateSelect.value;
  ui.userTemplateSelect.innerHTML = [
    `<option value="">No template</option>`,
    ...options.map((template) => `<option value="${template.id}" ${String(template.id) === current ? "selected" : ""}>${escapeHtml(template.name)}</option>`),
  ].join("");
  ui.applyTemplateButton.disabled = !options.length;
}

function renderTemplateList() {
  const templates = state.accessTemplates.filter((template) => template.active);
  ui.templateCount.textContent = `${templates.length} ${templates.length === 1 ? "template" : "templates"}`;
  if (!templates.length) {
    ui.templateList.innerHTML = emptyState("No templates", canManageTemplates() ? "Create the first reusable access profile." : "A supervisor or admin can create access profiles.");
    return;
  }
  ui.templateList.innerHTML = templates.map(renderTemplateCard).join("");
}

function renderTemplateCard(template) {
  const selected = state.editingTemplateId === template.id;
  const filled = Object.values(template.access_profile || {}).filter(Boolean).length;
  return `
    <article class="template-card ${selected ? "is-selected" : ""}" role="option" tabindex="0" aria-selected="${selected ? "true" : "false"}" data-template-id="${template.id}">
      <div class="template-copy">
        <strong>${escapeHtml(template.name)}</strong>
        <span>${escapeHtml(template.description || "Reusable access profile")}</span>
        <small>${filled} ${filled === 1 ? "access value" : "access values"} / Updated ${escapeHtml(formatCompactDate(template.updated_at))}</small>
      </div>
    </article>
  `;
}

function renderTemplateFields(values = {}) {
  const fields = activeAccessFields();
  ui.templateAccessFieldCount.textContent = `${fields.length} ${fields.length === 1 ? "field" : "fields"}`;
  if (!fields.length) {
    ui.templateAccessFields.innerHTML = `<div class="empty-state"><div><strong>No custom fields</strong><span>Admins can configure form fields from the backend.</span></div></div>`;
    return;
  }
  ui.templateAccessFields.innerHTML = fields.map((field) => renderCustomField(field, values[field.key])).join("");
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
    ? canModifyEmployees()
      ? `Created by ${employee.created_by || "Unknown"} / Last saved ${formatDateTime(employee.updated_at)}.`
      : "Edits submit as a change request until an admin approves them."
    : "Create a user in SQLite.";
  ui.formModeBadge.textContent = employee ? "Selected" : "New";
  ui.viewUserActivityButton.disabled = !employee;
  ui.copyUserButton.disabled = !employee;
  ui.userToTemplateButton.disabled = !employee || !canManageTemplates();
  ui.userToTemplateButton.title = !employee ? "" : canManageTemplates() ? "" : "Only supervisors or admins can create templates.";
  ui.deleteUserButton.disabled = !employee || !isAdmin();
  ui.deleteUserButton.title = !employee ? "" : isAdmin() ? "" : "Only admins can delete users.";
  ui.saveUserButton.textContent = employee && !canModifyEmployees() ? "Request Change" : employee ? "Save User" : "Add User";
}

function updateTemplateFormState() {
  const template = selectedTemplate();
  const canManage = canManageTemplates();
  ui.templateFormTitle.textContent = template ? "Modify Template" : "Add Template";
  ui.templateFormSubtitle.textContent = canManage
    ? template
      ? `Last saved ${formatDateTime(template.updated_at)}.`
      : "Reusable access profile."
    : "Supervisor or admin access required.";
  ui.templateModeBadge.textContent = template ? "Selected" : "New";
  ui.saveTemplateButton.disabled = !canManage;
  ui.deleteTemplateButton.disabled = !template || !canManage;
  ui.saveTemplateButton.title = canManage ? "" : "Only supervisors or admins can save templates.";
  ui.deleteTemplateButton.title = !template ? "" : canManage ? "" : "Only supervisors or admins can delete templates.";
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

function copySelectedEmployee() {
  const employee = selectedEmployee();
  if (!employee) return;
  state.selectedId = null;
  state.expandedProfileId = null;
  state.activityScope = "all";
  const copy = {
    ...EMPTY_EMPLOYEE,
    status: "active",
    request_source: employee.request_source || "",
    access_needed: employee.access_needed || "",
    access_profile: { ...(employee.access_profile || {}) },
    notes: employee.notes || "",
  };
  fillUserForm(copy, { accessProfileOverride: copy.access_profile });
  renderProfileList();
  renderTemplatePicker();
  renderOverview();
  ui.userForm.elements.name.focus();
  showToast("User copied");
}

function createTemplateDraftFromSelectedUser() {
  const employee = selectedEmployee();
  if (!employee || !canManageTemplates()) return;
  state.editingTemplateId = null;
  ui.templateForm.reset();
  ui.templateForm.elements.name.value = templateDraftName(employee);
  ui.templateForm.elements.description.value = `Copied from ${employee.name || employee.email || "selected user"}.`;
  renderTemplates();
  renderTemplateFields(employee.access_profile || {});
  updateTemplateFormState();
  setActiveTab("templates");
  ui.templateForm.elements.name.focus();
  showToast("Template draft ready");
}

function applySelectedTemplateToUserForm() {
  const templateId = Number(ui.userTemplateSelect.value || 0);
  const template = state.accessTemplates.find((item) => item.id === templateId && item.active);
  if (!template) return;
  const merged = {
    ...collectAccessProfile(ui.userForm),
    ...(template.access_profile || {}),
  };
  state.formAccessProfileOverride = merged;
  renderCustomFields(merged);
  showToast(`Applied ${template.name}`);
}

async function saveTemplate(event) {
  event.preventDefault();
  if (!canManageTemplates()) return;
  const template = selectedTemplate();
  const path = template ? `/api/access-templates/${template.id}` : "/api/access-templates";
  const method = template ? "PATCH" : "POST";
  setButtonLoading(ui.saveTemplateButton, true);
  try {
    const result = await api(path, { method, body: templatePayload() });
    state.editingTemplateId = result.accessTemplate.id;
    await loadAll();
    showToast(template ? "Template saved" : "Template added");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(ui.saveTemplateButton, false);
    updateTemplateFormState();
  }
}

async function deleteSelectedTemplate() {
  const template = selectedTemplate();
  if (!template || !canManageTemplates()) return;
  const confirmed = window.confirm(`Delete template "${template.name}"?`);
  if (!confirmed) return;
  setButtonLoading(ui.deleteTemplateButton, true);
  try {
    await api(`/api/access-templates/${template.id}`, { method: "DELETE" });
    state.editingTemplateId = null;
    clearTemplateForm();
    await loadAll();
    showToast("Template deleted");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(ui.deleteTemplateButton, false);
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

async function validateUpdateConfig(button) {
  const form = button.closest("#updateConfigForm");
  if (!form || !isAdmin()) return;
  setButtonLoading(button, true);
  try {
    const result = await api("/api/admin/update/validate", { method: "POST", body: updateConfigPayload(form) });
    state.update = result.update || null;
    renderBackend();
    showToast("Update settings validated");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function applyUpdate(button) {
  const form = button.closest("#updateConfigForm");
  if (!form || !isAdmin()) return;
  const confirmed = window.confirm("Update Gatewatch from GitHub now? SQLite data and logs stay in the persistent data directory.");
  if (!confirmed) return;
  setButtonLoading(button, true);
  try {
    const result = await api("/api/admin/update/apply", { method: "POST", body: updateConfigPayload(form) });
    state.update = result.update || null;
    renderBackend();
    showToast("Update started");
    window.setTimeout(() => loadBackend({ announce: false }), 2000);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function deleteSelectedEmployee() {
  const employee = selectedEmployee();
  if (!employee || !isAdmin()) return;
  const confirmed = window.confirm(`Move ${employee.name} to Deleted Users? Admins can restore the record later.`);
  if (!confirmed) return;
  setButtonLoading(ui.deleteUserButton, true);
  try {
    await api(`/api/employees/${employee.id}`, { method: "DELETE" });
    clearUserForm();
    await loadAll();
    showToast("User moved to Deleted Users");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(ui.deleteUserButton, false);
  }
}

async function restoreDeletedEmployee(event) {
  const button = event.target.closest("[data-restore-employee]");
  if (!button || !isAdmin()) return;
  const employeeId = Number(button.dataset.restoreEmployee);
  if (!employeeId) return;
  setButtonLoading(button, true);
  try {
    const result = await api(`/api/employees/${employeeId}/restore`, { method: "POST" });
    state.selectedId = result.employee.id;
    state.expandedProfileId = result.employee.id;
    await loadAll();
    setActiveTab("users");
    showToast("User restored");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
}

async function loadBackend({ announce = false } = {}) {
  if (!isAdmin()) return;
  state.backendLoading = true;
  renderBackend();
  setButtonLoading(ui.refreshBackendButton, true);
  try {
    const [config, diagnostics, update] = await Promise.all([api("/api/admin/config"), api("/api/admin/diagnostics"), api("/api/admin/update/status")]);
    state.config = config.config || null;
    state.diagnostics = diagnostics.diagnostics || null;
    state.update = update.update || null;
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
    const skipped = sync.skippedDeleted ? `, ${sync.skippedDeleted} deleted skipped` : "";
    showToast(`Directory synced: ${sync.created || 0} created, ${sync.updated || 0} updated${skipped}`);
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
  const move = event.target.closest("[data-move-access-field]");
  if (move && isAdmin()) {
    await moveAccessField(Number(move.dataset.moveAccessField), move.dataset.moveDirection, move);
    return;
  }
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
  const name = formValue("name");
  const email = formValue("email");
  const employee = selectedEmployee();
  return {
    employee_id: formValue("employee_id") || employee?.employee_id || generatedEmployeeKey(name, email),
    name,
    email,
    phone: formValue("phone"),
    status: formValue("status") || employee?.status || "active",
    request_source: formValue("request_source"),
    access_needed: formValue("access_needed"),
    request_received: formChecked("request_received"),
    manager_approved: formChecked("manager_approved"),
    it_provisioned: formChecked("it_provisioned"),
    employee_notified: formChecked("employee_notified"),
    access_profile: collectAccessProfile(ui.userForm),
    notes: formValue("notes"),
  };
}

function templatePayload() {
  return {
    name: ui.templateForm.elements.name.value.trim(),
    description: ui.templateForm.elements.description.value.trim(),
    access_profile: collectAccessProfile(ui.templateForm),
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
    supervisorGroupCanonical: form.elements.supervisorGroupCanonical.value.trim(),
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

function collectAccessProfile(form) {
  const values = {};
  for (const field of activeAccessFields()) {
    const element = form.elements[`access_profile.${field.key}`];
    if (!element) continue;
    values[field.key] = field.field_type === "checkbox" ? Boolean(element.checked) : String(element.value || "").trim();
  }
  return values;
}

function formValue(name) {
  const element = ui.userForm.elements[name];
  return element ? String(element.value || "").trim() : "";
}

function formChecked(name) {
  const element = ui.userForm.elements[name];
  return element ? Boolean(element.checked) : false;
}

function generatedEmployeeKey(name, email) {
  const source = email || name || `user-${Date.now()}`;
  const slug = String(source)
    .toLowerCase()
    .replace(/@.*/, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return `USER-${slug || Date.now()}`;
}

function fillUserForm(employee, { accessProfileOverride = null } = {}) {
  const data = employee || EMPTY_EMPLOYEE;
  state.formAccessProfileOverride = accessProfileOverride;
  ui.userTemplateSelect.value = "";
  ui.userForm.elements.id.value = data.id || "";
  for (const key of ["employee_id", "name", "email", "phone", "status", "request_source", "access_needed", "notes"]) {
    if (ui.userForm.elements[key]) ui.userForm.elements[key].value = data[key] || "";
  }
  for (const key of CHECKLIST_FIELDS) {
    ui.userForm.elements[key].checked = Boolean(data[key]);
  }
  renderCustomFields(accessProfileOverride || data.access_profile || {});
  updateFormState();
}

function clearUserForm({ focus = false } = {}) {
  state.selectedId = null;
  state.expandedProfileId = null;
  state.activityScope = "all";
  state.formAccessProfileOverride = null;
  ui.userForm.reset();
  fillUserForm(null);
  renderUsers();
  renderOverview();
  if (focus) ui.userForm.elements.name.focus();
}

function selectEmployee(id, { openUsers = false, expand = false } = {}) {
  const employee = state.employees.find((item) => item.id === id);
  if (!employee) return;
  state.selectedId = id;
  state.selectedActivityKey = null;
  state.activityScope = "selected";
  state.formAccessProfileOverride = null;
  if (expand) state.expandedProfileId = id;
  fillUserForm(employee);
  if (openUsers) setActiveTab("users");
  renderOverview();
  renderUsers();
}

function selectedTemplate() {
  return state.accessTemplates.find((template) => template.id === state.editingTemplateId) || null;
}

function fillTemplateForm(template) {
  const data = template || { id: "", name: "", description: "", access_profile: {} };
  ui.templateForm.elements.id.value = data.id || "";
  ui.templateForm.elements.name.value = data.name || "";
  ui.templateForm.elements.description.value = data.description || "";
  renderTemplateFields(data.access_profile || {});
  updateTemplateFormState();
}

function clearTemplateForm({ focus = false } = {}) {
  state.editingTemplateId = null;
  ui.templateForm.reset();
  fillTemplateForm(null);
  renderTemplates();
  if (focus) ui.templateForm.elements.name.focus();
}

function selectTemplate(id) {
  const template = state.accessTemplates.find((item) => item.id === id && item.active);
  if (!template) return;
  state.editingTemplateId = id;
  fillTemplateForm(template);
  renderTemplates();
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

function focusUserTemplatePicker(id) {
  const employee = state.employees.find((item) => item.id === id);
  if (!employee) return;
  selectEmployee(id, { openUsers: true, expand: true });
  if (ui.configuredFieldsDisclosure) ui.configuredFieldsDisclosure.open = true;
  ui.userTemplateSelect.focus();
  showToast("Choose a template to apply");
}

function selectActivityFromEvent(event) {
  const row = event.target.closest("[data-activity-key]");
  if (!row) return;
  const nextKey = row.dataset.activityKey;
  state.selectedActivityKey = state.selectedActivityKey === nextKey ? null : nextKey;
  const entry = state.audit.find((item) => activityKey(item) === state.selectedActivityKey);
  if (String(entry?.entity_type || "") === "employee" && state.employees.some((employee) => Number(employee.id) === Number(entry.entity_id))) {
    state.selectedId = Number(entry.entity_id);
  }
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

function permissions() {
  return state.auth?.permissions || {};
}

function currentActor() {
  return permissions().actor || state.auth?.user?.actor || state.auth?.user?.name || "Local user";
}

function canModifyEmployees() {
  return Boolean(permissions().canModifyEmployees);
}

function canManageTemplates() {
  return Boolean(permissions().canManageTemplates ?? canModifyEmployees());
}

function isAdmin() {
  const current = permissions();
  if ("canAdministerSystem" in current) return Boolean(current.canAdministerSystem);
  if ("canDeleteEmployees" in current) return Boolean(current.canDeleteEmployees);
  return Boolean(current.canModifyEmployees);
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

function activityEntries() {
  const employee = selectedEmployee();
  if (state.activityScope === "selected" && employee) {
    return state.audit.filter((entry) => String(entry.entity_type || "") === "employee" && Number(entry.entity_id) === Number(employee.id));
  }
  return currentActorAudit();
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

function setFilter(filter) {
  state.filter = FILTERS.some((item) => item.key === filter) ? filter : "all";
  try {
    localStorage.setItem(FILTER_STORAGE_KEY, state.filter);
  } catch {
    // Filters still work for this page when storage is unavailable.
  }
}

async function moveAccessField(fieldId, direction, button) {
  const fields = activeAccessFields();
  const index = fields.findIndex((field) => field.id === fieldId);
  const offset = direction === "up" ? -1 : direction === "down" ? 1 : 0;
  const nextIndex = index + offset;
  if (index < 0 || nextIndex < 0 || nextIndex >= fields.length) return;
  const reordered = [...fields];
  const [current] = reordered.splice(index, 1);
  reordered.splice(nextIndex, 0, current);
  const updates = reordered
    .map((field, orderIndex) => ({ field, sort_order: (orderIndex + 1) * 10 }))
    .filter((item) => Number(item.field.sort_order || 0) !== item.sort_order);
  setButtonLoading(button, true);
  try {
    for (const update of updates) {
      await api(`/api/access-fields/${update.field.id}`, { method: "PATCH", body: { sort_order: update.sort_order } });
    }
    await loadAll();
    renderBackend();
    showToast("Field moved");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    setButtonLoading(button, false);
  }
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
    employee.phone,
    employee.notes,
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

function statusTone(stateValue) {
  const normalized = String(stateValue || "").toLowerCase();
  if (normalized === "succeeded") return "online";
  if (normalized === "failed" || normalized === "unknown") return "critical";
  if (normalized === "running" || normalized === "restart_queued") return "warning";
  return "offline";
}

function parseAuditJson(value) {
  if (!value) return {};
  if (typeof value === "object") return value || {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function auditChanges(before, after) {
  const beforeObject = before || {};
  const afterObject = after || {};
  const ignored = new Set(["id", "created_at", "updated_at", "access_profile_json"]);
  const keys = [...new Set([...Object.keys(beforeObject), ...Object.keys(afterObject)])]
    .filter((key) => !ignored.has(key))
    .filter((key) => JSON.stringify(beforeObject[key] ?? "") !== JSON.stringify(afterObject[key] ?? ""))
    .sort((left, right) => left.localeCompare(right));
  return keys.map((key) => ({
    label: labelize(key),
    before: auditValue(beforeObject[key]),
    after: auditValue(afterObject[key]),
  }));
}

function auditValue(value) {
  if (value === undefined || value === null || value === "") return "--";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number") return String(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
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
        ${configInput("Supervisor group", "supervisorGroupCanonical", runtime.supervisorGroupCanonical || "", "gcefcu.org/Users/Gatewatch Supervisors", "span-2")}
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

function renderUpdatePanel(update) {
  const config = update?.config || {};
  const status = update?.status || {};
  const checks = update?.checks || [];
  const logTail = update?.logTail || "";
  const running = status.state === "running";
  const modes = ["auto", "volume", "systemd"];
  return `
    <section class="log-card span-2 update-panel" aria-labelledby="updatePanelTitle">
      <div class="section-row">
        <h3 id="updatePanelTitle">App Update</h3>
        <span class="severity severity--${statusTone(status.state)}">${escapeHtml(labelize(status.state || "idle"))}</span>
      </div>
      <form id="updateConfigForm" class="backend-config-form">
        <div class="field-grid">
          <label>
            <span>Mode</span>
            <select name="updateMode">
              ${modes.map((mode) => `<option value="${mode}" ${config.updateMode === mode ? "selected" : ""}>${escapeHtml(labelize(mode))}</option>`).join("")}
            </select>
          </label>
          ${configInput("GitHub branch", "updateBranch", config.updateBranch || "main", "main")}
          ${configInput("Source URL", "updateSourceUrl", config.updateSourceUrl || "", "https://github.com/skellywix/Gatewatch/archive/refs/heads/main.tar.gz", "span-2")}
          ${configInput("Data directory", "updateDataDir", config.updateDataDir || "", "/data", "span-2")}
          ${configInput("Install directory", "updateInstallDir", config.updateInstallDir || "", "/opt/gatewatch", "span-2")}
          ${configInput("Service name", "updateServiceName", config.updateServiceName || "gatewatch", "gatewatch")}
          ${configInput("Status file", "updateStatusFile", config.updateStatusFile || "", "/data/gatewatch-update-status.json", "span-2")}
          ${configInput("Log file", "updateLogFile", config.updateLogFile || "", "/data/gatewatch-update.log", "span-2")}
        </div>
        <label class="toggle-field config-toggle">
          <input name="restartAfterUpdate" type="checkbox" ${config.restartAfterUpdate !== false ? "checked" : ""} />
          Restart after update
        </label>
        <div class="config-status">
          ${metadataList([
            ["State", labelize(status.state || "idle")],
            ["Message", status.message || "--"],
            ["Updated", status.updatedAt ? formatDateTime(status.updatedAt) : "--"],
            ["Backup", status.backupPath || "--"],
            ["Release", status.releasePath || "--"],
          ])}
        </div>
        <div class="diagnostic-grid">${renderConfigChecks(checks)}</div>
        ${logTail ? `<pre class="update-log">${escapeHtml(logTail)}</pre>` : ""}
        <div class="form-actions">
          <button class="button button--secondary" type="button" data-validate-update ${running ? "disabled" : ""}>Validate Update</button>
          <button class="button button--primary" type="button" data-apply-update ${running ? "disabled" : ""}>Update from GitHub</button>
        </div>
      </form>
    </section>
  `;
}

function renderAccessFieldManager() {
  const editing = state.accessFields.find((field) => field.id === state.editingAccessFieldId) || null;
  const activeFields = activeAccessFields();
  return `
    <section class="log-card access-field-manager" aria-labelledby="customFormAdminTitle">
      <div class="section-row">
        <h3 id="customFormAdminTitle">Custom Form Fields</h3>
        <span class="muted-label">${activeFields.length} active</span>
      </div>
      <form id="accessFieldForm" class="access-field-form">
        <input name="id" type="hidden" value="${escapeHtml(editing?.id || "")}" />
        <div class="field-grid">
          ${configInput("Label", "label", editing?.label || "", "Core banking role", "", "text", "Name shown on the user form")}
          ${configInput("Section", "section", editing?.section || "Systems Access", "Systems Access", "", "text", "Group related fields together")}
          <label title="Choose what kind of input this field uses">
            <span>Type</span>
            <select name="fieldType">
              ${["text", "textarea", "select", "checkbox", "date"].map((type) => `<option value="${type}" ${editing?.field_type === type ? "selected" : ""}>${labelize(type)}</option>`).join("")}
            </select>
          </label>
          <label title="Lower positions appear earlier on the user form">
            <span>Position</span>
            <input name="sort_order" type="number" value="${escapeHtml(editing?.sort_order ?? "")}" placeholder="100" autocomplete="off" />
            <small>Lower appears first.</small>
          </label>
          <label class="span-2" title="Only used for dropdown fields">
            <span>Options</span>
            <textarea name="options" placeholder="One option per line">${escapeHtml((editing?.options || []).join("\n"))}</textarea>
          </label>
        </div>
        <label class="toggle-field config-toggle" title="Require this field before a user can be saved">
          <input name="required" type="checkbox" ${editing?.required ? "checked" : ""} />
          Required field
        </label>
        <div class="form-actions">
          <button class="button button--ghost" type="button" data-cancel-access-field ${editing ? "" : "disabled"}>Cancel</button>
          <button class="button button--primary" type="submit">${editing ? "Save Field" : "Add Field"}</button>
        </div>
      </form>
      <div id="accessProfileFields" class="access-field-list">
        ${activeFields.length ? activeFields.map((field, index) => renderAccessFieldRow(field, index, activeFields.length)).join("") : emptyState("No custom fields", "Add fields to shape the user form.")}
      </div>
    </section>
  `;
}

function renderAccessFieldRow(field, index = 0, total = 1) {
  return `
    <article class="access-field-row">
      <div>
        <strong>${escapeHtml(field.label)}</strong>
        <span>${escapeHtml(field.section)} / ${escapeHtml(labelize(field.field_type))}${field.required ? " / Required" : ""} / Position ${escapeHtml(field.sort_order ?? 0)}</span>
      </div>
      <div class="row-actions">
        <button class="button button--ghost" type="button" data-move-access-field="${field.id}" data-move-direction="up" title="Move this field earlier in the form" ${index === 0 ? "disabled" : ""}>Up</button>
        <button class="button button--ghost" type="button" data-move-access-field="${field.id}" data-move-direction="down" title="Move this field later in the form" ${index >= total - 1 ? "disabled" : ""}>Down</button>
        <button class="button button--ghost" type="button" data-edit-access-field="${field.id}" title="Edit this form field">Edit</button>
        <button class="button button--danger" type="button" data-delete-access-field="${field.id}" title="Remove this field from new user forms">Remove</button>
      </div>
    </article>
  `;
}

function configInput(label, name, value, placeholder, className = "", type = "text", title = "") {
  return `
    <label class="${escapeHtml(className)}" ${title ? `title="${escapeHtml(title)}"` : ""}>
      <span>${escapeHtml(label)}</span>
      <input name="${escapeHtml(name)}" type="${escapeHtml(type)}" value="${escapeHtml(value || "")}" placeholder="${escapeHtml(placeholder || "")}" autocomplete="off" />
    </label>
  `;
}

function updateConfigPayload(form) {
  return {
    updateMode: form.elements.updateMode?.value || "auto",
    updateBranch: form.elements.updateBranch?.value || "main",
    updateSourceUrl: form.elements.updateSourceUrl?.value || "",
    updateDataDir: form.elements.updateDataDir?.value || "",
    updateInstallDir: form.elements.updateInstallDir?.value || "",
    updateServiceName: form.elements.updateServiceName?.value || "gatewatch",
    updateStatusFile: form.elements.updateStatusFile?.value || "",
    updateLogFile: form.elements.updateLogFile?.value || "",
    restartAfterUpdate: Boolean(form.elements.restartAfterUpdate?.checked),
  };
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

function clearInvalidTemplateSelection() {
  if (state.editingTemplateId && !state.accessTemplates.some((template) => template.id === state.editingTemplateId && template.active)) {
    state.editingTemplateId = null;
    fillTemplateForm(null);
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
  if (["POST", "PATCH", "DELETE"].includes(request.method) && state.auth?.csrfToken) {
    headers[CSRF_HEADER] = state.auth.csrfToken;
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

function handleTabKeydown(event) {
  const current = event.target.closest("[data-tab]");
  if (!current || current.disabled) return;
  const tabs = [...document.querySelectorAll("[data-tab]")].filter((tab) => !tab.hidden && !tab.disabled);
  const currentIndex = tabs.indexOf(current);
  if (currentIndex < 0) return;

  let nextIndex = currentIndex;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex = (currentIndex + 1) % tabs.length;
  else if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = tabs.length - 1;
  else return;

  event.preventDefault();
  const next = tabs[nextIndex];
  setActiveTab(next.dataset.tab);
  next.focus();
}

function initialTheme() {
  const rootTheme = document.documentElement?.dataset?.theme;
  if (THEMES.has(rootTheme)) return rootTheme;
  try {
    const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
    if (THEMES.has(savedTheme)) return savedTheme;
  } catch {
    return "light";
  }
  return "light";
}

function initialFilter() {
  try {
    const savedFilter = localStorage.getItem(FILTER_STORAGE_KEY);
    if (FILTERS.some((item) => item.key === savedFilter)) return savedFilter;
  } catch {
    return "all";
  }
  return "all";
}

function setTheme(theme) {
  if (!THEMES.has(theme)) return;
  state.theme = theme;
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Theme still applies for this page even when storage is unavailable.
  }
}

function applyTheme(theme) {
  const activeTheme = THEMES.has(theme) ? theme : "light";
  if (document.documentElement) {
    document.documentElement.dataset.theme = activeTheme;
  }
  if (ui.themeLightButton && ui.themeDarkButton) {
    ui.themeLightButton.classList.toggle("is-active", activeTheme === "light");
    ui.themeLightButton.setAttribute("aria-pressed", activeTheme === "light" ? "true" : "false");
    ui.themeDarkButton.classList.toggle("is-active", activeTheme === "dark");
    ui.themeDarkButton.setAttribute("aria-pressed", activeTheme === "dark" ? "true" : "false");
  }
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

function iconSvg(name, className = "inline-icon") {
  const paths = {
    users: `<path d="M16 21v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2"></path><circle cx="9.5" cy="7" r="4"></circle><path d="M22 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path>`,
    check: `<path d="M20 6 9 17l-5-5"></path>`,
    clock: `<circle cx="12" cy="12" r="9"></circle><path d="M12 7v5l3 2"></path>`,
    alert: `<path d="M12 9v4"></path><path d="M12 17h.01"></path><path d="M10.3 3.9 2.6 17.3A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.7L13.7 3.9a2 2 0 0 0-3.4 0z"></path>`,
  };
  return `<svg class="${escapeHtml(className)}" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.check}</svg>`;
}

function labelize(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (match) => match.toUpperCase());
}

function templateDraftName(employee) {
  const base = employee.title || employee.department || employee.name || employee.email || "User";
  return `${base} Access`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
