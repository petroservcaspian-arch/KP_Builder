// static/app.js
// Полная исправленная версия: поиск с подсказками + стрелки/Enter + корзина КП + PDF без "body stream already read"

let timer = null;
let items = [];

// подсказки (выбор клавиатурой)
let suggestList = [];
let activeIndex = -1;

const el = (id) => document.getElementById(id);

function fmt(n) {
  const x = Number(n || 0);
  return x.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
}

function calc() {
  const vatRate = Number(el("vat_rate").value || 0);
  let subtotal = 0;

  items.forEach((it) => {
    const qty = Number(it.qty || 0);
    const price = Number(it.price || 0);
    const disc = Number(it.discount || 0);
    it.line = qty * price * (1 - disc / 100);
    subtotal += it.line;
  });

  const vat = subtotal * (vatRate / 100);
  const total = subtotal + vat;

  el("subtotal").textContent = fmt(subtotal);
  el("vat").textContent = fmt(vat);
  el("total").textContent = fmt(total);
}

function renderTable() {
  const body = el("itemsBody");
  body.innerHTML = "";

  items.forEach((it, idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <div><b>${it.name}</b></div>
        <div class="muted small">${it.group || ""}</div>
      </td>
      <td>${it.unit || ""}</td>
      <td>${fmt(it.price)} ${it.currency || ""}</td>
      <td><input class="kpInput" value="${it.qty}" data-i="${idx}" data-f="qty"></td>
      <td><input class="kpInput" value="${it.discount}" data-i="${idx}" data-f="discount"></td>
      <td>${fmt(it.line || 0)}</td>
      <td class="del" data-del="${idx}" title="Удалить">×</td>
    `;
    body.appendChild(tr);
  });

  body.querySelectorAll("input").forEach((inp) => {
    inp.addEventListener("input", (e) => {
      const i = Number(e.target.dataset.i);
      const f = e.target.dataset.f;
      items[i][f] = e.target.value;
      calc();
      renderTable();
    });
  });

  body.querySelectorAll("[data-del]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.del);
      items.splice(i, 1);
      calc();
      renderTable();
    });
  });

  calc();
}

function addItem(prod) {
  // если уже добавлен — увеличим количество
  const existing = items.find((x) => x.id === prod.id);
  if (existing) {
    existing.qty = Number(existing.qty || 0) + 1;
  } else {
    items.push({
      id: prod.id,
      name: prod.name,
      unit: prod.unit,
      price: Number(prod.price || 0),
      currency: prod.currency || "",
      group: prod.group || "",
      qty: 1,
      discount: 0,
      line: 0,
    });
  }
  renderTable();
}

async function doSearch(q) {
  const r = await fetch("/api/search?q=" + encodeURIComponent(q));
  return await r.json();
}

function setActive(i) {
  activeIndex = i;
  const box = el("suggest");
  [...box.querySelectorAll(".sugItem")].forEach((node, idx) => {
    node.classList.toggle("active", idx === activeIndex);
  });
}

function renderSuggest(list) {
  suggestList = list || [];
  activeIndex = suggestList.length ? 0 : -1;

  const box = el("suggest");
  if (!suggestList.length) {
    box.innerHTML = `<div class="sugItem"><div class="muted">Ничего не найдено</div></div>`;
    return;
  }

  box.innerHTML = "";
  suggestList.forEach((prod, idx) => {
    const row = document.createElement("div");
    row.className = "sugItem";
    row.innerHTML = `
      <div class="sugLeft">
        <div class="sugName"><b>${prod.name}</b></div>
        <div class="sugMeta">${prod.group || ""} • ${prod.unit || ""} • ${prod.availability || ""}</div>
      </div>
      <div class="sugRight">
        <div><b>${fmt(prod.price)} ${prod.currency || ""}</b></div>
        <div class="sugMeta">Enter/клик — добавить</div>
      </div>
    `;
    row.addEventListener("mouseenter", () => setActive(idx));
    row.addEventListener("click", () => addItem(prod));
    box.appendChild(row);
  });

  setActive(activeIndex);
}

function clearSuggest() {
  el("suggest").innerHTML = "";
  suggestList = [];
  activeIndex = -1;
}

function init() {
  // очистка поиска
  el("btnClear").addEventListener("click", () => {
    el("search").value = "";
    clearSuggest();
    el("search").focus();
  });

  // живой поиск
  el("search").addEventListener("input", () => {
    const q = el("search").value.trim();
    clearTimeout(timer);

    if (q.length < 2) {
      clearSuggest();
      return;
    }

    timer = setTimeout(async () => {
      try {
        const list = await doSearch(q);
        renderSuggest(list);
      } catch (e) {
        el("suggest").innerHTML = `<div class="sugItem"><div class="muted">Ошибка поиска</div></div>`;
      }
    }, 160);
  });

  // управление подсказками клавиатурой
  el("search").addEventListener("keydown", (e) => {
    if (!suggestList.length) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(Math.min(activeIndex + 1, suggestList.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(Math.max(activeIndex - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (activeIndex >= 0) addItem(suggestList[activeIndex]);
    } else if (e.key === "Escape") {
      clearSuggest();
    }
  });

  // PDF (исправлено: тело ответа читаем ОДИН раз)
  el("btnPDF").addEventListener("click", async () => {
    el("status").textContent = "Формируем PDF...";
    try {
      const payload = {
        company: {
          name: el("c_name").value || "",
          phone: el("c_phone").value || "",
          email: el("c_email").value || "",
        },
        client: {
          name: el("cl_name").value || "",
          contact: el("cl_contact").value || "",
          phone: el("cl_phone").value || "",
        },
        vat_rate: Number(el("vat_rate").value || 0),
        validity_days: Number(el("validity").value || 3),
        payment_terms: el("terms").value || "",
        delivery_terms: "",
        notes: "",
        items: items.map((x) => ({
          id: x.id,
          name: x.name,
          unit: x.unit,
          price: Number(x.price || 0),
          currency: x.currency || "",
          qty: Number(x.qty || 0),
          discount: Number(x.discount || 0),
          group: x.group || "",
        })),
      };

      if (payload.items.length === 0) {
        el("status").textContent = "Добавь хотя бы 1 позицию.";
        return;
      }

      const r = await fetch("/api/quote/pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!r.ok) {
        // читаем только text() один раз
        const txt = await r.text();
        let msg = txt;

        // если это JSON с error/traceback — аккуратно вытащим error
        try {
          const obj = JSON.parse(txt);
          msg = obj.error || obj.detail || txt;
        } catch (_) {}

        el("status").textContent = "Ошибка PDF: " + msg;
        return;
      }

      // OK: читаем blob (PDF)
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);

      const a = document.createElement("a");
      a.href = url;
      a.download = "KP.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();

      URL.revokeObjectURL(url);
      el("status").textContent = "PDF готов.";
    } catch (e) {
      el("status").textContent = "Ошибка: " + (e?.message || e);
    }
  });

  renderTable();
}

init();


