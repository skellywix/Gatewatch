const TABS = ["roster", "activity", "configuration"];

const state = {
  employees: [],
  changeRequests: [],
  audit: [],
  auth: null,
  config: null,
  configPreview: null,
  configLoading: false,
  summary: { total: 0, active: 0, disabled: 0, terminated: 0, updatedToday: 0 },
  selectedId: null,
  search: "",
  activeTab: tabFromLocation(),
};

const form = document.querySelector("#employeeForm");
const configForm = document.querySelector("#configForm");
const configTemplate = document.querySelector("#configTemplate");
const table = document.querySelector("#employeeTable");
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
document.querySelector("#resetButton").addEventListener("click", clearForm);
document.querySelector("#deleteButton").addEventListener("click", deleteSelectedEmployee);
document.querySelector("#syncEntraButton").addEventListener("click", syncEntraDirectory);
document.querySelector("#changeRequestList").addEventListener("click", reviewChangeRequest);
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

window.addEventListener("popstate", syncTabFromLocation);
window.addEventListener("hashchange", syncTabFromLocation);

loadAll(false);

async function loadAll(showSuccess) {
  try {
    const data = await api("/api/bootstrap");
    state.summary = data.summary;
    state.employees = data.employees;
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
  state.activeTab = requested === "configuration" && !canModifyEmployees() ? "roster" : requested;
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
  renderAutofillOptions();
  renderChangeRequests();
  renderActivity();
  renderConfig();
  updateFormPermissions();
}

function renderTabs() {
  const configAllowed = canModifyEmployees();
  const configTab = document.querySelector("#configurationTab");
  if (configTab) {
    configTab.hidden = !configAllowed;
    configTab.disabled = !configAllowed;
  }
  if (state.activeTab === "configuration" && !configAllowed) {
    state.activeTab = "roster";
    updateLocationTab("roster", { replace: true });
  }
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const active = button.dataset.tab === state.activeTab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll("[data-view]").forEach((panel) => {
    const allowed = panel.dataset.view !== "configuration" || configAllowed;
    const active = allowed && panel.dataset.view === state.activeTab;
    panel.hidden = !active;
    panel.classList.toggle("active", active);
  });
  if (state.activeTab === "configuration" && configAllowed && !state.config && !state.configLoading) {
    loadConfig(false);
  }
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

function renderAutofillOptions() {
  setDatalistOptions("#keyFobOptions", state.employees.map((employee) => employee.employee_id));
  setDatalistOptions("#emailOptions", state.employees.map((employee) => employee.email));
  setDatalistOptions("#departmentOptions", state.employees.map((employee) => employee.department));
  setDatalistOptions("#titleOptions", state.employees.map((employee) => employee.title));
  setDatalistOptions("#locationOptions", state.employees.map((employee) => employee.location));
  setDatalistOptions("#managerOptions", state.employees.map((employee) => employee.manager));
  setDatalistOptions("#accessNeededOptions", state.employees.map((employee) => employee.access_needed));
}

function setDatalistOptions(selector, values) {
  const list = document.querySelector(selector);
  if (!list) return;
  const unique = [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right))
    .slice(0, 200);
  list.innerHTML = unique.map((value) => `<option value="${escapeHtml(value)}"></option>`).join("");
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
        .map(([key, value]) => `${labelize(key)}: ${formatRequestValue(value)}`)
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
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value === 1) return "yes";
  if (value === 0) return "no";
  const text = String(value ?? "").trim();
  return text || "(blank)";
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
  if (state.activeTab !== "roster") {
    setActiveTab("roster", { push: true });
  }
  state.selectedId = employeeId;
  for (const [key, value] of Object.entries(employee)) {
    const field = form.elements[key];
    if (!field) continue;
    field.value = value ?? "";
  }
  syncStepToggles(employee);
  document.querySelector("#formTitle").textContent = "Edit Employee";
  document.querySelector("#formSubtitle").textContent = canModifyEmployees()
    ? `Last saved ${formatDateTime(employee.updated_at)}.`
    : "Submit edits for Domain Admin approval.";
  document.querySelector("#selectedBadge").outerHTML = statusBadge(employee.status, "selectedBadge");
  updateFormPermissions();
  renderEmployees();
}

function clearForm() {
  if (state.activeTab !== "roster") {
    setActiveTab("roster", { push: true });
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
  syncStepToggles({});
  document.querySelector("#formTitle").textContent = "Create Employee";
  document.querySelector("#formSubtitle").textContent = "Saved to SQLite immediately.";
  document.querySelector("#selectedBadge").outerHTML = `<span id="selectedBadge" class="status-badge muted">New</span>`;
  setSaveButtonLabel("Create employee");
  updateFormPermissions();
  renderEmployees();
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
  return `<span${idAttr} class="status-badge ${safe}">${labelize(safe)}</span>`;
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

function requiredGroupMessage() {
  const group = state.auth?.permissions?.adminGroup || "the configured admin group";
  return `Only members of ${group} can approve changes, delete, sync, or view configuration.`;
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
  const saveButton = document.querySelector("#saveButton");
  deleteButton.disabled = !state.selectedId || !canModifyEmployees();
  saveButton.disabled = false;
  if (state.selectedId && !canModifyEmployees()) {
    setSaveButtonLabel("Request changes");
    saveButton.title = "Submit a change request for Domain Admin approval.";
  } else {
    setSaveButtonLabel(state.selectedId ? "Save changes" : "Create employee");
    saveButton.title = "";
  }
  deleteButton.title = state.selectedId && !canModifyEmployees() ? requiredGroupMessage() : "";
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
