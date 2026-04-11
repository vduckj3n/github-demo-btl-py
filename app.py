from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_file, send_from_directory, make_response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import io
import secrets
import hashlib

from config import Config
from models import db, User, Lab, Commitment, ProgressUpdate, Notification, ActivityLog
from csrf_utils import generate_csrf_token, validate_csrf_token

app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Vui lòng đăng nhập để tiếp tục.'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Create upload folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ============== CSRF PROTECTION ==============

def get_csrf_token():
    """Wrapper for Jinja2 - returns CSRF token from session."""
    return generate_csrf_token()

app.jinja_env.globals['csrf_token'] = get_csrf_token

@app.before_request
def csrf_protect():
    """Validate CSRF token on all POST/PUT/DELETE requests, except login page."""
    if request.method in ('POST', 'PUT', 'DELETE'):
        # Skip CSRF on login (login form is not a CSRF risk - no state change on server)
        if request.endpoint == 'login':
            return
        token = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
        if not validate_csrf_token(token):
            flash('Yêu cầu không hợp lệ (CSRF token không đúng). Vui lòng thử lại.', 'danger')
            return redirect(request.referrer or url_for('dashboard'))

def get_client_ip():
    return request.remote_addr or request.headers.get('X-Forwarded-For', '').split(',')[0].strip()

# ============== AUTH ROUTES ==============

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            ActivityLog.log(user.id, 'LOGIN', details=f'User {username} đăng nhập thành công', ip_address=get_client_ip())
            db.session.commit()
            flash('Đăng nhập thành công!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Tên đăng nhập hoặc mật khẩu không đúng.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    ActivityLog.log(current_user.id, 'LOGOUT', details=f'User {current_user.username} đăng xuất', ip_address=get_client_ip())
    db.session.commit()
    logout_user()
    flash('Đã đăng xuất.', 'info')
    return redirect(url_for('login'))

# ============== PROFILE ROUTES ==============

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        errors = []
        if not current_user.check_password(current_password):
            errors.append('Mật khẩu hiện tại không đúng.')
        if new_password and len(new_password) < 6:
            errors.append('Mật khẩu mới phải có ít nhất 6 ký tự.')
        if new_password and new_password != confirm_password:
            errors.append('Mật khẩu mới và xác nhận mật khẩu không khớp.')

        if errors:
            for err in errors:
                flash(err, 'danger')
            return redirect(url_for('profile'))

        if new_password:
            current_user.set_password(new_password)
            db.session.commit()
            ActivityLog.log(current_user.id, 'PASSWORD_CHANGE', details='User thay đổi mật khẩu', ip_address=get_client_ip())
            flash('Mật khẩu đã được thay đổi thành công!', 'success')
        else:
            flash('Không có thay đổi nào được lưu.', 'info')

        return redirect(url_for('profile'))

    return render_template('users/profile.html')

# ============== NOTIFICATION ROUTES ==============

@app.route('/notifications')
@login_required
def notifications():
    """List all notifications for current user"""
    notifications_list = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
    unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return render_template('notifications/list.html', notifications=notifications_list, unread_count=unread_count)

@app.route('/notifications/mark-read/<int:notif_id>')
@login_required
def notifications_mark_read(notif_id):
    """Mark a single notification as read"""
    notif = Notification.query.filter_by(id=notif_id, user_id=current_user.id).first_or_404()
    notif.is_read = True
    db.session.commit()
    if notif.link:
        return redirect(notif.link)
    return redirect(url_for('notifications'))

@app.route('/notifications/mark-all-read', methods=['POST'])
@login_required
def notifications_mark_all_read():
    """Mark all notifications as read"""
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({'is_read': True})
    db.session.commit()
    flash('Tất cả thông báo đã được đánh dấu là đã đọc.', 'success')
    return redirect(url_for('notifications'))

@app.route('/api/notifications/count')
@login_required
def api_notifications_count():
    """Get unread notification count"""
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})

# ============== DASHBOARD ROUTES ==============

