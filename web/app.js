const configurationTabs = new Set(["setup", "identity", "data", "email", "evidence"]);

const state = {
  role: localStorage.getItem("access-register-role") || "Admin",
  view: "dashboard",
  summary: null,
  employees: [],
  resourceCategories: [],
  systems: [],
  accessRecords: [],
  imports: [],
  adSyncRuns: [],
  adSyncSettings: null,
  accessRequests: [],
  emailSettings: null,
  emailRoutes: [],
  disabledAccess: [],
  riskFindings: [],
  notifications: [],
  reviewCampaigns: [],
  sharedAccounts: [],
  physicalCredentials: [],
  connectors: [],
  ownerDashboard: null,
  backups: [],
  authSettings: null,
  offboarding: [],
  audit: [],
  auditIntegrity: null,
  session: null,
  selectedEmployeeId: null,
  selectedEmployee: null,
  pendingRemovalRecordId: null,
  filterText: "",
  statusFilter: "",
  configurationTab: configurationTabs.has(localStorage.getItem("gatewatch-configuration-tab"))
    ? localStorage.getItem("gatewatch-configuration-tab")
    : "setup",
};

const viewMeta = {
  dashboard: {
    title: "Dashboard",
    subtitle: "Track employee access, review stale privileges, and close offboarding gaps.",
  },
  inventory: {
    title: "Access Inventory",
    subtitle: "Create, review, and remove access records across software and physical systems.",
  },
  requests: {
    title: "Requests",
    subtitle: "Submit, approve, deny, and fulfill access requests without PDF handoffs.",
  },
  employees: {
    title: "Employees",
    subtitle: "Manage employee status so access reviews and offboarding stay current.",
  },
  systems: {
    title: "Systems & Locations",
    subtitle: "Define the resources that employees can access and assign accountable owners.",
  },
  reviews: {
    title: "Reviews",
    subtitle: "Certify stale or unknown access, or route it to removal.",
  },
  risk: {
    title: "Risk Center",
    subtitle: "Work disabled-user access, expiring access, overdue removals, and alert queues.",
  },
  offboarding: {
    title: "Offboarding",
    subtitle: "Remove access for terminated employees and capture evidence.",
  },
  governance: {
    title: "Governance",
    subtitle: "Manage recurring reviews, owner accountability, backups, and audit exports.",
  },
  configuration: {
    title: "Configuration",
    subtitle: "Set up authentication, directory sync, email notifications, connectors, backups, imports, and audit evidence.",
  },
  assets: {
    title: "Assets",
    subtitle: "Track shared accounts and physical credentials alongside system access.",
  },
  directory: {
    title: "AD Sync",
    subtitle: "Create new employees from Active Directory and flag disabled directory accounts.",
  },
  imports: {
    title: "Imports",
    subtitle: "Use CSV exports to discover accounts that are missing from the inventory.",
  },
  connectors: {
    title: "Connectors",
    subtitle: "Plan direct integrations for systems that should move beyond CSV imports.",
  },
  security: {
    title: "Security",
    subtitle: "Prepare AD or Entra role mappings for production authentication.",
  },
  audit: {
    title: "Audit Log",
    subtitle: "Inspect the evidence trail for inventory changes, reviews, imports, and removals.",
  },
};

const accessTypes = [
  "user",
  "admin",
  "building_code",
  "badge",
  "shared_account",
  "vendor",
  "service_account",
];

const statuses = ["requested", "approved", "active", "unknown", "removal_pending", "removed"];
const creatableStatuses = ["requested", "approved", "active", "unknown", "removal_pending"];
const categories = ["software", "physical_location", "network", "shared_resource"];
const riskLevels = ["standard", "privileged", "critical"];

const sampleCsv = `employee_id,email,name,account,role,access_type
E-1001,avery.morgan@example.local,Avery Morgan,avery.admin,Administrator,admin
E-1003,priya.shah@example.local,Priya Shah,pshah.user,Standard User,user
,unknown.contractor@example.local,Unknown Contractor,contractor.ext,Administrator,admin`;

const sampleAdCsv = `EmployeeID,Name,Mail,Department,Office,Manager,Enabled,ObjectGUID,UserPrincipalName,SamAccountName,DistinguishedName,LastLogonDate
E-1001,Avery Morgan,avery.morgan@example.local,Operations,HQ,Dana Chen,TRUE,9c61cfa4-4d83-44a3-97ef-1001,avery.morgan@example.local,amorgan,"CN=Avery Morgan,OU=Users,DC=example,DC=local",2026-06-20
E-1005,Taylor Kim,taylor.kim@example.local,Operations,HQ,Dana Chen,TRUE,9c61cfa4-4d83-44a3-97ef-1005,taylor.kim@example.local,tkim,"CN=Taylor Kim,OU=Users,DC=example,DC=local",2026-06-19
E-1006,Rene Carter,rene.carter@example.local,Finance,HQ,Riley Brooks,FALSE,9c61cfa4-4d83-44a3-97ef-1006,rene.carter@example.local,rcarter,"CN=Rene Carter,OU=Disabled,DC=example,DC=local",2026-05-02`;

let started = false;

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startApp, { once: true });
} else {
  startApp();
}

function startApp() {
  if (started) return;
  started = true;
  init();
}

async function init() {
  document.querySelector("#roleSelect").value = state.role;
  document.querySelector("#roleSelect").addEventListener("change", async (event) => {
    state.role = event.target.value;
    localStorage.setItem("access-register-role", state.role);
    renderAll();
  });

  document.querySelector("#refreshButton").addEventListener("click", () => loadAll(true));
  document.querySelectorAll("[data-config-tab]").forEach((button) => {
    button.addEventListener("click", () => setConfigurationTab(button.dataset.configTab));
  });
  document.querySelectorAll("[data-inventory-search]").forEach((input) => {
    input.addEventListener("input", (event) => {
      state.filterText = event.target.value.trim();
      syncInventoryFilters();
      renderAccessTables();
    });
  });
  document.querySelectorAll("[data-status-filter]").forEach((select) => {
    select.addEventListener("change", (event) => {
      state.statusFilter = event.target.value;
      syncInventoryFilters();
      renderAccessTables();
    });
  });
  document.querySelector("#sampleCsvButton").addEventListener("click", () => {
    document.querySelector('#importForm textarea[name="csv_text"]').value = sampleCsv;
  });
  document.querySelector("#sampleAdCsvButton").addEventListener("click", () => {
    document.querySelector('#adSyncForm textarea[name="directory_text"]').value = sampleAdCsv;
    document.querySelector('#adSyncForm select[name="format"]').value = "csv";
  });
  document.querySelector("#evidenceCancelButton").addEventListener("click", closeEvidenceDialog);
  document.querySelector("#evidenceForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitRemovalEvidence(event.target);
  });

  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });

  bindForms();
  hydrateStaticSelects();
  await loadAll(false);
}

function bindForms() {
  document.querySelector("#employeeForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/employees", "Employee created");
  });

  document.querySelector("#systemForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/systems", "System or location created");
  });

  document.querySelector("#resourceCategoryForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/resource-categories", "Business category added");
  });

  document.querySelector("#accessForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/access-records", "Access record created");
  });

  document.querySelector("#importForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formPayload(event.target);
    payload.system_id = Number(payload.system_id);
    try {
      const result = await api("/api/imports/accounts", { method: "POST", body: payload });
      event.target.reset();
      hydrateStaticSelects();
      await loadAll(false);
      showToast(
        `Imported ${result.importRun.total_rows} rows: ${result.importRun.matched_rows} matched, ${result.importRun.unmatched_rows} unmatched.`
      );
    } catch (error) {
      showToast(error.message, true);
    }
  });

  document.querySelector("#adSyncForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = formPayload(event.target);
    try {
      const result = await api("/api/ad/sync", { method: "POST", body: payload });
      await loadAll(false);
      showToast(
        `AD sync complete: ${result.adSyncRun.created_users} created, ${result.adSyncRun.updated_users} updated, ${result.adSyncRun.disabled_users} disabled flagged.`
      );
    } catch (error) {
      showToast(error.message, true);
    }
  });

  document.querySelector("#adScheduleForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveAdSchedule(event.target);
  });

  document.querySelector("#accessRequestForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/access-requests", "Access request submitted");
  });

  document.querySelector("#reviewCampaignForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/review-campaigns", "Review campaign created");
  });

  document.querySelector("#sharedAccountForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/shared-accounts", "Shared account added");
  });

  document.querySelector("#physicalCredentialForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/physical-credentials", "Physical credential added");
  });

  document.querySelector("#connectorForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/connectors", "Connector added");
  });

  document.querySelector("#authSettingsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveAuthSettings(event.target);
  });

  document.querySelector("#configurationAuthForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveAuthSettings(event.target);
  });

  document.querySelector("#configurationAdScheduleForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveAdSchedule(event.target);
  });

  document.querySelector("#configurationEmailForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveEmailSettings(event.target);
  });

  document.querySelector("#configurationConnectorForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitForm(event.target, "/api/connectors", "Connector added");
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const id = Number(button.dataset.id);
    if (action === "goto-view") {
      setView(button.dataset.viewTarget);
      return;
    }
    if (action === "goto-config-tab") {
      setView("configuration");
      setConfigurationTab(button.dataset.configTabTarget || "setup");
      return;
    }
    if (action === "refresh-data") {
      await loadAll(true);
      return;
    }
    if (action === "select-employee") await selectEmployee(id);
    if (action === "certify") await certifyAccess(id);
    if (action === "request-removal") await requestRemoval(id);
    if (action === "mark-removed") openEvidenceDialog(id);
    if (action === "terminate") await terminateEmployee(id);
    if (action === "save-employee-customization") await saveEmployeeCustomization(button.closest("form"));
    if (action === "route-disabled-removals") await routeDisabledRemovals();
    if (action === "approve-request") await decideRequest(id, "approve");
    if (action === "deny-request") await decideRequest(id, "deny");
    if (action === "route-request-email") await routeRequestEmail(id);
    if (action === "update-email-route") await updateEmailRoute(id, button.dataset.status);
    if (action === "complete-campaign") await completeCampaign(id);
    if (action === "ack-notification") await acknowledgeNotification(id);
    if (action === "run-backup") await runBackup(button);
    if (action === "run-scheduled-ad") await runScheduledAd();
  });

  document.addEventListener("submit", async (event) => {
    if (event.target.id !== "employeeEditForm") return;
    event.preventDefault();
    await saveEmployeeCustomization(event.target);
  });
}

