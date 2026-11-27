const $ = (sel) => document.querySelector(sel);
const state = { master: null, current: null, advice: null, radar: null, versions: [] };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => `${Math.round((v || 0) * 100)}%`;
const num = (v, d = 0) => Number(v || 0).toFixed(d);
function fmt(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return iso;
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 3800);
}

async function loadAll() {
  state.master = await api("/api/master");
  state.current = await api("/api/schedule/current");
  state.advice = await api("/api/ai/advice");
  state.radar = await api("/api/ai/radar");
  state.versions = await api("/api/schedule/versions");
  renderAll();
}

function renderAll() {
  renderBadge();
  renderKPI();
  renderGantt();
  renderAdvice();
  renderRadar();
  renderOrders();
  renderMachines();
  renderWorkers();
  renderVersions();
}

function renderBadge() {
  const v = state.current?.version || {};
  const badge = $("#versionBadge");
  if (!v.id) { badge.textContent = "等待排产"; return; }
  badge.textContent = `版本 #${v.id} · ${v.strategy} · ${v.solver} · ${num(v.solve_seconds, 1)}s`;
  const llm = state.advice?.llm_enabled;
  $("#llmTag").textContent = llm ? "规则 + 大模型" : "规则引擎";
}

function renderKPI() {
  const v = state.current?.version || {};
  const k = (state.current?.version?.metrics || {}).kpis || {};
  const p = state.current?.progress || {};
  const onTime = k.on_time_rate ?? 0;
  const cls = onTime >= 0.95 ? "good" : onTime >= 0.8 ? "warn" : "bad";
  const cards = [
    { label: "交期达成率", value: pct(onTime), sub: `${k.orders_on_time || 0}/${k.orders_total || 0} 张订单按期`, cls },
    { label: "拖期订单", value: `${k.orders_late || 0} 单`, sub: `平均拖期 ${num((k.avg_tardiness_min || 0) / 60, 1)} h`, cls: (k.orders_late || 0) ? "bad" : "good" },
    { label: "计划完工跨度", value: `${num(k.makespan_hours, 1)} h`, sub: `瓶颈 ${k.bottleneck?.code || "-"} ${pct(k.bottleneck?.utilization)}` },
    { label: "任务完成率", value: pct(p.progress_rate), sub: `${p.tasks_done || 0}/${p.tasks_total || 0} 道工序完工` },
    { label: "良品产出", value: `${p.good_qty || 0} 件`, sub: `报废 ${p.scrap_qty || 0} 件 · ${pct(p.scrap_rate)}` },
    { label: "求解方式", value: (v.solver || "-").toUpperCase(), sub: `策略 ${v.strategy || "-"} · ${num(v.solve_seconds, 1)}s` },
  ];
  $("#kpis").innerHTML = cards.map((c) => `
    <div class="kpi">
      <div class="label">${esc(c.label)}</div>
      <div class="value ${c.cls || ""}">${esc(c.value)}</div>
      <div class="sub">${esc(c.sub)}</div>
    </div>`).join("");
}