@app.route('/dashboard')
@login_required
def dashboard():
    today = datetime.utcnow().date()

    if current_user.is_admin():
        total = Commitment.query.count()
        active = Commitment.query.filter(Commitment.status == 'Đang thực hiện').count()
        completed = Commitment.query.filter(Commitment.status == 'Hoàn thành').count()
        overdue = Commitment.query.filter(Commitment.status == 'Quá hạn').count()
        new_commits = Commitment.query.filter(Commitment.status == 'Mới').count()

        recent = Commitment.query.order_by(Commitment.updated_at.desc()).limit(10).all()

        lab_stats = db.session.query(
            Lab.name,
            db.func.count(Commitment.id).label('total'),
            db.func.sum(db.case((Commitment.progress >= 100, 1), else_=0)).label('completed')
        ).outerjoin(Commitment).group_by(Lab.id).all()

        status_chart = {
            'labels': ['Mới', 'Đang thực hiện', 'Hoàn thành', 'Quá hạn'],
            'data': [new_commits, active, completed, overdue]
        }

        labs = Lab.query.all()
        commitments_by_lab = []
        for lab in labs:
            count = Commitment.query.filter_by(lab_id=lab.id).count()
            commitments_by_lab.append({'name': lab.name, 'count': count})

        # Upcoming deadlines (within 7 days)
        upcoming = Commitment.query.filter(
            Commitment.deadline <= today + timedelta(days=7),
            Commitment.deadline >= today,
            Commitment.status.in_(['Mới', 'Đang thực hiện'])
        ).order_by(Commitment.deadline.asc()).limit(5).all()

        unread_notif_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    else:
        lab_id = current_user.lab_id
        total = Commitment.query.filter_by(lab_id=lab_id).count()
        active = Commitment.query.filter_by(lab_id=lab_id, status='Đang thực hiện').count()
        completed = Commitment.query.filter_by(lab_id=lab_id, status='Hoàn thành').count()
        overdue = Commitment.query.filter_by(lab_id=lab_id, status='Quá hạn').count()
        new_commits = Commitment.query.filter_by(lab_id=lab_id, status='Mới').count()

        recent = Commitment.query.filter_by(lab_id=lab_id).order_by(Commitment.updated_at.desc()).limit(10).all()

        status_chart = {
            'labels': ['Mới', 'Đang thực hiện', 'Hoàn thành', 'Quá hạn'],
            'data': [new_commits, active, completed, overdue]
        }

        labs = None
        commitments_by_lab = None
        lab_stats = None
        upcoming = Commitment.query.filter(
            Commitment.lab_id == lab_id,
            Commitment.deadline <= today + timedelta(days=7),
            Commitment.deadline >= today,
            Commitment.status.in_(['Mới', 'Đang thực hiện'])
        ).order_by(Commitment.deadline.asc()).limit(5).all()

        unread_notif_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()

    return render_template('dashboard.html',
                           total=total, active=active, completed=completed,
                           overdue=overdue, recent=recent,
                           status_chart=status_chart,
                           labs=labs, lab_stats=lab_stats,
                           commitments_by_lab=commitments_by_lab,
                           upcoming=upcoming,
                           unread_notif_count=unread_notif_count)

@app.route('/my-tasks')
@login_required
def my_tasks():
    if current_user.is_admin():
        flash('Trang này dành cho user Lab.', 'info')
        return redirect(url_for('dashboard'))

    today = datetime.utcnow().date()
    my_tasks_list = Commitment.query.filter_by(assigned_to=current_user.id).order_by(Commitment.deadline.asc()).all()

    total = len(my_tasks_list)
    completed = len([t for t in my_tasks_list if t.status == 'Hoàn thành'])
    active = len([t for t in my_tasks_list if t.status == 'Đang thực hiện'])
    overdue = len([t for t in my_tasks_list if t.status == 'Quá hạn'])

    return render_template('my_tasks.html',
                           my_tasks=my_tasks_list,
                           total=total, completed=completed,
                           active=active, overdue=overdue,
                           today=today)

# ============== LAB ROUTES ==============

@app.route('/labs')
@login_required
def labs_list():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    labs_list = Lab.query.order_by(Lab.created_at.desc()).all()
    return render_template('labs/list.html', labs=labs_list)

@app.route('/labs/create', methods=['GET', 'POST'])
@login_required
def labs_create():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        manager_name = request.form.get('manager_name')
        email = request.form.get('email')

        lab = Lab(name=name, description=description, manager_name=manager_name, email=email)
        db.session.add(lab)
        db.session.commit()

        ActivityLog.log(current_user.id, 'CREATE', 'Lab', lab.id, f'Tạo Lab mới: {name}', get_client_ip())
        db.session.commit()
        flash(f'Lab "{name}" đã được tạo thành công!', 'success')
        return redirect(url_for('labs_list'))

    return render_template('labs/form.html', lab=None, action='Tạo Lab mới')

