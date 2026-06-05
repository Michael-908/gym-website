from flask import Flask, render_template, redirect, url_for, flash, request, abort, jsonify, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from models import (db, User, Member, Trainer, Workout, Nutrition, Attendance,
                    Payment, Progress, Class, ClassBooking, MembershipPlan,
                    MemberWorkout, WorkoutComment, NutritionComment, MpesaTransaction,
                    WorkoutLog, BodyMeasurement, TrainerRating)
from dotenv import load_dotenv
from functools import wraps
import os, stripe, requests, base64, json, io
from datetime import datetime, timedelta, date, timezone  # Added timezone here
from fpdf import FPDF
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature  # Added for password resets


app = Flask(__name__)
load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'gymapp-secret-key-2026')

# Get the database URL from Render, fallback to local SQLite if not set
database_url = os.getenv('DATABASE_URL', 'sqlite:///gym.db')

# Fix Render's legacy 'postgres://' prefix for SQLAlchemy 1.4+ compatibility
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI']        = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# Use Secure cookies on HTTPS (Render), plain cookies on local http
app.config['SESSION_COOKIE_SECURE']   = os.getenv('DATABASE_URL') is not None
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Stripe
app.config['STRIPE_PUBLIC_KEY']  = os.getenv('STRIPE_PUBLIC_KEY')
app.config['STRIPE_SECRET_KEY']  = os.getenv('STRIPE_SECRET_KEY')
stripe.api_key = app.config['STRIPE_SECRET_KEY'] or ''

# M-Pesa Daraja
app.config['MPESA_CONSUMER_KEY']    = os.getenv('MPESA_CONSUMER_KEY', '')
app.config['MPESA_CONSUMER_SECRET'] = os.getenv('MPESA_CONSUMER_SECRET', '')
app.config['MPESA_SHORTCODE']       = os.getenv('MPESA_SHORTCODE', '174379')
app.config['MPESA_PASSKEY']         = os.getenv('MPESA_PASSKEY', '')
app.config['MPESA_ENV']             = os.getenv('MPESA_ENV', 'sandbox')  # sandbox or production
app.config['MPESA_CALLBACK_URL']    = os.getenv('MPESA_CALLBACK_URL', 'https://yourdomain.com/webhook/mpesa')

# Email
app.config['MAIL_SERVER']         = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']           = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']        = True
app.config['MAIL_USERNAME']       = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.getenv('MAIL_PASSWORD')

# Anthropic (AI)
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

# ── Extensions ────────────────────────────────────────────────────────────────
db.init_app(app)
bcrypt = Bcrypt(app)
mail   = Mail(app)

# ── Init DB ───────────────────────────────────────────────────────────────────
# Run once at startup inside an app context — safe for Gunicorn multi-workers
# because each worker independently creates tables (CREATE TABLE IF NOT EXISTS
# is idempotent) and the admin/plan seed checks are guarded by .first() queries.
def initialize_database():
    with app.app_context():
        db.create_all()

        # Seed default admin if none exists
        if not User.query.filter_by(role='admin').first():
            hashed = bcrypt.generate_password_hash('admin123').decode('utf-8')
            db.session.add(User(
                username='Admin',
                email='admin@gym.com',
                password_hash=hashed,
                role='admin',
                added_by_admin=False
            ))
            db.session.commit()
            print('Default admin created: admin@gym.com / admin123 — Kinetix Gym')

        # Seed membership plans if none exist
        if not MembershipPlan.query.first():
            db.session.add_all([
                MembershipPlan(name='Monthly',   price=3000.0,  duration_days=30),
                MembershipPlan(name='Quarterly', price=8000.0,  duration_days=90),
                MembershipPlan(name='Annual',    price=25000.0, duration_days=365),
            ])
            db.session.commit()
            print('Default membership plans seeded.')

# Call immediately at module load time — works for both `gunicorn app:app`
# and `python app.py` (local dev)
initialize_database()

# ── Login Manager ─────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view             = 'login'
login_manager.login_message          = 'Please log in to access this page.'
login_manager.login_message_category = 'info'
login_manager.session_protection     = 'basic'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.context_processor
def inject_subscription_status():
    """Make subscription status available in all templates."""
    if current_user.is_authenticated and current_user.role == 'member':
        member = Member.query.filter_by(user_id=current_user.id).first()
        subscribed = member_has_active_subscription(member) if member else False
        return dict(
            has_subscription=subscribed,
            is_admin_member=current_user.added_by_admin
        )
    return dict(has_subscription=True, is_admin_member=False)

# ── Role Decorators ───────────────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            return redirect(url_for('smart_redirect'))
        return f(*args, **kwargs)
    return decorated

def trainer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'trainer']:
            return redirect(url_for('smart_redirect'))
        return f(*args, **kwargs)
    return decorated

def member_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'member':
            return redirect(url_for('smart_redirect'))
        member = Member.query.filter_by(user_id=current_user.id).first()
        if not member:
            member = Member(user_id=current_user.id, name=current_user.username,
                           email=current_user.email, status='active')
            db.session.add(member)
            db.session.commit()
        return f(*args, **kwargs)
    return decorated

@app.route('/redirect')
@login_required
def smart_redirect():
    if current_user.role == 'admin':
        return redirect(url_for('dashboard'))
    elif current_user.role == 'trainer':
        return redirect(url_for('trainer_dashboard'))
    else:
        return redirect(url_for('member_dashboard'))

# ── Serialiser for password-reset tokens ─────────────────────────────────────
def get_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'])

# ── Email Helpers ─────────────────────────────────────────────────────────────
def send_welcome_email(name, email, password=None):
    """Send a welcome email to a new member."""
    try:
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#0D1117;color:#E6EDF3;border-radius:12px;overflow:hidden;">
          <div style="background:#1A73E8;padding:28px 32px;text-align:center;">
            <h1 style="margin:0;color:#fff;font-size:1.6rem;">💪 Welcome to Kinetix Gym!</h1>
          </div>
          <div style="padding:32px;">
            <p style="font-size:1rem;">Hi <strong>{name}</strong>,</p>
            <p>We're thrilled to have you as part of the <strong>Kinetix Gym</strong> family. Your account has been created and you're all set to start your fitness journey with us!</p>
            {"<p><strong>Your temporary password:</strong> <code style='background:#21262D;padding:4px 8px;border-radius:4px;'>" + password + "</code><br><small>Please change it after first login via Settings.</small></p>" if password else ""}
            <p>Log in at any time to:</p>
            <ul>
              <li>Track your workouts &amp; progress</li>
              <li>View your nutrition plans</li>
              <li>Book gym classes</li>
              <li>Manage your membership</li>
            </ul>
            <p style="margin-top:24px;color:#8B949E;font-size:0.85rem;">Stay fit, stay healthy — the Kinetix Gym team.</p>
          </div>
        </div>
        """
        msg = Message(
            subject="Welcome to Kinetix Gym! 🏋️",
            recipients=[email],
            html=html,
            sender=app.config.get('MAIL_USERNAME', 'noreply@kinetixgym.com')
        )
        mail.send(msg)
    except Exception as e:
        print(f"Welcome email error: {e}")


def send_subscription_receipt(member, payment, plan):
    """Send a payment receipt email after successful subscription."""
    try:
        inv = payment.invoice_number or f"INV-{payment.id:05d}"
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#0D1117;color:#E6EDF3;border-radius:12px;overflow:hidden;">
          <div style="background:#1A73E8;padding:28px 32px;text-align:center;">
            <h1 style="margin:0;color:#fff;font-size:1.4rem;">🧾 Payment Receipt — Kinetix Gym</h1>
          </div>
          <div style="padding:32px;">
            <p>Hi <strong>{member.name}</strong>, thank you for subscribing!</p>
            <table style="width:100%;border-collapse:collapse;margin-top:16px;">
              <tr style="background:#21262D;">
                <td style="padding:10px 14px;font-weight:bold;">Invoice</td>
                <td style="padding:10px 14px;">{inv}</td>
              </tr>
              <tr>
                <td style="padding:10px 14px;font-weight:bold;">Plan</td>
                <td style="padding:10px 14px;">{plan.name} ({plan.duration_days} days)</td>
              </tr>
              <tr style="background:#21262D;">
                <td style="padding:10px 14px;font-weight:bold;">Amount Paid</td>
                <td style="padding:10px 14px;color:#10B981;font-weight:bold;">KSh {payment.amount:,.2f}</td>
              </tr>
              <tr>
                <td style="padding:10px 14px;font-weight:bold;">Payment Method</td>
                <td style="padding:10px 14px;">{(payment.method or "N/A").upper()}</td>
              </tr>
              <tr style="background:#21262D;">
                <td style="padding:10px 14px;font-weight:bold;">Expiry Date</td>
                <td style="padding:10px 14px;">{payment.expiry_date.strftime("%d %B %Y") if payment.expiry_date else "N/A"}</td>
              </tr>
              {"<tr><td style='padding:10px 14px;font-weight:bold;'>M-Pesa Receipt</td><td style='padding:10px 14px;'>" + payment.mpesa_receipt + "</td></tr>" if payment.mpesa_receipt else ""}
            </table>
            <p style="margin-top:24px;">All features are now unlocked. Enjoy your membership!</p>
            <p style="color:#8B949E;font-size:0.85rem;">— Kinetix Gym Team</p>
          </div>
        </div>
        """
        msg = Message(
            subject=f"Kinetix Gym — Payment Receipt ({inv})",
            recipients=[member.email],
            html=html,
            sender=app.config.get('MAIL_USERNAME', 'noreply@kinetixgym.com')
        )
        mail.send(msg)
    except Exception as e:
        print(f"Receipt email error: {e}")


