GOLD BOT — MT5 Market Data Setup

1. Purpose

This document describes the current backend setup for registering a trusted MT5 data source and receiving authenticated market data.

The current backend provides three ingestion endpoints:

- "POST /api/v1/market-data/candles"
- "POST /api/v1/market-data/account"
- "POST /api/v1/market-data/positions"

The ingestion service is disabled by default.

Important implementation status: The Python backend receiver and device-registration utility exist. The MQL5 Expert Advisor responsible for collecting MT5 data, generating the request signature, and sending HTTPS requests has not yet been implemented. This document does not claim that end-to-end MT5 connectivity is complete.

2. Prerequisites

Before enabling ingestion, prepare:

- A Windows VPS or another supported backend host.
- Python 3.12 or a compatible supported Python version.
- PostgreSQL.
- A valid HTTPS endpoint for the backend.
- Access to the backend environment configuration.
- An MT5 DEMO account for the planned integration.

Do not put MT5 account passwords, device secrets, database passwords, or encryption keys in GitHub source files.

3. Environment configuration

Create a private ".env" file in the "Backend" directory.

Required configuration:

APP_ENV=production

API_HOST=127.0.0.1
API_PORT=8000

DATABASE_URL=postgresql+psycopg://goldbot:YOUR_DATABASE_PASSWORD@127.0.0.1:5432/goldbot

JWT_SECRET=REPLACE_WITH_A_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS
BACKEND_API_KEY=REPLACE_WITH_ANOTHER_RANDOM_SECRET_OF_AT_LEAST_32_CHARACTERS

DEVICE_SECRET_ENCRYPTION_KEY=REPLACE_WITH_64_HEXADECIMAL_CHARACTERS

DEMO_ONLY=true
AUTOMATED_TRADING_ENABLED=false

MARKET_DATA_INGESTION_ENABLED=false

Generate a 32-byte encryption key on the trusted server:

openssl rand -hex 32

The generated value must contain exactly 64 hexadecimal characters.

Generate independent random values for "JWT_SECRET" and "BACKEND_API_KEY". Do not reuse the encryption key for either value.

Keep ingestion disabled until PostgreSQL, the device registry, HTTPS, and the registration process have been prepared.

After completing those checks, ingestion can be enabled with:

MARKET_DATA_INGESTION_ENABLED=true

Restart the backend after changing environment variables.

Never commit ".env" to GitHub.

4. PostgreSQL preparation

Create a dedicated PostgreSQL database and a restricted database user.

Example SQL, executed by a PostgreSQL administrator:

CREATE USER goldbot WITH PASSWORD 'REPLACE_WITH_A_STRONG_PASSWORD';

CREATE DATABASE goldbot OWNER goldbot;

The backend initializes the following tables when market-data ingestion is enabled:

- "gold_bot_trusted_devices"
- "gold_bot_market_data_messages"
- The persistent nonce table used for replay protection.

The backend must be able to create and access these tables.

Use a dedicated database account. Do not use the PostgreSQL superuser as the application's normal database account.

5. Register an MT5 data source

Run the registration utility from the "Backend" directory:

python scripts/register_mt5_device.py --source-id MT5-DEMO-01

The registration utility:

1. Validates the source identifier.
2. Generates a cryptographically secure device secret.
3. Encrypts the secret using AES-256-GCM.
4. Binds the encrypted secret to the source identifier.
5. Stores the encrypted secret in PostgreSQL.
6. Prints the plaintext secret once after successful registration.

Example source identifier:

MT5-DEMO-01

Save the generated device secret in a secure server-side secret store.

The secret is displayed only once. Do not commit it, send it in screenshots, or include it in logs.

Registering an existing source identifier does not replace its secret. If a secret is lost or compromised, revoke the old device through a trusted administrative process and register a new source identifier.

There is currently no public device-enrollment endpoint.

6. HTTP authentication protocol

Every MT5 data request must use HTTPS and include:

Content-Type: application/json
X-Device-ID: MT5-DEMO-01
X-Timestamp: UNIX_TIMESTAMP_SECONDS
X-Nonce: UNIQUE_RANDOM_VALUE
X-Signature: HMAC_SHA256_HEX

Signature generation

The backend uses HMAC-SHA256.

The signed message is constructed exactly as follows:

HTTP_METHOD
URL_PATH
TIMESTAMP
SOURCE_ID
NONCE
RAW_REQUEST_BODY

The first five fields are UTF-8 encoded and each ends with a newline character.

The exact raw HTTP body bytes are appended immediately after the final newline.

Conceptually:

canonical_header = (
    f"{method}\n"
    f"{path}\n"
    f"{timestamp}\n"
    f"{source_id}\n"
    f"{nonce}\n"
).encode("utf-8")

message = canonical_header + raw_body

signature = hmac_sha256(
    device_secret,
    message,
).hexdigest()

The signature must be calculated from the exact bytes transmitted. Reformatting JSON after signing invalidates the signature.

Security requirements

The current verifier enforces:

