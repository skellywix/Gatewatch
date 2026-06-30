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
  selectedId: null,
  editingAccessFieldId: null,
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
const toast = document.querySelector("#toast");

document.querySelector("#searchInput").addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  renderEmployees();
});

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

window.addEventListener("popstate", syncTabFromLocation);
window.addEventListener("hashchange", syncTabFromLocation);

loadAll(false);

async function loadAll(showSuccess) {
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
  }
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
  renderDirectory();
  renderEmployees();
  renderProfileEmployeeList();
  renderAutofillOptions();
  renderAccessProfileFields(selectedEmployee()?.access_profile || {});
  renderAccessFieldCatalog();
  renderChangeRequests();
  renderActivity();
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
  document.querySelector("#metricTotal").textContent = state.summary.total ?? 0;
  document.querySelector("#metricActive").textContent = state.summary.active ?? 0;
  document.querySelector("#metricProgress").textContent = state.summary.inProgress ?? 0;
  document.querySelector("#metricUpdated").textContent = state.summary.updatedToday ?? 0;
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
        <tr data-employee-id="${employee.id}" class="${employee.id === state.selectedId ? "selected" : ""}" tabindex="0" aria-label="Edit ${escapeHtml(employee.name)}">
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
          <td>${statusBadge(employee.status)}</td>
          <td>${formatDateTime(employee.updated_at)}</td>
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
          ${statusBadge(employee.status)}
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
}

