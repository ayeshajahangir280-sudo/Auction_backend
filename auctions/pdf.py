import io
import re
from decimal import Decimal
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.storage import default_storage
from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .models import Auction, Player, SoldPlayer, Team, UploadedImage


PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 42
PRIMARY = colors.HexColor("#1f3a5f")
MUTED = colors.HexColor("#64748b")
BORDER = colors.HexColor("#d7dde8")
SOFT_BG = colors.HexColor("#f4f7fb")
TEXT = colors.HexColor("#0f172a")


def build_team_roster_pdf(auction: Auction, team: Team, request=None) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, pageCompression=0)
    rows = _team_roster_rows(auction, team)

    _draw_header(pdf, auction, team, rows, request)
    y = PAGE_HEIGHT - 198
    _draw_table_header(pdf, y)
    y -= 38

    if not rows:
        _draw_empty_state(pdf, y)
    else:
        for index, row in enumerate(rows, start=1):
            if y < 112:
                _draw_footer(pdf)
                pdf.showPage()
                _draw_compact_page_header(pdf, auction, team)
                y = PAGE_HEIGHT - 104
                _draw_table_header(pdf, y)
                y -= 38
            _draw_player_row(pdf, y, index, row, auction.unit, request)
            y -= 72

    _draw_footer(pdf)
    pdf.save()
    return buffer.getvalue()


def team_roster_pdf_filename(auction: Auction, team: Team) -> str:
    raw = f"{auction.name}-{team.name}-roster"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower() or "team-roster"


def _team_roster_rows(auction: Auction, team: Team) -> list[dict]:
    sold_records = (
        SoldPlayer.objects.filter(auction=auction, team=team)
        .select_related("player", "player__category")
        .order_by("player__full_name", "player__id")
    )
    rows = [
        {
            "player": sold.player,
            "sold_price": sold.sold_price,
        }
        for sold in sold_records
    ]
    if rows:
        return rows

    players = auction.players.filter(sold_team=team).select_related("category").order_by("full_name", "id")
    return [{"player": player, "sold_price": player.sold_price or Decimal("0")} for player in players]


def _draw_header(pdf: canvas.Canvas, auction: Auction, team: Team, rows: list[dict], request) -> None:
    player_count = len(rows)
    total_spend = sum((row["sold_price"] for row in rows), Decimal("0"))

    pdf.setFillColor(SOFT_BG)
    pdf.rect(0, PAGE_HEIGHT - 150, PAGE_WIDTH, 150, stroke=0, fill=1)

    _draw_image_or_placeholder(pdf, team.logo_url, MARGIN, PAGE_HEIGHT - 122, 82, 82, team.short_name, request)

    pdf.setFillColor(PRIMARY)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawString(MARGIN + 104, PAGE_HEIGHT - 74, _fit_text(team.name, 330))

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(MARGIN + 104, PAGE_HEIGHT - 94, _fit_text(auction.name, 340))

    pdf.setFillColor(TEXT)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 68, "Team Roster")
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 86, f"{player_count} players bought")
    pdf.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 102, f"Team ID: {team.team_id}")

    y = PAGE_HEIGHT - 176
    _stat_box(pdf, MARGIN, y, 150, "Players Bought", str(player_count))
    _stat_box(pdf, MARGIN + 164, y, 170, "Total Spend", _money(total_spend, auction.unit))
    _stat_box(pdf, MARGIN + 348, y, 164, "Remaining Purse", _money(team.remaining_purse, auction.unit))


def _draw_compact_page_header(pdf: canvas.Canvas, auction: Auction, team: Team) -> None:
    pdf.setFillColor(PRIMARY)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(MARGIN, PAGE_HEIGHT - 54, _fit_text(team.name, 300))
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 54, _fit_text(auction.name, 220))


def _stat_box(pdf: canvas.Canvas, x: float, y: float, width: float, label: str, value: str) -> None:
    pdf.setStrokeColor(BORDER)
    pdf.setFillColor(colors.white)
    pdf.roundRect(x, y, width, 42, 5, stroke=1, fill=1)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(x + 10, y + 25, label.upper())
    pdf.setFillColor(TEXT)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(x + 10, y + 10, _fit_text(value, width - 20))


def _draw_table_header(pdf: canvas.Canvas, y: float) -> None:
    pdf.setFillColor(PRIMARY)
    pdf.roundRect(MARGIN, y - 20, PAGE_WIDTH - (MARGIN * 2), 28, 4, stroke=0, fill=1)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(MARGIN + 12, y - 10, "#")
    pdf.drawString(MARGIN + 50, y - 10, "PLAYER")
    pdf.drawString(MARGIN + 302, y - 10, "CATEGORY")
    pdf.drawRightString(PAGE_WIDTH - MARGIN - 12, y - 10, "FINAL BID")


