"use strict";

const BASE = "/new/";
const $ = (id) => document.getElementById(id);
let target;

function showStatus(message, link) {
  const el = $("status");
  el.textContent = message;
  if (link) {
    const a = document.createElement("a");
    a.href = link.href;
    a.textContent = " " + link.text;
    el.append(a);
  }
  el.hidden = false;
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
    throw error;
  }
  return response.json();
}

function link(href, ...children) {
  const li = document.createElement("li");
  const a = document.createElement("a");
  a.href = href;
  a.append(...children);
  li.append(a);
  return li;
}

function option(select, value, text) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = text;
  select.append(el);
}

function renderPicker(config) {
  for (const t of config.templates) {
    const name = document.createElement("strong");
    name.textContent = t.name;
    const summary = document.createElement("span");
    summary.textContent = t.summary;
    $("template-list").append(link(BASE + t.id, name, summary));
  }
  for (const area of config.areas) {
    const slug = encodeURIComponent(area.label.replace(/^area:/, ""));
    $("area-list").append(link(`${BASE}task?area=${slug}`, area.name));
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

function renderForm(config, template, params) {
  document.title = `New ticket: ${template.name}`;
  $("heading").textContent = document.title;
  $("summary").textContent = template.summary;

  if (template.area === "none") {
    $("area-field").hidden = true;
    $("group-field").hidden = true;
  } else {
    option($("area"), "", "Choose an area…");
    for (const a of config.areas) option($("area"), a.label, a.name);
    const wanted = params.get("area") ? "area:" + params.get("area") : template.area;
    if (config.areas.some((a) => a.label === wanted)) $("area").value = wanted;
  }
  option($("group"), "", "None");
  for (const g of config.groups) option($("group"), g.label, g.name);
  const group = params.get("group") ? "group:" + params.get("group") : template.group;
  if (config.groups.some((g) => g.label === group)) $("group").value = group;

  $("priority").value = template.priority;
  $("state").value = template.status;
  $("title").value = params.get("title") || "";
  $("description").value = config.descriptions[template.description];

  const preview = () => {
    $("labels-preview").textContent = "Labels: " + (selectedLabels(template).join(", ") || "none");
  };
  for (const id of ["area", "group", "human-only"]) $(id).addEventListener("change", preview);
  preview();

  $("form").addEventListener("submit", (event) => {
    event.preventDefault();
    submit(config, template);
  });
  $("form").hidden = false;
  $("title").focus();
}

async function resolveTarget(config) {
  const workspace = (await api("/workspace")).find((w) => w.slug === config.workspaceSlug);
  if (!workspace) throw new Error(`Workspace "${config.workspaceSlug}" not found.`);
  const projects = await api(`/project?workspaceId=${encodeURIComponent(workspace.id)}`);
  const project = (projects.data || projects).find((p) => p.slug === config.projectSlug);
  if (!project) throw new Error(`Project "${config.projectSlug}" not found.`);
  const labelIds = new Map();
  for (const label of await api(`/label/workspace/${encodeURIComponent(workspace.id)}`)) {
    if (label.taskId == null) labelIds.set(label.name, label.id);
  }
  return { workspace, project, labelIds };
}

async function submit(config, template) {
  const title = $("title").value.trim();
  if (!title) return showStatus("Give the ticket a title.");
  if (template.area !== "none" && !$("area").value) {
    return showStatus("Choose an area. Every ticket needs exactly one.");
  }
  const labels = selectedLabels(template);
  const missing = labels.filter((name) => !target.labelIds.has(name));
  if (missing.length) return showStatus(`Missing Kaneo labels: ${missing.join(", ")}.`);

  $("submit").disabled = true;
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
    $("submit").disabled = false;
    return showStatus(`Could not create the ticket: ${error.message}`);
  }

  const taskUrl = `/dashboard/workspace/${target.workspace.id}/project/${target.project.id}/task/${task.id}`;
  const failed = [];
  for (const name of labels) {
    try {
      await api(`/label/${target.labelIds.get(name)}/task`, {
        method: "PUT",
        body: JSON.stringify({ taskId: task.id }),
      });
    } catch {
      failed.push(name);
    }
  }
  if (failed.length) {
    return showStatus(`Created, but these labels were not attached: ${failed.join(", ")}.`,
      { href: taskUrl, text: "Open the ticket" });
  }
  window.location.assign(taskUrl);
}

async function main() {
  const config = await (await fetch(BASE + "templates.json", { cache: "no-cache" })).json();
  const id = window.location.pathname.slice(BASE.length).replace(/\/+$/, "");
  if (!id) return renderPicker(config);
  const template = config.templates.find((t) => t.id === id);
  if (!template) return showStatus(`No "${id}" template.`, { href: BASE, text: "All templates" });
  try {
    target = await resolveTarget(config);
  } catch (error) {
    if (error.status === 401) {
      return showStatus("Sign in to Kaneo, then reload this page.", { href: "/auth/sign-in", text: "Sign in" });
    }
    return showStatus(error.message);
  }
  renderForm(config, template, new URLSearchParams(window.location.search));
}

main().catch((error) => showStatus(error.message));