@app.route('/labs/edit/<int:lab_id>', methods=['GET', 'POST'])
@login_required
def labs_edit(lab_id):
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    lab = Lab.query.get_or_404(lab_id)

    if request.method == 'POST':
        old_name = lab.name
        lab.name = request.form.get('name')
        lab.description = request.form.get('description')
        lab.manager_name = request.form.get('manager_name')
        lab.email = request.form.get('email')

        db.session.commit()
        ActivityLog.log(current_user.id, 'UPDATE', 'Lab', lab.id, f'Cập nhật Lab: {old_name} -> {lab.name}', get_client_ip())
        db.session.commit()
        flash(f'Lab "{lab.name}" đã được cập nhật!', 'success')
        return redirect(url_for('labs_list'))

    return render_template('labs/form.html', lab=lab, action='Chỉnh sửa Lab')

@app.route('/labs/delete/<int:lab_id>', methods=['POST'])
@login_required
def labs_delete(lab_id):
    if not current_user.is_admin():
        flash('Bạn không có quyền thực hiện thao tác này.', 'danger')
        return redirect(url_for('labs_list'))

    lab = Lab.query.get_or_404(lab_id)
    lab_name = lab.name

    Commitment.query.filter_by(lab_id=lab_id).delete()
    User.query.filter_by(lab_id=lab_id).update({'lab_id': None})
    db.session.delete(lab)
    db.session.commit()

    ActivityLog.log(current_user.id, 'DELETE', 'Lab', lab_id, f'Xóa Lab: {lab_name}', get_client_ip())
    db.session.commit()
    flash(f'Lab "{lab_name}" đã được xóa thành công!', 'success')
    return redirect(url_for('labs_list'))

# ============== USER ROUTES (Admin) ==============

@app.route('/users')
@login_required
def users_list():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    users = User.query.order_by(User.username).all()
    return render_template('users/list.html', users=users)

@app.route('/users/create', methods=['GET', 'POST'])
@login_required
def users_create():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        lab_id = request.form.get('lab_id') if role == 'lab' else None

        if User.query.filter_by(username=username).first():
            flash('Tên đăng nhập đã tồn tại!', 'danger')
            return render_template('users/form.html', user=None, labs=labs, action='Tạo User mới')

        user = User(username=username, role=role, lab_id=lab_id)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        ActivityLog.log(current_user.id, 'CREATE', 'User', user.id, f'Tạo User mới: {username} (role={role})', get_client_ip())
        db.session.commit()
        flash(f'User "{username}" đã được tạo thành công!', 'success')
        return redirect(url_for('users_list'))

    return render_template('users/form.html', user=None, labs=labs, action='Tạo User mới')

@app.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
@login_required
def users_edit(user_id):
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    user = User.query.get_or_404(user_id)
    labs = Lab.query.all()

    if request.method == 'POST':
        old_username = user.username
        user.role = request.form.get('role')
        user.lab_id = request.form.get('lab_id') if user.role == 'lab' else None

        new_password = request.form.get('password')
        if new_password:
            user.set_password(new_password)
            pw_note = ' (password thay đổi)'
        else:
            pw_note = ''

        db.session.commit()
        ActivityLog.log(current_user.id, 'UPDATE', 'User', user.id, f'Cập nhật User: {old_username} -> {user.username}{pw_note}', get_client_ip())
        db.session.commit()
        flash(f'User "{user.username}" đã được cập nhật!', 'success')
        return redirect(url_for('users_list'))

    return render_template('users/form.html', user=user, labs=labs, action='Chỉnh sửa User')