async function submitForm(form, path, successMessage) {
  try {
    const payload = formPayload(form);
    for (const key of ["employee_id", "system_id", "review_frequency_days", "frequency_days", "interval_hours"]) {
      if (payload[key] && /^\d+$/.test(String(payload[key]))) payload[key] = Number(payload[key]);
    }
    await api(path, { method: "POST", body: payload });
    form.reset();
    hydrateStaticSelects();
    await loadAll(false);
    showToast(successMessage);
  } catch (error) {
    showToast(error.message, true);
  }
}

function formPayload(form) {
  const data = new FormData(form);
  const payload = {};
  for (const [key, value] of data.entries()) {
    payload[key] = typeof value === "string" ? value.trim() : value;
  }
  return payload;
}

async function loadAll(showSuccess) {
  try {
    const bootstrap = await loadData("/api/bootstrap");
    state.session = bootstrap.session || null;
    if (state.session?.role) {
      state.role = state.session.role;
    }
    state.summary = bootstrap.summary;
    state.employees = bootstrap.employees;
    state.resourceCategories = bootstrap.resourceCategories || [];
    state.systems = bootstrap.systems;
    state.accessRecords = bootstrap.accessRecords;
    state.imports = bootstrap.imports;
    state.adSyncRuns = bootstrap.adSyncRuns;
    state.adSyncSettings = bootstrap.adSyncSettings;
    state.accessRequests = bootstrap.accessRequests;
    state.emailSettings = bootstrap.emailSettings;
    state.emailRoutes = bootstrap.emailRoutes || [];
    state.disabledAccess = bootstrap.disabledAccess;
    state.riskFindings = bootstrap.riskFindings;
    state.notifications = bootstrap.notifications;
    state.reviewCampaigns = bootstrap.reviewCampaigns;
    state.sharedAccounts = bootstrap.sharedAccounts;
    state.physicalCredentials = bootstrap.physicalCredentials;
    state.connectors = bootstrap.connectors;
    state.ownerDashboard = bootstrap.ownerDashboard;
    state.backups = bootstrap.backups;
    state.authSettings = bootstrap.authSettings;
    state.offboarding = bootstrap.offboarding;
    state.audit = bootstrap.audit;
    state.auditIntegrity = bootstrap.auditIntegrity;
    syncRoleControl();

    hydrateDynamicSelects();
    if (!state.selectedEmployeeId && state.accessRecords.length) {
      state.selectedEmployeeId = state.accessRecords[0].employee_id;
    }
    if (state.selectedEmployeeId) {
      await loadSelectedEmployee();
    }
    renderAll();
    if (showSuccess) showToast("Data refreshed");
  } catch (error) {
    showToast(error.message, true);
  }
}

function syncRoleControl() {
  const select = document.querySelector("#roleSelect");
  if (!select) return;
  select.value = state.role;
  const trustedProxy = state.session?.authMode === "trusted_proxy";
  select.disabled = trustedProxy;
  select.title = trustedProxy ? "Role is derived from the authenticated AD account" : "";
}

async function loadData(path) {
  try {
    return await api(path);
  } catch (error) {
    throw new Error(`${path}: ${error.message}`);
  }
}

async function api(path, options = {}) {
  const headers = {
    Accept: "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "X-App-Role": state.role,
    "X-App-Actor": `Local ${state.role}`,
    ...(options.headers || {}),
  };
  const fetchOptions = { method: options.method || "GET", headers };
  if (options.body) {
    fetchOptions.body = JSON.stringify(options.body);
    fetchOptions.headers["Content-Type"] = "application/json";
  }
  let result;
  try {
    const response = await fetchWithTimeout(path, fetchOptions);
    result = {
      ok: response.ok,
      status: response.status,
      body: await response.json().catch(() => ({})),
    };
  } catch (error) {
    result = await xhrJson(path, fetchOptions).catch(() => {
      throw error;
    });
  }
  if (!result.ok) {
    throw new Error(result.body.error || `Request failed with HTTP ${result.status}`);
  }
  return result.body;
}

function fetchWithTimeout(path, options, timeoutMs = 1500) {
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  let timeoutId;
  const timeout = new Promise((_resolve, reject) => {
    timeoutId = window.setTimeout(() => {
      if (controller) controller.abort();
      reject(new Error("Request timed out"));
    }, timeoutMs);
  });
  return Promise.race([
    fetch(path, {
      ...options,
      ...(controller ? { signal: controller.signal } : {}),
    }),
    timeout,
  ]).finally(() => window.clearTimeout(timeoutId));
}

function xhrJson(path, options) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(options.method || "GET", path, true);
    request.timeout = 6000;
    Object.entries(options.headers || {}).forEach(([key, value]) => request.setRequestHeader(key, value));
    request.addEventListener("load", () => {
      let body = {};
      try {
        body = request.responseText ? JSON.parse(request.responseText) : {};
      } catch (_error) {
        body = {};
      }
      resolve({
        ok: request.status >= 200 && request.status < 300,
        status: request.status,
        body,
      });
    });
    request.addEventListener("error", () => reject(new Error("Request failed")));
    request.addEventListener("abort", () => reject(new Error("Request aborted")));
    request.addEventListener("timeout", () => reject(new Error("Request timed out")));
    request.send(options.body || null);
  });
}

function setView(view) {
  state.view = view;
  renderAll();
}

function setConfigurationTab(tab) {
  state.configurationTab = configurationTabs.has(tab) ? tab : "setup";
  localStorage.setItem("gatewatch-configuration-tab", state.configurationTab);
  renderConfigurationTabs();
}

function renderAll() {
  renderViewChrome();
  renderSummary();
  renderPriorityWork();
  renderAccessTables();
  renderDetail();
  renderEmployees();
  renderSystems();
  renderReviews();
  renderAccessRequests();
  renderRiskCenter();
  renderOffboarding();
  renderGovernance();
  renderAssets();
  renderAdSyncRuns();
  renderAdSchedule();
  renderConnectors();
  renderSecurity();
  renderConfiguration();
  renderImports();
  renderAudit();
  applyRoleLocks();
}

