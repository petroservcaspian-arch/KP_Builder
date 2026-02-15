# app/main.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from rapidfuzz import fuzz

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table, TableStyle,
    Paragraph, Spacer, Image as RLImage,
)
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

# ---------------- Константы компании (фиксируем навсегда) ----------------
FIX_COMPANY = {
    "name": "TENT GLOBAL SOLUTION",
    "phone": "+77785665001",
    "email": "tentatyrau@gmail.com",
}

# ---------------- App ----------------
app = FastAPI(title="KP Builder (ReportLab)")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------- Утилиты ----------------
def to_num(x: Any, default: float = 0.0) -> float:
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


def safe(s: Any) -> str:
    return str(s or "").strip()


def money(v: Any) -> str:
    v = to_num(v, 0.0)
    return f"{v:,.2f}".replace(",", " ")


def fmt_qty(q: Any) -> str:
    qf = to_num(q, 0.0)
    if abs(qf - round(qf)) < 1e-9:
        return str(int(round(qf)))
    return f"{qf:.3f}".rstrip("0").rstrip(".")


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


# ---------------- Шрифты (чтобы кириллица не превращалась в квадраты) ----------------
def register_fonts() -> Dict[str, str]:
    """
    Ищем DejaVuSans/DejaVuSans-Bold в папке fonts.
    Ты уже залил ttf в GitHub (на скрине они есть), но названия могут быть:
    - DejaVuSans.ttf / DejaVuSans-Bold.ttf
    - DejaVuSans[1].ttf / DejaVuSans-Bold[1].ttf
    """
    regular = None
    bold = None

    if FONTS_DIR.exists():
        for p in FONTS_DIR.glob("*.ttf"):
            name = p.name.lower()
            if "dejavu" in name and "bold" not in name and regular is None:
                regular = p
            if "dejavu" in name and "bold" in name and bold is None:
                bold = p

    # fallback (на всякий)
    if regular is None or bold is None:
        return {"regular": "Helvetica", "bold": "Helvetica-Bold"}

    pdfmetrics.registerFont(TTFont("KPFont", str(regular)))
    pdfmetrics.registerFont(TTFont("KPFontBold", str(bold)))
    return {"regular": "KPFont", "bold": "KPFontBold"}


FONTS = register_fonts()


# ---------------- Загрузка каталога ----------------
if not DATA_PATH.exists():
    raise FileNotFoundError(f"Не найден файл каталога: {DATA_PATH}")

df = pd.read_excel(DATA_PATH)

# Ищем колонку описания автоматически (под разные файлы)
DESC_CANDIDATES = [
    "desc", "description", "описание", "характеристика", "краткое описание", "short_desc",
    "summary", "примечание", "note"
]
desc_col: Optional[str] = None
for c in df.columns:
    cl = str(c).strip().lower()
    if cl in DESC_CANDIDATES:
        desc_col = c
        break

NEEDED = ["id", "name", "price", "currency", "unit", "group_name", "Наличие"]
for col in NEEDED:
    if col not in df.columns:
        df[col] = None

