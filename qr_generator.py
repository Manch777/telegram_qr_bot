import qrcode

def generate_qr(user_id):
    data = str(user_id)
    print(f"📦 Генерируем QR-код со значением: {data}")  # <-- ДОБАВЛЕНО
    img = qrcode.make(data)
    path = f"qrs/{user_id}.png"
    img.save(path)
    return path
