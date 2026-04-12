from flask import Flask, render_template, redirect, url_for, flash, request, abort
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from flask_mail import Mail, Message
from models import (db, User, Member, Trainer, Workout, Nutrition, Attendance,
                    Payment, Progress, Class, ClassBooking, MembershipPlan,
                    MemberWorkout, WorkoutComment, NutritionComment)
from dotenv import load_dotenv
from functools import wraps
import os
import stripe
from datetime import datetime, timedelta

app = Flask(__name__)
load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
app.config['SECRET_KEY']                  = 'gymapp-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI']     = 'sqlite:///gym.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE']       = False
app.config['SESSION_COOKIE_HTTPONLY']     = True
app.config['SESSION_COOKIE_SAMESITE']     = 'Lax'
app.config['REMEMBER_COOKIE_DURATION']    = 3600

# Stripe
app.config['STRIPE_PUBLIC_KEY']  = os.getenv('STRIPE_PUBLIC_KEY')
app.config['STRIPE_SECRET_KEY']  = os.getenv('STRIPE_SECRET_KEY')
stripe.api_key = app.config['STRIPE_SECRET_KEY'] or ''

# M-Pesa
app.config['MPESA_CONSUMER_KEY']    = os.getenv('MPESA_CONSUMER_KEY')
app.config['MPESA_CONSUMER_SECRET'] = os.getenv('MPESA_CONSUMER_SECRET')
app.config['MPESA_SHORTCODE']       = os.getenv('MPESA_SHORTCODE')
app.config['MPESA_PASSKEY']         = os.getenv('MPESA_PASSKEY')

# Email
app.config['MAIL_SERVER']         = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT']           = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS']        = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME']       = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

# ── Extensions ────────────────────────────────────────────────────────────────
db.init_app(app)
bcrypt = Bcrypt(app)
mail   = Mail(app)

# ── Seed DB ───────────────────────────────────────────────────────────────────
with app.app_context():
    db.create_all()
    if not MembershipPlan.query.first():
        plans = [
            MembershipPlan(name='Monthly',   price=30.0,  duration_days=30),
            MembershipPlan(name='Quarterly', price=80.0,  duration_days=90),
            MembershipPlan(name='Annual',    price=250.0, duration_days=365),
        ]
        db.session.add_all(plans)
        db.session.commit()

# ── Login Manager ─────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view             = 'login'
login_manager.login_message          = 'Please log in to access this page.'
login_manager.login_message_category = 'info'
login_manager.session_protection     = 'basic'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
        # Auto-create member profile if missing
        member = Member.query.filter_by(user_id=current_user.id).first()
        if not member:
            member = Member(user_id=current_user.id, name=current_user.username,
                           email=current_user.email, status='active')
            db.session.add(member)
            db.session.commit()
        return f(*args, **kwargs)
    return decorated

# ── Helper: smart redirect based on role ─────────────────────────────────────
@app.route('/redirect')
@login_required
def smart_redirect():
    if current_user.role == 'admin':
        return redirect(url_for('dashboard'))
    elif current_user.role == 'trainer':
        return redirect(url_for('trainer_dashboard'))
    else:
        return redirect(url_for('member_dashboard'))

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
        new_user = User(username=username, email=email, password_hash=hashed, role=role)
        db.session.add(new_user)
        db.session.flush()
        if role == 'member':
            db.session.add(Member(user_id=new_user.id, name=username, email=email))
        db.session.commit()
        flash('Account created! You can now log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/forgot-password')
