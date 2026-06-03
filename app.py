from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_cors import CORS
import sqlite3
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'orders.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT UNIQUE NOT NULL,
            order_name TEXT NOT NULL,
            functions TEXT NOT NULL,
            extra_features TEXT,
            processor_model TEXT,
            processor_note TEXT,
            total_cost REAL DEFAULT 0,
            deposit REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            cancel_requested INTEGER DEFAULT 0,
            cancel_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (order_no) REFERENCES orders(order_no)
        )
    ''')
    conn.commit()
    conn.close()


init_db()


@app.route('/')
def index():
    return render_template('mobile.html')


@app.route('/admin')
def admin_page():
    return render_template('admin.html')


@app.route('/manifest.json')
def manifest():
    m = {
        "name": "订单管理客户端",
        "short_name": "订单客户端",
        "start_url": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f5f5f5",
        "theme_color": "#1976d2",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    return jsonify(m)


@app.route('/admin/manifest.json')
def admin_manifest():
    m = {
        "name": "订单管理端",
        "short_name": "订单管理",
        "start_url": "/admin",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f5f5f5",
        "theme_color": "#d32f2f",
        "icons": [
            {"src": "/icon-admin-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-admin-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    return jsonify(m)


@app.route('/sw.js')
def service_worker():
    sw_code = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
self.addEventListener('fetch', e => {
    e.respondWith(fetch(e.request).catch(() => new Response('离线状态，请检查网络', {headers: {'Content-Type': 'text/plain; charset=utf-8'}})));
});
"""
    return app.response_class(sw_code, mimetype='application/javascript')


@app.route('/icon-192.png')
@app.route('/icon-512.png')
@app.route('/icon-admin-192.png')
@app.route('/icon-admin-512.png')
def generate_icon():
    import io
    from PIL import Image, ImageDraw, ImageFont
    
    path = request.path
    size = 512 if '512' in path else 192
    is_admin = 'admin' in path
    
    img = Image.new('RGB', (size, size), '#1976d2' if not is_admin else '#d32f2f')
    draw = ImageDraw.Draw(img)
    
    font_size = size // 3
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except:
        font = ImageFont.load_default()
    
    text = "管" if is_admin else "订"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - bbox[1]), text, fill='white', font=font)
    
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return app.response_class(buf.getvalue(), mimetype='image/png')


def build_order_response(conn, order):
    order_dict = dict(order)
    settlements = conn.execute(
        'SELECT * FROM settlements WHERE order_no = ? ORDER BY created_at DESC',
        (order_dict['order_no'],)
    ).fetchall()
    order_dict['settlements'] = [dict(s) for s in settlements]
    total_settled = sum(s['amount'] for s in settlements)
    order_dict['total_settled'] = total_settled
    order_dict['remaining'] = order_dict['total_cost'] - order_dict['deposit'] - total_settled
    return order_dict


@app.route('/api/orders', methods=['POST'])
def create_order():
    data = request.json
    conn = get_db()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn.execute('''
            INSERT INTO orders (order_no, order_name, functions, extra_features,
                              processor_model, processor_note, total_cost, deposit,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['order_no'], data['order_name'], data['functions'],
            data.get('extra_features', ''), data.get('processor_model', ''),
            data.get('processor_note', ''), data.get('total_cost', 0),
            data.get('deposit', 0), now, now
        ))
        conn.commit()
        return jsonify({'success': True, 'message': '订单创建成功'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': '订单号已存在'}), 400
    finally:
        conn.close()


@app.route('/api/orders', methods=['GET'])
def list_orders():
    conn = get_db()
    orders = conn.execute('SELECT * FROM orders ORDER BY created_at DESC').fetchall()
    result = [build_order_response(conn, o) for o in orders]
    conn.close()
    return jsonify(result)


@app.route('/api/orders/<order_no>', methods=['GET'])
def get_order(order_no):
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    result = build_order_response(conn, order)
    conn.close()
    return jsonify(result)


@app.route('/api/orders/<order_no>/settle', methods=['POST'])
def add_settlement(order_no):
    data = request.json
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    if order['status'] != 'active':
        conn.close()
        return jsonify({'success': False, 'message': '该订单已作废，无法结算'}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        INSERT INTO settlements (order_no, amount, note, created_at)
        VALUES (?, ?, ?, ?)
    ''', (order_no, data['amount'], data.get('note', ''), now))
    conn.execute('UPDATE orders SET updated_at = ? WHERE order_no = ?', (now, order_no))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '结算记录添加成功'})


@app.route('/api/orders/<order_no>/cancel', methods=['POST'])
def request_cancel(order_no):
    data = request.json
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    if order['status'] != 'active':
        conn.close()
        return jsonify({'success': False, 'message': '该订单已作废'}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders SET cancel_requested = 1, cancel_reason = ?, updated_at = ?
        WHERE order_no = ?
    ''', (data.get('reason', ''), now, order_no))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '作废申请已提交，等待服务端审核'})


@app.route('/api/orders/<order_no>/approve-cancel', methods=['POST'])
def approve_cancel(order_no):
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404
    if not order['cancel_requested']:
        conn.close()
        return jsonify({'success': False, 'message': '该订单未申请作废'}), 400

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders SET status = 'cancelled', updated_at = ? WHERE order_no = ?
    ''', (now, order_no))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '订单已作废'})


@app.route('/api/orders/<order_no>/reject-cancel', methods=['POST'])
def reject_cancel(order_no):
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders SET cancel_requested = 0, cancel_reason = NULL, updated_at = ?
        WHERE order_no = ?
    ''', (now, order_no))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '作废申请已驳回'})


@app.route('/api/orders/<order_no>', methods=['PUT'])
def update_order(order_no):
    data = request.json
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders SET order_name = ?, functions = ?, extra_features = ?,
               processor_model = ?, processor_note = ?, total_cost = ?,
               deposit = ?, updated_at = ?
        WHERE order_no = ?
    ''', (
        data.get('order_name', order['order_name']),
        data.get('functions', order['functions']),
        data.get('extra_features', order['extra_features']),
        data.get('processor_model', order['processor_model']),
        data.get('processor_note', order['processor_note']),
        data.get('total_cost', order['total_cost']),
        data.get('deposit', order['deposit']),
        now, order_no
    ))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '订单已更新'})


@app.route('/api/orders/<order_no>', methods=['DELETE'])
def delete_order(order_no):
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404

    conn.execute('DELETE FROM settlements WHERE order_no = ?', (order_no,))
    conn.execute('DELETE FROM orders WHERE order_no = ?', (order_no,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '订单已删除'})


@app.route('/api/orders/<order_no>/force-cancel', methods=['POST'])
def force_cancel(order_no):
    conn = get_db()
    order = conn.execute('SELECT * FROM orders WHERE order_no = ?', (order_no,)).fetchone()
    if not order:
        conn.close()
        return jsonify({'success': False, 'message': '订单不存在'}), 404

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE orders SET status = 'cancelled', cancel_requested = 0,
               cancel_reason = '管理端强制作废', updated_at = ?
        WHERE order_no = ?
    ''', (now, order_no))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '订单已强制作废'})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
