import os
import psycopg2
import requests
from flask import Flask, request, jsonify, session, send_file
from flask_cors import CORS
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO, StringIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import inch, mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors

app = Flask(__name__)

app.config.update(
    SECRET_KEY=os.getenv('SECRET_KEY', 'clave_super_secreta_para_nomina_2026'),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_NAME='nomina_session'
)

frontend_urls = [
    "https://nomina-frontend.onrender.com",
    "https://soporteagroavicola.github.io",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]

CORS(app, 
     origins=frontend_urls, 
     supports_credentials=True, 
     allow_headers=["Content-Type", "Authorization"],
     expose_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

def get_db_connection():
    database_url = os.getenv('DATABASE_URL', 'postgresql://nomina_db_naiu_user:58sgnjVGnVRtLVbOVqYiA7d41VXwsHUH@dpg-d9prbrr9ik0c73ci4e0g-a.oregon-postgres.render.com/nomina_db_naiu')
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except Exception as e:
        print(f"❌ Error conectando a la BD: {e}")
        return None

def init_db():
    try:
        conn = get_db_connection()
        if not conn: return
        cur = conn.cursor()
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS empleados (
                id_empleado SERIAL PRIMARY KEY, cedula TEXT UNIQUE NOT NULL, nombres TEXT NOT NULL, apellidos TEXT NOT NULL,
                fecha_nacimiento DATE, fecha_ingreso DATE, cargo TEXT, departamento TEXT, sucursal_id INTEGER,
                salario_mensual_usd REAL DEFAULT 0, tipo_pago TEXT DEFAULT 'Quincenal', activo INTEGER DEFAULT 1,
                email TEXT, telefono TEXT, direccion TEXT, cuenta_bancaria TEXT
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS sucursales (
                id_sucursal SERIAL PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, activo INTEGER DEFAULT 1
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS lotes_nomina (
                id_lote SERIAL PRIMARY KEY, descripcion TEXT, fecha_calculo DATE NOT NULL, 
                total_usd REAL DEFAULT 0, total_bs REAL DEFAULT 0, cantidad_empleados INTEGER DEFAULT 0
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS nominas (
                id_nomina SERIAL PRIMARY KEY, id_empleado INTEGER NOT NULL, fecha_inicio DATE NOT NULL, fecha_fin DATE NOT NULL,
                tipo TEXT CHECK(tipo IN ('Quincenal', 'Semanal')), faltas_dias INTEGER DEFAULT 0, salario_base_usd REAL,
                horas_extras_usd REAL DEFAULT 0, bono_complementario_usd REAL DEFAULT 0, total_asignaciones_usd REAL,
                total_deducciones_usd REAL, neto_pagar_usd REAL, neto_pagar_bs REAL, tasa_bcv REAL, fecha_calculo DATE,
                sso_usd REAL DEFAULT 0, rpe_usd REAL DEFAULT 0, faov_usd REAL DEFAULT 0,
                sso_bs REAL DEFAULT 0, rpe_bs REAL DEFAULT 0, faov_bs REAL DEFAULT 0,
                descripcion TEXT, lote_id INTEGER
            )
        ''')
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL
            )
        ''')
        default_pass = generate_password_hash('admin123')
        cur.execute("INSERT INTO usuarios (username, password) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING", ('admin', default_pass))
        
        cur.execute('''
            CREATE TABLE IF NOT EXISTS parametros (
                id SERIAL PRIMARY KEY, clave TEXT UNIQUE NOT NULL, valor TEXT NOT NULL, fecha_actualizacion DATE
            )
        ''')
        
        cur.execute("""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns 
                           WHERE table_name='parametros' AND column_name='valor' AND data_type='real') THEN
                    ALTER TABLE parametros ALTER COLUMN valor TYPE TEXT;
                END IF;
            END $$;
        """)

        parametros_default = [
            ('tasa_bcv', '755.1552'),
            ('cestaticket_usd', '40.0'),
            ('porcentaje_ivss', '0.04'),
            ('porcentaje_rpe', '0.005'),
            ('porcentaje_faov', '0.01'),
            ('rif_empresa', 'J409876136'),
            ('cuenta_empresa', '000102034732'),
            ('nombre_cuenta_empresa', 'CODIZULCA'),
            ('codigo_banco_defecto', 'BSCHVECA')
        ]
        for clave, valor in parametros_default:
            cur.execute("INSERT INTO parametros (clave, valor) VALUES (%s, %s) ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor", (clave, valor))

        cur.execute('''
            CREATE TABLE IF NOT EXISTS cestaticket_lotes (
                id_lote SERIAL PRIMARY KEY,
                descripcion TEXT,
                fecha_calculo DATE NOT NULL,
                total_bs REAL DEFAULT 0,
                cantidad_empleados INTEGER DEFAULT 0,
                tasa_bcv REAL
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS cestaticket_nominas (
                id SERIAL PRIMARY KEY,
                id_empleado INTEGER NOT NULL,
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE NOT NULL,
                dias_pagados INTEGER NOT NULL,
                valor_diario_usd REAL NOT NULL,
                tasa_bcv REAL NOT NULL,
                total_usd REAL NOT NULL,
                total_bs REAL NOT NULL,
                descripcion TEXT,
                lote_id INTEGER
            )
        ''')

        conn.commit(); cur.close(); conn.close()
        print("✅ Base de datos inicializada correctamente.")
    except Exception as e:
        print(f"❌ ERROR GRAVE EN init_db: {e}")

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

def login_required(f):
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'No autorizado'}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# ============================================
# MÓDULO DE AUTENTICACIÓN Y USUARIOS
# ============================================
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    cur.execute("SELECT id, password FROM usuarios WHERE username = %s", (username,))
    user = cur.fetchone()
    cur.close(); conn.close()
    if user and check_password_hash(user[1], password):
        session['user_id'] = user[0]
        session['username'] = username
        session.permanent = True
        return jsonify({'mensaje': 'Inicio de sesión exitoso', 'username': username})
    return jsonify({'error': 'Usuario o contraseña incorrectos'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'mensaje': 'Sesión cerrada correctamente'})

@app.route('/api/check_auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({'authenticated': True, 'username': session.get('username')})
    return jsonify({'authenticated': False}), 401

@app.route('/api/usuarios', methods=['GET'])
@login_required
def get_usuarios():
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM usuarios ORDER BY id")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([{'id': r[0], 'username': r[1]} for r in rows])

@app.route('/api/usuarios', methods=['POST'])
@login_required
def crear_usuario():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    if not username or not password: return jsonify({'error': 'Usuario y contraseña son requeridos'}), 400
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        hashed_pass = generate_password_hash(password)
        cur.execute("INSERT INTO usuarios (username, password) VALUES (%s, %s)", (username, hashed_pass))
        conn.commit()
        return jsonify({'mensaje': f'Usuario "{username}" creado exitosamente'})
    except Exception as e:
        if "duplicate key value violates unique constraint" in str(e):
            return jsonify({'error': 'El nombre de usuario ya existe'}), 400
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

@app.route('/api/usuarios/<int:id>', methods=['PUT'])
@login_required
def actualizar_usuario(id):
    data = request.json
    username = data.get('username')
    password = data.get('password')
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        if password:
            hashed_pass = generate_password_hash(password)
            cur.execute("UPDATE usuarios SET username = %s, password = %s WHERE id = %s", (username, hashed_pass, id))
        else:
            cur.execute("UPDATE usuarios SET username = %s WHERE id = %s", (username, id))
        conn.commit()
        return jsonify({'mensaje': 'Usuario actualizado exitosamente'})
    except Exception as e:
        if "duplicate key value violates unique constraint" in str(e):
            return jsonify({'error': 'El nombre de usuario ya existe'}), 400
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

@app.route('/api/usuarios/<int:id>', methods=['DELETE'])
@login_required
def eliminar_usuario(id):
    user_id = session.get('user_id')
    if user_id == id:
        return jsonify({'error': 'No puedes eliminar tu propio usuario'}), 400
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM usuarios WHERE id = %s", (id,))
        conn.commit()
        return jsonify({'mensaje': 'Usuario eliminado exitosamente'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

@app.route('/api/usuarios/password', methods=['PUT'])
@login_required
def cambiar_password():
    data = request.json
    old_password = data.get('old_password')
    new_password = data.get('new_password')
    if not old_password or not new_password: return jsonify({'error': 'La contraseña actual y la nueva son requeridas'}), 400
    user_id = session.get('user_id')
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    cur.execute("SELECT password FROM usuarios WHERE id = %s", (user_id,))
    user = cur.fetchone()
    if not user or not check_password_hash(user[0], old_password):
        cur.close(); conn.close()
        return jsonify({'error': 'La contraseña actual es incorrecta'}), 401
    try:
        new_hashed = generate_password_hash(new_password)
        cur.execute("UPDATE usuarios SET password = %s WHERE id = %s", (new_hashed, user_id))
        conn.commit()
        return jsonify({'mensaje': 'Contraseña actualizada exitosamente'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

# ============================================
# ENDPOINTS DE NÓMINA
# ============================================
@app.route('/api/empleados', methods=['GET'])
@login_required
def get_empleados():
    search = request.args.get('search', '')
    sucursal_id = request.args.get('sucursal_id', '')
    tipo_pago = request.args.get('tipo_pago', '')
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    query = "SELECT * FROM empleados WHERE activo = 1"
    params = []
    if sucursal_id: query += " AND sucursal_id = %s"; params.append(sucursal_id)
    if tipo_pago: query += " AND tipo_pago = %s"; params.append(tipo_pago)
    if search: query += " AND (cedula ILIKE %s OR nombres ILIKE %s OR apellidos ILIKE %s)"; sp = f"%{search}%"; params.extend([sp, sp, sp])
    query += " ORDER BY nombres"
    cur.execute(query, params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{
        'id_empleado': r[0], 'cedula': r[1], 'nombres': r[2], 'apellidos': r[3],
        'fecha_nacimiento': r[4].isoformat() if r[4] else None, 'fecha_ingreso': r[5].isoformat() if r[5] else None,
        'cargo': r[6], 'departamento': r[7], 'sucursal_id': r[8],
        'salario_mensual_usd': float(r[9]) if r[9] else 0, 'tipo_pago': r[10], 'activo': r[11], 'email': r[12], 'telefono': r[13], 'direccion': r[14], 'cuenta_bancaria': r[15]
    } for r in rows])

# ============================================
# ENDPOINT: EMPLEADOS CON SUCURSAL (PARA CESTATICKET)
# ============================================
@app.route('/api/empleados_con_sucursal', methods=['GET'])
@login_required
def get_empleados_con_sucursal():
    """
    Obtiene empleados con información de sucursal para filtrado
    """
    search = request.args.get('search', '')
    sucursal_id = request.args.get('sucursal_id', '')
    tipo_pago = request.args.get('tipo_pago', '')
    
    conn = get_db_connection()
    if not conn:
        return jsonify([])
    
    cur = conn.cursor()
    query = """
        SELECT 
            e.id_empleado, 
            e.cedula, 
            e.nombres, 
            e.apellidos,
            e.tipo_pago,
            e.sucursal_id,
            s.nombre as sucursal_nombre
        FROM empleados e
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE e.activo = 1
    """
    params = []
    
    if sucursal_id:
        query += " AND e.sucursal_id = %s"
        params.append(sucursal_id)
    
    if tipo_pago:
        query += " AND e.tipo_pago = %s"
        params.append(tipo_pago)
    
    if search:
        query += " AND (e.cedula ILIKE %s OR e.nombres ILIKE %s OR e.apellidos ILIKE %s)"
        sp = f"%{search}%"
        params.extend([sp, sp, sp])
    
    query += " ORDER BY s.nombre, e.nombres"
    
    try:
        cur.execute(query, params)
        rows = cur.fetchall()
    except Exception as e:
        print(f"❌ Error en consulta: {e}")
        cur.close()
        conn.close()
        return jsonify([])
    
    cur.close()
    conn.close()
    
    return jsonify([{
        'id_empleado': r[0],
        'cedula': r[1],
        'nombres': r[2],
        'apellidos': r[3],
        'tipo_pago': r[4] if r[4] else 'Quincenal',
        'sucursal_id': r[5],
        'sucursal_nombre': r[6] if r[6] else 'Sin sucursal'
    } for r in rows])

@app.route('/api/empleados', methods=['POST'])
@login_required
def crear_empleado():
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO empleados (cedula, nombres, apellidos, fecha_nacimiento, fecha_ingreso, cargo, departamento, sucursal_id, salario_mensual_usd, tipo_pago, email, telefono, direccion, cuenta_bancaria)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (data['cedula'], data['nombres'], data['apellidos'], data['fecha_nacimiento'], data['fecha_ingreso'], data['cargo'], data['departamento'], data['sucursal_id'], data['salario_mensual_usd'], data['tipo_pago'], data.get('email'), data.get('telefono'), data.get('direccion'), data.get('cuenta_bancaria')))
        conn.commit(); return jsonify({'mensaje': 'Empleado creado exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/empleados/<int:id>', methods=['PUT'])
@login_required
def actualizar_empleado(id):
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute('''
            UPDATE empleados SET 
                cedula=%s, nombres=%s, apellidos=%s, fecha_nacimiento=%s, fecha_ingreso=%s, 
                cargo=%s, departamento=%s, sucursal_id=%s, salario_mensual_usd=%s, 
                tipo_pago=%s, email=%s, telefono=%s, direccion=%s, cuenta_bancaria=%s
            WHERE id_empleado=%s
        ''', (data['cedula'], data['nombres'], data['apellidos'], data['fecha_nacimiento'], data['fecha_ingreso'], data['cargo'], data['departamento'], data['sucursal_id'], data['salario_mensual_usd'], data['tipo_pago'], data.get('email'), data.get('telefono'), data.get('direccion'), data.get('cuenta_bancaria'), id))
        conn.commit()
        return jsonify({'mensaje': 'Empleado actualizado exitosamente'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

@app.route('/api/empleados/<int:id>', methods=['DELETE'])
@login_required
def eliminar_empleado(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("UPDATE empleados SET activo = 0 WHERE id_empleado = %s", (id,))
        conn.commit(); return jsonify({'mensaje': 'Empleado eliminado exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/sucursales', methods=['GET'])
@login_required
def get_sucursales():
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    cur.execute('SELECT * FROM sucursales WHERE activo = 1 ORDER BY nombre')
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{'id_sucursal': r[0], 'nombre': r[1], 'activo': r[2]} for r in rows])

@app.route('/api/sucursales', methods=['POST'])
@login_required
def crear_sucursal():
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO sucursales (nombre) VALUES (%s)", (data['nombre'],))
        conn.commit(); return jsonify({'mensaje': 'Sucursal creada exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/sucursales/<int:id>', methods=['DELETE'])
@login_required
def eliminar_sucursal(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("UPDATE sucursales SET activo = 0 WHERE id_sucursal = %s", (id,))
        conn.commit(); return jsonify({'mensaje': 'Sucursal eliminada exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/parametros', methods=['GET'])
@login_required
def get_parametros():
    conn = get_db_connection()
    if not conn: return jsonify({})
    cur = conn.cursor()
    cur.execute("SELECT clave, valor FROM parametros")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify({row[0]: float(row[1]) if row[1].replace('.','',1).isdigit() else row[1] for row in rows})

@app.route('/api/parametros', methods=['PUT'])
@login_required
def actualizar_parametro():
    data = request.json
    clave = data.get('clave')
    valor = data.get('valor')
    if not clave or valor is None: return jsonify({'error': 'Clave y valor son requeridos'}), 400
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("UPDATE parametros SET valor = %s, fecha_actualizacion = CURRENT_DATE WHERE clave = %s", (str(valor), clave))
        conn.commit()
        return jsonify({'mensaje': f'Parámetro "{clave}" actualizado exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/actualizar_bcv', methods=['GET'])
@login_required
def actualizar_bcv():
    try:
        response = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=10)
        if response.status_code == 200:
            data = response.json()
            new_rate = float(data['promedio'])
            conn = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("UPDATE parametros SET valor = %s, fecha_actualizacion = CURRENT_DATE WHERE clave = 'tasa_bcv'", (str(new_rate),))
                conn.commit()
                cur.close()
                conn.close()
                return jsonify({'tasa': new_rate, 'mensaje': 'Tasa BCV actualizada exitosamente'})
        return jsonify({'error': 'No se pudo obtener la tasa de la API externa'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================
# CÁLCULO DE NÓMINA Y PASIVOS
# ============================================
@app.route('/api/calcular_nomina', methods=['POST'])
@login_required
def calcular_nomina():
    data = request.json
    tipo, fecha_inicio, fecha_fin = data.get('tipo'), data.get('fecha_inicio'), data.get('fecha_fin')
    descripcion = data.get('descripcion', '')
    empleados_ids, faltas_dict, horas_extras_dict = data.get('empleados_ids', []), data.get('faltas', {}), data.get('horas_extras', {})
    bonos_dict = data.get('bonos', {})
    aplicar_deducciones = data.get('aplicar_deducciones', True)
    split_60_40 = data.get('split_60_40', False)
    calcular_solo_bono = data.get('calcular_solo_bono', False)
    
    if not fecha_inicio or not fecha_fin or not empleados_ids: return jsonify({'error': 'Faltan datos'}), 400
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone(); tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    
    placeholders = ','.join(['%s'] * len(empleados_ids))
    cur.execute(f"SELECT * FROM empleados WHERE id_empleado IN ({placeholders})", empleados_ids)
    empleados = cur.fetchall()
    resultados = []
    total_usd_lote = 0.0
    total_bs_lote = 0.0
    
    start_date = datetime.strptime(fecha_inicio, "%Y-%m-%d").date()
    end_date = datetime.strptime(fecha_fin, "%Y-%m-%d").date()
    total_calendar_days = (end_date - start_date).days + 1
    
    for emp in empleados:
        cedula = emp[1]
        faltas = faltas_dict.get(cedula, 0) if not calcular_solo_bono else 0
        horas_data = horas_extras_dict.get(cedula, {})
        horas, valor_hora = horas_data.get('horas', 0), horas_data.get('valor_hora', 0)
        bono = bonos_dict.get(cedula, 0)
        salario_mensual = float(emp[9]) if emp[9] else 0
        
        if calcular_solo_bono:
            salario_base_full = 0
            base_incidencia_periodo = 0
            total_horas_extras = 0
            total_asignaciones_base = bono
            dias_teoricos_trabajo = 0
            dias_descanso = 0
        else:
            salario_diario_full = salario_mensual / 30
            salario_diario_incidencia = salario_mensual * 0.60 / 30
            total_horas_extras = horas * valor_hora
            if tipo == 'Quincenal':
                salario_base_full = salario_mensual / 2
                base_incidencia_periodo = salario_mensual * 0.60 / 2
                dias_teoricos_trabajo = 11
                dias_descanso = 4
                total_asignaciones_base = salario_base_full - (faltas * salario_diario_full) + total_horas_extras
            else:
                dias_teoricos_trabajo = 7
                dias_descanso = 2
                salario_base_full = salario_diario_full * 7
                base_incidencia_periodo = salario_diario_incidencia * 7
                total_asignaciones_base = salario_base_full - (faltas * salario_diario_full) + total_horas_extras
            
        if aplicar_deducciones:
            ivss = total_asignaciones_base * 0.04
            rpe = total_asignaciones_base * 0.005
            faov = total_asignaciones_base * 0.01
            total_deducciones = ivss + rpe + faov
        else:
            ivss, rpe, faov, total_deducciones = 0.0, 0.0, 0.0, 0.0
            
        neto_base_usd = total_asignaciones_base - total_deducciones
        if calcular_solo_bono:
            total_neto_usd = bono - total_deducciones
        else:
            total_neto_usd = (total_asignaciones_base) - total_deducciones

        if calcular_solo_bono:
            dias_reales_trabajados = 0
            salario_base_full = 0
        else:
            dias_reales_trabajados = max(0, dias_teoricos_trabajo - faltas)
        
        if split_60_40:
            pago_60_usd = (total_neto_usd * 0.60)
            pago_40_usd = total_neto_usd * 0.40
        else:
            pago_60_usd = total_neto_usd
            pago_40_usd = 0.0

        total_usd_lote += total_neto_usd
        total_bs_lote += total_neto_usd * tasa_bcv

        calculo = {
            'salario_base_full_usd': salario_base_full,
            'base_incidencia_60_usd': base_incidencia_periodo if not calcular_solo_bono else 0,
            'horas_extras_usd': total_horas_extras,
            'bono_complementario_usd': bono,
            'total_asignaciones_base_usd': total_asignaciones_base,
            'total_deducciones_usd': total_deducciones,
            'sso_usd': ivss, 'rpe_usd': rpe, 'faov_usd': faov,
            'neto_pagar_usd': total_neto_usd, 'neto_pagar_bs': total_neto_usd * tasa_bcv,
            'pago_60_usd': pago_60_usd, 'pago_40_usd': pago_40_usd,
            'pago_60_bs': pago_60_usd * tasa_bcv, 'pago_40_bs': pago_40_usd * tasa_bcv,
            'faltas_dias': faltas,
            'dias_totales_periodo': total_calendar_days,
            'dias_descanso': dias_descanso if not calcular_solo_bono else 0,
            'dias_reales_trabajados': dias_reales_trabajados,
            'split_60_40': split_60_40,
            'calcular_solo_bono': calcular_solo_bono,
            'empleado': {'id': emp[0], 'cedula': cedula, 'nombre_completo': f"{emp[2]} {emp[3]}"}
        }
        resultados.append(calculo)
        
    cur.execute('''
        INSERT INTO lotes_nomina (descripcion, fecha_calculo, total_usd, total_bs, cantidad_empleados)
        VALUES (%s, %s, %s, %s, %s) RETURNING id_lote
    ''', (descripcion, datetime.now().date(), total_usd_lote, total_bs_lote, len(empleados)))
    lote_id = cur.fetchone()[0]
    
    for emp, calculo in zip(empleados, resultados):
        cur.execute('''
            INSERT INTO nominas (
                id_empleado, fecha_inicio, fecha_fin, tipo, faltas_dias, 
                salario_base_usd, horas_extras_usd, bono_complementario_usd, 
                total_asignaciones_usd, total_deducciones_usd, 
                neto_pagar_usd, neto_pagar_bs, tasa_bcv, fecha_calculo,
                sso_usd, rpe_usd, faov_usd, 
                sso_bs, rpe_bs, faov_bs,
                descripcion, lote_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (emp[0], fecha_inicio, fecha_fin, tipo, calculo['faltas_dias'], 
              calculo['salario_base_full_usd'], calculo['horas_extras_usd'], 
              calculo['bono_complementario_usd'], calculo['total_asignaciones_base_usd'], 
              calculo['total_deducciones_usd'], calculo['neto_pagar_usd'], 
              calculo['neto_pagar_bs'], tasa_bcv, datetime.now().date(), 
              calculo['sso_usd'], calculo['rpe_usd'], calculo['faov_usd'], 
              calculo['sso_usd'] * tasa_bcv, calculo['rpe_usd'] * tasa_bcv, 
              calculo['faov_usd'] * tasa_bcv, descripcion, lote_id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'tasa_bcv': tasa_bcv, 'resultados': resultados, 'lote_id': lote_id})

@app.route('/api/calcular_pasivos', methods=['POST'])
@login_required
def calcular_pasivos():
    data = request.json
    salario_mensual = data.get('salario_mensual', 0)
    dias = data.get('dias', 30)
    usar_base_60 = data.get('usar_base_60', True)
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone()
    tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    cur.close(); conn.close()
    salario_diario = salario_mensual / 30
    if usar_base_60: salario_diario = salario_diario * 0.60
    total_usd = salario_diario * dias
    total_bs = total_usd * tasa_bcv
    return jsonify({
        'dias': dias,
        'tasa_bcv': tasa_bcv,
        'base_usada': 'Incidencia 60%' if usar_base_60 else '100% (Full)',
        'total_usd': total_usd,
        'total_bs': total_bs
    })

# ============================================
# ENDPOINT: CÁLCULO DE CESTATICKET
# ============================================
@app.route('/api/calcular_cestaticket', methods=['POST'])
@login_required
def calcular_cestaticket():
    data = request.json
    fecha_inicio, fecha_fin = data.get('fecha_inicio'), data.get('fecha_fin')
    descripcion = data.get('descripcion', '')
    empleados_ids = data.get('empleados_ids', [])
    faltas_dict = data.get('faltas', {})

    if not fecha_inicio or not fecha_fin or not empleados_ids:
        return jsonify({'error': 'Faltan datos'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cur = conn.cursor()
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone()
    tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'cestaticket_usd'")
    valor_row = cur.fetchone()
    valor_mensual_usd = float(valor_row[0]) if valor_row else 40.0

    valor_diario_usd = valor_mensual_usd / 30

    placeholders = ','.join(['%s'] * len(empleados_ids))
    cur.execute(f"SELECT * FROM empleados WHERE id_empleado IN ({placeholders})", empleados_ids)
    empleados = cur.fetchall()
    
    resultados = []
    total_bs_lote = 0.0
    
    start_date = datetime.strptime(fecha_inicio, "%Y-%m-%d").date()
    end_date = datetime.strptime(fecha_fin, "%Y-%m-%d").date()
    
    total_working_days = 0
    current_day = start_date
    while current_day <= end_date:
        if current_day.weekday() < 5:
            total_working_days += 1
        current_day += timedelta(days=1)

    for emp in empleados:
        emp_id = str(emp[0])
        
        faltas = faltas_dict.get(emp_id, 0)
        
        if isinstance(faltas, str):
            try:
                faltas = int(faltas) if faltas.isdigit() else 0
            except:
                faltas = 0
        elif not isinstance(faltas, (int, float)):
            faltas = 0
        else:
            faltas = int(faltas)
        
        descuento_usd = faltas * valor_diario_usd
        total_usd = valor_mensual_usd - descuento_usd
        if total_usd < 0:
            total_usd = 0
        
        total_bs = total_usd * tasa_bcv
        total_bs_lote += total_bs

        calculo = {
            'id_empleado': emp[0],
            'cedula': emp[1],
            'nombre_completo': f"{emp[2]} {emp[3]}",
            'dias_totales_periodo': total_working_days,
            'valor_mensual_usd': valor_mensual_usd,
            'valor_diario_usd': valor_diario_usd,
            'faltas': faltas,
            'descuento_usd': descuento_usd,
            'dias_pagados': max(0, 30 - faltas),
            'total_usd': total_usd,
            'total_bs': total_bs
        }
        resultados.append(calculo)

    cur.execute('''
        INSERT INTO cestaticket_lotes (descripcion, fecha_calculo, total_bs, cantidad_empleados, tasa_bcv)
        VALUES (%s, %s, %s, %s, %s) RETURNING id_lote
    ''', (descripcion, datetime.now().date(), total_bs_lote, len(empleados), tasa_bcv))
    lote_id = cur.fetchone()[0]

    for calc in resultados:
        cur.execute('''
            INSERT INTO cestaticket_nominas (
                id_empleado, fecha_inicio, fecha_fin, dias_pagados, 
                valor_diario_usd, tasa_bcv, total_usd, total_bs, descripcion, lote_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            calc['id_empleado'], fecha_inicio, fecha_fin, 
            calc['dias_pagados'], valor_diario_usd, tasa_bcv, 
            calc['total_usd'], calc['total_bs'], descripcion, lote_id
        ))

    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({
        'tasa_bcv': tasa_bcv,
        'valor_mensual_usd': valor_mensual_usd,
        'valor_diario_usd': valor_diario_usd,
        'total_working_days': total_working_days,
        'resultados': resultados,
        'lote_id': lote_id
    })

# ============================================
# HISTORIAL Y DETALLE CESTATICKET
# ============================================
@app.route('/api/lotes_cestaticket', methods=['GET'])
@login_required
def get_lotes_cestaticket():
    search = request.args.get('search', '')
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    query = '''
        SELECT l.*, COUNT(c.id) as total_empleados_detalle
        FROM cestaticket_lotes l
        LEFT JOIN cestaticket_nominas c ON l.id_lote = c.lote_id
        WHERE 1=1
    '''
    params = []
    if search: query += " AND (l.descripcion ILIKE %s OR CAST(l.id_lote AS TEXT) ILIKE %s)"; sp = f"%{search}%"; params.extend([sp, sp])
    query += " GROUP BY l.id_lote ORDER BY l.fecha_calculo DESC, l.id_lote DESC"
    cur.execute(query, params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{
        'id_lote': r[0], 'descripcion': r[1], 'fecha_calculo': r[2].isoformat() if r[2] else None,
        'total_bs': float(r[3]) if r[3] else 0, 'cantidad_empleados_lote': r[4],
        'tasa_bcv': float(r[5]) if r[5] else 0, 'total_empleados_detalle': r[6]
    } for r in rows])

@app.route('/api/lotes_cestaticket/<int:id>', methods=['DELETE'])
@login_required
def eliminar_lote_cestaticket(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM cestaticket_nominas WHERE lote_id = %s", (id,))
        cur.execute("DELETE FROM cestaticket_lotes WHERE id_lote = %s", (id,))
        conn.commit()
        return jsonify({'mensaje': 'Lote de cestaticket eliminado exitosamente'})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

# ============================================
# GENERADOR DE TXT Y PDF PARA CESTATICKET
# ============================================
@app.route('/api/generar_archivo_cestaticket/<int:lote_id>', methods=['GET'])
@login_required
def generar_archivo_cestaticket(lote_id):
    try:
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()
        cur.execute("SELECT valor FROM parametros WHERE clave = 'rif_empresa'")
        row = cur.fetchone()
        rif_empresa = str(row[0]) if row else "J409876136"
        cur.execute("SELECT valor FROM parametros WHERE clave = 'cuenta_empresa'")
        row = cur.fetchone()
        cuenta_empresa = str(row[0]) if row else "000102034732"
        cur.execute("SELECT valor FROM parametros WHERE clave = 'nombre_cuenta_empresa'")
        row = cur.fetchone()
        nombre_cuenta_empresa = str(row[0]) if row else "CODIZULCA"
        cur.execute("SELECT valor FROM parametros WHERE clave = 'codigo_banco_defecto'")
        row = cur.fetchone()
        codigo_banco = str(row[0]) if row else "BSCHVECA"

        cur.execute('''
            SELECT 
                e.cedula, 
                e.cuenta_bancaria, 
                e.nombres, 
                e.apellidos,
                c.total_bs as monto_pago_bs
            FROM cestaticket_nominas c
            JOIN empleados e ON c.id_empleado = e.id_empleado
            WHERE c.lote_id = %s 
              AND e.cuenta_bancaria IS NOT NULL 
              AND e.cuenta_bancaria != ''
        ''', (lote_id,))

        rows = cur.fetchall()
        cur.close(); conn.close()
        if not rows: return jsonify({'error': 'No hay empleados con cuentas bancarias registradas en este lote.'}), 404

        fecha_ejecucion = datetime.now().strftime("%d/%m/%Y")
        total_amount = 0.0
        buffer = StringIO()
        total_count = len(rows)
        header_line = f"HEADER  {total_count:08d}0011853{rif_empresa:<10}{fecha_ejecucion}{fecha_ejecucion}"
        buffer.write(header_line + "\n")
        for i, row in enumerate(rows, 1):
            cedula = str(row[0]) if row[0] else ''
            cuenta_empleado = str(row[1]) if row[1] else ''
            nombre = f"{row[2]} {row[3]}" if row[2] and row[3] else row[2] or row[3] or ''
            monto = float(row[4]) if row[4] else 0.0
            total_amount += monto
            monto_str = f"{monto:016.2f}".replace('.', ',')
            debit_line = (f"DEBITO  {i:08d}{rif_empresa:<10}{nombre_cuenta_empresa:<30}"
                          f"{fecha_ejecucion}{cuenta_empresa:<12}00000487092{monto_str:<21}VEB40 ")
            credit_line = (f"CREDITO {i:08d}{cedula:<10}{nombre:<29}"
                           f"{cuenta_empleado:<22}{monto_str:<21}00{codigo_banco:<8}")
            buffer.write(debit_line + "\n")
            buffer.write(credit_line + "\n")
        total_amount_str = f"{total_amount:015.2f}".replace('.', ',')
        total_line = f"TOTAL   {total_count:05d}{total_count:05d}{total_amount_str:<18}"
        buffer.write(total_line + "\n")
        mem = BytesIO()
        mem.write(buffer.getvalue().encode('cp1252'))
        mem.seek(0)
        buffer.close()
        return send_file(
            mem,
            as_attachment=True,
            download_name=f"CESTA_{datetime.now().strftime('%Y%m%d')}.txt",
            mimetype='text/plain'
        )
    except Exception as e:
        print(f"❌ Error generando archivo cestaticket: {e}")
        return jsonify({'error': f'Error interno generando el archivo: {str(e)}'}), 500

@app.route('/api/generar_recibo_cestaticket/<int:id>', methods=['GET'])
@login_required
def generar_recibo_cestaticket(id):
    try:
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()
        cur.execute('''
            SELECT 
                c.id, c.id_empleado, c.fecha_inicio, c.fecha_fin, c.dias_pagados, 
                c.valor_diario_usd, c.tasa_bcv, c.total_usd, c.total_bs, c.descripcion, c.lote_id,
                e.nombres, e.apellidos, e.cedula, e.cargo
            FROM cestaticket_nominas c
            JOIN empleados e ON c.id_empleado = e.id_empleado
            WHERE c.id = %s
        ''', (id,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row: return jsonify({'error': 'Cestaticket no encontrado'}), 404

        c = row
        empleado_nombre = f"{c[11]} {c[12]}".strip()
        empleado_cedula = c[13]
        cargo = c[14]
        descripcion = c[9] or "Recibo de Cestaticket"
        
        fecha_inicio = c[2].strftime("%d/%m/%Y") if c[2] else ''
        fecha_fin = c[3].strftime("%d/%m/%Y") if c[3] else ''
        dias_pagados = c[4]
        valor_diario_usd = float(c[5]) if c[5] else 0
        tasa_bcv = float(c[6]) if c[6] else 0
        total_usd = float(c[7]) if c[7] else 0
        total_bs = float(c[8]) if c[8] else 0
        valor_mensual_usd = 40.0

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
        elements = []
        styles = getSampleStyleSheet()
        normal_style = ParagraphStyle(name='Normal', fontName='Helvetica', fontSize=9)
        bold_style = ParagraphStyle(name='Bold', parent=normal_style, fontName='Helvetica-Bold', fontSize=9)
        title_style = ParagraphStyle(name='Title', fontSize=14, alignment=1, spaceAfter=10)

        logo_path = os.path.join(app.root_path, 'logo.png')
        try:
            logo = Image(logo_path)
            logo.drawHeight = 1.2*inch
            logo.drawWidth = 1.2*inch
            logo_table_data = [[logo, Paragraph(f"<b>{descripcion}</b>", title_style)]]
            logo_table = Table(logo_table_data, colWidths=[1.2*inch, 400])
            logo_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('ALIGN', (1,0), (1,0), 'RIGHT'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ]))
            elements.append(logo_table)
        except:
            elements.append(Paragraph(f"<b>{descripcion}</b>", title_style))
        
        header_data = [
            [Paragraph(f"<b>Empleado:</b> {empleado_nombre}", normal_style), Paragraph(f"<b>Cédula:</b> {empleado_cedula}", normal_style)],
            [Paragraph(f"<b>Cargo:</b> {cargo}", normal_style), Paragraph(f"<b>Período:</b> {fecha_inicio} a {fecha_fin}", normal_style)],
            [Paragraph(f"<b>Tasa BCV:</b> Bs. {tasa_bcv:.4f}", normal_style), Paragraph(f"<b>Valor Mensual:</b> ${valor_mensual_usd:.2f}", normal_style)],
        ]
        header_table = Table(header_data, colWidths=[250, 250])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 10*mm))

        concept_data = [
            [Paragraph("<b>Concepto</b>", bold_style), Paragraph("<b>Valor</b>", bold_style)],
            [Paragraph("Valor Mensual (Ley)", normal_style), Paragraph(f"${valor_mensual_usd:.2f} USD", normal_style)],
            [Paragraph("Días Pagados", normal_style), Paragraph(f"{dias_pagados} días", normal_style)],
            [Paragraph("Total a Pagar (USD)", normal_style), Paragraph(f"${total_usd:.2f}", normal_style)],
            [Paragraph("Total a Pagar (Bs)", normal_style), Paragraph(f"Bs. {total_bs:.2f}", bold_style)],
        ]
        concept_table = Table(concept_data, colWidths=[220, 200])
        concept_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightblue),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(concept_table)
        elements.append(Spacer(1, 10*mm))

        footer_data = [
            [Paragraph("<b>Total Neto a Pagar (Bs):</b>", normal_style), Paragraph(f"<b>Bs. {total_bs:.2f}</b>", bold_style)],
            [Paragraph("Generado por:", normal_style), Paragraph("Sistema de Nómina Agroavícola del Llano", normal_style)],
            [Paragraph("Fecha de Emisión:", normal_style), Paragraph(datetime.now().strftime("%d/%m/%Y %H:%M"), normal_style)],
        ]
        footer_table = Table(footer_data, colWidths=[170, 280])
        footer_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        elements.append(footer_table)

        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"recibo_cestaticket_{id}.pdf", mimetype='application/pdf')
    except Exception as e:
        print(f"❌ Error fatal en generar_recibo_cestaticket: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/recibo_cestaticket_html/<int:id>', methods=['GET'])
@login_required
def recibo_cestaticket_html(id):
    """
    Genera una vista HTML del recibo de Cestaticket para impresión en estilo Odoo
    TODOS LOS MONTOS EN BOLÍVARES (Bs.) - SIN DÓLARES
    """
    try:
        conn = get_db_connection()
        if not conn:
            return "<h1>Error de conexión a la base de datos</h1>", 500
        cur = conn.cursor()
        
        cur.execute('''
            SELECT 
                c.id, 
                c.id_empleado, 
                c.fecha_inicio, 
                c.fecha_fin, 
                c.dias_pagados, 
                c.valor_diario_usd, 
                c.tasa_bcv, 
                c.total_usd, 
                c.total_bs, 
                c.descripcion, 
                c.lote_id,
                e.nombres, 
                e.apellidos, 
                e.cedula, 
                e.cargo,
                e.fecha_ingreso
            FROM cestaticket_nominas c
            JOIN empleados e ON c.id_empleado = e.id_empleado
            WHERE c.id = %s
        ''', (id,))
        
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if not row:
            return "<h1>Recibo no encontrado</h1><p>El ID del recibo no existe.</p>", 404

        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT valor FROM parametros WHERE clave = 'rif_empresa'")
            rif_row = cur.fetchone()
            rif_empresa = str(rif_row[0]) if rif_row else "J-505631349"
            
            cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
            tasa_row = cur.fetchone()
            tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
            
            cur.execute("SELECT valor FROM parametros WHERE clave = 'nombre_cuenta_empresa'")
            cuenta_row = cur.fetchone()
            nombre_cuenta = str(cuenta_row[0]) if cuenta_row else "AGROAVICOLA DEL LLANO, C.A"
            
            cur.close()
            conn.close()
        else:
            rif_empresa = "J-505631349"
            tasa_bcv = 755.1552
            nombre_cuenta = "AGROAVICOLA DEL LLANO, C.A"

        nombres = row[11] or ''
        apellidos = row[12] or ''
        nombre_completo = f"{nombres} {apellidos}".strip()
        cedula = row[13] or ''
        cargo = row[14] or ''
        fecha_ingreso = row[15].strftime("%d/%m/%Y") if row[15] else ''
        
        fecha_inicio = row[2].strftime("%d/%m/%Y") if row[2] else ''
        fecha_fin = row[3].strftime("%d/%m/%Y") if row[3] else ''
        dias_pagados = row[4] if row[4] else 30
        total_bs = float(row[8]) if row[8] else 0
        lote_id = row[10]
        
        valor_diario_usd = float(row[5]) if row[5] else (40.0 / 30)
        valor_diario_bs = valor_diario_usd * tasa_bcv
        
        faltas = 30 - dias_pagados
        descuento_bs = faltas * valor_diario_bs
        
        total_bs_calculado = dias_pagados * valor_diario_bs
        if total_bs == 0:
            total_bs = total_bs_calculado
        
        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        hora_actual = datetime.now().strftime("%H:%M:%S")
        numero_recibo = f"CESTA-{lote_id}-{cedula}"

        total_bs_formateado = f"{total_bs:,.2f}".replace(",", ".")
        descuento_bs_formateado = f"{descuento_bs:,.2f}".replace(",", ".")
        valor_diario_bs_formateado = f"{valor_diario_bs:,.2f}".replace(",", ".")
        tasa_bcv_formateada = f"{tasa_bcv:,.4f}".replace(",", ".")

        html = f'''
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Recibo Cestaticket</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{
                    font-family: 'Courier New', Courier, monospace;
                    font-size: 11px;
                    background: #f0f0f0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    padding: 20px;
                }}
                .recibo-container {{
                    background: white;
                    width: 210mm;
                    padding: 15mm 12mm;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                    border-radius: 4px;
                }}
                @media print {{
                    body {{ background: white; padding: 0; }}
                    .recibo-container {{
                        box-shadow: none;
                        border-radius: 0;
                        padding: 10mm 12mm;
                        width: 100%;
                    }}
                    .no-print {{ display: none !important; }}
                    .print-header {{ display: none !important; }}
                }}
                .print-header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 15px;
                    padding-bottom: 10px;
                    border-bottom: 1px solid #ddd;
                }}
                .btn-print {{
                    background: #1a2a6c;
                    color: white;
                    border: none;
                    padding: 8px 20px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 12px;
                    font-weight: 600;
                }}
                .btn-print:hover {{ background: #2d4373; }}
                .btn-pdf {{
                    background: #dc3545;
                    color: white;
                    border: none;
                    padding: 8px 20px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 12px;
                    font-weight: 600;
                    margin-left: 8px;
                }}
                .btn-pdf:hover {{ background: #c82333; }}
                .header {{
                    text-align: center;
                    margin-bottom: 10px;
                    padding-bottom: 8px;
                    border-bottom: 2px solid #000;
                }}
                .header .title {{ font-size: 16px; font-weight: bold; }}
                .header .subtitle {{ font-size: 12px; }}
                .header .rif {{ font-size: 11px; color: #555; }}
                .header .boleto {{ font-size: 13px; font-weight: bold; margin-top: 3px; }}
                .header .status {{ display: inline-block; padding: 2px 12px; background: #28a745; color: white; border-radius: 3px; font-size: 10px; font-weight: bold; }}
                .header .fecha-hora {{ font-size: 11px; color: #555; }}
                .separator {{ border: none; border-top: 1px dashed #999; margin: 6px 0; }}
                .section-title {{ font-weight: bold; font-size: 12px; margin: 8px 0 4px 0; }}
                .section {{ margin-bottom: 6px; }}
                .row {{ display: flex; justify-content: space-between; padding: 2px 0; }}
                .row-label {{ font-weight: bold; }}
                .table {{ width: 100%; border-collapse: collapse; margin: 4px 0; }}
                .table th {{ border-bottom: 2px solid #000; padding: 4px 6px; text-align: left; font-size: 10px; }}
                .table td {{ padding: 3px 6px; border-bottom: 1px solid #ddd; font-size: 10px; }}
                .table .total-row td {{ border-top: 2px solid #000; font-weight: bold; }}
                .table .total-row td:last-child {{ text-align: right; }}
                .table .right {{ text-align: right; }}
                .table .center {{ text-align: center; }}
                .valores {{
                    font-size: 10px;
                    margin: 4px 0;
                    padding: 4px 8px;
                    background: #f8f9fa;
                    border: 1px solid #ddd;
                    border-radius: 3px;
                }}
                .valores .row {{ padding: 1px 0; }}
                .declaracion {{
                    font-size: 10px;
                    margin: 8px 0;
                    padding: 6px;
                    border: 1px solid #ddd;
                    border-radius: 3px;
                    background: #fafafa;
                }}
                .firmas {{
                    margin: 10px 0;
                    display: flex;
                    justify-content: space-between;
                }}
                .firmas .firma {{
                    text-align: center;
                    width: 45%;
                }}
                .firmas .linea {{
                    border-top: 1px solid #000;
                    width: 80%;
                    margin: 20px auto 4px auto;
                }}
                .footer {{
                    text-align: center;
                    font-size: 9px;
                    color: #888;
                    margin-top: 8px;
                    border-top: 2px solid #000;
                    padding-top: 6px;
                }}
                .monto-bs {{
                    font-weight: bold;
                    color: #1a2a6c;
                }}
                .monto-descuento {{
                    color: #dc3545;
                }}
                @media print {{
                    .recibo-container {{
                        padding: 8mm 10mm;
                    }}
                    .header .title {{ font-size: 14px; }}
                    .btn-print, .btn-pdf {{ display: none !important; }}
                }}
                @page {{
                    size: A4;
                    margin: 0;
                }}
            </style>
        </head>
        <body>
            <div class="recibo-container" id="recibo">
                <div class="print-header no-print">
                    <span style="font-weight: bold; font-size: 14px;">📄 Recibo de Cestaticket</span>
                    <div>
                        <button class="btn-print" onclick="window.print()">🖨️ Imprimir</button>
                        <button class="btn-pdf" onclick="window.print()">📥 PDF</button>
                    </div>
                </div>

                <div class="header">
                    <div class="title">AGROAVICOLA DEL LLANO, C.A.</div>
                    <div class="rif">RIF: {rif_empresa}</div>
                    <div class="subtitle">RECIBO DE CESTATICKET</div>
                    <div class="boleto">BOLETO: {numero_recibo}</div>
                    <div style="display:flex; justify-content:space-between; margin-top:4px;">
                        <span><span class="status">PAGADO</span></span>
                        <span class="fecha-hora">Fecha: {fecha_actual}  Hora: {hora_actual}</span>
                    </div>
                    <div style="font-size:10px; color:#555; margin-top:2px;">PAGO DE CESTATICKET SOCIALISTA</div>
                </div>

                <hr class="separator">

                <div class="section">
                    <div class="section-title">EMPLEADO</div>
                    <div class="row"><span class="row-label">NOMBRE:</span> <span>{nombre_completo}</span></div>
                    <div class="row"><span class="row-label">C.I.:</span> <span>{cedula}</span></div>
                    <div class="row"><span class="row-label">CARGO:</span> <span>{cargo}</span></div>
                    <div class="row"><span class="row-label">FECHA INGRESO:</span> <span>{fecha_ingreso}</span></div>
                    <div class="row"><span class="row-label">PERIODO:</span> <span>{fecha_inicio} al {fecha_fin}</span></div>
                </div>

                <hr class="separator">

                <div class="section">
                    <div class="section-title">DETALLE DEL PAGO</div>
                    <table class="table">
                        <thead>
                            <tr>
                                <th>CONCEPTO</th>
                                <th style="text-align:center;">CANTIDAD</th>
                                <th style="text-align:right;">MONTO Bs.</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>CESTA TICKET SOCIALISTA</td>
                                <td class="center">{dias_pagados} DÍAS</td>
                                <td class="right monto-bs">{total_bs_formateado}</td>
                            </tr>
                            {f'<tr><td>(-) DESCUENTO POR FALTAS</td><td class="center">{faltas} DÍAS</td><td class="right monto-descuento">({descuento_bs_formateado})</td></tr>' if faltas > 0 else ''}
                        </tbody>
                        <tfoot>
                            <tr class="total-row">
                                <td colspan="2" style="text-align:right;">TOTAL A PAGAR:</td>
                                <td class="right monto-bs">{total_bs_formateado}</td>
                            </tr>
                        </tfoot>
                    </table>
                </div>

                <hr class="separator">

                <div class="valores">
                    <div class="row"><span class="row-label">VALOR POR DÍA (Bs.):</span> <span>{valor_diario_bs_formateado}</span></div>
                    <div class="row"><span class="row-label">TASA BCV:</span> <span>{tasa_bcv_formateada}</span></div>
                </div>

                <hr class="separator">

                <div class="declaracion">
                    <div style="font-weight:bold; margin-bottom:4px;">DECLARACIÓN Y FIRMAS</div>
                    <p>Declaro que he recibido el total indicado y recibo de conformidad
                    con lo establecido en el Art. 30 del Reglamento de la Ley de
                    Alimentación para los trabajadores y trabajadoras, declaro que
                    he recibido de la empresa AGROAVICOLA DEL LLANO, C.A. las
                    cantidades arriba descritas, a través de transferencia bancaria.</p>
                </div>

                <div class="firmas">
                    <div class="firma">
                        <div class="linea"></div>
                        <div style="font-size:9px;">RECIBO CONFORME</div>
                        <div style="font-size:9px; color:#555;">FIRMA, CÉDULA</div>
                    </div>
                    <div class="firma">
                        <div class="linea"></div>
                        <div style="font-size:9px;">SELLO HÚMEDO</div>
                    </div>
                </div>

                <div style="text-align:center; margin:8px 0;">
                    <div style="font-size:12px; font-weight:bold;">HUELLAS</div>
                </div>

                <div style="text-align:right; font-size:10px; margin-top:4px;">
                    Fecha: {fecha_actual}
                </div>

                <div class="footer">
                    FIN DEL RECIBO &nbsp;|&nbsp; LOTE: {lote_id} - ID: {id}
                </div>
            </div>

            <script>
                function imprimirRecibo() {{
                    window.print();
                }}
                document.addEventListener('keydown', function(e) {{
                    if ((e.ctrlKey || e.metaKey) && e.key === 'p') {{
                        e.preventDefault();
                        window.print();
                    }}
                }});
            </script>
        </body>
        </html>
        '''
        
        return html, 200, {'Content-Type': 'text/html'}
        
    except Exception as e:
        print(f"❌ Error generando recibo HTML: {e}")
        import traceback
        traceback.print_exc()
        return f"<h1>Error al generar el recibo</h1><p>{str(e)}</p>", 500

# ============================================
# 🆕 NUEVO ENDPOINT: RECIBO DE NÓMINA EN HTML (ESTILO ODOO)
# ============================================
@app.route('/api/recibo_nomina_html/<int:id_nomina>', methods=['GET'])
@login_required
def recibo_nomina_html(id_nomina):
    """
    Genera una vista HTML del recibo de Nómina para impresión en estilo Odoo
    TODOS LOS MONTOS EN BOLÍVARES (Bs.) - CON DÓLARES COMO REFERENCIA
    """
    try:
        conn = get_db_connection()
        if not conn:
            return "<h1>Error de conexión a la base de datos</h1>", 500
        cur = conn.cursor()

        cur.execute('''
            SELECT 
                n.id_nomina,
                n.id_empleado,
                n.fecha_inicio,
                n.fecha_fin,
                n.tipo,
                n.faltas_dias,
                n.salario_base_usd,
                n.horas_extras_usd,
                n.bono_complementario_usd,
                n.total_asignaciones_usd,
                n.total_deducciones_usd,
                n.neto_pagar_usd,
                n.neto_pagar_bs,
                n.tasa_bcv,
                n.fecha_calculo,
                n.sso_usd,
                n.rpe_usd,
                n.faov_usd,
                n.sso_bs,
                n.rpe_bs,
                n.faov_bs,
                n.descripcion,
                n.lote_id,
                e.nombres,
                e.apellidos,
                e.cedula,
                e.cargo,
                e.salario_mensual_usd,
                e.fecha_ingreso
            FROM nominas n
            JOIN empleados e ON n.id_empleado = e.id_empleado
            WHERE n.id_nomina = %s
        ''', (id_nomina,))

        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return "<h1>Recibo no encontrado</h1><p>El ID de la nómina no existe.</p>", 404

        conn = get_db_connection()
        rif_empresa = "J-505631349"
        nombre_cuenta = "AGROAVICOLA DEL LLANO, C.A"
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT valor FROM parametros WHERE clave = 'rif_empresa'")
            rif_row = cur.fetchone()
            if rif_row:
                rif_empresa = str(rif_row[0])
            cur.execute("SELECT valor FROM parametros WHERE clave = 'nombre_cuenta_empresa'")
            cuenta_row = cur.fetchone()
            if cuenta_row:
                nombre_cuenta = str(cuenta_row[0])
            cur.close()
            conn.close()

        (id_nomina, id_empleado, fecha_inicio, fecha_fin, tipo, faltas_dias,
         salario_base_usd, horas_extras_usd, bono_complementario_usd,
         total_asignaciones_usd, total_deducciones_usd, neto_pagar_usd,
         neto_pagar_bs, tasa_bcv, fecha_calculo, sso_usd, rpe_usd, faov_usd,
         sso_bs, rpe_bs, faov_bs, descripcion, lote_id,
         nombres, apellidos, cedula, cargo, salario_mensual_usd, fecha_ingreso) = row

        nombre_completo = f"{nombres} {apellidos}".strip()
        fecha_ingreso_str = fecha_ingreso.strftime("%d/%m/%Y") if fecha_ingreso else ''
        fecha_inicio_str = fecha_inicio.strftime("%d/%m/%Y") if fecha_inicio else ''
        fecha_fin_str = fecha_fin.strftime("%d/%m/%Y") if fecha_fin else ''
        fecha_calculo_str = fecha_calculo.strftime("%d/%m/%Y") if fecha_calculo else datetime.now().strftime("%d/%m/%Y")

        salario_base_bs = salario_base_usd * tasa_bcv
        horas_extras_bs = horas_extras_usd * tasa_bcv
        bono_comp_bs = bono_complementario_usd * tasa_bcv
        total_asignaciones_bs = total_asignaciones_usd * tasa_bcv
        total_deducciones_bs = total_deducciones_usd * tasa_bcv
        neto_bs = neto_pagar_bs
        salario_mensual_bs = salario_mensual_usd * tasa_bcv

        neto_base_bs = neto_bs - bono_comp_bs
        pago_60_bs = (neto_base_bs * 0.60) + bono_comp_bs if neto_pagar_usd > 0 else 0
        pago_40_bs = neto_base_bs * 0.40 if neto_pagar_usd > 0 else 0

        def fmt(n):
            return f"{n:,.2f}".replace(",", ".")

        salario_base_bs_str = fmt(salario_base_bs)
        horas_extras_bs_str = fmt(horas_extras_bs)
        bono_comp_bs_str = fmt(bono_comp_bs)
        total_asignaciones_bs_str = fmt(total_asignaciones_bs)
        total_deducciones_bs_str = fmt(total_deducciones_bs)
        neto_bs_str = fmt(neto_bs)
        sso_bs_str = fmt(sso_bs)
        rpe_bs_str = fmt(rpe_bs)
        faov_bs_str = fmt(faov_bs)
        tasa_bcv_str = fmt(tasa_bcv)
        salario_mensual_bs_str = fmt(salario_mensual_bs)
        pago_60_bs_str = fmt(pago_60_bs)
        pago_40_bs_str = fmt(pago_40_bs)

        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        hora_actual = datetime.now().strftime("%H:%M:%S")
        numero_recibo = f"NOM-{lote_id}-{cedula}"

        html = f'''
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Recibo de Nómina</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{
                    font-family: 'Courier New', Courier, monospace;
                    font-size: 11px;
                    background: #f0f0f0;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    padding: 20px;
                }}
                .recibo-container {{
                    background: white;
                    width: 210mm;
                    padding: 15mm 12mm;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                    border-radius: 4px;
                }}
                @media print {{
                    body {{ background: white; padding: 0; }}
                    .recibo-container {{
                        box-shadow: none;
                        border-radius: 0;
                        padding: 10mm 12mm;
                        width: 100%;
                    }}
                    .no-print {{ display: none !important; }}
                }}
                .print-header {{
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 15px;
                    padding-bottom: 10px;
                    border-bottom: 1px solid #ddd;
                }}
                .btn-print {{
                    background: #1a2a6c;
                    color: white;
                    border: none;
                    padding: 8px 20px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 12px;
                    font-weight: 600;
                }}
                .btn-print:hover {{ background: #2d4373; }}
                .btn-pdf {{
                    background: #dc3545;
                    color: white;
                    border: none;
                    padding: 8px 20px;
                    border-radius: 4px;
                    cursor: pointer;
                    font-size: 12px;
                    font-weight: 600;
                    margin-left: 8px;
                }}
                .btn-pdf:hover {{ background: #c82333; }}
                .header {{
                    text-align: center;
                    margin-bottom: 10px;
                    padding-bottom: 8px;
                    border-bottom: 2px solid #000;
                }}
                .header .title {{ font-size: 16px; font-weight: bold; }}
                .header .subtitle {{ font-size: 12px; }}
                .header .rif {{ font-size: 11px; color: #555; }}
                .header .boleto {{ font-size: 13px; font-weight: bold; margin-top: 3px; }}
                .header .status {{ display: inline-block; padding: 2px 12px; background: #28a745; color: white; border-radius: 3px; font-size: 10px; font-weight: bold; }}
                .header .fecha-hora {{ font-size: 11px; color: #555; }}
                .separator {{ border: none; border-top: 1px dashed #999; margin: 6px 0; }}
                .section-title {{ font-weight: bold; font-size: 12px; margin: 8px 0 4px 0; }}
                .section {{ margin-bottom: 6px; }}
                .row {{ display: flex; justify-content: space-between; padding: 2px 0; }}
                .row-label {{ font-weight: bold; }}
                .table {{ width: 100%; border-collapse: collapse; margin: 4px 0; }}
                .table th {{ border-bottom: 2px solid #000; padding: 4px 6px; text-align: left; font-size: 10px; }}
                .table td {{ padding: 3px 6px; border-bottom: 1px solid #ddd; font-size: 10px; }}
                .table .total-row td {{ border-top: 2px solid #000; font-weight: bold; }}
                .table .total-row td:last-child {{ text-align: right; }}
                .table .right {{ text-align: right; }}
                .table .center {{ text-align: center; }}
                .valores {{
                    font-size: 10px;
                    margin: 4px 0;
                    padding: 4px 8px;
                    background: #f8f9fa;
                    border: 1px solid #ddd;
                    border-radius: 3px;
                }}
                .valores .row {{ padding: 1px 0; }}
                .declaracion {{
                    font-size: 10px;
                    margin: 8px 0;
                    padding: 6px;
                    border: 1px solid #ddd;
                    border-radius: 3px;
                    background: #fafafa;
                }}
                .firmas {{
                    margin: 10px 0;
                    display: flex;
                    justify-content: space-between;
                }}
                .firmas .firma {{
                    text-align: center;
                    width: 45%;
                }}
                .firmas .linea {{
                    border-top: 1px solid #000;
                    width: 80%;
                    margin: 20px auto 4px auto;
                }}
                .footer {{
                    text-align: center;
                    font-size: 9px;
                    color: #888;
                    margin-top: 8px;
                    border-top: 2px solid #000;
                    padding-top: 6px;
                }}
                .monto-bs {{
                    font-weight: bold;
                    color: #1a2a6c;
                }}
                .monto-descuento {{
                    color: #dc3545;
                }}
                @media print {{
                    .recibo-container {{
                        padding: 8mm 10mm;
                    }}
                    .header .title {{ font-size: 14px; }}
                    .btn-print, .btn-pdf {{ display: none !important; }}
                }}
                @page {{
                    size: A4;
                    margin: 0;
                }}
            </style>
        </head>
        <body>
            <div class="recibo-container" id="recibo">
                <div class="print-header no-print">
                    <span style="font-weight: bold; font-size: 14px;">📄 Recibo de Nómina</span>
                    <div>
                        <button class="btn-print" onclick="window.print()">🖨️ Imprimir</button>
                        <button class="btn-pdf" onclick="window.print()">📥 PDF</button>
                    </div>
                </div>

                <div class="header">
                    <div class="title">{nombre_cuenta}</div>
                    <div class="rif">RIF: {rif_empresa}</div>
                    <div class="subtitle">RECIBO DE NÓMINA</div>
                    <div class="boleto">BOLETO: {numero_recibo}</div>
                    <div style="display:flex; justify-content:space-between; margin-top:4px;">
                        <span><span class="status">PAGADO</span></span>
                        <span class="fecha-hora">Fecha: {fecha_actual}  Hora: {hora_actual}</span>
                    </div>
                    <div style="font-size:10px; color:#555; margin-top:2px;">PAGO DE SUELDO Y BENEFICIOS</div>
                </div>

                <hr class="separator">

                <div class="section">
                    <div class="section-title">EMPLEADO</div>
                    <div class="row"><span class="row-label">NOMBRE:</span> <span>{nombre_completo}</span></div>
                    <div class="row"><span class="row-label">C.I.:</span> <span>{cedula}</span></div>
                    <div class="row"><span class="row-label">CARGO:</span> <span>{cargo}</span></div>
                    <div class="row"><span class="row-label">FECHA INGRESO:</span> <span>{fecha_ingreso_str}</span></div>
                    <div class="row"><span class="row-label">PERIODO:</span> <span>{fecha_inicio_str} al {fecha_fin_str}</span></div>
                    <div class="row"><span class="row-label">TIPO PAGO:</span> <span>{tipo}</span></div>
                </div>

                <hr class="separator">

                <div class="section">
                    <div class="section-title">DETALLE DE LIQUIDACIÓN</div>
                    <table class="table">
                        <thead>
                            <tr>
                                <th>CÓDIGO</th>
                                <th>CONCEPTO</th>
                                <th style="text-align:right;">MONTO Bs.</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td>1000</td>
                                <td>Salario Base del Período</td>
                                <td class="right">{salario_base_bs_str}</td>
                            </tr>
                            <tr>
                                <td>1004</td>
                                <td>Horas Extras</td>
                                <td class="right">{horas_extras_bs_str}</td>
                            </tr>
                            <tr>
                                <td>1010</td>
                                <td>Bono Complementario (Exento)</td>
                                <td class="right">{bono_comp_bs_str}</td>
                            </tr>
                            <tr style="font-weight:bold; border-top:1px solid #000;">
                                <td colspan="2">TOTAL ASIGNACIONES</td>
                                <td class="right monto-bs">{total_asignaciones_bs_str}</td>
                            </tr>
                            <tr>
                                <td>4900</td>
                                <td>Seguro Social (SSO) 4%</td>
                                <td class="right monto-descuento">({sso_bs_str})</td>
                            </tr>
                            <tr>
                                <td>4905</td>
                                <td>RPE 0.5%</td>
                                <td class="right monto-descuento">({rpe_bs_str})</td>
                            </tr>
                            <tr>
                                <td>4910</td>
                                <td>FAOV 1%</td>
                                <td class="right monto-descuento">({faov_bs_str})</td>
                            </tr>
                            <tr style="font-weight:bold; border-top:1px solid #000;">
                                <td colspan="2">TOTAL DEDUCCIONES</td>
                                <td class="right monto-descuento">({total_deducciones_bs_str})</td>
                            </tr>
                            <tr class="total-row">
                                <td colspan="2">LÍQUIDO A PAGAR</td>
                                <td class="right monto-bs">{neto_bs_str}</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <hr class="separator">

                <div class="section">
                    <div class="section-title">FORMA DE PAGO</div>
                    <div class="row"><span class="row-label">Pago en Cuenta (60% + Bono):</span> <span>{pago_60_bs_str}</span></div>
                    <div class="row"><span class="row-label">Pago en Efectivo (40%):</span> <span>{pago_40_bs_str}</span></div>
                </div>

                <hr class="separator">

                <div class="valores">
                    <div class="row"><span class="row-label">Salario Mensual (Bs.):</span> <span>{salario_mensual_bs_str}</span></div>
                    <div class="row"><span class="row-label">Salario Mensual (USD):</span> <span>${salario_mensual_usd:.2f}</span></div>
                    <div class="row"><span class="row-label">Tasa BCV:</span> <span>{tasa_bcv_str}</span></div>
                </div>

                <hr class="separator">

                <div class="declaracion">
                    <div style="font-weight:bold; margin-bottom:4px;">DECLARACIÓN Y FIRMAS</div>
                    <p>Declaro que he recibido el total indicado y recibo de conformidad
                    con lo establecido en la Ley Orgánica del Trabajo, los Trabajadores y las Trabajadoras (LOTTT).
                    </p>
                </div>

                <div class="firmas">
                    <div class="firma">
                        <div class="linea"></div>
                        <div style="font-size:9px;">RECIBO CONFORME</div>
                        <div style="font-size:9px; color:#555;">FIRMA, CÉDULA</div>
                    </div>
                    <div class="firma">
                        <div class="linea"></div>
                        <div style="font-size:9px;">SELLO HÚMEDO</div>
                    </div>
                </div>

                <div style="text-align:center; margin:8px 0;">
                    <div style="font-size:12px; font-weight:bold;">HUELLAS</div>
                </div>

                <div style="text-align:right; font-size:10px; margin-top:4px;">
                    Fecha: {fecha_actual}
                </div>

                <div class="footer">
                    FIN DEL RECIBO &nbsp;|&nbsp; LOTE: {lote_id} - ID: {id_nomina}
                </div>
            </div>

            <script>
                function imprimirRecibo() {{
                    window.print();
                }}
                document.addEventListener('keydown', function(e) {{
                    if ((e.ctrlKey || e.metaKey) && e.key === 'p') {{
                        e.preventDefault();
                        window.print();
                    }}
                }});
            </script>
        </body>
        </html>
        '''

        return html, 200, {'Content-Type': 'text/html'}

    except Exception as e:
        print(f"❌ Error generando recibo HTML de nómina: {e}")
        import traceback
        traceback.print_exc()
        return f"<h1>Error al generar el recibo</h1><p>{str(e)}</p>", 500

# ============================================
# RECIBO CESTATICKET PARA MATRIZ DE PUNTO (VERSIÓN TXT)
# ============================================
@app.route('/api/generar_recibo_cestaticket_matriz/<int:id>', methods=['GET'])
@login_required
def generar_recibo_cestaticket_matriz(id):
    """
    Genera un recibo de pago de Cestaticket en formato TXT para impresión en matriz de punto
    TODOS LOS MONTOS EN BOLÍVARES (Bs.) - SIN DÓLARES
    """
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()
        
        cur.execute('''
            SELECT 
                c.id, 
                c.id_empleado, 
                c.fecha_inicio, 
                c.fecha_fin, 
                c.dias_pagados, 
                c.valor_diario_usd, 
                c.tasa_bcv, 
                c.total_usd, 
                c.total_bs, 
                c.descripcion, 
                c.lote_id,
                e.nombres, 
                e.apellidos, 
                e.cedula, 
                e.cargo,
                e.fecha_ingreso
            FROM cestaticket_nominas c
            JOIN empleados e ON c.id_empleado = e.id_empleado
            WHERE c.id = %s
        ''', (id,))
        
        row = cur.fetchone()
        cur.close()
        conn.close()
        
        if not row:
            return jsonify({'error': 'Cestaticket no encontrado'}), 404

        conn = get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT valor FROM parametros WHERE clave = 'rif_empresa'")
            rif_row = cur.fetchone()
            rif_empresa = str(rif_row[0]) if rif_row else "J-505631349"
            
            cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
            tasa_row = cur.fetchone()
            tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
            
            cur.execute("SELECT valor FROM parametros WHERE clave = 'cestaticket_usd'")
            cesta_row = cur.fetchone()
            valor_mensual_usd = float(cesta_row[0]) if cesta_row else 40.0
            
            cur.execute("SELECT valor FROM parametros WHERE clave = 'nombre_cuenta_empresa'")
            cuenta_row = cur.fetchone()
            nombre_cuenta = str(cuenta_row[0]) if cuenta_row else "AGROAVICOLA DEL LLANO, C.A"
            
            cur.close()
            conn.close()
        else:
            rif_empresa = "J-505631349"
            tasa_bcv = 755.1552
            valor_mensual_usd = 40.0
            nombre_cuenta = "AGROAVICOLA DEL LLANO, C.A"

        nombres = row[11] or ''
        apellidos = row[12] or ''
        nombre_completo = f"{nombres} {apellidos}".strip()
        cedula = row[13] or ''
        cargo = row[14] or ''
        fecha_ingreso = row[15].strftime("%d/%m/%Y") if row[15] else ''
        
        fecha_inicio = row[2].strftime("%d/%m/%Y") if row[2] else ''
        fecha_fin = row[3].strftime("%d/%m/%Y") if row[3] else ''
        dias_pagados = row[4] if row[4] else 30
        valor_diario_usd = float(row[5]) if row[5] else (valor_mensual_usd / 30)
        total_bs = float(row[8]) if row[8] else 0
        lote_id = row[10]
        
        valor_diario_bs = valor_diario_usd * tasa_bcv
        faltas = 30 - dias_pagados
        descuento_bs = faltas * valor_diario_bs
        
        total_bs_calculado = dias_pagados * valor_diario_bs
        if total_bs == 0:
            total_bs = total_bs_calculado
        
        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        hora_actual = datetime.now().strftime("%H:%M:%S")
        numero_recibo = f"CESTA-{lote_id}-{cedula}"
        
        total_bs_str = f"{total_bs:,.2f}".replace(",", ".")
        descuento_bs_str = f"{descuento_bs:,.2f}".replace(",", ".")
        valor_diario_bs_str = f"{valor_diario_bs:,.2f}".replace(",", ".")
        tasa_bcv_str = f"{tasa_bcv:,.4f}".replace(",", ".")
        
        buffer = StringIO()
        
        buffer.write("=" * 80 + "\n")
        buffer.write("\n")
        buffer.write(" " * 20 + "AGROAVICOLA DEL LLANO, C.A." + "\n")
        buffer.write(" " * 28 + f"RIF: {rif_empresa}" + "\n")
        buffer.write(" " * 22 + "RECIBO DE CESTATICKET" + "\n")
        buffer.write("\n")
        buffer.write(" " * 30 + f"BOLETO: {numero_recibo}" + "\n")
        buffer.write(" " * 20 + "-" * 40 + "\n")
        buffer.write(" " * 20 + f"Status: PAGADO" + " " * 30 + f"Fecha: {fecha_actual}" + "\n")
        buffer.write(" " * 20 + f"Hora: {hora_actual}" + "\n")
        buffer.write(" " * 20 + "-" * 40 + "\n")
        buffer.write(" " * 20 + "PAGO DE CESTATICKET SOCIALISTA" + "\n")
        buffer.write("\n")
        
        buffer.write("-" * 80 + "\n")
        buffer.write(" " * 0 + "EMPLEADO:" + "\n")
        buffer.write("-" * 80 + "\n")
        buffer.write(" " * 0 + "NOMBRE: " + nombre_completo.ljust(45) + "C.I: " + cedula + "\n")
        buffer.write(" " * 0 + "CARGO: " + cargo.ljust(48) + "FECHA INGRESO: " + fecha_ingreso + "\n")
        buffer.write(" " * 0 + "PERIODO: " + fecha_inicio + " al " + fecha_fin + "\n")
        buffer.write("-" * 80 + "\n")
        
        buffer.write("\n")
        buffer.write(" " * 0 + "DETALLE DEL PAGO" + "\n")
        buffer.write("-" * 80 + "\n")
        buffer.write(" " * 0 + "CONCEPTO" + " " * 40 + "CANTIDAD" + " " * 15 + "MONTO Bs." + "\n")
        buffer.write("-" * 80 + "\n")
        
        buffer.write(" " * 0 + "CESTA TICKET SOCIALISTA" + " " * 22 + f"{dias_pagados:>8} DÍAS" + " " * 5 + f"{total_bs_str:>14}" + "\n")
        
        if faltas > 0:
            buffer.write(" " * 0 + "(-) DESCUENTO POR FALTAS" + " " * 17 + f"{faltas:>8} DÍAS" + " " * 5 + f"({descuento_bs_str:>13})" + "\n")
        
        buffer.write("-" * 80 + "\n")
        buffer.write(" " * 0 + " " * 56 + "TOTAL A PAGAR: " + f"{total_bs_str:>14}" + "\n")
        buffer.write("=" * 80 + "\n")
        
        buffer.write("\n")
        buffer.write(" " * 0 + "VALORES DE REFERENCIA" + "\n")
        buffer.write("-" * 80 + "\n")
        buffer.write(" " * 0 + "VALOR POR DÍA (Bs.): " + f"{valor_diario_bs_str:>14}" + "\n")
        buffer.write(" " * 0 + "TASA BCV: " + f"{tasa_bcv_str:>14}" + "\n")
        buffer.write("-" * 80 + "\n")
        
        buffer.write("\n")
        buffer.write("DECLARACIÓN Y FIRMAS" + "\n")
        buffer.write("-" * 80 + "\n")
        buffer.write("\n")
        buffer.write("Declaro que he recibido el total indicado y recibo de conformidad\n")
        buffer.write("con lo establecido en el Art. 30 del Reglamento de la Ley de\n")
        buffer.write("Alimentación para los trabajadores y trabajadoras, declaro que\n")
        buffer.write("he recibido de la empresa AGROAVICOLA DEL LLANO, C.A. las\n")
        buffer.write("cantidades arriba descritas, a traves de transferencia bancaria.\n")
        buffer.write("\n")
        buffer.write("-" * 80 + "\n")
        buffer.write("\n")
        buffer.write(" " * 10 + "RECIBO CONFORME:" + "\n")
        buffer.write("\n")
        buffer.write(" " * 15 + "_________________________" + "\n")
        buffer.write(" " * 15 + "FIRMA, CÉDULA" + "\n")
        buffer.write("\n")
        buffer.write(" " * 15 + "_________________________" + "\n")
        buffer.write(" " * 15 + "SELLO HÚMEDO" + "\n")
        buffer.write("\n")
        buffer.write("\n")
        buffer.write(" " * 10 + "HUELLAS" + "\n")
        buffer.write("\n")
        buffer.write(" " * 50 + f"Fecha: {fecha_actual}" + "\n")
        buffer.write("\n")
        buffer.write("=" * 80 + "\n")
        buffer.write(" " * 30 + "FIN DEL RECIBO" + "\n")
        buffer.write("=" * 80 + "\n")
        buffer.write(" " * 25 + f"LOTE: {lote_id} - ID: {id}" + "\n")
        buffer.write("=" * 80 + "\n")
        
        mem = BytesIO()
        mem.write(buffer.getvalue().encode('cp850'))
        mem.seek(0)
        buffer.close()
        
        return send_file(
            mem,
            as_attachment=True,
            download_name=f"RECIBO_CESTA_{cedula}_{datetime.now().strftime('%Y%m%d')}.txt",
            mimetype='text/plain'
        )
        
    except Exception as e:
        print(f"❌ Error generando recibo en matriz: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Error interno: {str(e)}'}), 500

# ============================================
# REPORTE DE PASIVOS LABORALES
# ============================================
@app.route('/api/reporte_pasivos', methods=['POST'])
@login_required
def reporte_pasivos():
    """
    Calcula los pasivos laborales de un empleado o todos los empleados
    Basado en la LOTTT (Ley Orgánica del Trabajo, los Trabajadores y las Trabajadoras)
    
    - Utilidades (Art. 131): 30 días mínimo, 120 días máximo
    - Aguinaldo (Art. 132): 15 días mínimo, 30 días máximo
    - Vacaciones (Art. 190): 15 días + 1 día por año (máx 30)
    - Bono Vacacional (Art. 192): 7 días + 1 día por año (máx 21)
    """
    data = request.json
    empleado_id = data.get('empleado_id')
    calcular_para_todos = data.get('calcular_para_todos', False)
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cur = conn.cursor()
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone()
    tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    
    if calcular_para_todos:
        cur.execute("SELECT * FROM empleados WHERE activo = 1 ORDER BY nombres")
    else:
        if not empleado_id:
            return jsonify({'error': 'Se requiere empleado_id o calcular_para_todos=true'}), 400
        cur.execute("SELECT * FROM empleados WHERE id_empleado = %s AND activo = 1", (empleado_id,))
    
    empleados = cur.fetchall()
    cur.close()
    conn.close()
    
    if not empleados:
        return jsonify({'error': 'No se encontraron empleados'}), 404
    
    resultados = []
    total_general_usd = 0
    total_general_bs = 0
    
    for emp in empleados:
        empleado = {
            'id': emp[0],
            'cedula': emp[1],
            'nombre': f"{emp[2]} {emp[3]}",
            'cargo': emp[6] or 'Sin cargo',
            'fecha_ingreso': emp[5].isoformat() if emp[5] else None,
            'salario_mensual_usd': float(emp[9]) if emp[9] else 0,
            'tipo_pago': emp[10] or 'Quincenal'
        }
        
        if emp[5]:
            fecha_ingreso = emp[5]
            hoy = datetime.now().date()
            anos = hoy.year - fecha_ingreso.year
            meses = hoy.month - fecha_ingreso.month
            dias = hoy.day - fecha_ingreso.day
            
            if dias < 0:
                meses -= 1
                dias += 30
            if meses < 0:
                anos -= 1
                meses += 12
            
            antiguedad = {
                'anos': anos,
                'meses': meses,
                'dias': dias,
                'total_anos': anos + (meses / 12) + (dias / 365)
            }
        else:
            antiguedad = {'anos': 0, 'meses': 0, 'dias': 0, 'total_anos': 0}
        
        salario_base_usd = empleado['salario_mensual_usd']
        salario_base_bs = salario_base_usd * tasa_bcv
        
        alicuota_utilidades = (salario_base_usd * 15 / 12) / 30
        alicuota_bono_vacacional = (salario_base_usd * 7 / 12) / 30
        
        salario_integral_usd = salario_base_usd + alicuota_utilidades + alicuota_bono_vacacional
        salario_integral_bs = salario_integral_usd * tasa_bcv
        
        dias_utilidades = min(120, max(30, 30 + (antiguedad['anos'] * 10)))
        utilidades_usd = (salario_integral_usd / 30) * dias_utilidades
        utilidades_bs = utilidades_usd * tasa_bcv
        
        dias_aguinaldo = min(30, max(15, 15 + antiguedad['anos']))
        aguinaldo_usd = (salario_base_usd / 30) * dias_aguinaldo
        aguinaldo_bs = aguinaldo_usd * tasa_bcv
        
        dias_vacaciones = min(30, 15 + antiguedad['anos'])
        vacaciones_usd = (salario_base_usd / 30) * dias_vacaciones
        vacaciones_bs = vacaciones_usd * tasa_bcv
        
        dias_bono_vacacional = min(21, 7 + antiguedad['anos'])
        bono_vacacional_usd = (salario_base_usd / 30) * dias_bono_vacacional
        bono_vacacional_bs = bono_vacacional_usd * tasa_bcv
        
        total_pasivos_usd = utilidades_usd + aguinaldo_usd + vacaciones_usd + bono_vacacional_usd
        total_pasivos_bs = total_pasivos_usd * tasa_bcv
        
        total_general_usd += total_pasivos_usd
        total_general_bs += total_pasivos_bs
        
        resultados.append({
            'empleado': empleado,
            'antiguedad': antiguedad,
            'salario_base_usd': salario_base_usd,
            'salario_base_bs': salario_base_bs,
            'alicuota_utilidades_usd': alicuota_utilidades,
            'alicuota_bono_vacacional_usd': alicuota_bono_vacacional,
            'salario_integral_usd': salario_integral_usd,
            'salario_integral_bs': salario_integral_bs,
            'utilidades': {
                'dias': dias_utilidades,
                'usd': utilidades_usd,
                'bs': utilidades_bs
            },
            'aguinaldo': {
                'dias': dias_aguinaldo,
                'usd': aguinaldo_usd,
                'bs': aguinaldo_bs
            },
            'vacaciones': {
                'dias': dias_vacaciones,
                'usd': vacaciones_usd,
                'bs': vacaciones_bs
            },
            'bono_vacacional': {
                'dias': dias_bono_vacacional,
                'usd': bono_vacacional_usd,
                'bs': bono_vacacional_bs
            },
            'total_pasivos_usd': total_pasivos_usd,
            'total_pasivos_bs': total_pasivos_bs
        })
    
    return jsonify({
        'tasa_bcv': tasa_bcv,
        'total_general_usd': total_general_usd,
        'total_general_bs': total_general_bs,
        'resultados': resultados
    })

# ============================================
# REPORTE PARAFISCALES
# ============================================
@app.route('/api/reporte_parafiscales', methods=['POST'])
@login_required
def reporte_parafiscales():
    """
    Genera el reporte de descuentos parafiscales:
    - IVSS: 4%
    - RPE: 0.5%
    - FAOV: 1%
    - LPPP: Estimado de prestaciones (9% del salario integral)
    """
    data = request.json
    fecha_inicio = data.get('fecha_inicio')
    fecha_fin = data.get('fecha_fin')
    empleado_id = data.get('empleado_id')
    
    if not fecha_inicio or not fecha_fin:
        return jsonify({'error': 'Fechas requeridas'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cur = conn.cursor()
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone()
    tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'porcentaje_ivss'")
    ivss_row = cur.fetchone()
    porcentaje_ivss = float(ivss_row[0]) if ivss_row else 0.04
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'porcentaje_rpe'")
    rpe_row = cur.fetchone()
    porcentaje_rpe = float(rpe_row[0]) if rpe_row else 0.005
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'porcentaje_faov'")
    faov_row = cur.fetchone()
    porcentaje_faov = float(faov_row[0]) if faov_row else 0.01
    
    query = """
        SELECT 
            n.id_nomina,
            n.id_empleado,
            n.fecha_calculo,
            n.total_asignaciones_usd,
            n.sso_usd,
            n.rpe_usd,
            n.faov_usd,
            n.sso_bs,
            n.rpe_bs,
            n.faov_bs,
            e.nombres,
            e.apellidos,
            e.cedula,
            e.salario_mensual_usd
        FROM nominas n
        JOIN empleados e ON n.id_empleado = e.id_empleado
        WHERE n.fecha_calculo BETWEEN %s AND %s
    """
    params = [fecha_inicio, fecha_fin]
    
    if empleado_id:
        query += " AND n.id_empleado = %s"
        params.append(empleado_id)
    
    query += " ORDER BY e.nombres, n.fecha_calculo"
    
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        return jsonify({'error': 'No hay nóminas en el período seleccionado'}), 404
    
    empleados_dict = {}
    total_ivss = 0
    total_rpe = 0
    total_faov = 0
    total_lppp = 0
    total_asignaciones = 0
    
    for row in rows:
        emp_id = row[1]
        if emp_id not in empleados_dict:
            empleados_dict[emp_id] = {
                'cedula': row[12],
                'nombre': f"{row[10]} {row[11]}",
                'salario_mensual_usd': float(row[13]) if row[13] else 0,
                'nominas': [],
                'total_ivss': 0,
                'total_rpe': 0,
                'total_faov': 0,
                'total_lppp': 0,
                'total_asignaciones': 0
            }
        
        sso_usd = float(row[4]) if row[4] else 0
        rpe_usd = float(row[5]) if row[5] else 0
        faov_usd = float(row[6]) if row[6] else 0
        asignaciones = float(row[3]) if row[3] else 0
        
        empleados_dict[emp_id]['total_ivss'] += sso_usd
        empleados_dict[emp_id]['total_rpe'] += rpe_usd
        empleados_dict[emp_id]['total_faov'] += faov_usd
        empleados_dict[emp_id]['total_asignaciones'] += asignaciones
        
        total_ivss += sso_usd
        total_rpe += rpe_usd
        total_faov += faov_usd
        total_asignaciones += asignaciones
    
    for emp_id, emp_data in empleados_dict.items():
        lppp = emp_data['total_asignaciones'] * 0.09
        emp_data['total_lppp'] = lppp
        total_lppp += lppp
    
    return jsonify({
        'tasa_bcv': tasa_bcv,
        'porcentaje_ivss': porcentaje_ivss,
        'porcentaje_rpe': porcentaje_rpe,
        'porcentaje_faov': porcentaje_faov,
        'periodo': {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin
        },
        'totales': {
            'ivss_usd': total_ivss,
            'ivss_bs': total_ivss * tasa_bcv,
            'rpe_usd': total_rpe,
            'rpe_bs': total_rpe * tasa_bcv,
            'faov_usd': total_faov,
            'faov_bs': total_faov * tasa_bcv,
            'lppp_usd': total_lppp,
            'lppp_bs': total_lppp * tasa_bcv,
            'total_asignaciones_usd': total_asignaciones,
            'total_asignaciones_bs': total_asignaciones * tasa_bcv
        },
        'detalle_empleados': empleados_dict
    })

# ============================================
# RESUMEN EN DÓLARES
# ============================================
@app.route('/api/resumen_dolares', methods=['POST'])
@login_required
def resumen_dolares():
    """
    Genera un resumen mensual en dólares de todos los conceptos
    """
    data = request.json
    mes = data.get('mes')
    anio = data.get('anio')
    empleado_id = data.get('empleado_id')
    
    if not mes or not anio:
        return jsonify({'error': 'Mes y año requeridos'}), 400
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Error de conexión'}), 500
    
    cur = conn.cursor()
    
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone()
    tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    
    fecha_inicio = f"{anio}-{mes:02d}-01"
    if mes == 12:
        fecha_fin = f"{anio+1}-01-01"
    else:
        fecha_fin = f"{anio}-{mes+1:02d}-01"
    
    query = """
        SELECT 
            n.id_empleado,
            n.fecha_calculo,
            n.salario_base_usd,
            n.horas_extras_usd,
            n.bono_complementario_usd,
            n.total_asignaciones_usd,
            n.total_deducciones_usd,
            n.neto_pagar_usd,
            e.nombres,
            e.apellidos,
            e.cedula,
            e.tipo_pago
        FROM nominas n
        JOIN empleados e ON n.id_empleado = e.id_empleado
        WHERE n.fecha_calculo >= %s AND n.fecha_calculo < %s
    """
    params = [fecha_inicio, fecha_fin]
    
    if empleado_id:
        query += " AND n.id_empleado = %s"
        params.append(empleado_id)
    
    query += " ORDER BY e.nombres"
    
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        return jsonify({'error': 'No hay nóminas en el mes seleccionado'}), 404
    
    empleados_dict = {}
    total_salario = 0
    total_horas_extras = 0
    total_bono = 0
    total_asignaciones = 0
    total_deducciones = 0
    total_neto = 0
    
    for row in rows:
        emp_id = row[0]
        if emp_id not in empleados_dict:
            empleados_dict[emp_id] = {
                'cedula': row[10],
                'nombre': f"{row[8]} {row[9]}",
                'tipo_pago': row[11],
                'salario_usd': 0,
                'horas_extras_usd': 0,
                'bono_usd': 0,
                'asignaciones_usd': 0,
                'deducciones_usd': 0,
                'neto_usd': 0
            }
        
        salario = float(row[2]) if row[2] else 0
        horas_extras = float(row[3]) if row[3] else 0
        bono = float(row[4]) if row[4] else 0
        asignaciones = float(row[5]) if row[5] else 0
        deducciones = float(row[6]) if row[6] else 0
        neto = float(row[7]) if row[7] else 0
        
        empleados_dict[emp_id]['salario_usd'] += salario
        empleados_dict[emp_id]['horas_extras_usd'] += horas_extras
        empleados_dict[emp_id]['bono_usd'] += bono
        empleados_dict[emp_id]['asignaciones_usd'] += asignaciones
        empleados_dict[emp_id]['deducciones_usd'] += deducciones
        empleados_dict[emp_id]['neto_usd'] += neto
        
        total_salario += salario
        total_horas_extras += horas_extras
        total_bono += bono
        total_asignaciones += asignaciones
        total_deducciones += deducciones
        total_neto += neto
    
    return jsonify({
        'tasa_bcv': tasa_bcv,
        'periodo': {
            'mes': mes,
            'anio': anio,
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin
        },
        'totales': {
            'salario_usd': total_salario,
            'horas_extras_usd': total_horas_extras,
            'bono_usd': total_bono,
            'asignaciones_usd': total_asignaciones,
            'deducciones_usd': total_deducciones,
            'neto_usd': total_neto
        },
        'detalle_empleados': empleados_dict
    })

# ============================================
# GENERADOR DE PDF DEL LOTE DE NÓMINA
# ============================================
@app.route('/api/generar_lote_pdf/<int:lote_id>', methods=['GET'])
@login_required
def generar_lote_pdf(lote_id):
    try:
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()
        cur.execute("SELECT * FROM lotes_nomina WHERE id_lote = %s", (lote_id,))
        lote_row = cur.fetchone()
        if not lote_row: return jsonify({'error': 'Lote no encontrado'}), 404
        cur.execute('''
            SELECT 
                n.id_nomina, n.neto_pagar_usd, n.neto_pagar_bs, n.descripcion, n.tipo,
                e.nombres, e.apellidos, e.cedula, e.cargo, e.salario_mensual_usd
            FROM nominas n
            JOIN empleados e ON n.id_empleado = e.id_empleado
            WHERE n.lote_id = %s
            ORDER BY e.nombres
        ''', (lote_id,))
        nominas_rows = cur.fetchall()
        cur.close(); conn.close()

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
        elements = []
        styles = getSampleStyleSheet()
        normal_style = styles['Normal']
        title_style = ParagraphStyle(name='Title', fontSize=16, alignment=1, spaceAfter=10)
        
        logo_path = os.path.join(app.root_path, 'logo.png')
        try:
            logo = Image(logo_path)
            logo.drawHeight = 1.2*inch
            logo.drawWidth = 1.2*inch
            logo_table_data = [[logo, Paragraph(f"<b>Nómina Agroavícola del Llano</b>", title_style)]]
            logo_table = Table(logo_table_data, colWidths=[1.2*inch, 400])
            logo_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('ALIGN', (1,0), (1,0), 'RIGHT'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ]))
            elements.append(logo_table)
        except:
            elements.append(Paragraph(f"<b>Nómina Agroavícola del Llano</b>", title_style))
            
        elements.append(Paragraph(f"<b>Lote #{lote_row[0]} - {lote_row[1] or 'Sin descripción'}</b><br/><small>Generado el {lote_row[2].strftime('%d/%m/%Y')}</small>", normal_style))
        elements.append(Spacer(1, 10*mm))
        
        data = [["Cédula", "Empleado", "Cargo", "Salario Mensual", "Neto USD", "Neto Bs"]]
        total_usd = 0.0
        total_bs = 0.0
        
        for row in nominas_rows:
            nombre = f"{row[5]} {row[6]}"
            neto_usd = float(row[1]) if row[1] else 0
            neto_bs = float(row[2]) if row[2] else 0
            total_usd += neto_usd
            total_bs += neto_bs
            data.append([row[7], nombre, row[8] or '', f"${float(row[9]):.2f}" if row[9] else '', f"${neto_usd:.2f}", f"Bs. {neto_bs:.2f}"])
        
        data.append([
            "", "", "", 
            Paragraph("<b>TOTAL GENERAL</b>", normal_style), 
            Paragraph(f"<b>${total_usd:.2f}</b>", normal_style), 
            Paragraph(f"<b>Bs. {total_bs:.2f}</b>", normal_style)
        ])

        table = Table(data, colWidths=[80, 130, 120, 100, 80, 80])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightblue),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ]))
        elements.append(table)
        
        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"lote_{lote_id}.pdf", mimetype='application/pdf')
    except Exception as e:
        print(f"❌ Error generando PDF del lote: {e}")
        return jsonify({'error': f'Error interno generando el PDF del lote: {str(e)}'}), 500

# ============================================
# GENERADOR DE RECIBO DE NÓMINA (PDF - reportlab)
# ============================================
@app.route('/api/generar_recibo/<int:id_nomina>', methods=['GET'])
@login_required
def generar_recibo_pdf(id_nomina):
    try:
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()
        cur.execute('''
            SELECT 
                n.id_nomina, n.id_empleado, n.fecha_inicio, n.fecha_fin, n.tipo, n.faltas_dias, 
                n.salario_base_usd, n.horas_extras_usd, n.bono_complementario_usd, n.total_asignaciones_usd, 
                n.total_deducciones_usd, n.neto_pagar_usd, n.neto_pagar_bs, n.tasa_bcv, n.fecha_calculo, 
                n.sso_usd, n.rpe_usd, n.faov_usd, n.sso_bs, n.rpe_bs, n.faov_bs, 
                n.descripcion, n.lote_id,
                e.nombres, e.apellidos, e.cedula, e.cargo, e.salario_mensual_usd
            FROM nominas n
            JOIN empleados e ON n.id_empleado = e.id_empleado
            WHERE n.id_nomina = %s
        ''', (id_nomina,))
        
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row: return jsonify({'error': 'Nómina no encontrada'}), 404

        n = row 
        nombres = n[23] if n[23] else ''
        apellidos = n[24] if n[24] else ''
        cedula = n[25] if n[25] else ''
        cargo = n[26] if n[26] else ''
        salario_mensual_usd = float(n[27]) if n[27] else 0
        
        empleado_nombre = f"{nombres} {apellidos}".strip()
        
        fecha_inicio = n[2].strftime("%d/%m/%Y") if n[2] else ''
        fecha_fin = n[3].strftime("%d/%m/%Y") if n[3] else ''
        tipo = n[4]
        salario_base_usd = float(n[6]) if n[6] else 0
        horas_extras_usd = float(n[7]) if n[7] else 0
        bono_complementario_usd = float(n[8]) if n[8] else 0
        total_asignaciones_usd = float(n[9]) if n[9] else 0
        total_deducciones_usd = float(n[10]) if n[10] else 0
        neto_usd = float(n[11]) if n[11] else 0
        neto_bs = float(n[12]) if n[12] else 0
        sso_usd = float(n[15]) if n[15] else 0
        rpe_usd = float(n[16]) if n[16] else 0
        faov_usd = float(n[17]) if n[17] else 0
        tasa_bcv = float(n[13]) if n[13] else 0
        descripcion = n[21] or "Recibo de Nómina"

        salario_mensual_bs = salario_mensual_usd * tasa_bcv
        salario_base_bs = salario_base_usd * tasa_bcv
        horas_extras_bs = horas_extras_usd * tasa_bcv
        bono_complementario_bs = bono_complementario_usd * tasa_bcv
        total_asignaciones_bs = total_asignaciones_usd * tasa_bcv
        total_deducciones_bs = total_deducciones_usd * tasa_bcv
        sso_bs = sso_usd * tasa_bcv
        rpe_bs = rpe_usd * tasa_bcv
        faov_bs = faov_usd * tasa_bcv
        neto_base_bs = neto_bs - bono_complementario_bs
        pago_60_bs = (neto_base_bs * 0.60) + bono_complementario_bs if neto_usd > 0 else 0
        pago_40_bs = neto_base_bs * 0.40 if neto_usd > 0 else 0

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20*mm, rightMargin=20*mm, topMargin=20*mm, bottomMargin=20*mm)
        elements = []
        styles = getSampleStyleSheet()
        normal_style = ParagraphStyle(name='Normal', fontName='Helvetica', fontSize=9)
        bold_style = ParagraphStyle(name='Bold', parent=normal_style, fontName='Helvetica-Bold', fontSize=9)
        title_style = ParagraphStyle(name='Title', fontSize=14, alignment=1, spaceAfter=10)

        logo_path = os.path.join(app.root_path, 'logo.png')
        try:
            logo = Image(logo_path)
            logo.drawHeight = 1.2*inch
            logo.drawWidth = 1.2*inch
            logo_table_data = [[logo, Paragraph(f"<b>{descripcion}</b>", title_style)]]
            logo_table = Table(logo_table_data, colWidths=[1.2*inch, 400])
            logo_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('ALIGN', (1,0), (1,0), 'RIGHT'),
                ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ]))
            elements.append(logo_table)
        except:
            elements.append(Paragraph(f"<b>{descripcion}</b>", title_style))
        
        header_data = [
            [Paragraph(f"<b>Empleado:</b> {empleado_nombre}", normal_style), Paragraph(f"<b>Cédula:</b> {cedula}", normal_style)],
            [Paragraph(f"<b>Cargo:</b> {cargo}", normal_style), Paragraph(f"<b>Período:</b> {fecha_inicio} a {fecha_fin}", normal_style)],
            [Paragraph(f"<b>Salario Mensual:</b> Bs. {salario_mensual_bs:.2f}", normal_style), Paragraph(f"<b>Tasa BCV:</b> Bs. {tasa_bcv:.4f}", normal_style)],
        ]
        header_table = Table(header_data, colWidths=[250, 250])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 10*mm))

        concept_data = [
            [Paragraph("<b>Cód.</b>", bold_style), Paragraph("<b>Concepto</b>", bold_style), Paragraph("<b>Días</b>", bold_style), Paragraph("<b>Monto (Bs.)</b>", bold_style)],
            [Paragraph("1000", normal_style), Paragraph("Salario Base del Período", normal_style), Paragraph(f"{'11' if tipo == 'Quincenal' else '5'}" if tipo else '-', normal_style), Paragraph(f"Bs. {salario_base_bs:.2f}", normal_style)],
            [Paragraph("1004", normal_style), Paragraph("Horas Extras", normal_style), Paragraph("-", normal_style), Paragraph(f"Bs. {horas_extras_bs:.2f}", normal_style)],
            [Paragraph("1010", normal_style), Paragraph("Bono Complementario (*Exento de deducciones)", normal_style), Paragraph("-", normal_style), Paragraph(f"Bs. {bono_complementario_bs:.2f}", normal_style)],
            [Paragraph("---", normal_style), Paragraph("Total Asignaciones", normal_style), Paragraph("", normal_style), Paragraph(f"Bs. {total_asignaciones_bs:.2f}", bold_style)],
            [Paragraph("4900", normal_style), Paragraph("Seguro Social Obligatorio (SSO)", normal_style), Paragraph("-", normal_style), Paragraph(f"(Bs. {sso_bs:.2f})", normal_style)],
            [Paragraph("4905", normal_style), Paragraph("Régimen Prestacional Empleo (RPE)", normal_style), Paragraph("-", normal_style), Paragraph(f"(Bs. {rpe_bs:.2f})", normal_style)],
            [Paragraph("4910", normal_style), Paragraph("Fondo Ahorro Oblig. (FAOV)", normal_style), Paragraph("-", normal_style), Paragraph(f"(Bs. {faov_bs:.2f})", normal_style)],
            [Paragraph("---", normal_style), Paragraph("Total Deducciones", normal_style), Paragraph("", normal_style), Paragraph(f"(Bs. {total_deducciones_bs:.2f})", bold_style)],
        ]
        concept_table = Table(concept_data, colWidths=[50, 220, 60, 120])
        concept_table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.lightblue),
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('ALIGN', (0,0), (0,-1), 'CENTER'),
            ('ALIGN', (3,0), (3,-1), 'RIGHT'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(concept_table)
        elements.append(Spacer(1, 10*mm))

        footer_data = [
            [Paragraph("<b>Líquido a Pagar (Bs):</b>", normal_style), Paragraph(f"<b>Bs. {neto_bs:.2f}</b>", bold_style)],
            [Paragraph("", normal_style), Paragraph("", normal_style)],
            [Paragraph("<b>Pago en Cuenta (60% + Bono 100%):</b>", normal_style), Paragraph(f"Bs. {pago_60_bs:.2f}", normal_style)],
            [Paragraph("<b>Pago en Efectivo (40%):</b>", normal_style), Paragraph(f"Bs. {pago_40_bs:.2f}", normal_style)],
            [Paragraph("", normal_style), Paragraph("", normal_style)],
            [Paragraph("Generado por:", normal_style), Paragraph("Sistema de Nómina Agroavícola del Llano", normal_style)],
            [Paragraph("Fecha de Emisión:", normal_style), Paragraph(datetime.now().strftime("%d/%m/%Y %H:%M"), normal_style)],
        ]
        footer_table = Table(footer_data, colWidths=[170, 280])
        footer_table.setStyle(TableStyle([
            ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ('ALIGN', (0,0), (0,-1), 'LEFT'),
            ('ALIGN', (1,0), (1,-1), 'RIGHT'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        elements.append(footer_table)

        doc.build(elements)
        buffer.seek(0)
        return send_file(buffer, as_attachment=True, download_name=f"recibo_{id_nomina}.pdf", mimetype='application/pdf')
    except Exception as e:
        print(f"❌ Error fatal en generar_recibo_pdf: {e}")
        return jsonify({'error': str(e)}), 500

# ============================================
# 🔥 RUTA UNIFICADA PARA VER Y ELIMINAR LOTE (SOLUCIÓN AL 405)
# ============================================
@app.route('/api/lotes/<int:id>', methods=['GET', 'DELETE'])
@login_required
def manejar_lote(id):
    # ------------------------
    # SI ES UN GET (VER DETALLE)
    # ------------------------
    if request.method == 'GET':
        try:
            conn = get_db_connection()
            if not conn: return jsonify({'error': 'Error de conexión'}), 500
            cur = conn.cursor()
            cur.execute("SELECT * FROM lotes_nomina WHERE id_lote = %s", (id,))
            lote_row = cur.fetchone()
            if not lote_row: return jsonify({'error': 'Lote no encontrado'}), 404
            
            cur.execute('''
                SELECT 
                    n.id_nomina, n.id_empleado, n.fecha_inicio, n.fecha_fin, 
                    n.tipo, n.faltas_dias, n.salario_base_usd, 
                    n.horas_extras_usd, n.bono_complementario_usd, 
                    n.total_asignaciones_usd, n.total_deducciones_usd, 
                    n.neto_pagar_usd, n.neto_pagar_bs, 
                    n.sso_usd, n.rpe_usd, n.faov_usd,
                    n.sso_bs, n.rpe_bs, n.faov_bs,
                    e.nombres, e.apellidos, e.cedula
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s
                ORDER BY e.nombres
            ''', (id,))
            nominas_rows = cur.fetchall()
            cur.close(); conn.close()

            nominas = []
            for n in nominas_rows:
                nominas.append({
                    'id_nomina': n[0],
                    'id_empleado': n[1],
                    'fecha_inicio': n[2].isoformat() if n[2] else None,
                    'fecha_fin': n[3].isoformat() if n[3] else None,
                    'tipo': n[4],
                    'faltas_dias': n[5],
                    'salario_base_usd': float(n[6]) if n[6] else 0,
                    'horas_extras_usd': float(n[7]) if n[7] else 0,
                    'bono_complementario_usd': float(n[8]) if n[8] else 0,
                    'total_asignaciones_usd': float(n[9]) if n[9] else 0,
                    'total_deducciones_usd': float(n[10]) if n[10] else 0,
                    'neto_pagar_usd': float(n[11]) if n[11] else 0,
                    'neto_pagar_bs': float(n[12]) if n[12] else 0,
                    'sso_usd': float(n[13]) if n[13] else 0,
                    'rpe_usd': float(n[14]) if n[14] else 0,
                    'faov_usd': float(n[15]) if n[15] else 0,
                    'sso_bs': float(n[16]) if n[16] else 0,
                    'rpe_bs': float(n[17]) if n[17] else 0,
                    'faov_bs': float(n[18]) if n[18] else 0,
                    'nombres': n[19],
                    'apellidos': n[20],
                    'cedula': n[21]
                })

            return jsonify({
                'id_lote': lote_row[0],
                'descripcion': lote_row[1],
                'fecha_calculo': lote_row[2].isoformat() if lote_row[2] else None,
                'total_usd': float(lote_row[3]) if lote_row[3] else 0,
                'total_bs': float(lote_row[4]) if lote_row[4] else 0,
                'cantidad_empleados': lote_row[5] if lote_row[5] else 0,
                'nominas': nominas
            })
        except Exception as e:
            print(f"❌ Error crítico en get_lote_detalle: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': f'Error interno del servidor: {str(e)}'}), 500

    # ------------------------
    # SI ES UN DELETE (ELIMINAR)
    # ------------------------
    elif request.method == 'DELETE':
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM nominas WHERE lote_id = %s", (id,))
            cur.execute("DELETE FROM lotes_nomina WHERE id_lote = %s", (id,))
            conn.commit()
            return jsonify({'mensaje': 'Lote eliminado exitosamente'})
        except Exception as e:
            conn.rollback()
            return jsonify({'error': str(e)}), 400
        finally:
            cur.close(); conn.close()

# ============================================
# GENERADOR DE ARCHIVO DE PAGO (TXT)
# ============================================
@app.route('/api/generar_archivo_pago/<int:lote_id>', methods=['GET'])
@login_required
def generar_archivo_pago(lote_id):
    try:
        tipo = request.args.get('tipo', '60')
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión'}), 500
        cur = conn.cursor()

        cur.execute("SELECT valor FROM parametros WHERE clave = 'rif_empresa'")
        row = cur.fetchone()
        rif_empresa = str(row[0]) if row else "J409876136"

        cur.execute("SELECT valor FROM parametros WHERE clave = 'cuenta_empresa'")
        row = cur.fetchone()
        cuenta_empresa = str(row[0]) if row else "000102034732"

        cur.execute("SELECT valor FROM parametros WHERE clave = 'nombre_cuenta_empresa'")
        row = cur.fetchone()
        nombre_cuenta_empresa = str(row[0]) if row else "CODIZULCA"

        cur.execute("SELECT valor FROM parametros WHERE clave = 'codigo_banco_defecto'")
        row = cur.fetchone()
        codigo_banco = str(row[0]) if row else "BSCHVECA"

        cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
        row = cur.fetchone()
        tasa_bcv = float(row[0]) if row else 755.1552

        if tipo == '60':
            cur.execute('''
                SELECT 
                    e.cedula, 
                    e.cuenta_bancaria, 
                    e.nombres, 
                    e.apellidos,
                    (n.neto_pagar_bs * 0.60) + (n.bono_complementario_usd * n.tasa_bcv) as monto_pago_bs
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s 
                  AND e.cuenta_bancaria IS NOT NULL 
                  AND e.cuenta_bancaria != ''
            ''', (lote_id,))
        elif tipo == '40':
            cur.execute('''
                SELECT 
                    e.cedula, 
                    e.cuenta_bancaria, 
                    e.nombres, 
                    e.apellidos,
                    (n.neto_pagar_bs * 0.40) as monto_pago_bs
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s 
                  AND e.cuenta_bancaria IS NOT NULL 
                  AND e.cuenta_bancaria != ''
            ''', (lote_id,))
        else:  # 100%
            cur.execute('''
                SELECT 
                    e.cedula, 
                    e.cuenta_bancaria, 
                    e.nombres, 
                    e.apellidos,
                    n.neto_pagar_bs as monto_pago_bs
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s 
                  AND e.cuenta_bancaria IS NOT NULL 
                  AND e.cuenta_bancaria != ''
            ''', (lote_id,))

        rows = cur.fetchall()
        cur.close()
        conn.close()
        if not rows:
            return jsonify({'error': 'No hay empleados con cuentas bancarias registradas en este lote.'}), 404

        fecha_ejecucion = datetime.now().strftime("%d/%m/%Y")
        total_amount = 0.0
        buffer = StringIO()
        total_count = len(rows)
        header_line = f"HEADER  {total_count:08d}0011853{rif_empresa:<10}{fecha_ejecucion}{fecha_ejecucion}"
        buffer.write(header_line + "\n")
        for i, row in enumerate(rows, 1):
            cedula = str(row[0]) if row[0] else ''
            cuenta_empleado = str(row[1]) if row[1] else ''
            nombre = f"{row[2]} {row[3]}" if row[2] and row[3] else row[2] or row[3] or ''
            monto = float(row[4]) if row[4] else 0.0
            total_amount += monto
            monto_str = f"{monto:016.2f}".replace('.', ',')
            debit_line = (f"DEBITO  {i:08d}{rif_empresa:<10}{nombre_cuenta_empresa:<30}"
                          f"{fecha_ejecucion}{cuenta_empresa:<12}00000487092{monto_str:<21}VEB40 ")
            credit_line = (f"CREDITO {i:08d}{cedula:<10}{nombre:<29}"
                           f"{cuenta_empleado:<22}{monto_str:<21}00{codigo_banco:<8}")
            buffer.write(debit_line + "\n")
            buffer.write(credit_line + "\n")
        total_amount_str = f"{total_amount:015.2f}".replace('.', ',')
        total_line = f"TOTAL   {total_count:05d}{total_count:05d}{total_amount_str:<18}"
        buffer.write(total_line + "\n")
        mem = BytesIO()
        mem.write(buffer.getvalue().encode('cp1252'))
        mem.seek(0)
        buffer.close()
        return send_file(
            mem,
            as_attachment=True,
            download_name=f"PROV_{tipo}_{datetime.now().strftime('%Y%m%d')}.txt",
            mimetype='text/plain'
        )
    except Exception as e:
        print(f"❌ Error generando archivo de pago: {e}")
        return jsonify({'error': f'Error interno generando el archivo: {str(e)}'}), 500

with app.app_context():
    init_db()

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
