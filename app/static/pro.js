/* 生产化前端增强：登录、计划审批发布、派工单与 OEE。在 app.js 之前加载。 */
(() => {
  const TOKEN_KEY = "aps_token";
  let user = null;
  let me = null;

  const token = () => localStorage.getItem(TOKEN_KEY) || "";
  const $ = (s) => document.querySelector(s);

  // 登录层样式（避免改动主样式表，随增强脚本一起下发）
  const style = document.createElement("style");
  style.textContent = `
    #loginMask { position: fixed; inset: 0; z-index: 80; display: grid; place-items: center;
      background: radial-gradient(900px 500px at 30% 0%, #16233a 0%, #0b1220 60%); }
    .login-card { width: min(380px, 92vw); background: #131f33; border: 1px solid #223049;
      border-radius: 14px; padding: 22px 24px; display: flex; flex-direction: column; gap: 6px;
      color: #e6edf7; font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
      box-shadow: 0 24px 60px rgba(0,0,0,.5); }
    .login-card h2 { margin: 0 0 4px; font-size: 17px; }
    .login-sub { margin: 0 0 10px; color: #8ea3bf; font-size: 12px; line-height: 1.5; }
    .login-card label { color: #8ea3bf; font-size: 11px; margin-top: 8px; }
    .login-card input { background: #101b2d; border: 1px solid #223049; border-radius: 8px;
      padding: 9px 12px; color: #e6edf7; font: inherit; }
    .login-card button { margin-top: 14px; padding: 10px; border-radius: 8px; border: 1px solid #3a6fe0;
      background: linear-gradient(180deg,#4f8cff,#3a6fe0); color: #fff; font-weight: 600; cursor: pointer; }
    .login-msg { color: #f26b6b; font-size: 12px; min-height: 18px; margin-top: 6px; }
    .login-hint { color: #6c82a3; font-size: 11px; margin: 10px 0 0; }
    .grid { align-items: start; }
    .gantt-card { align-self: start; }
    @media (max-width: 640px) { #tab-dispatch table { font-size: 11px; } }
  `;
  document.head.appendChild(style);

  // 统一注入令牌；401 时回到登录层
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const opts = { ...init };
    const url = typeof input === "string" ? input : input.url;
    if (url.startsWith("/api") && !token() && !url.includes("/auth/login")) {
      // 未登录时不打真实请求，避免控制台报错与后端噪音
      return new Response(JSON.stringify({ detail: "未登录" }), {
        status: 401, headers: { "Content-Type": "application/json" },
      });
    }
    if (url.startsWith("/api") && token()) {
      opts.headers = { ...(init.headers || {}), Authorization: `Bearer ${token()}` };
    }
    const res = await nativeFetch(input, opts);
    if (res.status === 401 && url.startsWith("/api") && !url.includes("/auth/login")) {
      showLogin("登录已失效，请重新登录");
    }
    return res;
  };

  function toastSafe(msg) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(el._pt);
    el._pt = setTimeout(() => el.classList.add("hidden"), 3800);
  }

  function showLogin(message = "") {
    if (document.getElementById("loginMask")) {
      if (message) document.getElementById("loginMsg").textContent = message;
      return;
    }
    const mask = document.createElement("div");
    mask.id = "loginMask";
    mask.innerHTML = `
      <form id="loginForm" class="login-card">
        <h2>精密加工 AI 排产 · 登录</h2>
        <p class="login-sub">账号由车间管理员分配，角色决定可操作范围。</p>
        <label>账号</label><input id="loginUser" autocomplete="username" required />
        <label>口令</label><input id="loginPass" type="password" autocomplete="current-password" required />
        <button class="primary" type="submit">登录</button>
        <div id="loginMsg" class="login-msg">${message || ""}</div>
        <p class="login-hint">初始管理员：admin，口令取自部署时的 ADMIN_PASSWORD。</p>
      </form>`;
    document.body.appendChild(mask);
    document.getElementById("loginForm").onsubmit = async (e) => {
      e.preventDefault();
      const body = { username: $("#loginUser").value.trim(), password: $("#loginPass").value };
      const res = await nativeFetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        document.getElementById("loginMsg").textContent = data.detail || "登录失败";
        return;
      }
      localStorage.setItem(TOKEN_KEY, data.token);
      location.reload();
    };
  }

  const roleAllows = (...roles) => me && roles.includes(me.role);

  async function call(path, method = "GET", body) {
    const res = await window.fetch(path, {
      method, headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.status);
    return data;
  }

  async function loadMe() {
    me = await call("/api/auth/me");
    user = me.username;
    const chip = $("#userChip");
    if (chip) chip.textContent = `${me.full_name || me.username} · ${me.role}`;
    if (me.must_change_password) {
      toastSafe("当前为初始口令，请在账号管理中尽快修改");
    }
  }

  function addActions() {
    const actions = $(".actions");
    if (!actions || document.getElementById("btnSubmitPlan")) return;
    const current = async () => (await call("/api/plan/state")).version || {};
    const act = (label, path, cls = "ghost") => {
      const b = document.createElement("button");
      b.className = cls;
      b.textContent = label;
      b.onclick = async () => {
        try {
          const v = await current();
          if (!v.id) throw new Error("当前没有排产版本");
          await call(`/api/plan/${v.id}/${path}`, "POST", { note: `${label}（${me.role}）` });
          toastSafe(`${label}成功：版本 #${v.id}`);
          renderPlanState();
        } catch (err) {
          toastSafe(`${label}失败：${err.message}`);
        }
      };
      return b;
    };
    if (roleAllows("planner", "admin")) {
      actions.appendChild(act("提交审批", "submit"));
      actions.appendChild(act("发布计划", "publish", "primary"));
    }
  }

  function addTab() {
    if (document.getElementById("tab-dispatch")) return;
    const tabs = $(".tabs");
    if (!tabs) return;
    const btn = document.createElement("button");
    btn.dataset.tab = "dispatch";
    btn.textContent = "派工与 OEE";
    tabs.appendChild(btn);
    const body = document.createElement("div");
    body.id = "tab-dispatch";
    body.className = "tab-body hidden";
    const parent = tabs.parentElement;
    parent.appendChild(body);
    btn.onclick = () => {
      document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-body").forEach((x) => x.classList.add("hidden"));
      body.classList.remove("hidden");
      renderDispatch();
    };
  }

  async function renderDispatch() {
    const box = document.getElementById("tab-dispatch");
    if (!box) return;
    try {
      const [d, o, c, p] = await Promise.all([
        call("/api/dispatch"), call("/api/oee"), call("/api/capacity?days=7"), call("/api/plan/state"),
      ]);
      const state = (p.version && p.version.status) || "-";
      const oee = o.overall || {};
      const cards = (d.cards || []).map((x) => `<tr>
        <td>${x.machine}</td><td>${x.order_code}</td><td>${x.op_name}</td><td>${x.operator}</td>
        <td>${x.start.slice(5)} → ${x.end.slice(5)}</td><td>${x.quantity}</td>
        <td>${x.changeover ? '<span class="pill warn">需换型</span>' : '<span class="pill mute">连续</span>'}</td>
        <td>${x.frozen ? '<span class="pill ok">已冻结</span>' : '<span class="pill mute">可重排</span>'}</td>
        <td><span class="pill ${x.status === "done" ? "ok" : x.status === "doing" ? "warn" : "mute"}">${x.status}</span></td>
      </tr>`).join("");
      const oeeRows = (o.machines || []).map((m) => `<tr><td>${m.machine}</td>
        <td>${(m.availability * 100).toFixed(0)}%</td><td>${(m.performance * 100).toFixed(0)}%</td>
        <td>${(m.quality * 100).toFixed(0)}%</td><td><b>${(m.oee * 100).toFixed(0)}%</b></td>
        <td>${m.scheduled_hours}h</td><td>${m.downtime_hours}h</td></tr>`).join("");
      const capRows = (c.days || []).map((day) => `<tr><td>${day.date.slice(5)}</td>
        ${(c.summary.machines || []).map((mm) => `<td>${(day.utilization[mm] * 100).toFixed(0)}%</td>`).join("")}</tr>`).join("");
      box.innerHTML = `
        <div class="diff-box">计划状态：<b>${state}</b> · 派工日期 ${d.date || "-"} · 当班 ${d.summary?.tasks || 0} 道工序，
          ${d.summary?.overdue || 0} 道已超时 · 换型 ${d.summary?.changeovers || 0} 次</div>
        <h3>当日派工单</h3>
        <table><thead><tr><th>设备</th><th>订单</th><th>工序</th><th>人员</th><th>时间</th><th>数量</th><th>换型</th><th>冻结</th><th>状态</th></tr></thead>
        <tbody>${cards || `<tr><td colspan="9">当班无派工</td></tr>`}</tbody></table>
        <h3>设备 OEE</h3>
        <div class="diff-box">整体 OEE <b>${((oee.oee || 0) * 100).toFixed(0)}%</b>（可用 ${((oee.availability || 0) * 100).toFixed(0)}% ·
          性能 ${((oee.performance || 0) * 100).toFixed(0)}% · 良品 ${((oee.quality || 0) * 100).toFixed(0)}%）</div>
        <table><thead><tr><th>设备</th><th>可用率</th><th>性能率</th><th>良品率</th><th>OEE</th><th>计划工时</th><th>停机</th></tr></thead>
        <tbody>${oeeRows}</tbody></table>
        <h3>未来 7 天产能负荷</h3>
        <table><thead><tr><th>日期</th>${(c.summary.machines || []).map((m) => `<th>${m}</th>`).join("")}</tr></thead>
        <tbody>${capRows}</tbody></table>
        <div class="diff-box">${(o.basis || "")}</div>`;
    } catch (err) {
      box.innerHTML = `<div class="diff-box">加载失败：${err.message}</div>`;
    }
  }

  async function renderPlanState() {
    try {
      const p = await call("/api/plan/state");
      const v = p.version || {};
      const badge = document.getElementById("versionBadge");
      if (badge && v.id) badge.textContent = `版本 #${v.id} · ${v.strategy} · ${v.status}`;
    } catch (e) { /* 静默 */ }
  }

  document.addEventListener("DOMContentLoaded", async () => {
    if (!token()) {
      showLogin();
      return;
    }
    try {
      await loadMe();
    } catch (err) {
      localStorage.removeItem(TOKEN_KEY);
      showLogin("请重新登录");
      return;
    }
    addActions();
    addTab();
    setTimeout(renderPlanState, 1200);
  });
})();
