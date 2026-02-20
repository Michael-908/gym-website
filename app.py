from flask import Flask, render_template

app = Flask(__name__)

# Login page
@app.route('/')
def login():
    return render_template('login.html')

# Register page
@app.route('/register')
def register():
    return render_template('register.html')

# Forgot password page
@app.route('/forgot-password')
def forgot_password():
    return render_template('forgot-password.html')

# Dashboard
@app.route('/dashboard')
def dashboard():
    return render_template('index.html')

# Members page
@app.route('/members')
def members():
    return render_template('members.html')

# Trainers page
@app.route('/trainers')
def trainers():
    return render_template('trainers.html')

# Workouts page
@app.route('/workouts')
def workouts():
    return render_template('workouts.html')

# Nutrition page
@app.route('/nutrition')
def nutrition():
    return render_template('nutrition.html')

# Attendance page
@app.route('/attendance')
def attendance():
    return render_template('attendance.html')

# Payments page
@app.route('/payments')
def payments():
    return render_template('payments.html')

# Reports page
@app.route('/reports')
def reports():
    return render_template('reports.html')

# Progress page
@app.route('/progress')
def progress():
    return render_template('progress.html')

# Classes page
@app.route('/classes')
def classes():
    return render_template('classes.html')

# Settings page
@app.route('/settings')
def settings():
    return render_template('settings.html')

# 404 page
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
