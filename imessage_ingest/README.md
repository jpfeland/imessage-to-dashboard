# iMessage → Supabase Ingestion Service

A minimal, read-only Python service that syncs iMessages from macOS to a
Supabase `communications` table — but **only** for phone numbers that already
exist in your `users` table.  Designed for project managers who want AI to
reason over client conversations.

---

## Privacy Model

> **Only whitelisted numbers are ever uploaded.**
>
> Every message is checked against the `users.phone_number` column in
> Supabase.  If a sender's number does not appear there, the message is
> silently skipped and never leaves the machine.  The original `chat.db` is
> never modified; every run works on a temporary copy.

---

## Requirements

| Requirement | Version |
|---|---|
| macOS | 12 Monterey or later (iCloud Messages sync) |
| Python | 3.11+ |
| Supabase project | with `users` and `communications` tables (see schema below) |

---

## Supabase Schema

The service reads phone numbers from your existing `profiles` table
(`profiles.phone_number`) and writes messages into a `communications` table.

Run this migration in the Supabase SQL editor to create the `communications`
table (the `profiles` table already exists in your project):

```sql
-- Communications table — stores ingested iMessages for AI reference
CREATE TABLE IF NOT EXISTS communications (
  id bigserial PRIMARY KEY,
  source text NOT NULL,           -- 'imessage'
  external_id text NOT NULL,      -- message ROWID from chat.db
  client_id uuid REFERENCES profiles(id),
  thread_key text,                -- chat ROWID
  direction text NOT NULL,        -- 'incoming' | 'outgoing'
  sender_handle text NOT NULL,    -- raw phone string from chat.db
  body text,
  sent_at timestamptz NOT NULL,
  created_at timestamptz DEFAULT now(),
  UNIQUE (source, external_id)
);
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <this-repo>
cd imessage-to-dashboard
pip install -r imessage_ingest/requirements.txt
```

### 2. Grant Full Disk Access

macOS requires **Full Disk Access** permission for the Terminal (or whichever
app runs this script) before it can read `~/Library/Messages/chat.db`.

1. Open **System Settings → Privacy & Security → Full Disk Access**.
2. Click **+** and add your terminal application (e.g. Terminal.app, iTerm2).
3. Re-launch the terminal.

### 3. Set environment variables

Copy the example env file and fill in your credentials:

```bash
cp .env.example .env
# then edit .env with your real values
```

Or export them directly:

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_KEY="your-service-role-key"
```

> **Security note:** Use the *service role* key only on this trusted machine.
> Never commit `.env` to source control.

### 4. Ensure `profiles.phone_number` is populated

The service reads phone numbers from the `profiles` table.  Make sure each
client profile has a `phone_number` in E.164 format (e.g. `+16125551234`)
before running.  Only numbers found in `profiles` will ever be ingested.

---

## Running

### Live mode (incremental sync)

```bash
cd imessage-to-dashboard
./imessage-to-dashboard live
```

- Reads `state.json` for the last processed message ROWID.
- Fetches only new messages.
- Uploads matched messages and updates `state.json`.
- Safe to run as often as you like (e.g. every 60 seconds via LaunchAgent).

### Backfill mode (historical sync for one client)

```bash
./imessage-to-dashboard backfill --user-id 550e8400-e29b-41d4-a716-446655440000
```

- Fetches **all** messages for the given user's phone number.
- Does **not** touch `state.json`.
- Idempotent — safe to re-run; duplicates are silently ignored.

---

## LaunchAgent (automatic scheduling)

Create the plist file to run live sync every 60 seconds:

```bash
cat > ~/Library/LaunchAgents/com.company.imessage_ingest.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.company.imessage_ingest</string>

    <key>ProgramArguments</key>
    <array>
        <!-- Full path to the imessage-to-dashboard script -->
        <string>/path/to/imessage-to-dashboard/imessage-to-dashboard</string>
        <string>live</string>
    </array>

    <!-- Working directory must contain the imessage_ingest package -->
    <key>WorkingDirectory</key>
    <string>/path/to/imessage-to-dashboard</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>SUPABASE_URL</key>
        <string>https://your-project.supabase.co</string>
        <key>SUPABASE_SERVICE_KEY</key>
        <string>your-service-role-key</string>
    </dict>

    <!-- Run every 60 seconds -->
    <key>StartInterval</key>
    <integer>60</integer>

    <key>StandardOutPath</key>
    <string>/tmp/imessage_ingest.log</string>

    <key>StandardErrorPath</key>
    <string>/tmp/imessage_ingest.err</string>

    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF
```

Load the agent:

```bash
launchctl load ~/Library/LaunchAgents/com.company.imessage_ingest.plist
```

Check it is running:

```bash
launchctl list | grep imessage_ingest
```

Unload (stop) the agent:

```bash
launchctl unload ~/Library/LaunchAgents/com.company.imessage_ingest.plist
```

View logs:

```bash
tail -f /tmp/imessage_ingest.log
tail -f /tmp/imessage_ingest.err
```

---

## Project Structure

```
imessage-to-dashboard          ← executable entry point
imessage_ingest/
├── ingest.py                  # CLI logic (live / backfill commands)
├── db.py                      # Read-only SQLite access; copies chat.db before opening
├── supabase.py                # PostgREST client (fetch users, insert communications)
├── utils.py                   # Phone normalization + Apple timestamp conversion
├── state.json                 # Persists last_seen_message_id between live runs
├── requirements.txt
└── README.md
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `FileNotFoundError: chat.db not found` | Grant Full Disk Access to your terminal (see Setup §2) |
| `Missing required environment variable(s)` | Set `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` in `.env` or shell |
| `No profile found in Supabase` | Check the UUID passed to `--user-id` |
| HTTP 401 from Supabase | Verify your service role key |
| No messages matched | Confirm `profiles.phone_number` is populated and in E.164 format |
