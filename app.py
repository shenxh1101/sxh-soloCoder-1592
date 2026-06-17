import os
import csv
import io
import random
import string
from functools import wraps
from datetime import datetime, timedelta, date
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, send_file
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'express-locker-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///locker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


OVERDUE_HOURS = 24


class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)


class Locker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    status = db.Column(db.String(20), default='idle')
    size = db.Column(db.String(10), default='medium')


class Package(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_no = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    pickup_code = db.Column(db.String(6), nullable=False)
    stored_at = db.Column(db.DateTime, default=datetime.now)
    picked_at = db.Column(db.DateTime)
    notified_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='stored')

    locker_id = db.Column(db.Integer, db.ForeignKey('locker.id'))
    locker = db.relationship('Locker', backref=db.backref('current_package', uselist=False))


class DailyStats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stat_date = db.Column(db.Date, unique=True, nullable=False)
    stored_count = db.Column(db.Integer, default=0)
    picked_count = db.Column(db.Integer, default=0)
    occupied_hours = db.Column(db.Float, default=0)
    total_locker_count = db.Column(db.Integer, default=24)

    @property
    def utilization_rate(self):
        total_hours = self.total_locker_count * 24
        if total_hours == 0:
            return 0
        return round((self.occupied_hours / total_hours) * 100, 1)


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('请先登录管理员账号', 'error')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def generate_pickup_code():
    return ''.join(random.choices(string.digits, k=6))


def get_or_create_daily_stats(d=None):
    if d is None:
        d = date.today()
    stats = DailyStats.query.filter_by(stat_date=d).first()
    if not stats:
        total = Locker.query.count() or 24
        stats = DailyStats(stat_date=d, total_locker_count=total)
        db.session.add(stats)
        db.session.flush()
    return stats


def add_occupied_hours_for_today():
    now = datetime.now()
    today_stats = get_or_create_daily_stats()
    occupied_lockers = Locker.query.filter_by(status='occupied').count()
    today_stats.occupied_hours += occupied_lockers


def init_lockers():
    if Locker.query.count() == 0:
        sizes = ['small', 'medium', 'large']
        for i in range(1, 25):
            size = sizes[(i - 1) % 3]
            locker = Locker(code=f'A{i:02d}', size=size)
            db.session.add(locker)
        db.session.commit()


def init_admin():
    if Admin.query.count() == 0:
        admin = Admin(
            username='admin',
            password_hash=generate_password_hash('admin123')
        )
        db.session.add(admin)
        db.session.commit()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        admin = Admin.query.filter_by(username=username).first()
        if admin and check_password_hash(admin.password_hash, password):
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            flash('登录成功', 'success')
            next_url = request.args.get('next') or url_for('admin')
            return redirect(next_url)
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('已退出登录', 'success')
    return redirect(url_for('index'))


@app.route('/store', methods=['GET', 'POST'])
def store_package():
    if request.method == 'POST':
        locker_id = request.form.get('locker_id')
        tracking_no = request.form.get('tracking_no')
        phone = request.form.get('phone')

        if not locker_id or not tracking_no or not phone:
            flash('请填写所有信息', 'error')
            return redirect(url_for('store_package'))

        locker = Locker.query.get(locker_id)
        if not locker or locker.status != 'idle':
            flash('该格口不可用', 'error')
            return redirect(url_for('store_package'))

        pickup_code = generate_pickup_code()
        while Package.query.filter_by(pickup_code=pickup_code, status='stored').first():
            pickup_code = generate_pickup_code()

        package = Package(
            tracking_no=tracking_no,
            phone=phone,
            pickup_code=pickup_code,
            locker_id=locker.id
        )
        locker.status = 'occupied'
        db.session.add(package)

        today_stats = get_or_create_daily_stats()
        today_stats.stored_count += 1

        db.session.commit()

        return render_template('store_success.html',
                             locker=locker,
                             package=package)

    idle_lockers = Locker.query.filter_by(status='idle').all()
    return render_template('store.html', lockers=idle_lockers)


@app.route('/pickup', methods=['GET', 'POST'])
def pickup_package():
    if request.method == 'POST':
        pickup_code = request.form.get('pickup_code')
        phone = request.form.get('phone')

        if not pickup_code or not phone:
            flash('请填写取件码和手机号', 'error')
            return redirect(url_for('pickup_package'))

        package = Package.query.filter_by(
            pickup_code=pickup_code,
            status='stored'
        ).first()

        if not package:
            flash('取件码无效', 'error')
            return redirect(url_for('pickup_package'))

        if package.phone != phone:
            flash('手机号不匹配', 'error')
            return redirect(url_for('pickup_package'))

        package.status = 'picked'
        package.picked_at = datetime.now()

        locker = Locker.query.get(package.locker_id)
        if locker:
            locker.status = 'idle'

        today_stats = get_or_create_daily_stats()
        today_stats.picked_count += 1

        db.session.commit()

        return render_template('pickup_success.html', package=package)

    return render_template('pickup.html')