def send_password_reset_email(email, reset_url):
    """Send a password-reset link email."""
    try:
        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;background:#0D1117;color:#E6EDF3;border-radius:12px;overflow:hidden;">
          <div style="background:#F85149;padding:28px 32px;text-align:center;">
            <h1 style="margin:0;color:#fff;font-size:1.4rem;">🔑 Password Reset — Kinetix Gym</h1>
          </div>
          <div style="padding:32px;">
            <p>We received a request to reset your Kinetix Gym password.</p>
            <p>Click the button below to set a new password. This link expires in <strong>30 minutes</strong>.</p>
            <div style="text-align:center;margin:28px 0;">
              <a href="{reset_url}" style="background:#1A73E8;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:bold;font-size:1rem;">Reset My Password</a>
            </div>
            <p style="font-size:0.85rem;color:#8B949E;">If you didn't request this, you can safely ignore this email. Your password will remain unchanged.</p>
          </div>
        </div>
        """
        msg = Message(
            subject="Kinetix Gym — Password Reset Request",
            recipients=[email],
            html=html,
            sender=app.config.get('MAIL_USERNAME', 'noreply@kinetixgym.com')
        )
        mail.send(msg)
    except Exception as e:
        print(f"Reset email error: {e}")


def member_has_active_subscription(member):
    """Return True if member has a valid (non-expired) payment or was added by admin."""
    if current_user.added_by_admin:
        return True
    if not member:
        return False
    today = date.today()
    active = Payment.query.filter_by(member_id=member.id, status='completed').filter(
        Payment.expiry_date >= today
    ).first()
    return active is not None


def subscription_required(f):
    """Lock routes for self-registered members who haven't subscribed yet."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'member':
            return redirect(url_for('smart_redirect'))
        if current_user.added_by_admin:
            return f(*args, **kwargs)
        member = Member.query.filter_by(user_id=current_user.id).first()
        if not member_has_active_subscription(member):
            flash('Please subscribe to a membership plan to access this feature.', 'warning')
            return redirect(url_for('my_payments'))
        return f(*args, **kwargs)
    return decorated

# ── M-Pesa Helpers ────────────────────────────────────────────────────────────
def get_mpesa_token():
    env = app.config['MPESA_ENV']
    base = 'https://sandbox.safaricom.co.ke' if env == 'sandbox' else 'https://api.safaricom.co.ke'
    key    = app.config['MPESA_CONSUMER_KEY']
    secret = app.config['MPESA_CONSUMER_SECRET']
    if not key or not secret:
        return None
    creds = base64.b64encode(f'{key}:{secret}'.encode()).decode()
    try:
        r = requests.get(
            f'{base}/oauth/v1/generate?grant_type=client_credentials',
            headers={'Authorization': f'Basic {creds}'}, timeout=10
        )
        return r.json().get('access_token')
    except Exception:
        return None

def stk_push(phone, amount, account_ref, description):
    """Initiate M-Pesa STK Push. Returns (checkout_request_id, merchant_request_id) or (None, error_msg)."""
    env = app.config['MPESA_ENV']
    base = 'https://sandbox.safaricom.co.ke' if env == 'sandbox' else 'https://api.safaricom.co.ke'
    token = get_mpesa_token()
    if not token:
        return None, 'Could not authenticate with M-Pesa. Check API credentials.'

    shortcode = app.config['MPESA_SHORTCODE']
    passkey   = app.config['MPESA_PASSKEY']
    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    password  = base64.b64encode(f'{shortcode}{passkey}{timestamp}'.encode()).decode()

    # Normalize phone: 0712... -> 254712...
    phone = phone.strip().replace(' ', '').replace('-', '')
    if phone.startswith('0'):
        phone = '254' + phone[1:]
    elif phone.startswith('+'):
        phone = phone[1:]

    payload = {
        'BusinessShortCode': shortcode,
        'Password': password,
        'Timestamp': timestamp,
        'TransactionType': 'CustomerPayBillOnline',
        'Amount': int(amount),
        'PartyA': phone,
        'PartyB': shortcode,
        'PhoneNumber': phone,
        'CallBackURL': app.config['MPESA_CALLBACK_URL'],
        'AccountReference': account_ref,
        'TransactionDesc': description
    }
    try:
        r = requests.post(
            f'{base}/mpesa/stkpush/v1/processrequest',
            json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=15
        )
        data = r.json()
        if data.get('ResponseCode') == '0':
            return data.get('CheckoutRequestID'), data.get('MerchantRequestID')
        return None, data.get('CustomerMessage', data.get('errorMessage', 'STK Push failed'))
    except Exception as e:
        return None, str(e)