def forgot_password():
    return render_template('forgot-password.html')

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
    card_revenue    = db.session.query(db.func.sum(Payment.amount)).filter_by(method='card').scalar() or 0
    stripe_revenue  = db.session.query(db.func.sum(Payment.amount)).filter_by(method='stripe').scalar() or 0
    card_revenue    = card_revenue + stripe_revenue
    recent_payments = Payment.query.order_by(Payment.payment_date.desc()).limit(5).all()
    recent_attendance = Attendance.query.order_by(Attendance.check_in.desc()).limit(10).all()
    return render_template('index.html',
                           total_members=total_members,
                           active_members=active_members,
                           total_trainers=total_trainers,
                           total_payments=total_payments,
                           total_classes=total_classes,
                           total_workouts=total_workouts,
                           cash_revenue=cash_revenue,
                           mpesa_revenue=mpesa_revenue,
                           card_revenue=card_revenue,
                           recent_payments=recent_payments,
                           recent_attendance=recent_attendance)

# ═══════════════════════════════════════════════════════════════════════════════
#  TRAINER DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/trainer-dashboard')
@login_required
@trainer_required
def trainer_dashboard():
    from datetime import date
    trainer = Trainer.query.filter_by(user_id=current_user.id).first()
    today   = date.today()

    today_classes = []
    my_clients    = []
    if trainer:
        today_classes = Class.query.filter(
            Class.trainer_id == trainer.id,
            db.func.date(Class.schedule) == today
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
                           trainer=trainer,
                           today_classes=today_classes,
                           my_clients=my_clients,
                           week_attendance=week_attendance,
                           recent_attendance=recent_attendance,
                           all_members=all_members,
                           today=today)

# ═══════════════════════════════════════════════════════════════════════════════
#  MEMBER DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/member-dashboard')
@login_required
def member_dashboard():
    from datetime import date
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('login'))

    member_workouts  = MemberWorkout.query.filter_by(member_id=member.id).order_by(MemberWorkout.assigned_date.desc()).limit(5).all()
    member_progress  = Progress.query.filter_by(member_id=member.id).order_by(Progress.date.desc()).limit(6).all()
    today            = date.today()
    upcoming_classes = (ClassBooking.query.filter_by(member_id=member.id)
                        .join(Class).filter(Class.schedule >= datetime.now())
                        .order_by(Class.schedule).limit(5).all())
    nutrition_plan   = Nutrition.query.filter_by(member_id=member.id).order_by(Nutrition.date.desc()).first()
    recent_payments  = Payment.query.filter_by(member_id=member.id).order_by(Payment.payment_date.desc()).limit(3).all()
    latest_payment   = Payment.query.filter_by(member_id=member.id).order_by(Payment.expiry_date.desc()).first()
    month_attendance = Attendance.query.filter_by(member_id=member.id).filter(
        db.func.strftime('%Y-%m', Attendance.date) == today.strftime('%Y-%m')
    ).count()
    available_classes = Class.query.filter(Class.schedule >= datetime.now()).order_by(Class.schedule).limit(10).all()
    all_trainers      = Trainer.query.all()

    return render_template('member_dashboard.html',
                           member=member,
                           member_workouts=member_workouts,
                           member_progress=member_progress,
                           upcoming_classes=upcoming_classes,
                           nutrition_plan=nutrition_plan,
                           recent_payments=recent_payments,
                           latest_payment=latest_payment,
                           month_attendance=month_attendance,
                           available_classes=available_classes,
                           all_trainers=all_trainers,
                           today=today)

# ═══════════════════════════════════════════════════════════════════════════════
#  MEMBERS  (Admin only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/members')
@login_required
@admin_required
def members():
    return render_template('members.html',
                           members=Member.query.all(),
                           plans=MembershipPlan.query.all())