@app.route('/users/delete/<int:user_id>', methods=['POST'])
@login_required
def users_delete(user_id):
    if not current_user.is_admin():
        flash('Bạn không có quyền thực hiện thao tác này.', 'danger')
        return redirect(url_for('users_list'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('Không thể xóa chính mình.', 'danger')
        return redirect(url_for('users_list'))

    assigned_count = Commitment.query.filter_by(assigned_to=user.id).count()
    created_count = Commitment.query.filter_by(created_by=user.id).count()
    progress_updates_count = ProgressUpdate.query.filter_by(created_by=user.id).count()

    if assigned_count:
        flash(
            'Không thể xóa User này vì hiện tại user đang phụ trách các cam kết. ' 
            'Vui lòng chuyển giao hoặc xóa các cam kết trước khi xóa.',
            'danger'
        )
        return redirect(url_for('users_list'))

    # Disassociate nullable audit references so the user can be removed cleanly.
    Commitment.query.filter_by(created_by=user.id).update({'created_by': None})
    ProgressUpdate.query.filter_by(created_by=user.id).update({'created_by': None})
    Notification.query.filter_by(user_id=user.id).delete()
    ActivityLog.query.filter_by(user_id=user.id).delete()
    db.session.commit()

    user_name = user.username
    db.session.delete(user)
    db.session.commit()

    ActivityLog.log(current_user.id, 'DELETE', 'User', user_id, f'Xóa User: {user_name}', get_client_ip())
    db.session.commit()
    flash(f'User "{user_name}" đã được xóa thành công!', 'success')
    return redirect(url_for('users_list'))

# ============== COMMITMENT ROUTES ==============

@app.route('/commitments')
@login_required
def commitments_list():
    query = Commitment.query

    if not current_user.is_admin():
        query = query.filter_by(lab_id=current_user.lab_id)

    lab_filter = request.args.get('lab_id')
    status_filter = request.args.get('status')
    search = request.args.get('search')

    if lab_filter:
        query = query.filter_by(lab_id=int(lab_filter))
    if status_filter:
        query = query.filter_by(status=status_filter)
    if search:
        query = query.filter(Commitment.title.contains(search))

    commitments = query.order_by(Commitment.deadline.asc()).all()
    labs = Lab.query.all()

    return render_template('commitments/list.html', commitments=commitments, labs=labs)

@app.route('/commitments/create', methods=['GET', 'POST'])
@login_required
def commitments_create():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()
    selected_lab_id = request.args.get('lab_id', type=int)
    lab_users = User.query.filter_by(lab_id=selected_lab_id, role='lab').all() if selected_lab_id else []

    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        lab_id = request.form.get('lab_id')
        assigned_to = request.form.get('assigned_to')
        start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
        deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%d').date()

        if not assigned_to:
            flash('Phải chọn user chịu trách nhiệm cho cam kết.', 'danger')
            temp_commitment = Commitment(
                title=title,
                description=description,
                lab_id=int(lab_id) if lab_id else None,
                assigned_to=None,
                start_date=start_date,
                deadline=deadline,
                created_by=current_user.id
            )
            lab_users = User.query.filter_by(lab_id=lab_id, role='lab').all() if lab_id else []
            return render_template('commitments/form.html', commitment=temp_commitment, labs=labs,
                                   lab_users=lab_users, action='Tạo Cam kết mới')

        commitment = Commitment(
            title=title,
            description=description,
            lab_id=lab_id,
            assigned_to=assigned_to,
            start_date=start_date,
            deadline=deadline,
            created_by=current_user.id
        )
        db.session.add(commitment)
        db.session.commit()

        ActivityLog.log(current_user.id, 'CREATE', 'Commitment', commitment.id,
                        f'Tạo cam kết: {title} (lab_id={lab_id}, deadline={deadline})', get_client_ip())

        Notification.notify_assignment(commitment, int(assigned_to))

        db.session.commit()
        flash(f'Cam kết "{title}" đã được tạo thành công!', 'success')
        return redirect(url_for('commitments_list'))

    return render_template('commitments/form.html', commitment=None, labs=labs,
                           lab_users=lab_users, action='Tạo Cam kết mới')

@app.route('/commitments/edit/<int:commitment_id>', methods=['GET', 'POST'])
@login_required
def commitments_edit(commitment_id):
    commitment = Commitment.query.get_or_404(commitment_id)

    if not current_user.is_admin() and commitment.lab_id != current_user.lab_id:
        flash('Bạn không có quyền chỉnh sửa cam kết này.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()
    lab_users = User.query.filter_by(lab_id=commitment.lab_id, role='lab').all()

    if request.method == 'POST':
        old_title = commitment.title
        if current_user.is_admin():
            assigned_to = request.form.get('assigned_to')
            commitment.title = request.form.get('title')
            commitment.description = request.form.get('description')
            commitment.lab_id = request.form.get('lab_id')
            commitment.start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
            commitment.deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%d').date()

            if not assigned_to:
                flash('Phải chọn user chịu trách nhiệm cho cam kết.', 'danger')
                lab_users = User.query.filter_by(lab_id=commitment.lab_id, role='lab').all()
                return render_template('commitments/form.html', commitment=commitment, labs=labs,
                                       lab_users=lab_users, action='Chỉnh sửa Cam kết')

            commitment.assigned_to = assigned_to
            Notification.notify_assignment(commitment, int(commitment.assigned_to))

        elif commitment.lab_id == current_user.lab_id:
            assigned_to = request.form.get('assigned_to')
            if not assigned_to:
                flash('Phải chọn user chịu trách nhiệm cho cam kết.', 'danger')
                lab_users = User.query.filter_by(lab_id=commitment.lab_id, role='lab').all()
                return render_template('commitments/form.html', commitment=commitment, labs=labs,
                                       lab_users=lab_users, action='Chỉnh sửa Cam kết')
            commitment.assigned_to = assigned_to
            Notification.notify_assignment(commitment, int(commitment.assigned_to))

        db.session.commit()
        ActivityLog.log(current_user.id, 'UPDATE', 'Commitment', commitment.id,
                        f'Cập nhật cam kết: {old_title} -> {commitment.title}', get_client_ip())
        db.session.commit()
        flash(f'Cam kết đã được cập nhật!', 'success')
        return redirect(url_for('commitments_list'))

    return render_template('commitments/form.html', commitment=commitment, labs=labs,
                           lab_users=lab_users, action='Chỉnh sửa Cam kết')

@app.route('/commitments/detail/<int:commitment_id>')
@login_required
def commitments_detail(commitment_id):
    commitment = Commitment.query.get_or_404(commitment_id)

    if not current_user.is_admin() and commitment.lab_id != current_user.lab_id:
        flash('Bạn không có quyền xem cam kết này.', 'danger')
        return redirect(url_for('dashboard'))

    updates = ProgressUpdate.query.filter_by(commitment_id=commitment_id).order_by(ProgressUpdate.created_at.desc()).all()

    return render_template('commitments/detail.html', commitment=commitment, updates=updates)

@app.route('/commitments/delete/<int:commitment_id>', methods=['POST'])
@login_required
def commitments_delete(commitment_id):
    if not current_user.is_admin():
        return jsonify({'success': False, 'message': 'Không có quyền'})

    commitment = Commitment.query.get_or_404(commitment_id)
    commit_title = commitment.title
    db.session.delete(commitment)
    db.session.commit()

    ActivityLog.log(current_user.id, 'DELETE', 'Commitment', commitment_id,
                    f'Xóa cam kết: {commit_title}', get_client_ip())
    db.session.commit()
    return jsonify({'success': True})

# ============== PROGRESS UPDATE ROUTES ==============

@app.route('/progress/update/<int:commitment_id>', methods=['GET', 'POST'])
@login_required
def progress_update(commitment_id):
    commitment = Commitment.query.get_or_404(commitment_id)

    if not current_user.is_admin() and commitment.lab_id != current_user.lab_id:
        flash('Bạn không có quyền cập nhật cam kết này.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        new_progress = int(request.form.get('progress'))
        notes = request.form.get('notes')
        attachment = None

        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                attachment = filename

        update = ProgressUpdate(
            commitment_id=commitment_id,
            progress=new_progress,
            notes=notes,
            attachment=attachment,
            created_by=current_user.id
        )
        db.session.add(update)

        old_progress = commitment.progress
        old_status = commitment.status
        commitment.progress = new_progress
        commitment.update_status()

        ActivityLog.log(current_user.id, 'PROGRESS', 'Commitment', commitment_id,
                        f'Cập nhật tiến độ: {old_progress}% -> {new_progress}%', get_client_ip())

        # Notify if overdue
        if commitment.status == 'Quá hạn' and old_progress < 100:
            notif_user_id = commitment.assigned_to if commitment.assigned_to else None
            if notif_user_id:
                Notification.notify_overdue(commitment, notif_user_id)

        # Notify creator when commitment is completed
        if commitment.status == 'Hoàn thành' and old_status != 'Hoàn thành':
            notif_user_id = commitment.created_by if commitment.created_by else None
            if notif_user_id:
                Notification.notify_completion(commitment, notif_user_id)

        db.session.commit()
        flash(f'Tiến độ đã được cập nhật lên {new_progress}%!', 'success')
        return redirect(url_for('commitments_detail', commitment_id=commitment_id))

    return render_template('commitments/progress_form.html', commitment=commitment)

# ============== FILE DOWNLOAD ROUTES ==============

@app.route('/uploads/<filename>')
@login_required
def download_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)

# ============== REPORT ROUTES ==============

@app.route('/reports')
@login_required
def reports():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()

    total = Commitment.query.count()
    completed = Commitment.query.filter_by(status='Hoàn thành').count()
    completion_rate = (completed / total * 100) if total > 0 else 0

    lab_data = []
    for lab in labs:
        commits = Commitment.query.filter_by(lab_id=lab.id).all()
        total_lab = len(commits)
        completed_lab = len([c for c in commits if c.status == 'Hoàn thành'])
        overdue_lab = len([c for c in commits if c.status == 'Quá hạn'])
        lab_data.append({
            'name': lab.name,
            'total': total_lab,
            'completed': completed_lab,
            'overdue': overdue_lab,
            'rate': (completed_lab / total_lab * 100) if total_lab > 0 else 0
        })

    status_dist = db.session.query(
        Commitment.status,
        db.func.count(Commitment.id)
    ).group_by(Commitment.status).all()

    chart_labels = [s[0] for s in status_dist]
    chart_data = [s[1] for s in status_dist]

    return render_template('reports/index.html',
                           labs=labs,
                           total=total,
                           completed=completed,
                           completion_rate=completion_rate,
                           lab_data=lab_data,
                           chart_labels=chart_labels,
                           chart_data=chart_data)

# ============== EXPORT ROUTES ==============

def export_to_csv(data):
    """Export data to CSV using Python's built-in csv module."""
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    for row in data['rows']:
        writer.writerow(row)
    output.seek(0)
    return output


@app.route('/export/commitments')
@login_required
def export_commitments():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập.', 'danger')
        return redirect(url_for('dashboard'))

    commitments = Commitment.query.order_by(Commitment.deadline.asc()).all()

    rows = []
    rows.append(['STT', 'Tiêu đề', 'Lab', 'Người phụ trách', 'Tiến độ (%)', 'Ngày bắt đầu', 'Deadline', 'Trạng thái'])
    for idx, c in enumerate(commitments, 1):
        lab_name = c.lab.name if c.lab else '-'
        assignee = c.assignee.username if c.assignee else '-'
        rows.append([
            idx, c.title, lab_name, assignee,
            c.progress,
            c.start_date.strftime('%d/%m/%Y'),
            c.deadline.strftime('%d/%m/%Y'),
            c.status
        ])

    output = export_to_csv({'rows': rows})
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='danh_sach_cam_ket.csv'
    )


@app.route('/export/labs')
@login_required
def export_labs():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()
    rows = []
    rows.append(['STT', 'Tên Lab', 'Quản lý', 'Email', 'Tổng cam kết', 'Hoàn thành', 'Quá hạn', 'Tỷ lệ (%)'])

    for idx, lab in enumerate(labs, 1):
        commits = Commitment.query.filter_by(lab_id=lab.id).all()
        total = len(commits)
        completed = len([c for c in commits if c.status == 'Hoàn thành'])
        overdue = len([c for c in commits if c.status == 'Quá hạn'])
        rate = (completed / total * 100) if total > 0 else 0
        rows.append([
            idx, lab.name, lab.manager_name or '-', lab.email or '-',
            total, completed, overdue, f'{rate:.1f}'
        ])

    output = export_to_csv({'rows': rows})
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='bao_cao_theo_lab.csv'
    )


def get_pdf_font_name():
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return 'Helvetica'

    font_name = 'UnicodeFont'
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name

    windir = os.environ.get('WINDIR', r'C:\Windows')
    font_files = [
        os.path.join(windir, 'Fonts', 'arialuni.ttf'),
        os.path.join(windir, 'Fonts', 'ARIALUNI.TTF'),
        os.path.join(windir, 'Fonts', 'SegoeUI.ttf'),
        os.path.join(windir, 'Fonts', 'calibri.ttf'),
        os.path.join(windir, 'Fonts', 'arial.ttf'),
        os.path.join(windir, 'Fonts', 'times.ttf'),
        os.path.join(windir, 'Fonts', 'DejaVuSans.ttf'),
    ]

    for font_path in font_files:
        try:
            if font_path and os.path.exists(font_path):
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                pdfmetrics.registerFontFamily(font_name,
                                              normal=font_name,
                                              bold=font_name,
                                              italic=font_name,
                                              boldItalic=font_name)
                return font_name
        except Exception:
            continue

    return 'Helvetica'


def export_to_pdf(data, title, filename):
    """Export data to PDF using reportlab"""
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        return None

    font_name = get_pdf_font_name()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm)
    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle('Title', parent=styles['Title'], fontName=font_name, fontSize=16, spaceAfter=20, alignment=1)
    elements.append(Paragraph(title, title_style))

    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D6EFD')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), font_name),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('FONTNAME', (0, 1), (-1, -1), font_name),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8F9FA')]),
    ]))
    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return buffer