# ── Invoice Generator ─────────────────────────────────────────────────────────
def generate_invoice_pdf(payment, member):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Header
    pdf.set_fill_color(13, 17, 23)
    pdf.rect(0, 0, 210, 40, 'F')
    pdf.set_text_color(26, 115, 232)
    pdf.set_font('Helvetica', 'B', 22)
    pdf.set_xy(10, 10)
    pdf.cell(0, 10, 'KINETIX GYM', ln=True)
    pdf.set_text_color(139, 148, 158)
    pdf.set_font('Helvetica', '', 10)
    pdf.set_xy(10, 22)
    pdf.cell(0, 8, 'Nairobi, Kenya  |  info@kinetixgym.com  |  +254 700 000 000', ln=True)

    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(10, 50)

    # Invoice title
    pdf.set_font('Helvetica', 'B', 16)
    pdf.set_text_color(26, 115, 232)
    pdf.cell(0, 10, 'PAYMENT INVOICE', ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font('Helvetica', '', 10)
    pdf.ln(2)

    inv_no = payment.invoice_number or f'INV-{payment.id:05d}'
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(50, 7, 'Invoice Number:')
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 7, inv_no, ln=True)

    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(50, 7, 'Invoice Date:')
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 7, payment.payment_date.strftime('%d %B %Y') if payment.payment_date else 'N/A', ln=True)

    if payment.expiry_date:
        pdf.set_font('Helvetica', 'B', 10)
        pdf.cell(50, 7, 'Valid Until:')
        pdf.set_font('Helvetica', '', 10)
        pdf.cell(0, 7, payment.expiry_date.strftime('%d %B %Y'), ln=True)

    pdf.ln(6)

    # Bill To
    pdf.set_fill_color(230, 237, 243)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(0, 8, '  BILLED TO', fill=True, ln=True)
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 7, f'  {member.name}', ln=True)
    pdf.cell(0, 7, f'  {member.email or "—"}', ln=True)
    pdf.cell(0, 7, f'  {member.phone or "—"}', ln=True)
    if member.age:
        pdf.cell(0, 7, f'  Age: {member.age} | Group: {member.age_group or "Adult"}', ln=True)
    pdf.ln(4)

    # Table header
    pdf.set_fill_color(13, 17, 23)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(90, 9, '  Description', fill=True)
    pdf.cell(40, 9, 'Method', fill=True, align='C')
    pdf.cell(60, 9, 'Amount (KSh)', fill=True, align='R', ln=True)

    # Table row
    pdf.set_text_color(0, 0, 0)
    pdf.set_fill_color(245, 248, 252)
    pdf.set_font('Helvetica', '', 10)
    plan_name = payment.plan.name if payment.plan else 'Gym Membership'
    pdf.cell(90, 9, f'  {plan_name} Membership', fill=True)
    pdf.cell(40, 9, (payment.method or 'N/A').upper(), fill=True, align='C')
    pdf.cell(60, 9, f'{payment.amount:,.2f}', fill=True, align='R', ln=True)

    # M-Pesa receipt if available
    if payment.mpesa_receipt:
        pdf.set_fill_color(255, 255, 255)
        pdf.cell(90, 9, '  M-Pesa Transaction Code')
        pdf.cell(40, 9, '')
        pdf.cell(60, 9, payment.mpesa_receipt, align='R', ln=True)

    pdf.ln(2)
    # Total
    pdf.set_fill_color(26, 115, 232)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Helvetica', 'B', 11)
    pdf.cell(130, 10, '  TOTAL PAID', fill=True)
    pdf.cell(60, 10, f'KSh {payment.amount:,.2f}', fill=True, align='R', ln=True)

    pdf.set_text_color(0, 0, 0)
    pdf.ln(8)

    # Status badge
    pdf.set_font('Helvetica', 'B', 10)
    status_color = (16, 185, 129) if payment.status == 'completed' else (245, 158, 11)
    pdf.set_text_color(*status_color)
    pdf.cell(0, 8, f'Payment Status: {payment.status.upper()}', ln=True)

    pdf.set_text_color(139, 148, 158)
    pdf.set_font('Helvetica', '', 9)
    pdf.ln(6)
    pdf.cell(0, 6, 'Thank you for your membership. Stay fit, stay strong — Kinetix Gym!', ln=True, align='C')
    pdf.cell(0, 6, 'This is a computer-generated invoice and requires no signature.', ln=True, align='C')

    buf = io.BytesIO()
    pdf_bytes = pdf.output()
    buf.write(pdf_bytes)
    buf.seek(0)
    return buf

# ── AI Workout Suggestion ─────────────────────────────────────────────────────
def get_ai_workout_suggestion(age, goal, fitness_level='Beginner', medical_notes=''):
    """Call Anthropic API to get personalized workout suggestions."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = f"""You are a professional gym trainer at Kinetix Gym in Nairobi, Kenya. Suggest 3 specific workout plans for a member with these details:
- Age: {age}
- Fitness Goal: {goal}
- Fitness Level: {fitness_level}
- Medical Notes: {medical_notes or 'None'}

Return ONLY a JSON array (no markdown, no extra text) with exactly 3 objects, each having these fields:
"name" (string), "type" (one of: HIIT/Strength/Cardio/Flexibility/General), "duration" (integer minutes), 
"difficulty" (Beginner/Intermediate/Advanced), "description" (2-3 sentences), "exercises" (list of 4-5 exercise names)

Example format: [{{"name":"Morning HIIT","type":"HIIT","duration":30,"difficulty":"Beginner","description":"...","exercises":["Jumping Jacks","Burpees"]}}]"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith('```'):
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f'AI suggestion error: {e}')
        return None

# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('smart_redirect'))
    if request.method == 'POST':
        email    = request.form.get('email')
        password = request.form.get('password')
        user     = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('smart_redirect'))
        flash('Invalid email or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email    = request.form.get('email')
        password = request.form.get('password')
        role     = request.form.get('role', 'member')
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'warning')
            return redirect(url_for('login'))
        hashed   = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(username=username, email=email, password_hash=hashed, role=role,
                        added_by_admin=False)
        db.session.add(new_user)
        db.session.flush()
        if role == 'member':
            db.session.add(Member(user_id=new_user.id, name=username, email=email))
        db.session.commit()
        send_welcome_email(username, email)
        flash('Account created! A welcome email has been sent. You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user  = User.query.filter_by(email=email).first()
        if user:
            s     = get_serializer()
            token = s.dumps(email, salt='password-reset')
            user.reset_token        = token
            # Modern, non-deprecated way to get a naive UTC datetime object
            user.reset_token_expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)
            db.session.commit()
            reset_url = url_for('reset_password', token=token, _external=True)
            send_password_reset_email(email, reset_url)
        
        # Always show success to avoid email enumeration
        flash('If that email is registered, a reset link has been sent. Check your inbox.', 'info')
        return redirect(url_for('login'))
    return render_template('forgot-password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    s = get_serializer()
    try:
        # max_age=1800 enforces the 30-minute limit on the token itself
        email = s.loads(token, salt='password-reset', max_age=1800)
    except (SignatureExpired, BadSignature):
        flash('The reset link is invalid or has expired. Please request a new one.', 'danger')
        return redirect(url_for('forgot_password'))

    # Verify the user exists and the token matches what is in the DB
    user = User.query.filter_by(email=email, reset_token=token).first()
    
    # Extra safety check: verify database expiry time against current time
    if not user or (user.reset_token_expiry and user.reset_token_expiry < datetime.now(timezone.utc).replace(tzinfo=None)):
        flash('Invalid or expired reset link.', 'danger')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        new_pw  = request.form.get('new_password', '')
        conf_pw = request.form.get('confirm_password', '')
        
        if len(new_pw) < 6:
            flash('Password must be at least 6 characters.', 'danger')
            return render_template('reset_password.html', token=token)
        if new_pw != conf_pw:
            flash('Passwords do not match.', 'danger')
            return render_template('reset_password.html', token=token)
            
        # Update password and clear out the reset tokens so they can't be reused
        user.password_hash      = bcrypt.generate_password_hash(new_pw).decode('utf-8')
        user.reset_token        = None
        user.reset_token_expiry = None
        db.session.commit()
        
        flash('Password updated successfully! You can now log in.', 'success')
        return redirect(url_for('login'))

    return render_template('reset_password.html', token=token)

# ═══════════════════════════════════════════════════════════════════════════════
#  ADMIN DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/dashboard')
@login_required
@admin_required
def dashboard():
    total_members   = Member.query.count()
    active_members  = Member.query.filter_by(status='active').count()
    total_trainers  = Trainer.query.count()
    total_payments  = db.session.query(db.func.sum(Payment.amount)).scalar() or 0
    total_classes   = Class.query.count()
    total_workouts  = Workout.query.filter_by(is_personal=False).count()
    cash_revenue    = db.session.query(db.func.sum(Payment.amount)).filter_by(method='cash').scalar() or 0
    mpesa_revenue   = db.session.query(db.func.sum(Payment.amount)).filter_by(method='mpesa').scalar() or 0
    card_revenue    = (db.session.query(db.func.sum(Payment.amount)).filter_by(method='card').scalar() or 0) + \
                      (db.session.query(db.func.sum(Payment.amount)).filter_by(method='stripe').scalar() or 0)
    recent_payments   = Payment.query.order_by(Payment.payment_date.desc()).limit(5).all()
    recent_attendance = Attendance.query.order_by(Attendance.check_in.desc()).limit(10).all()

    # Members expiring soon (within 7 days)
    today = date.today()
    expiring_soon = Payment.query.filter(
        Payment.expiry_date >= today,
        Payment.expiry_date <= today + timedelta(days=7),
        Payment.status == 'completed'
    ).all()

    # Age group breakdown
    youth_count  = Member.query.filter_by(age_group='Youth').count()
    adult_count  = Member.query.filter_by(age_group='Adult').count()
    senior_count = Member.query.filter_by(age_group='Senior').count()

    return render_template('index.html',
                           total_members=total_members, active_members=active_members,
                           total_trainers=total_trainers, total_payments=total_payments,
                           total_classes=total_classes, total_workouts=total_workouts,
                           cash_revenue=cash_revenue, mpesa_revenue=mpesa_revenue,
                           card_revenue=card_revenue, recent_payments=recent_payments,
                           recent_attendance=recent_attendance, expiring_soon=expiring_soon,
                           youth_count=youth_count, adult_count=adult_count, senior_count=senior_count)

# ═══════════════════════════════════════════════════════════════════════════════
#  TRAINER DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/trainer-dashboard')
@login_required
@trainer_required
def trainer_dashboard():
    trainer = Trainer.query.filter_by(user_id=current_user.id).first()
    today   = date.today()
    today_classes = []
    my_clients    = []
    if trainer:
        today_classes = Class.query.filter(
            Class.trainer_id == trainer.id,
            db.cast(Class.schedule, db.Date) == today
        ).all()
        workout_ids = [w.id for w in Workout.query.filter_by(trainer_id=trainer.id).all()]
        if workout_ids:
            my_clients = Member.query.join(MemberWorkout).filter(
                MemberWorkout.workout_id.in_(workout_ids)
            ).distinct().all()

    week_attendance   = Attendance.query.filter(Attendance.date >= today).count()
    recent_attendance = Attendance.query.order_by(Attendance.check_in.desc()).limit(10).all()
    all_members       = Member.query.all()

    return render_template('trainer_dashboard.html',
                           trainer=trainer, today_classes=today_classes,
                           my_clients=my_clients, week_attendance=week_attendance,
                           recent_attendance=recent_attendance, all_members=all_members, today=today)

# ═══════════════════════════════════════════════════════════════════════════════
#  MEMBER DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/member-dashboard')
@login_required
def member_dashboard():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('login'))

    today            = date.today()
    member_workouts  = MemberWorkout.query.filter_by(member_id=member.id).order_by(MemberWorkout.assigned_date.desc()).limit(5).all()
    member_progress  = Progress.query.filter_by(member_id=member.id).order_by(Progress.date.desc()).limit(6).all()
    upcoming_classes = (ClassBooking.query.filter_by(member_id=member.id)
                        .join(Class).filter(Class.schedule >= datetime.now())
                        .order_by(Class.schedule).limit(5).all())
    nutrition_plan   = Nutrition.query.filter_by(member_id=member.id).order_by(Nutrition.date.desc()).first()
    recent_payments  = Payment.query.filter_by(member_id=member.id).order_by(Payment.payment_date.desc()).limit(3).all()
    latest_payment   = Payment.query.filter_by(member_id=member.id).order_by(Payment.expiry_date.desc()).first()
    # Use extract() which works on both PostgreSQL and SQLite
    month_attendance = Attendance.query.filter_by(member_id=member.id).filter(
        db.extract('year',  Attendance.date) == today.year,
        db.extract('month', Attendance.date) == today.month
    ).count()
    available_classes = Class.query.filter(Class.schedule >= datetime.now()).order_by(Class.schedule).limit(10).all()
    all_trainers      = Trainer.query.all()
    recent_logs       = WorkoutLog.query.filter_by(member_id=member.id).order_by(WorkoutLog.completed_at.desc()).limit(5).all()
    latest_measurements = BodyMeasurement.query.filter_by(member_id=member.id).order_by(BodyMeasurement.date.desc()).first()

    has_subscription = member_has_active_subscription(member)
    
    return render_template('member_dashboard.html',
                           member=member, member_workouts=member_workouts,
                           member_progress=member_progress, upcoming_classes=upcoming_classes,
                           nutrition_plan=nutrition_plan, recent_payments=recent_payments,
                           latest_payment=latest_payment, month_attendance=month_attendance,
                           available_classes=available_classes, all_trainers=all_trainers,
                           recent_logs=recent_logs, latest_measurements=latest_measurements,
                           has_subscription=has_subscription, today=today)
# ═══════════════════════════════════════════════════════════════════════════════
#  MEMBERS (Admin)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/members')
@login_required
@admin_required
def members():
    return render_template('members.html', members=Member.query.all(), plans=MembershipPlan.query.all())

@app.route('/members/add', methods=['POST'])
@login_required
@admin_required
def add_member():
    name     = request.form.get('name')
    email    = request.form.get('email')
    phone    = request.form.get('phone')
    plan_id  = request.form.get('plan_id')
    goal     = request.form.get('goal')
    status   = request.form.get('status', 'active')
    password = request.form.get('password')
    dob_str  = request.form.get('date_of_birth')
    age_group = request.form.get('age_group', 'Adult')
    emergency_name  = request.form.get('emergency_contact_name')
    emergency_phone = request.form.get('emergency_contact_phone')
    mpesa_phone     = request.form.get('mpesa_phone')
    medical_notes   = request.form.get('medical_notes')

    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'danger')
        return redirect(url_for('members'))

    hashed   = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user = User(username=name, email=email, password_hash=hashed, role='member',
                    added_by_admin=True)
    db.session.add(new_user)
    db.session.flush()

    dob = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None
    db.session.add(Member(
        user_id=new_user.id, name=name, email=email, phone=phone,
        plan_id=int(plan_id) if plan_id else None, goal=goal, status=status,
        date_of_birth=dob, age_group=age_group,
        emergency_contact_name=emergency_name, emergency_contact_phone=emergency_phone,
        mpesa_phone=mpesa_phone, medical_notes=medical_notes
    ))
    db.session.commit()
    # Send welcome email with their password so they can log in
    send_welcome_email(name, email, password=password)
    flash(f'Member {name} added! A welcome email has been sent to {email}.', 'success')
    return redirect(url_for('members'))

@app.route('/members/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_member(id):
    member = Member.query.get_or_404(id)
    if request.method == 'POST':
        member.name    = request.form.get('name')
        member.email   = request.form.get('email')
        member.phone   = request.form.get('phone')
        pid = request.form.get('plan_id')
        member.plan_id = int(pid) if pid else None
        member.goal    = request.form.get('goal')
        member.status  = request.form.get('status')
        member.age_group = request.form.get('age_group', 'Adult')
        dob_str = request.form.get('date_of_birth')
        member.date_of_birth = datetime.strptime(dob_str, '%Y-%m-%d').date() if dob_str else None
        member.emergency_contact_name  = request.form.get('emergency_contact_name')
        member.emergency_contact_phone = request.form.get('emergency_contact_phone')
        member.mpesa_phone    = request.form.get('mpesa_phone')
        member.medical_notes  = request.form.get('medical_notes')
        db.session.commit()
        flash('Member updated!', 'success')
        return redirect(url_for('members'))
    return render_template('edit_member.html', member=member, plans=MembershipPlan.query.all())

@app.route('/members/delete/<int:id>')
@login_required
@admin_required
def delete_member(id):
    member = Member.query.get_or_404(id)
    user   = User.query.get(member.user_id)
    db.session.delete(member)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash('Member deleted.', 'success')
    return redirect(url_for('members'))

# ═══════════════════════════════════════════════════════════════════════════════
#  TRAINERS (Admin)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/trainers')
@login_required
@admin_required
def trainers():
    return render_template('trainers.html', trainers=Trainer.query.all())

@app.route('/trainers/add', methods=['POST'])
@login_required
@admin_required
def add_trainer():
    name = request.form.get('name'); email = request.form.get('email')
    phone = request.form.get('phone'); specialization = request.form.get('specialization')
    bio = request.form.get('bio'); password = request.form.get('password')
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'danger')
        return redirect(url_for('trainers'))
    hashed   = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user = User(username=name, email=email, password_hash=hashed, role='trainer')
    db.session.add(new_user); db.session.flush()
    db.session.add(Trainer(user_id=new_user.id, name=name, email=email, phone=phone,
                           specialization=specialization, bio=bio))
    db.session.commit()
    flash(f'Trainer {name} added!', 'success')
    return redirect(url_for('trainers'))

