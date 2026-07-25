import asyncio, hashlib, hmac, os, re, secrets, sqlite3, threading
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import quote
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from waitress import serve

BOT_TOKEN=os.getenv('BOT_TOKEN','').strip(); ADMIN_ID=int(os.getenv('ADMIN_ID','0'))
ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin'); ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD','change-me')
SECRET_KEY=os.getenv('SECRET_KEY',secrets.token_hex(32)); PUBLIC_URL=os.getenv('PUBLIC_URL','').rstrip('/')
CHANNEL_ID=os.getenv('CHANNEL_ID',''); GROUP_ID=os.getenv('GROUP_ID','')
CHANNEL_LINK=os.getenv('CHANNEL_LINK',''); GROUP_LINK=os.getenv('GROUP_LINK','')
REGISTRATION_LINK=os.getenv('REGISTRATION_LINK','https://example.com')
DB_PATH=os.getenv('DB_PATH','/data/reward_bot.db'); PORT=int(os.getenv('PORT','8080'))
MIN_WITHDRAWAL=int(os.getenv('MIN_WITHDRAWAL','50'))
WAIT_REG_PROOF='wait_reg_proof'; WAIT_REG_ID='wait_reg_id'; WAIT_NEWBIE_PROOF='wait_newbie_proof'; WAIT_NEWBIE_ID='wait_newbie_id'; WAIT_UPI='wait_upi'

app=Flask(__name__); app.secret_key=SECRET_KEY

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    os.makedirs(os.path.dirname(DB_PATH) or '.',exist_ok=True)
    c=sqlite3.connect(DB_PATH,timeout=30); c.row_factory=sqlite3.Row; return c

