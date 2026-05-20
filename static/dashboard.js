(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function show(el) {
    el.classList.remove("hidden");
  }

  function hide(el) {
    el.classList.add("hidden");
  }

  function setText(el, text) {
    el.textContent = text;
  }

  function apiError(payload) {
    if (payload && payload.error && payload.error.message) {
      return payload.error.message;
    }
    return "Ошибка запроса";
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      ...options,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(apiError(body));
    }
    return body;
  }

  function setProgress(bar, statusEl, percent, text) {
    bar.style.width = Math.min(100, Math.max(0, percent)) + "%";
    setText(statusEl, text);
  }

  function uploadWithXhr(file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);

      const progressWrap = $("#upload-progress");
      const bar = $("#upload-bar");
      const statusEl = $("#upload-status");
      const errEl = $("#upload-error");

      hide(errEl);
      show(progressWrap);
      setProgress(bar, statusEl, 0, "Подготовка…");

      xhr.upload.addEventListener("progress", (ev) => {
        if (!ev.lengthComputable) {
          setText(statusEl, "Отправка: " + formatBytes(ev.loaded) + "…");
          return;
        }
        const pct = Math.round((100 * ev.loaded) / ev.total);
        setProgress(
          bar,
          statusEl,
          pct,
          "Отправка: " + formatBytes(ev.loaded) + " / " + formatBytes(ev.total) + " (" + pct + "%)"
        );
      });

      xhr.addEventListener("load", () => {
        let body;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          reject(new Error("Неверный ответ сервера"));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300 && body.ok) {
          setProgress(bar, statusEl, 100, "Загружено: " + body.data.name + " (" + body.data.size_human + ")");
          resolve(body.data);
          return;
        }
        reject(new Error(apiError(body)));
      });

      xhr.addEventListener("error", () => reject(new Error("Сбой сети при загрузке")));
      xhr.addEventListener("abort", () => reject(new Error("Загрузка отменена")));

      xhr.open("POST", "/api/upload");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.withCredentials = true;
      xhr.send(form);
    });
  }

  function parseViaWebSocket(filename) {
    return new Promise((resolve, reject) => {
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(scheme + "//" + location.host + "/ws/parse");

      const progressWrap = $("#parse-progress");
      const bar = $("#parse-bar");
      const statusEl = $("#parse-status");

      show(progressWrap);
      setProgress(bar, statusEl, 0, "Подключение…");

      ws.addEventListener("open", () => {
        ws.send(JSON.stringify({ file: filename }));
      });

      ws.addEventListener("message", (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "progress") {
          setProgress(
            bar,
            statusEl,
            msg.percent,
            "Разбор: " + formatBytes(msg.bytes_read) + " / " + formatBytes(msg.total_bytes) +
              " — в БД " + msg.inserted + " записей (" + msg.percent + "%)"
          );
        } else if (msg.type === "done") {
          setProgress(bar, statusEl, 100, "Готово: добавлено " + msg.inserted + " записей");
          ws.close();
          resolve(msg);
        } else if (msg.type === "error") {
          ws.close();
          reject(new Error(msg.message || "Ошибка разбора"));
        }
      });

      ws.addEventListener("error", () => {
        reject(new Error("Ошибка WebSocket"));
      });

      ws.addEventListener("close", (ev) => {
        if (ev.wasClean) return;
        reject(new Error("Соединение WebSocket закрыто"));
      });
    });
  }

  let selectedFile = null;

  async function refreshFileList(selectName) {
    const list = $("#file-list");
    try {
      const body = await fetchJson("/api/files");
      list.innerHTML = "";
      if (!body.data.length) {
        list.innerHTML = '<li class="px">Нет файлов. Загрузите access-лог.</li>';
        return;
      }
      body.data.forEach((f) => {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "file-item" + (f.name === selectName ? " file-item-active" : "");
        btn.textContent = f.name + " — " + f.size_human;
        btn.dataset.name = f.name;
        btn.addEventListener("click", () => selectFile(f.name));
        li.appendChild(btn);
        list.appendChild(li);
      });
    } catch (err) {
      list.innerHTML = '<li class="pe">' + err.message + "</li>";
    }
  }

  async function selectFile(name) {
    selectedFile = name;
    document.querySelectorAll(".file-item").forEach((el) => {
      el.classList.toggle("file-item-active", el.dataset.name === name);
    });

    const empty = $("#file-detail-empty");
    const detail = $("#file-detail");
    const preview = $("#file-preview");

    hide(detail);
    hide(preview);
    show(empty);
    setText(empty, "Загрузка…");

    try {
      const body = await fetchJson("/api/files/" + encodeURIComponent(name));
      const d = body.data;
      hide(empty);
      detail.innerHTML = "";
      const fields = [
        ["Имя", d.name],
        ["Размер", d.size_human + " (" + d.size + " байт)"],
        ["Изменён", d.modified],
        ["Строк в превью", String(d.preview_count)],
      ];
      fields.forEach(([label, value]) => {
        const dt = document.createElement("dt");
        dt.textContent = label;
        const dd = document.createElement("dd");
        dd.textContent = value;
        detail.appendChild(dt);
        detail.appendChild(dd);
      });
      show(detail);
      preview.textContent = (d.preview_lines || []).join("\n") || "(пустой файл)";
      show(preview);
    } catch (err) {
      setText(empty, err.message);
      show(empty);
    }
  }

  function readFilters() {
    const form = $("#filter-form");
    const data = new FormData(form);
    const params = new URLSearchParams();
    for (const [key, value] of data.entries()) {
      if (value) params.set(key, value);
    }
    return params;
  }

  async function refreshUrlOptions() {
    const params = readFilters();
    ["url", "group_by"].forEach((k) => params.delete(k));
    const body = await fetchJson("/api/urls?" + params.toString());
    const select = $("#url-select");
    const current = select.value;
    select.innerHTML = '<option value="">— все —</option>';
    body.data.forEach((row) => {
      const opt = document.createElement("option");
      opt.value = row.url_path;
      opt.textContent = row.url_path + " (" + row.hits + ")";
      if (row.url_path === current) opt.selected = true;
      select.appendChild(opt);
    });
  }

  async function loadTable() {
    const summary = $("#data-summary");
    const errEl = $("#data-error");
    const empty = $("#data-empty");
    const wrap = $("#data-table-wrap");
    const thead = $("#data-thead");
    const tbody = $("#data-tbody");

    hide(errEl);
    hide(summary);
    setText(empty, "Загрузка…");
    show(empty);
    hide(wrap);

    try {
      const params = readFilters();
      const body = await fetchJson("/api/logs?" + params.toString());
      const columns = body.columns || [];
      const rows = body.data || [];

      thead.innerHTML = "";
      const headRow = document.createElement("tr");
      columns.forEach(([, title]) => {
        const th = document.createElement("th");
        th.textContent = title;
        headRow.appendChild(th);
      });
      thead.appendChild(headRow);

      tbody.innerHTML = "";
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        columns.forEach(([key]) => {
          const td = document.createElement("td");
          const val = row[key];
          td.textContent = val == null ? "—" : String(val);
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });

      hide(empty);
      if (!rows.length) {
        setText(empty, "Нет записей по выбранным фильтрам.");
        show(empty);
        hide(wrap);
      } else {
        show(wrap);
        show(summary);
        setText(summary, "Загружено записей: " + rows.length);
      }
    } catch (err) {
      setText(empty, "");
      hide(empty);
      setText(errEl, err.message);
      show(errEl);
    }
  }

  $("#upload-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("#upload-input");
    const btn = $("#upload-btn");
    const errEl = $("#upload-error");
    const file = input.files && input.files[0];
    if (!file) return;

    hide(errEl);
    btn.disabled = true;
    try {
      const meta = await uploadWithXhr(file);
      await refreshFileList(meta.name);
      await selectFile(meta.name);
      try {
        await parseViaWebSocket(meta.name);
        await refreshUrlOptions();
      } catch (parseErr) {
        setText(errEl, "Файл сохранён, но разбор не выполнен: " + parseErr.message);
        show(errEl);
      }
    } catch (err) {
      setText(errEl, err.message);
      show(errEl);
    } finally {
      btn.disabled = false;
    }
  });

  $("#filter-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    await loadTable();
  });

  refreshFileList().then(() => {
    if (selectedFile) selectFile(selectedFile);
  });
  refreshUrlOptions().catch(() => {});
})();
