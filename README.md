# Reward Team Telegram Bot

GitHub aur Railway ke liye ready Telegram reward bot.

## Features

- Channel aur group membership verification
- First Scratch Card: ₹10–₹20
- Registration link aur screenshot proof
- Admin approval/rejection
- Second Scratch Card: ₹10–₹20
- Newbie Order proof + ID submission
- Admin approval/rejection
- Lucky Wheel: ₹50–₹500
- User reward balance
- Admin `/stats` command
- SQLite database

## Files

- `bot.py` — main bot
- `requirements.txt` — Python packages
- `Procfile` — Railway worker command
- `.env.example` — required variables
- `.gitignore` — private files excluded

## Telegram setup

1. BotFather se bot create karein.
2. Bot ko channel aur group dono me admin banayein.
3. Public channel/group ke liye `@username` use karein.
4. Private chat ke liye numeric ID use karein, jaise `-1001234567890`.
5. Apna Telegram numeric user ID `ADMIN_ID` me dalein.

## GitHub upload

1. GitHub par new repository banayein.
2. Is folder ki sab files upload karein.
3. `.env` aur bot token GitHub me upload na karein.

## Railway deployment

1. Railway me `New Project` kholein.
2. `Deploy from GitHub Repo` select karein.
3. Repository connect karein.
4. Variables section me ye variables add karein:

```env
BOT_TOKEN=your_bot_token
ADMIN_ID=your_telegram_numeric_id
CHANNEL_ID=@yourchannel
GROUP_ID=@yourgroup
CHANNEL_LINK=https://t.me/yourchannel
GROUP_LINK=https://t.me/yourgroup
REGISTRATION_LINK=https://your-registration-link
DB_PATH=reward_bot.db
```

5. Railway deploy complete hone dein.
6. Logs me `Reward Team Bot started.` dikhna chahiye.

## Important database note

Railway container restart/redeploy par local SQLite database delete ho sakta hai.
Testing ke liye SQLite theek hai. Production me Railway Volume ya PostgreSQL use karna better hai.

## Commands

- `/start` — user menu
- `/cancel` — current proof upload cancel
- `/stats` — admin statistics
