// static/app.js - ИСПРАВЛЕННАЯ ВЕРСИЯ
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

  // ИСПРАВЛЕНО: правильные ID элементов
  const subtotalEl = $("#subtotal");
  const vatEl = $("#vat");
  const totalEl = $("#total");
  
  if (subtotalEl) subtotalEl.textContent = money(subtotal);
  if (vatEl) vatEl.textContent = money(vat);
  if (totalEl) totalEl.textContent = money(total);
}

function renderCart() {
  // ИСПРАВЛЕНО: правильный ID таблицы
  const host = $("#itemsBody");
  if (!host) return;
  
  host.innerHTML = "";

  cart.forEach((it, idx) => {
    const row = document.createElement("tr");

    const qty = num(it.qty, 1);
    const price = num(it.price, 0);
    const disc = num(it.discount, 0);
    const lineSum = qty * price * (1 - disc / 100);

    row.innerHTML = `
      <td>
        <div style="font-weight: 600; margin-bottom: 4px;">${it.name}</div>
        <textarea class="input" rows="2" placeholder="Описание" data-idx="${idx}" data-field="description" style="width: 100%; font-size: 13px;">${it.description || ""}</textarea>
        ${it.image_url ? `<div style="margin-top: 4px;"><a href="${it.image_url}" target="_blank" style="font-size: 12px; color: #2563eb;">📷 Фото</a></div>` : ''}
      </td>
      <td>${it.unit || ""}</td>
      <td>
        <input class="input" type="number" min="0" step="0.01" value="${price}" data-idx="${idx}" data-field="price" style="width: 100px;">
      </td>
      <td>
        <input class="input" type="number" min="0" step="1" value="${qty}" data-idx="${idx}" data-field="qty" style="width: 70px;">
      </td>
      <td>
        <input class="input" type="number" min="0" max="100" step="1" value="${disc}" data-idx="${idx}" data-field="discount" style="width: 70px;">
      </td>
      <td style="text-align: right; font-weight: 600;">${money(lineSum)}</td>
      <td>
        <button class="btn ghost" data-del="${idx}" style="padding: 4px 8px;">×</button>
      </td>
    `;

    host.appendChild(row);

    // Обработчики изменений
    row.querySelectorAll("input, textarea").forEach(inp => {
      inp.addEventListener("input", (e) => {
        const i = Number(e.target.dataset.idx);
        const field = e.target.dataset.field;
        
        if (field === "price") {
          cart[i].price = num(e.target.value, 0);
        } else if (field === "qty") {
          cart[i].qty = num(e.target.value, 0);
        } else if (field === "discount") {
          cart[i].discount = num(e.target.value, 0);
        } else if (field === "description") {
          cart[i].description = stripHtml(e.target.value);
        }
        
        renderCart();
        recalc();
      });
    });

    // Кнопка удаления
    const delBtn = row.querySelector("[data-del]");
    if (delBtn) {
      delBtn.addEventListener("click", () => {
        const i = Number(delBtn.dataset.del);
        cart.splice(i, 1);
        renderCart();
        recalc();
      });
    }
  });

  recalc();
}

// НОВАЯ ФУНКЦИЯ: отображение результатов поиска
function renderSuggestions(list) {
  const host = $("#suggest");
  if (!host) return;
  
  host.innerHTML = "";
  
  if (list.length === 0) {
    host.innerHTML = '<div style="padding: 16px; text-align: center; color: #9ca3af;">Ничего не найдено</div>';
    return;
  }

  list.forEach(item => {
    const div = document.createElement("div");
    div.className = "suggest-item";
    
    div.innerHTML = `
      <div style="font-weight: 600; margin-bottom: 4px;">${item.name}</div>
      <div style="display: flex; justify-content: space-between; font-size: 14px;">
        <span style="color: #6b7280;">${item.group_name || ""}</span>
        <span style="color: #2563eb; font-weight: 600;">${money(item.price)} ${item.currency || "KZT"}</span>
      </div>
    `;
    
    div.addEventListener("click", () => {
      // Берем первое фото из списка
      const firstImage = (item.image_url || "").split(",")[0].trim();
      
      cart.push({
        id: item.id,
        name: item.name,
        description: stripHtml(item.description || ""),
        image_url: firstImage,
        unit: item.unit || "",
        price: item.price || 0,
        qty: 1,
        discount: 0
      });
      
      renderCart();
      host.innerHTML = "";
      
      const searchInput = $("#search");
      if (searchInput) searchInput.value = "";
    });
    
    host.appendChild(div);
  });
}

async function downloadPdf() {
  if (!cart.length) {
    alert("Добавьте хотя бы 1 позицию в КП");
    return;
  }

  // ИСПРАВЛЕНО: правильные ID всех элементов
  const payload = {
    quote_no: $("#quote_no")?.value?.trim() || "",
    vat_rate: num($("#vat_rate")?.value, 0),
    validity_days: num($("#validity")?.value, 3),  // ИСПРАВЛЕНО
    payment_terms: $("#terms")?.value || "",  // ИСПРАВЛЕНО
    company: {
      name: $("#c_name")?.value || "TENT GLOBAL SOLUTION",
      phone: $("#c_phone")?.value || "+77785665001",
      email: $("#c_email")?.value || "tentatyrau@gmail.com"
    },
    client: {
      type: "",
      contact: $("#cl_contact")?.value?.trim() || "",  // ИСПРАВЛЕНО
      phone: $("#cl_phone")?.value?.trim() || ""  // ИСПРАВЛЕНО
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

  const statusEl = $("#status");
  if (statusEl) statusEl.textContent = "Генерация PDF...";

  try {
    const r = await fetch("/api/quote/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!r.ok) {
      const txt = await r.text();
      alert("Ошибка создания PDF: " + txt);
      if (statusEl) statusEl.textContent = "";
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

    if (statusEl) {
      statusEl.textContent = "✅ PDF скачан!";
      setTimeout(() => { statusEl.textContent = ""; }, 3000);
    }
  } catch (err) {
    alert("Ошибка: " + err.message);
    if (statusEl) statusEl.textContent = "";
  }
}

// ИСПРАВЛЕННАЯ ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ
function init() {
  // Поиск с задержкой (debounce)
  const searchInput = $("#search");
  if (searchInput) {
    let searchTimeout;
    searchInput.addEventListener("input", async (e) => {
      clearTimeout(searchTimeout);
      const q = e.target.value.trim();
      
      if (q.length >= 2) {
        searchTimeout = setTimeout(async () => {
          try {
            const results = await apiSearch(q);
            renderSuggestions(results);
          } catch (err) {
            console.error("Ошибка поиска:", err);
          }
        }, 300);
      } else {
        const suggest = $("#suggest");
        if (suggest) suggest.innerHTML = "";
      }
    });
  }

  // Кнопка очистки
  const btnClear = $("#btnClear");
  if (btnClear) {
    btnClear.addEventListener("click", () => {
      if (searchInput) searchInput.value = "";
      const suggest = $("#suggest");
      if (suggest) suggest.innerHTML = "";
    });
  }

  // Кнопка PDF - ИСПРАВЛЕНО: правильный ID
  const btnPdf = $("#btnPDF");
  if (btnPdf) {
    btnPdf.addEventListener("click", downloadPdf);
  }

  // Пересчет при изменении НДС/срока
  const vatRate = $("#vat_rate");
  if (vatRate) {
    vatRate.addEventListener("input", recalc);
  }
  
  const validity = $("#validity");
  if (validity) {
    validity.addEventListener("input", recalc);
  }

  renderCart();
  recalc();
}

document.addEventListener("DOMContentLoaded", init);
