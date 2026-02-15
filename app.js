// web/app.js

const API = {
  search: (q) => fetch(`/api/search?q=${encodeURIComponent(q)}&limit=15`).then(r => r.json()),
  pdf: (payload) => fetch(`/api/quote/pdf`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
};

function el(id) { return document.getElementById(id); }

function fmtMoney(n) {
  const x = Number(n || 0);
  return x.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function toNum(v) {
  if (v === null || v === undefined) return 0;
  if (typeof v === "number") return v;
  const s = String(v).replace(/\s/g, "").replace(",", ".");
  const x = Number(s);
  return Number.isFinite(x) ? x : 0;
}

let state = {
  query: "",
  results: [],
  items: [],
  vat_rate: 0,
  validity_days: 3,

  client: { name: "", contact: "", phone: "" },

  // поля “как в КП Оп.pdf”, можно заполнять или оставить пустыми
  komu: "Клиент",
  organizaciya: "",
  client_phone: "",
  theme: "",

  supply_terms: "",
  payment_terms: "",
  note: "",
};

function recalc() {
  let subtotal = 0;
  for (const it of state.items) {
    const qty = toNum(it.qty);
    const price = toNum(it.price);
    const disc = toNum(it.discount);
    subtotal += qty * price * (1 - disc / 100);
  }
  const vat = subtotal * (toNum(state.vat_rate) / 100);
  const total = subtotal + vat;

  el("sumSubtotal").textContent = fmtMoney(subtotal);
  el("sumVat").textContent = fmtMoney(vat);
  el("sumTotal").textContent = fmtMoney(total);
}

function renderResults() {
  const box = el("results");
  box.innerHTML = "";

  state.results.forEach((r) => {
    const div = document.createElement("div");
    div.className = "result";

    div.innerHTML = `
      <div class="result-main">
        <div class="result-title">${r.name}</div>
        <div class="result-sub">
          ${r.group ? `<span class="pill">${r.group}</span>` : ""}
          ${r.unit ? `<span class="pill">Ед: ${r.unit}</span>` : ""}
          <span class="pill">Цена: ${fmtMoney(r.price)} ${r.currency || "KZT"}</span>
        </div>
        ${r.desc ? `<div class="result-desc">${r.desc}</div>` : ""}
      </div>
      <button class="btn btn-add">Добавить</button>
    `;

    div.querySelector(".btn-add").addEventListener("click", () => addItem(r));
    box.appendChild(div);
  });
}

function addItem(r) {
  state.items.push({
    id: r.id,
    name: r.name,
    unit: r.unit || "",
    currency: r.currency || "KZT",

    // ВАЖНО: эти поля редактируются и такими уйдут в PDF
    price: toNum(r.price || 0),
    desc: r.desc || "",

    qty: 1,
    discount: 0,
  });
  renderItems();
  recalc();
}

function removeItem(idx) {
  state.items.splice(idx, 1);
  renderItems();
  recalc();
}

function renderItems() {
  const tbody = el("itemsBody");
  tbody.innerHTML = "";

  state.items.forEach((it, idx) => {
    const qty = toNum(it.qty);
    const price = toNum(it.price);
    const disc = toNum(it.discount);
    const sum = qty * price * (1 - disc / 100);

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="c-num">${idx + 1}</td>

      <td class="c-name">
        <div class="name-big">${it.name}</div>
        <textarea class="name-desc" rows="2" placeholder="Краткое описание / характеристика (редактируемое)">${it.desc || ""}</textarea>
      </td>

      <td class="c-unit">${it.unit || ""}</td>

      <td class="c-price">
        <input class="inp inp-money" type="text" value="${fmtMoney(price)}" />
        <div class="muted">KZT</div>
      </td>

      <td class="c-qty">
        <input class="inp" type="number" min="0" step="1" value="${qty}" />
      </td>

      <td class="c-disc">
        <input class="inp" type="number" min="0" step="0.1" value="${disc}" />
      </td>

      <td class="c-sum">${fmtMoney(sum)}</td>

      <td class="c-x"><button class="btn btn-x">✕</button></td>
    `;

    // desc
    tr.querySelector(".name-desc").addEventListener("input", (e) => {
      it.desc = e.target.value;
    });

    // price
    tr.querySelector(".inp-money").addEventListener("input", (e) => {
      // разрешаем ввод "90 000" / "90000" / "90,000"
      const raw = e.target.value;
      it.price = toNum(raw);
      renderItems(); // чтобы красиво форматировалось обратно
      recalc();
    });

    // qty
    tr.querySelector(".c-qty .inp").addEventListener("input", (e) => {
      it.qty = toNum(e.target.value);
      renderItems();
      recalc();
    });

    // discount
    tr.querySelector(".c-disc .inp").addEventListener("input", (e) => {
      it.discount = toNum(e.target.value);
      renderItems();
      recalc();
    });

    // remove
    tr.querySelector(".btn-x").addEventListener("click", () => removeItem(idx));

    tbody.appendChild(tr);
  });
}

async function doSearch() {
  const q = el("q").value.trim();
  if (q.length < 2) return;
  state.results = await API.search(q);
  renderResults();
}

async function downloadPDF() {
  if (!state.items.length) {
    alert("Добавьте хотя бы один товар");
    return;
  }

  // ФИКС компания (с сервера всё равно зафиксировано), но отправим для совместимости:
  const payload = {
    company: {
      name: "TENT GLOBAL SOLUTION",
      phone: "+77785665001",
      email: "tentatyrau@gmail.com",
    },

    client: {
      name: state.client.name || "",
      contact: state.client.contact || "",
      phone: state.client.phone || "",
    },

    // поля “как в КП Оп.pdf”
    komu: state.komu || "Клиент",
    organizaciya: state.organizaciya || "",
    client_phone: state.client_phone || state.client.phone || "",
    theme: state.theme || "",

    vat_rate: toNum(state.vat_rate),
    validity_days: toNum(state.validity_days),

    supply_terms: state.supply_terms || "",
    payment_terms: state.payment_terms || "",
    note: state.note || "",

    items: state.items.map(it => ({
      id: it.id,
      name: it.name,
      desc: it.desc || "",
      unit: it.unit || "",
      currency: it.currency || "KZT",

      // ВАЖНО: берём именно отредактированные значения
      price: toNum(it.price),
      qty: toNum(it.qty),
      discount: toNum(it.discount),
    })),
  };

  const resp = await API.pdf(payload);
  if (!resp.ok) {
    const txt = await resp.text().catch(() => "");
    alert("Ошибка PDF: " + resp.status + "\n" + txt);
    return;
  }

  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);

  const a = document.createElement("a");
  a.href = url;
  a.download = "KP.pdf";
  document.body.appendChild(a);
  a.click();
  a.remove();

  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function init() {
  // search
  el("btnSearch").addEventListener("click", doSearch);
  el("q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch();
  });

  // client fields
  el("clientName").addEventListener("input", (e) => state.client.name = e.target.value);
  el("clientContact").addEventListener("input", (e) => state.client.contact = e.target.value);
  el("clientPhone").addEventListener("input", (e) => state.client.phone = e.target.value);

  // КП-поля
  el("komu").addEventListener("input", (e) => state.komu = e.target.value);
  el("organizaciya").addEventListener("input", (e) => state.organizaciya = e.target.value);
  el("client_phone").addEventListener("input", (e) => state.client_phone = e.target.value);
  el("theme").addEventListener("input", (e) => state.theme = e.target.value);

  el("vat").addEventListener("input", (e) => { state.vat_rate = e.target.value; recalc(); });
  el("validity").addEventListener("input", (e) => { state.validity_days = e.target.value; });

  el("supply_terms").addEventListener("input", (e) => state.supply_terms = e.target.value);
  el("payment_terms").addEventListener("input", (e) => state.payment_terms = e.target.value);
  el("note").addEventListener("input", (e) => state.note = e.target.value);

  // pdf
  el("btnPdf").addEventListener("click", downloadPDF);

  recalc();
}

document.addEventListener("DOMContentLoaded", init);