def export_dashboard_to_pdf(summary_data, status_rows, lab_rows, upcoming_rows):
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    except ImportError:
        return None

    font_name = get_pdf_font_name()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1*cm, leftMargin=1*cm, topMargin=1*cm, bottomMargin=1*cm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Title'], fontName=font_name, fontSize=20, alignment=1, textColor=colors.HexColor('#0D6EFD'), spaceAfter=16)
    header_style = ParagraphStyle('Header', parent=styles['Heading2'], fontName=font_name, fontSize=14, textColor=colors.HexColor('#083E7D'), spaceAfter=8)
    normal_style = ParagraphStyle('Normal', parent=styles['BodyText'], fontName=font_name, fontSize=10, leading=14)

    elements = [
        Paragraph('BÁO CÁO TOÀN BỘ DASHBOARD', title_style),
        Paragraph(f'Ngày tạo: {datetime.now().strftime("%d/%m/%Y %H:%M")}', normal_style),
        Spacer(1, 12)
    ]

    elements.append(Paragraph('1. Tổng quan', header_style))
    summary_table = Table(summary_data, colWidths=[90] * len(summary_data[0]))
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D6EFD')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), font_name),
        ('FONTNAME', (0, 1), (-1, 1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#E9F2FF')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey)
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 18))

    elements.append(Paragraph('2. Phân bố theo trạng thái', header_style))
    status_table = Table(status_rows, colWidths=[220, 120])
    status_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#198754')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), font_name),
        ('FONTNAME', (0, 1), (-1, -1), font_name),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8F9FA')])
    ]))
    elements.append(status_table)
    elements.append(Spacer(1, 18))

    elements.append(Paragraph('3. Chi tiết theo Lab', header_style))
    lab_table = Table(lab_rows, colWidths=[35, 145, 75, 75, 75, 75])
    lab_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0D6EFD')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), font_name),
        ('FONTNAME', (0, 1), (-1, -1), font_name),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8F9FA')])
    ]))
    elements.append(lab_table)
    elements.append(Spacer(1, 18))

    if upcoming_rows:
        elements.append(Paragraph('4. Cam kết sắp tới hạn', header_style))
        upcoming_table = Table(upcoming_rows, colWidths=[35, 220, 90, 90, 90])
        upcoming_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#DC3545')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), font_name),
            ('FONTNAME', (0, 1), (-1, -1), font_name),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F8F9FA')])
        ]))
        elements.append(upcoming_table)

    doc.build(elements)
    buffer.seek(0)
    return buffer


