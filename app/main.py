# app/main.py
from __future__ import annotations

import re
import html as html_lib
import hashlib
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------- Утилиты ----------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: Any) -> str:
    """Удаляет HTML-теги и нормализует пробелы."""
    s = "" if text is None else str(text)
    if not s:
        return ""
    s = html_lib.unescape(s)
    s = s.replace("\u00a0", " ")
    s = _TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


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


def money(v: Any) -> str:
    v = to_num(v, 0.0)
    return f"{v:,.2f}".replace(",", " ")


def fmt_qty(q: float) -> str:
    if abs(q - round(q)) < 1e-9:
        return str(int(round(q)))
    return f"{q:.3f}".rstrip("0").rstrip(".")


def register_fonts() -> Dict[str, str]:
    """
    Надёжная регистрация DejaVu (кириллица) для Windows/Render.
    Поддерживает имена файлов с [1]: DejaVuSans[1].ttf, DejaVuSans-Bold[1].ttf
    """
    def pick_dejavu() -> Tuple[Optional[Path], Optional[Path]]:
        if not FONTS_DIR.exists():
            return None, None
        ttf = list(FONTS_DIR.glob("*.ttf"))
        if not ttf:
            return None, None

        reg = None
        bold = None
        for p in ttf:
            name = p.name.lower()
            if "dejavu" in name and "bold" not in name:
                reg = reg or p
            if "dejavu" in name and "bold" in name:
                bold = bold or p
        return reg, bold

    reg, bold = pick_dejavu()
    if reg and bold:
        pdfmetrics.registerFont(TTFont("KPFont", str(reg)))
        pdfmetrics.registerFont(TTFont("KPFontBold", str(bold)))
        return {"regular": "KPFont", "bold": "KPFontBold"}

    # fallback
    return {"regular": "Helvetica", "bold": "Helvetica-Bold"}


