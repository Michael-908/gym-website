from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from models import db, User, Member, Trainer, Workout, Nutrition, Attendance, Payment, Progress, Class, ClassBooking, MembershipPlan
from dotenv import load_dotenv
import os
import stripe
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

load_dotenv()

#Configuration
app.config['SECRET_KEY'] = 'gymapp-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gym.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_DURATION'] = 3600

# Payment Configuration
# Force the app to use the environment variable only
app.config['STRIPE_PUBLIC_KEY'] = os.getenv('STRIPE_PUBLIC_KEY')
app.config['STRIPE_SECRET_KEY'] = os.getenv('STRIPE_SECRET_KEY')

# Verify the key is loaded before proceeding
if not app.config['STRIPE_SECRET_KEY'] or 'your_stripe' in app.config['STRIPE_SECRET_KEY']:
    raise RuntimeError("Stripe Secret Key not found in environment. Check your .env file.")

stripe.api_key = app.config['STRIPE_SECRET_KEY']

app.config['MPESA_CONSUMER_KEY'] = os.getenv('MPESA_CONSUMER_KEY')
app.config['MPESA_CONSUMER_SECRET'] = os.getenv('MPESA_CONSUMER_SECRET')
app.config['MPESA_SHORTCODE'] = os.getenv('MPESA_SHORTCODE')
app.config['MPESA_PASSKEY'] = os.getenv('MPESA_PASSKEY')

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

#Extensions
db.init_app(app)
bcrypt = Bcrypt(app)
mail = Mail(app)

# Initialize database and seed data
with app.app_context():
    db.create_all()
    # Seed membership plans if not exist
    if not MembershipPlan.query.first():
        plans = [
            MembershipPlan(name='Monthly', price=30.0, duration_days=30),
            MembershipPlan(name='Quarterly', price=80.0, duration_days=90),
            MembershipPlan(name='Annual', price=250.0, duration_days=365)
        ]
        db.session.add_all(plans)
        db.session.commit()
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to acces this page'
login_manager.login_message_category = 'info'
login_manager.session_protection = 'basic'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

#Authentication routes
@app.route('/', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password. Please try again.', 'danger')
            return render_template('login.html')
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
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'member')

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash('Email already registered. Please login.', 'warning')
            return redirect(url_for('login'))
        
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(username=username, email=email, password_hash=hashed_pw, role=role)
        db.session.add(new_user)
        db.session.flush()
        if role == 'member':
            new_member = Member(user_id=new_user.id, name=username, email=email)
            db.session.add(new_member)
        db.session.commit()
        flash('Account created successfully! You can now login in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/forgot-password')
def forgot_password():
    return render_template('forgot-password.html')

# Main Routes
@app.route('/dashboard')
@login_required
def dashboard():
    total_members = Member.query.count()
    active_members = Member.query.filter_by(status='active').count()
    total_trainers = Trainer.query.count()
    total_payments = db.session.query(db.func.sum(Payment.amount)).scalar() or 0
    return render_template('index.html',
                           total_members=total_members,
                           active_members=active_members,total_trainers=total_trainers,total_payments=total_payments)


@app.route('/members')
@login_required
def members():
    all_members = Member.query.all()
    return render_template('members.html', members=all_members, plans=MembershipPlan.query.all())

@app.route('/members/add', methods=['POST'])
@login_required
def add_member():
    name = request.form.get('name')
    email = request.form.get('email')
    phone = request.form.get('phone')
    plan_id = request.form.get('plan_id')
    goal = request.form.get('goal')
    status = request.form.get('status')
    password = request.form.get('password')

    existing = User.query.filter_by(email=email).first()
    if existing:
      flash('A user with this email already exists.', 'danger')
      return redirect(url_for('members'))

    hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user  = User(username=name, email=email, password_hash=hashed_pw, role='member')
    db.session.add(new_user)
    db.session.flush()

    # Create member profile
    new_member = Member(
        user_id = new_user.id,
        name    = name,
        email   = email,
        phone   = phone,
        plan_id = int(plan_id) if plan_id else None,
        goal = goal,
        status = status
    )
    db.session.add(new_member)
    db.session.commit()
    flash(f'Member {name} added successfully', 'success')
    return redirect(url_for('members'))