function renderViewChrome() {
  const meta = viewMeta[state.view];
  document.querySelector("#viewTitle").textContent = meta.title;
  document.querySelector("#viewSubtitle").textContent = meta.subtitle;
  document.querySelectorAll(".nav-button").forEach((button) => {
    const isActive = button.dataset.view === state.view;
    button.classList.toggle("active", isActive);
    if (isActive) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelector(`#${state.view}View`).classList.add("active");
  renderConfigurationTabs();
}

function renderConfigurationTabs() {
  document.querySelectorAll("[data-config-tab]").forEach((button) => {
    const isActive = button.dataset.configTab === state.configurationTab;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  });
  document.querySelectorAll("[data-config-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.configPanel === state.configurationTab);
  });
}

function renderConfiguration() {
  renderConfigurationChecklist();
  renderConfigurationStatus();
  renderConfigurationAuthSummary();
  renderEmailSettings();
  renderConfigurationLists();
}

function renderConfigurationChecklist() {
  const container = document.querySelector("#configurationChecklist");
  if (!container || !state.summary) return;
  const settings = state.adSyncSettings || {};
  const auth = state.authSettings || {};
  const email = state.emailSettings || {};
  const steps = [
    {
      title: "Authentication",
      detail: auth.provider === "local_role_selector" ? "Local role selector active" : labelize(auth.provider),
      done: Boolean(auth.provider && auth.provider !== "local_role_selector" && auth.login_required),
      tab: "identity",
    },
    {
      title: "AD sync",
      detail: settings.enabled ? `${settings.interval_hours || 24} hour schedule` : "Schedule disabled",
      done: Boolean(settings.enabled && settings.has_directory_payload),
      tab: "data",
    },
    {
      title: "Connectors",
      detail: `${state.connectors.length} connector plan${state.connectors.length === 1 ? "" : "s"}`,
      done: state.connectors.length > 0,
      tab: "data",
    },
    {
      title: "Email notices",
      detail: email.configured ? `${labelize(email.provider)} handoff ready` : "Action recipients missing",
      done: Boolean(email.configured),
      tab: "email",
    },
    {
      title: "Backups",
      detail: state.backups.length ? `Latest ${formatDateTime(state.backups[0].created_at)}` : "No backups run",
      done: state.backups.some((backup) => backup.status === "complete"),
      tab: "evidence",
    },
    {
      title: "Audit trail",
      detail: `${state.audit.length} recent events loaded`,
      done: state.audit.length > 0,
      tab: "evidence",
    },
  ];
  container.innerHTML = `
    <div class="setup-checklist-copy">
      <h2>Setup checklist</h2>
      <p>Finish these items before relying on Gatewatch as the operational access register.</p>
    </div>
    <div class="setup-step-grid">
      ${steps
        .map(
          (step) => `
            <button class="setup-step ${step.done ? "complete" : "attention"}" type="button" data-action="goto-config-tab" data-config-tab-target="${step.tab}">
              <span class="setup-step-state">${step.done ? "Done" : "Set up"}</span>
              <strong>${escapeHtml(step.title)}</strong>
              <small>${escapeHtml(step.detail)}</small>
            </button>
          `
        )
        .join("")}
    </div>
  `;
}

function renderConfigurationStatus() {
  const container = document.querySelector("#configurationStatusList");
  if (!container) return;
  const auth = state.authSettings || {};
  const settings = state.adSyncSettings || {};
  const email = state.emailSettings || {};
  const rows = [
    ["Current role", state.role, "active"],
    ["Auth mode", state.session?.authMode || "local", auth.provider === "local_role_selector" ? "unknown" : "active"],
    ["Directory schedule", settings.enabled ? "Enabled" : "Disabled", settings.enabled ? "active" : "unknown"],
    ["Stored AD payload", settings.has_directory_payload ? "Present" : "Missing", settings.has_directory_payload ? "active" : "removal_pending"],
    ["Email provider", email.provider ? labelize(email.provider) : "Not set", email.configured ? "active" : "unknown"],
    ["Email notices", String(state.emailRoutes.length), state.emailRoutes.length ? "active" : "unknown"],
    ["Connector plans", String(state.connectors.length), state.connectors.length ? "active" : "unknown"],
    ["Backups", String(state.backups.length), state.backups.length ? "active" : "removal_pending"],
  ];
  container.innerHTML = rows
    .map(
      ([label, value, tone]) => `
        <div class="status-row">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <span class="status ${tone}">${escapeHtml(labelize(tone))}</span>
        </div>
      `
    )
    .join("");
}

function renderConfigurationAuthSummary() {
  const container = document.querySelector("#configurationAuthSummary");
  if (!container) return;
  const auth = state.authSettings || {};
  const groupRows = [
    ["Admin", auth.admin_group],
    ["Supervisor", auth.supervisor_group],
    ["Reviewer", auth.reviewer_group],
    ["HR", auth.hr_group],
    ["Read-only", auth.readonly_group],
  ];
  container.innerHTML = `
    <div class="config-summary-card">
      <span class="note-label">Provider</span>
      <strong>${escapeHtml(labelize(auth.provider || "local_role_selector"))}</strong>
      <p>${auth.login_required ? "Real login required once provider is wired." : "Local role selector remains available."}</p>
    </div>
    <div class="settings-list compact-list">
      ${groupRows
        .map(
          ([label, value]) => `
            <div class="settings-row">
              <span>${escapeHtml(label)}</span>
              <code>${escapeHtml(value || "Not mapped")}</code>
            </div>
          `
        )
        .join("")}
    </div>
    <div class="command-box inline-command">
      <div class="note-label">Deployment note</div>
      <code>Use trusted proxy authentication, TLS, and server-side identity before exposing Gatewatch beyond localhost.</code>
    </div>
  `;
}

function renderConfigurationLists() {
  const backups = document.querySelector("#configurationBackupRuns");
  if (backups) {
    backups.innerHTML =
      state.backups
        .map((backup) => backupRunHtml(backup))
        .join("") || `<div class="empty-state"><h2>No backups yet</h2><p>Run a backup before major setup changes.</p></div>`;
  }

  const connectors = document.querySelector("#configurationConnectorsList");
  if (connectors) {
    connectors.innerHTML =
      state.connectors
        .map((connector) => connectorHtml(connector))
        .join("") || `<div class="empty-state"><h2>No connectors planned</h2><p>Add connector plans from Data feeds.</p></div>`;
  }

  const imports = document.querySelector("#configurationImportRuns");
  if (imports) {
    imports.innerHTML =
      state.imports
        .slice(0, 5)
        .map((run) => importRunHtml(run))
        .join("") || `<div class="empty-state"><h2>No imports yet</h2><p>Use CSV imports to discover account gaps.</p></div>`;
  }

  const emailRoutes = document.querySelector("#configurationEmailRoutesList");
  if (emailRoutes) {
    emailRoutes.innerHTML =
      state.emailRoutes
        .slice(0, 12)
        .map((route) => emailRouteHtml(route, { compact: true }))
        .join("") || `<div class="empty-state"><h2>No email notices yet</h2><p>Notify an action owner from a pending request to start the trail.</p></div>`;
  }
}

function backupRunHtml(backup) {
  return `
    <article class="stack-item">
      <div class="detail-header">
        <div>
          <div class="primary-text">${escapeHtml(backup.status === "complete" ? "Backup complete" : "Backup failed")}</div>
          <div class="secondary-text">${escapeHtml(backup.backup_path || "Backup path hidden for this role")} | ${formatDateTime(backup.created_at)}</div>
        </div>
        ${statusChip(backup.status || "unknown")}
      </div>
      <div class="meta-row">
        <span class="type-chip">${backup.size_bytes || 0} bytes</span>
        <span class="type-chip">${backup.retention_days} day retention</span>
      </div>
    </article>
  `;
}

function connectorHtml(connector) {
  return `
    <article class="stack-item">
      <div class="detail-header">
        <div>
          <div class="primary-text">${escapeHtml(connector.name)}</div>
          <div class="secondary-text">${escapeHtml(connector.connector_type)} | Owner ${escapeHtml(connector.owner)}</div>
        </div>
        ${statusChip(connector.status)}
      </div>
      <div class="secondary-text">${escapeHtml(connector.instructions || "No integration notes yet.")}</div>
    </article>
  `;
}

function importRunHtml(run) {
  return `
    <article class="stack-item">
      <div class="primary-text">${escapeHtml(run.source_name)}</div>
      <div class="secondary-text">${escapeHtml(run.system_name)} | ${formatDateTime(run.created_at)}</div>
      <div class="meta-row">
        <span class="type-chip">${run.total_rows} rows</span>
        <span class="status active">${run.matched_rows} matched</span>
        <span class="status removal_pending">${run.inactive_employee_rows} inactive</span>
        <span class="status unknown">${run.unmatched_rows} unmatched</span>
        <span class="type-chip">${run.created_access_records} access records</span>
      </div>
    </article>
  `;
}

function emailRouteHtml(route, options = {}) {
  const nextActions = [
    route.status === "drafted"
      ? `<button class="small-button" data-action="update-email-route" data-id="${route.id}" data-status="sent" type="button" ${reviewDisabled()}>Mark sent</button>`
      : "",
    route.status === "sent"
      ? `<button class="small-button" data-action="update-email-route" data-id="${route.id}" data-status="action_taken" type="button" ${reviewDisabled()}>Mark action</button>`
      : "",
    route.status === "action_taken"
      ? `<button class="small-button" data-action="update-email-route" data-id="${route.id}" data-status="closed" type="button" ${reviewDisabled()}>Close notice</button>`
      : "",
  ].join("");
  return `
    <article class="${options.compact ? "stack-item" : "email-route-card"}">
      <div class="detail-header">
        <div>
          <div class="primary-text">${escapeHtml(route.employee_name || "Access request")} -> ${escapeHtml(route.system_name || "")}</div>
          <div class="secondary-text">${escapeHtml(labelize(route.provider))} | ${escapeHtml(route.recipients)} | ${formatDateTime(route.updated_at)}</div>
        </div>
        ${statusChip(route.status)}
      </div>
      <div class="secondary-text">${escapeHtml(route.subject)}</div>
      <div class="meta-row">
        <a class="small-button link-button" href="${attr(route.compose_url)}" target="_blank" rel="noopener noreferrer">Open ${escapeHtml(labelize(route.provider))}</a>
        ${nextActions}
        <span class="type-chip">Request ${escapeHtml(route.request_status || "pending")}</span>
      </div>
    </article>
  `;
}

function renderSummary() {
  if (!state.summary) return;
  const cards = [
    ["Active access", state.summary.activeAccess, "Current approved, active, or imported access", "neutral"],
    ["Privileged access", state.summary.privilegedAccess, "Admin, critical, and high-risk records", "attention"],
    ["Stale reviews", state.summary.staleReviews, "Access needing owner certification", "warning"],
    ["Removal pending", state.summary.removalsPending, "Open offboarding or review removals", "danger"],
    ["Unmatched imports", state.summary.unmatchedImports, "Accounts not tied to active employees", "warning"],
    ["AD disabled", state.summary.adDisabledUsers, "Directory-disabled users still in the register", "danger"],
    ["Pending requests", state.summary.pendingRequests, "Access requests awaiting decision", "attention"],
    ["Risk findings", state.summary.riskFindings, "Open issues across access governance", "danger"],
    ["Expiring access", state.summary.expiringAccess, "Temporary access expiring soon", "warning"],
    ["Notifications", state.summary.pendingNotifications, "Pending reminders or escalations", "attention"],
  ];
  document.querySelector("#summaryCards").innerHTML = cards
    .map(
      ([label, value, note, tone]) => `
        <article class="kpi ${tone}">
          <div class="kpi-label">${escapeHtml(label)}</div>
          <div class="kpi-value">${value}</div>
          <div class="kpi-note">${escapeHtml(note)}</div>
        </article>
      `
    )
    .join("");
}

function renderPriorityWork() {
  const container = document.querySelector("#priorityWork");
  if (!container || !state.summary) return;
  const items = [
    {
      label: "Removals",
      value: state.summary.removalsPending,
      note: "Need evidence",
      view: "offboarding",
      tone: "danger",
    },
    {
      label: "Reviews",
      value: state.summary.staleReviews,
      note: "Need certification",
      view: "reviews",
      tone: "warning",
    },
    {
      label: "Requests",
      value: state.summary.pendingRequests,
      note: "Await decision",
      view: "requests",
      tone: "attention",
    },
    {
      label: "Import gaps",
      value: state.summary.unmatchedImports,
      note: "Unmatched accounts",
      view: "imports",
      tone: "warning",
    },
  ];
  container.innerHTML = `
    <div class="action-strip-copy">
      <div class="eyebrow">Today</div>
      <h2>Priority work</h2>
      <p>Jump straight to the queues that usually need owner or admin action.</p>
    </div>
    <div class="action-cards">
      ${items
        .map(
          (item) => `
            <button class="action-card ${item.tone}" type="button" data-view-jump="${item.view}">
              <span class="action-value">${item.value}</span>
              <span>
                <strong>${escapeHtml(item.label)}</strong>
                <small>${escapeHtml(item.note)}</small>
              </span>
            </button>
          `
        )
        .join("")}
    </div>
  `;
  container.querySelectorAll("[data-view-jump]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.viewJump));
  });
}