@app.route('/members/add', methods=['POST'])
@login_required
@admin_required
def add_member():
    name     = request.form.get('name')
    email    = request.form.get('email')
    phone    = request.form.get('phone')
    plan_id  = request.form.get('plan_id')
    goal     = request.form.get('goal')
    status   = request.form.get('status')
    password = request.form.get('password')
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'danger')
        return redirect(url_for('members'))
    hashed   = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user = User(username=name, email=email, password_hash=hashed, role='member')
    db.session.add(new_user)
    db.session.flush()
    db.session.add(Member(user_id=new_user.id, name=name, email=email, phone=phone,
                          plan_id=int(plan_id) if plan_id else None, goal=goal, status=status))
    db.session.commit()
    flash(f'Member {name} added!', 'success')
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
        plan_id        = request.form.get('plan_id')
        member.plan_id = int(plan_id) if plan_id else None
        member.goal    = request.form.get('goal')
        member.status  = request.form.get('status')
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
#  TRAINERS  (Admin only)
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
    name           = request.form.get('name')
    email          = request.form.get('email')
    phone          = request.form.get('phone')
    specialization = request.form.get('specialization')
    bio            = request.form.get('bio')
    password       = request.form.get('password')
    if User.query.filter_by(email=email).first():
        flash('A user with this email already exists.', 'danger')
        return redirect(url_for('trainers'))
    hashed      = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user    = User(username=name, email=email, password_hash=hashed, role='trainer')
    db.session.add(new_user)
    db.session.flush()
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
        trainer.name           = request.form.get('name')
        trainer.email          = request.form.get('email')
        trainer.phone          = request.form.get('phone')
        trainer.specialization = request.form.get('specialization')
        trainer.bio            = request.form.get('bio')
        db.session.commit()
        flash('Trainer updated!', 'success')
        return redirect(url_for('trainers'))
    return render_template('edit_trainer.html', trainer=trainer)

@app.route('/trainers/delete/<int:id>')
@login_required
@admin_required
def delete_trainer(id):
    trainer = Trainer.query.get_or_404(id)
    user    = User.query.get(trainer.user_id)
    db.session.delete(trainer)
    if user:
        db.session.delete(user)
    db.session.commit()
    flash('Trainer deleted.', 'success')
    return redirect(url_for('trainers'))

# Member view of trainers (read-only profiles)
@app.route('/trainer-profiles')
@login_required
def trainer_profiles():
    all_trainers = Trainer.query.all()
    return render_template('trainer_profiles.html', trainers=all_trainers)

# ═══════════════════════════════════════════════════════════════════════════════
#  WORKOUTS
#  - Admin/Trainer: full CRUD, assign to members, comment on member workouts
#  - Member: add personal workouts, view only their own, delete their own
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/workouts')
@login_required
@trainer_required
def workouts():
    all_workouts = Workout.query.filter_by(is_personal=False).all()
    all_trainers = Trainer.query.all()
    all_members  = Member.query.all()
    return render_template('workouts.html',
                           workouts=all_workouts,
                           trainers=all_trainers,
                           members=all_members)

@app.route('/workouts/add', methods=['POST'])
@login_required
@trainer_required
def add_workout():
    trainer_id = request.form.get('trainer_id')
    new_workout = Workout(
        name        = request.form.get('name'),
        type        = request.form.get('type'),
        duration    = int(request.form.get('duration', 0)),
        difficulty  = request.form.get('difficulty'),
        trainer_id  = int(trainer_id) if trainer_id else None,
        description = request.form.get('description'),
        is_personal = False
    )
    db.session.add(new_workout)
    db.session.commit()
    flash('Workout added!', 'success')
    return redirect(url_for('workouts'))

@app.route('/workouts/assign', methods=['POST'])
@login_required
@trainer_required
def assign_workout():
    member_id  = request.form.get('member_id')
    workout_id = request.form.get('workout_id')
    existing   = MemberWorkout.query.filter_by(member_id=int(member_id), workout_id=int(workout_id)).first()
    if existing:
        flash('Workout already assigned to this member.', 'warning')
    else:
        db.session.add(MemberWorkout(member_id=int(member_id), workout_id=int(workout_id)))
        db.session.commit()
        flash('Workout assigned to member!', 'success')
    return redirect(url_for('workouts'))