@app.route('/members/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_member(id):
    member = Member.query.get_or_404(id)
    if request.method == 'POST':
        member.name   = request.form.get('name')
        member.email  = request.form.get('email')
        member.phone  = request.form.get('phone')
        plan_id       = request.form.get('plan_id')
        member.plan_id = int(plan_id) if plan_id else None
        member.goal   = request.form.get('goal')
        member.status = request.form.get('status')
        db.session.commit()
        flash('Member updated successfully!', 'success')
        return redirect(url_for('members'))
    return render_template('edit_member.html', member=member, plans=MembershipPlan.query.all())

@app.route('/members/delete/<int:id>')
@login_required
def delete_member(id):
    member = Member.query.get_or_404(id)
    user   = User.query.get(member.user_id)
    db.session.delete(member)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash('Member deleted successfully!', 'success')
    return redirect(url_for('members'))


# Trainers page
@app.route('/trainers')
@login_required
def trainers():
    all_trainers = Trainer.query.all()
    return render_template('trainers.html', trainers=all_trainers)

@app.route('/trainers/add', methods=['POST'])
@login_required
def add_trainer():
    name = request.form.get('name')
    email = request.form.get('email')
    phone = request.form.get('phone')
    specialization = request.form.get('specialization')
    bio = request.form.get('bio')
    password = request.form.get('password')

    existing = User.query.filter_by(email=email).first()
    if existing:
        flash('A user with this email already exists.', 'danger')
        return redirect(url_for('trainers'))
    
    hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user  = User(username=name, email=email, password_hash=hashed_pw, role='trainer')
    db.session.add(new_user)
    db.session.flush()

    new_trainer = Trainer(
        user_id        = new_user.id,
        name           = name,
        email          = email,
        phone          = phone,
        specialization = specialization,
        bio            = bio
    )
    db.session.add(new_trainer)
    db.session.commit()
    flash(f'Trainer {name} added successfully!', 'success')
    return redirect(url_for('trainers'))

@app.route('/trainers/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_trainer(id):
    trainer = Trainer.query.get_or_404(id)
    if request.method == 'POST':
        trainer.name = request.form.get('name')
        trainer.email = request.form.get('email')
        trainer.phone = request.form.get('phone')
        trainer.specialization = request.form.get('specialization')
        trainer.bio = request.form.get('bio')
        db.session.commit()
        flash('Trainer updated successfully!', 'success')
        return redirect(url_for('trainers'))
    return render_template('edit_trainer.html', trainer=trainer)

@app.route('/trainers/delete/<int:id>')
@login_required
def delete_trainer(id):
    trainer = Trainer.query.get_or_404(id)
    user    = User.query.get(trainer.user_id)
    db.session.delete(trainer)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash('Trainer deleted successfully!', 'success')
    return redirect(url_for('trainers'))

# Workouts page
@app.route('/workouts')
@login_required
def workouts():
    all_workouts = Workout.query.all()
    all_trainers = Trainer.query.all()
    return render_template('workouts.html', workouts=all_workouts, trainers=all_trainers)

@app.route('/workouts/add', methods=['POST'])
@login_required
def add_workout():
    name = request.form.get('name')
    type = request.form.get('type')
    duration = request.form.get('duration')
    difficulty = request.form.get('difficulty')
    trainer_id = request.form.get('trainer_id')
    description = request.form.get('description')

    new_workout = Workout(
        name = name,
        type = type,
        duration = int(duration),
        difficulty = difficulty,
        trainer_id = int(trainer_id),
        description = description
    )
    db.session.add(new_workout)
    db.session.commit()
    flash(f'Workout {name} added successfully!', 'success')
    return redirect(url_for('workouts'))

@app.route('/workouts/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_workout(id):
    workout = Workout.query.get_or_404(id)
    all_trainers = Trainer.query.all()
    if request.method == 'POST':
        workout.name        = request.form.get('name')
        workout.type        = request.form.get('type')
        workout.duration    = int(request.form.get('duration'))
        workout.difficulty  = request.form.get('difficulty')
        workout.trainer_id  = int(request.form.get('trainer_id'))
        workout.description = request.form.get('description')
        db.session.commit()
        flash('Workout updated successfully!', 'success')
        return redirect(url_for('workouts'))
    return render_template('edit_workout.html', workout=workout, trainers=all_trainers)

@app.route('/workouts/delete/<int:id>')
@login_required
def delete_workout(id):
    workout = Workout.query.get_or_404(id)
    db.session.delete(workout)
    db.session.commit()
    flash('Workout deleted successfully!', 'success')
    return redirect(url_for('workouts'))

# Nutrition page
@app.route('/nutrition')
@login_required
def nutrition():
    all_nutrition = Nutrition.query.all()
    all_members = Member.query.all()
    return render_template('nutrition.html', nutrition=all_nutrition, members=all_members)

@app.route('/nutrition/add', methods=['POST'])
@login_required
def add_nutrition():
    member_id = request.form.get('member_id')
    meal_plan = request.form.get('meal_plan')
    calories = request.form.get('calories')
    protein = request.form.get('protein')
    schedule = request.form.get('schedule')

    new_plan = Nutrition(
        member_id = int(member_id),
        meal_plan = meal_plan,
        calories = int(calories),
        protein = int(protein),
        schedule = schedule
    )
    db.session.add(new_plan)
    db.session.commit()
    flash('Meal plan added successfullly!', 'success')
    return redirect(url_for('nutrition'))

@app.route('/nutrition/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_nutrition(id):
    plan = Nutrition.query.get_or_404(id)
    all_members = Member.query.all()
    if request.method == 'POST':
        plan.member_id = int(request.form.get('member_id'))
        plan.meal_plan = request.form.get('meal_plan')
        plan.calories  = int(request.form.get('calories'))
        plan.protein   = int(request.form.get('protein'))
        plan.schedule  = request.form.get('schedule')
        db.session.commit()
        flash('Meal plan updated successfully!', 'success')
        return redirect(url_for('nutrition'))
    return render_template('edit_nutrition.html', plan=plan, members=all_members)

@app.route('/nutrition/delete/<int:id>')
@login_required
def delete_nutrition(id):
    plan = Nutrition.query.get_or_404(id)
    db.session.delete(plan)
    db.session.commit()
    flash('Meal plan deleted successfully!', 'success')
    return redirect(url_for('nutrition'))


# Attendance page
@app.route('/attendance')
@login_required
def attendance():
    from datetime import date
    all_attendance = Attendance.query.order_by(Attendance.check_in.desc()).all()
    all_members = Member.query.all()
    today = date.today()
    today_count = Attendance.query.filter_by(date=today).count()
    checked_in_count = Attendance.query.filter_by(date=today, check_out=None).count()
    total_count = Attendance.query.count()
    return render_template('attendance.html', attendance=all_attendance,
                           members=all_members,
                           today_count=today_count,
                           checked_in_count=checked_in_count,
                           total_count=total_count)

@app.route('/attendance/checkin', methods=['POST'])
@login_required
def check_in():
    from datetime import datetime, date
    member_id = request.form.get('member_id')

    # Check if member is already checked in today
    today           = date.today()
    existing        = Attendance.query.filter_by(
                        member_id=int(member_id),
                        date=today,
                        check_out=None).first()
    if existing:
        flash('Member is already checked in today!', 'warning')
        return redirect(url_for('attendance'))

    new_record = Attendance(
        member_id = int(member_id),
        check_in  = datetime.now(),
        date      = today
    )
    db.session.add(new_record)
    db.session.commit()
    flash('Member checked in successfully!', 'success')
    return redirect(url_for('attendance'))

@app.route('/attendance/checkout/<int:id>')
@login_required
def check_out(id):
    from datetime import datetime
    record = Attendance.query.get_or_404(id)
    record.check_out = datetime.now()
    db.session.commit()
    flash('Member checked out successfully!', 'success')
    return redirect(url_for('attendance'))

@app.route('/attendance/delete/<int:id>')
@login_required
def delete_attendance(id):
    record = Attendance.query.get_or_404(id)
    db.session.delete(record)
    db.session.commit()
    flash('Attendance record deleted successfully!', 'success')
    return redirect(url_for('attendance'))

# Payments page
@app.route('/payments')
@login_required
def payments():
    from datetime import date
    from calendar import monthrange
    all_payments = Payment.query.order_by(Payment.payment_date.desc()).all()
    all_members = Member.query.all()
    total_revenue = db.session.query(db.func.sum(Payment.amount)).scalar() or 0
    total_payments = Payment.query.count()
    today = date.today()
    month_end = date(today.year, today.month,
                     monthrange(today.year, today.month)[1])
    expiring_count = Payment.query.filter(
                     Payment.expiry_date <= month_end,
                     Payment.expiry_date >= today).count()
    
    return render_template('payments.html',
                           payment=all_payments,
                           members=all_members,
                           plans=MembershipPlan.query.all(),
                           total_revenue=total_revenue,
                           total_payments=total_payments,
                           expiring_count=expiring_count)

@app.route('/payment/add', methods=['POST'])
@login_required
def add_payment():
    from datetime import datetime
    member_id   = request.form.get('member_id')
    amount      = request.form.get('amount')
    method      = request.form.get('method')
    expiry_date = request.form.get('expiry_date')

    new_payment = Payment(
        member_id   = int(member_id),
        amount      = float(amount),
        method      = method,
        expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d').date(),
        status      = 'completed'
    )
    db.session.add(new_payment)
    db.session.commit()
    flash('Payment recorded successfully!', 'success')
    return redirect(url_for('payments'))

@app.route('/payments/delete/<int:id>')
@login_required
def delete_payment(id):
    payment = Payment.query.get_or_404(id)
    db.session.delete(payment)
    db.session.commit()
    flash('Payment deleted successfully!', 'success')
    return redirect(url_for('payments'))

# Stripe Payment Route
@app.route('/payment/stripe/<int:plan_id>', methods=['POST'])
@login_required
def stripe_payment(plan_id):
    plan = MembershipPlan.query.get_or_404(plan_id)
    
    # Get the member's profile safely
    member = Member.query.filter_by(user_id=current_user.id).first()
    
    if not member:
        flash('Member profile not found. Please make sure you are logged in as a member.', 'danger')
        return redirect(url_for('dashboard'))
    
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': f'{plan.name} Membership',
                    },
                    'unit_amount': int(plan.price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('payments', _external=True),
            metadata={
                'plan_id': str(plan_id), 
                'member_id': str(member.id)
            }
        )
        return redirect(checkout_session.url)
        
    except Exception as e:
        flash(f'Error creating Stripe session: {str(e)}', 'danger')
        return redirect(url_for('payments'))
    
# ====================== STRIPE PAYMENT SUCCESS ======================
@app.route('/payment/success')
@login_required
def payment_success():
    session_id = request.args.get('session_id')
    if session_id:
        flash('✅ Payment successful! Your membership has been activated.', 'success')
    else:
        flash('Payment completed, but session could not be verified.', 'warning')
    
    return redirect(url_for('dashboard'))

# ====================== STRIPE WEBHOOK ======================
@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('stripe-signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')

    if not endpoint_secret:
        print("Warning: STRIPE_WEBHOOK_SECRET not set in .env")
        return 'Webhook secret not configured', 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError:
        return 'Invalid payload', 400
    except stripe.error.SignatureVerificationError:
        return 'Invalid signature', 400
    except Exception as e:
        print(f"Webhook error: {e}")
        return 'Error', 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        try:
            member_id = int(session['metadata'].get('member_id'))
            plan_id = int(session['metadata'].get('plan_id'))

            member = Member.query.get(member_id)
            plan = MembershipPlan.query.get(plan_id)

            if member and plan:
                expiry_date = datetime.utcnow().date() + timedelta(days=plan.duration_days)

                payment = Payment(
                    member_id=member_id,
                    amount=plan.price,
                    method='stripe',
                    expiry_date=expiry_date,
                    transaction_id=session.get('id'),
                    status='completed',
                    external_status='completed'
                )
                db.session.add(payment)
                
                member.plan_id = plan_id
                member.status = 'active'
                db.session.commit()

                send_payment_email(member.email, plan.name, plan.price)
                print(f"Payment processed for member {member_id}")
                
        except Exception as e:
            print(f"Error processing webhook: {e}")

    return '', 200

# Email function
def send_payment_email(email, plan_name, amount):
    msg = Message('Payment Confirmation', recipients=[email])
    msg.body = f'Thank you for your payment.\n\nPlan: {plan_name}\nAmount: ${amount}\n\nYour membership is now active.'
    mail.send(msg)

# Reports page
@app.route('/reports')
@login_required
def reports():
    total_members = Member.query.count()
    total_trainers = Trainer.query.count()
    total_attendance = Attendance.query.count()
    total_revenue = db.session.query(db.func.sum(Payment.amount)).scalar() or 0
    recent_payments = Payment.query.order_by(Payment.payment_date.desc()).limit(10).all()

    # Revenue by method
    cash_revenue = db.session.query(db.func.sum(Payment.amount)).filter_by(method='cash').scalar() or 0
    mpesa_revenue = db.session.query(db.func.sum(Payment.amount)).filter_by(method='mpesa').scalar() or 0
    card_revenue = db.session.query(db.func.sum(Payment.amount)).filter_by(method='card').scalar() or 0

    # Membership plan counts
    monthly_count = Member.query.join(MembershipPlan).filter(MembershipPlan.name == 'Monthly').count()
    quarterly_count = Member.query.join(MembershipPlan).filter(MembershipPlan.name == 'Quarterly').count()
    annual_count = Member.query.join(MembershipPlan).filter(MembershipPlan.name == 'Annual').count()
    return render_template('reports.html',
                           total_members=total_members,
                           total_trainers=total_trainers,
                           total_attendance=total_attendance,
                           total_revenue=total_revenue,
                           recent_payments=recent_payments,
                           cash_revenue=cash_revenue,
                           mpesa_revenue=mpesa_revenue,
                           card_revenue=card_revenue,
                           monthly_count=monthly_count,
                           quarterly_count=quarterly_count,
                           annual_count=annual_count)
    


# Progress page
@app.route('/progress')
@login_required
def progress():
    all_progress = Progress.query.order_by(Progress.date.desc()).all()
    all_members = Member.query.all()
    return render_template('progress.html', progress=all_progress, members=all_members)

@app.route('/progress/add', methods=['POST'])
@login_required
def add_progress():
    member_id = request.form.get('member_id')
    weight = request.form.get('weight')
    height = request.form.get('height')
    strength_score = request.form.get('strength_score')

    # Calculate BMI  automatically
    bmi = None
    if weight and height:
        height_m = float(height) / 100
        bmi = round(float(weight)/ (height_m ** 2), 2)

    new_record = Progress(
        member_id = int(member_id),
        weight = float(weight),
        bmi = bmi,
        strength_score = float(strength_score) if strength_score else None
    )
    db.session.add(new_record)
    db.session.commit()
    flash('Progress record added succesfully!', 'success')
    return redirect(url_for('progress'))

@app.route('/progress/delete/<int:id>')
@login_required
def delete_progress(id):
    record = Progress.query.get_or_404(id)
    db.session.delete(record)
    db.session.commit()
    flash('Progress record deleted successfully!', 'success')
    return redirect(url_for('progress'))

# Classes page
@app.route('/classes')
@login_required
def classes():
    all_classes = Class.query.all()
    all_trainers = Trainer.query.all()
    return render_template('classes.html', classes=all_classes, trainers=all_trainers)

@app.route('/classes/add', methods=['POST'])
@login_required
def add_class():
    from datetime import datetime
    name = request.form.get('name')
    trainer_id = request.form.get('trainer_id')
    schedule = request.form.get('schedule')
    capacity = request.form.get('capacity')
    description = request.form.get('description')

    new_class = Class(
        name = name,
        trainer_id = int(trainer_id),
        schedule = datetime.strptime(schedule, '%Y-%m-%dT%H:%M'),
        capacity = int(capacity),
        description = description
    )
    db.session.add(new_class)
    db.session.commit()
    flash(f'Class {name} added successfully!', 'success')
    return redirect(url_for('classes'))

@app.route('/classes/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_class(id):
    from datetime import datetime
    gym_class    = Class.query.get_or_404(id)
    all_trainers = Trainer.query.all()
    if request.method == 'POST':
        gym_class.name        = request.form.get('name')
        gym_class.trainer_id  = int(request.form.get('trainer_id'))
        gym_class.schedule    = datetime.strptime(request.form.get('schedule'), '%Y-%m-%dT%H:%M')
        gym_class.capacity    = int(request.form.get('capacity'))
        gym_class.description = request.form.get('description')
        db.session.commit()
        flash('Class updated successfully!', 'success')
        return redirect(url_for('classes'))
    return render_template('edit_class.html', gym_class=gym_class, trainers=all_trainers)

@app.route('/classes/delete/<int:id>')
@login_required
def delete_class(id):
    gym_class = Class.query.get_or_404(id)
    db.session.delete(gym_class)
    db.session.commit()
    flash('Class deleted successfully!', 'success')
    return redirect(url_for('classes'))

# Settings page
@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

@app.route('/settings/change-password', methods=['POST'])
@login_required
def change_password():
    current_password = request.form.get('current_password')
    new_password     = request.form.get('new_password')
    confirm_password = request.form.get('confirm_password')

    if not bcrypt.check_password_hash(current_user.password_hash, current_password):
        flash('Current password is incorrect.', 'danger')
        return redirect(url_for('settings'))

    if new_password != confirm_password:
        flash('New passwords do not match.', 'danger')
        return redirect(url_for('settings'))

    current_user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
    db.session.commit()
    flash('Password updated successfully!', 'success')
    return redirect(url_for('settings'))

# Error Handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Create DB & Admin Account
def create_tables():
    with app.app_context():
        db.create_all()
        #Create default admin
        if not User.query.filter_by(role='admin').first():
            hashed_pw = bcrypt.generate_password_hash('admin123').decode('utf-8')
            admin_user = User(
                username = 'Admin',
                email = 'admin@gym.com',
                password_hash = hashed_pw,
                role = 'admin'
            )
            db.session.add(admin_user)
            db.session.commit()
            print('Default admin created: admin@gym.com / admin123')

if __name__ == '__main__':
    create_tables()
    app.run(debug=True)