function renderAccessTables() {
  syncInventoryFilters();
  const filtered = filteredRecords();
  const countLabel = `${filtered.length} ${filtered.length === 1 ? "record" : "records"}`;
  const dashboardCount = document.querySelector("#dashboardInventoryCount");
  const inventoryCount = document.querySelector("#inventoryRecordCount");
  if (dashboardCount) dashboardCount.textContent = countLabel;
  if (inventoryCount) inventoryCount.textContent = countLabel;
  document.querySelector("#accessTable").innerHTML = filtered.slice(0, 60).map(dashboardRecordRow).join("");
  document.querySelector("#inventoryTable").innerHTML = filtered.map(inventoryRecordRow).join("");
}

function filteredRecords() {
  const filterText = state.filterText.toLowerCase();
  return state.accessRecords.filter((record) => {
    if (state.statusFilter && record.status !== state.statusFilter) return false;
    if (!filterText) return true;
    return [
      record.employee_name,
      record.employee_email,
      record.employee_identifier,
      record.system_name,
      record.access_level,
      record.owner,
      record.department,
      record.location,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(filterText);
  });
}

function syncInventoryFilters() {
  document.querySelectorAll("[data-inventory-search]").forEach((input) => {
    if (document.activeElement !== input) input.value = state.filterText;
  });
  document.querySelectorAll("[data-status-filter]").forEach((select) => {
    select.value = state.statusFilter;
  });
}

function dashboardRecordRow(record) {
  return `
    <tr>
      <td class="cell-compact">
        <div class="primary-text">${escapeHtml(record.employee_name)}</div>
        <div class="secondary-text">${escapeHtml(record.employee_identifier)} | ${escapeHtml(record.department)}</div>
      </td>
      <td class="cell-compact">
        <div class="primary-text">${escapeHtml(record.system_name)}</div>
        <div class="secondary-text">${labelize(record.system_category)} | ${riskChip(record.risk_level)}</div>
      </td>
      <td class="cell-compact">
        <div class="primary-text">${escapeHtml(record.access_level)}</div>
        <div class="secondary-text">${typeChip(record.access_type)}</div>
      </td>
      <td>${statusChip(record.status)}${record.is_stale ? " " + staleChip() : ""}</td>
      <td class="cell-nowrap">${escapeHtml(record.owner)}</td>
      <td class="cell-nowrap">${formatDate(record.last_reviewed_at)}</td>
      <td class="actions-cell"><button class="small-button" data-action="select-employee" data-id="${record.employee_id}" type="button" aria-label="Inspect ${attr(record.employee_name)}">Inspect</button></td>
    </tr>
  `;
}

function inventoryRecordRow(record) {
  return `
    <tr>
      <td class="cell-compact">
        <div class="primary-text">${escapeHtml(record.employee_name)}</div>
        <div class="secondary-text">${escapeHtml(record.employee_email)}</div>
      </td>
      <td class="cell-compact">
        <div class="primary-text">${escapeHtml(record.system_name)}</div>
        <div class="secondary-text">${escapeHtml(record.owner)}</div>
      </td>
      <td>${typeChip(record.access_type)}</td>
      <td class="cell-compact">${escapeHtml(record.access_level)}</td>
      <td>${statusChip(record.status)}${record.is_stale ? " " + staleChip() : ""}</td>
      <td>${riskChip(record.risk_level)}</td>
      <td class="actions-cell">
        <div class="button-row row-actions">
          <button class="small-button" data-action="certify" data-id="${record.id}" type="button" ${reviewDisabled()} aria-label="Certify ${attr(record.employee_name)} ${attr(record.system_name)}">Certify</button>
          <button class="small-button" data-action="request-removal" data-id="${record.id}" type="button" ${reviewDisabled()} aria-label="Route ${attr(record.employee_name)} ${attr(record.system_name)} to removal">Remove</button>
          <button class="danger-button small-button" data-action="mark-removed" data-id="${record.id}" type="button" ${updateDisabled()} aria-label="Add removal evidence for ${attr(record.employee_name)} ${attr(record.system_name)}">Evidence</button>
        </div>
      </td>
    </tr>
  `;
}

async function selectEmployee(employeeId) {
  state.selectedEmployeeId = employeeId;
  await loadSelectedEmployee();
  if (state.view !== "dashboard") {
    state.view = "dashboard";
    renderAll();
    return;
  }
  renderDetail();
}

async function loadSelectedEmployee() {
  try {
    state.selectedEmployee = await api(`/api/employees/${state.selectedEmployeeId}`);
  } catch (_error) {
    state.selectedEmployeeId = null;
    state.selectedEmployee = null;
  }
}

function renderDetail() {
  const target = document.querySelector("#employeeDetail");
  if (!state.selectedEmployee) {
    target.innerHTML = `
      <div class="empty-state">
        <h2>Select an employee</h2>
        <p>Choose a row in the access inventory to inspect current access and removal tasks.</p>
      </div>
    `;
    return;
  }

  const { employee, access } = state.selectedEmployee;
  const openAccess = access.filter((record) => ["active", "approved", "unknown"].includes(record.status));
  const openRemovals = access.filter((record) =>
    ["active", "approved", "unknown", "removal_pending"].includes(record.status)
  );
  target.innerHTML = `
    <div class="detail-body">
      <div class="detail-hero">
        <div class="employee-avatar" aria-hidden="true">${escapeHtml(initials(employee.name))}</div>
        <div class="detail-identity">
          <h2>${escapeHtml(employee.name)}</h2>
          <div class="secondary-text">${escapeHtml(employee.employee_id)} / ${escapeHtml(employee.department)} / ${escapeHtml(employee.location)}</div>
        </div>
        <div class="detail-status">${statusChip(employee.status)}</div>
      </div>
      <div class="detail-stats" aria-label="Employee access summary">
        <span><strong>${access.length}</strong><small>Total access</small></span>
        <span><strong>${openAccess.length}</strong><small>Open access</small></span>
        <span><strong>${openRemovals.length}</strong><small>Removal scope</small></span>
      </div>
      <div class="button-row detail-actions">
        <button class="small-button" data-action="terminate" data-id="${employee.id}" type="button" ${terminateDisabled(employee)}>Mark terminated</button>
      </div>
      <div class="meta-row detail-meta">
        ${directoryChip(employee)}
        ${employee.admin_override ? '<span class="status unknown">Manual override</span>' : ""}
        ${employee.ad_last_sync_at ? `<span class="type-chip">AD sync ${formatDate(employee.ad_last_sync_at)}</span>` : ""}
      </div>
      ${employeeEditForm(employee)}
      <section class="detail-section">
        <h3>Current Access</h3>
        <div class="access-list">
          ${
            access.length
              ? access.map(detailAccessItem).join("")
              : '<div class="secondary-text">No access records for this employee.</div>'
          }
        </div>
      </section>
      <section class="detail-section">
        <h3>Offboarding Checklist</h3>
        <div class="access-list">
          ${
            openRemovals.length
              ? openRemovals.map(offboardingAccessItem).join("")
              : '<div class="secondary-text">No open access removals.</div>'
          }
        </div>
      </section>
    </div>
  `;
}

function employeeEditForm(employee) {
  return `
    <section class="detail-section detail-form-section">
      <div class="section-heading">
        <h3>Admin Customization</h3>
        <p>Use manual override when directory data should not replace local HR details.</p>
      </div>
      <form id="employeeEditForm" class="detail-form" onsubmit="return false">
        <input type="hidden" name="id" value="${employee.id}" />
        <label class="field"><span>Name</span><input name="name" value="${attr(employee.name)}" required /></label>
        <label class="field"><span>Email</span><input name="email" type="email" value="${attr(employee.email)}" required /></label>
        <label class="field"><span>Department</span><input name="department" value="${attr(employee.department)}" required /></label>
        <label class="field"><span>Location</span><input name="location" value="${attr(employee.location)}" required /></label>
        <label class="field"><span>Manager</span><input name="manager" value="${attr(employee.manager || "")}" /></label>
        <label class="checkbox-field">
          <input name="admin_override" type="checkbox" value="true" ${employee.admin_override ? "checked" : ""} />
          <span>Protect these manual details from AD sync</span>
        </label>
        <label class="field"><span>Admin notes</span><textarea name="admin_notes">${escapeHtml(employee.admin_notes || "")}</textarea></label>
        <div class="form-actions">
          <button class="primary-button" type="button" data-action="save-employee-customization" ${state.role === "Admin" ? "" : "disabled"}>Save customization</button>
        </div>
      </form>
    </section>
  `;
}

function detailAccessItem(record) {
  return `
    <article class="access-item ${record.status === "removal_pending" ? "warning" : ""}">
      <div class="primary-text">${escapeHtml(record.system_name)}</div>
      <div class="secondary-text">${escapeHtml(record.access_level)} | ${labelize(record.access_type)}</div>
      <div class="meta-row">
        ${statusChip(record.status)}
        ${riskChip(record.risk_level)}
        ${record.is_stale ? staleChip() : ""}
      </div>
    </article>
  `;
}

function offboardingAccessItem(record) {
  return `
    <article class="access-item ${record.status === "removal_pending" ? "warning" : ""}">
      <div class="primary-text">${escapeHtml(record.system_name)}</div>
      <div class="secondary-text">Owner: ${escapeHtml(record.owner)} | Due: ${formatDate(record.removal_due_at)}</div>
      <div class="meta-row">
        ${statusChip(record.status)}
        <button class="danger-button small-button" data-action="mark-removed" data-id="${record.id}" type="button" ${updateDisabled()}>Mark removed</button>
      </div>
    </article>
  `;
}

function renderEmployees() {
  document.querySelector("#employeeTable").innerHTML = state.employees
    .map(
      (employee) => `
        <tr>
            <td>
              <div class="primary-text">${escapeHtml(employee.name)}</div>
              <div class="secondary-text">${escapeHtml(employee.employee_id)} | ${escapeHtml(employee.email)}</div>
            </td>
          <td>${escapeHtml(employee.department)}</td>
          <td>${escapeHtml(employee.location)}</td>
          <td>${statusChip(employee.status)}</td>
          <td>${directoryChip(employee)}</td>
          <td>${employee.access_count || 0}</td>
            <td class="actions-cell">
              <div class="button-row row-actions">
                <button class="small-button" data-action="select-employee" data-id="${employee.id}" type="button" aria-label="Inspect ${attr(employee.name)}">Inspect</button>
                <button class="small-button" data-action="terminate" data-id="${employee.id}" type="button" ${terminateDisabled(employee)} aria-label="Terminate ${attr(employee.name)}">Terminate</button>
              </div>
            </td>
          </tr>
      `
    )
    .join("");
}

function renderAccessRequests() {
  const container = document.querySelector("#accessRequestsList");
  if (!container) return;
  if (!state.accessRequests.length) {
    container.innerHTML = `<div class="empty-state"><h2>No requests yet</h2><p>Submit a request to replace the old PDF workflow.</p></div>`;
    renderRequestEmailRoutes();
    return;
  }
  container.innerHTML = state.accessRequests
    .map(
      (request) => `
        <article class="stack-item">
          <div class="detail-header">
            <div>
              <div class="primary-text">${escapeHtml(request.employee_name)} -> ${escapeHtml(request.system_name)}</div>
              <div class="secondary-text">${escapeHtml(request.access_level)} | ${labelize(request.access_type)} | Requested by ${escapeHtml(request.requester)}</div>
            </div>
            ${statusChip(request.status)}
          </div>
          <div class="secondary-text">${escapeHtml(request.business_reason)}</div>
          <div class="meta-row">
            ${request.expiration_date ? `<span class="type-chip">Expires ${formatDate(request.expiration_date)}</span>` : ""}
            <span class="type-chip">Owner ${escapeHtml(request.system_owner)}</span>
            <button class="small-button" data-action="approve-request" data-id="${request.id}" type="button" ${reviewDisabled()} ${request.status !== "pending" ? "disabled" : ""} aria-label="Approve request for ${attr(request.employee_name)}">Approve</button>
            <button class="danger-button small-button" data-action="deny-request" data-id="${request.id}" type="button" ${reviewDisabled()} ${request.status !== "pending" ? "disabled" : ""} aria-label="Deny request for ${attr(request.employee_name)}">Deny</button>
          </div>
          ${emailRouteControls(request)}
        </article>
      `
    )
    .join("");
  renderRequestEmailRoutes();
}

function renderRequestEmailRoutes() {
  const container = document.querySelector("#requestEmailRoutesList");
  if (!container) return;
  container.innerHTML =
    state.emailRoutes
      .slice(0, 8)
      .map((route) => emailRouteHtml(route, { compact: true }))
      .join("") || `<div class="empty-state"><h2>No email notices</h2><p>Configured Outlook and Gmail action notices will appear here.</p></div>`;
}

function emailRouteControls(request) {
  const routes = routesForRequest(request.id);
  const latest = routes[0];
  if (!latest) {
    const disabled = emailRouteDisabled(request);
    return `
      <div class="email-route-card">
        <div>
          <div class="primary-text">Email notice</div>
          <div class="secondary-text">${state.emailSettings?.configured ? `Ready for ${escapeHtml(labelize(state.emailSettings.provider))}` : "Configure action recipients first"}</div>
        </div>
        <button class="small-button" data-action="route-request-email" data-id="${request.id}" type="button" ${disabled}>Notify</button>
      </div>
    `;
  }
  return emailRouteHtml(latest, { compact: false });
}

function routesForRequest(requestId) {
  return state.emailRoutes
    .filter((route) => route.request_id === requestId)
    .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
}

function emailRouteDisabled(request) {
  if (!["Admin", "Supervisor", "Reviewer"].includes(state.role)) return "disabled";
  if (request.status !== "pending") return "disabled";
  if (!state.emailSettings?.configured) return "disabled";
  return "";
}

function renderRiskCenter() {
  const disabled = document.querySelector("#disabledAccessQueue");
  if (disabled) {
    disabled.innerHTML =
      state.disabledAccess
        .map(
          (item) => `
            <article class="stack-item warning">
              <div class="primary-text">${escapeHtml(item.employee_name)} still has ${escapeHtml(item.system_name)}</div>
              <div class="secondary-text">${escapeHtml(item.access_level)} | ${labelize(item.access_type)} | Owner ${escapeHtml(item.system_owner)}</div>
              <div class="meta-row">${statusChip(item.status)} ${riskChip(item.risk_level)} <span class="type-chip">AD disabled ${formatDate(item.ad_disabled_flagged_at)}</span></div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No disabled-user access</h2><p>AD-disabled users with active access will appear here.</p></div>`;
  }

  const risk = document.querySelector("#riskFindings");
  if (risk) {
    risk.innerHTML =
      state.riskFindings
        .map(
          (finding) => `
            <article class="stack-item">
              <div class="detail-header">
                <div>
                  <div class="primary-text">${escapeHtml(finding.title)}</div>
                  <div class="secondary-text">${escapeHtml(finding.subject)} | ${escapeHtml(finding.target)}</div>
                </div>
                <span class="risk ${escapeHtml(finding.severity)}">${escapeHtml(labelize(finding.severity))}</span>
              </div>
              <div class="secondary-text">${escapeHtml(finding.recommendation)}</div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No risk findings</h2><p>Expired access, overdue removals, and shared-account gaps will appear here.</p></div>`;
  }

  const notifications = document.querySelector("#notificationsList");
  if (notifications) {
    notifications.innerHTML =
      state.notifications
        .map(
          (note) => `
            <article class="stack-item">
              <div class="detail-header">
                <div>
                  <div class="primary-text">${escapeHtml(note.subject)}</div>
                  <div class="secondary-text">${escapeHtml(note.recipient)} | ${formatDateTime(note.created_at)}</div>
                </div>
                <span class="risk ${escapeHtml(note.severity)}">${escapeHtml(labelize(note.severity))}</span>
              </div>
              <div class="secondary-text">${escapeHtml(note.body)}</div>
              <div class="meta-row">
                ${statusChip(note.status)}
                <button class="small-button" data-action="ack-notification" data-id="${note.id}" type="button" ${notificationDisabled()} ${note.status !== "pending" ? "disabled" : ""}>Acknowledge</button>
              </div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No notifications</h2><p>Escalations and reminders will appear here.</p></div>`;
  }
}

function renderGovernance() {
  const campaigns = document.querySelector("#reviewCampaignsList");
  if (campaigns) {
    campaigns.innerHTML =
      state.reviewCampaigns
        .map(
          (campaign) => `
            <article class="stack-item">
              <div class="detail-header">
                <div>
                  <div class="primary-text">${escapeHtml(campaign.name)}</div>
                  <div class="secondary-text">${escapeHtml(campaign.system_name || "All systems")} | Owner ${escapeHtml(campaign.owner)}</div>
                </div>
                ${statusChip(campaign.status)}
              </div>
              <div class="meta-row">
                <span class="type-chip">Due ${formatDate(campaign.due_date)}</span>
                <span class="type-chip">Every ${campaign.frequency_days} days</span>
                <button class="small-button" data-action="complete-campaign" data-id="${campaign.id}" type="button" ${reviewDisabled()} ${campaign.status === "complete" ? "disabled" : ""} aria-label="Complete ${attr(campaign.name)}">Complete</button>
              </div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No review campaigns</h2><p>Create recurring reviews for owners.</p></div>`;
  }

  const owner = document.querySelector("#ownerDashboard");
  if (owner) {
    const systems = state.ownerDashboard?.systems || [];
    owner.innerHTML =
      systems
        .map(
          (system) => `
            <article class="stack-item">
              <div class="primary-text">${escapeHtml(system.name)}</div>
              <div class="secondary-text">Owner ${escapeHtml(system.owner)} | ${labelize(system.category)}</div>
              <div class="meta-row">
                <span class="type-chip">${system.access_count || 0} access records</span>
                <span class="status removal_pending">${system.removals_pending || 0} removals</span>
                <span class="status unknown">${system.review_due || 0} reviews due</span>
              </div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No owner data</h2><p>Add systems and access records to populate owner accountability.</p></div>`;
  }

  const backups = document.querySelector("#backupRuns");
  if (backups) {
    backups.innerHTML =
      state.backups
        .map((backup) => backupRunHtml(backup))
        .join("") || `<div class="empty-state"><h2>No backups yet</h2><p>Run a backup before using the app as a source of truth.</p></div>`;
  }
}

function renderAssets() {
  const shared = document.querySelector("#sharedAccountsList");
  if (shared) {
    shared.innerHTML =
      state.sharedAccounts
        .map(
          (account) => `
            <article class="stack-item">
              <div class="primary-text">${escapeHtml(account.account_name)} | ${escapeHtml(account.system_name)}</div>
              <div class="secondary-text">Owner ${escapeHtml(account.owner)} | Users ${escapeHtml(account.approved_users || "Not listed")}</div>
              <div class="meta-row">
                ${statusChip(account.status)}
                <span class="${account.mfa_enabled ? "status active" : "status terminated"}">${account.mfa_enabled ? "MFA documented" : "MFA missing"}</span>
                <span class="type-chip">Rotation ${formatDate(account.rotation_due_at)}</span>
              </div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No shared accounts</h2><p>Add break-glass or shared credentials that need rotation evidence.</p></div>`;
  }

  const physical = document.querySelector("#physicalCredentialsList");
  if (physical) {
    physical.innerHTML =
      state.physicalCredentials
        .map(
          (credential) => `
            <article class="stack-item">
              <div class="primary-text">${escapeHtml(credential.employee_name)} | ${labelize(credential.credential_type)}</div>
              <div class="secondary-text">${escapeHtml(credential.location)} | ${escapeHtml(credential.credential_identifier || "No ID")} | ${escapeHtml(credential.zone || "No zone")}</div>
              <div class="meta-row">${statusChip(credential.status)} <span class="type-chip">Due ${formatDate(credential.due_at)}</span></div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No physical credentials</h2><p>Add badges, keys, codes, or fobs to track return and rotation evidence.</p></div>`;
  }
}

function renderAdSyncRuns() {
  const container = document.querySelector("#adSyncRuns");
  if (!container) return;
  if (!state.adSyncRuns.length) {
    container.innerHTML = `<div class="empty-state"><h2>No AD syncs yet</h2><p>Paste an AD export to create and update employee records.</p></div>`;
    return;
  }
  container.innerHTML = state.adSyncRuns
    .map(
      (run) => `
        <article class="stack-item">
          <div class="primary-text">${escapeHtml(run.source_name)}</div>
          <div class="secondary-text">${formatDateTime(run.created_at)} | ${escapeHtml(run.format.toUpperCase())}</div>
          <div class="meta-row">
            <span class="type-chip">${run.total_rows} rows</span>
            <span class="status active">${run.created_users} created</span>
            <span class="status unknown">${run.updated_users} updated</span>
            <span class="status removal_pending">${run.disabled_users} disabled</span>
            <span class="type-chip">${run.preserved_overrides} preserved overrides</span>
            ${run.error_rows ? `<span class="status terminated">${run.error_rows} errors</span>` : ""}
          </div>
        </article>
      `
    )
    .join("");
}

function renderAdSchedule() {
  if (!state.adSyncSettings) return;
  document.querySelectorAll("#adScheduleForm, #configurationAdScheduleForm").forEach(renderAdScheduleForm);
}

function renderAdScheduleForm(form) {
  form.querySelector('input[name="enabled"]').checked = Boolean(state.adSyncSettings.enabled);
  const sourceName = form.querySelector('input[name="source_name"]');
  if (sourceName) sourceName.value = state.adSyncSettings.source_name || "Scheduled Active Directory sync";
  const format = form.querySelector('select[name="format"]');
  if (format) format.value = state.adSyncSettings.format || "csv";
  form.querySelector('input[name="interval_hours"]').value = state.adSyncSettings.interval_hours || 24;
  form.querySelector('input[name="next_run_at"]').value = state.adSyncSettings.next_run_at || "";
  const payload = form.querySelector('textarea[name="directory_text"]');
  if (state.role !== "Admin") {
    payload.value = "";
    return;
  }
  if (!payload.dataset.userTouched) {
    payload.value = state.adSyncSettings.directory_text || sampleAdCsv;
  }
  if (!payload.dataset.listenerAttached) {
    payload.addEventListener("input", () => {
      payload.dataset.userTouched = "true";
    });
    payload.dataset.listenerAttached = "true";
  }
}

function renderConnectors() {
  const container = document.querySelector("#connectorsList");
  if (!container) return;
  container.innerHTML =
    state.connectors
      .map((connector) => connectorHtml(connector))
      .join("") || `<div class="empty-state"><h2>No connectors planned</h2><p>Add systems that should eventually move from CSV import to direct integration.</p></div>`;
}

function renderSecurity() {
  if (!state.authSettings) return;
  document.querySelectorAll("#authSettingsForm, #configurationAuthForm").forEach(renderAuthSettingsForm);
}

function renderAuthSettingsForm(form) {
  form.querySelector('select[name="provider"]').value = state.authSettings.provider || "local_role_selector";
  form.querySelector('input[name="login_required"]').checked = Boolean(state.authSettings.login_required);
  for (const key of ["admin_group", "supervisor_group", "reviewer_group", "hr_group", "readonly_group"]) {
    form.querySelector(`[name="${key}"]`).value = state.authSettings[key] || "";
  }
  form.querySelector('textarea[name="notes"]').value = state.authSettings.notes || "";
}

function renderEmailSettings() {
  const form = document.querySelector("#configurationEmailForm");
  if (!form || !state.emailSettings) return;
  form.querySelector('select[name="provider"]').value = state.emailSettings.provider || "outlook";
  form.querySelector('input[name="default_recipients"]').value = state.emailSettings.default_recipients || "";
  form.querySelector('input[name="cc_recipients"]').value = state.emailSettings.cc_recipients || "";
  form.querySelector('input[name="sender_label"]').value = state.emailSettings.sender_label || "Gatewatch";
  form.querySelector('input[name="subject_prefix"]').value = state.emailSettings.subject_prefix || "Gatewatch action needed";
  form.querySelector('textarea[name="instructions"]').value = state.emailSettings.instructions || "";

  const summary = document.querySelector("#configurationEmailSummary");
  if (summary) {
    summary.innerHTML = `
      <div class="config-summary-card">
        <span class="note-label">Provider</span>
        <strong>${escapeHtml(labelize(state.emailSettings.provider || "outlook"))}</strong>
        <p>${state.emailSettings.configured ? "Action recipients are configured for pending-request notices." : "Add action recipients before sending Outlook or Gmail notices."}</p>
      </div>
      <div class="settings-list compact-list">
        <div class="settings-row">
          <span>To</span>
          <code>${escapeHtml(state.emailSettings.default_recipients || "Not configured")}</code>
        </div>
        <div class="settings-row">
          <span>CC</span>
          <code>${escapeHtml(state.emailSettings.cc_recipients || "None")}</code>
        </div>
        <div class="settings-row">
          <span>Subject</span>
          <code>${escapeHtml(state.emailSettings.subject_prefix || "Gatewatch action needed")}</code>
        </div>
      </div>
    `;
  }
}

function renderSystems() {
  document.querySelector("#systemTable").innerHTML = state.systems
    .map(
      (system) => `
        <tr>
          <td>
            <div class="primary-text">${escapeHtml(system.name)}</div>
            <div class="secondary-text">${escapeHtml(system.description || "No description")}</div>
          </td>
          <td>${labelize(system.category)}</td>
          <td>${escapeHtml(system.resource_category_name || "Uncategorized")}</td>
          <td>${escapeHtml(system.product_name || system.name)}</td>
          <td>${systemUrlList(system)}</td>
          <td>${escapeHtml(system.owner)}</td>
          <td>${riskChip(system.risk_level)}</td>
          <td>${system.review_frequency_days} days</td>
          <td>${system.access_count || 0}</td>
        </tr>
      `
    )
    .join("");

  const categoryList = document.querySelector("#resourceCategoriesList");
  if (categoryList) {
    categoryList.innerHTML =
      state.resourceCategories
        .map(
          (category) => `
            <article class="stack-item">
              <div class="detail-header">
                <div>
                  <div class="primary-text">${escapeHtml(category.name)}</div>
                  <div class="secondary-text">${escapeHtml(category.description || "No description")}</div>
                </div>
                ${riskChip(category.default_risk_level)}
              </div>
              <div class="meta-row"><span class="type-chip">${category.system_count || 0} resources</span></div>
            </article>
          `
        )
        .join("") || `<div class="empty-state"><h2>No business categories</h2><p>Add a category before cataloging resources.</p></div>`;
  }
}

function renderReviews() {
  const reviewRecords = state.accessRecords.filter((record) => record.is_stale || record.status === "unknown");
  document.querySelector("#reviewTable").innerHTML =
    reviewRecords
      .map(
        (record) => `
          <tr>
            <td>
              <div class="primary-text">${escapeHtml(record.employee_name)}</div>
              <div class="secondary-text">${escapeHtml(record.department)}</div>
            </td>
            <td>${escapeHtml(record.system_name)}</td>
            <td>
              <div class="primary-text">${escapeHtml(record.access_level)}</div>
              <div class="secondary-text">${typeChip(record.access_type)}</div>
            </td>
            <td>${escapeHtml(record.owner)}</td>
            <td>${formatDate(record.last_reviewed_at)} ${record.is_stale ? staleChip() : ""}</td>
            <td class="actions-cell">
              <div class="button-row row-actions">
                <button class="small-button" data-action="certify" data-id="${record.id}" type="button" ${reviewDisabled()} aria-label="Certify ${attr(record.employee_name)} ${attr(record.system_name)}">Certify</button>
                <button class="danger-button small-button" data-action="request-removal" data-id="${record.id}" type="button" ${reviewDisabled()} aria-label="Route ${attr(record.employee_name)} ${attr(record.system_name)} to removal">Route</button>
              </div>
            </td>
          </tr>
        `
      )
      .join("") || `<tr><td colspan="6">No stale or unknown access records.</td></tr>`;
}

function renderOffboarding() {
  const container = document.querySelector("#offboardingList");
  if (!state.offboarding.length) {
    container.innerHTML = `<div class="empty-state"><h2>No terminated employees</h2><p>Offboarding work appears here as employees are marked terminated.</p></div>`;
    return;
  }
  container.innerHTML = state.offboarding
    .map((employee) => {
      const warning = Number(employee.open_removals || 0) > 0;
      return `
        <article class="offboarding-card ${warning ? "warning" : ""}">
          <div class="detail-header">
            <div>
              <div class="primary-text">${escapeHtml(employee.name)}</div>
              <div class="secondary-text">${escapeHtml(employee.employee_id)} | ${escapeHtml(employee.department)} | Terminated ${formatDate(employee.termination_date)}</div>
            </div>
            ${warning ? statusChip("removal_pending") : statusChip("removed")}
          </div>
          <div class="meta-row">
            <span class="type-chip">${employee.open_removals || 0} open removals</span>
            <span class="type-chip">${employee.completed_removals || 0} completed</span>
            <button class="small-button" data-action="select-employee" data-id="${employee.id}" type="button" aria-label="Open offboarding checklist for ${attr(employee.name)}">Open checklist</button>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderImports() {
  const container = document.querySelector("#importRuns");
  if (!state.imports.length) {
    container.innerHTML = `<div class="empty-state"><h2>No imports yet</h2><p>Paste a CSV account export to create the first import run.</p></div>`;
    return;
  }
  container.innerHTML = state.imports
    .map((run) => importRunHtml(run))
    .join("");
}

function renderAudit() {
  renderAuditIntegrity();
  document.querySelector("#auditTable").innerHTML = state.audit
    .map(
      (entry) => `
        <tr>
          <td>${formatDateTime(entry.created_at)}</td>
          <td>${escapeHtml(entry.actor)}</td>
          <td>${escapeHtml(entry.role)}</td>
          <td><span class="audit-action">${escapeHtml(entry.action.replaceAll("_", " "))}</span></td>
          <td>${escapeHtml(entry.entity_type)} #${entry.entity_id ?? ""}</td>
          <td>${escapeHtml(entry.summary)}</td>
        </tr>
      `
    )
    .join("");
}

function renderAuditIntegrity() {
  const container = document.querySelector("#auditIntegrity");
  if (!container) return;
  const integrity = state.auditIntegrity;
  if (!integrity) {
    container.innerHTML = `
      <div class="integrity-card muted">
        <span class="status unknown">Not available</span>
        <div>
          <strong>Audit verification is hidden for this role.</strong>
          <p>Privileged roles can verify the audit evidence chain.</p>
        </div>
      </div>
    `;
    return;
  }
  const tone = integrity.valid ? "complete" : "failed";
  const hashPreview = integrity.latest_hash ? integrity.latest_hash.slice(0, 12) : "No entries";
  container.innerHTML = `
    <div class="integrity-card ${escapeHtml(tone)}">
      <span class="status ${escapeHtml(tone)}">${integrity.valid ? "Verified" : "Broken"}</span>
      <div>
        <strong>${integrity.valid ? "Audit trail verifies" : "Audit trail needs review"}</strong>
        <p>${escapeHtml(String(integrity.checked_entries))} entries checked / Latest hash ${escapeHtml(hashPreview)}</p>
      </div>
    </div>
  `;
}

async function certifyAccess(recordId) {
  try {
    await api(`/api/access-records/${recordId}/review`, {
      method: "POST",
      body: { decision: "certified", notes: "Access certified from Gatewatch." },
    });
    await loadAll(false);
    showToast("Access certified");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function requestRemoval(recordId) {
  try {
    await api(`/api/access-records/${recordId}/review`, {
      method: "POST",
      body: { decision: "remove", notes: "Reviewer routed access to removal." },
    });
    await loadAll(false);
    showToast("Access routed to removal");
  } catch (error) {
    showToast(error.message, true);
  }
}

function openEvidenceDialog(recordId) {
  state.pendingRemovalRecordId = recordId;
  const dialog = document.querySelector("#evidenceDialog");
  const form = document.querySelector("#evidenceForm");
  form.reset();
  dialog.hidden = false;
  form.querySelector("textarea").focus();
}

function closeEvidenceDialog() {
  state.pendingRemovalRecordId = null;
  document.querySelector("#evidenceDialog").hidden = true;
}

async function submitRemovalEvidence(form) {
  const evidence = formPayload(form).evidence;
  if (!evidence) {
    showToast("Removal evidence is required", true);
    return;
  }
  try {
    await api(`/api/access-records/${state.pendingRemovalRecordId}`, {
      method: "PATCH",
      body: { status: "removed", removal_evidence: evidence },
    });
    closeEvidenceDialog();
    await loadAll(false);
    showToast("Removal evidence saved");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function terminateEmployee(employeeId) {
  const employee = state.employees.find((item) => item.id === employeeId);
  if (!employee || employee.status === "terminated") return;
  const confirmed = window.confirm(`Mark ${employee.name} terminated and route active access to removal?`);
  if (!confirmed) return;
  try {
    await api(`/api/employees/${employeeId}`, {
      method: "PATCH",
      body: { status: "terminated" },
    });
    await loadAll(false);
    showToast("Employee marked terminated and access routed to removal");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveEmployeeCustomization(form) {
  const payload = formPayload(form);
  const employeeId = Number(payload.id);
  delete payload.id;
  payload.admin_override = form.querySelector('input[name="admin_override"]').checked;
  try {
    await api(`/api/employees/${employeeId}`, {
      method: "PATCH",
      body: payload,
    });
    await loadAll(false);
    showToast("Employee customization saved");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function routeDisabledRemovals() {
  try {
    const result = await api("/api/disabled-access/route-removal", { method: "POST", body: {} });
    await loadAll(false);
    showToast(`Routed ${result.result.routed} disabled-user access records to removal`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function decideRequest(requestId, decision) {
  try {
    const notes = decision === "approve" ? "Approved in Gatewatch." : "Denied in Gatewatch.";
    await api(`/api/access-requests/${requestId}/decision`, {
      method: "POST",
      body: { decision, decision_notes: notes },
    });
    await loadAll(false);
    showToast(`Request ${decision === "approve" ? "approved" : "denied"}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function completeCampaign(campaignId) {
  try {
    await api(`/api/review-campaigns/${campaignId}`, {
      method: "PATCH",
      body: { status: "complete", notes: "Completed from Governance view." },
    });
    await loadAll(false);
    showToast("Review campaign completed");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function acknowledgeNotification(notificationId) {
  try {
    await api(`/api/notifications/${notificationId}`, { method: "PATCH", body: {} });
    await loadAll(false);
    showToast("Notification acknowledged");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function runBackup(button = null) {
  const form = button?.closest("form");
  const retentionInput = form?.querySelector('input[name="retention_days"]');
  const retentionDays = retentionInput?.value ? Number(retentionInput.value) : 90;
  try {
    const result = await api("/api/backups/run", { method: "POST", body: { retention_days: retentionDays } });
    await loadAll(false);
    showToast(`Backup ${result.backup.status}`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveAdSchedule(form) {
  const payload = formPayload(form);
  payload.enabled = form.querySelector('input[name="enabled"]').checked;
  payload.format =
    form.querySelector('select[name="format"]')?.value ||
    document.querySelector('#adSyncForm select[name="format"]')?.value ||
    "csv";
  payload.source_name = payload.source_name || "Scheduled Active Directory sync";
  if (!payload.directory_text) {
    payload.directory_text = document.querySelector('#adSyncForm textarea[name="directory_text"]').value;
  }
  try {
    await api("/api/ad-sync-settings", { method: "POST", body: payload });
    await loadAll(false);
    showToast("Scheduled AD sync settings saved");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function runScheduledAd() {
  try {
    const result = await api("/api/ad/run-scheduled", { method: "POST", body: { force: true } });
    await loadAll(false);
    showToast(result.result.skipped ? result.result.reason : "Scheduled AD sync ran");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveAuthSettings(form) {
  const payload = formPayload(form);
  payload.login_required = form.querySelector('input[name="login_required"]').checked;
  try {
    await api("/api/auth-settings", { method: "POST", body: payload });
    await loadAll(false);
    showToast("Authentication settings saved");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function saveEmailSettings(form) {
  const payload = formPayload(form);
  try {
    await api("/api/email-settings", { method: "POST", body: payload });
    await loadAll(false);
    showToast("Email notification settings saved");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function routeRequestEmail(requestId) {
  try {
    const result = await api(`/api/access-requests/${requestId}/email-route`, { method: "POST", body: {} });
    await loadAll(false);
    showToast(`${labelize(result.emailRoute.provider)} notice created`);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function updateEmailRoute(routeId, status) {
  if (!status) return;
  try {
    await api(`/api/email-routes/${routeId}`, {
      method: "PATCH",
      body: {
        status,
        status_notes: `Marked ${labelize(status)} from Gatewatch.`,
      },
    });
    await loadAll(false);
    showToast("Email notice updated");
  } catch (error) {
    showToast(error.message, true);
  }
}

function hydrateStaticSelects() {
  fillSelect('#accessForm select[name="access_type"]', accessTypes, labelize);
  fillSelect('#accessForm select[name="status"]', creatableStatuses, labelize, "active");
  fillSelect('#systemForm select[name="category"]', categories, labelize);
  fillSelect('#systemForm select[name="risk_level"]', riskLevels, labelize, "standard");
  fillSelect('#resourceCategoryForm select[name="default_risk_level"]', riskLevels, labelize, "standard");
  const importCsv = document.querySelector('#importForm textarea[name="csv_text"]');
  if (importCsv && !importCsv.value.trim()) importCsv.value = sampleCsv;
  const adPayload = document.querySelector('#adSyncForm textarea[name="directory_text"]');
  if (adPayload && !adPayload.value.trim()) adPayload.value = sampleAdCsv;
  const configurationAdPayload = document.querySelector('#configurationAdScheduleForm textarea[name="directory_text"]');
  if (configurationAdPayload && !configurationAdPayload.value.trim()) configurationAdPayload.value = sampleAdCsv;
}

function hydrateDynamicSelects() {
  const employeeOptions = state.employees.map((employee) => ({ value: employee.id, label: `${employee.name} (${employee.employee_id})` }));
  const systemOptions = state.systems.map((system) => ({ value: system.id, label: systemLabel(system) }));
  const resourceCategoryOptions = state.resourceCategories.map((category) => ({ value: category.id, label: category.name }));
  fillSelect('#systemForm select[name="resource_category_id"]', resourceCategoryOptions);
  fillSelect(
    '#accessForm select[name="employee_id"]',
    employeeOptions
  );
  fillSelect(
    '#accessForm select[name="system_id"]',
    systemOptions
  );
  fillSelect(
    '#importForm select[name="system_id"]',
    systemOptions
  );
  fillSelect('#accessRequestForm select[name="employee_id"]', employeeOptions);
  fillSelect('#accessRequestForm select[name="system_id"]', systemOptions);
  fillSelect('#accessRequestForm select[name="access_type"]', accessTypes, labelize);
  fillSelect('#reviewCampaignForm select[name="system_id"]', [{ value: "", label: "All systems" }, ...systemOptions]);
  fillSelect('#sharedAccountForm select[name="system_id"]', systemOptions);
  fillSelect('#physicalCredentialForm select[name="employee_id"]', employeeOptions);
  fillSelect(
    '#physicalCredentialForm select[name="system_id"]',
    [{ value: "", label: "No linked system" }, ...systemOptions]
  );
}

function fillSelect(selector, values, labeler = null, selected = null) {
  const select = document.querySelector(selector);
  if (!select) return;
  const prior = select.value;
  select.innerHTML = values
    .map((value) => {
      const optionValue = typeof value === "object" ? value.value : value;
      const label = typeof value === "object" ? value.label : labeler ? labeler(value) : value;
      return `<option value="${escapeHtml(String(optionValue))}">${escapeHtml(label)}</option>`;
    })
    .join("");
  select.value = selected || prior || select.options[0]?.value || "";
}

function applyRoleLocks() {
  setFormDisabled("#employeeForm", !["Admin", "HR"].includes(state.role));
  setFormDisabled("#systemForm", !["Admin", "Supervisor"].includes(state.role));
  setFormDisabled("#resourceCategoryForm", !["Admin", "Supervisor"].includes(state.role));
  setFormDisabled("#accessForm", !["Admin", "Supervisor"].includes(state.role));
  setFormDisabled("#importForm", state.role !== "Admin");
  setFormDisabled("#adSyncForm", state.role !== "Admin");
  setFormDisabled("#adScheduleForm", state.role !== "Admin");
  setFormDisabled("#accessRequestForm", !["Admin", "Supervisor", "HR", "Employee"].includes(state.role));
  setFormDisabled("#reviewCampaignForm", !["Admin", "Supervisor", "Reviewer"].includes(state.role));
  setFormDisabled("#sharedAccountForm", state.role !== "Admin");
  setFormDisabled("#physicalCredentialForm", !["Admin", "HR"].includes(state.role));
  setFormDisabled("#connectorForm", state.role !== "Admin");
  setFormDisabled("#authSettingsForm", state.role !== "Admin");
  setFormDisabled("#configurationAuthForm", state.role !== "Admin");
  setFormDisabled("#configurationAdScheduleForm", state.role !== "Admin");
  setFormDisabled("#configurationEmailForm", state.role !== "Admin");
  setFormDisabled("#configurationConnectorForm", state.role !== "Admin");
  setFormDisabled("#configurationBackupForm", state.role !== "Admin");
  setFormDisabled("#employeeEditForm", state.role !== "Admin");
  setFormDisabled("#evidenceForm", !["Admin", "Supervisor", "HR"].includes(state.role));
  setActionDisabled("route-disabled-removals", !["Admin", "Supervisor", "HR"].includes(state.role));
  setActionDisabled("run-backup", state.role !== "Admin");
  setActionDisabled("update-email-route", !["Admin", "Supervisor", "Reviewer"].includes(state.role));
}

function setFormDisabled(selector, disabled) {
  document.querySelectorAll(`${selector} input, ${selector} select, ${selector} textarea, ${selector} button`).forEach((element) => {
    element.disabled = disabled;
  });
}

function setActionDisabled(action, disabled) {
  document.querySelectorAll(`[data-action="${action}"]`).forEach((element) => {
    element.disabled = disabled;
  });
}

function reviewDisabled() {
  return ["Admin", "Supervisor", "Reviewer"].includes(state.role) ? "" : "disabled";
}

function updateDisabled() {
  return ["Admin", "Supervisor", "HR"].includes(state.role) ? "" : "disabled";
}

function notificationDisabled() {
  return ["Admin", "Supervisor", "Reviewer", "HR"].includes(state.role) ? "" : "disabled";
}

function terminateDisabled(employee) {
  if (employee.status === "terminated") return "disabled";
  return ["Admin", "HR"].includes(state.role) ? "" : "disabled";
}

function statusChip(value) {
  const safe = String(value || "unknown");
  return `<span class="status ${escapeHtml(safe)}">${escapeHtml(labelize(safe))}</span>`;
}

function directoryChip(employee) {
  if (employee.ad_enabled === 0) {
    return `<span class="status terminated">AD disabled</span>`;
  }
  if (employee.ad_enabled === 1) {
    return `<span class="status active">AD enabled</span>`;
  }
  return `<span class="status unknown">${escapeHtml(labelize(employee.source || "manual"))}</span>`;
}

function riskChip(value) {
  const safe = String(value || "standard");
  return `<span class="risk ${escapeHtml(safe)}">${escapeHtml(labelize(safe))}</span>`;
}

function typeChip(value) {
  return `<span class="type-chip">${escapeHtml(labelize(value || "user"))}</span>`;
}

function staleChip() {
  return `<span class="status stale">Review due</span>`;
}

function labelize(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function initials(value) {
  const parts = String(value || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return (parts[0]?.[0] || "?") + (parts.length > 1 ? parts[parts.length - 1][0] : "");
}

function formatDate(value) {
  if (!value) return "Never";
  return String(value).slice(0, 10);
}

function formatDateTime(value) {
  if (!value) return "";
  return String(value).replace("T", " ").replace("Z", "");
}

function systemLabel(system) {
  const product = system.product_name && system.product_name !== system.name ? ` (${system.product_name})` : "";
  const category = system.resource_category_name ? ` - ${system.resource_category_name}` : "";
  return `${system.name}${product}${category}`;
}

function systemUrlList(system) {
  const links = [
    ["App", system.application_url],
    ["Admin", system.admin_url],
    ["Docs", system.documentation_url],
  ].filter((entry) => entry[1]);
  if (!links.length) return '<span class="secondary-text">No URLs</span>';
  return `<div class="url-list">${links.map(([label, url]) => externalLink(label, url)).join("")}</div>`;
}

function externalLink(label, url) {
  return `<a href="${attr(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function attr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

function showToast(message, isError = false) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove("show"), 3800);
}
