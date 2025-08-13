import qrcode
from io import BytesIO

async def generate_qr(user_id: int, ticket_type: str):
    """
    Генерирует QR-код с данными в формате "user_id:ticket_type".
    """
    qr_data = f"{user_id}:{ticket_type}"
    qr_img = qrcode.make(qr_data)

    buffer = BytesIO()
    qr_img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

