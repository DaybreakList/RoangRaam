"""
PDF receipt of a closed bill (TASK-49).

Built in memory for each download and never written to disk: a receipt holds
the guest's details, and anything under MEDIA_ROOT is served without a login
check in production (AGENTS.md rule 12).

Thai needs text shaping, or vowels and tone marks above and below a letter are
lost ("ที่นี่" prints as "ทีนี"). fpdf2 shapes text with uharfbuzz, so that
package is required, not optional.
"""

import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from django.utils import dateformat
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace
from PIL import Image

from .folio import FolioLine, get_folio

logger = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent / 'fonts'
FONT = 'Sarabun'

# Colours from static/css/tokens.css
PRIMARY = (18, 105, 93)
TEXT = (23, 43, 53)
MUTED = (88, 108, 118)
BORDER = (220, 228, 231)
TINT = (231, 242, 237)


@dataclass(frozen=True)
class ReceiptData:
    """Everything printed on one receipt, read once so drawing needs no queries."""

    hotel_name: str
    hotel_address: str
    logo: bytes | None
    number: int
    issued_at: datetime  # in the hotel's time zone
    issued_by: str
    booking_code: str
    guest_name: str
    guest_email: str
    guest_phone: str
    check_in: date
    check_out: date
    nights: int
    lines: list[FolioLine]
    net_amount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    paid: Decimal


def receipt_data(property, booking):
    """
    The receipt of `booking` of `property`, or None while its bill is still open.

    `booking` must come from a query scoped to `property`, with its guest
    selected. Amounts and the tax rate come from the Receipt, so a later change
    of Property.tax_rate does not change a receipt already issued.
    """
    folio = get_folio(property, booking)
    receipt = folio.receipt
    if receipt is None:
        return None
    guest = booking.guest
    return ReceiptData(
        hotel_name=property.name,
        hotel_address=property.address,
        logo=_read_logo(property),
        number=receipt.number,
        issued_at=receipt.issued_at.astimezone(ZoneInfo(property.timezone)),
        issued_by=str(receipt.issued_by or ''),
        booking_code=booking.code,
        guest_name=f'{guest.first_name} {guest.last_name}',
        guest_email=guest.email,
        guest_phone=guest.phone,
        check_in=booking.check_in_date,
        check_out=booking.check_out_date,
        nights=booking.nights,
        lines=folio.lines,
        net_amount=receipt.total - receipt.tax_amount,
        tax_rate=receipt.tax_rate,
        tax_amount=receipt.tax_amount,
        total=receipt.total,
        paid=folio.paid,
    )


def render_receipt_pdf(data):
    """The receipt as PDF bytes."""
    return bytes(build_receipt_pdf(data).output())


def build_receipt_pdf(data):
    pdf = FPDF(format='A4')
    pdf.set_title(f'ใบเสร็จรับเงิน {data.number} - {data.hotel_name}')
    pdf.set_creator('RoangRaam')
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=22)
    pdf.add_font(FONT, '', FONT_DIR / 'Sarabun-Regular.ttf')
    pdf.add_font(FONT, 'B', FONT_DIR / 'Sarabun-Bold.ttf')
    pdf.set_text_shaping(True)
    pdf.add_page()

    _header(pdf, data)
    _details(pdf, data)
    _night_table(pdf, data)
    _totals(pdf, data)
    _footer_note(pdf, data)
    return pdf


# ---------- drawing ----------------------------------------------------------

