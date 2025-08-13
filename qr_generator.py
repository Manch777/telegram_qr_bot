# qr_generator.py
import qrcode
from io import BytesIO

async def generate_qr(row_id: int, ticket_type: str) -> bytes:
    """
    Генерирует PNG-байты QR-кода.
    Полезная нагрузка: "row_id:ticket_type" (например: "123:single").
    Верификацию типа на входе не делаем — на сканировании используем тип из БД.
    """
    payload = f"{row_id}:{ticket_type}"
    img = qrcode.make(payload)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()  # возвращаем bytes
