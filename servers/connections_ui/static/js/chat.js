/**
 * License: MIT
 * Chat page: sends messages to the chat server, which relays to the AI server.
 */

(function () {
  const messagesEl = document.getElementById("chat-messages");
  const placeholderEl = document.getElementById("chat-placeholder");
  const promptEl = document.getElementById("chat-prompt");
  const submitBtn = document.getElementById("chat-submit");
  const profileSelect = document.getElementById("chat-profile");

  let chatagentUrl = null;
  let namespace = null;

  function hidePlaceholder() {
    if (placeholderEl) placeholderEl.hidden = true;
  }

  function appendMessage(role, text) {
    hidePlaceholder();
    const div = document.createElement("div");
    div.className = "chat-message " + role;
    div.setAttribute("role", "listitem");
    div.textContent = text;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function setLoading(loading) {
    if (!submitBtn) return;
    submitBtn.disabled = loading;
    submitBtn.textContent = loading ? "…" : "Send";
  }

  let thinkingEl = null;

  function showThinking() {
    hidePlaceholder();
    thinkingEl = document.createElement("div");
    thinkingEl.className = "chat-message assistant thinking";
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

  function getChatagentUrl() {
    if (chatagentUrl) return Promise.resolve(chatagentUrl);
    return fetch("/api/chatagent-url")
      .then(function (r) {
        if (!r.ok) throw new Error("Could not get chat server URL");
        return r.json();
      })
      .then(function (data) {
        chatagentUrl = data.url || null;
        return chatagentUrl;
      });
  }

  function sendFromInput() {
    const text = (promptEl && promptEl.value) ? promptEl.value.trim() : "";
    if (!text) return;

    const profile = (profileSelect && profileSelect.value) ? profileSelect.value : "fast";
    appendMessage("user", text);
    if (promptEl) promptEl.value = "";

    setLoading(true);
    showThinking();
    getChatagentUrl()
      .then(function (base) {
        if (!base) throw new Error("No chat agent URL");
        const body = { prompt: text, profile: profile };
        if (namespace) body.namespace = namespace;
        return fetch(base + "/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status === 502 ? "Chat server or AI unavailable" : "Request failed");
        return r.json();
      })
      .then(function (data) {
        removeThinking();
        if (data.namespace) namespace = data.namespace;
        const output = data.output;
        const assistantText = (output && output.text) ? output.text : "(No response text)";
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
})();