@app.route('/trainers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_trainer(id):
    trainer = Trainer.query.get_or_404(id)
    if request.method == 'POST':
        trainer.name = request.form.get('name'); trainer.email = request.form.get('email')
        trainer.phone = request.form.get('phone')
        trainer.specialization = request.form.get('specialization'); trainer.bio = request.form.get('bio')
        db.session.commit(); flash('Trainer updated!', 'success')
        return redirect(url_for('trainers'))
    return render_template('edit_trainer.html', trainer=trainer)

@app.route('/trainers/delete/<int:id>')
@login_required
@admin_required
def delete_trainer(id):
    trainer = Trainer.query.get_or_404(id)
    user = User.query.get(trainer.user_id)
    db.session.delete(trainer)
    if user: db.session.delete(user)
    db.session.commit(); flash('Trainer deleted.', 'success')
    return redirect(url_for('trainers'))

@app.route('/trainer-profiles')
@login_required
def trainer_profiles():
    trainers = Trainer.query.all()
    # Get member for rating check
    member = None
    if current_user.role == 'member':
        member = Member.query.filter_by(user_id=current_user.id).first()
    return render_template('trainer_profiles.html', trainers=trainers, member=member)

# Trainer rating by member
@app.route('/trainers/rate/<int:trainer_id>', methods=['POST'])
@login_required
@member_required
@subscription_required
def rate_trainer(trainer_id):
    member  = Member.query.filter_by(user_id=current_user.id).first()
    rating  = int(request.form.get('rating', 5))
    review  = request.form.get('review', '')
    existing = TrainerRating.query.filter_by(trainer_id=trainer_id, member_id=member.id).first()
    if existing:
        existing.rating = rating; existing.review = review
        flash('Rating updated!', 'success')
    else:
        db.session.add(TrainerRating(trainer_id=trainer_id, member_id=member.id, rating=rating, review=review))
        flash('Trainer rated! Thank you.', 'success')
    db.session.commit()
    return redirect(url_for('trainer_profiles'))

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKOUTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/workouts')
@login_required
@trainer_required
def workouts():
    all_workouts = Workout.query.filter_by(is_personal=False).all()
    return render_template('workouts.html', workouts=all_workouts,
                           trainers=Trainer.query.all(), members=Member.query.all())

@app.route('/workouts/add', methods=['POST'])
@login_required
@trainer_required
def add_workout():
    tid = request.form.get('trainer_id')
    db.session.add(Workout(
        name=request.form.get('name'), type=request.form.get('type'),
        duration=int(request.form.get('duration', 0)), difficulty=request.form.get('difficulty'),
        trainer_id=int(tid) if tid else None, description=request.form.get('description'),
        is_personal=False
    ))
    db.session.commit(); flash('Workout added!', 'success')
    return redirect(url_for('workouts'))

@app.route('/workouts/assign', methods=['POST'])
@login_required
@trainer_required
def assign_workout():
    mid = int(request.form.get('member_id')); wid = int(request.form.get('workout_id'))
    if MemberWorkout.query.filter_by(member_id=mid, workout_id=wid).first():
        flash('Already assigned.', 'warning')
    else:
        db.session.add(MemberWorkout(member_id=mid, workout_id=wid)); db.session.commit()
        flash('Workout assigned!', 'success')
    return redirect(url_for('workouts'))

@app.route('/workouts/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@trainer_required
def edit_workout(id):
    workout = Workout.query.get_or_404(id)
    if request.method == 'POST':
        workout.name = request.form.get('name'); workout.type = request.form.get('type')
        workout.duration = int(request.form.get('duration')); workout.difficulty = request.form.get('difficulty')
        tid = request.form.get('trainer_id'); workout.trainer_id = int(tid) if tid else None
        workout.description = request.form.get('description')
        db.session.commit(); flash('Workout updated!', 'success')
        return redirect(url_for('workouts'))
    return render_template('edit_workout.html', workout=workout, trainers=Trainer.query.all())

@app.route('/workouts/delete/<int:id>')
@login_required
@trainer_required
def delete_workout(id):
    db.session.delete(Workout.query.get_or_404(id)); db.session.commit()
    flash('Workout deleted.', 'success'); return redirect(url_for('workouts'))

@app.route('/workouts/<int:workout_id>/comment', methods=['POST'])
@login_required
@trainer_required
def comment_workout(workout_id):
    Workout.query.get_or_404(workout_id)
    trainer = Trainer.query.filter_by(user_id=current_user.id).first()
    text = request.form.get('comment', '').strip()
    if trainer and text:
        db.session.add(WorkoutComment(workout_id=workout_id, trainer_id=trainer.id, comment=text))
        db.session.commit(); flash('Comment added!', 'success')
    return redirect(url_for('workouts'))

# ─── Member workouts ──────────────────────────────────────────────────────────

@app.route('/my-workouts')
@login_required
@member_required
@subscription_required
def my_workouts():
    member = Member.query.filter_by(user_id=current_user.id).first()
    personal_workouts = Workout.query.filter_by(member_id=member.id, is_personal=True).all()
    assigned_workouts = MemberWorkout.query.filter_by(member_id=member.id).all()
    workout_logs      = WorkoutLog.query.filter_by(member_id=member.id).order_by(WorkoutLog.completed_at.desc()).limit(10).all()
    all_workouts      = [mw.workout for mw in assigned_workouts] + personal_workouts
    return render_template('my_workouts.html', member=member,
                           personal_workouts=personal_workouts,
                           assigned_workouts=assigned_workouts,
                           workout_logs=workout_logs,
                           all_workouts=all_workouts)

@app.route('/my-workouts/add', methods=['POST'])
@login_required
@member_required
@subscription_required
def add_my_workout():
    member = Member.query.filter_by(user_id=current_user.id).first()
    db.session.add(Workout(
        name=request.form.get('name'), type=request.form.get('type'),
        duration=int(request.form.get('duration', 0)), difficulty=request.form.get('difficulty'),
        description=request.form.get('description'), member_id=member.id, is_personal=True
    ))
    db.session.commit(); flash('Workout added!', 'success')
    return redirect(url_for('my_workouts'))

@app.route('/my-workouts/delete/<int:id>')
@login_required
@member_required
@subscription_required
def delete_my_workout(id):
    member  = Member.query.filter_by(user_id=current_user.id).first()
    workout = Workout.query.get_or_404(id)
    if workout.member_id != member.id or not workout.is_personal:
        flash('You can only delete your own workouts.', 'danger')
        return redirect(url_for('my_workouts'))
    db.session.delete(workout); db.session.commit()
    flash('Workout deleted.', 'success'); return redirect(url_for('my_workouts'))

@app.route('/my-workouts/log', methods=['POST'])
@login_required
@member_required
@subscription_required
def log_workout():
    member = Member.query.filter_by(user_id=current_user.id).first()
    wid    = request.form.get('workout_id')
    workout = Workout.query.get(int(wid)) if wid else None
    db.session.add(WorkoutLog(
        member_id=member.id,
        workout_id=int(wid) if wid else None,
        workout_name=workout.name if workout else request.form.get('workout_name', 'Custom'),
        notes=request.form.get('notes'),
        duration_mins=int(request.form.get('duration_mins', 0)),
        calories_burned=int(request.form.get('calories_burned', 0))
    ))
    db.session.commit(); flash('Workout logged!', 'success')
    return redirect(url_for('my_workouts'))

# AI workout suggestion endpoint
@app.route('/my-workouts/ai-suggest')
@login_required
@member_required
@subscription_required
def ai_suggest_workouts():
    member = Member.query.filter_by(user_id=current_user.id).first()
    age    = member.age or 25
    goal   = member.goal or 'General Fitness'
    suggestions = get_ai_workout_suggestion(age, goal, medical_notes=member.medical_notes or '')
    if not suggestions:
        flash('AI suggestions unavailable right now. Please try again later.', 'warning')
        return redirect(url_for('my_workouts'))
    return render_template('ai_workouts.html', member=member, suggestions=suggestions)

@app.route('/my-workouts/ai-save', methods=['POST'])
@login_required
@member_required
@subscription_required
def save_ai_workout():
    member = Member.query.filter_by(user_id=current_user.id).first()
    db.session.add(Workout(
        name=request.form.get('name'), type=request.form.get('type'),
        duration=int(request.form.get('duration', 30)),
        difficulty=request.form.get('difficulty', 'Beginner'),
        description=request.form.get('description'),
        member_id=member.id, is_personal=True, is_ai_suggested=True,
        target_age_group=member.age_group, target_goal=member.goal
    ))
    db.session.commit(); flash('AI workout saved to your plans!', 'success')
    return redirect(url_for('my_workouts'))

# ═══════════════════════════════════════════════════════════════════════════════
#  NUTRITION
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/nutrition')
@login_required
@trainer_required
def nutrition():
    return render_template('nutrition.html', nutrition=Nutrition.query.all(), members=Member.query.all())

@app.route('/nutrition/add', methods=['POST'])
@login_required
@trainer_required
def add_nutrition():
    db.session.add(Nutrition(
        member_id=int(request.form.get('member_id')), meal_plan=request.form.get('meal_plan'),
        calories=int(request.form.get('calories')), protein=int(request.form.get('protein')),
        schedule=request.form.get('schedule')
    )); db.session.commit(); flash('Meal plan added!', 'success')
    return redirect(url_for('nutrition'))

@app.route('/nutrition/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@trainer_required
def edit_nutrition(id):
    plan = Nutrition.query.get_or_404(id)
    if request.method == 'POST':
        plan.member_id=int(request.form.get('member_id')); plan.meal_plan=request.form.get('meal_plan')
        plan.calories=int(request.form.get('calories')); plan.protein=int(request.form.get('protein'))
        plan.schedule=request.form.get('schedule'); db.session.commit(); flash('Updated!', 'success')
        return redirect(url_for('nutrition'))
    return render_template('edit_nutrition.html', plan=plan, members=Member.query.all())

@app.route('/nutrition/delete/<int:id>')
@login_required
@trainer_required
def delete_nutrition(id):
    db.session.delete(Nutrition.query.get_or_404(id)); db.session.commit()
    flash('Deleted.', 'success'); return redirect(url_for('nutrition'))

@app.route('/nutrition/<int:nutrition_id>/comment', methods=['POST'])
@login_required
@trainer_required
def comment_nutrition(nutrition_id):
    Nutrition.query.get_or_404(nutrition_id)
    trainer = Trainer.query.filter_by(user_id=current_user.id).first()
    text = request.form.get('comment', '').strip()
    if trainer and text:
        db.session.add(NutritionComment(nutrition_id=nutrition_id, trainer_id=trainer.id, comment=text))
        db.session.commit(); flash('Comment added!', 'success')
    return redirect(url_for('nutrition'))

@app.route('/my-nutrition')
@login_required
@member_required
@subscription_required
def my_nutrition():
    member = Member.query.filter_by(user_id=current_user.id).first()
    plans  = Nutrition.query.filter_by(member_id=member.id).order_by(Nutrition.date.desc()).all()
    return render_template('my_nutrition.html', member=member, plans=plans)

# ═══════════════════════════════════════════════════════════════════════════════
#  ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/attendance')
@login_required
@trainer_required
def attendance():
    today = date.today()
    return render_template('attendance.html',
                           attendance=Attendance.query.order_by(Attendance.check_in.desc()).all(),
                           members=Member.query.all(),
                           today_count=Attendance.query.filter_by(date=today).count(),
                           checked_in_count=Attendance.query.filter_by(date=today, check_out=None).count(),
                           total_count=Attendance.query.count())

@app.route('/attendance/checkin', methods=['POST'])
@login_required
@trainer_required
def check_in():
    mid   = int(request.form.get('member_id')); today = date.today()
    if Attendance.query.filter_by(member_id=mid, date=today, check_out=None).first():
        flash('Already checked in today!', 'warning')
    else:
        db.session.add(Attendance(member_id=mid, check_in=datetime.now(), date=today))
        db.session.commit(); flash('Member checked in!', 'success')
    return redirect(url_for('attendance'))

@app.route('/attendance/checkout/<int:id>')
@login_required
@trainer_required
def check_out(id):
    record = Attendance.query.get_or_404(id)
    record.check_out = datetime.now(); db.session.commit()
    flash('Checked out!', 'success'); return redirect(url_for('attendance'))

@app.route('/attendance/delete/<int:id>')
@login_required
@trainer_required
def delete_attendance(id):
    db.session.delete(Attendance.query.get_or_404(id)); db.session.commit()
    flash('Record deleted.', 'success'); return redirect(url_for('attendance'))

@app.route('/my-attendance')
@login_required
@member_required
@subscription_required
def my_attendance():
    member  = Member.query.filter_by(user_id=current_user.id).first()
    records = Attendance.query.filter_by(member_id=member.id).order_by(Attendance.check_in.desc()).all()
    return render_template('my_attendance.html', member=member, records=records, total=len(records), today=date.today())

# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENTS — ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/payments')
@login_required
@admin_required
def payments():
    from calendar import monthrange
    all_payments  = Payment.query.order_by(Payment.payment_date.desc()).all()
    today         = date.today()
    month_end     = date(today.year, today.month, monthrange(today.year, today.month)[1])
    expiring_count = Payment.query.filter(
        Payment.expiry_date <= month_end, Payment.expiry_date >= today
    ).count()
    return render_template('payments.html',
                           payments=all_payments, members=Member.query.all(),
                           plans=MembershipPlan.query.all(),
                           total_revenue=db.session.query(db.func.sum(Payment.amount)).scalar() or 0,
                           total_payments=Payment.query.count(),
                           expiring_count=expiring_count)

@app.route('/payment/add', methods=['POST'])
@login_required
@admin_required
def add_payment():
    expiry = request.form.get('expiry_date')
    plan_id = request.form.get('plan_id')
    member_id = int(request.form.get('member_id'))
    inv_no = f'INV-{datetime.now().strftime("%Y%m%d%H%M%S")}'
    db.session.add(Payment(
        member_id=member_id, amount=float(request.form.get('amount')),
        method=request.form.get('method'),
        expiry_date=datetime.strptime(expiry, '%Y-%m-%d').date() if expiry else None,
        plan_id=int(plan_id) if plan_id else None,
        invoice_number=inv_no, status='completed'
    ))
    db.session.commit()
    # Send receipt if member has email
    new_pay = Payment.query.order_by(Payment.id.desc()).first()
    if new_pay:
        pay_member = Member.query.get(member_id)
        pay_plan   = MembershipPlan.query.get(int(plan_id)) if plan_id else None
        if pay_member and pay_plan and pay_member.email:
            send_subscription_receipt(pay_member, new_pay, pay_plan)
    flash('Payment recorded! Receipt sent to member.', 'success')
    return redirect(url_for('payments'))

@app.route('/payments/delete/<int:id>')
@login_required
@admin_required
def delete_payment(id):
    db.session.delete(Payment.query.get_or_404(id)); db.session.commit()
    flash('Payment deleted.', 'success'); return redirect(url_for('payments'))

# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENTS — MEMBER: M-PESA + STRIPE
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/my-payments')
@login_required
@member_required
def my_payments():
    member = Member.query.filter_by(user_id=current_user.id).first()
    my_pay  = Payment.query.filter_by(member_id=member.id).order_by(Payment.payment_date.desc()).all()
    plans   = MembershipPlan.query.all()
    latest  = Payment.query.filter_by(member_id=member.id).order_by(Payment.expiry_date.desc()).first()
    today   = date.today()
    pending_mpesa = MpesaTransaction.query.filter_by(member_id=member.id, status='pending').order_by(MpesaTransaction.created_at.desc()).first()
    return render_template('my_payments.html', member=member, payments=my_pay,
                           plans=plans, latest_payment=latest, today=today,
                           pending_mpesa=pending_mpesa,
                           stripe_public_key=app.config.get('STRIPE_PUBLIC_KEY', ''))

# ── Stripe Checkout ───────────────────────────────────────────────────────────
@app.route('/payment/stripe/<int:plan_id>', methods=['POST'])
@login_required
def stripe_payment(plan_id):
    plan   = MembershipPlan.query.get_or_404(plan_id)
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'kes',
                    'product_data': {
                        'name': f'{plan.name} Membership — {member.name}',
                        'description': f'Gym membership for {member.name} (ID #{member.id})'
                    },
                    'unit_amount': int(plan.price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            customer_email=member.email,
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('my_payments', _external=True),
            metadata={'plan_id': str(plan_id), 'member_id': str(member.id), 'member_name': member.name}
        )
        return redirect(session.url)
    except Exception as e:
        flash(f'Payment error: {str(e)}', 'danger')
        return redirect(url_for('my_payments'))

@app.route('/payment/success')
@login_required
def payment_success():
    flash('Payment successful! Your membership is now active.', 'success')
    return redirect(url_for('member_dashboard'))

@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    payload    = request.get_data(as_text=True)
    sig_header = request.headers.get('stripe-signature')
    secret     = os.getenv('STRIPE_WEBHOOK_SECRET')
    if not secret:
        return 'No webhook secret', 400
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception:
        return 'Invalid', 400
    if event['type'] == 'checkout.session.completed':
        s = event['data']['object']
        try:
            member_id = int(s['metadata']['member_id'])
            plan_id   = int(s['metadata']['plan_id'])
            member    = Member.query.get(member_id)
            plan      = MembershipPlan.query.get(plan_id)
            if member and plan:
                expiry = datetime.utcnow().date() + timedelta(days=plan.duration_days)
                inv_no = f'INV-STRIPE-{s.get("id","")[-8:]}'
                db.session.add(Payment(
                    member_id=member_id, amount=plan.price, method='stripe',
                    expiry_date=expiry, transaction_id=s.get('id'),
                    plan_id=plan_id, invoice_number=inv_no,
                    status='completed', external_status='completed'
                ))
                member.plan_id = plan_id; member.status = 'active'
                db.session.commit()
                # Send receipt email
                new_pay = Payment.query.filter_by(invoice_number=inv_no).first()
                if new_pay and member.email:
                    send_subscription_receipt(member, new_pay, plan)
        except Exception as e:
            print(f'Stripe webhook error: {e}')
    return '', 200

# ── M-Pesa STK Push ───────────────────────────────────────────────────────────
@app.route('/payment/mpesa/<int:plan_id>', methods=['POST'])
@login_required
@member_required
def mpesa_payment(plan_id):
    plan   = MembershipPlan.query.get_or_404(plan_id)
    member = Member.query.filter_by(user_id=current_user.id).first()
    phone  = request.form.get('phone') or member.mpesa_phone or member.phone
    if not phone:
        flash('Please provide your M-Pesa phone number.', 'danger')
        return redirect(url_for('my_payments'))

    account_ref = f'GYM{member.id:04d}'
    desc        = f'{plan.name} Membership - {member.name}'

    checkout_id, merchant_id = stk_push(phone, int(plan.price), account_ref, desc)
    if not checkout_id:
        flash(f'M-Pesa request failed: {merchant_id}', 'danger')
        return redirect(url_for('my_payments'))

    # Save pending transaction
    db.session.add(MpesaTransaction(
        member_id=member.id, plan_id=plan_id, phone_number=phone,
        amount=plan.price, checkout_request_id=checkout_id,
        merchant_request_id=merchant_id, status='pending'
    ))
    # Update member's mpesa_phone if not set
    if not member.mpesa_phone:
        member.mpesa_phone = phone
    db.session.commit()

    flash(f'M-Pesa STK Push sent to {phone}. Enter your PIN on your phone to complete payment.', 'success')
    return redirect(url_for('my_payments'))

@app.route('/webhook/mpesa', methods=['POST'])
def mpesa_webhook():
    """Daraja callback URL for STK Push result."""
    try:
        data = request.get_json(force=True)
        result = data.get('Body', {}).get('stkCallback', {})
        checkout_id = result.get('CheckoutRequestID')
        result_code = result.get('ResultCode')

        txn = MpesaTransaction.query.filter_by(checkout_request_id=checkout_id).first()
        if not txn:
            return jsonify({'ResultCode': 0}), 200

        if result_code == 0:
            # Success — extract receipt
            items = result.get('CallbackMetadata', {}).get('Item', [])
            receipt = next((i['Value'] for i in items if i['Name'] == 'MpesaReceiptNumber'), None)
            amount  = next((i['Value'] for i in items if i['Name'] == 'Amount'), txn.amount)

            txn.status = 'completed'; txn.mpesa_receipt = receipt
            plan   = MembershipPlan.query.get(txn.plan_id)
            member = Member.query.get(txn.member_id)
            if plan and member:
                expiry = datetime.utcnow().date() + timedelta(days=plan.duration_days)
                inv_no = f'INV-MPESA-{receipt or checkout_id[-8:]}'
                db.session.add(Payment(
                    member_id=member.id, amount=amount, method='mpesa',
                    expiry_date=expiry, transaction_id=checkout_id,
                    mpesa_receipt=receipt, plan_id=txn.plan_id,
                    invoice_number=inv_no, status='completed', external_status='completed'
                ))
                member.plan_id = txn.plan_id; member.status = 'active'
                db.session.flush()
                new_pay = Payment.query.filter_by(invoice_number=inv_no).first()
                if new_pay and member.email:
                    send_subscription_receipt(member, new_pay, plan)
        else:
            txn.status = 'failed'

        db.session.commit()
    except Exception as e:
        print(f'M-Pesa webhook error: {e}')
    return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'}), 200

# M-Pesa status check (AJAX polling)
@app.route('/payment/mpesa/status/<checkout_id>')
@login_required
def mpesa_status(checkout_id):
    txn = MpesaTransaction.query.filter_by(checkout_request_id=checkout_id).first()
    if not txn:
        return jsonify({'status': 'not_found'})
    return jsonify({'status': txn.status, 'receipt': txn.mpesa_receipt})

# ── Invoice Download ──────────────────────────────────────────────────────────
@app.route('/payment/invoice/<int:payment_id>')
@login_required
def download_invoice(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    # Only admin or the paying member can download
    if current_user.role == 'member':
        member = Member.query.filter_by(user_id=current_user.id).first()
        if not member or payment.member_id != member.id:
            abort(403)
    member  = Member.query.get(payment.member_id)
    buf     = generate_invoice_pdf(payment, member)
    inv_no  = payment.invoice_number or f'INV-{payment.id:05d}'
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=f'{inv_no}.pdf')

# ═══════════════════════════════════════════════════════════════════════════════
#  PROGRESS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/progress')
@login_required
@trainer_required
def progress():
    return render_template('progress.html',
                           progress=Progress.query.order_by(Progress.date.desc()).all(),
                           members=Member.query.all())

@app.route('/progress/add', methods=['POST'])
@login_required
@trainer_required
def add_progress():
    weight = request.form.get('weight'); height = request.form.get('height')
    bmi = round(float(weight) / ((float(height)/100)**2), 2) if weight and height else None
    db.session.add(Progress(
        member_id=int(request.form.get('member_id')), weight=float(weight), bmi=bmi,
        strength_score=float(request.form.get('strength_score')) if request.form.get('strength_score') else None
    )); db.session.commit(); flash('Progress added!', 'success')
    return redirect(url_for('progress'))

@app.route('/progress/delete/<int:id>')
@login_required
@trainer_required
def delete_progress(id):
    db.session.delete(Progress.query.get_or_404(id)); db.session.commit()
    flash('Deleted.', 'success'); return redirect(url_for('progress'))

@app.route('/my-progress')
@login_required
@member_required
@subscription_required
def my_progress():
    member = Member.query.filter_by(user_id=current_user.id).first()
    records      = Progress.query.filter_by(member_id=member.id).order_by(Progress.date.desc()).all()
    measurements = BodyMeasurement.query.filter_by(member_id=member.id).order_by(BodyMeasurement.date.desc()).all()
    return render_template('my_progress.html', member=member, records=records, measurements=measurements)

@app.route('/my-progress/add', methods=['POST'])
@login_required
@member_required
@subscription_required
def add_my_progress():
    member = Member.query.filter_by(user_id=current_user.id).first()
    weight = request.form.get('weight'); height = request.form.get('height')
    bmi = round(float(weight) / ((float(height)/100)**2), 2) if weight and height else None
    db.session.add(Progress(
        member_id=member.id, weight=float(weight), bmi=bmi,
        strength_score=float(request.form.get('strength_score')) if request.form.get('strength_score') else None
    )); db.session.commit(); flash('Progress recorded!', 'success')
    return redirect(url_for('my_progress'))

# Body measurements
@app.route('/my-measurements/add', methods=['POST'])
@login_required
@member_required
@subscription_required
def add_measurement():
    member = Member.query.filter_by(user_id=current_user.id).first()
    db.session.add(BodyMeasurement(
        member_id=member.id,
        chest=float(request.form.get('chest')) if request.form.get('chest') else None,
        waist=float(request.form.get('waist')) if request.form.get('waist') else None,
        hips=float(request.form.get('hips')) if request.form.get('hips') else None,
        thighs=float(request.form.get('thighs')) if request.form.get('thighs') else None,
        arms=float(request.form.get('arms')) if request.form.get('arms') else None,
        body_fat_pct=float(request.form.get('body_fat_pct')) if request.form.get('body_fat_pct') else None,
    )); db.session.commit(); flash('Measurements saved!', 'success')
    return redirect(url_for('my_progress'))

# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/classes')
@login_required
@trainer_required
def classes():
    return render_template('classes.html', classes=Class.query.all(), trainers=Trainer.query.all())

@app.route('/classes/add', methods=['POST'])
@login_required
@trainer_required
def add_class():
    db.session.add(Class(
        name=request.form.get('name'), trainer_id=int(request.form.get('trainer_id')),
        schedule=datetime.strptime(request.form.get('schedule'), '%Y-%m-%dT%H:%M'),
        capacity=int(request.form.get('capacity', 20)), description=request.form.get('description')
    )); db.session.commit(); flash('Class added!', 'success')
    return redirect(url_for('classes'))

@app.route('/classes/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@trainer_required
def edit_class(id):
    gym_class = Class.query.get_or_404(id)
    if request.method == 'POST':
        gym_class.name = request.form.get('name')
        gym_class.trainer_id = int(request.form.get('trainer_id'))
        gym_class.schedule = datetime.strptime(request.form.get('schedule'), '%Y-%m-%dT%H:%M')
        gym_class.capacity = int(request.form.get('capacity'))
        gym_class.description = request.form.get('description')
        db.session.commit(); flash('Class updated!', 'success')
        return redirect(url_for('classes'))
    return render_template('edit_class.html', gym_class=gym_class, trainers=Trainer.query.all())

@app.route('/classes/delete/<int:id>')
@login_required
@trainer_required
def delete_class(id):
    db.session.delete(Class.query.get_or_404(id)); db.session.commit()
    flash('Class deleted.', 'success'); return redirect(url_for('classes'))

@app.route('/member/book-class/<int:class_id>')
@login_required
@member_required
@subscription_required
def book_class(class_id):
    member    = Member.query.filter_by(user_id=current_user.id).first()
    gym_class = Class.query.get_or_404(class_id)
    if ClassBooking.query.filter_by(member_id=member.id, class_id=class_id).first():
        flash('You already booked this class!', 'warning')
    elif len(gym_class.bookings) >= gym_class.capacity:
        flash('This class is full!', 'danger')
    else:
        db.session.add(ClassBooking(member_id=member.id, class_id=class_id))
        db.session.commit(); flash(f'Booked {gym_class.name}!', 'success')
    return redirect(url_for('member_dashboard'))

@app.route('/member/cancel-booking/<int:booking_id>')
@login_required
@member_required
@subscription_required
def cancel_booking(booking_id):
    db.session.delete(ClassBooking.query.get_or_404(booking_id)); db.session.commit()
    flash('Booking cancelled.', 'success'); return redirect(url_for('member_dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
#  REPORTS (Admin)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/reports')
@login_required
@admin_required
def reports():
    return render_template('reports.html',
        total_members=Member.query.count(),
        total_trainers=Trainer.query.count(),
        total_attendance=Attendance.query.count(),
        total_revenue=db.session.query(db.func.sum(Payment.amount)).scalar() or 0,
        recent_payments=Payment.query.order_by(Payment.payment_date.desc()).limit(10).all(),
        cash_revenue=db.session.query(db.func.sum(Payment.amount)).filter_by(method='cash').scalar() or 0,
        mpesa_revenue=db.session.query(db.func.sum(Payment.amount)).filter_by(method='mpesa').scalar() or 0,
        card_revenue=(db.session.query(db.func.sum(Payment.amount)).filter_by(method='card').scalar() or 0) +
                     (db.session.query(db.func.sum(Payment.amount)).filter_by(method='stripe').scalar() or 0),
        monthly_count=Member.query.join(MembershipPlan).filter(MembershipPlan.name=='Monthly').count(),
        quarterly_count=Member.query.join(MembershipPlan).filter(MembershipPlan.name=='Quarterly').count(),
        annual_count=Member.query.join(MembershipPlan).filter(MembershipPlan.name=='Annual').count()
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

@app.route('/settings/change-password', methods=['POST'])
@login_required
def change_password():
    current_pw = request.form.get('current_password')
    new_pw     = request.form.get('new_password')
    confirm_pw = request.form.get('confirm_password')
    if not bcrypt.check_password_hash(current_user.password_hash, current_pw):
        flash('Current password is incorrect.', 'danger'); return redirect(url_for('settings'))
    if new_pw != confirm_pw:
        flash('Passwords do not match.', 'danger'); return redirect(url_for('settings'))
    current_user.password_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
    db.session.commit(); flash('Password updated!', 'success')
    return redirect(url_for('settings'))

# ═══════════════════════════════════════════════════════════════════════════════
#  ERROR HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('403.html'), 403

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)