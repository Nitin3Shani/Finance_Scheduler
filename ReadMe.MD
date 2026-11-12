# PricePulse — E-Commerce Price Tracker

Compare product prices on **Amazon India** and **Flipkart**, track them over time, view price history charts, and get an **email alert** when a price drops to your target.

## Features

- Live **Search & Compare**: scrapes both stores and shows which one is cheaper (sponsored/ad results are skipped)
- **Track up to 10 products** per user with per-platform price history
- **Analytics**: lowest / highest / average price, per platform and combined, with a line chart (Recharts)
- **Price drop alerts** by email (SMTP); an alert pauses itself after it fires
- **Background scheduler** (APScheduler, jobs persisted in Postgres) re-checks all tracked products periodically
- JWT auth with access + refresh tokens, Argon2 password hashing
- Light / dark theme

## Tech stack

| Layer | Tools |
|-------|-------|
| Backend | FastAPI, SQLAlchemy 2 (async) + asyncpg, PostgreSQL, Pydantic v2 |
| Scraping | Playwright (Chromium) + playwright-stealth, BeautifulSoup |
| Jobs / email | APScheduler, aiosmtplib |
| Frontend | React 19, Vite, Tailwind CSS 4, Recharts, lucide-react |

## Project structure

```
.
├── backend/
│   ├── requirements.txt
│   ├── .env                  # you create this (see below)
│   └── app/
│       ├── main.py           # FastAPI app, CORS, lifespan (creates tables, starts scheduler)
│       ├── config.py         # settings loaded from .env
│       ├── database.py       # async engine + session
│       ├── models.py         # User, TrackedProduct, PriceHistory, PriceAlert
│       ├── schemas.py        # Pydantic request/response models
│       ├── auth.py           # hashing + JWT helpers, get_current_user
│       ├── routers/
│       │   ├── auth.py            # /auth/*
│       │   ├── product_router.py  # /products/*
│       │   └── alert_router.py    # /alerts/*
│       └── services/
│           ├── scraper.py    # Amazon/Flipkart search + product page scrapers
│           ├── scheduler.py  # periodic price check job
│           └── notifier.py   # alert emails
└── frontend/
    ├── package.json
    ├── vite.config.js
    └── src/ (App.jsx, main.jsx, index.css)
```

> Adjust folder names above if your layout differs. The backend imports use the `app.` package prefix, so run uvicorn from the `backend/` folder.

## Getting started

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL running locally (create an empty database, e.g. `pricepulse`)

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install psycopg2-binary      # needed by the scheduler's job store (see note below)
playwright install chromium
```

Create `backend/.env` (use `.env.example` as a template):

```env
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/pricepulse
SECRET_KEY=a-long-random-string
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=your-app-password
```

Generate a secret key with: `python -c "import secrets; print(secrets.token_urlsafe(48))"`

Run the API:

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000
- Interactive docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

Tables are created automatically on startup.

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. To point at a different API, create `frontend/.env`:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## API overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Create account |
| POST | `/auth/login` | Get access + refresh tokens (form data) |
| POST | `/auth/refresh` | Exchange a refresh token for new tokens |
| GET | `/auth/me` | Current user |
| GET | `/products/search?query=` | Search and compare Amazon vs Flipkart |
| POST | `/products/` | Track a product |
| GET | `/products/` | List tracked products |
| GET | `/products/{id}` | Price history + stats |
| POST | `/products/{id}/refresh` | Scrape prices now |
| DELETE | `/products/{id}` | Stop tracking |
| POST | `/alerts/` | Create or update a price alert |
| GET / DELETE | `/alerts/{product_id}` | Read or remove an alert |

All routes except `/auth/*` and `/health` need `Authorization: Bearer <access_token>`.

## Scheduler

`app/services/scheduler.py` runs `check_all_tracked_products` on an interval, saves a new `PriceHistory` row per platform, and sends an email if an active alert's target is met.

- The job is stored in the `apscheduler_jobs` table, so **changing the interval in code does not affect an already-saved job**. Reschedule it or delete the row and restart.
- The interval is currently short for testing. **Set it to something like 6 hours (360 minutes) in production** to avoid being rate-limited or blocked.
- `POST /products/test-trigger-job` runs a check immediately; remove it before deploying.

## Notes and limitations

- **Scraping is fragile.** Amazon and Flipkart change their markup and may block automated traffic or show CAPTCHAs. If prices stop updating, check the scraper selectors first.
- **Do not hammer the sites.** Keep the scheduler interval long and respect each site's terms of service. This project is intended for personal/educational use.
- The scheduler's job store uses a synchronous driver (`psycopg2`), which isn't in `requirements.txt`; install `psycopg2-binary` as shown above.
- Gmail needs an [app password](https://support.google.com/accounts/answer/185833) for SMTP, not your normal password.

## Security checklist before deploying

- Never commit `.env`. The `.gitignore` here excludes it; if a real `.env` was ever committed, rotate the secrets.
- Replace the dev CORS origins in `main.py` with your real frontend URL.
- Remove the `/products/test-trigger-job` endpoint.
- Use a strong, unique `SECRET_KEY`.