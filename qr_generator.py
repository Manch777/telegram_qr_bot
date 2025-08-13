# qr_generator.py
import qrcode
from io import BytesIO

async def generate_qr(row_id: int) -> bytes:
    """
    Генерирует PNG-байты QR-кода.
    Полезная нагрузка: только row_id (только цифры) — подходит для ?start=<payload>.
    """
    payload = str(row_id)  # НИКАКИХ ":" !
    img = qrcode.make(payload)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
