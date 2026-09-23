# Telegram QR Event Bot

A production-oriented Telegram bot for event registration, ticket management, payment confirmation, QR-based admission, and event administration.

The project combines a Telegram bot, PostgreSQL database, QR ticket generation, a companion web-based QR scanner, administrative tools, and deployment configuration.

## Features

### Event Registration

- Event-based user registration
- Multiple ticket types
- Configurable ticket prices
- Promo code support
- "1+1" ticket mechanics with configurable limits
- Persistent active event configuration
- Support for multiple purchases per user

### Payment Workflow

- Payment link support
- SBP/manual payment details
- Payment confirmation workflow
- Administrative payment approval and rejection
- Ticket activation after successful payment
- Administrator notifications

### QR Tickets

- Unique QR code generation for individual ticket records
- QR-based admission validation
- Protection against repeated ticket activation
- Ticket type verification during scanning
- Backward compatibility with legacy user-ID QR codes

### QR Scanner

The bot works together with a separate web-based QR scanner designed for event staff.

The scanner:

- Uses the device camera
- Reads QR codes directly in the browser
- Redirects scanned ticket data to the Telegram bot
- Supports the bot's ticket validation workflow
- Provides a mobile-friendly interface for event admission

The scanner frontend is maintained as a separate companion repository:

**[Telegram QR Scanner](https://github.com/Manch777/Telegram_QR_Scanner)**

### Administration

Administrative tools are available directly through Telegram and include:

- Event creation and configuration
- Event activation/deactivation
- Ticket price management
- Promo code management
- "1+1" ticket limit configuration
- Payment approval and rejection
- Scanner access management
- Administrative role management
- Broadcast messaging
- Event statistics
- Excel report export
- Database cleanup tools

Administrative and scanner permissions are stored in the database.

## Tech Stack

- **Python 3.11**
- **aiogram 3**
- **aiohttp**
- **PostgreSQL**
- **SQLAlchemy**
- **asyncpg**
- **databases**
- **OpenPyXL**
- **qrcode**
- **Pillow**
- **Docker**
- **Render**

## Project Structure

```text
Telegram_QR_Event_Bot/
├── handlers/
│   ├── __init__.py
│   ├── admin.py
│   └── user.py
├── .dockerignore
├── .env.example
├── .gitignore
├── config.py
├── database.py
├── Dockerfile
├── init_db.py
├── main.py
├── qr_generator.py
├── render.yaml
└── requirements.txt
```

### Main Components

**`main.py`**  
Application entry point. Initializes the database, restores the active event, configures Telegram commands and webhook handling, and starts the aiohttp server.

**`handlers/user.py`**  
User-facing Telegram workflows including registration, ticket selection, promo codes, payments, and ticket-related interactions.

**`handlers/admin.py`**  
Administrative workflows including event management, payments, broadcasts, scanner permissions, statistics, and report generation.

**`database.py`**  
PostgreSQL database layer containing table definitions and asynchronous database operations.

**`qr_generator.py`**  
Generates QR codes for individual ticket records.

**`config.py`**  
Loads and validates application configuration from environment variables.

**`init_db.py`**  
Utility script for initializing database tables.

## Configuration

Copy the example environment configuration:

```bash
cp .env.example .env
```

Then configure the required environment variables.

Example:

```dotenv
BOT_TOKEN=your_telegram_bot_token
POSTGRES_URL=postgresql://username:password@host:5432/database

WEBHOOK_URL=https://your-service.onrender.com
SCAN_WEBAPP_URL=https://your-scanner-webapp.example.com

CHANNEL_ID=@your_channel
ADMIN_CONTACT=@your_admin_username

PAYMENT_LINK=https://your-payment-link.example.com
SBP_PAYMENT_DETAILS=your_payment_details

ADMIN_EVENT_PASSWORD=change_me
ADMIN_BROADCAST_PASSWORD=change_me

PROMOCODES=PROMO1,PROMO2

EVENT_CODE=default_event
EVENT_TITLE=Default Event
```

Never commit real tokens, passwords, payment information, or database credentials to the repository.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/Manch777/Telegram_QR_Event_Bot.git
cd Telegram_QR_Event_Bot
```

### 2. Create a virtual environment

```bash
python -m venv .venv
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

On Windows:

```powershell
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create `.env` based on `.env.example` and provide the required configuration.

### 5. Initialize the database

```bash
python init_db.py
```

### 6. Start the application

```bash
python main.py
```

## Docker

Build the image:

```bash
docker build -t telegram-qr-event-bot .
```

Run the container with environment variables from `.env`:

```bash
docker run --env-file .env -p 10000:10000 telegram-qr-event-bot
```

## Webhook

The application exposes the Telegram webhook at:

```text
/webhook
```

A health-check endpoint is available at:

```text
/healthcheck
```

The application configures the Telegram webhook automatically during startup and includes retry handling for temporary Telegram API/network failures.

## Ticket Validation Flow

The current ticket flow uses a database row ID as the unique QR payload:

```text
Ticket purchase
      ↓
Payment confirmation
      ↓
Ticket record
      ↓
QR generation
      ↓
Web scanner
      ↓
Telegram deep link
      ↓
Ticket lookup
      ↓
Admission validation
      ↓
Ticket marked as activated
```

Legacy QR codes based on Telegram user IDs are also supported for backward compatibility.

## Event Persistence

The currently active event is persisted in the database rather than existing only in application memory.

This allows the selected event to survive application restarts and redeployments.

## Deployment

The repository contains a `render.yaml` configuration for deployment on Render.

Production secrets and environment-specific values should be configured through the hosting platform rather than committed to Git.

The application is designed to run as an aiohttp web service with Telegram webhook integration.

## Security Notes

- Secrets are loaded from environment variables.
- Required credentials are validated during application startup.
- Local `.env` files are excluded from Git.
- Runtime databases and generated QR files are excluded from version control.
- Full Telegram updates are not written to application error logs.
- Administrative and scanner access is controlled separately from regular user functionality.

## Companion QR Scanner

The project includes a separate lightweight web application responsible for camera-based QR scanning:

**[Telegram QR Scanner](https://github.com/Manch777/Telegram_QR_Scanner)**

The scanner reads the QR payload in the browser and forwards it to the Telegram bot through a Telegram deep link. Ticket validation and admission logic remain on the bot/backend side.

The bot and scanner are maintained in separate repositories because they have independent deployment lifecycles, while together they form a single event ticketing system.

### System Overview

```text
┌──────────────────────┐
│    Telegram User     │
└──────────┬───────────┘
           │ Registration / Payment
           ▼
┌──────────────────────┐
│ Telegram QR Event Bot│
│      (aiogram)       │
└──────┬─────────┬─────┘
       │         │
       │         └────────────► PostgreSQL
       │
       ▼
┌──────────────────────┐
│      QR Ticket       │
└──────────┬───────────┘
           │
           │ Scanned by event staff
           ▼
┌──────────────────────┐
│ Telegram QR Scanner  │
│   (Browser / ZXing)  │
└──────────┬───────────┘
           │ QR payload
           ▼
┌──────────────────────┐
│ Telegram Deep Link   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Telegram QR Event Bot│
│  Ticket Validation   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│      Admission       │
└──────────────────────┘
```

## Project Status

The project has been deployed and used as a working event-management solution.

The repositories have been prepared for portfolio presentation while preserving compatibility with the existing production workflow.

## License

This project is provided as a portfolio project. No open-source license is currently specified.