def _draw_player_row(pdf: canvas.Canvas, y: float, index: int, row: dict, unit: str, request) -> None:
    player: Player = row["player"]
    sold_price = row["sold_price"]

    pdf.setStrokeColor(BORDER)
    pdf.setFillColor(colors.white)
    pdf.roundRect(MARGIN, y - 52, PAGE_WIDTH - (MARGIN * 2), 62, 4, stroke=1, fill=1)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(MARGIN + 18, y - 18, str(index))

    _draw_image_or_placeholder(pdf, player.image_url, MARGIN + 42, y - 44, 44, 44, _initials(player.full_name), request)

    pdf.setFillColor(TEXT)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(MARGIN + 98, y - 11, _fit_text(player.full_name or "Unnamed Player", 184))
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(MARGIN + 98, y - 29, _fit_text(player.player_id, 160))

    pdf.setFillColor(colors.HexColor("#eaf1fb"))
    pdf.roundRect(MARGIN + 298, y - 31, 116, 22, 4, stroke=0, fill=1)
    pdf.setFillColor(PRIMARY)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(MARGIN + 356, y - 24, _fit_text(player.category.name if player.category else "No category", 104))

    pdf.setFillColor(TEXT)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawRightString(PAGE_WIDTH - MARGIN - 14, y - 20, _fit_text(_money(sold_price, unit), 100))


def _draw_empty_state(pdf: canvas.Canvas, y: float) -> None:
    pdf.setStrokeColor(BORDER)
    pdf.setFillColor(SOFT_BG)
    pdf.roundRect(MARGIN, y - 76, PAGE_WIDTH - (MARGIN * 2), 70, 6, stroke=1, fill=1)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawCentredString(PAGE_WIDTH / 2, y - 37, "No players bought yet.")


def _draw_footer(pdf: canvas.Canvas) -> None:
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(MARGIN, 24, "Generated by Auction Software")
    pdf.drawRightString(PAGE_WIDTH - MARGIN, 24, f"Page {pdf.getPageNumber()}")


def _draw_image_or_placeholder(pdf: canvas.Canvas, value: str, x: float, y: float, width: float, height: float, fallback: str, request) -> None:
    image = _image_reader(value, request)
    if image:
        pdf.drawImage(image, x, y, width=width, height=height, preserveAspectRatio=True, anchor="c", mask="auto")
        return

    pdf.setFillColor(colors.HexColor("#e7edf8"))
    pdf.rect(x, y, width, height, stroke=0, fill=1)
    pdf.setStrokeColor(BORDER)
    pdf.rect(x, y, width, height, stroke=1, fill=0)
    pdf.setFillColor(PRIMARY)
    pdf.setFont("Helvetica-Bold", min(12, max(7, width / 5)))
    pdf.drawCentredString(x + width / 2, y + height / 2 - 4, _fit_text(fallback or "N/A", width - 8))


def _image_reader(value: str, request) -> ImageReader | None:
    data = _image_bytes(value, request)
    if not data:
        return None
    try:
        image = PillowImage.open(io.BytesIO(data))
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        converted = io.BytesIO()
        image.save(converted, format="PNG")
        converted.seek(0)
        return ImageReader(converted)
    except Exception:
        return None


def _image_bytes(value: str, request) -> bytes | None:
    if not value:
        return None

    media_path = _media_path_from_value(value, request)
    if media_path:
        uploaded = UploadedImage.objects.filter(path=media_path).first()
        if uploaded:
            return bytes(uploaded.data)
        if default_storage.exists(media_path):
            with default_storage.open(media_path, "rb") as stored_file:
                return stored_file.read()

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return None

    try:
        request_obj = Request(value, headers={"User-Agent": "auction-roster-pdf/1.0"})
        with urlopen(request_obj, timeout=3) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return None
            return response.read(3 * 1024 * 1024)
    except Exception:
        return None


def _media_path_from_value(value: str, request) -> str:
    parsed = urlparse(value)
    path = unquote(parsed.path if parsed.scheme else value).lstrip("/")
    media_url = settings.MEDIA_URL.strip("/")
    candidates = []
    if media_url and path.startswith(f"{media_url}/"):
        candidates.append(path[len(media_url) + 1 :])
    candidates.append(path)
    if request:
        request_path = request.build_absolute_uri("/").rstrip("/")
        if value.startswith(request_path):
            candidates.append(path)
    for candidate in candidates:
        candidate = candidate.lstrip("/")
        if candidate.startswith("uploads/images/"):
            return candidate
    return ""


def _fit_text(value: object, max_width: float) -> str:
    text = str(value or "")
    if len(text) <= 2:
        return text
    approx_char_width = 5.3
    max_chars = max(int(max_width / approx_char_width), 3)
    return text if len(text) <= max_chars else f"{text[: max_chars - 1]}..."


def _money(value: Decimal | int | float | str | None, unit: str = "") -> str:
    amount = Decimal(str(value or "0"))
    normalized = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return f"{normalized} {unit}".strip()


def _initials(value: str) -> str:
    parts = [part for part in re.split(r"\s+", value or "") if part]
    return "".join(part[0].upper() for part in parts[:2]) or "P"
