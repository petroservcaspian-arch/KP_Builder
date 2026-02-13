# app/main.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from rapidfuzz import fuzz
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ---------------- Пути ----------------
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"
WEB_DIR = ROOT / "web"
OUTPUT_DIR = ROOT / "output"
FONTS_DIR = ROOT / "fonts"
OUTPUT_DIR.mkdir(exist_ok=True)

DATA_PATH = DATA_DIR / "catalog.xlsx"

# ---------------- App ----------------
app = FastAPI(title="KP Builder (ReportLab)")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------- Загрузка каталога ----------------
if not DATA_PATH.exists():
    raise FileNotFoundError(f"Не найден файл каталога: {DATA_PATH}")

df = pd.read_excel(DATA_PATH)

NEEDED = ["id", "name", "price", "currency", "unit", "group_name", "Наличие"]
for col in NEEDED:
    if col not in df.columns:
        df[col] = None

df["name"] = df["name"].astype(str).fillna("").str.strip()
df["name_norm"] = (
    df["name"]
    .astype(str)
    .str.lower()
    .str.replace(r"\s+", " ", regex=True)
    .str.strip()
)
NAMES = df["name_norm"].tolist()


# ---------------- Поиск ----------------
def _tokenize(q: str) -> List[str]:
    q = (q or "").lower().strip()
    out, cur = [], []
    for ch in q:
        if ch.isalnum() or ch in ["-", "_", ".", "№"]:
            cur.append(ch)
        else:
            if cur:
                out.append("".join(cur))
                cur = []
    if cur:
        out.append("".join(cur))
    return [t for t in out if len(t) >= 2]


def _score(name_norm: str, q: str, tokens: List[str]) -> float:
    if not name_norm:
        return 0.0
    for t in tokens:
        if t not in name_norm:
            return 0.0
    base = fuzz.WRatio(q, name_norm)  # 0..100
    bonus = 0.0
    if q in name_norm:
        bonus += 15.0
    if name_norm.startswith(q):
        bonus += 10.0
    return base + bonus


@app.get("/", response_class=HTMLResponse)
def home():
    index = WEB_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h3>Нет web/index.html</h3>", status_code=500)
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/search")
def search(q: str = Query("", min_length=1), limit: int = 15):
    qn = (q or "").lower().strip()
    if len(qn) < 2:
        return []
    tokens = _tokenize(qn)

    scored: List[Tuple[float, int]] = []
    for idx, name_norm in enumerate(NAMES):
        s = _score(name_norm, qn, tokens)
        if s > 0:
            scored.append((s, idx))

    scored.sort(reverse=True, key=lambda x: x[0])
    scored = scored[:limit]

    results = []
    for s, idx in scored:
        row = df.iloc[idx]
        results.append({
            "id": int(row["id"]) if pd.notna(row["id"]) else (idx + 1),
            "name": row["name"],
            "unit": row["unit"] if pd.notna(row["unit"]) else "",
            "price": float(row["price"]) if pd.notna(row["price"]) else 0.0,
            "currency": row["currency"] if pd.notna(row["currency"]) else "KZT",
            "group": row["group_name"] if pd.notna(row["group_name"]) else "",
            "availability": row["Наличие"] if pd.notna(row["Наличие"]) else "",
            "score": round(float(s), 2),
        })
    return results


# ---------------- Расчёты ----------------
def to_num(x, default: float = 0.0) -> float:
    try:
        if x is None:
            return float(default)
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip().replace("\u00a0", "").replace(" ", "")
        s = s.replace(",", ".")
        if s == "":
            return float(default)
        return float(s)
    except Exception:
        return float(default)


def calc_totals(items: List[Dict[str, Any]], vat_rate: float = 0.0) -> Dict[str, float]:
    subtotal = 0.0
    for it in items:
        qty = to_num(it.get("qty"), 0.0)
        price = to_num(it.get("price"), 0.0)
        disc = to_num(it.get("discount"), 0.0)
        subtotal += qty * price * (1 - disc / 100.0)
    vat = subtotal * (vat_rate / 100.0)
    total = subtotal + vat
    return {"subtotal": round(subtotal, 2), "vat": round(vat, 2), "total": round(total, 2)}