def fetch_image_to_cache(url_or_path: str) -> Optional[Path]:
    """
    Принимает http(s) URL или локальный путь.
    Возвращает путь к локальному файлу-кэшу, если удалось.
    """
    if not url_or_path:
        return None

    s = str(url_or_path).strip()
    if not s:
        return None

    cache_dir = OUTPUT_DIR / "_img_cache"
    cache_dir.mkdir(exist_ok=True)

    # локальный файл
    p = Path(s)
    if p.exists() and p.is_file():
        return p

    # URL
    if s.lower().startswith(("http://", "https://")):
        h = hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]
        ext = ".jpg"
        # грубо пытаемся угадать расширение
        low = s.lower()
        if ".png" in low:
            ext = ".png"
        elif ".webp" in low:
            ext = ".webp"
        elif ".jpeg" in low:
            ext = ".jpeg"
        out = cache_dir / f"{h}{ext}"

        if out.exists() and out.stat().st_size > 0:
            return out

        try:
            req = urllib.request.Request(
                s,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            out.write_bytes(data)
            if out.stat().st_size > 0:
                return out
        except Exception:
            return None

    return None


# ---------------- Загрузка каталога ----------------
if not DATA_PATH.exists():
    raise FileNotFoundError(f"Не найден файл каталога: {DATA_PATH}")

df = pd.read_excel(DATA_PATH)

# ожидаемые колонки (твои: description, image_url)
NEEDED = ["id", "name", "price", "currency", "unit", "group_name", "Наличие", "description", "image_url"]
for col in NEEDED:
    if col not in df.columns:
        df[col] = None

# нормализация строк
df["name"] = df["name"].astype(str).fillna("").map(strip_html)
df["description"] = df["description"].astype(str).fillna("").map(strip_html)

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
            "description": row["description"] if pd.notna(row["description"]) else "",
            "image_url": row["image_url"] if pd.notna(row["image_url"]) else "",
            "unit": row["unit"] if pd.notna(row["unit"]) else "",
            "price": float(row["price"]) if pd.notna(row["price"]) else 0.0,
            "currency": row["currency"] if pd.notna(row["currency"]) else "KZT",
            "group": row["group_name"] if pd.notna(row["group_name"]) else "",
            "availability": row["Наличие"] if pd.notna(row["Наличие"]) else "",
            "score": round(float(s), 2),
        })
    return results


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

    # фикс-поставщик (как ты просил: закрепить навсегда)
    if not company.get("name"):
        company["name"] = "TENT GLOBAL SOLUTION"
    if not company.get("phone"):
        company["phone"] = "+77785665001"
    if not company.get("email"):
        company["email"] = "tentatyrau@gmail.com"

    # клиент: без “компания клиента”, оставляем просто клиент
    if "name" not in client:
        client["name"] = ""

    totals = calc_totals(items, vat_rate=vat_rate)
    now = datetime.now()
    quote_no = payload.get("quote_no") or f"KP-{now:%Y%m%d-%H%M%S}"
    out_path = OUTPUT_DIR / f"{quote_no}.pdf"

    fonts = register_fonts()
    font_name = fonts["regular"]
    font_bold = fonts["bold"]

    def safe(s: Any) -> str:
        return str(s or "").strip()

    def wrap_lines(c: canvas.Canvas, text: str, max_w: float, font: str, size: float) -> List[str]:
        c.setFont(font, size)
        words = (text or "").split()
        if not words:
            return []
        lines: List[str] = []
        cur = ""
        for w in words:
            t = (cur + " " + w).strip()
            if c.stringWidth(t, font, size) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    # --- Цвета (синий + золото) ---
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

    # колонки считаем от ширины, чтобы НИКОГДА не “съезжало”
    table_w = right - left
    # № | Наименование | Характеристика | Ед | Цена | Кол-во | Сумма
    w_no = 10 * mm
    w_unit = 12 * mm
    w_price = 22 * mm
    w_qty = 18 * mm
    w_sum = 24 * mm
    w_name = 62 * mm
    w_desc = table_w - (w_no + w_name + w_desc if False else 0)  # заглушка

    # корректно считаем характеристику
    w_desc = table_w - (w_no + w_name + w_unit + w_price + w_qty + w_sum)

    x_no = left
    x_name = x_no + w_no
    x_desc = x_name + w_name
    x_unit = x_desc + w_desc
    x_price = x_unit + w_unit
    x_qty = x_price + w_price
    x_sum = x_qty + w_qty

    def header() -> float:
        header_h = 38 * mm
        cnv.setFillColor(BRAND)
        cnv.rect(0, H - header_h, W, header_h, stroke=0, fill=1)

        cnv.setStrokeColor(GOLD)
        cnv.setLineWidth(1.2)
        cnv.line(0, H - header_h, W, H - header_h)

        logo_path = (STATIC_DIR / "logo.png").resolve()
        if logo_path.exists():
            cnv.drawImage(str(logo_path), left, H - header_h + 6 * mm,
                          width=42 * mm, height=26 * mm, mask="auto")

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
        cnv.drawRightString(
            right, H - header_h + 6 * mm,
            f"{safe(company.get('phone'))}   |   {safe(company.get('email'))}"
        )
        return H - header_h - 10 * mm

    def table_header(y: float) -> float:
        h = 10 * mm
        cnv.setFillColor(BRAND_DARK)
        cnv.roundRect(left, y - h, table_w, h, 5, stroke=0, fill=1)

        cnv.setStrokeColor(GOLD)
        cnv.setLineWidth(1)
        cnv.line(left, y - h, right, y - h)

        cnv.setFillColor(colors.white)
        cnv.setFont(font_bold, 9)

        cnv.drawString(x_no + 3, y - 7 * mm, "№")
        cnv.drawString(x_name + 3, y - 7 * mm, "Наименование")
        cnv.drawString(x_desc + 3, y - 7 * mm, "Характеристика")
        cnv.drawString(x_unit + 3, y - 7 * mm, "Ед.")

        # чтобы заголовки не налезали — делаем фиксированные центры
        cnv.drawCentredString(x_price + w_price / 2, y - 7 * mm, "Цена")
        cnv.drawCentredString(x_qty + w_qty / 2, y - 7 * mm, "Кол-во")
        cnv.drawCentredString(x_sum + w_sum / 2, y - 7 * mm, "Сумма")

        return y - h - 2 * mm

    def new_page() -> float:
        cnv.showPage()
        y2 = header()
        y2 = table_header(y2)
        return y2

    y = header()

    # ---- Блоки Поставщик/Клиент ----
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
    cnv.drawString(left + 8, y - 22, safe(company.get("name")))
    cnv.setFillColor(MUTED)
    cnv.drawString(left + 8, y - 34, f"Тел: {safe(company.get('phone'))}")
    cnv.drawString(left + 8, y - 44, f"Email: {safe(company.get('email'))}")

    cnv.setFillColor(TEXT)
    cnv.setFont(font_bold, 10)
    cnv.drawString(mid + gap / 2 + 8, y - 10, "Клиент")
    cnv.setFont(font_name, 9)
    # оставляем просто "клиент", без компании
    cnv.drawString(mid + gap / 2 + 8, y - 22, safe(client.get("type") or ""))  # например "Физ лицо" если хочешь
    cnv.setFillColor(MUTED)
    cnv.drawString(mid + gap / 2 + 8, y - 34, f"Контакт: {safe(client.get('contact'))}")
    cnv.drawString(mid + gap / 2 + 8, y - 44, f"Тел: {safe(client.get('phone'))}")

    y -= (box_h + 10 * mm)

    # ---- Таблица ----
    y = table_header(y)

    row_pad_y = 2.5 * mm

    # для приложений (фото)
    attachments: List[Tuple[int, str, str]] = []  # (номер строки, name, image_url)

    for i, it in enumerate(items, start=1):
        if y < 65 * mm:
            y = new_page()

        name = strip_html(it.get("name"))
        desc = strip_html(it.get("description"))
        unit = strip_html(it.get("unit"))
        img = strip_html(it.get("image_url"))

        price = to_num(it.get("price"), 0.0)
        qty = to_num(it.get("qty"), 0.0)
        disc = to_num(it.get("discount"), 0.0)
        line_sum = qty * price * (1 - disc / 100.0)

        # строки для названия/описания (чтобы не лезло в колонки)
        cnv.setFillColor(TEXT)
        name_lines = wrap_lines(cnv, name, w_name - 6, font_name, 9)
        desc_lines = wrap_lines(cnv, desc, w_desc - 6, font_name, 7.6)  # маленький шрифт

        # высота строки = максимум линий
        lines_count = max(1, len(name_lines), len(desc_lines))
        row_h = (lines_count * 4.3 * mm) + row_pad_y * 2

        # zebra фон
        if i % 2 == 0:
            cnv.setFillColor(ZEBRA)
            cnv.rect(left, y - row_h, table_w, row_h, stroke=0, fill=1)

        # №
        cnv.setFillColor(TEXT)
        cnv.setFont(font_name, 9)
        cnv.drawString(x_no + 3, y - row_pad_y - 4.0 * mm, str(i))

        # Наименование
        cnv.setFont(font_name, 9)
        yy = y - row_pad_y - 4.0 * mm
        for ln in (name_lines or [""]):
            cnv.drawString(x_name + 3, yy, ln)
            yy -= 4.3 * mm

        # Характеристика (маленьким)
        cnv.setFillColor(MUTED)
        cnv.setFont(font_name, 7.6)
        yy2 = y - row_pad_y - 3.7 * mm
        for ln in (desc_lines or [""]):
            cnv.drawString(x_desc + 3, yy2, ln)
            yy2 -= 4.0 * mm

        # Ед.
        cnv.setFillColor(MUTED)
        cnv.setFont(font_name, 9)
        cnv.drawString(x_unit + 3, y - row_pad_y - 4.0 * mm, unit)

        # Цена / Кол-во / Сумма
        cnv.setFillColor(TEXT)
        cnv.setFont(font_name, 9)
        cnv.drawRightString(x_price + w_price - 3, y - row_pad_y - 4.0 * mm, money(price))
        cnv.drawRightString(x_qty + w_qty - 3, y - row_pad_y - 4.0 * mm, fmt_qty(qty))
        cnv.drawRightString(x_sum + w_sum - 3, y - row_pad_y - 4.0 * mm, money(line_sum))

        # граница строки
        cnv.setStrokeColor(LINE)
        cnv.setLineWidth(0.7)
        cnv.line(left, y - row_h, right, y - row_h)

        y -= row_h

        if img:
            attachments.append((i, name, img))

    y -= 8 * mm

    # ---- Итоги ----
    if y < 60 * mm:
        y = new_page()

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
    y -= 6 * mm

    if terms.strip():
        cnv.setFillColor(MUTED)
        cnv.setFont(font_bold, 9)
        cnv.drawString(left, y, "Условия:")
        y -= 5 * mm
        cnv.setFillColor(TEXT)
        cnv.setFont(font_name, 9)
        # простой wrap
        lines = wrap_lines(cnv, strip_html(terms), right - left, font_name, 9)
        for ln in lines[:6]:
            cnv.drawString(left, y, ln)
            y -= 4.5 * mm

    # ---- Приложения: фото (как в примере) ----
    if attachments:
        # начинаем приложения с новой страницы, чтобы не ломать верстку
        cnv.showPage()
        y = header()

        cnv.setFillColor(TEXT)
        cnv.setFont(font_bold, 12)
        cnv.drawString(left, y, "Приложение: фото")
        y -= 8 * mm

        for idx_attach, (row_no, nm, img_url) in enumerate(attachments[:6], start=1):
            if y < 70 * mm:
                cnv.showPage()
                y = header()
                cnv.setFillColor(TEXT)
                cnv.setFont(font_bold, 12)
                cnv.drawString(left, y, "Приложение: фото")
                y -= 8 * mm

            cnv.setFillColor(MUTED)
            cnv.setFont(font_bold, 9)
            cnv.drawString(left, y, f"Приложение {idx_attach} (позиция №{row_no}): {nm}")
            y -= 6 * mm

            img_path = fetch_image_to_cache(img_url)
            if img_path and img_path.exists():
                # размещаем изображение в рамке
                frame_w = right - left
                frame_h = 55 * mm
                cnv.setStrokeColor(LINE)
                cnv.setLineWidth(1)
                cnv.roundRect(left, y - frame_h, frame_w, frame_h, 6, stroke=1, fill=0)

                try:
                    cnv.drawImage(
                        str(img_path),
                        left + 4 * mm,
                        y - frame_h + 4 * mm,
                        width=frame_w - 8 * mm,
                        height=frame_h - 8 * mm,
                        preserveAspectRatio=True,
                        anchor="c",
                        mask="auto",
                    )
                except Exception:
                    cnv.setFillColor(MUTED)
                    cnv.setFont(font_name, 9)
                    cnv.drawString(left + 6 * mm, y - 12 * mm, "Не удалось вставить изображение по ссылке.")
                y -= (frame_h + 10 * mm)
            else:
                cnv.setFillColor(MUTED)
                cnv.setFont(font_name, 9)
                cnv.drawString(left, y, f"Фото не найдено/не скачалось: {img_url}")
                y -= 10 * mm

    # ---- Footer ----
    cnv.setStrokeColor(GOLD)
    cnv.setLineWidth(1)
    cnv.line(left, 14 * mm, right, 14 * mm)

    cnv.setFillColor(BRAND_DARK)
    cnv.setFont(font_name, 8.5)
    cnv.drawString(left, 9 * mm, f"{safe(company.get('name'))} • {safe(company.get('phone'))} • {safe(company.get('email'))}")

    cnv.save()
    return FileResponse(path=str(out_path), filename=out_path.name, media_type="application/pdf")
