"use strict";

const stage = document.querySelector("#stage");
const selectionPanel = document.querySelector("#selection");
const next = document.querySelector("#next");
const back = document.querySelector("#back");
const steps = document.querySelector("#steps");
const caption = document.querySelector("#caption");
const toast = document.querySelector("#alert");
const planFile = document.querySelector("#plan-file");

const state = {
  data: null, step: 0, visited: 0, series: "", model: "",
  query: "", selected: new Set(), ids: new Set(), plan: null, result: null, busy: false,
  prepared: new Set(), downloadStatus: "",
};
let toastTimeout;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function button(label, className, onClick) {
  const element = node("button", className, label);
  element.type = "button";
  element.addEventListener("click", onClick);
  return element;
}

function pressed(element, active) {
  element.setAttribute("aria-pressed", String(active));
}

function showToast(message) {
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => { toast.hidden = true; }, 7500);
}

async function request(route, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  };
  const response = await fetch("api/" + route, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "工具连接已中断。请重新打开程序。");
  return data;
}

function title(parent, heading, detail) {
  const header = node("div", "section-title");
  const h2 = node("h2", "", heading);
  h2.tabIndex = -1;
  header.append(h2, node("p", "", detail));
  parent.append(header);
}

function summary() {
  const heading = node("div", "summary-heading");
  heading.append(node("h2", "", "已选设备"), node("span", "count", state.selected.size));
  const list = node("div", "summary-list");
  if (!state.selected.size) {
    list.append(node("p", "summary-empty", "选择同一组灯光中需要配对的设备。"));
  }
  for (const key of state.selected) {
    const item = state.data.firmwares.find(firmware => firmware.key === key);
    const row = node("div", "selected");
    const remove = button("×", "remove", () => selectDevice(key));
    remove.setAttribute("aria-label", "移除 " + item.label);
    const info = node("div", "selected-info");
    info.append(node("span", "", item.label), node("small", "", item.updater));
    row.append(info, remove);
    list.append(row);
  }
  selectionPanel.replaceChildren(
    heading, list,
    node("p", "summary-hint", "同一套设备使用同一份配对方案。刷写后将频道和无线 ID 设为相同。"),
  );
}

function selectDevice(key) {
  if (state.selected.has(key)) state.selected.delete(key);
  else state.selected.add(key);
  state.result = null;
  for (const card of stage.querySelectorAll("[data-device]")) {
    setDeviceState(card, state.selected.has(card.dataset.device));
  }
  summary();
  if (!state.selected.size && state.step !== 0) go(0);
  else if (state.step === 2) renderStage();
  controls();
}

function setDeviceState(card, active) {
  pressed(card, active);
  card.querySelector(".device-mark").textContent = card.disabled ? "–" : active ? "✓" : "+";
}

function devices(page) {
  title(page, "选取设备版本", "点击下一步后，将自动从神牛官网下载并校验所选固件。");
  const family = node("div", "switches series");
  family.setAttribute("aria-label", "设备系列");
  for (const [name, models] of Object.entries(state.data.series)) {
    const tab = button(name, "", () => {
      if (state.series === name) return;
      state.series = name;
      state.model = models[0];
      state.query = "";
      renderStage();
      stage.querySelector(".series [aria-pressed=true]").focus({preventScroll: true});
    });
    pressed(tab, name === state.series);
    family.append(tab);
  }
  const browser = node("div", "catalog-browser");
  const sidebar = node("section", "model-panel");
  const search = node("input", "model-search");
  search.type = "search";
  search.placeholder = "查找型号";
  search.setAttribute("aria-label", "在本系列查找型号");
  search.value = state.query;
  const filters = node("div", "model-list");
  filters.setAttribute("aria-label", "设备型号");
  const details = node("section", "model-details");
  details.setAttribute("aria-label", "固件版本");
  const models = state.data.series[state.series];
  const comparable = value => value.toLowerCase().replace(/[\s-]+/g, "");
  const entries = models.map(name => {
    const control = button(name, "model", () => {
      state.model = name;
      refresh();
    });
    filters.append(control);
    return [name, control];
  });
  const empty = node("p", "model-empty", "找不到该型号");
  filters.append(empty);
  function refresh() {
    let first = null;
    for (const [name, control] of entries) {
      control.hidden = !comparable(name).includes(comparable(state.query));
      if (!control.hidden && first === null) first = name;
    }
    empty.hidden = first !== null;
    if (!entries.some(([name, control]) => name === state.model && !control.hidden)) {
      state.model = first ?? state.model;
    }
    for (const [name, control] of entries) pressed(control, name === state.model);
    details.replaceChildren(...(first === null ? [] : versionCards()));
  }
  search.addEventListener("input", () => {
    state.query = search.value;
    refresh();
  });
  sidebar.append(search, filters);
  browser.append(sidebar, details);
  page.append(family, browser);
  refresh();
}

