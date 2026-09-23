from database import engine, metadata


if __name__ == "__main__":
    metadata.create_all(
        engine,
        checkfirst=True,
    )

    print("✅ Database tables initialized.")