@app.route('/export/report/pdf')
@login_required
def export_report_pdf():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập.', 'danger')
        return redirect(url_for('dashboard'))

    today = datetime.utcnow().date()
    total = Commitment.query.count()
    active = Commitment.query.filter(Commitment.status == 'Đang thực hiện').count()
    completed = Commitment.query.filter_by(status='Hoàn thành').count()
    overdue = Commitment.query.filter_by(status='Quá hạn').count()
    new_commits = Commitment.query.filter_by(status='Mới').count()
    completion_rate = (completed / total * 100) if total > 0 else 0

    status_dist = db.session.query(
        Commitment.status,
        db.func.count(Commitment.id)
    ).group_by(Commitment.status).all()

    labs = Lab.query.all()
    lab_rows = [['STT', 'Tên Lab', 'Tổng cam kết', 'Hoàn thành', 'Quá hạn', 'Tỷ lệ (%)']]
    for idx, lab in enumerate(labs, 1):
        commits = Commitment.query.filter_by(lab_id=lab.id).all()
        total_lab = len(commits)
        completed_lab = len([c for c in commits if c.status == 'Hoàn thành'])
        overdue_lab = len([c for c in commits if c.status == 'Quá hạn'])
        rate = (completed_lab / total_lab * 100) if total_lab > 0 else 0
        lab_rows.append([idx, lab.name, total_lab, completed_lab, overdue_lab, f'{rate:.1f}'])

    upcoming_commitments = Commitment.query.filter(
        Commitment.deadline <= today + timedelta(days=7),
        Commitment.deadline >= today,
        Commitment.status.in_(['Mới', 'Đang thực hiện'])
    ).order_by(Commitment.deadline.asc()).limit(10).all()
    upcoming_rows = [['STT', 'Tiêu đề', 'Lab', 'Deadline', 'Trạng thái']]
    for idx, c in enumerate(upcoming_commitments, 1):
        lab_name = c.lab.name if c.lab else '-'
        upcoming_rows.append([
            idx,
            c.title,
            lab_name,
            c.deadline.strftime('%d/%m/%Y'),
            c.status
        ])

    summary_data = [
        ['Tổng cam kết', 'Hoàn thành', 'Đang thực hiện', 'Quá hạn', 'Mới', 'Tỷ lệ (%)'],
        [
            str(total),
            str(completed),
            str(active),
            str(overdue),
            str(new_commits),
            f'{completion_rate:.1f}%'
        ]
    ]

    status_rows = [['Trạng thái', 'Số lượng']] + [[status, count] for status, count in status_dist]

    output = export_dashboard_to_pdf(summary_data, status_rows, lab_rows, upcoming_rows)

    if output:
        return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='bao_cao_dashboard.pdf')

    flash('PDF export unavailable: reportlab chưa được cài đặt.', 'danger')
    return redirect(url_for('reports'))


