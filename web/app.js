const state = {
  employees: [],
  audit: [],
  summary: { total: 0, active: 0, terminated: 0, updatedToday: 0 },
  selectedId: null,
  search: "",
};

const form = document.querySelector("#employeeForm");
const table = document.querySelector("#employeeTable");
const toast = document.querySelector("#toast");

document.querySelector("#searchInput").addEventListener("input", (event) => {
  state.search = event.target.value.trim();
  renderEmployees();
});

document.querySelector("#refreshButton").addEventListener("click", () => loadAll(true));
document.querySelector("#newEmployeeButton").addEventListener("click", clearForm);
document.querySelector("#resetButton").addEventListener("click", clearForm);
document.querySelector("#deleteButton").addEventListener("click", deleteSelectedEmployee);
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

table.addEventListener("click", (event) => {
  const row = event.target.closest("[data-employee-id]");
  if (!row) return;
  selectEmployee(Number(row.dataset.employeeId));
});

loadAll(false);

async function loadAll(showSuccess) {
  try {
    const data = await api("/api/bootstrap");
    state.summary = data.summary;
    state.employees = data.employees;
    state.audit = data.audit;
    if (state.selectedId && !state.employees.some((employee) => employee.id === state.selectedId)) {
      clearForm();
    }
    renderAll();
    if (showSuccess) showToast("Refreshed");
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderAll() {
  renderMetrics();
  renderEmployees();
  renderActivity();
}

function renderMetrics() {
  document.querySelector("#metricTotal").textContent = state.summary.total ?? 0;
  document.querySelector("#metricActive").textContent = state.summary.active ?? 0;
  document.querySelector("#metricProgress").textContent = state.summary.inProgress ?? 0;
  document.querySelector("#metricUpdated").textContent = state.summary.updatedToday ?? 0;
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
        <td colspan="5">
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
        <tr data-employee-id="${employee.id}" class="${employee.id === state.selectedId ? "selected" : ""}" tabindex="0">
          <td>
            <div class="employee-cell">
              <span class="avatar">${escapeHtml(initials(employee.name))}</span>
              <span>
                <strong>${escapeHtml(employee.name)}</strong>
                <small>${escapeHtml(employee.employee_id)} / ${escapeHtml(employee.email)}</small>
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

function renderActivity() {
  const list = document.querySelector("#activityList");
  if (!state.audit.length) {
    list.innerHTML = `<div class="empty-state"><strong>No activity yet</strong><span>Changes appear here after the first save.</span></div>`;
    return;
  }
  list.innerHTML = state.audit
    .slice(0, 8)
    .map(
      (entry) => `
        <article class="activity-item">
          <span class="activity-action">${escapeHtml(entry.action)}</span>
          <div>
            <strong>${escapeHtml(entry.summary)}</strong>
            <small>${formatDateTime(entry.created_at)} / ${escapeHtml(entry.actor)}</small>
          </div>
        </article>
      `
    )
    .join("");
}

function selectEmployee(employeeId) {
  const employee = state.employees.find((item) => item.id === employeeId);
  if (!employee) return;
  state.selectedId = employeeId;
  for (const [key, value] of Object.entries(employee)) {
    const field = form.elements[key];
    if (!field) continue;
    field.value = value ?? "";
  }
  syncStepToggles(employee);
  document.querySelector("#formTitle").textContent = "Edit Employee";
  document.querySelector("#formSubtitle").textContent = `Last saved ${formatDateTime(employee.updated_at)}.`;
  document.querySelector("#selectedBadge").outerHTML = statusBadge(employee.status, "selectedBadge");
  document.querySelector("#saveButton").textContent = "Save changes";
  document.querySelector("#deleteButton").disabled = false;
  renderEmployees();
}

function clearForm() {
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
  document.querySelector("#saveButton").textContent = "Create employee";
  document.querySelector("#deleteButton").disabled = true;
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
    state.selectedId = result.employee.id;
    await loadAll(false);
    selectEmployee(result.employee.id);
    showToast(id ? "Employee updated" : "Employee created");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function deleteSelectedEmployee() {
  if (!state.selectedId) return;
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
    "X-Gatewatch-Actor": "Local user",
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
  const safe = status === "terminated" ? "terminated" : "active";
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