@app.route('/admin')
@admin_required
def admin():
    filter_phone = request.args.get('phone', '').strip()
    filter_tracking = request.args.get('tracking', '').strip()
    filter_locker = request.args.get('locker', '').strip()
    filter_status = request.args.get('status', '').strip()

    query = Package.query

    if filter_phone:
        query = query.filter(Package.phone.contains(filter_phone))
    if filter_tracking:
        query = query.filter(Package.tracking_no.contains(filter_tracking))
    if filter_locker:
        query = query.join(Locker).filter(Locker.code.contains(filter_locker))
    if filter_status:
        query = query.filter(Package.status == filter_status)

    packages = query.order_by(Package.stored_at.desc()).limit(200).all()

    lockers = Locker.query.all()
    now = datetime.now()

    for pkg in packages:
        if pkg.status == 'stored' and pkg.stored_at:
            elapsed_hours = (now - pkg.stored_at).total_seconds() / 3600
            pkg.overdue_hours = max(0, int(elapsed_hours - OVERDUE_HOURS))
        else:
            pkg.overdue_hours = 0

    for locker in lockers:
        locker.current_pkg = Package.query.filter_by(
            locker_id=locker.id, status='stored'
        ).first()

    stats = {
        'total_lockers': len(lockers),
        'idle_lockers': len([l for l in lockers if l.status == 'idle']),
        'occupied_lockers': len([l for l in lockers if l.status == 'occupied']),
        'fault_lockers': len([l for l in lockers if l.status == 'fault']),
        'stored_packages': Package.query.filter_by(status='stored').count(),
        'overdue_packages': sum(
            1 for p in Package.query.filter_by(status='stored').all()
            if (now - p.stored_at).total_seconds() > OVERDUE_HOURS * 3600
        )
    }

    return render_template('admin.html',
                         lockers=lockers,
                         packages=packages,
                         stats=stats,
                         now=now,
                         filter_phone=filter_phone,
                         filter_tracking=filter_tracking,
                         filter_locker=filter_locker,
                         filter_status=filter_status)


@app.route('/admin/overdue')
@admin_required
def overdue_list():
    now = datetime.now()
    threshold = now - timedelta(hours=OVERDUE_HOURS)

    overdue_packages = Package.query.filter(
        Package.status == 'stored',
        Package.stored_at <= threshold
    ).order_by(Package.stored_at.asc()).all()

    for pkg in overdue_packages:
        elapsed = (now - pkg.stored_at).total_seconds() / 3600
        pkg.overdue_hours = int(elapsed - OVERDUE_HOURS)
        pkg.total_hours = int(elapsed)
        locker = Locker.query.get(pkg.locker_id)
        pkg.locker_code = locker.code if locker else '-'

    notified_count = len([p for p in overdue_packages if p.notified_at])
    unnotified_count = len(overdue_packages) - notified_count

    return render_template('overdue.html',
                         packages=overdue_packages,
                         notified_count=notified_count,
                         unnotified_count=unnotified_count,
                         now=now)


@app.route('/admin/overdue/<int:package_id>/notify', methods=['POST'])
@admin_required
def mark_notified(package_id):
    pkg = Package.query.get_or_404(package_id)
    pkg.notified_at = datetime.now()
    db.session.commit()
    return jsonify({
        'success': True,
        'message': '已记录通知时间',
        'notified_at': pkg.notified_at.strftime('%Y-%m-%d %H:%M')
    })


@app.route('/admin/locker/<int:locker_id>/open', methods=['POST'])
@admin_required
def open_locker(locker_id):
    locker = Locker.query.get_or_404(locker_id)
    return jsonify({
        'success': True,
        'message': f'格口 {locker.code} 已打开'
    })


@app.route('/admin/locker/<int:locker_id>/toggle_fault', methods=['POST'])
@admin_required
def toggle_fault(locker_id):
    locker = Locker.query.get_or_404(locker_id)
    if locker.status == 'fault':
        locker.status = 'idle'
        message = f'格口 {locker.code} 已恢复正常'
    elif locker.status == 'occupied':
        return jsonify({'success': False, 'message': '格口正在使用中，无法标记故障'})
    else:
        locker.status = 'fault'
        message = f'格口 {locker.code} 已标记为故障'
    db.session.commit()
    return jsonify({'success': True, 'message': message})


