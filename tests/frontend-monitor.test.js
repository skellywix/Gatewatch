const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const repoRoot = path.resolve(__dirname, "..");

function cssBlock(css, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`));
  assert.ok(match, `Missing CSS block for ${selector}`);
  return match[1];
}

class FakeClassList {
  constructor(element) {
    this.element = element;
    this.classes = new Set(String(element.className || "").split(/\s+/).filter(Boolean));
  }

  add(name) {
    this.classes.add(name);
    this.sync();
  }

  remove(name) {
    this.classes.delete(name);
    this.sync();
  }

  toggle(name, force) {
    const shouldHave = force === undefined ? !this.classes.has(name) : Boolean(force);
    if (shouldHave) {
      this.classes.add(name);
    } else {
      this.classes.delete(name);
    }
    this.sync();
    return shouldHave;
  }

  contains(name) {
    return this.classes.has(name);
  }

  sync() {
    this.element.className = [...this.classes].join(" ");
  }
}

class FakeElement {
  constructor({ id = "", className = "", dataset = {}, value = "", textContent = "", hidden = false } = {}) {
    this.id = id;
    this.className = className;
    this.dataset = { ...dataset };
    this.value = value;
    this.defaultValue = value;
    this.textContent = textContent;
    this.innerHTML = "";
    this.hidden = hidden;
    this.disabled = false;
    this.checked = false;
    this.defaultChecked = false;
    this.tabIndex = 0;
    this.title = "";
    this.attributes = new Map();
    this.listeners = new Map();
    this.classList = new FakeClassList(this);
    this.elements = {};
  }

  addEventListener(type, handler) {
    this.listeners.set(type, handler);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name);
  }

  focus() {
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
  }

  reset() {
    Object.values(this.elements).forEach((element) => {
      element.value = element.defaultValue || "";
      element.checked = Boolean(element.defaultChecked);
    });
  }

  closest() {
    return null;
  }

  querySelector() {
    return null;
  }
}

function formElements() {
  const elements = {};
  for (const name of [
    "id",
    "employee_id",
    "name",
    "email",
    "phone",
    "department",
    "title",
    "location",
    "manager",
    "status",
    "request_source",
    "access_needed",
    "notes",
    "request_received",
    "manager_approved",
    "it_provisioned",
    "employee_notified",
  ]) {
    elements[name] = new FakeElement({ id: `form-${name}` });
  }
  elements.status.value = "active";
  elements.status.defaultValue = "active";
  return elements;
}

function createDom() {
  const elements = new Map();
  const tabButtons = [];
  const panels = [];
  const document = {
    activeElement: null,
    querySelector(selector) {
      if (selector === ".tabs") return elements.get("tabs");
      if (selector.startsWith("#")) return elements.get(selector.slice(1)) || null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "[data-tab]") return tabButtons;
      if (selector === "[data-panel]") return panels;
      return [];
    },
  };

  function add(id, options = {}) {
    const element = new FakeElement({ id, ...options });
    element.ownerDocument = document;
    elements.set(id, element);
    return element;
  }

  add("tabs", { className: "tabs" });
  tabButtons.push(add("overviewTab", { className: "tab is-active", dataset: { tab: "overview" } }));
  tabButtons.push(add("usersTab", { className: "tab", dataset: { tab: "users" } }));
  tabButtons.push(add("activityTab", { className: "tab", dataset: { tab: "activity" } }));
  tabButtons.push(add("backendTab", { className: "tab", dataset: { tab: "backend" }, hidden: true }));

  panels.push(add("overviewPanel", { className: "tab-panel is-active", dataset: { panel: "overview" } }));
  panels.push(add("usersPanel", { className: "tab-panel", dataset: { panel: "users" }, hidden: true }));
  panels.push(add("activityPanel", { className: "tab-panel", dataset: { panel: "activity" }, hidden: true }));
  panels.push(add("backendPanel", { className: "tab-panel", dataset: { panel: "backend" }, hidden: true }));

  for (const id of [
    "primaryAction",
    "searchField",
    "searchInput",
    "searchHelp",
    "statusFilters",
    "metrics",
    "monitoringList",
    "signalCount",
    "activityFeed",
    "activityCount",
    "detailInspector",
    "overallStatusLight",
    "overallStatusText",
    "lastUpdated",
    "userSearchField",
    "userSearchInput",
    "userSearchHelp",
    "userSearchOptions",
    "userListCount",
    "userProfileList",
    "newUserButton",
    "userForm",
    "userFormTitle",
    "userFormSubtitle",
    "formModeBadge",
    "customAccessFields",
    "customFieldCount",
    "viewUserActivityButton",
    "deleteUserButton",
    "clearUserButton",
    "saveUserButton",
    "activityActor",
    "activityExportLink",
    "activityLogList",
    "refreshBackendButton",
    "syncDirectoryButton",
    "backendConfigSummary",
    "backendConfigBody",
    "adminLogBody",
    "adminLogsSummary",
    "toast",
  ]) {
    if (!elements.has(id)) add(id);
  }

  elements.get("userForm").elements = formElements();
  return { document, elements, tabButtons, panels };
}

function createApp({ hash = "" } = {}) {
  const dom = createDom();
  const location = { hash, pathname: "/", search: "" };
  const history = {
    pushState(_state, _title, url) {
      location.hash = String(url).includes("#") ? String(url).slice(String(url).indexOf("#")) : "";
    },
    replaceState(_state, _title, url) {
      location.hash = String(url).includes("#") ? String(url).slice(String(url).indexOf("#")) : "";
    },
  };
  const context = vm.createContext({
    console,
    document: dom.document,
    fetch() {
      throw new Error("fetch should not run in frontend monitor regression tests");
    },
    history,
    location,
    window: {
      addEventListener() {},
      clearTimeout() {},
      confirm() {
        return true;
      },
      setTimeout() {},
    },
    Intl,
    Date,
    Number,
    JSON,
    String,
    Boolean,
    RegExp,
    Set,
    Map,
  });
  const appPath = path.join(repoRoot, "web", "app.js");
  const source = readFileSync(appPath, "utf8").replace(/\r?\nloadAll\(\);\r?\n/, "\n");
  vm.runInContext(
    `${source}\nglobalThis.__gatewatch = { state, ui, renderTabs, renderOverview, renderUsers, renderActivity, setActiveTab, visibleOverviewEmployees, validateSearch, selectEmployee, selectedEmployee, filterCounts };`,
    context,
    { filename: appPath },
  );
  return { ...context.__gatewatch, ...dom, location };
}

function seedEmployees(app) {
  app.state.auth = { permissions: { actor: "Test Operator", canModifyEmployees: false } };
  app.state.loading = false;
  app.state.loadedOnce = true;
  app.state.recentUntil = 0;
  app.state.summary = {};
  app.state.audit = [];
  app.state.accessFields = [];
  app.state.employees = [
    {
      id: 1,
      employee_id: "FOB-1001",
      name: "Avery Morgan",
      email: "avery@example.test",
      phone: "555-1001",
      department: "Operations",
      title: "Operations Lead",
      location: "HQ",
      manager: "Dana Chen",
      status: "active",
      request_source: "HR",
      access_needed: "",
      request_received: 1,
      manager_approved: 1,
      it_provisioned: 1,
      employee_notified: 1,
      access_profile: { branch: "HQ" },
      updated_at: "2026-06-30T12:00:00Z",
    },
    {
      id: 2,
      employee_id: "FOB-1002",
      name: "Blake Rivera",
      email: "blake@example.test",
      phone: "555-1002",
      department: "IT",
      title: "Support Tech",
      location: "Remote",
      manager: "Avery Morgan",
      status: "active",
      request_source: "Manager",
      access_needed: "VPN and payroll",
      request_received: 1,
      manager_approved: 1,
      it_provisioned: 0,
      employee_notified: 0,
      access_profile: {},
      updated_at: "2026-06-30T13:00:00Z",
    },
    {
      id: 3,
      employee_id: "FOB-1003",
      name: "Casey Singh",
      email: "casey@example.test",
      phone: "555-1003",
      department: "Finance",
      title: "Analyst",
      location: "Branch",
      manager: "Avery Morgan",
      status: "disabled",
      request_source: "IT",
      access_needed: "",
      request_received: 0,
      manager_approved: 0,
      it_provisioned: 0,
      employee_notified: 0,
      access_profile: {},
      updated_at: "2026-06-30T14:00:00Z",
    },
    {
      id: 4,
      employee_id: "FOB-1004",
      name: "Drew Patel",
      email: "drew@example.test",
      phone: "555-1004",
      department: "Sales",
      title: "Rep",
      location: "HQ",
      manager: "Casey Singh",
      status: "terminated",
      request_source: "HR",
      access_needed: "",
      request_received: 0,
      manager_approved: 0,
      it_provisioned: 0,
      employee_notified: 0,
      access_profile: {},
      updated_at: "2026-06-30T15:00:00Z",
    },
  ];
}

test("overview is the default monitor tab in HTML and app state", () => {
  const html = readFileSync(path.join(repoRoot, "web", "index.html"), "utf8");
  assert.match(html, /id="overviewTab" class="tab is-active"[^>]+aria-selected="true"/);
  assert.match(html, /id="overviewPanel" class="tab-panel is-active"[^>]+data-panel="overview"/);
  assert.match(html, /id="usersPanel" class="tab-panel"[^>]+hidden/);

  const app = createApp();
  assert.equal(app.state.activeTab, "overview");
  app.renderTabs();
  assert.equal(app.elements.get("overviewPanel").hidden, false);
  assert.equal(app.elements.get("usersPanel").hidden, true);
  assert.equal(app.elements.get("overviewTab").getAttribute("aria-selected"), "true");

  app.setActiveTab("backend");
  assert.equal(app.state.activeTab, "overview");
  assert.equal(app.elements.get("backendTab").hidden, true);
});

test("main navigation tabs keep stable dimensions across active states", () => {
  const css = readFileSync(path.join(repoRoot, "web", "styles.css"), "utf8");
  const shell = cssBlock(css, ".monitor-shell");
  const tabs = cssBlock(css, ".tabs");
  const tab = cssBlock(css, ".tab");
  const activeTab = cssBlock(css, ".tab.is-active");

  assert.match(shell, /grid-auto-rows:\s*max-content;/);
  assert.match(shell, /align-content:\s*start;/);
  assert.match(tabs, /align-items:\s*center;/);
  assert.match(tab, /width:\s*112px;/);
  assert.match(tab, /min-height:\s*44px;/);
  assert.match(tab, /display:\s*inline-flex;/);
  assert.match(tab, /align-items:\s*center;/);
  assert.match(tab, /justify-content:\s*center;/);
  assert.doesNotMatch(activeTab, /\b(width|min-width|max-width|height|min-height|max-height|padding)\s*:/);
});

test("overview search, filters, and selected record state stay wired together", () => {
  const app = createApp();
  seedEmployees(app);

  app.renderOverview();
  assert.deepEqual({ ...app.filterCounts() }, { active: 2, inProgress: 1, disabled: 1, terminated: 1 });
  assert.equal(app.visibleOverviewEmployees().length, 4);
  assert.match(app.elements.get("statusFilters").innerHTML, /data-filter="all" aria-pressed="true"/);
  assert.match(app.elements.get("monitoringList").innerHTML, /Avery Morgan/);
  assert.match(app.elements.get("monitoringList").innerHTML, /Drew Patel/);

  app.state.filter = "inProgress";
  app.renderOverview();
  assert.equal(app.elements.get("signalCount").textContent, "1 record");
  assert.match(app.elements.get("statusFilters").innerHTML, /data-filter="inProgress" aria-pressed="true"/);
  assert.match(app.elements.get("monitoringList").innerHTML, /Blake Rivera/);
  assert.doesNotMatch(app.elements.get("monitoringList").innerHTML, /Avery Morgan/);

  app.state.filter = "all";
  app.state.overviewQuery = "finance";
  app.elements.get("searchInput").value = "finance";
  app.renderOverview();
  assert.equal(app.elements.get("searchField").dataset.state, "valid");
  assert.equal(app.elements.get("searchInput").getAttribute("aria-invalid"), "false");
  assert.equal(app.elements.get("searchHelp").textContent, "Search active.");
  assert.equal(app.elements.get("signalCount").textContent, "1 record");
  assert.match(app.elements.get("monitoringList").innerHTML, /Casey Singh/);

  app.state.overviewQuery = "<bad>";
  app.elements.get("searchInput").value = "<bad>";
  app.renderOverview();
  assert.equal(app.elements.get("searchField").dataset.state, "error");
  assert.equal(app.elements.get("searchInput").getAttribute("aria-invalid"), "true");
  assert.equal(app.elements.get("searchHelp").textContent, "Remove unsupported characters.");
  assert.match(app.elements.get("monitoringList").innerHTML, /No matching users/);

  app.state.overviewQuery = "";
  app.elements.get("searchInput").value = "";
  app.selectEmployee(3);
  assert.equal(app.selectedEmployee().name, "Casey Singh");
  assert.match(app.elements.get("monitoringList").innerHTML, /is-selected[\s\S]*aria-selected="true" data-signal-id="3"/);
  assert.match(app.elements.get("detailInspector").innerHTML, /Casey Singh/);
  assert.match(app.elements.get("detailInspector").innerHTML, /Disabled/);
});

test("selected users scope the activity log and expanded entries show field changes", () => {
  const app = createApp();
  seedEmployees(app);
  app.state.audit = [
    {
      id: 77,
      created_at: "2026-06-30T16:00:00Z",
      action: "update",
      entity_type: "employee",
      entity_id: 2,
      actor: "Test Operator",
      summary: "Updated employee Blake Rivera.",
      before_json: JSON.stringify({ name: "Blake Rivera", phone: "555-1002", notes: "VPN" }),
      after_json: JSON.stringify({ name: "Blake Rivera", phone: "555-2222", notes: "VPN and payroll" }),
    },
  ];

  app.selectEmployee(2);
  app.state.selectedActivityKey = "77";
  app.renderActivity();

  assert.equal(app.elements.get("activityActor").textContent, "Showing activity for Blake Rivera.");
  assert.match(app.elements.get("activityLogList").innerHTML, /data-activity-scope="selected" aria-pressed="true"/);
  assert.match(app.elements.get("activityLogList").innerHTML, /aria-expanded="true"/);
  assert.match(app.elements.get("activityLogList").innerHTML, /Phone/);
  assert.match(app.elements.get("activityLogList").innerHTML, /555-1002/);
  assert.match(app.elements.get("activityLogList").innerHTML, /555-2222/);
  assert.match(app.elements.get("activityLogList").innerHTML, /VPN and payroll/);
});
