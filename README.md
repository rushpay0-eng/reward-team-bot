# Reward Bot Complete Pro

Professional Telegram reward bot with:

- Dynamic single-dashboard navigation
- Back to Dashboard on every user screen
- Real Scratch Cards and Lucky Wheel
- Registration and Newbie proof review
- Secure proof image preview in Admin Panel
- 24-hour admin analytics and activity log
- Complete withdrawal history
- Admin-controlled reward settings
- Daily Check-in bonus
- Atomic duplicate-claim protection
- Railway Volume-ready database

## Required GitHub structure

main.py
requirements.txt
Procfile
.env.example
.gitignore
README.md

templates/
  scratch.html
  wheel.html
  login.html
  admin.html

static/
  miniapp.css
  admin.css

## Railway

Start command:

python main.py

Public Network port:

8080

Attach a Volume at:

/data

Admin Panel:

https://YOUR-DOMAIN.up.railway.app/admin


## 3-Level Referral System

Railway variable required:

BOT_USERNAME=YourBotUsername

Default commission rates:

- Level 1: 0.3%
- Level 2: 0.2%
- Level 3: 0.1%

Referral commission applies to First Scratch, Second Scratch, Lucky Wheel and Daily Check-in rewards.