function renderGantt() {
  const svg = $("#gantt");
  const as = state.current?.assignments || [];
  const machines = state.master?.machines || [];
  if (!as.length || !machines.length) {
    svg.innerHTML = `<text x="20" y="40">暂无排产结果，点击右上角“开始排产”。</text>`;
    svg.setAttribute("width", 600); svg.setAttribute("height", 120);
    return;
  }
  const priorities = {};
  (state.master.orders || []).forEach((o) => { priorities[o.code] = o.priority; });
  const t0 = new Date(Math.min(...as.map((a) => +new Date(a.start))));
  const t1 = new Date(Math.max(...as.map((a) => +new Date(a.end))));
  const origin = new Date(t0); origin.setHours(0, 0, 0, 0);
  const wrap = document.querySelector(".gantt-wrap");
  const avail = Math.max(640, (wrap?.clientWidth || 1000) - 124);
  const hours = Math.max(24, (t1 - origin) / 3600000 + 4);
  const pxHour = Math.max(9, Math.min(40, avail / hours));
  const labelW = 104, rowH = 34, headH = 28, padB = 12;
  const width = labelW + hours * pxHour;
  const height = headH + machines.length * rowH + padB;
  const X = (d) => labelW + (d - origin) / 3600000 * pxHour;

  let out = `<svg id="gantt" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;
  const days = [];
  for (let d = new Date(origin); d <= t1; d = new Date(+d + 86400000)) days.push(new Date(d));
  days.forEach((d) => {
    const x = X(d);
    out += `<line class="day-line" x1="${x}" y1="${headH}" x2="${x}" y2="${height - padB}"/>`;
    out += `<text x="${x + 4}" y="${headH - 8}">${d.getMonth() + 1}/${d.getDate()}</text>`;
  });
  machines.forEach((m, i) => {
    const y = headH + i * rowH;
    out += `<line class="row-line" x1="0" y1="${y}" x2="${width}" y2="${y}"/>`;
    out += `<text x="8" y="${y + rowH / 2 + 4}">${esc(m.code)}</text>`;
  });
  as.forEach((a) => {
    const row = machines.findIndex((m) => m.code === a.machine_code);
    if (row < 0) return;
    const x = X(new Date(a.start));
    const w = Math.max(3, X(new Date(a.end)) - x);
    const y = headH + row * rowH + 4;
    const pr = priorities[a.order_code] || 2;
    let fill = pr === 3 ? "#f26b6b" : pr === 2 ? "#f5b544" : "#4f8cff";
    let stroke = "rgba(0,0,0,.25)";
    if (a.status === "done") { fill = "#34d399"; stroke = "#0b3b2c"; }
    else if (a.status === "doing") { fill = "#22d3ee"; }
    out += `<rect class="bar" data-id="${a.id}" x="${x.toFixed(1)}" y="${y}" width="${Math.max(3, w - 1.5).toFixed(1)}" height="${rowH - 9}" rx="3" fill="${fill}" stroke="${stroke}">
      <title>${esc(a.order_code)} · ${esc(a.op_name)}
设备 ${esc(a.machine_code)} · 人员 ${esc(a.operator_code)}
${fmt(a.start)} → ${fmt(a.end)} · ${a.quantity} 件 · ${a.status}</title></rect>`;
    if (w > 56) {
      out += `<text class="bar-label" x="${x + 4}" y="${y + rowH / 2 - 1}" pointer-events="none">${esc(a.order_code.slice(-4))}</text>`;
      if (w > 90) out += `<text class="bar-label" x="${x + 4}" y="${y + rowH / 2 + 10}" style="fill:#07182a;font-size:8px" pointer-events="none">${esc(a.op_name.slice(0, 10))}</text>`;
    }
  });
  svg.outerHTML = out + `</svg>`;
  // 重新绑定（outerHTML 替换后重新取节点）
  $("#gantt").addEventListener("click", (e) => {
    const rect = e.target.closest("rect.bar");
    if (rect) openReport(Number(rect.dataset.id));
  });
}

function renderAdvice() {
  const a = state.advice || {};
  const cur = a.current?.kpis || {};
  if (!a.strategy) { $("#advice").innerHTML = `<p class="rationale">正在分析订单结构与产能负荷…</p>`; return; }
  const factors = (a.factors || []).map((f) => `
    <div class="factor"><span>${esc(f.name)}：<b>${esc(f.value)}</b></span><span>${esc(f.reading)}</span></div>`).join("");
  $("#advice").innerHTML = `
    <h3>排产策略建议 <span class="tag">置信度 ${pct(a.confidence)}</span></h3>
    <div class="strategy-line"><strong>${esc(a.label)}</strong><span>${esc(a.strategy)}</span></div>
    <p class="rationale">${esc(a.rationale)}</p>
    <div class="factors">${factors}</div>
    <button class="primary" id="btnAdopt">按该策略重新排产</button>
    <div class="factor" style="margin-top:8px"><span>当前版本：<b>${esc(a.current?.strategy || "未排产")}</b></span><span>${cur.orders_late != null ? `拖期 ${cur.orders_late} 单` : ""}</span></div>`;
  $("#btnAdopt").onclick = () => doSolve("auto");
}

function renderRadar() {
  const r = state.radar || {};
  const risks = (r.risks || []).slice(0, 5);
  const bn = (r.bottleneck || []).slice(0, 3);
  const body = risks.length ? risks.map((x) => `
    <div class="risk"><span class="lv ${esc(x.level)}">${esc(x.level)}</span>
      <span class="code">${esc(x.order_code)}</span>
      <span class="reason">${esc(x.reason)}</span></div>`).join("") : `<div class="risk"><span class="lv 低">低</span><span>暂无订单风险</span></div>`;
  const bnHtml = bn.map((b) => `<div class="factor"><span>${esc(b.code)} 利用率 <b>${pct(b.utilization)}</b></span><span>预计排队 ${num(b.queue_hours, 1)} h</span></div>`).join("");
  const sug = (r.suggestions || []).slice(0, 2).map((s) => `<div class="factor"><span>· ${esc(s)}</span></div>`).join("");
  $("#radar").innerHTML = `<h3>交付风险雷达</h3><div class="risk-list">${body}</div>
    <div style="margin-top:8px">${bnHtml}</div>${sug}`;
}

function renderOrders() {
  const orders = state.master?.orders || [];
  const as = state.current?.assignments || [];
  const risks = {};
  (state.radar?.risks || []).forEach((r) => { risks[r.order_code] = r; });
  const rows = orders.map((o) => {
    const mine = as.filter((a) => a.order_code === o.code);
    const planned = mine.length ? mine.reduce((m, a) => (new Date(a.end) > new Date(m) ? a.end : m), mine[0].end) : null;
    const done = mine.length && mine.every((a) => a.status === "done");
    const risk = risks[o.code];
    const lv = risk ? risk.level : "-";
    const cls = lv === "高" ? "bad" : lv === "中" ? "warn" : lv === "低" ? "ok" : "mute";
    return `<tr>
      <td>${esc(o.code)}</td><td>${esc(o.product_code)} ${esc(o.product_name)}</td>
      <td>${o.qty}</td><td>${esc(o.customer)}</td>
      <td>${fmt(o.due_date)}</td><td>${fmt(planned)}</td>
      <td><span class="pill ${done ? "ok" : "mute"}">${done ? "已完工" : (mine.some((a) => a.status === "doing") ? "执行中" : "已排产")}</span></td>
      <td><span class="pill ${cls}">${esc(risk ? risk.level + " · " + risk.reason : "未排产")}</span></td>
    </tr>`;
  }).join("");
  $("#tab-orders").innerHTML = `<table><thead><tr><th>订单号</th><th>产品</th><th>数量</th><th>客户</th><th>交期</th><th>计划完工</th><th>状态</th><th>交付风险</th></tr></thead><tbody>${rows || `<tr><td colspan="8">暂无订单</td></tr>`}</tbody></table>`;
}

function renderMachines() {
  const ms = state.master?.machines || [];
  const util = state.current?.version?.metrics?.kpis?.utilization || {};
  const rows = ms.map((m) => `<tr><td>${esc(m.code)}</td><td>${esc(m.name)}</td><td>${esc(m.machine_type)}</td>
    <td>${"★".repeat(m.precision_grade)}</td><td>${esc(m.status)}</td><td>${pct(util[m.code])}</td></tr>`).join("");
  $("#tab-machines").innerHTML = `<table><thead><tr><th>设备</th><th>名称</th><th>类型</th><th>精度</th><th>状态</th><th>利用率</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderWorkers() {
  const ws = state.master?.workers || [];
  const types = ["mill", "lathe", "boring", "grind", "edm", "inspect", "heat"];
  const head = `<tr><th>人员</th><th>等级</th><th>班次</th>${types.map((t) => `<th>${t}</th>`).join("")}</tr>`;
  const rows = ws.map((w) => `<tr><td>${esc(w.name)} (${esc(w.code)})</td><td>L${w.level}</td><td>${esc(w.shift)}</td>
    ${types.map((t) => `<td>${w.skills && w.skills[t] ? `<span class="skill hi">L${w.skills[t]}</span>` : `<span class="skill">—</span>`}</td>`).join("")}</tr>`).join("");
  const count = {};
  types.forEach((t) => { count[t] = ws.filter((w) => (w.skills || {})[t] >= 3).length; });
  $("#tab-workers").innerHTML = `<table><thead>${head}</thead><tbody>${rows}</tbody></table>
    <div class="diff-box">技能覆盖（≥L3）：${types.map((t) => `${t} ${count[t]} 人`).join(" · ")}</div>`;
}

function renderVersions() {
  const vs = state.versions || [];
  const rows = vs.map((v) => `<tr>
    <td>#${v.id}</td><td>${fmt(v.created_at)}</td><td>${esc(v.strategy)}</td><td>${esc(v.solver)}</td>
    <td>${pct(v.metrics?.kpis?.on_time_rate)}</td><td>${v.metrics?.kpis?.orders_late ?? "-"}</td>
    <td>${num(v.solve_seconds, 1)}s</td><td>${esc(v.note || "-")}</td>
    <td>${v.parent_id ? `<button class="ghost" data-diff="${v.parent_id}" data-target="${v.id}">对比上一版</button>` : ""}</td></tr>`).join("");
  $("#tab-versions").innerHTML = `<table><thead><tr><th>版本</th><th>时间</th><th>策略</th><th>求解器</th><th>按期率</th><th>拖期</th><th>耗时</th><th>说明</th><th></th></tr></thead>
    <tbody>${rows || `<tr><td colspan="9">暂无版本</td></tr>`}</tbody></table>
    <div id="diffBox" class="diff-box"></div>`;
  $("#tab-versions").querySelectorAll("button[data-diff]").forEach((b) => {
    b.onclick = async () => {
      const diff = await api(`/api/schedule/diff?base=${b.dataset.diff}&target=${b.dataset.target}`);
      $("#diffBox").innerHTML = `版本 #${diff.base} → #${diff.target}：<b>${diff.summary.changed}</b> 道工序改期/换机，${diff.summary.added} 道新增，${diff.summary.removed} 道移出，${diff.summary.kept} 道保持不变。
        ${(diff.changed || []).slice(0, 4).map((c) => `<br>· ${esc(c.order_code)} ${esc(c.op_name)}：${esc(c.before.machine)}/${esc(c.before.start)} → ${esc(c.after.machine)}/${esc(c.after.start)}`).join("")}`;
    };
  });
}

function openReport(id) {
  const a = (state.current?.assignments || []).find((x) => x.id === id);
  if (!a) return;
  $("#modalTitle").textContent = `${a.order_code} · ${a.op_name}`;
  $("#modalBody").innerHTML = `
    <div>设备 <b>${esc(a.machine_code)}</b> · 人员 <b>${esc(a.operator_code)}</b> · 计划 ${fmt(a.start)} → ${fmt(a.end)}</div>
    <div>计划数量 ${a.quantity} 件 · 已报工 ${a.good_qty} 件 · 状态 ${a.status}</div>
    <label>本次良品数量</label><input id="goodQty" type="number" min="0" value="${Math.max(a.quantity - a.good_qty, 0)}" />
    <label>本次报废数量</label><input id="scrapQty" type="number" min="0" value="0" />`;
  $("#modal").classList.remove("hidden");
  $("#modalSubmit").onclick = async () => {
    const good = Number($("#goodQty").value || 0);
    const scrap = Number($("#scrapQty").value || 0);
    await api("/api/progress/report", { method: "POST", body: JSON.stringify({ assignment_id: a.id, good_qty: good, scrap_qty: scrap }) });
    $("#modal").classList.add("hidden");
    toast("报工已记录");
    await refresh();
  };
}

async function doSolve(strategy) {
  const btn = $("#btnSolve");
  btn.disabled = true; btn.textContent = "求解中…";
  try {
    const v = await api("/api/schedule/solve", { method: "POST", body: JSON.stringify({ strategy, time_limit: 8, note: "看板手动排产" }) });
    toast(`排产完成：版本 #${v.id}，按期率 ${pct(v.metrics.kpis.on_time_rate)}，拖期 ${v.metrics.kpis.orders_late} 单`);
    await refresh();
  } catch (e) {
    toast("排产失败：" + e.message.slice(0, 80));
  } finally {
    btn.disabled = false; btn.textContent = "开始排产";
  }
}

async function refresh() {
  state.current = await api("/api/schedule/current");
  state.versions = await api("/api/schedule/versions");
  state.radar = await api("/api/ai/radar");
  state.advice = await api("/api/ai/advice");
  renderBadge(); renderKPI(); renderGantt(); renderRadar(); renderAdvice(); renderOrders(); renderVersions();
}

function appendMsg(text, who) {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.textContent = text;
  $("#chatLog").appendChild(div);
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
}

async function sendChat(text) {
  appendMsg(text, "user");
  $("#chatInput").value = "";
  try {
    const r = await api("/api/ai/chat", { method: "POST", body: JSON.stringify({ message: text, execute: true }) });
    appendMsg(r.reply || "（无回复）", "bot");
    if (r.executed) await refresh();
    if (r.radar) renderRadarFrom(r.radar);
  } catch (e) {
    appendMsg("指令执行失败：" + e.message.slice(0, 100), "bot");
  }
}

function renderRadarFrom(r) {
  state.radar = r;
  renderRadar();
}

// 事件绑定
$("#btnSolve").onclick = () => doSolve($("#strategy").value);
$("#chatForm").addEventListener("submit", (e) => { e.preventDefault(); const t = $("#chatInput").value.trim(); if (t) sendChat(t); });
$("#modalCancel").onclick = () => $("#modal").classList.add("hidden");
document.querySelectorAll(".tabs button").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    document.querySelectorAll(".tab-body").forEach((x) => x.classList.add("hidden"));
    $(`#tab-${b.dataset.tab}`).classList.remove("hidden");
  };
});
document.querySelectorAll("button[data-sim]").forEach((b) => {
  b.onclick = () => {
    const map = {
      breakdown: "CNC-M03 故障停机 4 小时，重新排产",
      urgent: "PO20261127002 加急，重新排产",
      leave: "张伟请假 8 小时，重新排产",
    };
    sendChat(map[b.dataset.sim]);
  };
});

loadAll().catch((e) => toast("加载失败：" + e.message));