def init_db():
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT,
          verified INTEGER DEFAULT 0, state TEXT, blocked INTEGER DEFAULT 0,
          first_status TEXT DEFAULT 'locked', first_reward INTEGER DEFAULT 10,
          registration_status TEXT DEFAULT 'not_submitted', registration_id TEXT, registration_proof TEXT,
          second_status TEXT DEFAULT 'locked', second_reward INTEGER DEFAULT 10,
          newbie_status TEXT DEFAULT 'not_submitted', newbie_id TEXT, newbie_proof TEXT,
          wheel_status TEXT DEFAULT 'locked', wheel_reward INTEGER DEFAULT 50,
          balance INTEGER DEFAULT 0, upi_id TEXT);
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,kind TEXT,status TEXT DEFAULT 'pending',created_at TEXT,reason TEXT);
        CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,amount INTEGER,upi_id TEXT,status TEXT DEFAULT 'pending',created_at TEXT,reason TEXT);
        ''')

def ensure(tg):
    with db() as c:
        c.execute('INSERT INTO users(user_id,username,first_name,joined_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name',(tg.id,tg.username,tg.first_name,now()))
        return c.execute('SELECT * FROM users WHERE user_id=?',(tg.id,)).fetchone()
def user(uid):
    with db() as c:return c.execute('SELECT * FROM users WHERE user_id=?',(uid,)).fetchone()
def upd(uid,**v):
    if not v:return
    with db() as c:c.execute('UPDATE users SET '+','.join(f'{k}=?' for k in v)+' WHERE user_id=?',(*v.values(),uid))

def token(uid,purpose):
    raw=f'{uid}:{purpose}'; sig=hmac.new(SECRET_KEY.encode(),raw.encode(),hashlib.sha256).hexdigest(); return f'{uid}.{sig}'
def verify_token(t,purpose):
    try:
        u,s=t.split('.',1); uid=int(u); return uid if hmac.compare_digest(s,token(uid,purpose).split('.',1)[1]) else None
    except:return None
def wurl(path,uid,purpose): return f'{PUBLIC_URL}{path}?token={quote(token(uid,purpose))}'

def menu():
    return InlineKeyboardMarkup([[InlineKeyboardButton('📢 Join Channel',url=CHANNEL_LINK),InlineKeyboardButton('👥 Join Group',url=GROUP_LINK)],[InlineKeyboardButton('✅ Verify & Unlock',callback_data='verify')],[InlineKeyboardButton('🎁 My Rewards',callback_data='rewards'),InlineKeyboardButton('💸 Withdrawal',callback_data='withdraw')]])
def reg_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton('📝 Complete Registration',url=REGISTRATION_LINK)],[InlineKeyboardButton('📸 Upload Proof',callback_data='reg_upload')]])
def newbie_menu(): return InlineKeyboardMarkup([[InlineKeyboardButton('📸 Upload Proof',callback_data='newbie_upload')]])

async def member_ok(ctx,chat_id,uid):
    try:
        m=await ctx.bot.get_chat_member(chat_id,uid)
        return m.status in {ChatMemberStatus.MEMBER,ChatMemberStatus.ADMINISTRATOR,ChatMemberStatus.OWNER,ChatMemberStatus.RESTRICTED}
    except:return False

async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=ensure(update.effective_user)
    if u['blocked']: return await update.message.reply_text('Your account is blocked.')
    await update.message.reply_text('🎉 <b>Welcome to Reward Team!</b>\n\nChannel aur Group join karke first Scratch Card unlock karein.',parse_mode=ParseMode.HTML,reply_markup=menu())
async def chatid(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    ch=update.effective_chat; await update.message.reply_text(f'🆔 Chat ID: <code>{ch.id}</code>\nType: {ch.type}',parse_mode=ParseMode.HTML)
async def cancel(update:Update,ctx:ContextTypes.DEFAULT_TYPE): upd(update.effective_user.id,state=None); await update.message.reply_text('Cancelled.',reply_markup=menu())

async def buttons(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; u=ensure(q.from_user)
    if u['blocked']: return await q.message.reply_text('Your account is blocked.')
    if q.data=='verify':
        if not await member_ok(ctx,CHANNEL_ID,uid) or not await member_ok(ctx,GROUP_ID,uid): return await q.message.reply_text('❌ Pehle Channel aur Group dono join karein.',reply_markup=menu())
        upd(uid,verified=1,first_status='ready')
        return await q.message.reply_text('✅ Verified! Ab proper Scratch Card kholen.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎁 Open Scratch Card',web_app=WebAppInfo(wurl('/scratch/1',uid,'s1')))]]))
    if q.data=='reg_upload':
        if user(uid)['first_status']!='used': return await q.message.reply_text('Pehle first Scratch Card complete karein.')
        upd(uid,state=WAIT_REG_PROOF); return await q.message.reply_text('📸 Registration screenshot bhejein.')
    if q.data=='newbie_upload':
        if user(uid)['second_status']!='used': return await q.message.reply_text('Pehle second Scratch Card complete karein.')
        upd(uid,state=WAIT_NEWBIE_PROOF); return await q.message.reply_text('📸 Newbie Order screenshot bhejein.')
    if q.data=='rewards':
        u=user(uid); return await q.message.reply_text(f"🎁 First: ₹{u['first_reward'] if u['first_status']=='used' else 0}\nSecond: ₹{u['second_reward'] if u['second_status']=='used' else 0}\nWheel: ₹{u['wheel_reward'] if u['wheel_status']=='used' else 0}\n\n💰 Balance: ₹{u['balance']}")
    if q.data=='withdraw':
        u=user(uid)
        with db() as c:p=c.execute("SELECT * FROM withdrawals WHERE user_id=? AND status='pending'",(uid,)).fetchone()
        if p:return await q.message.reply_text(f"⏳ Pending withdrawal ₹{p['amount']} to {p['upi_id']}")
        if u['balance']<MIN_WITHDRAWAL:return await q.message.reply_text(f"Minimum withdrawal ₹{MIN_WITHDRAWAL}. Your balance ₹{u['balance']}")
        upd(uid,state=WAIT_UPI); return await q.message.reply_text('Apni UPI ID bhejein, example: name@upi')

async def photos(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=ensure(update.effective_user); uid=u['user_id']; fid=update.message.photo[-1].file_id
    if u['state']==WAIT_REG_PROOF: upd(uid,registration_proof=fid,state=WAIT_REG_ID); await update.message.reply_text('🆔 Registration ID bhejein.')
    elif u['state']==WAIT_NEWBIE_PROOF: upd(uid,newbie_proof=fid,state=WAIT_NEWBIE_ID); await update.message.reply_text('🆔 Apni ID bhejein.')
    else: await update.message.reply_text('Pehle Upload Proof button tap karein.')
async def texts(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    u=ensure(update.effective_user); uid=u['user_id']; t=update.message.text.strip()
    if u['state']==WAIT_REG_ID:
        upd(uid,registration_id=t,registration_status='pending',state=None)
        with db() as c:c.execute('INSERT INTO reviews(user_id,kind,created_at) VALUES(?,?,?)',(uid,'registration',now()))
        return await update.message.reply_text('✅ Registration proof submitted. Admin approval ka wait karein.')
    if u['state']==WAIT_NEWBIE_ID:
        upd(uid,newbie_id=t,newbie_status='pending',state=None)
        with db() as c:c.execute('INSERT INTO reviews(user_id,kind,created_at) VALUES(?,?,?)',(uid,'newbie',now()))
        return await update.message.reply_text('✅ Newbie Order proof submitted. Admin approval ka wait karein.')
    if u['state']==WAIT_UPI:
        if not re.fullmatch(r'[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}',t): return await update.message.reply_text('Valid UPI ID bhejein.')
        amount=user(uid)['balance']
        with db() as c:c.execute('INSERT INTO withdrawals(user_id,amount,upi_id,created_at) VALUES(?,?,?,?)',(uid,amount,t,now()))
        upd(uid,balance=0,upi_id=t,state=None); return await update.message.reply_text(f'✅ Withdrawal submitted\nAmount: ₹{amount}\nUPI: {t}\nStatus: Pending')
    await update.message.reply_text('Menu ke liye /start bhejein.')

async def stats(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:return
    await update.message.reply_text(f'Admin Panel: {PUBLIC_URL}/admin')

def run_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    b=Application.builder().token(BOT_TOKEN).build(); b.add_handler(CommandHandler('start',start)); b.add_handler(CommandHandler('chatid',chatid)); b.add_handler(CommandHandler('id',chatid)); b.add_handler(CommandHandler('cancel',cancel)); b.add_handler(CommandHandler('stats',stats)); b.add_handler(CallbackQueryHandler(buttons)); b.add_handler(MessageHandler(filters.PHOTO,photos)); b.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,texts)); b.run_polling(drop_pending_updates=True, close_loop=False, stop_signals=None)

@app.get('/')
def home():return 'Reward Bot Pro running',200
@app.get('/scratch/<int:stage>')
def scratch(stage):
    purpose='s1' if stage==1 else 's2'; uid=verify_token(request.args.get('token',''),purpose)
    if not uid:return 'Invalid link',403
    u=user(uid)
    if stage==1 and (not u or not u['verified']):return 'Locked',403
    if stage==2 and u['registration_status']!='approved':return 'Locked',403
    reward=u['first_reward'] if stage==1 else u['second_reward']
    return render_template('scratch.html',stage=stage,reward=reward,token=request.args['token'])
@app.post('/api/scratch')
def scratch_api():
    d=request.get_json() or {}; stage=int(d.get('stage',0)); purpose='s1' if stage==1 else 's2'; uid=verify_token(d.get('token',''),purpose)
    if not uid:return jsonify(ok=False),403
    u=user(uid); key='first' if stage==1 else 'second'; status=u[f'{key}_status']; reward=u[f'{key}_reward']
    if status!='used': upd(uid,**{f'{key}_status':'used','balance':u['balance']+reward})
    return jsonify(ok=True,reward=reward,next='Registration complete karke proof upload karein.' if stage==1 else 'Complete Newbie Order, then Upload Proof aur ID bhejein.')
@app.get('/wheel')
def wheel():
    uid=verify_token(request.args.get('token',''),'wheel'); u=user(uid) if uid else None
    if not u or u['newbie_status']!='approved':return 'Locked',403
    return render_template('wheel.html',reward=u['wheel_reward'],token=request.args['token'])
@app.post('/api/wheel')
def wheel_api():
    d=request.get_json() or {}; uid=verify_token(d.get('token',''),'wheel')
    if not uid:return jsonify(ok=False),403
    u=user(uid)
    if u['wheel_status']!='used':upd(uid,wheel_status='used',balance=u['balance']+u['wheel_reward'])
    return jsonify(ok=True,reward=u['wheel_reward'])

def admin_required(f):
    @wraps(f)
    def x(*a,**k):return f(*a,**k) if session.get('admin') else redirect(url_for('login'))
    return x
@app.route('/admin/login',methods=['GET','POST'])
def login():
    error=None
    if request.method=='POST':
        if hmac.compare_digest(request.form.get('username',''),ADMIN_USERNAME) and hmac.compare_digest(request.form.get('password',''),ADMIN_PASSWORD):session['admin']=1;return redirect('/admin')
        error='Invalid login'
    return render_template('login.html',error=error)
@app.get('/admin/logout')
def logout():session.clear();return redirect('/admin/login')
@app.get('/admin')
@admin_required
def admin():
    with db() as c:
        counts={'users':c.execute('SELECT COUNT(*) FROM users').fetchone()[0],'verified':c.execute('SELECT COUNT(*) FROM users WHERE verified=1').fetchone()[0],'proofs':c.execute("SELECT COUNT(*) FROM reviews WHERE status='pending'").fetchone()[0],'withdrawals':c.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'").fetchone()[0]}
        proofs=c.execute("SELECT r.*,u.first_name,u.username,u.registration_id,u.newbie_id FROM reviews r JOIN users u ON u.user_id=r.user_id WHERE r.status='pending' ORDER BY r.id DESC").fetchall()
        withdrawals=c.execute("SELECT w.*,u.first_name,u.username FROM withdrawals w JOIN users u ON u.user_id=w.user_id WHERE w.status='pending' ORDER BY w.id DESC").fetchall()
        users=c.execute('SELECT * FROM users ORDER BY joined_at DESC LIMIT 100').fetchall()
    return render_template('admin.html',counts=counts,proofs=proofs,withdrawals=withdrawals,users=users)

def send_message(uid,text,markup=None):
    async def go():
        b=Application.builder().token(BOT_TOKEN).build(); await b.initialize(); await b.bot.send_message(uid,text,reply_markup=markup); await b.shutdown()
    asyncio.run(go())
@app.post('/admin/proof/<int:rid>/<action>')
@admin_required
def proof_action(rid,action):
    with db() as c:
        r=c.execute('SELECT * FROM reviews WHERE id=?',(rid,)).fetchone()
        if not r:return redirect('/admin')
        status='approved' if action=='approve' else 'rejected'; reason=request.form.get('reason',''); reward=int(request.form.get('reward','50'))
        c.execute('UPDATE reviews SET status=?,reason=? WHERE id=?',(status,reason,rid))
        if r['kind']=='registration':c.execute('UPDATE users SET registration_status=?,second_status=?,second_reward=? WHERE user_id=?',(status,'ready' if status=='approved' else 'locked',10,r['user_id']))
        else:c.execute('UPDATE users SET newbie_status=?,wheel_status=?,wheel_reward=? WHERE user_id=?',(status,'ready' if status=='approved' else 'locked',reward,r['user_id']))
    if status=='approved' and r['kind']=='registration':send_message(r['user_id'],'✅ Registration Approved! Second Scratch Card unlock ho gaya.',InlineKeyboardMarkup([[InlineKeyboardButton('🎁 Open Second Scratch',web_app=WebAppInfo(wurl('/scratch/2',r['user_id'],'s2')))]]))
    elif status=='approved':send_message(r['user_id'],'✅ Newbie Order Approved! Lucky Wheel unlock ho gaya.',InlineKeyboardMarkup([[InlineKeyboardButton('🎡 Open Lucky Wheel',web_app=WebAppInfo(wurl('/wheel',r['user_id'],'wheel')))]]))
    else:send_message(r['user_id'],f'❌ Proof rejected. Reason: {reason or "Invalid proof"}')
    return redirect('/admin')
@app.post('/admin/withdrawal/<int:wid>/<action>')
@admin_required
def withdrawal_action(wid,action):
    with db() as c:
        w=c.execute('SELECT * FROM withdrawals WHERE id=?',(wid,)).fetchone()
        if not w:return redirect('/admin')
        reason=request.form.get('reason',''); status='paid' if action=='paid' else 'rejected'; c.execute('UPDATE withdrawals SET status=?,reason=? WHERE id=?',(status,reason,wid))
        if status=='rejected':c.execute('UPDATE users SET balance=balance+? WHERE user_id=?',(w['amount'],w['user_id']))
    send_message(w['user_id'],f"✅ Withdrawal Paid: ₹{w['amount']}" if status=='paid' else f"❌ Withdrawal rejected. ₹{w['amount']} returned. Reason: {reason or 'Payment issue'}")
    return redirect('/admin')
@app.post('/admin/user/<int:uid>/toggle')
@admin_required
def toggle(uid):
    u=user(uid); upd(uid,blocked=0 if u['blocked'] else 1); return redirect('/admin')

if __name__=='__main__':
    if not BOT_TOKEN or not PUBLIC_URL.startswith('https://'):raise RuntimeError('Set BOT_TOKEN and HTTPS PUBLIC_URL')
    init_db(); threading.Thread(target=run_bot,daemon=True).start(); serve(app,host='0.0.0.0',port=PORT,threads=8)