# ============== ACTIVITY LOG ROUTES ==============

@app.route('/activity-logs')
@login_required
def activity_logs():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    page = request.args.get('page', 1, type=int)
    per_page = 50

    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('activity_logs/list.html', logs=logs)


# ============== API ROUTES ==============

@app.route('/api/stats')
@login_required
def api_stats():
    if current_user.is_admin():
        commitments = Commitment.query.all()
    else:
        commitments = Commitment.query.filter_by(lab_id=current_user.lab_id).all()

    stats = {
        'total': len(commitments),
        'by_status': {},
        'avg_progress': 0
    }

    for c in commitments:
        stats['by_status'][c.status] = stats['by_status'].get(c.status, 0) + 1

    if commitments:
        stats['avg_progress'] = sum(c.progress for c in commitments) / len(commitments)

    return jsonify(stats)

@app.route('/api/commitments/<int:commitment_id>/timeline')
@login_required
def api_timeline(commitment_id):
    commitment = Commitment.query.get_or_404(commitment_id)
    updates = ProgressUpdate.query.filter_by(commitment_id=commitment_id).order_by(ProgressUpdate.created_at).all()

    timeline = [{
        'date': commitment.start_date.isoformat(),
        'progress': 0,
        'note': 'Bắt đầu'
    }]
    timeline.extend([{
        'date': u.created_at.isoformat(),
        'progress': u.progress,
        'note': u.notes
    } for u in updates])

    return jsonify(timeline)

