(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    userId: localStorage.getItem("dsh_user_id") || "u_demo",
    sessionId: localStorage.getItem("dsh_session_id") || null,
    assistantId: null,
    streaming: false,
    abort: null,
    pendingInterrupt: null,
    citations: new Map(),
    currentText: "",
    feedbackMessageId: null,
    feedbackRating: null,
  };

  const els = {
    sessionList: $("sessionList"),
    messageList: $("messageList"),
    welcome: $("welcome"),
    input: $("messageInput"),
    form: $("composerForm"),
    send: $("sendBtn"),
    status: $("composerStatus"),
    statusText: $("composerStatusText"),
    stop: $("stopInlineBtn"),
    headerSession: $("headerSession"),
    userIdLabel: $("userIdLabel"),
    citationDrawer: $("citationDrawer"),
    citationOverlay: $("citationOverlay"),
    citationList: $("citationList"),
    feedbackDialog: $("feedbackDialog"),
    feedbackForm: $("feedbackForm"),
    feedbackError: $("feedbackError"),
    toast: $("toast"),
    live: $("liveRegion"),
  };

  const headers = () => ({ "Content-Type": "application/json", "X-User-Id": state.userId });

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderInline(text) {
    return escapeHtml(text)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function renderContent(text, citations) {
    const lines = String(text || "").split("\n");
    return lines
      .map((line) => {
        if (!line.trim()) return "";
        let html = renderInline(line);
        html = html.replace(/\[(\d+)\]/g, (m, n) => {
          const idx = Number(n);
          const has = (citations || []).some((c) => Number(c.index) === idx);
          if (!has) return m;
          return `<button type="button" class="citation-ref" data-cite="${idx}" aria-label="查看引用 ${idx}">[${idx}]</button>`;
        });
        if (/^\s*[-*]\s+/.test(line)) return `<p class="md-li">${html.replace(/^\s*[-*]\s+/, "")}</p>`;
        return `<p>${html}</p>`;
      })
      .join("");
  }

  function toast(message) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => (els.toast.hidden = true), 3200);
  }

  function setStatus(text, busy) {
    els.status.hidden = !busy && !text;
    els.statusText.textContent = text || "";
    els.stop.hidden = !busy;
    state.streaming = !!busy;
    els.send.classList.toggle("is-busy", !!busy);
    els.input.disabled = false;
  }

  function scrollBottom() {
    const container = $("messages");
    container.scrollTop = container.scrollHeight;
  }

  function showWelcome(show) {
    els.welcome.hidden = !show;
  }

  function setBubbleContent(wrap, content, citations) {
    const bubble = wrap.querySelector(".bubble");
    if (!bubble) return;
    bubble.innerHTML = `<div class="msg-text">${renderContent(content, citations)}</div>`;
  }

  function messageElement(role, content, citations, id, status) {
    const wrap = document.createElement("article");
    wrap.className = `message message-${role}`;
    if (id) wrap.dataset.id = id;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.innerHTML = role === "assistant"
      ? '<svg viewBox="0 0 32 32" aria-hidden="true"><use href="#i-cat"/></svg>'
      : "我";

    const main = document.createElement("div");
    main.className = "message-main";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    main.appendChild(bubble);

    if (role === "assistant") {
      const footer = document.createElement("div");
      footer.className = "msg-footer";
      const state = document.createElement("span");
      state.className = "msg-status";
      state.textContent = status === "waiting_user" ? "等待选择" : "";
      const actions = document.createElement("div");
      actions.className = "msg-actions";
      actions.innerHTML = `
        <button type="button" class="msg-action" data-rating="up" aria-label="有帮助" title="有帮助">
          <svg class="icon" aria-hidden="true"><use href="#i-thumb-up"/></svg>
        </button>
        <button type="button" class="msg-action is-down" data-rating="down" aria-label="没帮助" title="没帮助">
          <svg class="icon" aria-hidden="true"><use href="#i-thumb-down"/></svg>
        </button>
        <button type="button" class="msg-action" data-copy aria-label="复制" title="复制">
          <svg class="icon" aria-hidden="true"><use href="#i-copy"/></svg>
        </button>`;
      footer.appendChild(state);
      footer.appendChild(actions);
      main.appendChild(footer);
      wrap.appendChild(avatar);
      wrap.appendChild(main);
      wrap._citations = citations || [];
      wrap._content = content || "";
      setBubbleContent(wrap, content, citations || []);
      bindMessageActions(wrap);
    } else {
      wrap.appendChild(main);
      wrap.appendChild(avatar);
      setBubbleContent(wrap, content, []);
    }
    return wrap;
  }

  function bindMessageActions(wrap) {
    wrap.querySelectorAll(".citation-ref").forEach((btn) => {
      btn.addEventListener("click", () => openCitation(Number(btn.dataset.cite), wrap._citations || []));
    });
    const up = wrap.querySelector('.msg-action[data-rating="up"]');
    const down = wrap.querySelector('.msg-action[data-rating="down"]');
    const copy = wrap.querySelector("[data-copy]");
    if (up) up.addEventListener("click", () => sendFeedback(wrap, "up"));
    if (down) down.addEventListener("click", () => {
      state.feedbackMessageId = wrap.dataset.id;
      state.feedbackRating = "down";
      els.feedbackError.hidden = true;
      els.feedbackForm.reset();
      els.feedbackDialog.showModal();
    });
    if (copy) copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(wrap._content || "");
        toast("已复制");
      } catch {
        toast("复制失败，请手动选择");
      }
    });
  }

  async function sendFeedback(wrap, rating) {
    const id = wrap.dataset.id;
    if (!id) return;
    try {
      const res = await fetch(`/api/v1/messages/${encodeURIComponent(id)}/feedback`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ rating }),
      });
      if (!res.ok) throw new Error("反馈失败");
      toast(rating === "up" ? "感谢反馈" : "已记录");
    } catch {
      toast("反馈提交失败");
    }
  }

  function openCitation(index, citations) {
    const cite = (citations || []).find((c) => Number(c.index) === index);
    if (!cite) return;
    els.citationList.innerHTML = `
      <section class="citation-item">
        <p class="citation-index">[${index}]</p>
        <h3>${escapeHtml(cite.title || "知识库来源")}</h3>
        <p class="citation-source">${escapeHtml(cite.source || "")}${cite.effective_from ? " · 生效 " + escapeHtml(cite.effective_from) : ""}</p>
        <pre>${escapeHtml(cite.content || "")}</pre>
      </section>`;
    els.citationDrawer.hidden = false;
    els.citationOverlay.hidden = false;
    els.citationDrawer.querySelector("button")?.focus();
  }

  function closeCitation() {
    els.citationDrawer.hidden = true;
    els.citationOverlay.hidden = true;
  }

  function renderInterrupt(payload) {
    state.pendingInterrupt = payload;
    const wrap = document.createElement("article");
    wrap.className = "message message-assistant interrupt-card-wrap";
    const body = document.createElement("div");
    body.className = "message-main";
    const card = document.createElement("div");
    card.className = "interrupt-card";
    if (payload.type === "order_select") {
      card.innerHTML = `<h3>${escapeHtml(payload.question || "请选择订单")}</h3>
        <div class="order-options">${(payload.options || []).map((o, i) => `
          <label class="order-option"><input type="radio" name="orderChoice" value="${escapeHtml(o.order_id)}" ${i === 0 ? "checked" : ""}>
          <span><strong>订单 ${escapeHtml(o.order_id)}</strong><small>${escapeHtml(o.title || "")} · ¥${Number(o.paid_amount || 0).toFixed(2)} · ${escapeHtml(o.status || "")}</small></span></label>`).join("")}</div>
        <div class="interrupt-actions"><button type="button" class="btn btn-primary" data-confirm-order>确认</button><button type="button" class="btn btn-ghost" data-cancel>取消</button></div>`;
    } else if (payload.type === "confirm_refund") {
      card.innerHTML = `<h3>确认提交退款申请？</h3>
        <div class="receipt-lines"><div><span>订单</span><strong>${escapeHtml(payload.order_id)}</strong></div>
        <div><span>退款金额</span><strong>¥${Number(payload.amount || 0).toFixed(2)}</strong></div>
        <div><span>原因</span><strong>${escapeHtml(payload.reason || "")}</strong></div>
        <div><span>运费承担</span><strong>${escapeHtml(payload.shipping_payer || "")}</strong></div></div>
        <div class="interrupt-actions"><button type="button" class="btn btn-primary" data-confirm-refund>确认退款</button><button type="button" class="btn btn-ghost" data-cancel>取消</button></div>`;
    } else {
      card.innerHTML = `<p>${escapeHtml(payload.question || "需要你补充信息")}</p><button class="btn btn-ghost" data-cancel>取消</button>`;
    }
    body.appendChild(card);
    wrap.appendChild(body);
    els.messageList.appendChild(wrap);
    scrollBottom();
    card.querySelector("[data-confirm-order]")?.addEventListener("click", () => {
      const selected = card.querySelector('input[name="orderChoice"]:checked');
      if (!selected) return toast("请选择订单");
      submitResume({ order_id: selected.value }, null, wrap);
    });
    card.querySelector("[data-confirm-refund]")?.addEventListener("click", () => {
      submitResume({ confirmed: true }, payload.confirm_token || null, wrap);
    });
    card.querySelector("[data-cancel]")?.addEventListener("click", () => {
      submitResume({ confirmed: false, order_id: null }, null, wrap);
    });
  }

  async function submitResume(response, token, card) {
    if (!state.pendingInterrupt || !state.sessionId) return;
    const interrupt = state.pendingInterrupt;
    state.pendingInterrupt = null;
    if (card) card.remove();
    setStatus("正在处理…", true);
    try {
      await streamRequest("/api/v1/chat/resume", {
        session_id: state.sessionId,
        interrupt_id: interrupt.interrupt_id,
        response,
        confirm_token: token,
      });
    } catch (err) {
      showError(err.message || "恢复失败");
    } finally {
      setStatus("", false);
    }
  }

  function parseSSE(frame) {
    let event = "message";
    const data = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (!data.length) return null;
    try {
      return { event, data: JSON.parse(data.join("\n")) };
    } catch {
      return null;
    }
  }

  async function streamRequest(path, body) {
    const controller = new AbortController();
    state.abort = controller;
    const res = await fetch(path, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) {
      let detail = {};
      try { detail = await res.json(); } catch {}
      throw new Error(detail?.detail?.message || detail?.message || `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || "";
      for (const frame of frames) {
        const parsed = parseSSE(frame);
        if (parsed) handleEvent(parsed);
      }
    }
    if (buffer.trim()) {
      const parsed = parseSSE(buffer);
      if (parsed) handleEvent(parsed);
    }
  }

  function handleEvent({ event, data }) {
    if (event === "meta") {
      state.sessionId = data.session_id;
      state.assistantId = data.message_id;
      state.currentText = "";
      state.citations = new Map();
      localStorage.setItem("dsh_session_id", state.sessionId);
      els.headerSession.textContent = `会话 ${state.sessionId.slice(-6)}`;
      showWelcome(false);
      let msg = document.getElementById(`msg-${data.message_id}`);
      if (!msg) {
        msg = messageElement("assistant", "", [], data.message_id, "streaming");
        msg.id = `msg-${data.message_id}`;
        els.messageList.appendChild(msg);
      } else {
        msg._content = "";
        msg._citations = [];
        msg.querySelector(".bubble").innerHTML = "";
      }
      scrollBottom();
    } else if (event === "citation") {
      state.citations.set(Number(data.index), data);
      const msg = document.getElementById(`msg-${state.assistantId}`);
      if (msg) {
        msg._citations = [...state.citations.values()];
        setBubbleContent(msg, state.currentText, msg._citations);
        bindMessageActions(msg);
      }
    } else if (event === "token") {
      state.currentText += data.delta || "";
      const msg = document.getElementById(`msg-${state.assistantId}`);
      if (msg) {
        msg._content = state.currentText;
        setBubbleContent(msg, state.currentText, [...state.citations.values()]);
        bindMessageActions(msg);
        scrollBottom();
      }
    } else if (event === "phase") {
      if (data.phase === "write") setStatus("正在提交退款，请稍候…", true);
    } else if (event === "interrupt") {
      setStatus("", false);
      renderInterrupt(data);
    } else if (event === "done") {
      setStatus("", false);
      const msg = document.getElementById(`msg-${state.assistantId}`);
      if (msg) {
        msg._content = state.currentText;
        const status = msg.querySelector(".msg-status");
        if (status) status.textContent = data.receipt ? "已提交" : "";
        setBubbleContent(msg, state.currentText, [...state.citations.values()]);
        bindMessageActions(msg);
      }
      toast(data.receipt ? `退款申请 ${data.receipt.refund_id} 已提交` : "回答完成");
      loadSessions();
    } else if (event === "error") {
      setStatus("", false);
      showError(`${data.code || "ERROR"}：${data.message || "请求失败"}`);
    }
  }

  function showError(message) {
    const wrap = messageElement("assistant", `⚠️ ${message}`, [], null, "error");
    els.messageList.appendChild(wrap);
    showWelcome(false);
    scrollBottom();
  }

  async function sendMessage(text) {
    if (!text.trim() || state.streaming) return;
    const message = text.trim();
    els.input.value = "";
    els.input.style.height = "";
    showWelcome(false);
    els.messageList.appendChild(messageElement("user", message, [], null, "done"));
    scrollBottom();
    setStatus("小猫正在输入…", true);
    try {
      await streamRequest("/api/v1/chat/stream", {
        session_id: state.sessionId,
        message,
        client_message_id: `cm_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        context: { page: "chat" },
      });
    } catch (err) {
      if (err.name !== "AbortError") showError(err.message || "网络错误");
    } finally {
      setStatus("", false);
    }
  }

  async function loadSessions() {
    try {
      const res = await fetch("/api/v1/sessions", { headers: headers() });
      if (!res.ok) return;
      const data = await res.json();
      els.sessionList.innerHTML = (data.items || []).map((s) => `
        <button type="button" class="session-item ${s.id === state.sessionId ? "active" : ""}" data-session="${escapeHtml(s.id)}">
          <span class="session-dot"></span><span class="session-copy"><strong>${escapeHtml(s.title || "新对话")}</strong><small>${escapeHtml(new Date(s.updated_at).toLocaleString())}</small></span>
        </button>`).join("") || '<p class="empty-hint">还没有会话</p>';
      els.sessionList.querySelectorAll("[data-session]").forEach((btn) => {
        btn.addEventListener("click", () => openSession(btn.dataset.session));
      });
    } catch {}
  }

  async function openSession(id) {
    if (state.streaming) return;
    state.sessionId = id;
    localStorage.setItem("dsh_session_id", id);
    const res = await fetch(`/api/v1/sessions/${encodeURIComponent(id)}/messages`, { headers: headers() });
    if (!res.ok) return toast("会话不存在");
    const data = await res.json();
    els.messageList.innerHTML = "";
    showWelcome(!(data.items || []).length);
    for (const m of data.items || []) {
      const el = messageElement(m.role, m.content || "", m.citations || [], m.id, m.status);
      els.messageList.appendChild(el);
    }
    els.headerSession.textContent = `会话 ${id.slice(-6)}`;
    scrollBottom();
    loadSessions();
    try {
      const pending = await fetch(`/api/v1/sessions/${encodeURIComponent(id)}/interrupt`, { headers: headers() });
      const payload = await pending.json();
      if (payload.interrupt) renderInterrupt(payload.interrupt);
    } catch {}
  }

  function newChat() {
    if (state.streaming) return;
    state.sessionId = null;
    state.pendingInterrupt = null;
    localStorage.removeItem("dsh_session_id");
    els.messageList.innerHTML = "";
    showWelcome(true);
    els.headerSession.textContent = "新对话";
    els.input.focus();
    loadSessions();
  }

  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(els.input.value);
  });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(els.input.value);
    }
  });
  els.input.addEventListener("input", () => {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(160, els.input.scrollHeight) + "px";
  });
  els.send.addEventListener("click", (e) => {
    if (state.streaming && state.abort) {
      e.preventDefault();
      state.abort.abort();
      setStatus("已停止", false);
    }
  });
  els.stop.addEventListener("click", () => {
    state.abort?.abort();
    setStatus("已停止", false);
  });
  $("newChatBtn").addEventListener("click", newChat);
  $("refreshSessionsBtn").addEventListener("click", loadSessions);
  $("switchUserBtn").addEventListener("click", () => {
    const next = prompt("切换演示账号", state.userId);
    if (next && next.trim()) {
      state.userId = next.trim();
      localStorage.setItem("dsh_user_id", state.userId);
      els.userIdLabel.textContent = state.userId;
      newChat();
    }
  });
  $("citationCloseBtn").addEventListener("click", closeCitation);
  els.citationOverlay.addEventListener("click", closeCitation);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeCitation();
      if (els.feedbackDialog.open) els.feedbackDialog.close();
    }
  });
  $("feedbackCloseBtn").addEventListener("click", () => els.feedbackDialog.close());
  $("feedbackCancelBtn").addEventListener("click", () => els.feedbackDialog.close());
  els.feedbackDialog.addEventListener("click", (e) => {
    if (e.target === els.feedbackDialog) els.feedbackDialog.close();
  });
  document.querySelectorAll(".reason-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const selected = chip.getAttribute("aria-pressed") === "true";
      document.querySelectorAll(".reason-chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
      chip.setAttribute("aria-pressed", selected ? "false" : "true");
    });
  });
  els.feedbackForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const reason = document.querySelector(".reason-chip[aria-pressed='true']")?.dataset.reason || null;
    const comment = $("feedbackComment").value.trim() || null;
    if (!state.feedbackMessageId) return;
    try {
      const res = await fetch(`/api/v1/messages/${encodeURIComponent(state.feedbackMessageId)}/feedback`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ rating: "down", reason, comment }),
      });
      if (!res.ok) throw new Error("提交失败");
      els.feedbackDialog.close();
      toast("感谢反馈，我们会继续改进");
    } catch {
      els.feedbackError.textContent = "提交失败，请稍后再试";
      els.feedbackError.hidden = false;
    }
  });
  document.querySelectorAll("[data-suggestion]").forEach((btn) => {
    btn.addEventListener("click", () => sendMessage(btn.dataset.suggestion));
  });
  $("closeSidebarBtn").addEventListener("click", () => $("sidebar").classList.remove("open"));
  $("menuBtn").addEventListener("click", () => $("sidebar").classList.toggle("open"));

  els.userIdLabel.textContent = state.userId;
  loadSessions();
  if (state.sessionId) openSession(state.sessionId);
})();
