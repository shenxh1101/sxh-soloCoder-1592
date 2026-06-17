import os
import random
import string
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SECRET_KEY'] = 'express-locker-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///locker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


class Locker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    status = db.Column(db.String(20), default='idle')
    size = db.Column(db.String(10), default='medium')

    package = db.relationship('Package', backref='locker', uselist=False)


class Package(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_no = db.Column(db.String(50), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    pickup_code = db.Column(db.String(6), nullable=False)
    stored_at = db.Column(db.DateTime, default=datetime.now)
    picked_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='stored')

    locker_id = db.Column(db.Integer, db.ForeignKey('locker.id'))


def generate_pickup_code():
    return ''.join(random.choices(string.digits, k=6))


def init_lockers():
    if Locker.query.count() == 0:
        sizes = ['small', 'medium', 'large']
        for i in range(1, 25):
            size = sizes[(i - 1) % 3]
            locker = Locker(code=f'A{i:02d}', size=size)
            db.session.add(locker)
        db.session.commit()


@app.route('/')
def index():
    return render_template('index.html')


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
        if package.locker:
            package.locker.status = 'idle'
        db.session.commit()

        return render_template('pickup_success.html', package=package)

    return render_template('pickup.html')


@app.route('/admin')
def admin():
    lockers = Locker.query.all()
    packages = Package.query.order_by(Package.stored_at.desc()).limit(50).all()

    now = datetime.now()
    for pkg in packages:
        if pkg.status == 'stored' and pkg.stored_at:
            pkg.overdue_hours = max(0, int((now - pkg.stored_at).total_seconds() / 3600) - 24)
        else:
            pkg.overdue_hours = 0

    stats = {
        'total_lockers': len(lockers),
        'idle_lockers': len([l for l in lockers if l.status == 'idle']),
        'occupied_lockers': len([l for l in lockers if l.status == 'occupied']),
        'fault_lockers': len([l for l in lockers if l.status == 'fault']),
        'stored_packages': len([p for p in packages if p.status == 'stored']),
        'overdue_packages': len([p for p in packages if p.status == 'stored' and (now - p.stored_at).total_seconds() > 86400])
    }

    return render_template('admin.html',
                         lockers=lockers,
                         packages=packages,
                         stats=stats,
                         now=now)


@app.route('/admin/locker/<int:locker_id>/open', methods=['POST'])
def open_locker(locker_id):
    locker = Locker.query.get_or_404(locker_id)
    return jsonify({
        'success': True,
        'message': f'格口 {locker.code} 已打开'
    })


@app.route('/admin/locker/<int:locker_id>/toggle_fault', methods=['POST'])
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


@app.route('/admin/report')
def report():
    today = datetime.now().date()
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

        total_lockers = Locker.query.count()
        occupied_lockers = Locker.query.filter_by(status='occupied').count()
        utilization = round((occupied_lockers / total_lockers * 100), 1) if total_lockers > 0 else 0

        days.append({
            'date': day.strftime('%Y-%m-%d'),
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


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        init_lockers()
    app.run(debug=True, host='0.0.0.0', port=5000)
