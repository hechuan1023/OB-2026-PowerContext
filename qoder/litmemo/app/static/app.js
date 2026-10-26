const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

let toastTimer;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!response.ok) {
    throw new Error(data.detail || `请求失败（${response.status}）`);
  }
  return data;
}

function jsonOptions(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("active", panel.id === `tab-${tab.dataset.tab}`);
    });
    if (tab.dataset.tab === "library") loadDocuments();
    if (tab.dataset.tab === "memory") loadMemories();
  });
});

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("health").innerHTML = [
      `<span class="badge ${health.seekdb.ok ? "ok" : "err"}">seekdb ${health.seekdb.ok ? `已连接 · ${health.seekdb.chunks} 个片段` : "未连接"}</span>`,
      `<span class="badge ${health.powercontext.ok ? "ok" : "err"}">PowerContext ${health.powercontext.ok ? "已连接" : "未连接"}</span>`,
      `<span class="badge">${esc(health.embedding_model)}</span>`,
    ].join("");
  } catch (error) {
    $("health").innerHTML = `<span class="badge err">${esc(error.message)}</span>`;
  }
}

function numOrNull(id) {
  const value = $(id).value.trim();
  return value === "" ? null : Number(value);
}

function searchPayload() {
  return {
    query: $("query").value.trim(),
    mode: $("mode").value,
    limit: Number($("limit").value) || 6,
    year_from: numOrNull("year_from"),
    year_to: numOrNull("year_to"),
    kind: $("kind").value || null,
    use_memory: $("use_memory").checked,
  };
}

function renderMemoryBanner(memory) {
  if (!memory) { $("memory-banner").innerHTML = ""; return; }
  const ok = memory.status && memory.status.ok;
  const parts = [];
  if (memory.applied_constraints) {
    const c = memory.applied_constraints;
    const details = [];
    if (c.topic) details.push(`方向：${esc(c.topic)}`);
    if (c.year_from) details.push(`年份 ≥ ${esc(c.year_from)}`);
    if (c.exclude_terms && c.exclude_terms.length) details.push(`排除：${esc(c.exclude_terms.join("、"))}`);
    parts.push(`<h4>已应用 PowerContext 中的研究设定</h4><p class="muted">${details.join("；") || "（空设定）"}</p>`);
  }
  if (memory.excluded_by_memory) {
    parts.push(`<p class="muted">按你的排除规则过滤掉 ${memory.excluded_by_memory} 条结果。</p>`);
  }
  if (memory.recalled && memory.recalled.length) {
    const items = memory.recalled.map((hit) => {
      let text = hit.text;
      try { text = JSON.stringify(JSON.parse(hit.text)); } catch { /* 保留原文 */ }
      return `<li><span class="tag">${esc(hit.kind)}</span> ${esc(text).slice(0, 220)}</li>`;
    }).join("");
    parts.push(`<h4>本次召回的相关记忆</h4><ul>${items}</ul>`);
  }
  if (memory.progress_saved) {
    parts.push(`<p class="muted small">本次检索进展已写回 PowerContext，下次打开可一键恢复。</p>`);
  }
  const body = parts.join("") || `<p class="muted">本次未使用研究记忆。</p>`;
  $("memory-banner").innerHTML = ok
    ? `<div class="banner memory-ok">${body}</div>`
    : `<div class="banner memory-err"><h4>PowerContext 不可用</h4><p class="muted">${esc(memory.status?.error || "未知错误")}</p><p class="muted small">检索仍由 seekdb 完成，但研究设定与进展未参与。</p></div>`;
}

function renderResults(data) {
  renderMemoryBanner(data.memory);
  if (!data.results.length) {
    $("results").innerHTML = `<div class="card muted">没有命中结果。可以先到「文献库」导入文献，或放宽年份 / 类型过滤。</div>`;
    return;
  }
  $("results").innerHTML = data.results.map((row, index) => `
    <article class="result">
      <div class="result-head">
        <span class="result-title">${index + 1}. ${esc(row.title)}</span>
        ${row.score === null ? "" : `<span class="score">${esc(row.score_label)} ${esc(row.score)}</span>`}
      </div>
      <div class="result-meta">
        <span>出处：${esc(row.source)} · ${esc(row.locator)}</span>
        <span>${row.year ? esc(row.year) : "年份未知"}</span>
        <span class="tag">${esc(row.kind)}</span>
        ${row.tags ? `<span>标签：${esc(row.tags)}</span>` : ""}
      </div>
      <div class="result-text">${esc(row.text)}</div>
    </article>`).join("");
}

$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = searchPayload();
  if (!payload.query) { toast("请输入检索内容"); return; }
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  button.textContent = "检索中…";
  try {
    renderResults(await api("/api/search", jsonOptions("POST", payload)));
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "检索";
  }
});