function renderAccessProfileInput(field, value) {
  const name = accessProfileInputName(field.key);
  const required = field.required ? " required" : "";
  const label = `<span>${escapeHtml(field.label)}</span>`;
  if (field.field_type === "checkbox") {
    return `
      <label class="toggle-row access-toggle">
        <input name="${escapeHtml(name)}" type="checkbox" ${value ? "checked" : ""}${required} />
        <span>${escapeHtml(field.label)}</span>
      </label>
    `;
  }
  if (field.field_type === "textarea") {
    return `
      <label class="span-2">
        ${label}
        <textarea name="${escapeHtml(name)}" maxlength="2000" placeholder="${escapeHtml(field.label)}"${required}>${escapeHtml(value || "")}</textarea>
      </label>
    `;
  }
  if (field.field_type === "select") {
    return `
      <label>
        ${label}
        <select name="${escapeHtml(name)}"${required}>
          <option value="">Not set</option>
          ${(field.options || []).map((option) => `<option value="${escapeHtml(option)}" ${String(value || "") === option ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
        </select>
      </label>
    `;
  }
  return `
    <label>
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
            <button class="secondary-button" type="button" data-access-field-action="edit" data-access-field-id="${field.id}">Edit</button>
            <button class="danger-button" type="button" data-access-field-action="delete" data-access-field-id="${field.id}" ${field.active ? "" : "disabled"}>Remove</button>
          </div>
        </article>
      `
    )
    .join("");
}

function renderActivity() {
  const list = document.querySelector("#activityList");
  if (!state.audit.length) {
    list.innerHTML = `<div class="empty-state"><strong>No activity yet</strong><span>Changes appear here after the first save.</span></div>`;
    return;
  }
  list.innerHTML = state.audit
    .slice(0, 50)
    .map(
      (entry) => `
        <article class="activity-item">
          <span class="activity-action">${escapeHtml(labelize(entry.action))}</span>
          <div class="activity-copy">
            <strong>${escapeHtml(entry.summary)}</strong>
            <div class="activity-meta">
              <span>Changed by <b>${escapeHtml(entry.actor || "Local user")}</b></span>
              <span>${formatDateTime(entry.created_at)}</span>
            </div>
          </div>
        </article>
      `
    )
    .join("");
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
            <button class="rail-action approve-action" type="button" data-request-action="approve" data-request-id="${request.id}">Approve</button>
            <button class="rail-action muted-link" type="button" data-request-action="reject" data-request-id="${request.id}">Reject</button>
          </div>
        `
        : `<small>Waiting for Domain Admin approval.</small>`;
      return `
        <article class="change-request-item">
          <strong>${escapeHtml(employeeName)}</strong>
          <small>${escapeHtml(keyFob)}</small>
          <span class="field-chip">${escapeHtml(fields || "No fields")}</span>
          <small>Requested by ${escapeHtml(request.requested_by || "Local user")}</small>
          ${actions}
        </article>
      `;
    })
    .join("");
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
  try {
    const data = await api("/api/admin/diagnostics");
    state.diagnostics = data.diagnostics;
    renderDiagnostics();
    if (showSuccess) showToast("Logs refreshed");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    state.diagnosticsLoading = false;
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
  document.querySelector("#selectedBadge").outerHTML = statusBadge(employee.status, "selectedBadge");
  updateFormPermissions();
  renderEmployees();
  renderProfileEmployeeList();
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
  document.querySelector("#selectedBadge").outerHTML = `<span id="selectedBadge" class="status-badge status-chip muted">New</span>`;
  setSaveButtonLabel("Create employee");
  updateFormPermissions();
  renderEmployees();
  renderProfileEmployeeList();
  form.elements.employee_id.focus();
}

async function saveEmployee() {
  const payload = formPayload();
  const id = state.selectedId;
  const path = id ? `/api/employees/${id}` : "/api/employees";
  const method = id ? "PATCH" : "POST";
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
  button.disabled = true;
  try {
    await api(`/api/change-requests/${requestId}/${action}`, { method: "POST", body: {} });
    await loadAll(false);
    showToast(action === "approve" ? "Change request approved" : "Change request rejected");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
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
  try {
    await api(`/api/employees/${state.selectedId}`, { method: "DELETE" });
    clearForm();
    await loadAll(false);
    showToast("Employee deleted");
  } catch (error) {
    showToast(error.message, true);
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
  button.disabled = true;
  result.textContent = "Syncing";
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
    button.disabled = !(state.auth && state.auth.graphConfigured && canModifyEmployees());
  }
}

async function loadConfig(showSuccess) {
  if (!canModifyEmployees() || state.configLoading) return;
  state.configLoading = true;
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
}

function renderConfig() {
  const checks = document.querySelector("#configChecks");
  if (!checks || !configTemplate) return;
  if (!canModifyEmployees()) {
    checks.innerHTML = "";
    configTemplate.textContent = "";
    return;
  }
  const config = state.configPreview || state.config;
  if (!config) {
    checks.innerHTML = `<div class="empty-state"><strong>No checks loaded</strong><span>Open Configuration or refresh checks.</span></div>`;
    configTemplate.textContent = "";
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
}

async function validateConfig(event) {
  event.preventDefault();
  if (!canModifyEmployees()) {
    showToast(requiredGroupMessage(), true);
    return;
  }
  try {
    const data = await api("/api/admin/config/validate", {
      method: "POST",
      body: configPayload(),
    });
    state.configPreview = data.preview;
    renderConfig();
    showToast("Configuration validated");
  } catch (error) {
    showToast(error.message, true);
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
  try {
    await navigator.clipboard.writeText(text);
    showToast("Environment template copied");
  } catch (error) {
    showToast("Clipboard access was blocked by the browser", true);
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

function statusBadge(status, id = "") {
  const safe = status === "terminated" || status === "disabled" ? status : "active";
  const idAttr = id ? ` id="${id}"` : "";
  return `<span${idAttr} class="status-badge status-chip ${safe}">${labelize(safe)}</span>`;
}

function progressPill(employee) {
  const complete = [
    employee.request_received,
    employee.manager_approved,
    employee.it_provisioned,
    employee.employee_notified,
  ].filter(Boolean).length;
  const label = complete === 4 ? "Ready" : complete === 0 ? "Not started" : `${complete}/4 done`;
  const tone = complete === 4 ? "complete" : complete === 0 ? "muted" : "working";
  const source = employee.request_source ? ` by ${employee.request_source}` : "";
  const needed = employee.access_needed ? ` - ${employee.access_needed}` : "";
  return `
    <span class="progress-pill ${tone}">${escapeHtml(label)}</span>
    <small class="progress-note">${escapeHtml(`${source}${needed}`.trim())}</small>
  `;
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
  }
}

function stepIsPressed(step) {
  return document.querySelector(`[data-step="${step}"]`)?.getAttribute("aria-pressed") === "true";
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