function versionCards() {
  const versions = state.data.firmwares.filter(firmware => firmware.model === state.model);
  const heading = node("div", "version-heading");
  heading.append(node("h3", "", state.model), node("span", "", versions.length + " 个固件版本"));
  const cards = node("div", "cards");
  if (!versions.length) {
    cards.append(node("p", "summary-empty", "此型号暂无支持的固件版本。"));
  }
  versions.forEach((item, index) => {
    const card = button("", "device", () => selectDevice(item.key));
    card.disabled = !item.available;
    card.dataset.device = item.key;
    card.title = item.filename;
    card.style.setProperty("--index", index);
    const header = node("span", "device-head");
    const mark = node("span", "device-mark");
    mark.setAttribute("aria-hidden", "true");
    header.append(node("strong", "", item.variant === "通用" ? item.model : item.variant), mark);
    const meta = node("span", "device-meta");
    meta.append(node("span", "", item.version), node("small", "updater", item.updater));
    card.append(header, meta);
    if (!item.available) card.append(node("span", "device-note", item.status));
    setDeviceState(card, state.selected.has(item.key));
    cards.append(card);
  });
  return [heading, cards];
}

function idsMatch() {
  if (!state.plan) return false;
  const keys = Object.keys(state.plan.sync_words);
  return keys.length === state.ids.size && keys.every(key => state.ids.has(Number(key)));
}

function selectIds(ids) {
  state.ids = new Set(ids);
  state.result = null;
  for (const cell of stage.querySelectorAll("[data-id]")) {
    pressed(cell, state.ids.has(Number(cell.dataset.id)));
  }
  controls();
}

function wireless(page) {
  title(page, "挑选无线 ID", "任选 01–99。选中的 ID 会使用新同步字，其他 ID 和 OFF 沿用原厂设置。");
  const row = node("div", "toolrow");
  row.append(
    button("恢复默认 90–99", "soft", () => selectIds(state.data.defaultIds)),
    button("全选", "soft", () => selectIds(Array.from({length: 99}, (_, index) => index + 1))),
    button("清空", "soft", () => selectIds([])),
    button("导入配对方案", "soft", () => planFile.click()),
  );
  const grid = node("div", "ids");
  const off = node("span", "id-off", "OFF");
  off.title = "关闭 ID 时沿用原厂设置";
  grid.append(off);
  for (let id = 1; id <= 99; id++) {
    const cell = button(String(id).padStart(2, "0"), "id-button", () => {
      const updated = new Set(state.ids);
      if (updated.has(id)) updated.delete(id);
      else updated.add(id);
      selectIds(updated);
    });
    cell.dataset.id = id;
    pressed(cell, state.ids.has(id));
    grid.append(cell);
  }
  page.append(row, grid);
}

function preview(page) {
  title(page, "确认并导出",
    state.selected.size + " 个设备版本共享 " + state.ids.size + " 个无线 ID。");
  const row = node("div", "toolrow");
  row.append(button("重新随机", "soft", () => preparePreview(true)));
  const words = node("div", "preview");
  for (const id of [...state.ids].sort((a, b) => a - b)) {
    const padded = String(id).padStart(2, "0");
    const entry = node("span", "word");
    entry.append(node("small", "", "ID " + padded), node("strong", "", state.plan.sync_words[padded]));
    words.append(entry);
  }
  page.append(row, words);
  if (state.result) {
    const done = node("div", "export-summary");
    done.append(node("strong", "", "已导出 " + state.result.files.length + " 份固件"),
      node("span", "", state.result.folder),
      node("p", "", "保留 sync-plan.json，添加设备时可再次导入。"));
    page.append(done);
  }
}

