// Ticket templates for Kaneo. Served on Kaneo's own origin, so API calls use
// the viewer's existing Kaneo session cookie; nothing here holds a credential.
// A ticket is created only when the viewer submits the form, never on load.
"use strict";

const BASE = "/new/";
const $ = (id) => document.getElementById(id);

function showStatus(message, isError) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", Boolean(isError));
  el.hidden = false;
}

function showStatusLink(message, href, linkText) {
  showStatus(message + " ", true);
  const a = document.createElement("a");
  a.href = href;
  a.textContent = linkText;
  $("status").append(a);
}

async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    credentials: "same-origin",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  if (!response.ok) {
    const error = new Error(`${options.method || "GET"} ${path}: HTTP ${response.status}`);
    error.status = response.status;
    try {
      const body = await response.json();
      if (body && body.message) error.message += ` (${body.message})`;
    } catch {
      // Body was not JSON; the status is enough.
    }
    throw error;
  }
  return response.json();
}

function option(select, value, text) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = text;
  select.append(el);
}

// "homelab" and "area:homelab" both name the same area.
function normalise(value, prefix) {
  if (!value) return "";
  return value.startsWith(prefix) ? value : prefix + value;
}

function renderPicker(config) {
  document.title = "New ticket";
  $("summary").textContent = "Pick a template. Nothing is created until you submit the form.";
  for (const t of config.templates) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = BASE + t.id;
    const name = document.createElement("strong");
    name.textContent = t.name;
    const summary = document.createElement("span");
    summary.textContent = t.summary;
    a.append(name, summary);
    li.append(a);
    $("template-list").append(li);
  }
  for (const area of config.areas) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `${BASE}task?area=${encodeURIComponent(area.label.replace(/^area:/, ""))}`;
    a.textContent = area.name;
    li.append(a);
    $("area-list").append(li);
  }
  $("picker").hidden = false;
}

function selectedLabels(template) {
  const labels = [...template.labels];
  if (template.area !== "none" && $("area").value) labels.push($("area").value);
  if ($("group").value) labels.push($("group").value);
  if ($("human-only").checked) labels.push("human-only");
  return labels;
}

function updateLabelsPreview(template) {
  const labels = selectedLabels(template);
  $("labels-preview").textContent = labels.length
    ? "Labels: " + labels.join(", ")
    : "Labels: none yet";
}

function renderForm(config, template, params) {
  document.title = `New ticket: ${template.name}`;
  $("heading").textContent = `New ticket: ${template.name}`;
  $("summary").textContent = template.summary;

  const area = $("area");
  if (template.area === "none") {
    $("area-field").hidden = true;
    $("group-field").hidden = true;
  } else {
    option(area, "", "Choose an area…");
    for (const a of config.areas) option(area, a.label, a.name);
    const wanted = normalise(params.get("area"), "area:") ||
      (template.area === "choose" ? "" : template.area);
    if (config.areas.some((a) => a.label === wanted)) area.value = wanted;
  }

  const group = $("group");
  option(group, "", "None");
  for (const g of config.groups) option(group, g.label, g.name);
  const wantedGroup = normalise(params.get("group"), "group:") || template.group || "";
  if (config.groups.some((g) => g.label === wantedGroup)) group.value = wantedGroup;

  $("priority").value = template.priority || "no-priority";
  $("state").value = template.status || "planned";
  $("title").value = params.get("title") || "";
  $("description").value = config.descriptions[template.description] || "";

  for (const id of ["area", "group", "human-only"]) {
    $(id).addEventListener("change", () => updateLabelsPreview(template));
  }
  updateLabelsPreview(template);

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit(config, template);
  });
  $("form").hidden = false;
  $("title").focus();
}

async function resolveTarget(config) {
  const workspaces = await api("/workspace");
  const workspace = workspaces.find((w) => w.slug === config.workspaceSlug);
  if (!workspace) throw new Error(`Workspace "${config.workspaceSlug}" not found for this account.`);
  const projects = await api(`/project?workspaceId=${encodeURIComponent(workspace.id)}`);
  const list = Array.isArray(projects) ? projects : projects.data || [];
  const project = list.find((p) => p.slug === config.projectSlug);
  if (!project) throw new Error(`Project "${config.projectSlug}" not found.`);
  // Workspace labels are the rows not attached to a task.
  const labels = await api(`/label/workspace/${encodeURIComponent(workspace.id)}`);
  const labelIds = new Map();
  for (const label of labels) {
    if (label.taskId == null) labelIds.set(label.name, label.id);
  }
  return { workspace, project, labelIds };
}

let target;

async function submit(config, template) {
  const title = $("title").value.trim();
  if (!title) {
    showStatus("Give the ticket a title.", true);
    $("title").focus();
    return;
  }
  if (template.area !== "none" && !$("area").value) {
    showStatus("Choose an area. Every ticket needs exactly one.", true);
    $("area").focus();
    return;
  }
  const labels = selectedLabels(template);
  const missing = labels.filter((name) => !target.labelIds.has(name));
  if (missing.length) {
    showStatus(`These labels don't exist in Kaneo: ${missing.join(", ")}.`, true);
    return;
  }

  $("submit").disabled = true;
  showStatus("Creating…", false);
  let task;
  try {
    task = await api(`/task/${encodeURIComponent(target.project.id)}`, {
      method: "POST",
      body: JSON.stringify({
        title,
        description: $("description").value,
        priority: $("priority").value,
        status: $("state").value,
      }),
    });
  } catch (error) {
    showStatus(`Could not create the ticket: ${error.message}`, true);
    $("submit").disabled = false;
    return;
  }

  const taskUrl =
    `/dashboard/workspace/${encodeURIComponent(target.workspace.id)}` +
    `/project/${encodeURIComponent(target.project.id)}/task/${encodeURIComponent(task.id)}`;
  const failed = [];
  for (const name of labels) {
    try {
      await api(`/label/${encodeURIComponent(target.labelIds.get(name))}/task`, {
        method: "PUT",
        body: JSON.stringify({ taskId: task.id }),
      });
    } catch {
      failed.push(name);
    }
  }
  if (failed.length) {
    showStatusLink(
      `Created ${config.projectSlug}-${task.number}, but these labels were not attached: ${failed.join(", ")}.`,
      taskUrl,
      "Open the ticket",
    );
    return;
  }
  window.location.assign(taskUrl);
}

async function main() {
  let config;
  try {
    const response = await fetch(BASE + "templates.json", { cache: "no-cache" });
    config = await response.json();
  } catch (error) {
    showStatus(`Could not load templates: ${error.message}`, true);
    return;
  }

  const id = window.location.pathname.slice(BASE.length).replace(/\/+$/, "");
  if (!id) {
    renderPicker(config);
    return;
  }
  const template = config.templates.find((t) => t.id === id);
  if (!template) {
    showStatusLink(`There is no "${id}" template.`, BASE, "See all templates");
    return;
  }

  try {
    target = await resolveTarget(config);
  } catch (error) {
    if (error.status === 401 || error.status === 403) {
      showStatusLink("Sign in to Kaneo first, then reload this page.", "/auth/sign-in", "Sign in");
    } else {
      showStatus(error.message, true);
    }
    return;
  }
  renderForm(config, template, new URLSearchParams(window.location.search));
}

main();
