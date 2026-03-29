# APEX V75 — Signal Bot Setup Guide

## Quick Start (5 steps)

### Step 1 — Install Python dependencies
```bash
pip install -r requirements.txt
```

### Step 2 — Create your .env file
Copy `.env.example` to `.env` and fill in your values:
```
DERIV_APP_ID=1089
DERIV_API_KEY=your_deriv_api_key_here
TELEGRAM_BOT_TOKEN=your_NEW_bot_token_here
TELEGRAM_CHAT_ID=8746686966
ACCOUNT_BALANCE=1000
```

**IMPORTANT — Get a new Telegram bot token:**
1. Open Telegram → @BotFather
2. Send /mybots → your bot → API Token → Revoke → generate new
3. Paste new token into .env

**Get your Deriv API key:**
1. Log into Deriv → Settings → API Token
2. Create token with: Read + Trade scope
3. Paste into .env

### Step 3 — Test the connection
```bash
python -c "from config import DERIV_API_KEY, TELEGRAM_BOT_TOKEN; print('API key set:', bool(DERIV_API_KEY)); print('TG token set:', bool(TELEGRAM_BOT_TOKEN))"
```

### Step 4 — Run the bot
```bash
python main.py
```

You will receive a startup confirmation on Telegram within ~30 seconds.

### Step 5 — Keep it running 24/7 (Production)

**Option A — PM2 (recommended):**
```bash
npm install -g pm2
pm2 start main.py --interpreter python3 --name apex-bot
pm2 startup
pm2 save
```

**Option B — systemd (Linux server):**
```bash
sudo nano /etc/systemd/system/apex-bot.service
```
Paste:
```
[Unit]
Description=APEX V75 Signal Bot
After=network.target

[Service]
WorkingDirectory=/path/to/apex_bot
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```
Then:
```bash
sudo systemctl enable apex-bot
sudo systemctl start apex-bot
```

**Option C — screen (simplest):**
```bash
screen -S apex
python main.py
# Press Ctrl+A then D to detach
# Re-attach: screen -r apex
```

---

## Telegram Commands

| Command | Action |
|---------|--------|
| /status | Current market state, active signals, session |
| /signals | List all open signal cards |
| /balance | Account balance, daily P&L |
| /pause | Pause all new signals |
| /resume | Resume signals |
| /weekly | Weekly performance report |
| /journal | Last 10 trades |
| /kill VT001 | Cancel signal #VT001 |
| /risk 1.5 | Set risk to 1.5% per trade |
| /help | All commands |

---

## How to Use Signals

When APEX fires a signal card to Telegram:
1. Open your Deriv trading platform
2. Place a **limit order** at the Entry price shown
3. Set your **Stop Loss** at the SL price shown
4. Set **TP1, TP2, TP3** at the prices shown
5. Set lot size to the lot size shown

When TP1 is hit → close 35% of position
When TP2 is hit → close 35% of position
Remaining 30% → let run to TP3 or trail stop

**You execute all orders manually. APEX only sends signals.**

---

## File Structure

```
apex_bot/
├── main.py          # Entry point
├── config.py        # All settings (loaded from .env)
├── api.py           # Deriv WebSocket connection
├── data_store.py    # Candle storage
├── indicators.py    # All 15+ indicators
├── strategies.py    # All 11 strategies
├── scorer.py        # Confluence scoring
├── risk.py          # Risk management
├── signals.py       # Signal card builder
├── telegram_bot.py  # Telegram interface
├── trade_manager.py # Trade lifecycle
├── journal.py       # SQLite + CSV logging
├── watchdog.py      # Uptime + scheduled alerts
├── .env             # Your secrets (never share)
├── requirements.txt
└── logs/            # Auto-created log files
```

---

## Troubleshooting

**Bot not connecting to Deriv:**
- Check DERIV_API_KEY is correct
- Check DERIV_APP_ID (default 1089 works for most)
- Check internet connection on server

**Telegram messages not arriving:**
- Verify bot token is valid (revoke and regenerate if needed)
- Check TELEGRAM_CHAT_ID is your personal chat ID (not group)
- Send /start to your bot first to activate the chat

**No signals after 30+ minutes:**
- This is normal — APEX requires 70%+ confluence
- Asian session (00:00-07:00 UTC) has fewer signals
- Check /status to confirm bot is scanning

**pandas-ta not found:**
```bash
pip install pandas-ta==0.3.14b
```

---

*APEX v2.0 — No auto-trade. All signals require manual execution.*
