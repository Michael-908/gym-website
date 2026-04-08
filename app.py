from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from models import db, User, Member, Trainer, Workout, Nutrition, Attendance, Payment, Progress, Class, ClassBooking
import os

app = Flask(__name__)

#Configuration
app.config['SECRET_KEY'] = 'gymapp-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///gym.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_DURATION'] = 3600

#Extensions
db.init_app(app)
bcrypt = Bcrypt(app)
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
    return render_template('members.html', members=all_members)

@app.route('/members/add', methods=['POST'])
@login_required
def add_member():
    name = request.form.get('name')
    email = request.form.get('email')
    phone = request.form.get('phone')
    membership_plan = request.form.get('membership_plan')
    goal = request.form.get('goal')
    status = request.form.get('status')
    password = request.form.get('password')

    existing = User.query.filter_by(email=email).first()
    if existing:
      flash('A user with this eamil already exists.', 'danger')
      return redirect(url_for('members'))

    hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
    new_user  = User(username=name, email=email, password_hash=hashed_pw, role='member')
    db.session.add(new_user)
    db.session.flush()

# Create meber profile
    new_member = Member(
    user_id = new_user.id,
    name    = name,
    email   = email,
    phone   = phone,
    membership_plan = membership_plan,
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
        member.name            = request.form.get('name')
        member.email           = request.form.get('email')
        member.phone           = request.form.get('phone')
        member.membership_plan = request.form.get('membership_plan')
        member.goal            = request.form.get('goal')
        member.status          = request.form.get('status')
        db.session.commit()
        flash('Member updated successfully!', 'success')
        return redirect(url_for('members'))
    return render_template('edit_member.html', member=member)

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
    member_id = request.form.get('memeber_id')
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
    monthly_count = Member.query.filter_by(membership_plan='monthly').count()
    quarterly_count = Member.query.filter_by(membership_plan='quarterly').count()
    annual_count = Member.query.filter_by(membership_plan='annual').count()
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
