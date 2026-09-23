from io import BytesIO

import qrcode


async def generate_qr(row_id: int) -> bytes:
    """Generate a QR code containing the ticket row ID."""
    payload = str(row_id)

    image = qrcode.make(payload)

    buffer = BytesIO()
    image.save(buffer, format="PNG")

    return buffer.getvalue()
