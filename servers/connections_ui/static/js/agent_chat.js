/**
 * Agent Chat page: namespace + prompt, calls agent_chat server (memory per namespace).
 * Auto-scrolling message area, submit on Enter.
 */
(function () {
  const messagesEl = document.getElementById("agent-chat-messages");
  const placeholderEl = document.getElementById("agent-chat-placeholder");
  const namespaceEl = document.getElementById("agent-namespace");
  const promptEl = document.getElementById("agent-chat-prompt");
  const submitBtn = document.getElementById("agent-chat-submit");

  let agentChatUrl = null;

  function hidePlaceholder() {
    if (placeholderEl) placeholderEl.hidden = true;
  }

  function renderMarkdown(text) {
    if (typeof marked !== "undefined" && marked.parse) {
      var html = marked.parse(text, { breaks: true, gfm: true });
      if (typeof DOMPurify !== "undefined") {
        return DOMPurify.sanitize(html);
      }
      return html;
    }
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
  }

  function appendMessage(role, text) {
    hidePlaceholder();
    var div = document.createElement("div");
    div.className = "agent-chat-message " + role;
    div.setAttribute("role", "listitem");
    if (role === "assistant") {
      div.classList.add("markdown-body");
      div.innerHTML = renderMarkdown(text);
    } else {
      div.textContent = text;
    }
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setLoading(loading) {
    if (!submitBtn) return;
    submitBtn.disabled = loading;
    submitBtn.textContent = loading ? "…" : "Send";
  }

  var thinkingEl = null;

  function showThinking() {
    hidePlaceholder();
    thinkingEl = document.createElement("div");
    thinkingEl.className = "agent-chat-message assistant thinking";
    thinkingEl.setAttribute("role", "status");
    thinkingEl.setAttribute("aria-live", "polite");
    thinkingEl.innerHTML = "<span class=\"thinking-dots\"><span></span><span></span><span></span></span> Thinking…";
    messagesEl.appendChild(thinkingEl);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function removeThinking() {
    if (thinkingEl && thinkingEl.parentNode) {
      thinkingEl.parentNode.removeChild(thinkingEl);
      thinkingEl = null;
    }
  }

  function getAgentChatUrl() {
    if (agentChatUrl) return Promise.resolve(agentChatUrl);
    return fetch("/api/agent-chat-url")
      .then(function (r) {
        if (!r.ok) throw new Error("Could not get agent chat server URL");
        return r.json();
      })
      .then(function (data) {
        agentChatUrl = data.url || null;
        return agentChatUrl;
      });
  }

  function sendFromInput() {
    var namespace = (namespaceEl && namespaceEl.value) ? namespaceEl.value.trim() : "";
    if (!namespace) {
      appendMessage("assistant", "Please enter a namespace (e.g. your email) above first.");
      return;
    }
    var text = (promptEl && promptEl.value) ? promptEl.value.trim() : "";
    if (!text) return;

    appendMessage("user", text);
    if (promptEl) promptEl.value = "";

    setLoading(true);
    showThinking();
    getAgentChatUrl()
      .then(function (base) {
        if (!base) throw new Error("No agent chat server URL");
        return fetch(base + "/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ namespace: namespace, prompt: text }),
        });
      })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status === 502 ? "Agent chat or AI unavailable" : "Request failed");
        return r.json();
      })
      .then(function (data) {
        removeThinking();
        var assistantText = (data.text != null && data.text !== "") ? data.text : "(No response)";
        appendMessage("assistant", assistantText);
      })
      .catch(function (err) {
        removeThinking();
        appendMessage("assistant", "Error: " + (err.message || String(err)));
      })
      .finally(function () {
        setLoading(false);
      });
  }

  if (submitBtn) {
    submitBtn.addEventListener("click", sendFromInput);
  }

  if (promptEl) {
    promptEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendFromInput();
      }
    });
  }

  /* Auto-scroll when new content is added */
  if (messagesEl && typeof MutationObserver !== "undefined") {
    var observer = new MutationObserver(function () {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
    observer.observe(messagesEl, { childList: true, subtree: true });
  }
})();
