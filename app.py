from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_file, send_from_directory
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import os
import io

from config import Config
from models import db, User, Lab, Commitment, ProgressUpdate

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
            flash('Đăng nhập thành công!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Tên đăng nhập hoặc mật khẩu không đúng.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Đã đăng xuất.', 'info')
    return redirect(url_for('login'))

# ============== DASHBOARD ROUTES ==============

@app.route('/dashboard')
@login_required
def dashboard():
    today = datetime.utcnow().date()

    if current_user.is_admin():
        # Admin sees all
        total = Commitment.query.count()
        active = Commitment.query.filter(Commitment.status == 'Đang thực hiện').count()
        completed = Commitment.query.filter(Commitment.status == 'Hoàn thành').count()
        overdue = Commitment.query.filter(Commitment.status == 'Quá hạn').count()
        new_commits = Commitment.query.filter(Commitment.status == 'Mới').count()

        recent = Commitment.query.order_by(Commitment.updated_at.desc()).limit(10).all()

        # Stats by lab
        lab_stats = db.session.query(
            Lab.name,
            db.func.count(Commitment.id).label('total'),
            db.func.sum(db.case((Commitment.progress >= 100, 1), else_=0)).label('completed')
        ).outerjoin(Commitment).group_by(Lab.id).all()

        # Chart data
        status_chart = {
            'labels': ['Mới', 'Đang thực hiện', 'Hoàn thành', 'Quá hạn'],
            'data': [new_commits, active, completed, overdue]
        }

        labs = Lab.query.all()
        commitments_by_lab = []
        for lab in labs:
            count = Commitment.query.filter_by(lab_id=lab.id).count()
            commitments_by_lab.append({'name': lab.name, 'count': count})

    else:
        # Lab user sees only their lab
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

    return render_template('dashboard.html',
                           total=total, active=active, completed=completed,
                           overdue=overdue, recent=recent,
                           status_chart=status_chart,
                           labs=labs, lab_stats=lab_stats,
                           commitments_by_lab=commitments_by_lab)

@app.route('/my-tasks')
@login_required
def my_tasks():
    """Page showing tasks assigned to current user"""
    if current_user.is_admin():
        flash('Trang này dành cho user Lab.', 'info')
        return redirect(url_for('dashboard'))

    today = datetime.utcnow().date()

    # Get tasks assigned to this user
    my_tasks_list = Commitment.query.filter_by(assigned_to=current_user.id).order_by(Commitment.deadline.asc()).all()

    # Stats
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
        lab.name = request.form.get('name')
        lab.description = request.form.get('description')
        lab.manager_name = request.form.get('manager_name')
        lab.email = request.form.get('email')

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

    # Delete related commitments first
    Commitment.query.filter_by(lab_id=lab_id).delete()

    # Delete related users (set lab_id to null)
    User.query.filter_by(lab_id=lab_id).update({'lab_id': None})

    # Delete the lab
    db.session.delete(lab)
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
        user.role = request.form.get('role')
        user.lab_id = request.form.get('lab_id') if user.role == 'lab' else None

        new_password = request.form.get('password')
        if new_password:
            user.set_password(new_password)

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

    user_name = user.username
    db.session.delete(user)
    db.session.commit()

    flash(f'User "{user_name}" đã được xóa thành công!', 'success')
    return redirect(url_for('users_list'))

# ============== COMMITMENT ROUTES ==============

@app.route('/commitments')
@login_required
def commitments_list():
    query = Commitment.query

    # Filter by lab for lab users
    if not current_user.is_admin():
        query = query.filter_by(lab_id=current_user.lab_id)

    # Apply filters
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
        assigned_to = request.form.get('assigned_to') or None
        start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
        deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%d').date()

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

        flash(f'Cam kết "{title}" đã được tạo thành công!', 'success')
        return redirect(url_for('commitments_list'))

    return render_template('commitments/form.html', commitment=None, labs=labs,
                           lab_users=lab_users, action='Tạo Cam kết mới')

@app.route('/commitments/edit/<int:commitment_id>', methods=['GET', 'POST'])
@login_required
def commitments_edit(commitment_id):
    commitment = Commitment.query.get_or_404(commitment_id)

    # Only admin can edit all, lab can only edit their own
    if not current_user.is_admin() and commitment.lab_id != current_user.lab_id:
        flash('Bạn không có quyền chỉnh sửa cam kết này.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()
    lab_users = User.query.filter_by(lab_id=commitment.lab_id, role='lab').all()

    if request.method == 'POST':
        if current_user.is_admin():
            commitment.title = request.form.get('title')
            commitment.description = request.form.get('description')
            commitment.lab_id = request.form.get('lab_id')
            commitment.assigned_to = request.form.get('assigned_to') or None
            commitment.start_date = datetime.strptime(request.form.get('start_date'), '%Y-%m-%d').date()
            commitment.deadline = datetime.strptime(request.form.get('deadline'), '%Y-%m-%d').date()

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
    db.session.delete(commitment)
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

        # Handle file upload
        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                attachment = filename

        # Create progress update record
        update = ProgressUpdate(
            commitment_id=commitment_id,
            progress=new_progress,
            notes=notes,
            attachment=attachment,
            created_by=current_user.id
        )
        db.session.add(update)

        # Update commitment progress and status
        commitment.progress = new_progress
        commitment.update_status()

        db.session.commit()
        flash(f'Tiến độ đã được cập nhật lên {new_progress}%!', 'success')
        return redirect(url_for('commitments_detail', commitment_id=commitment_id))

    return render_template('commitments/progress_form.html', commitment=commitment)

# ============== FILE DOWNLOAD ROUTES ==============

@app.route('/uploads/<filename>')
@login_required
def download_file(filename):
    """Serve uploaded attachment files to authenticated users."""
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        filename,
        as_attachment=True
    )

# ============== REPORT ROUTES ==============

@app.route('/reports')
@login_required
def reports():
    if not current_user.is_admin():
        flash('Bạn không có quyền truy cập trang này.', 'danger')
        return redirect(url_for('dashboard'))

    labs = Lab.query.all()

    # Overall stats
    total = Commitment.query.count()
    completed = Commitment.query.filter_by(status='Hoàn thành').count()
    completion_rate = (completed / total * 100) if total > 0 else 0

    # Stats by lab
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

    # Chart data
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
    """Get all lab users for a specific lab"""
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

def ensure_commitments_assigned_to_column():
    """Ensure historic sqlite tables from old schema include the assigned_to column."""
    with app.app_context():
        inspector = db.inspect(db.engine)
        if 'commitments' not in inspector.get_table_names():
            return

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
        ensure_commitments_assigned_to_column()

        # Check if admin exists
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print('Default admin user created: admin / admin123')

        # Create sample labs if none exist
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


# Initialize DB when app is created (flask run / WSGI + direct run)
with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)