const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("sendBtn");
const startBtn = document.getElementById("startBtn");
const resetBtn = document.getElementById("resetBtn");
const sessionInfoEl = document.getElementById("sessionInfo");

let sessionId = null;
let sending = false;

marked.setOptions({
  gfm: true,
  breaks: false,
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return hljs.highlightAuto(code).value;
  },
});

function renderMarkdown(text) {
  const raw = marked.parse(text || "");
  return DOMPurify.sanitize(raw);
}

function scrollDown() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(cls, html) {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  if (html !== undefined) el.innerHTML = html;
  messagesEl.appendChild(el);
  scrollDown();
  return el;
}

function addToolBlock(kind, name, payload) {
  const ok = kind === "tool_result" && payload.ok;
  const err = kind === "tool_result" && !payload.ok;
  const el = document.createElement("div");
  el.className = "tool-block " + (err ? "err" : ok ? "ok" : "");
  const summary = document.createElement("summary");
  if (kind === "tool_call") {
    summary.textContent = "⚙ tool call: " + name;
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = JSON.stringify(payload.arguments, null, 2);
    pre.appendChild(code);
    el.appendChild(summary);
    el.appendChild(pre);
  } else {
    summary.textContent = (payload.ok ? "✔ " : "✘ ") + name + " result";
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = payload.output;
    pre.appendChild(code);
    el.appendChild(summary);
    el.appendChild(pre);
  }
  messagesEl.appendChild(el);
  scrollDown();
  return el;
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || detail;
    } catch (e) {}
    throw new Error(detail);
  }
  return resp.json();
}

async function startSession() {
  startBtn.disabled = true;
  try {
    const body = {
      project_dir: document.getElementById("projectDir").value || ".",
      model: document.getElementById("model").value || null,
      api_key: document.getElementById("apiKey").value || null,
      max_turns: parseInt(document.getElementById("maxTurns").value || "25", 10),
      stream: document.getElementById("stream").checked,
    };
    const session = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    sessionId = session.session_id;
    sessionInfoEl.textContent =
      "session " + session.session_id +
      "\nproject " + session.project_dir +
      "\nmodel " + session.model +
      "\nmax turns " + session.max_turns;
    sessionInfoEl.classList.remove("hidden");
    inputEl.disabled = false;
    sendBtn.disabled = false;
    resetBtn.disabled = false;
    inputEl.focus();
  } catch (err) {
    addMessage("error", "Could not start session: " + escapeHtml(err.message));
  } finally {
    startBtn.disabled = false;
  }
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function send() {
  const text = inputEl.value.trim();
  if (!text || sending || !sessionId) return;
  sending = true;
  sendBtn.disabled = true;
  inputEl.value = "";

  addMessage("user", escapeHtml(text));
  const assistantEl = addMessage("assistant", "");
  assistantEl.classList.add("typing");
  let buffer = "";
  let renderTimer = null;

  const scheduleRender = () => {
    if (renderTimer) return;
    renderTimer = setTimeout(() => {
      renderTimer = null;
      assistantEl.innerHTML = renderMarkdown(buffer) || "&nbsp;";
      scrollDown();
    }, 80);
  };

  try {
    const resp = await fetch("/api/sessions/" + sessionId + "/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || resp.statusText);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let raw = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      raw += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = raw.indexOf("\n\n")) !== -1) {
        const frame = raw.slice(0, idx);
        raw = raw.slice(idx + 2);
        if (!frame.startsWith("data: ")) continue;
        let event;
        try {
          event = JSON.parse(frame.slice(6));
        } catch (e) {
          continue;
        }
        if (event.type === "token") {
          buffer += event.text;
          scheduleRender();
        } else if (event.type === "tool_call") {
          addToolBlock("tool_call", event.name, event);
        } else if (event.type === "tool_result") {
          addToolBlock("tool_result", event.name, event);
        } else if (event.type === "status") {
          addMessage("status", escapeHtml(event.text));
        } else if (event.type === "final") {
          buffer = event.content;
          assistantEl.innerHTML = renderMarkdown(buffer);
          scrollDown();
        } else if (event.type === "error") {
          addMessage("error", escapeHtml(event.message));
        }
      }
    }
  } catch (err) {
    addMessage("error", escapeHtml(err.message));
  } finally {
    assistantEl.classList.remove("typing");
    sending = false;
    sendBtn.disabled = false;
    inputEl.focus();
  }
}

async function resetChat() {
  if (!sessionId) return;
  await api("/api/sessions/" + sessionId + "/reset", { method: "POST", body: "{}" });
  messagesEl.innerHTML = "";
}

startBtn.addEventListener("click", startSession);
sendBtn.addEventListener("click", send);
resetBtn.addEventListener("click", resetChat);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
