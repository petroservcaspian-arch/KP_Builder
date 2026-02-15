// static/app.js
const $ = (sel) => document.querySelector(sel);

function money(n) {
  const v = Number(n || 0);
  return v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function num(x, def = 0) {
  const v = Number(String(x ?? "").replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(v) ? v : def;
}

function stripHtml(s) {
  if (!s) return "";
  return String(s).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

let cart = []; // {id,name,description,image_url,unit,price,qty,discount}

async function apiSearch(q) {
  const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=20`);
  return await r.json();
}

function recalc() {
  let subtotal = 0;
  for (const it of cart) {
    const qty = num(it.qty, 0);
    const price = num(it.price, 0);
    const disc = num(it.discount, 0);
    subtotal += qty * price * (1 - disc / 100);
  }
  const vatRate = num($("#vat_rate")?.value, 0);
  const vat = subtotal * (vatRate / 100);
  const total = subtotal + vat;

  $("#sum_subtotal").textContent = money(subtotal);
  $("#sum_vat").textContent = money(vat);
  $("#sum_total").textContent = money(total);
}

function renderCart() {
  const host = $("#cart");
  host.innerHTML = "";

  cart.forEach((it, idx) => {
    const row = document.createElement("div");
    row.className = "kp-row";

    row.innerHTML = `
      <div class="kp-cell kp-prod">
        <div class="kp-prod-title">${it.name}</div>
        <div class="kp-prod-sub">${it.group || ""}</div>
        <textarea class="kp-desc" rows="2" placeholder="Краткое описание (редактируемое)">${it.description || ""}</textarea>
        ${it.image_url ? `<div class="kp-link"><a href="${it.image_url}" target="_blank" rel="noreferrer">Фото</a></div>` : ``}
      </div>

      <div class="kp-cell kp-unit">${it.unit || ""}</div>

      <div class="kp-cell kp-price">
        <input class="kp-inp" type="text" value="${money(it.price)}" data-k="price" data-i="${idx}">
        <div class="kp-curr">KZT</div>
      </div>

      <div class="kp-cell kp-qty">
        <input class="kp-inp" type="number" min="0" step="1" value="${it.qty ?? 1}" data-k="qty" data-i="${idx}">
      </div>

      <div class="kp-cell kp-disc">
        <input class="kp-inp" type="number" min="0" step="1" value="${it.discount ?? 0}" data-k="discount" data-i="${idx}">
      </div>

      <div class="kp-cell kp-sum" id="line_${idx}"></div>

      <div class="kp-cell kp-del">
        <button class="kp-x" data-del="${idx}">×</button>
      </div>
    `;

    host.appendChild(row);

    // line sum
    const qty = num(it.qty, 0);
    const price = num(it.price, 0);
    const disc = num(it.discount, 0);
    const s = qty * price * (1 - disc / 100);
    row.querySelector(`#line_${idx}`).textContent = money(s);

    // description binding
    row.querySelector(".kp-desc").addEventListener("input", (e) => {
      cart[idx].description = stripHtml(e.target.value); // чистим теги даже если вставили
      recalc();
    });
  });

  // bind inputs
  host.querySelectorAll("input.kp-inp").forEach(inp => {
    inp.addEventListener("input", (e) => {
      const i = Number(e.target.dataset.i);
      const k = e.target.dataset.k;
      if (k === "price") {
        cart[i].price = num(e.target.value, 0);
      } else if (k === "qty") {
        cart[i].qty = num(e.target.value, 0);
      } else if (k === "discount") {
        cart[i].discount = num(e.target.value, 0);
      }
      renderCart();
      recalc();
    });
  });

  // delete
  host.querySelectorAll("button.kp-x").forEach(btn => {
    btn.addEventListener("click", () => {
      const i = Number(btn.dataset.del);
      cart.splice(i, 1);
      renderCart();
      recalc();
    });
  });

  recalc();
}

function renderResults(list) {
  const host = $("#results");
  host.innerHTML = "";
  list.forEach(item => {
    const card = document.createElement("div");
    card.className = "res-card";
    card.innerHTML = `
      <div class="res-title">${item.name}</div>
      <div class="res-sub">${item.group || ""}</div>
      <div class="res-desc">${item.description ? item.description : ""}</div>
      <div class="res-meta">
        <span>${item.unit || ""}</span>
        <span>${money(item.price)} ${item.currency || ""}</span>
      </div>
      <button class="res-add">Добавить</button>
    `;
    card.querySelector(".res-add").addEventListener("click", () => {
      cart.push({
        id: item.id,
        name: item.name,
        group: item.group || "",
        description: item.description || "",
        image_url: item.image_url || "",
        unit: item.unit || "",
        price: item.price || 0,
        qty: 1,
        discount: 0
      });
      renderCart();
      recalc();
    });
    host.appendChild(card);
  });
}

async function onSearch() {
  const q = $("#q").value.trim();
  if (q.length < 2) return;
  const list = await apiSearch(q);
  renderResults(list);
}

async function downloadPdf() {
  if (!cart.length) {
    alert("Добавь хотя бы 1 позицию");
    return;
  }

  const payload = {
    quote_no: $("#quote_no")?.value?.trim() || "",
    vat_rate: num($("#vat_rate")?.value, 0),
    validity_days: num($("#validity_days")?.value, 3),
    payment_terms: $("#payment_terms")?.value || "",
    company: {
      name: "TENT GLOBAL SOLUTION",
      phone: "+77785665001",
      email: "tentatyrau@gmail.com"
    },
    client: {
      type: ($("#client_type")?.value || "").trim(),
      contact: ($("#client_contact")?.value || "").trim(),
      phone: ($("#client_phone")?.value || "").trim()
    },
    items: cart.map(it => ({
      id: it.id,
      name: it.name,
      description: stripHtml(it.description || ""),
      image_url: (it.image_url || "").trim(),
      unit: it.unit,
      price: num(it.price, 0),
      qty: num(it.qty, 0),
      discount: num(it.discount, 0)
    }))
  };

  const r = await fetch("/api/quote/pdf", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!r.ok) {
    const txt = await r.text();
    alert("Ошибка PDF: " + txt);
    return;
  }

  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = (payload.quote_no && payload.quote_no.trim()) ? `${payload.quote_no}.pdf` : "KP.pdf";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function init() {
  const btnSearch = $("#btn_search");
if (btnSearch) btnSearch.addEventListener("click", onSearch);
  const qEl = $("#q");
if (qEl) {
  qEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter") onSearch();
  });
}
  
  const btnPdf = $("#btn_pdf");
if (btnPdf) btnPdf.addEventListener("click", downloadPdf);

  // пересчёт при смене НДС/прочего
  ["vat_rate", "validity_days", "payment_terms"].forEach(id => {
    const el = $("#" + id);
    if (el) el.addEventListener("input", recalc);
  });

  renderCart();
  recalc();
}

document.addEventListener("DOMContentLoaded", init);