- HMAC-SHA256 signature verification.
- Constant-time signature comparison.
- Request timestamp freshness.
- Maximum request age of 30 seconds.
- Future clock tolerance of 5 seconds.
- Nonce length between 16 and 128 characters.
- Persistent atomic nonce claiming.
- Duplicate nonce rejection.
- Device identity lookup from the trusted registry.
- Exact source identifier matching between the header and JSON body.
- Rejection of URL query parameters.
- Rejection of duplicate JSON keys.
- JSON request body limit of 2,000,000 bytes.

Synchronize the MT5 host clock with a reliable time source. Requests with stale timestamps are rejected.

7. Data endpoint requirements

Candles

Endpoint:

POST /api/v1/market-data/candles

Required payload fields:

- "message_id"
- "source_id"
- "symbol"
- "timeframe"
- "sent_at"
- "volume_source"
- "candles"

Supported symbol:

XAUUSD

Supported timeframes:

M1, M5, M15, M30, H1, H4, D1, W1, MN1

Each candle must include:

- "open_time"
- "open"
- "high"
- "low"
- "close"
- "tick_volume"
- "real_volume"
- "spread_points"
- "is_closed"

Only closed candles are accepted.

Candles must be chronologically ordered and have unique timestamps.

Account snapshot

Endpoint:

POST /api/v1/market-data/account

Required fields include:

- "message_id"
- "source_id"
- "account_mode"
- "account_currency"
- "balance"
- "equity"
- "daily_start_equity"
- "free_margin"
- "used_margin"
- "daily_realized_loss"
- "positions_count"
- "sent_at"

Account modes currently represented by the schema:

DEMO, REAL, CONTEST, UNKNOWN

Important: The current schema can represent REAL accounts. The ingestion endpoint does not yet independently prove that an account is DEMO. Do not treat "DEMO_ONLY=true" as proof that this account-mode enforcement is complete. A trusted MT5 account-mode verification and rejection layer is still required before any automated trading workflow.

Open positions snapshot

Endpoint:

POST /api/v1/market-data/positions

Required top-level fields:

- "message_id"
- "source_id"
- "sent_at"
- "positions"

Each position includes:

- "position_id"
- "symbol"
- "side"
- "volume"
- "entry_price"
- "current_price"
- "stop_loss"
- "take_profit"
- "floating_profit"
- "opened_at"

Position snapshots can represent existing positions without a stop loss. This does not mean the risk engine should approve opening a new position without a valid protective stop loss.

8. API responses

A newly stored message returns:

{
  "status": "stored",
  "message_id": "MESSAGE_UUID",
  "message_type": "candles",
  "source_id": "MT5-DEMO-01"
}

An identical message already stored returns:

{
  "status": "duplicate",
  "message_id": "MESSAGE_UUID",
  "message_type": "candles",
  "source_id": "MT5-DEMO-01"
}

Relevant HTTP status codes:

Status| Meaning
200| Message stored or duplicate accepted
401| Authentication rejected
409| Message ID reused with different content
422| Invalid market-data payload
503| Ingestion dependencies or storage unavailable

9. Operational security

Before exposing the backend:

1. Configure HTTPS with a valid TLS certificate.
2. Keep PostgreSQL inaccessible from the public internet.
3. Restrict database permissions.
4. Protect ".env" with operating-system permissions.
5. Keep the device secret outside GitHub.
6. Synchronize host clocks.
7. Monitor authentication failures and database errors.
8. Back up PostgreSQL securely.
9. Revoke compromised devices through a trusted administrative process.
10. Do not enable real trading.

Do not expose the backend over plain HTTP on a public network.

10. Current implementation status

Component| Status
Market-data Pydantic schemas| Implemented
HMAC-SHA256 verification| Implemented
Timestamp validation| Implemented
Nonce replay protection| Implemented
PostgreSQL nonce storage| Implemented
AES-256-GCM device-secret encryption| Implemented
Trusted device registry| Implemented
Device registration utility| Implemented
Candle ingestion endpoint| Implemented
Account ingestion endpoint| Implemented
Position ingestion endpoint| Implemented
Persistent raw message storage| Implemented
Automatic nonce cleanup| Implemented
Python unit tests| Passing in GitHub Actions
PostgreSQL live integration tests| Not yet verified
MQL5 Expert Advisor| Not implemented
MT5-to-backend end-to-end verification| Not completed
Independent DEMO account enforcement| Not completed
Analysis orchestration using live market data| Not completed
Automated trade execution| Disabled

11. Next implementation steps

The next engineering tasks are:

1. Implement the MQL5 Expert Advisor.
2. Implement exact UTF-8 request-body serialization and HMAC generation in MQL5.
3. Add secure HTTPS transmission and retry handling.
4. Send closed candles, account snapshots, and open-position snapshots.
5. Verify account mode using actual MT5 account properties.
6. Add real PostgreSQL integration tests.
7. Verify ingestion end to end using an MT5 DEMO account.
8. Add data freshness and missing-data monitoring.
9. Connect stored market data to the analysis orchestration layer.

No automated trading should be enabled until account verification, risk controls, audit logging, and end-to-end tests have been completed.

---

Document status: Setup and integration specification for the current backend implementation. It is not evidence of a completed MT5-to-backend connection.
