# Resale Atlas — Milestone 3 Client

A lightweight browser application for the supplied `resale_analytics` MySQL schema. It is designed for easy demonstration: one Flask server serves the dashboard and exposes parameterized API endpoints.

## Features implemented

- User registration with server-side validation and salted `scrypt` password hashing.
- Add a review for a development and view existing reviews.
- Top 10 project/property-type groups by resale inventory, resale amount, and total gain over market value.
- Development location, listing count, and average price per sqm by unit type.
- All properties in an area with average price per sqm by unit type through the results table.
- Area + price range + multi-amenity property search.
- Top 5 developers by resale listings with average price/sqm, down payment, and gain.
- Property listings filtered by area, property type, and contract year. The supplied schema has no `contract_year` column, so `YEAR(property.delivery_date)` is used and labelled as contract year in the UI.
- Average installment price, down payment, number of properties, discount/gain measure, contract age, and market price by area and development.
- Remote database status indicator showing the server hostname.

## Run against the actual remote MySQL server

1. Create the schema and load data on the remote server using the supplied SQL files. Do not point this client at localhost.
2. Create the environment file:

```bash
cp .env.example .env
```

3. Fill `.env` with the remote MySQL host, credentials, and database name. The application deliberately rejects `localhost`, `127.0.0.1`, and `::1`.
4. Install packages and start the app:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

5. Open `http://127.0.0.1:5000`. The browser is local, but all data requests go from Flask to the configured remote MySQL host. The green connection indicator displays the remote MySQL server hostname.