# ---------------- Шрифты ----------------
def _pick_font_file(candidates: List[str]) -> Path:
    for name in candidates:
        p = FONTS_DIR / name
        if p.exists():
            return p
    raise FileNotFoundError(
        "Не найдены файлы шрифтов в папке fonts/. "
        "Нужны DejaVuSans.ttf и DejaVuSans-Bold.ttf (или варианты с [1])."
    )


def _register_fonts() -> Tuple[str, str]:
    regular = _pick_font_file(["DejaVuSans.ttf", "DejaVuSans[1].ttf"])
    bold = _pick_font_file(["DejaVuSans-Bold.ttf", "DejaVuSans-Bold[1].ttf"])
    pdfmetrics.registerFont(TTFont("TGS-Regular", str(regular)))
    pdfmetrics.registerFont(TTFont("TGS-Bold", str(bold)))
    return "TGS-Regular", "TGS-Bold"


# ---------------- PDF (ReportLab) ----------------
@app.post("/api/quote/pdf")
def make_pdf(payload: Dict[str, Any] = Body(...)):
    company = payload.get("company") or {}
    client = payload.get("client") or {}
    items = payload.get("items") or []

    vat_rate = to_num(payload.get("vat_rate"), 0.0)
    validity_days = int(to_num(payload.get("validity_days"), 3))
    terms = str(payload.get("payment_terms") or "")

    if not items:
        return JSONResponse(status_code=400, content={"error": "Пустой список товаров"})

    # фиксированные контакты
    company_name = str(company.get("name") or "TENT GLOBAL SOLUTION").strip()
    company_phone = str(company.get("phone") or "+77785665001").strip()
    company_email = str(company.get("email") or "tentatyrau@gmail.com").strip()

    client_name = str(client.get("name") or "Клиент").strip()
    client_contact = str(client.get("contact") or "").strip()
    client_phone = str(client.get("phone") or "").strip()

    totals = calc_totals(items, vat_rate=vat_rate)
    now = datetime.now()
    quote_no = str(payload.get("quote_no") or f"KP-{now:%Y%m%d-%H%M%S}").strip()
    out_path = OUTPUT_DIR / f"{quote_no}.pdf"

    font_name, font_bold = _register_fonts()

    def money(v: Any) -> str:
        v = to_num(v, 0.0)
        return f"{v:,.2f}".replace(",", " ")

    def fmt_qty(q: float) -> str:
        if abs(q - round(q)) < 1e-9:
            return str(int(round(q)))
        return f"{q:.3f}".rstrip("0").rstrip(".")

    # --- Цвета ---
    BRAND = colors.HexColor("#0E5D8A")
    BRAND_DARK = colors.HexColor("#0A3E5F")
    GOLD = colors.HexColor("#D4AF37")
    LINE = colors.HexColor("#D7DCE6")
    MUTED = colors.HexColor("#6B7280")
    TEXT = colors.HexColor("#111827")
    ZEBRA = colors.HexColor("#F6F8FC")

    cnv = canvas.Canvas(str(out_path), pagesize=A4)
    W, H = A4
    left = 16 * mm
    right = W - 16 * mm
    table_w = right - left

    # ---- КОЛОНКИ: ширины строго суммой = table_w (178mm)
    # No 9mm, Name 92mm, Unit 12mm, Price 22mm, Qty 15mm, Sum 28mm  => 178mm
    w_no = 9 * mm
    w_name = 92 * mm
    w_unit = 12 * mm
    w_price = 22 * mm
    w_qty = 15 * mm
    w_sum = table_w - (w_no + w_name + w_unit + w_price + w_qty)  # остаток

    x_no_l = left
    x_no_r = x_no_l + w_no

    x_name_l = x_no_r
    x_name_r = x_name_l + w_name

    x_unit_l = x_name_r
    x_unit_r = x_unit_l + w_unit

    x_price_l = x_unit_r
    x_price_r = x_price_l + w_price

    x_qty_l = x_price_r
    x_qty_r = x_qty_l + w_qty

    x_sum_l = x_qty_r
    x_sum_r = right  # конец таблицы

    def draw_center(xl: float, xr: float, y: float, text: str, font: str, size: float, color=colors.white):
        cnv.setFillColor(color)
        cnv.setFont(font, size)
        cx = (xl + xr) / 2
        cnv.drawCentredString(cx, y, text)

    def header() -> float:
        header_h = 38 * mm
        cnv.setFillColor(BRAND)
        cnv.rect(0, H - header_h, W, header_h, stroke=0, fill=1)

        cnv.setStrokeColor(GOLD)
        cnv.setLineWidth(1.2)
        cnv.line(0, H - header_h, W, H - header_h)

        logo_path = (STATIC_DIR / "logo.png").resolve()
        if logo_path.exists():
            cnv.drawImage(str(logo_path), left, H - header_h + 6 * mm, width=42 * mm, height=26 * mm, mask="auto")

        cnv.setFillColor(GOLD)
        cnv.setFont(font_bold, 16)
        cnv.drawString(left + 48 * mm, H - 16 * mm, "TENT GLOBAL")
        cnv.drawString(left + 48 * mm, H - 24 * mm, "SOLUTION")

        cnv.setFillColor(colors.white)
        cnv.setFont(font_name, 11)
        cnv.drawString(left + 48 * mm, H - 31 * mm, "ТЕНТЫ • БРЕЗЕНТ • АНГАРЫ")

        cnv.setFillColor(colors.white)
        cnv.setFont(font_bold, 11)
        cnv.drawRightString(right, H - 16 * mm, f"КП № {quote_no}")
        cnv.setFont(font_name, 9)
        cnv.drawRightString(right, H - 22 * mm, f"Дата: {now:%d.%m.%Y}")

        cnv.setFont(font_name, 9)
        cnv.drawRightString(right, H - header_h + 6 * mm, f"{company_phone}   |   {company_email}")

        return H - header_h - 10 * mm

    y = header()

    # ---- Поставщик/Клиент ----
    box_h = 26 * mm
    gap = 6 * mm
    mid = (left + right) / 2

    def box(x1, y1, x2, y2):
        cnv.setFillColor(colors.white)
        cnv.setStrokeColor(LINE)
        cnv.roundRect(x1, y1, x2 - x1, y2 - y1, 6, stroke=1, fill=1)

    box(left, y - box_h, mid - gap / 2, y)
    box(mid + gap / 2, y - box_h, right, y)

    cnv.setFillColor(TEXT)
    cnv.setFont(font_bold, 10)
    cnv.drawString(left + 8, y - 10, "Поставщик")
    cnv.setFont(font_name, 9)
    cnv.drawString(left + 8, y - 22, company_name)
    cnv.setFillColor(MUTED)
    cnv.drawString(left + 8, y - 34, f"Тел: {company_phone}")
    cnv.drawString(left + 8, y - 44, f"Email: {company_email}")

    cnv.setFillColor(TEXT)
    cnv.setFont(font_bold, 10)
    cnv.drawString(mid + gap / 2 + 8, y - 10, "Клиент")
    cnv.setFont(font_name, 9)
    cnv.drawString(mid + gap / 2 + 8, y - 22, client_name)
    cnv.setFillColor(MUTED)
    cnv.drawString(mid + gap / 2 + 8, y - 34, f"Контакт: {client_contact}")
    cnv.drawString(mid + gap / 2 + 8, y - 44, f"Тел: {client_phone}")

    y -= (box_h + 10 * mm)

    # ---- Шапка таблицы (НИЧЕГО НЕ НАЛЕЗЕТ) ----
    def table_header():
        nonlocal y
        h = 10 * mm
        cnv.setFillColor(BRAND_DARK)
        cnv.roundRect(left, y - h, table_w, h, 5, stroke=0, fill=1)

        cnv.setStrokeColor(GOLD)
        cnv.setLineWidth(1)
        cnv.line(left, y - h, right, y - h)

        # Заголовки по центру каждой колонки
        draw_center(x_no_l, x_no_r, y - 7 * mm, "№", font_bold, 9)
        cnv.setFillColor(colors.white)
        cnv.setFont(font_bold, 9)
        cnv.drawString(x_name_l + 3, y - 7 * mm, "Наименование")
        draw_center(x_unit_l, x_unit_r, y - 7 * mm, "Ед.", font_bold, 9)
        draw_center(x_price_l, x_price_r, y - 7 * mm, "Цена", font_bold, 9)
        draw_center(x_qty_l, x_qty_r, y - 7 * mm, "Кол-во", font_bold, 9)
        draw_center(x_sum_l, x_sum_r, y - 7 * mm, "Сумма", font_bold, 9)

        y -= (h + 2 * mm)

    def new_page():
        nonlocal y
        cnv.showPage()
        y = header()
        table_header()

    table_header()

    # ---- Строки ----
    row_h = 9 * mm

    for i, it in enumerate(items, start=1):
        if y < 55 * mm:
            new_page()

        if i % 2 == 0:
            cnv.setFillColor(ZEBRA)
            cnv.rect(left, y - row_h + 1, table_w, row_h, stroke=0, fill=1)

        name = str(it.get("name") or "").strip()
        unit = str(it.get("unit") or "").strip()
        price = to_num(it.get("price"), 0.0)
        qty = to_num(it.get("qty"), 0.0)
        disc = to_num(it.get("discount"), 0.0)
        line_sum = qty * price * (1 - disc / 100.0)

        cnv.setFillColor(TEXT)
        cnv.setFont(font_name, 9)

        cnv.drawString(x_no_l + 3, y - 6 * mm, str(i))

        # обрезаем по ширине, чтобы не лезло в "Ед."
        max_name_width = (x_name_r - 6) - (x_name_l + 3)
        show_name = name
        while cnv.stringWidth(show_name, font_name, 9) > max_name_width and len(show_name) > 4:
            show_name = show_name[:-1]
        if show_name != name:
            show_name = show_name[:-3] + "..."
        cnv.drawString(x_name_l + 3, y - 6 * mm, show_name)

        cnv.setFillColor(MUTED)
        cnv.drawCentredString((x_unit_l + x_unit_r) / 2, y - 6 * mm, unit)

        cnv.setFillColor(TEXT)
        cnv.drawRightString(x_price_r - 3, y - 6 * mm, money(price))
        cnv.drawRightString(x_qty_r - 3, y - 6 * mm, fmt_qty(qty))
        cnv.drawRightString(x_sum_r - 3, y - 6 * mm, money(line_sum))

        y -= row_h

    # линия под таблицей
    cnv.setStrokeColor(LINE)
    cnv.setLineWidth(1)
    cnv.line(left, y, right, y)
    y -= 10 * mm

    # ---- Итоги ----
    box_w = 90 * mm
    box_h2 = 34 * mm
    box_x = right - box_w
    box_y = y - box_h2

    cnv.setFillColor(colors.white)
    cnv.setStrokeColor(LINE)
    cnv.roundRect(box_x, box_y, box_w, box_h2, 6, stroke=1, fill=1)

    cnv.setFillColor(TEXT)
    cnv.setFont(font_bold, 10)
    cnv.drawString(box_x + 10, box_y + box_h2 - 10 * mm, "Итоги")

    cnv.setFont(font_name, 9)
    cnv.drawString(box_x + 10, box_y + box_h2 - 18 * mm, "Сумма:")
    cnv.drawRightString(box_x + box_w - 10, box_y + box_h2 - 18 * mm, money(totals["subtotal"]))

    cnv.drawString(box_x + 10, box_y + box_h2 - 25 * mm, f"НДС ({vat_rate}%):")
    cnv.drawRightString(box_x + box_w - 10, box_y + box_h2 - 25 * mm, money(totals["vat"]))

    cnv.setFont(font_bold, 10)
    cnv.drawString(box_x + 10, box_y + 6 * mm, "Итого:")
    cnv.drawRightString(box_x + box_w - 10, box_y + 6 * mm, money(totals["total"]))

    y = box_y - 10 * mm

    cnv.setFillColor(TEXT)
    cnv.setFont(font_bold, 9)
    cnv.drawString(left, y, f"Срок действия КП: {validity_days} дней")

    # footer
    cnv.setStrokeColor(GOLD)
    cnv.setLineWidth(1)
    cnv.line(left, 14 * mm, right, 14 * mm)

    cnv.setFillColor(BRAND_DARK)
    cnv.setFont(font_name, 8.5)
    cnv.drawString(left, 9 * mm, f"{company_name} • {company_phone} • {company_email}")

    cnv.save()
    return FileResponse(path=str(out_path), filename=out_path.name, media_type="application/pdf")
