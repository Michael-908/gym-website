from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# Users table
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(100), nullable=False)
    email         = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role          = db.Column(db.String(20), nullable=False, default='member')
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    member = db.relationship('Member', back_populates='user', uselist=False, cascade="all, delete-orphan")
    trainer = db.relationship('Trainer', back_populates='user', uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<User {self.email} - {self.role}>'


# Members Table
class Member(db.Model):
    __tablename__ = 'members'
    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    name      = db.Column(db.String(100), nullable=False)
    phone     = db.Column(db.String(20))
    email     = db.Column(db.String(150))
    plan_id   = db.Column(db.Integer, db.ForeignKey('membership_plans.id'))
    goal      = db.Column(db.String(200))
    join_date = db.Column(db.Date, default=datetime.utcnow)
    status    = db.Column(db.String(20), default='active')

    user       = db.relationship('User', back_populates='member')
    plan       = db.relationship('MembershipPlan', backref='members')
    attendance = db.relationship('Attendance',    backref='member', lazy=True)
    payments   = db.relationship('Payment',       backref='member', lazy=True)
    progress   = db.relationship('Progress',      backref='member', lazy=True)
    nutrition  = db.relationship('Nutrition',     backref='member', lazy=True)
    bookings   = db.relationship('ClassBooking',  backref='member', lazy=True)
    workouts   = db.relationship('MemberWorkout', backref='member', lazy=True)

    def __repr__(self):
        return f'<Member {self.name}>'


# Membership Plans Table
class MembershipPlan(db.Model):
    __tablename__ = 'membership_plans'
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(50), nullable=False)
    price         = db.Column(db.Float, nullable=False)
    duration_days = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return f'<MembershipPlan {self.name} - ${self.price}>'


# Trainers Table
class Trainer(db.Model):
    __tablename__  = 'trainers'
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, unique=True)
    name           = db.Column(db.String(100), nullable=False)
    phone          = db.Column(db.String(20))
    email          = db.Column(db.String(150))
    specialization = db.Column(db.String(100))
    bio            = db.Column(db.Text)

    user     = db.relationship('User', back_populates='trainer')
    workouts = db.relationship('Workout', backref='trainer', lazy=True)
    classes  = db.relationship('Class',   backref='trainer', lazy=True)

    def __repr__(self):
        return f'<Trainer {self.name}>'


# Workouts Table
class Workout(db.Model):
    __tablename__ = 'workouts'
    id          = db.Column(db.Integer, primary_key=True)
    trainer_id  = db.Column(db.Integer, db.ForeignKey('trainers.id'), nullable=True)  # nullable so members can create personal workouts
    member_id   = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=True)   # set when member creates own workout
    name        = db.Column(db.String(100), nullable=False)
    type        = db.Column(db.String(50))
    duration    = db.Column(db.Integer)
    difficulty  = db.Column(db.String(20))
    description = db.Column(db.Text)
    is_personal = db.Column(db.Boolean, default=False)  # True = member's personal workout

    assigned_members = db.relationship('MemberWorkout', backref='workout', lazy=True)
    comments         = db.relationship('WorkoutComment', backref='workout', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Workout {self.name}>'


# Member Workouts (Junction Table)
class MemberWorkout(db.Model):
    __tablename__ = 'member_workouts'
    id            = db.Column(db.Integer, primary_key=True)
    member_id     = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    workout_id    = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=False)
    assigned_date = db.Column(db.Date, default=datetime.utcnow)

    def __repr__(self):
        return f'<MemberWorkout member={self.member_id} workout={self.workout_id}>'


# Workout Comments (Trainer comments on member workouts)
class WorkoutComment(db.Model):
    __tablename__ = 'workout_comments'
    id         = db.Column(db.Integer, primary_key=True)
    workout_id = db.Column(db.Integer, db.ForeignKey('workouts.id'), nullable=False)
    trainer_id = db.Column(db.Integer, db.ForeignKey('trainers.id'), nullable=False)
    comment    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    trainer = db.relationship('Trainer', backref='workout_comments')

    def __repr__(self):
        return f'<WorkoutComment trainer={self.trainer_id} workout={self.workout_id}>'


# Nutrition Table
class Nutrition(db.Model):
    __tablename__ = 'nutrition'
    id        = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    meal_plan = db.Column(db.String(100))
    calories  = db.Column(db.Integer)
    protein   = db.Column(db.Integer)
    schedule  = db.Column(db.Text)
    date      = db.Column(db.Date, default=datetime.utcnow)

    comments = db.relationship('NutritionComment', backref='nutrition', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Nutrition member={self.member_id}>'


# Nutrition Comments (Trainer comments on nutrition plans)
class NutritionComment(db.Model):
    __tablename__ = 'nutrition_comments'
    id           = db.Column(db.Integer, primary_key=True)
    nutrition_id = db.Column(db.Integer, db.ForeignKey('nutrition.id'), nullable=False)
    trainer_id   = db.Column(db.Integer, db.ForeignKey('trainers.id'), nullable=False)
    comment      = db.Column(db.Text, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    trainer = db.relationship('Trainer', backref='nutrition_comments')

    def __repr__(self):
        return f'<NutritionComment trainer={self.trainer_id} nutrition={self.nutrition_id}>'


# Attendance Table
class Attendance(db.Model):
    __tablename__ = 'attendance'
    id        = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    check_in  = db.Column(db.DateTime, default=datetime.utcnow)
    check_out = db.Column(db.DateTime, nullable=True)
    date      = db.Column(db.Date, default=datetime.utcnow)

    def __repr__(self):
        return f'<Attendance member={self.member_id} date={self.date}>'


# Payments Table
class Payment(db.Model):
    __tablename__   = 'payments'
    id              = db.Column(db.Integer, primary_key=True)
    member_id       = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    amount          = db.Column(db.Float, nullable=False)
    method          = db.Column(db.String(20))
    expiry_date     = db.Column(db.Date)
    payment_date    = db.Column(db.DateTime, default=datetime.utcnow)
    status          = db.Column(db.String(20), default='completed')
    transaction_id  = db.Column(db.String(100))
    external_status = db.Column(db.String(20), default='pending')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Payment member={self.member_id} amount={self.amount}>'


# Progress Table
class Progress(db.Model):
    __tablename__  = 'progress'
    id             = db.Column(db.Integer, primary_key=True)
    member_id      = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    weight         = db.Column(db.Float)
    bmi            = db.Column(db.Float)
    strength_score = db.Column(db.Float)
    date           = db.Column(db.Date, default=datetime.utcnow)

    def __repr__(self):
        return f'<Progress member={self.member_id} date={self.date}>'


# Classes Table
class Class(db.Model):
    __tablename__ = 'classes'
    id          = db.Column(db.Integer, primary_key=True)
    trainer_id  = db.Column(db.Integer, db.ForeignKey('trainers.id'), nullable=False)
    name        = db.Column(db.String(100), nullable=False)
    schedule    = db.Column(db.DateTime)
    capacity    = db.Column(db.Integer, default=20)
    description = db.Column(db.Text)

    bookings = db.relationship('ClassBooking', backref='gym_class', lazy=True)

    def __repr__(self):
        return f'<Class {self.name}>'


# Class Bookings Table
class ClassBooking(db.Model):
    __tablename__ = 'class_bookings'
    id           = db.Column(db.Integer, primary_key=True)
    member_id    = db.Column(db.Integer, db.ForeignKey('members.id'), nullable=False)
    class_id     = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    booking_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<ClassBooking member={self.member_id} class={self.class_id}>'