@app.route('/workouts/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@trainer_required
def edit_workout(id):
    workout      = Workout.query.get_or_404(id)
    all_trainers = Trainer.query.all()
    if request.method == 'POST':
        workout.name        = request.form.get('name')
        workout.type        = request.form.get('type')
        workout.duration    = int(request.form.get('duration'))
        workout.difficulty  = request.form.get('difficulty')
        tid                 = request.form.get('trainer_id')
        workout.trainer_id  = int(tid) if tid else None
        workout.description = request.form.get('description')
        db.session.commit()
        flash('Workout updated!', 'success')
        return redirect(url_for('workouts'))
    return render_template('edit_workout.html', workout=workout, trainers=all_trainers)

@app.route('/workouts/delete/<int:id>')
@login_required
@trainer_required
def delete_workout(id):
    workout = Workout.query.get_or_404(id)
    db.session.delete(workout)
    db.session.commit()
    flash('Workout deleted.', 'success')
    return redirect(url_for('workouts'))

# Trainer comments on workouts
@app.route('/workouts/<int:workout_id>/comment', methods=['POST'])
@login_required
@trainer_required
def comment_workout(workout_id):
    workout = Workout.query.get_or_404(workout_id)
    trainer = Trainer.query.filter_by(user_id=current_user.id).first()
    if not trainer:
        flash('Trainer profile not found.', 'danger')
        return redirect(url_for('workouts'))
    comment_text = request.form.get('comment', '').strip()
    if comment_text:
        db.session.add(WorkoutComment(workout_id=workout_id, trainer_id=trainer.id, comment=comment_text))
        db.session.commit()
        flash('Comment added!', 'success')
    return redirect(url_for('workouts'))

# ─── Member workout routes ────────────────────────────────────────────────────

@app.route('/my-workouts')
@login_required
@member_required
def my_workouts():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    # Both personal workouts AND assigned workouts
    personal_workouts  = Workout.query.filter_by(member_id=member.id, is_personal=True).all()
    assigned_workouts  = MemberWorkout.query.filter_by(member_id=member.id).all()
    return render_template('my_workout.html',
                           member=member,
                           personal_workouts=personal_workouts,
                           assigned_workouts=assigned_workouts)

@app.route('/my-workouts/add', methods=['POST'])
@login_required
@member_required
def add_my_workout():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    new_workout = Workout(
        name        = request.form.get('name'),
        type        = request.form.get('type'),
        duration    = int(request.form.get('duration', 0)),
        difficulty  = request.form.get('difficulty'),
        description = request.form.get('description'),
        member_id   = member.id,
        is_personal = True
    )
    db.session.add(new_workout)
    db.session.commit()
    flash('Workout added!', 'success')
    return redirect(url_for('my_workouts'))

@app.route('/my-workouts/delete/<int:id>')
@login_required
@member_required
def delete_my_workout(id):
    member  = Member.query.filter_by(user_id=current_user.id).first()
    workout = Workout.query.get_or_404(id)
    if workout.member_id != member.id or not workout.is_personal:
        flash('You can only delete your own workouts.', 'danger')
        return redirect(url_for('my_workouts'))
    db.session.delete(workout)
    db.session.commit()
    flash('Workout deleted.', 'success')
    return redirect(url_for('my_workouts'))

# ═══════════════════════════════════════════════════════════════════════════════
#  NUTRITION
#  - Admin/Trainer: full CRUD + comment on plans
#  - Member: view own plans only
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/nutrition')
@login_required
@trainer_required
def nutrition():
    all_nutrition = Nutrition.query.all()
    all_members   = Member.query.all()
    return render_template('nutrition.html', nutrition=all_nutrition, members=all_members)

@app.route('/nutrition/add', methods=['POST'])
@login_required
@trainer_required
def add_nutrition():
    db.session.add(Nutrition(
        member_id = int(request.form.get('member_id')),
        meal_plan = request.form.get('meal_plan'),
        calories  = int(request.form.get('calories')),
        protein   = int(request.form.get('protein')),
        schedule  = request.form.get('schedule')
    ))
    db.session.commit()
    flash('Meal plan added!', 'success')
    return redirect(url_for('nutrition'))

@app.route('/nutrition/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@trainer_required
def edit_nutrition(id):
    plan        = Nutrition.query.get_or_404(id)
    all_members = Member.query.all()
    if request.method == 'POST':
        plan.member_id = int(request.form.get('member_id'))
        plan.meal_plan = request.form.get('meal_plan')
        plan.calories  = int(request.form.get('calories'))
        plan.protein   = int(request.form.get('protein'))
        plan.schedule  = request.form.get('schedule')
        db.session.commit()
        flash('Meal plan updated!', 'success')
        return redirect(url_for('nutrition'))
    return render_template('edit_nutrition.html', plan=plan, members=all_members)

@app.route('/nutrition/delete/<int:id>')
@login_required
@trainer_required
def delete_nutrition(id):
    plan = Nutrition.query.get_or_404(id)
    db.session.delete(plan)
    db.session.commit()
    flash('Meal plan deleted.', 'success')
    return redirect(url_for('nutrition'))

# Trainer comment on nutrition
@app.route('/nutrition/<int:nutrition_id>/comment', methods=['POST'])
@login_required
@trainer_required
def comment_nutrition(nutrition_id):
    Nutrition.query.get_or_404(nutrition_id)
    trainer      = Trainer.query.filter_by(user_id=current_user.id).first()
    comment_text = request.form.get('comment', '').strip()
    if trainer and comment_text:
        db.session.add(NutritionComment(nutrition_id=nutrition_id, trainer_id=trainer.id, comment=comment_text))
        db.session.commit()
        flash('Comment added!', 'success')
    return redirect(url_for('nutrition'))

# Member view own nutrition
@app.route('/my-nutrition')
@login_required
@member_required
def my_nutrition():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    plans = Nutrition.query.filter_by(member_id=member.id).order_by(Nutrition.date.desc()).all()
    return render_template('my_nutrition.html', member=member, plans=plans)

# ═══════════════════════════════════════════════════════════════════════════════
#  ATTENDANCE
#  - Admin/Trainer: full CRUD (check-in, check-out, delete)
#  - Member: view own attendance only
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/attendance')
@login_required
@trainer_required
def attendance():
    from datetime import date
    all_attendance   = Attendance.query.order_by(Attendance.check_in.desc()).all()
    all_members      = Member.query.all()
    today            = date.today()
    today_count      = Attendance.query.filter_by(date=today).count()
    checked_in_count = Attendance.query.filter_by(date=today, check_out=None).count()
    total_count      = Attendance.query.count()
    return render_template('attendance.html',
                           attendance=all_attendance,
                           members=all_members,
                           today_count=today_count,
                           checked_in_count=checked_in_count,
                           total_count=total_count)

@app.route('/attendance/checkin', methods=['POST'])
@login_required
@trainer_required
def check_in():
    from datetime import date
    member_id = int(request.form.get('member_id'))
    today     = date.today()
    existing  = Attendance.query.filter_by(member_id=member_id, date=today, check_out=None).first()
    if existing:
        flash('Member already checked in today!', 'warning')
    else:
        db.session.add(Attendance(member_id=member_id, check_in=datetime.now(), date=today))
        db.session.commit()
        flash('Member checked in!', 'success')
    return redirect(url_for('attendance'))

@app.route('/attendance/checkout/<int:id>')
@login_required
@trainer_required
def check_out(id):
    record           = Attendance.query.get_or_404(id)
    record.check_out = datetime.now()
    db.session.commit()
    flash('Member checked out!', 'success')
    return redirect(url_for('attendance'))

@app.route('/attendance/delete/<int:id>')
@login_required
@trainer_required
def delete_attendance(id):
    record = Attendance.query.get_or_404(id)
    db.session.delete(record)
    db.session.commit()
    flash('Attendance record deleted.', 'success')
    return redirect(url_for('attendance'))

# Member view own attendance
@app.route('/my-attendance')
@login_required
@member_required
def my_attendance():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    records = Attendance.query.filter_by(member_id=member.id).order_by(Attendance.check_in.desc()).all()
    total   = len(records)
    return render_template('my_attendance.html', member=member, records=records, total=total)

# ═══════════════════════════════════════════════════════════════════════════════
#  PAYMENTS
#  - Admin: full access / record payments
#  - Member: view own payments + Stripe checkout
#  - Trainer: NO access
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/payments')
@login_required
@admin_required
def payments():
    from datetime import date
    from calendar import monthrange
    all_payments   = Payment.query.order_by(Payment.payment_date.desc()).all()
    all_members    = Member.query.all()
    total_revenue  = db.session.query(db.func.sum(Payment.amount)).scalar() or 0
    total_payments = Payment.query.count()
    today          = date.today()
    month_end      = date(today.year, today.month, monthrange(today.year, today.month)[1])
    expiring_count = Payment.query.filter(Payment.expiry_date <= month_end, Payment.expiry_date >= today).count()
    return render_template('payments.html',
                           payments=all_payments,
                           members=all_members,
                           plans=MembershipPlan.query.all(),
                           total_revenue=total_revenue,
                           total_payments=total_payments,
                           expiring_count=expiring_count)

@app.route('/payment/add', methods=['POST'])
@login_required
@admin_required
def add_payment():
    expiry = request.form.get('expiry_date')
    db.session.add(Payment(
        member_id   = int(request.form.get('member_id')),
        amount      = float(request.form.get('amount')),
        method      = request.form.get('method'),
        expiry_date = datetime.strptime(expiry, '%Y-%m-%d').date(),
        status      = 'completed'
    ))
    db.session.commit()
    flash('Payment recorded!', 'success')
    return redirect(url_for('payments'))

@app.route('/payments/delete/<int:id>')
@login_required
@admin_required
def delete_payment(id):
    payment = Payment.query.get_or_404(id)
    db.session.delete(payment)
    db.session.commit()
    flash('Payment deleted.', 'success')
    return redirect(url_for('payments'))

# Member: view own payments
@app.route('/my-payments')
@login_required
@member_required
def my_payments():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    my_pay   = Payment.query.filter_by(member_id=member.id).order_by(Payment.payment_date.desc()).all()
    plans    = MembershipPlan.query.all()
    latest   = Payment.query.filter_by(member_id=member.id).order_by(Payment.expiry_date.desc()).first()
    return render_template('my_payments.html', member=member, payments=my_pay, plans=plans, latest_payment=latest)

# Stripe: member checkout
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
                    'currency': 'usd',
                    'product_data': {'name': f'{plan.name} Membership'},
                    'unit_amount': int(plan.price * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('payment_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('my_payments', _external=True),
            metadata={'plan_id': str(plan_id), 'member_id': str(member.id)}
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
    payload         = request.get_data(as_text=True)
    sig_header      = request.headers.get('stripe-signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    if not endpoint_secret:
        return 'Webhook secret not configured', 400
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return 'Invalid', 400
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        try:
            member_id = int(session['metadata']['member_id'])
            plan_id   = int(session['metadata']['plan_id'])
            member    = Member.query.get(member_id)
            plan      = MembershipPlan.query.get(plan_id)
            if member and plan:
                expiry = datetime.utcnow().date() + timedelta(days=plan.duration_days)
                db.session.add(Payment(member_id=member_id, amount=plan.price, method='stripe',
                                       expiry_date=expiry, transaction_id=session.get('id'),
                                       status='completed', external_status='completed'))
                member.plan_id = plan_id
                member.status  = 'active'
                db.session.commit()
        except Exception as e:
            print(f'Webhook error: {e}')
    return '', 200

# ═══════════════════════════════════════════════════════════════════════════════
#  PROGRESS
#  - Admin/Trainer: full CRUD
#  - Member: view own + add own records
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/progress')
@login_required
@trainer_required
def progress():
    all_progress = Progress.query.order_by(Progress.date.desc()).all()
    all_members  = Member.query.all()
    return render_template('progress.html', progress=all_progress, members=all_members)

@app.route('/progress/add', methods=['POST'])
@login_required
@trainer_required
def add_progress():
    weight         = request.form.get('weight')
    height         = request.form.get('height')
    strength_score = request.form.get('strength_score')
    bmi = None
    if weight and height:
        h   = float(height) / 100
        bmi = round(float(weight) / (h ** 2), 2)
    db.session.add(Progress(
        member_id      = int(request.form.get('member_id')),
        weight         = float(weight),
        bmi            = bmi,
        strength_score = float(strength_score) if strength_score else None
    ))
    db.session.commit()
    flash('Progress record added!', 'success')
    return redirect(url_for('progress'))

@app.route('/progress/delete/<int:id>')
@login_required
@trainer_required
def delete_progress(id):
    record = Progress.query.get_or_404(id)
    db.session.delete(record)
    db.session.commit()
    flash('Progress record deleted.', 'success')
    return redirect(url_for('progress'))

# Member: view + add own progress
@app.route('/my-progress')
@login_required
@member_required
def my_progress():
    member  = Member.query.filter_by(user_id=current_user.id).first()
    records = Progress.query.filter_by(member_id=member.id).order_by(Progress.date.desc()).all()
    return render_template('my_progress.html', member=member, records=records)

@app.route('/my-progress/add', methods=['POST'])
@login_required
@member_required
def add_my_progress():
    member = Member.query.filter_by(user_id=current_user.id).first()
    if not member:
        flash('Member profile not found.', 'danger')
        return redirect(url_for('member_dashboard'))
    weight         = request.form.get('weight')
    height         = request.form.get('height')
    strength_score = request.form.get('strength_score')
    bmi = None
    if weight and height:
        h   = float(height) / 100
        bmi = round(float(weight) / (h ** 2), 2)
    db.session.add(Progress(
        member_id      = member.id,
        weight         = float(weight),
        bmi            = bmi,
        strength_score = float(strength_score) if strength_score else None
    ))
    db.session.commit()
    flash('Progress record added!', 'success')
    return redirect(url_for('my_progress'))

# ═══════════════════════════════════════════════════════════════════════════════
#  CLASSES
#  - Admin/Trainer: full CRUD
#  - Member: view + book/cancel (on member dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/classes')
@login_required
@trainer_required
def classes():
    all_classes  = Class.query.all()
    all_trainers = Trainer.query.all()
    return render_template('classes.html', classes=all_classes, trainers=all_trainers)

@app.route('/classes/add', methods=['POST'])
@login_required
@trainer_required
def add_class():
    schedule = request.form.get('schedule')
    db.session.add(Class(
        name        = request.form.get('name'),
        trainer_id  = int(request.form.get('trainer_id')),
        schedule    = datetime.strptime(schedule, '%Y-%m-%dT%H:%M'),
        capacity    = int(request.form.get('capacity', 20)),
        description = request.form.get('description')
    ))
    db.session.commit()
    flash('Class added!', 'success')
    return redirect(url_for('classes'))

@app.route('/classes/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@trainer_required
def edit_class(id):
    gym_class    = Class.query.get_or_404(id)
    all_trainers = Trainer.query.all()
    if request.method == 'POST':
        gym_class.name        = request.form.get('name')
        gym_class.trainer_id  = int(request.form.get('trainer_id'))
        gym_class.schedule    = datetime.strptime(request.form.get('schedule'), '%Y-%m-%dT%H:%M')
        gym_class.capacity    = int(request.form.get('capacity'))
        gym_class.description = request.form.get('description')
        db.session.commit()
        flash('Class updated!', 'success')
        return redirect(url_for('classes'))
    return render_template('edit_class.html', gym_class=gym_class, trainers=all_trainers)

@app.route('/classes/delete/<int:id>')
@login_required
@trainer_required
def delete_class(id):
    gym_class = Class.query.get_or_404(id)
    db.session.delete(gym_class)
    db.session.commit()
    flash('Class deleted.', 'success')
    return redirect(url_for('classes'))

# Member: book a class
@app.route('/member/book-class/<int:class_id>')
@login_required
@member_required
def book_class(class_id):
    member    = Member.query.filter_by(user_id=current_user.id).first()
    gym_class = Class.query.get_or_404(class_id)
    if ClassBooking.query.filter_by(member_id=member.id, class_id=class_id).first():
        flash('You already booked this class!', 'warning')
        return redirect(url_for('member_dashboard'))
    if len(gym_class.bookings) >= gym_class.capacity:
        flash('This class is full!', 'danger')
        return redirect(url_for('member_dashboard'))
    db.session.add(ClassBooking(member_id=member.id, class_id=class_id))
    db.session.commit()
    flash(f'Booked {gym_class.name}!', 'success')
    return redirect(url_for('member_dashboard'))

# Member: cancel a booking
@app.route('/member/cancel-booking/<int:booking_id>')
@login_required
@member_required
def cancel_booking(booking_id):
    booking = ClassBooking.query.get_or_404(booking_id)
    db.session.delete(booking)
    db.session.commit()
    flash('Booking cancelled.', 'success')
    return redirect(url_for('member_dashboard'))

# ═══════════════════════════════════════════════════════════════════════════════
#  REPORTS  (Admin only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/reports')
@login_required
@admin_required
def reports():
    total_members    = Member.query.count()
    total_trainers   = Trainer.query.count()
    total_attendance = Attendance.query.count()
    total_revenue    = db.session.query(db.func.sum(Payment.amount)).scalar() or 0
    recent_payments  = Payment.query.order_by(Payment.payment_date.desc()).limit(10).all()
    cash_revenue     = db.session.query(db.func.sum(Payment.amount)).filter_by(method='cash').scalar() or 0
    mpesa_revenue    = db.session.query(db.func.sum(Payment.amount)).filter_by(method='mpesa').scalar() or 0
    card_revenue     = db.session.query(db.func.sum(Payment.amount)).filter_by(method='card').scalar() or 0
    monthly_count    = Member.query.join(MembershipPlan).filter(MembershipPlan.name == 'Monthly').count()
    quarterly_count  = Member.query.join(MembershipPlan).filter(MembershipPlan.name == 'Quarterly').count()
    annual_count     = Member.query.join(MembershipPlan).filter(MembershipPlan.name == 'Annual').count()
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

# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS  (All roles)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/settings')
@login_required
def settings():
    return render_template('settings.html')

@app.route('/settings/change-password', methods=['POST'])
@login_required
def change_password():
    current_pw  = request.form.get('current_password')
    new_pw      = request.form.get('new_password')
    confirm_pw  = request.form.get('confirm_password')
    if not bcrypt.check_password_hash(current_user.password_hash, current_pw):
        flash('Current password is incorrect.', 'danger')
        return redirect(url_for('settings'))
    if new_pw != confirm_pw:
        flash('Passwords do not match.', 'danger')
        return redirect(url_for('settings'))
    current_user.password_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
    db.session.commit()
    flash('Password updated!', 'success')
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

# ═══════════════════════════════════════════════════════════════════════════════
#  INIT DB + DEFAULT ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

def create_tables():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(role='admin').first():
            hashed = bcrypt.generate_password_hash('admin123').decode('utf-8')
            db.session.add(User(username='Admin', email='admin@gym.com',
                                password_hash=hashed, role='admin'))
            db.session.commit()
            print('Default admin created: admin@gym.com / admin123')

if __name__ == '__main__':
    create_tables()
    app.run(debug=True)