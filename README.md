# Cricket Auction Backend

Django + Django REST Framework backend for the cricket auction system.

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 8000
```

The local `.env` is read automatically. Use `DATABASE_URL` for PostgreSQL. If no database URL is provided, the backend uses local SQLite for development.

## Demo Accounts

After `seed_demo`:

- Super Admin: `admin` / `admin123`
- Auction Manager: `manager` / `manager123`
- Team Owner: `owner_bom` / `owner123`

## Important API Routes

- `POST /api/auth/login/`
- `GET /api/auth/me/`
- `GET/POST /api/auctions/`
- `GET /api/categories/` (read-only; categories are created from player imports)
- `GET/POST /api/players/`
- `POST /api/players/import-excel/`
- `GET/POST /api/teams/`
- `GET /api/auctions/{auction_id}/live-state/`
- `POST /api/auctions/{auction_id}/set-current-player/`
- `POST /api/auctions/{auction_id}/manual-bid/`
- `POST /api/auctions/{auction_id}/team-owner-bid/`
- `GET /api/auctions/{auction_id}/pending-bids/`
- `POST /api/auctions/{auction_id}/bids/{bid_id}/approve/`
- `POST /api/auctions/{auction_id}/bids/{bid_id}/reject/`
- `POST /api/auctions/{auction_id}/sell-player/`
- `POST /api/auctions/{auction_id}/mark-unsold/`
- `POST /api/auctions/{auction_id}/next-player/`
- `GET /api/auctions/{auction_id}/team-roster/?team_id=TEAM-BOM`
- `GET /api/auctions/{auction_id}/sold-players/`
- `GET /api/auctions/{auction_id}/results/`
- `GET /api/auctions/{auction_id}/public-live/`
- `GET /api/auctions/{auction_id}/projector/`

Every authenticated list/queryset is scoped by role:

- Super Admin can see all auctions.
- Auction Manager can see only the assigned auction.
- Team Owner can see only their team and assigned auction data.