def calc_day_utilization(target_date):
    day_start = datetime.combine(target_date, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    total_lockers = Locker.query.count() or 24

    packages = Package.query.filter(
        (Package.stored_at < day_end) &
        ((Package.picked_at.is_(None)) | (Package.picked_at > day_start))
    ).all()

    total_occupied_hours = 0.0
    for pkg in packages:
        occupy_start = max(pkg.stored_at, day_start)
        if pkg.picked_at:
            occupy_end = min(pkg.picked_at, day_end)
        else:
            occupy_end = min(datetime.now(), day_end)
        if occupy_end > occupy_start:
            hours = (occupy_end - occupy_start).total_seconds() / 3600
            total_occupied_hours += hours

    possible_hours = total_lockers * 24
    if target_date == date.today():
        now = datetime.now()
        current_hour_elapsed = (now - day_start).total_seconds() / 3600
        current_hour_elapsed = max(min(current_hour_elapsed, 24), 0.5)
        possible_hours = total_lockers * current_hour_elapsed

    if possible_hours == 0:
        return 0.0
    return round((total_occupied_hours / possible_hours) * 100, 1)


@app.route('/admin/report')
@admin_required
def report():
    today = date.today()
    days = []

    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)

        stored_count = Package.query.filter(
            Package.stored_at >= day_start,
            Package.stored_at < day_end
        ).count()

        picked_count = Package.query.filter(
            Package.picked_at >= day_start,
            Package.picked_at < day_end
        ).count()

        utilization = calc_day_utilization(day)

        days.append({
            'date': day.strftime('%Y-%m-%d'),
            'is_today': (day == today),
            'stored': stored_count,
            'picked': picked_count,
            'utilization': utilization
        })

    total_stored = sum(d['stored'] for d in days)
    total_picked = sum(d['picked'] for d in days)
    avg_utilization = round(sum(d['utilization'] for d in days) / len(days), 1) if days else 0

    return render_template('report.html',
                         days=days,
                         total_stored=total_stored,
                         total_picked=total_picked,
                         avg_utilization=avg_utilization)


@app.route('/admin/export/<range_type>')
@admin_required
def export_csv(range_type):
    now = datetime.now()
    today_start = datetime.combine(date.today(), datetime.min.time())

    if range_type == 'today':
        start_time = today_start
        end_time = datetime.combine(date.today(), datetime.max.time())
        filename = f'packages_today_{now.strftime("%Y%m%d")}.csv'
    elif range_type == '7days':
        start_time = datetime.combine(date.today() - timedelta(days=6), datetime.min.time())
        end_time = datetime.combine(date.today(), datetime.max.time())
        filename = f'packages_7days_{now.strftime("%Y%m%d")}.csv'
    else:
        return jsonify({'success': False, 'message': '无效的导出范围'}), 400

    packages = Package.query.filter(
        Package.stored_at >= start_time,
        Package.stored_at <= end_time
    ).order_by(Package.stored_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        '格口编号', '快递单号', '手机号', '取件码',
        '存入时间', '取件时间', '状态', '超时时长(小时)', '通知时间'
    ])

    for pkg in packages:
        locker = Locker.query.get(pkg.locker_id)
        locker_code = locker.code if locker else '-'

        overdue_hours = 0
        if pkg.status == 'stored' and pkg.stored_at:
            elapsed = (now - pkg.stored_at).total_seconds() / 3600
            overdue_hours = max(0, int(elapsed - OVERDUE_HOURS))

        status_text = '待取件' if pkg.status == 'stored' else '已取件'
        stored_str = pkg.stored_at.strftime('%Y-%m-%d %H:%M:%S') if pkg.stored_at else ''
        picked_str = pkg.picked_at.strftime('%Y-%m-%d %H:%M:%S') if pkg.picked_at else ''
        notified_str = pkg.notified_at.strftime('%Y-%m-%d %H:%M:%S') if pkg.notified_at else ''

        writer.writerow([
            locker_code, pkg.tracking_no, pkg.phone, pkg.pickup_code,
            stored_str, picked_str, status_text, overdue_hours, notified_str
        ])

    output.seek(0)
    mem = io.BytesIO()
    mem.write(output.getvalue().encode('utf-8-sig'))
    mem.seek(0)

    return send_file(
        mem,
        mimetype='text/csv; charset=utf-8',
        as_attachment=True,
        download_name=filename
    )


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_lockers()
        init_admin()
    app.run(debug=True, use_reloader=False, host='0.0.0.0', port=5000)
