from database import metadata, engine, users  # Обязательно импортируй таблицу!

if __name__ == "__main__":
    metadata.create_all(engine, checkfirst=True)
    print("✅ Таблица users создана.")