async function loadMemoryState() {
  try {
    const data = await api("/api/memory");
    const c = data.constraints;
    if (c) {
      $("c_topic").value = c.topic || "";
      $("c_year_from").value = c.year_from || "";
      $("c_exclude").value = (c.exclude_terms || []).join(", ");
      $("c_limit").value = c.default_limit || 6;
    }
    const progress = data.progress;
    if (progress && progress.last_query) {
      $("resume-card").hidden = false;
      $("resume-text").textContent =
        `上次检索「${progress.last_query}」（${progress.mode}，命中 ${progress.result_count} 条）` +
        (progress.top_titles?.length ? `，最相关：${progress.top_titles.join("、")}` : "");
      $("resume-btn").onclick = () => {
        $("query").value = progress.last_query;
        $("mode").value = progress.mode || "hybrid";
        $("year_from").value = progress.year_from || "";
        $("year_to").value = progress.year_to || "";
        $("kind").value = progress.kind || "";
        $("limit").value = progress.limit || 6;
        toast("已恢复上次检索条件，点击「检索」继续");
      };
    }
  } catch (error) {
    console.warn("记忆读取失败", error);
  }
}

$("constraints-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/memory/constraints", jsonOptions("POST", {
      topic: $("c_topic").value,
      year_from: numOrNull("c_year_from"),
      exclude_terms: $("c_exclude").value,
      default_limit: Number($("c_limit").value) || 6,
    }));
    toast("研究设定已保存到 PowerContext");
  } catch (error) {
    toast(error.message);
  }
});

async function loadDocuments() {
  try {
    const data = await api("/api/documents");
    $("doc-count").textContent = `${data.documents.length} 篇`;
    if (!data.documents.length) {
      $("documents").innerHTML = `<p class="muted">还没有文献，点击上方「一键导入内置示例文献」。</p>`;
      return;
    }
    $("documents").innerHTML = `<table><thead><tr><th>标题</th><th>年份</th><th>类型</th><th>片段</th><th>来源</th><th></th></tr></thead><tbody>` +
      data.documents.map((doc) => `<tr>
        <td>${esc(doc.title)}</td><td>${doc.year || "—"}</td><td>${esc(doc.kind)}</td>
        <td>${esc(doc.chunks)}</td><td class="muted small">${esc(doc.source)}</td>
        <td><button class="link" data-doc="${esc(doc.doc_id)}">删除</button></td>
      </tr>`).join("") + "</tbody></table>";
    $("documents").querySelectorAll("button[data-doc]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/api/documents/${button.dataset.doc}`, { method: "DELETE" });
        toast("已删除");
        loadDocuments();
        loadHealth();
      });
    });
  } catch (error) {
    $("documents").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = $("files").files;
  if (!files.length) { toast("请选择文件"); return; }
  const form = new FormData();
  [...files].forEach((file) => form.append("files", file));
  if (numOrNull("u_year")) form.append("year", numOrNull("u_year"));
  if ($("u_kind").value) form.append("kind", $("u_kind").value);
  if ($("u_tags").value) form.append("tags", $("u_tags").value);
  try {
    const data = await api("/api/documents/upload", { method: "POST", body: form });
    $("import-log").textContent = data.imported.map((item) => `${item.title}：${item.chunks} 个片段`).join("；");
    toast("导入完成，向量与全文索引已写入 seekdb");
    loadDocuments();
    loadHealth();
  } catch (error) {
    toast(error.message);
  }
});

$("samples-btn").addEventListener("click", async () => {
  try {
    const data = await api("/api/documents/samples", { method: "POST" });
    $("import-log").textContent = data.imported.map((item) => `${item.title}：${item.chunks} 个片段`).join("；");
    toast("示例文献已导入");
    loadDocuments();
    loadHealth();
  } catch (error) {
    toast(error.message);
  }
});

function prettyMemoryText(text) {
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") {
      return Object.entries(parsed).filter(([k]) => !k.startsWith("_"))
        .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("、") : value}`).join("\n");
    }
  } catch { /* 普通文本 */ }
  return text;
}

async function loadMemories() {
  try {
    const data = await api("/api/memory");
    $("memory-scope").textContent = data.status.ok ? `scope: ${data.status.scope_id}` : "未连接";
    if (!data.memories.length) {
      $("memories").innerHTML = `<p class="muted">当前 scope 还没有记忆。保存一次研究设定或执行一次检索就会写入。</p>`;
      return;
    }
    $("memories").innerHTML = data.memories.map((record) => `
      <div class="memory-item">
        <span class="tag">${esc(record.kind)}</span>
        <span class="artifact"> ${esc(record.artifact.artifact_id)}@${esc(record.artifact.revision)} · state=${esc(record.state)}</span>
        <pre>${esc(prettyMemoryText(record.text))}</pre>
        <button class="link" data-artifact="${esc(record.artifact.artifact_id)}"
          data-revision="${esc(record.artifact.revision)}" data-state-version="${esc(record.state_version)}">遗忘这条</button>
      </div>`).join("");
    $("memories").querySelectorAll("button[data-artifact]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api("/api/memory/forget", jsonOptions("POST", {
          artifact_id: button.dataset.artifact,
          revision: Number(button.dataset.revision),
          state_version: Number(button.dataset.stateVersion),
        }));
        toast("已遗忘，不再参与召回");
        loadMemories();
      });
    });
  } catch (error) {
    $("memories").innerHTML = `<p class="muted">${esc(error.message)}</p>`;
  }
}

$("note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("note-text").value.trim();
  if (!text) return;
  try {
    await api("/api/memory/notes", jsonOptions("POST", { text }));
    $("note-text").value = "";
    toast("笔记已保存为 PowerContext 记忆");
    loadMemories();
  } catch (error) {
    toast(error.message);
  }
});

loadHealth();
loadMemoryState();
loadDocuments();