df["name"] = df["name"].astype(str).fillna("").str.strip()
df["name_norm"] = (
    df["name"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
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

    scored: List[tuple[float, int]] = []
    for idx, name_norm in enumerate(NAMES):
        s = _score(name_norm, qn, tokens)
        if s > 0:
            scored.append((s, idx))

    scored.sort(reverse=True, key=lambda x: x[0])
    scored = scored[:limit]

    results = []
    for s, idx in scored:
        row = df.iloc[idx]
        desc_val = ""
        if desc_col and desc_col in df.columns and pd.notna(row.get(desc_col)):
            desc_val = str(row.get(desc_col)).strip()

        results.append({
            "id": int(row["id"]) if pd.notna(row["id"]) else (idx + 1),
            "name": row["name"],
            "desc": desc_val,  # <- КРАТКОЕ ОПИСАНИЕ ДЛЯ UI (редактируемое)
            "unit": row["unit"] if pd.notna(row["unit"]) else "",
            "price": float(row["price"]) if pd.notna(row["price"]) else 0.0,
            "currency": row["currency"] if pd.notna(row["currency"]) else "KZT",
            "group": row["group_name"] if pd.notna(row["group_name"]) else "",
            "availability": row["Наличие"] if pd.notna(row["Наличие"]) else "",
            "score": round(float(s), 2),
        })
    return results


# ---------------- PDF (ReportLab + Table) ----------------
@app.post("/api/quote/pdf")
def make_pdf(payload: Dict[str, Any] = Body(...)):
    company_in = payload.get("company") or {}
    client = payload.get("client") or {}
    items = payload.get("items") or []
    vat_rate = to_num(payload.get("vat_rate"), 0.0)
    validity_days = int(to_num(payload.get("validity_days"), 3))

    # (по желанию) поля “как в КП Оп”
    komu = safe(payload.get("komu") or safe(client.get("name")) or "Клиент")
    organizaciya = safe(payload.get("organizaciya") or "")
    client_phone = safe(payload.get("client_phone") or safe(client.get("phone")))
    theme = safe(payload.get("theme") or "")

    supply_terms = safe(payload.get("supply_terms") or "")      # "Срок поставки: ..."
    payment_terms = safe(payload.get("payment_terms") or "")    # "Условия оплаты: ..."
    note_block = safe(payload.get("note") or "")                # доп. текст

    if not items:
        return JSONResponse(status_code=400, content={"error": "Пустой список товаров"})

    # Фиксируем компанию (как ты просил) независимо от payload
    company = {
        "name": FIX_COMPANY["name"],
        "phone": FIX_COMPANY["phone"],
        "email": FIX_COMPANY["email"],
    }

    totals = calc_totals(items, vat_rate=vat_rate)
    now = datetime.now()
    quote_no = safe(payload.get("quote_no")) or f"KP-{now:%Y%m%d-%H%M%S}"
    out_path = OUTPUT_DIR / f"{quote_no}.pdf"

    # --- Стиль/цвета (как твой бренд) ---
    BRAND = colors.HexColor("#0E5D8A")
    BRAND_DARK = colors.HexColor("#0A3E5F")
    GOLD = colors.HexColor("#D4AF37")
    LINE = colors.HexColor("#D7DCE6")
    MUTED = colors.HexColor("#6B7280")
    TEXT = colors.HexColor("#111827")
    ZEBRA = colors.HexColor("#F6F8FC")

    # --- Документ ---
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=12 * mm,
        title=quote_no,
    )

    styles = getSampleStyleSheet()

    base = ParagraphStyle(
        "base",
        parent=styles["Normal"],
        fontName=FONTS["regular"],
        fontSize=9,
        leading=11,
        textColor=TEXT,
    )
    small = ParagraphStyle(
        "small",
        parent=base,
        fontSize=8,
        leading=10,
        textColor=MUTED,
    )
    h1 = ParagraphStyle(
        "h1",
        parent=base,
        fontName=FONTS["bold"],
        fontSize=14,
        leading=16,
        textColor=TEXT,
    )
    h2 = ParagraphStyle(
        "h2",
        parent=base,
        fontName=FONTS["bold"],
        fontSize=10,
        leading=12,
        textColor=TEXT,
    )

    story: List[Any] = []

    # --- Шапка (логотип + бренд-полоса) ---
    logo_path = (STATIC_DIR / "logo.png").resolve()

    header_tbl_data = []

    # левая часть: лого
    if logo_path.exists():
        img = RLImage(str(logo_path))
        img.drawHeight = 22 * mm
        img.drawWidth = 36 * mm
        left_cell = img
    else:
        left_cell = Paragraph(company["name"], h2)

    # правая часть: номер/дата/контакты
    right_text = (
        f"<font color='#FFFFFF'><b>КП № {quote_no}</b><br/>"
        f"Дата: {now:%d.%m.%Y}<br/></font>"
        f"<font color='#FFFFFF'>{company['phone']} &nbsp; | &nbsp; {company['email']}</font>"
    )
    right_cell = Paragraph(right_text, ParagraphStyle(
        "hdr",
        parent=base,
        fontName=FONTS["regular"],
        fontSize=9,
        leading=11,
        textColor=colors.white,
    ))

    center_text = Paragraph(
        "<font color='#D4AF37'><b>TENT GLOBAL SOLUTION</b></font><br/>"
        "<font color='#FFFFFF'>ТЕНТЫ • БРЕЗЕНТ • АНГАРЫ</font>",
        ParagraphStyle("center", parent=base, fontName=FONTS["regular"], fontSize=10, leading=12, textColor=colors.white)
    )

    header_tbl_data.append([left_cell, center_text, right_cell])

    header_tbl = Table(
        header_tbl_data,
        colWidths=[40*mm, 90*mm, 55*mm],
        rowHeights=[30*mm],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BRAND),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("ALIGN", (1, 0), (1, 0), "LEFT"),
        ("ALIGN", (2, 0), (2, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, GOLD),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6*mm))

    # --- Блок “как в КП Оп.pdf” (КОМУ/ОТ/ОРГАНИЗАЦИЯ/ТЕЛЕФОНЫ/ТЕМА...) ---
    meta_left = [
        ["КОМУ:", komu],
        ["ОРГАНИЗАЦИЯ:", organizaciya],
        ["НОМЕР ТЕЛЕФОНА:", client_phone],
        ["На тему:", theme],
    ]
    meta_right = [
        ["ОТ:", "TENT GLOBAL SOLUTION"],
        ["ОБЩЕЕ ЧИСЛО СТРАНИЦ:", "1"],
        ["ТЕЛЕФОН ОТПРАВИТЕЛЯ:", company["phone"]],
        ["E-mail:", company["email"]],
    ]

    # делаем 2 колонки таблично
    meta_tbl = Table(
        [
            [
                Table(meta_left, colWidths=[40*mm, 70*mm]),
                Table(meta_right, colWidths=[50*mm, 45*mm]),
            ]
        ],
        colWidths=[110*mm, 95*mm],
    )
    meta_tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 6*mm))

    story.append(Paragraph("Согласно запросу, предлагаем вашему вниманию, ценовое предложение:", base))
    story.append(Spacer(1, 4*mm))

    # --- Таблица товаров: теперь НИЧЕГО не “наезжает” ---
    # Колонки: №, Наименование, Характеристика(описание), Ед.изм., Цена, Кол-во, Сумма
    tbl_data: List[List[Any]] = []
    tbl_data.append([
        Paragraph("<b>№</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
        Paragraph("<b>Наименование</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
        Paragraph("<b>Характеристика</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
        Paragraph("<b>Ед.изм.</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
        Paragraph("<b>Цена</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
        Paragraph("<b>Кол-во</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
        Paragraph("<b>Сумма</b>", ParagraphStyle("th", parent=base, fontName=FONTS["bold"], textColor=colors.white)),
    ])

    for i, it in enumerate(items, start=1):
        name = safe(it.get("name"))
        desc = safe(it.get("desc"))  # <- редактируемое описание из UI
        unit = safe(it.get("unit"))
        price = to_num(it.get("price"), 0.0)  # <- редактируемая цена из UI
        qty = to_num(it.get("qty"), 0.0)
        disc = to_num(it.get("discount"), 0.0)
        line_sum = qty * price * (1 - disc / 100.0)

        tbl_data.append([
            Paragraph(str(i), base),
            Paragraph(name, base),
            Paragraph(desc if desc else "", small),
            Paragraph(unit, base),
            Paragraph(money(price), base),
            Paragraph(fmt_qty(qty), base),
            Paragraph(money(line_sum), base),
        ])

    # Итоговая строка (как “ИТОГО”)
    tbl_data.append([
        "",
        "",
        "",
        "",
        "",
        Paragraph("<b>ИТОГО</b>", ParagraphStyle("it", parent=base, fontName=FONTS["bold"])),
        Paragraph(f"<b>{money(totals['total'])}</b>", ParagraphStyle("it2", parent=base, fontName=FONTS["bold"])),
    ])

    # ширины колонок (под А4)
    col_widths = [8*mm, 55*mm, 60*mm, 14*mm, 20*mm, 16*mm, 22*mm]

    items_tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_DARK),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, GOLD),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),

        ("BOX", (0, 0), (-1, -2), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -2), 0.4, LINE),

        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("ALIGN", (4, 1), (6, -1), "RIGHT"),

        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),

        # зебра-строки
        ("ROWBACKGROUNDS", (0, 1), (-1, -3), [colors.white, ZEBRA]),

        # строка ИТОГО
        ("SPAN", (0, -1), (4, -1)),
        ("BACKGROUND", (0, -1), (-1, -1), colors.white),
        ("LINEABOVE", (0, -1), (-1, -1), 1.0, LINE),
        ("ALIGN", (5, -1), (6, -1), "RIGHT"),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 6*mm))

    # --- Срок действия КП ---
    story.append(Paragraph(f"<b>Срок действия КП:</b> {validity_days} дней", base))
    story.append(Spacer(1, 2*mm))

    # --- Доп. условия как в КП Оп ---
    if supply_terms:
        story.append(Paragraph(f"<b>Срок поставки:</b> {supply_terms}", base))
    if payment_terms:
        story.append(Paragraph(f"<b>Условия оплаты:</b> {payment_terms}", base))
    if note_block:
        story.append(Spacer(1, 2*mm))
        story.append(Paragraph(note_block, small))

    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("С уважением", base))
    story.append(Paragraph("<b>Директор</b>", base))

    # --- Сборка PDF ---
    doc.build(story)

    return FileResponse(path=str(out_path), filename=out_path.name, media_type="application/pdf")