def _header(pdf, data):
    top = pdf.get_y()
    left = pdf.l_margin
    if data.logo:
        try:
            pdf.image(Image.open(io.BytesIO(data.logo)), x=left, y=top, h=20, keep_aspect_ratio=True, w=20)
            left += 24
        except Exception:
            # A broken logo file must not stop the receipt
            logger.exception('Could not draw the hotel logo on receipt %s', data.number)

    # Hotel on the left
    pdf.set_xy(left, top)
    pdf.set_font(FONT, 'B', 15)
    pdf.set_text_color(*TEXT)
    pdf.multi_cell(95, 7, data.hotel_name, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if data.hotel_address:
        pdf.set_x(left)
        pdf.set_font(FONT, '', 9)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(95, 4.5, data.hotel_address, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    hotel_bottom = pdf.get_y()

    # Title and number on the right
    right_w = 70
    right_x = pdf.w - pdf.r_margin - right_w
    pdf.set_xy(right_x, top)
    pdf.set_font(FONT, 'B', 18)
    pdf.set_text_color(*PRIMARY)
    pdf.cell(right_w, 9, 'ใบเสร็จรับเงิน', align='R', new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font(FONT, '', 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(right_w, 5, 'RECEIPT', align='R', new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font(FONT, 'B', 11)
    pdf.set_text_color(*TEXT)
    pdf.cell(right_w, 6, f'เลขที่ {data.number}', align='R', new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font(FONT, '', 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(right_w, 5, f'วันที่ {_datetime(data.issued_at)}', align='R', new_x=XPos.LEFT, new_y=YPos.NEXT)

    bottom = max(hotel_bottom, pdf.get_y(), top + 22) + 4
    pdf.set_draw_color(*PRIMARY)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, bottom, pdf.w - pdf.r_margin, bottom)
    pdf.set_xy(pdf.l_margin, bottom + 5)


def _details(pdf, data):
    width = (pdf.w - pdf.l_margin - pdf.r_margin - 6) / 2
    top = pdf.get_y()
    _info_box(pdf, pdf.l_margin, top, width, 'ผู้เข้าพัก', [
        data.guest_name,
        data.guest_phone,
        data.guest_email,
    ])
    left_bottom = pdf.get_y()
    _info_box(pdf, pdf.l_margin + width + 6, top, width, 'การเข้าพัก', [
        f'รหัสการจอง {data.booking_code}',
        f'{_date(data.check_in)} ถึง {_date(data.check_out)}',
        f'{data.nights} คืน',
    ])
    pdf.set_xy(pdf.l_margin, max(left_bottom, pdf.get_y()) + 6)


def _info_box(pdf, x, y, width, title, rows):
    rows = [row for row in rows if row]
    height = 9 + 5.5 * len(rows) + 3
    pdf.set_fill_color(*TINT)
    pdf.set_draw_color(*TINT)
    pdf.rect(x, y, width, height, style='F', round_corners=True, corner_radius=2)
    pdf.set_xy(x + 4, y + 3)
    pdf.set_font(FONT, 'B', 9)
    pdf.set_text_color(*PRIMARY)
    pdf.cell(width - 8, 5, title, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font(FONT, '', 10)
    pdf.set_text_color(*TEXT)
    for row in rows:
        pdf.set_x(x + 4)
        pdf.cell(width - 8, 5.5, row, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_y(y + height)


def _night_table(pdf, data):
    pdf.set_font(FONT, 'B', 11)
    pdf.set_text_color(*TEXT)
    pdf.cell(0, 7, 'ค่าห้องพักรายคืน', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)

    pdf.set_font(FONT, '', 10)
    pdf.set_draw_color(*BORDER)
    pdf.set_line_width(0.2)
    headings = ('คืนวันที่', 'ห้อง', 'ประเภทห้อง', 'ราคา (บาท)')
    with pdf.table(
        col_widths=(40, 22, 70, 42),
        text_align=('LEFT', 'CENTER', 'LEFT', 'RIGHT'),
        headings_style=_heading_style(),
        line_height=7,
        padding=(1.5, 2),
        borders_layout='HORIZONTAL_LINES',
        cell_fill_color=(250, 251, 252),
        cell_fill_mode='ROWS',
        repeat_headings=1,
    ) as table:
        table.row(headings)
        for line in data.lines:
            table.row((_date(line.night), line.room_number, line.room_type_name, _money(line.price)))
        if not data.lines:
            table.row(('-', '-', 'ไม่มีห้องที่ใช้งานอยู่', '-'))
    pdf.ln(4)


def _heading_style():
    return FontFace(family=FONT, emphasis='BOLD', color=(255, 255, 255), fill_color=PRIMARY)


def _totals(pdf, data):
    width = 86
    x = pdf.w - pdf.r_margin - width
    if pdf.get_y() + 45 > pdf.page_break_trigger:
        pdf.add_page()
    rows = [
        ('ยอดก่อนภาษี', _money(data.net_amount)),
        (f'ภาษีมูลค่าเพิ่ม {_rate(data.tax_rate)}%', _money(data.tax_amount)),
    ]
    pdf.set_font(FONT, '', 10)
    pdf.set_text_color(*MUTED)
    for label, value in rows:
        pdf.set_x(x)
        pdf.cell(width - 36, 6.5, label)
        pdf.set_text_color(*TEXT)
        pdf.cell(36, 6.5, value, align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*MUTED)

    top = pdf.get_y() + 1
    pdf.set_fill_color(*PRIMARY)
    pdf.rect(x, top, width, 11, style='F', round_corners=True, corner_radius=2)
    pdf.set_xy(x + 3, top + 1.5)
    pdf.set_font(FONT, 'B', 12)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(width - 42, 8, 'ยอดรวมทั้งสิ้น')
    pdf.cell(36, 8, f'{_money(data.total)} บาท', align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_y(top + 13)
    pdf.set_x(x)
    pdf.set_font(FONT, '', 10)
    pdf.set_text_color(*MUTED)
    pdf.cell(width - 36, 6.5, 'ชำระแล้ว')
    pdf.set_text_color(*TEXT)
    pdf.cell(36, 6.5, _money(data.paid), align='R', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)


def _footer_note(pdf, data):
    pdf.set_draw_color(*BORDER)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)
    pdf.set_font(FONT, '', 9)
    pdf.set_text_color(*MUTED)
    notes = ['ราคาห้องพักรวมภาษีมูลค่าเพิ่มแล้ว']
    if data.issued_by:
        notes.append(f'ออกโดย {data.issued_by}')
    notes.append('ขอบคุณที่เข้าพักกับเรา')
    pdf.multi_cell(0, 5, ' · '.join(notes), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ---------- formatting -------------------------------------------------------

def _money(amount):
    return f'{amount:,.2f}'


def _rate(rate):
    # 7.00 -> "7", 7.50 -> "7.5"
    return f'{rate.normalize():f}'


def _date(value):
    return dateformat.format(value, 'j M Y')


def _datetime(value):
    return dateformat.format(value, 'j M Y H:i')


def _read_logo(property):
    if not property.logo:
        return None
    try:
        with property.logo.open('rb') as file:
            return file.read()
    except OSError:
        logger.exception('Could not read the logo of property %s', property.pk)
        return None
