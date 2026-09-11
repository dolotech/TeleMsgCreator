/* TeleMsgCreator Web 编辑器前端逻辑（原生 JS，无构建步骤） */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const state = {
    media: [],
    rows: [[{ text: "", type: "url", value: "", style: "" }]],
    previewTimer: null,
  };

  function toast(message, kind) {
    const el = $("#toast");
    el.textContent = message;
    el.className = "toast show " + (kind || "");
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = "toast " + (kind || ""); }, 4200);
  }

  /* 统一的失败提示：后端会把 Telegram 的英文错误翻译成「问题 + 怎么办」 */
  function toastError(data, fallback) {
    const message = data.error || data.detail || fallback || "操作失败";
    const text = typeof message === "string" ? message : JSON.stringify(message);
    toast(data.hint ? text + "　👉 " + data.hint : text, "err");
    if (data.action === "open_settings") openSetup(1);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  /* 后端一律用 UTC 存时间，展示时必须转回浏览器本地时区，
     否则「我明明选了 09:00 却显示 01:00」。 */
  function fmtTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (isNaN(date.getTime())) return String(value).slice(0, 16).replace("T", " ");
    const pad = (n) => String(n).padStart(2, "0");
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
      " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
  }

  async function api(path, options) {
    const res = await fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" },
    }, options || {}));
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { ok: false, error: text }; }
    if (data.ok === undefined) data.ok = res.ok;
    return data;
  }

  // ------------------------------------------------------------- draft
  function collectDraft() {
    const chat = $("#chatId").value.trim();
    const text = $("#bodyText").value;
    const parseMode = $("#parseMode").value || null;
    const kind = $("#mediaKind").value;

    const draft = {
      chat_id: chat || null,
      parse_mode: text.trim() ? parseMode : null,
      text: null,
      disable_notification: $("#silent").checked,
      protect_content: $("#protect").checked,
    };

    const threadId = $("#threadId").value.trim();
    if (threadId) draft.message_thread_id = parseInt(threadId, 10);

    if (state.media.length === 1) {
      draft.media = {
        kind: state.media[0].kind || kind,
        source: state.media[0].path,
        has_spoiler: $("#spoiler").checked,
      };
      if (text.trim()) { draft.media.caption = text; draft.media.parse_mode = parseMode; }
      draft.parse_mode = null;
    } else if (state.media.length > 1) {
      draft.parse_mode = null;
      draft.media_group = state.media.slice(0, 10).map((m, i) => {
        const item = { kind: m.kind || "photo", source: m.path };
        if (i === 0 && text.trim()) { item.caption = text; item.parse_mode = parseMode; }
        return item;
      });
    } else {
      draft.text = text;
    }

    const rows = state.rows
      .map((row) => row.filter((b) => b.text.trim()).map(toButton).filter(Boolean))
      .filter((row) => row.length);
    if (rows.length) draft.keyboard = { rows: rows };
    return draft;
  }

  function toButton(b) {
    const button = { text: b.text.trim() };
    if (b.style) button.style = b.style;
    const value = b.value.trim();
    switch (b.type) {
      case "url": return value ? Object.assign(button, { url: value }) : null;
      case "callback": return value ? Object.assign(button, { callback_data: value }) : null;
      case "copy": return value ? Object.assign(button, { copy_text: { text: value } }) : null;
      case "switch": return Object.assign(button, { switch_inline_query: value });
      case "web_app": return value ? Object.assign(button, { web_app: { url: value } }) : null;
      case "pay": return Object.assign(button, { pay: true });
      default: return null;
    }
  }

  // ----------------------------------------------------------- preview
  function schedulePreview() {
    clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(refreshPreview, 320);
  }

  async function refreshPreview() {
    updateCounters();
    const draft = collectDraft();
    const isEmpty = !draft.media && !draft.media_group && !(draft.text || "").trim();
    if (isEmpty) {
      $("#previewHost").innerHTML =
        '<div class="tg-bubble tg-empty"><span>在这里开始写正文，或拖入一张图片…</span></div>';
      renderIssues([], [], "还没有内容，写点什么就能看到预览");
      return;
    }
    const data = await api("/api/preview", { method: "POST", body: JSON.stringify({ draft: draft }) });
    if (!data.ok) {
      toastError(data, "草稿不合法");
      return;
    }
    $("#previewHost").innerHTML = data.html;
    renderIssues(
      (data.report.errors || []).concat(data.report.warnings || []),
      data.report.notes || []
    );
  }

  function renderIssues(issues, notes, emptyMessage) {
    const host = $("#issues");
    const noteList = notes || [];
    if (!issues.length && !noteList.length) {
      host.innerHTML = '<li class="ok muted">' + escapeHtml(emptyMessage || "✔ 没有发现问题，可以发送") + "</li>";
      return;
    }
    const issuesHtml = issues.map((i) =>
      '<li class="' + escapeHtml(i.severity) + '"><span class="f">' + escapeHtml(i.field) + "</span><br>" +
      escapeHtml(i.message) +
      (i.hint ? '<br><span class="muted">建议：' + escapeHtml(i.hint) + "</span>" : "") + "</li>").join("");
    const notesHtml = noteList.map((n) =>
      '<li class="note">ℹ ' + escapeHtml(n) + "</li>").join("");
    host.innerHTML = (issuesHtml || '<li class="ok muted">✔ 没有发现问题，可以发送</li>') + notesHtml;
  }

  function updateCounters() {
    const text = $("#bodyText").value;
    const hasMedia = state.media.length > 0;
    const limit = hasMedia ? window.TELEMSG_LIMITS.caption : window.TELEMSG_LIMITS.text;
    const el = $("#counter");
    el.textContent = text.length + " / " + limit + (hasMedia ? "（caption）" : "（正文）");
    el.classList.toggle("over", text.length > limit);
  }

  // ------------------------------------------------------------- media
  function renderMedia() {
    const host = $("#mediaList");
    host.innerHTML = state.media.map((m, idx) => {
      const thumb = m.kind === "photo"
        ? '<img class="thumb" src="' + escapeHtml(m.url) + '" alt="">'
        : '<span class="thumb" style="display:inline-flex;width:34px;height:34px;align-items:center;justify-content:center">📎</span>';
      return "<li>" + thumb +
        '<span class="name">' + escapeHtml(m.filename) + "</span>" +
        '<span class="muted">' + Math.round((m.size || 0) / 1024) + " KB</span>" +
        '<button class="icon danger" data-remove="' + idx + '">✕</button></li>';
    }).join("");
    $$("#mediaList [data-remove]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.media.splice(parseInt(btn.dataset.remove, 10), 1);
        renderMedia();
        schedulePreview();
      });
    });
    const hint = $("#groupHint");
    if (state.media.length > 1) {
      hint.textContent = "媒体组模式：" + state.media.length + " 项（最多 10 项，且 Telegram 不支持带按钮）";
      $("#spoiler").disabled = true;
    } else {
      hint.textContent = state.media.length === 1 ? "单媒体模式：可以带内联按钮" : "";
      $("#spoiler").disabled = false;
    }
  }

  async function uploadFiles(files) {
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      let data;
      try {
        const res = await fetch("/api/upload", { method: "POST", body: form });
        data = await res.json();
      } catch (e) {
        data = { ok: false, detail: "上传请求失败" };
      }
      if (!data.ok) { toast(data.detail || data.error || "上传失败", "err"); continue; }
      state.media.push(data);
    }
    renderMedia();
    schedulePreview();
  }

  function addMediaByUrl() {
    const input = $("#mediaUrl");
    const value = input.value.trim();
    if (!value) { toast("请先粘贴 URL 或 file_id", "err"); return; }
    const isUrl = /^https?:\/\//i.test(value);
    const kind = $("#mediaKind").value;
    const inferred = /\.(gif)$/i.test(value) ? "animation"
      : /\.(mp4|mov|webm|m4v)$/i.test(value) ? "video"
      : /\.(pdf|zip|txt|docx?|xlsx?)$/i.test(value) ? "document" : kind;
    state.media.push({
      path: value,
      filename: isUrl ? value.split("/").pop() || value : "file_id:" + value.slice(0, 16),
      size: 0,
      kind: inferred,
      url: isUrl ? value : "",
    });
    input.value = "";
    renderMedia();
    schedulePreview();
  }

  // ----------------------------------------------------------- buttons
  function buttonFields(b, r, c) {
    const types = [
      ["url", "跳转链接"],
      ["callback", "回调数据"],
      ["copy", "复制文本"],
      ["switch", "分享/内联"],
      ["web_app", "Web App"],
      ["pay", "支付按钮"],
    ];
    const styles = [["", "默认配色"], ["primary", "蓝色"], ["success", "绿色"], ["danger", "红色"]];
    const options = (list, selected) => list.map(([v, label]) =>
      '<option value="' + v + '"' + (v === selected ? " selected" : "") + ">" + label + "</option>").join("");
    const placeholders = {
      url: "https://example.com",
      callback: "callback_data（≤64 字节）",
      copy: "点击后复制的文本",
      switch: "内联查询内容，可留空",
      web_app: "https://app.example.com",
      pay: "无需填写",
    };
    return (
      '<input class="full" data-row="' + r + '" data-col="' + c + '" data-field="text" placeholder="按钮文案"' +
        ' value="' + escapeHtml(b.text) + '">' +
      '<select data-row="' + r + '" data-col="' + c + '" data-field="type">' + options(types, b.type) + "</select>" +
      '<select data-row="' + r + '" data-col="' + c + '" data-field="style">' + options(styles, b.style || "") + "</select>" +
      '<input class="full" data-row="' + r + '" data-col="' + c + '" data-field="value" placeholder="' +
        escapeHtml(placeholders[b.type] || "") + '" value="' + escapeHtml(b.value) + '"' +
        (b.type === "pay" ? " disabled" : "") + ">"
    );
  }

  function renderRows() {
    const host = $("#buttonRows");
    host.innerHTML = state.rows.map((row, r) => (
      '<div class="button-row" data-row="' + r + '">' +
        '<div class="fields">' + row.map((b, c) => buttonFields(b, r, c)).join("") + "</div>" +
        '<div style="display:flex;flex-direction:column;gap:4px">' +
          '<button class="icon" data-add-col="' + r + '" title="本行再加一个按钮">＋</button>' +
          '<button class="icon danger" data-del-row="' + r + '" title="删除本行">✕</button>' +
        "</div>" +
      "</div>"
    )).join("") +
      '<div class="row tight" style="margin-top:6px"><button id="addRow">＋ 新增一行按钮</button></div>';

    $$("#buttonRows input, #buttonRows select").forEach((el) => {
      const handler = (rerender) => () => {
        const r = parseInt(el.dataset.row, 10);
        const c = parseInt(el.dataset.col, 10);
        state.rows[r][c][el.dataset.field] = el.value;
        if (rerender) renderRows();
        schedulePreview();
      };
      el.addEventListener("input", handler(false));
      el.addEventListener("change", handler(true));
    });
    $$("#buttonRows [data-add-col]").forEach((btn) => btn.addEventListener("click", () => {
      state.rows[parseInt(btn.dataset.addCol, 10)].push({ text: "", type: "url", value: "", style: "" });
      renderRows();
      schedulePreview();
    }));
    $$("#buttonRows [data-del-row]").forEach((btn) => btn.addEventListener("click", () => {
      state.rows.splice(parseInt(btn.dataset.delRow, 10), 1);
      if (!state.rows.length) state.rows = [[{ text: "", type: "url", value: "", style: "" }]];
      renderRows();
      schedulePreview();
    }));
    $("#addRow").addEventListener("click", () => {
      if (state.rows.length >= 20) { toast("最多 20 行按钮", "err"); return; }
      state.rows.push([{ text: "", type: "url", value: "", style: "" }]);
      renderRows();
      schedulePreview();
    });
  }

  // ----------------------------------------------------------- actions
  async function refreshTemplates() {
    const data = await api("/api/templates");
    $("#templateSelect").innerHTML = '<option value="">（选择模板…）</option>' +
      (data.items || []).map((t) => '<option value="' + escapeHtml(t.name) + '">' + escapeHtml(t.name) + "</option>").join("");
  }

  async function loadTemplate(name) {
    const data = await api("/api/templates/" + encodeURIComponent(name));
    if (!data.ok) { toast(data.detail || "模板载入失败", "err"); return; }
    applyDraft(data.draft);
    toast("已载入模板 " + name, "ok");
  }

  function applyDraft(draft) {
    $("#chatId").value = draft.chat_id || "";
    $("#parseMode").value = draft.parse_mode || "HTML";
    const text = draft.text ||
      (draft.media && draft.media.caption) ||
      (draft.media_group && draft.media_group[0] && draft.media_group[0].caption) || "";
    $("#bodyText").value = text;
    $("#silent").checked = !!draft.disable_notification;
    $("#protect").checked = !!draft.protect_content;
    state.media = [];
    if (draft.media) {
      state.media.push({
        path: draft.media.source,
        filename: draft.media.source.split("/").pop(),
        size: 0, kind: draft.media.kind, url: draft.media.source,
      });
    }
    (draft.media_group || []).forEach((m) => state.media.push({
      path: m.source, filename: m.source.split("/").pop(), size: 0, kind: m.kind, url: m.source,
    }));
    const rows = ((draft.keyboard || {}).rows || []).map((row) => row.map((b) => {
      let type = "url";
      let value = b.url || "";
      if (b.callback_data) { type = "callback"; value = b.callback_data; }
      else if (b.copy_text) { type = "copy"; value = b.copy_text.text; }
      else if (b.pay) { type = "pay"; value = ""; }
      else if (b.web_app) { type = "web_app"; value = b.web_app.url; }
      else if (b.switch_inline_query !== undefined) { type = "switch"; value = b.switch_inline_query || ""; }
      return { text: b.text, type: type, value: value, style: b.style || "" };
    }));
    state.rows = rows.length ? rows : [[{ text: "", type: "url", value: "", style: "" }]];
    renderMedia();
    renderRows();
    schedulePreview();
  }

  async function doSend() {
    const draft = collectDraft();
    const btn = $("#sendBtn");
    btn.disabled = true;
    try {
      const data = await api("/api/send", {
        method: "POST",
        body: JSON.stringify({ draft: draft, chat_id: draft.chat_id, dry_run: false }),
      });
      if (data.ok) {
        toast("发送成功：message_id = " + ((data.result.message_ids || []).join(", ") || "-"), "ok");
        (data.result.notes || []).forEach((note) => toast("ℹ " + note));
        refreshHistory();
      } else {
        toastError(data, "发送失败");
      }
    } catch (e) {
      toast("请求异常：" + e.message, "err");
    } finally {
      btn.disabled = false;
    }
  }

  async function doSchedule() {
    const when = $("#scheduleAt").value;
    if (!when) { toast("请先选择定时时间", "err"); return; }
    const draft = collectDraft();
    const data = await api("/api/schedule", {
      method: "POST",
      body: JSON.stringify({
        draft: draft,
        chat_id: draft.chat_id,
        when: new Date(when).toISOString(),
        name: $("#templateName").value || null,
      }),
    });
    if (data.ok) { toast("已排期（本地时间）：" + fmtTime(data.when), "ok"); refreshSchedules(); }
    else toastError(data, "排期失败");
  }

  async function saveTemplate() {
    const name = $("#templateName").value.trim();
    if (!name) { toast("请先填写模板名称", "err"); return; }
    const data = await api("/api/templates", {
      method: "POST",
      body: JSON.stringify({ name: name, draft: collectDraft() }),
    });
    if (data.ok) { toast("模板已保存：" + name, "ok"); refreshTemplates(); }
    else toastError(data, "保存失败");
  }

  async function showJson() {
    const data = await api("/api/compile", {
      method: "POST",
      body: JSON.stringify({ draft: collectDraft() }),
    });
    if (data.ok) {
      $("#jsonOut").textContent = JSON.stringify(data.request, null, 2);
      $("#jsonBox").style.display = "block";
    } else {
      toastError(data, "编译失败");
    }
  }

  async function refreshHistory() {
    const data = await api("/api/history?limit=12");
    $("#history").innerHTML = (data.items || []).map((h) =>
      '<li class="' + (h.ok ? "" : "error") + '">' +
      escapeHtml(fmtTime(h.created_at)) + " → " + escapeHtml(h.chat_id || "-") + " " +
      (h.ok ? '<span class="muted">#' + (h.message_id || "-") + "</span>"
            : "<span style='color:var(--err)'>" + escapeHtml(h.error || "失败") + "</span>") + "</li>").join("");
  }

  async function refreshSchedules() {
    const data = await api("/api/schedules");
    const items = (data.items || []).slice(0, 10);
    $("#schedules").innerHTML = items.length ? items.map((s) =>
      '<li><span class="muted">' + escapeHtml(fmtTime(s.next_run_at)) + "</span> → " +
      escapeHtml(s.chat_id) +
      ' <button class="icon danger" data-cancel="' + escapeHtml(s.id) + '">✕</button></li>').join("")
      : '<li class="muted">暂无计划任务</li>';
    $$("#schedules [data-cancel]").forEach((btn) => btn.addEventListener("click", async () => {
      await api("/api/schedules/" + btn.dataset.cancel, { method: "DELETE" });
      refreshSchedules();
    }));
  }

  // ------------------------------------------------------- 设置向导
  const setupState = { step: 1, botToken: null };

  function openSetup(step) {
    setupState.step = step || (window.TELEMSG_BOOT.hasToken ? 3 : 1);
    $("#setupToken").value = "";
    $("#setupStep1Msg").textContent = window.TELEMSG_BOOT.hasToken
      ? "当前已配置：" + window.TELEMSG_BOOT.tokenHint + "（留空则保持不变）"
      : "";
    $("#setupChat").value = $("#chatId").value || window.TELEMSG_BOOT.defaultChat || "";
    $("#setupModal").hidden = false;
    gotoStep(setupState.step);
  }

  function closeSetup() {
    $("#setupModal").hidden = true;
  }

  function gotoStep(step) {
    setupState.step = step;
    $$("#setupModal .step-body").forEach((el) => {
      el.hidden = Number(el.dataset.step) !== step;
    });
    $$("#setupSteps li").forEach((li) => {
      const n = Number(li.dataset.step);
      li.classList.toggle("active", n === step);
      li.classList.toggle("done", n < step);
    });
    $("#setupTitle").textContent = window.TELEMSG_BOOT.hasToken
      ? "设置"
      : "开始使用 TeleMsgCreator";
  }

  async function saveSettings({ token, chat, persist }) {
    const body = { persist: persist !== false };
    if (token) body.bot_token = token;
    if (chat !== undefined) body.default_chat_id = chat;
    return await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
  }

  async function setupVerify() {
    const token = $("#setupToken").value.trim();
    if (!token && !window.TELEMSG_BOOT.hasToken) {
      $("#setupStep1Msg").textContent = "请先填入 Token";
      return;
    }
    $("#setupNext1").disabled = true;
    $("#setupStep1Msg").textContent = "正在验证…";
    try {
      const data = await saveSettings({ token: token || null, persist: true });
      if (!data.ok) {
        $("#setupStep1Msg").textContent = "";
        const err = $("#setupError");
        err.hidden = false;
        err.innerHTML = "<b>❌ " + escapeHtml(data.error || "验证失败") + "</b>" +
          (data.hint ? "<br><span class='muted'>" + escapeHtml(data.hint) + "</span>" : "") +
          (data.raw ? "<br><span class='muted'>原文：" + escapeHtml(data.raw) + "</span>" : "");
        $("#setupNext1").disabled = false;
        toastError(data, "验证失败");
        return;
      }
      $("#setupError").hidden = true;
      applySettingsResult(data);
      gotoStep(2);
    } catch (e) {
      $("#setupStep1Msg").textContent = "请求失败：" + e.message;
    } finally {
      $("#setupNext1").disabled = false;
    }
  }

  function applySettingsResult(data) {
    const s = data.settings || {};
    window.TELEMSG_BOOT.hasToken = s.has_token;
    window.TELEMSG_BOOT.tokenHint = s.token_hint;
    window.TELEMSG_BOOT.defaultChat = s.default_chat_id || "";
    if (s.default_chat_id !== undefined) $("#chatId").value = s.default_chat_id;
    updateTokenBadge(s);

    const box = $("#setupBotInfo");
    if (data.bot) {
      box.className = "result-box";
      box.innerHTML = "✅ 已验证：<b>@" + escapeHtml(data.bot.username) + "</b>" +
        "<br><span class='muted'>" + escapeHtml(data.bot.first_name || "") + " · id " +
        escapeHtml(String(data.bot.id)) + "</span>" +
        (s.saved_to ? "<br><span class='muted'>已保存到 " + escapeHtml(s.saved_to) + "</span>" : "");
    }
    if (data.chat_check) {
      box.innerHTML += data.chat_check.ok
        ? "<br>✅ 目标频道可访问：<b>" + escapeHtml(data.chat_check.title) + "</b>"
        : "<br>⚠️ 目标频道暂时访问不到：<span class='muted'>" +
          escapeHtml(data.chat_check.error || "") + "</span>";
      if (!data.chat_check.ok) box.className = "result-box warn";
    }
    (data.warnings || []).forEach((w) => {
      box.innerHTML += "<br><span style='color:var(--warn)'>⚠️ " + escapeHtml(w) + "</span>";
    });
  }

  function updateTokenBadge(s) {
    const badges = $$("header .badge");
    if (badges.length < 2) return;
    const badge = badges[1];
    if (s.has_token) {
      badge.className = "badge ok";
      badge.textContent = "Token 已配置 · " + s.token_hint;
    } else {
      badge.className = "badge err";
      badge.textContent = "缺少 Bot Token（点右上角「设置」）";
    }
  }

  async function setupSaveChat(skip) {
    const chat = skip ? "" : $("#setupChat").value.trim();
    const data = await saveSettings({ chat: chat, persist: true });
    if (!data.ok) {
      toastError(data, "保存失败");
      return;
    }
    applySettingsResult(data);
    const done = $("#setupDone");
    done.className = "result-box";
    done.innerHTML =
      "✅ 配置已保存" +
      (data.settings && data.settings.saved_to
        ? "<br><span class='muted'>写入 " + escapeHtml(data.settings.saved_to) + "</span>"
        : "<br><span class='muted'>仅在本次运行内生效</span>") +
      (chat ? "<br>默认发送到：<b>" + escapeHtml(chat) + "</b>" : "") +
      "<br><br>别忘了把机器人加进频道并给「发布消息」权限，否则发送时会报 chat not found。";
    (data.warnings || []).forEach((w) => {
      done.innerHTML += "<br><span style='color:var(--warn)'>⚠️ " + escapeHtml(w) + "</span>";
    });
    gotoStep(4);
  }

  // -------------------------------------------------------------- init
  document.addEventListener("DOMContentLoaded", () => {
    renderMedia();
    renderRows();
    refreshTemplates();
    refreshHistory();
    refreshSchedules();
    schedulePreview();

    ["#bodyText", "#chatId", "#parseMode", "#silent", "#protect", "#spoiler", "#threadId"].forEach((sel) => {
      const el = $(sel);
      if (!el) return;
      el.addEventListener("input", schedulePreview);
      el.addEventListener("change", schedulePreview);
    });

    const zone = $("#dropzone");
    zone.addEventListener("click", () => $("#fileInput").click());
    $("#fileInput").addEventListener("change", (e) => uploadFiles(Array.from(e.target.files)));
    $("#addUrlBtn").addEventListener("click", addMediaByUrl);
    $("#mediaUrl").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); addMediaByUrl(); }
    });
    ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.add("hover");
    }));
    ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => {
      e.preventDefault();
      zone.classList.remove("hover");
    }));
    zone.addEventListener("drop", (e) => {
      if (e.dataTransfer && e.dataTransfer.files.length) uploadFiles(Array.from(e.dataTransfer.files));
    });

    $("#sendBtn").addEventListener("click", doSend);
    $("#dryRunBtn").addEventListener("click", showJson);
    $("#scheduleBtn").addEventListener("click", doSchedule);
    $("#saveTplBtn").addEventListener("click", saveTemplate);
    $("#templateSelect").addEventListener("change", (e) => {
      if (e.target.value) loadTemplate(e.target.value);
    });

    const presets = {
      buy: { text: "🛒 立即购买", type: "url", value: "https://" },
      support: { text: "💬 联系客服", type: "url", value: "https://t.me/" },
      copy: { text: "📋 复制邀请码", type: "copy", value: "INVITE2026" },
      sub: { text: "🔔 订阅频道", type: "url", value: "https://t.me/" },
    };
    $$("[data-preset]").forEach((btn) => btn.addEventListener("click", () => {
      const p = presets[btn.dataset.preset];
      if (!p) return;
      state.rows[state.rows.length - 1].push(Object.assign({ style: "" }, p));
      renderRows();
      schedulePreview();
    }));

    $("#checkBot").addEventListener("click", async () => {
      const data = await api("/api/me");
      if (data.ok) toast("机器人 @" + data.bot.username + " 连接正常", "ok");
      else toastError(data, "连接失败");
    });

    // 设置向导
    $("#openSettings").addEventListener("click", () => openSetup());
    $("#setupClose").addEventListener("click", closeSetup);
    $("#setupModal").addEventListener("click", (e) => {
      if (e.target.id === "setupModal") closeSetup();
    });
    $("#setupNext1").addEventListener("click", setupVerify);
    $("#setupBack2").addEventListener("click", () => gotoStep(1));
    $("#setupNext2").addEventListener("click", () => gotoStep(3));
    $("#setupSkipChat").addEventListener("click", () => setupSaveChat(true));
    $("#setupSaveChat").addEventListener("click", () => setupSaveChat(false));
    $("#setupFinish").addEventListener("click", closeSetup);
    $("#setupToken").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); setupVerify(); }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#setupModal").hidden) closeSetup();
    });

    // 首次进入且没有 token 时，直接把引导摆出来
    if (!window.TELEMSG_BOOT.hasToken) openSetup(1);
  });
})();