@app.route('/api/labs/<int:lab_id>/users')
@login_required
def api_lab_users(lab_id):
    users = User.query.filter_by(lab_id=lab_id, role='lab').all()
    return jsonify([{'id': u.id, 'username': u.username} for u in users])

# ============== ERROR HANDLERS ==============

@app.errorhandler(404)
def not_found(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def server_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500

# ============== INIT DATABASE ==============

def ensure_tables():
    """Create new tables (notifications, activity_logs) if they don't exist."""
    with app.app_context():
        inspector = db.inspect(db.engine)
        existing_tables = inspector.get_table_names()

        # Ensure new tables are created
        tables_to_check = ['notifications', 'activity_logs']
        for table in tables_to_check:
            if table not in existing_tables:
                db.create_all()
                print(f'Created new table: {table}')
                break

        # Migration for old commitments table
        if 'commitments' in existing_tables:
            columns = [c['name'] for c in inspector.get_columns('commitments')]
            if 'assigned_to' not in columns:
                try:
                    db.session.execute('ALTER TABLE commitments ADD COLUMN assigned_to INTEGER')
                    db.session.commit()
                    print('Migration: added commitments.assigned_to column')
                except Exception as e:
                    db.session.rollback()
                    print(f'Migration warning: could not add assigned_to column: {e}')


def init_db():
    """Initialize database with tables and default admin user"""
    with app.app_context():
        db.create_all()
        ensure_tables()

        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print('Default admin user created: admin / admin123')

        if Lab.query.count() == 0:
            sample_labs = [
                Lab(name='Lab A', description='Phòng thí nghiệm Khoa học Máy tính', manager_name='TS. Nguyễn Văn A', email='labA@ptit.edu.vn'),
                Lab(name='Lab B', description='Phòng thí nghiệm Mạng và Truyền thông', manager_name='ThS. Trần Văn B', email='labB@ptit.edu.vn'),
                Lab(name='Lab C', description='Phòng thí nghiệm Điện tử Viễn thông', manager_name='PGS.TS. Lê Văn C', email='labC@ptit.edu.vn'),
            ]
            for lab in sample_labs:
                db.session.add(lab)
            db.session.commit()
            print(f'{len(sample_labs)} sample labs created')


with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