function renderStage(focus = false) {
  const page = node("section", "page");
  [devices, wireless, preview][state.step](page);
  stage.replaceChildren(page);
  if (focus) page.querySelector("h2").focus({preventScroll: true});
}

function controls() {
  stage.inert = state.busy;
  selectionPanel.inert = state.busy;
  stage.setAttribute("aria-busy", String(state.busy));
  const prepared = state.selected.size > 0 && [...state.selected].every(key => state.prepared.has(key));
  for (const stepButton of steps.querySelectorAll("button")) {
    const index = Number(stepButton.dataset.step);
    if (state.step === index) stepButton.setAttribute("aria-current", "step");
    else stepButton.removeAttribute("aria-current");
    stepButton.disabled = state.busy || !state.data || index > state.visited ||
      (index > 0 && !prepared) || (index > 1 && !idsMatch());
  }
  back.hidden = state.step === 0;
  back.disabled = state.busy;
  next.disabled = state.busy || !state.data || !state.selected.size ||
    (state.step > 0 && !state.ids.size);
  next.replaceChildren(
    state.downloadStatus ? "下载校验中…" : state.busy ? "处理中…" : state.step < 2 ? "下一步" :
      state.result ? "打开输出目录" : "选择位置并导出",
    node("span", "", " →"),
  );
  caption.textContent = state.step === 0
    ? state.downloadStatus || (state.data ? "支持 " + state.data.firmwares.length + " 个固件版本 · 按需下载" : "")
    : state.step === 1 ? "已选择 " + state.ids.size + " 个 ID" + (idsMatch() ? " · 沿用已保存方案" : "")
      : state.data.candidates.toLocaleString("zh-CN") + " 个候选同步字";
}

function go(index) {
  state.step = index;
  state.visited = Math.max(state.visited, index);
  renderStage(true);
  controls();
}

async function busy(work) {
  if (state.busy) return;
  state.busy = true;
  controls();
  try {
    await work();
  } catch (error) {
    showToast(error.message);
  } finally {
    state.busy = false;
    state.downloadStatus = "";
    controls();
  }
}

function prepareDevices() {
  return busy(async () => {
    const pending = [...state.selected].filter(key => !state.prepared.has(key));
    for (const [index, key] of pending.entries()) {
      const item = state.data.firmwares.find(firmware => firmware.key === key);
      state.downloadStatus = `正在下载与校验 ${index + 1}/${pending.length} · ${item.label}`;
      controls();
      await request("prepare", [key]);
      state.prepared.add(key);
    }
    go(1);
  });
}

function preparePreview(randomize = false) {
  return busy(async () => {
    if (randomize || !idsMatch()) {
      state.plan = await request("plan", [...state.ids]);
      state.result = null;
    }
    go(2);
  });
}

steps.addEventListener("click", event => {
  const target = event.target.closest("button[data-step]");
  if (target && !target.disabled) go(Number(target.dataset.step));
});
back.addEventListener("click", () => go(state.step - 1));
next.addEventListener("click", () => {
  if (state.step === 0) prepareDevices();
  else if (state.step === 1) preparePreview();
  else busy(async () => {
    if (state.result) {
      await request("open", {});
      return;
    }
    const answer = await request("export", {selection: [...state.selected], plan: state.plan});
    if (!answer.cancelled) {
      state.result = answer;
      renderStage(true);
    }
  });
});

planFile.addEventListener("change", () => {
  const file = planFile.files[0];
  planFile.value = "";
  if (!file) return;
  busy(async () => {
    if (file.size > 4096) throw new Error("配对方案文件过大。");
    state.plan = await request("import", await file.text());
    selectIds(Object.keys(state.plan.sync_words).map(Number));
    showToast("配对方案已导入");
  });
});

request("catalog").then(data => {
  state.data = data;
  [state.series] = Object.keys(data.series);
  [state.model] = data.series[state.series];
  state.ids = new Set(data.defaultIds);
  summary();
  renderStage();
  controls();
}).catch(error => {
  stage.replaceChildren(node("p", "notice", error.message));
  controls();
});
