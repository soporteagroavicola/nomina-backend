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
    "http://localhost:5000",
    "http://127.0.0.1:5000"
]
CORS(app, origins=frontend_urls, supports_credentials=True, allow_headers=["Content-Type", "Authorization"])

def get_db_connection():
    database_url = 'postgresql://nomina_db_naiu_user:58sgnjVGnVRtLVbOVqYiA7d41VXwsHUH@dpg-d9prbrr9ik0c73ci4e0g-a.oregon-postgres.render.com/nomina_db_naiu'
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
        cur.execute("ALTER TABLE nominas ADD COLUMN IF NOT EXISTS descripcion TEXT")
        cur.execute("ALTER TABLE nominas ADD COLUMN IF NOT EXISTS lote_id INTEGER")
        cur.execute("ALTER TABLE nominas ADD COLUMN IF NOT EXISTS bono_complementario_usd REAL DEFAULT 0")
        
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

        conn.commit(); cur.close(); conn.close()
        print("✅ Base de datos inicializada y parámetros de empresa corregidos automáticamente.")
    except Exception as e:
        print(f"❌ ERROR GRAVE EN init_db: {e}")

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
            INSERT INTO nominas (id_empleado, fecha_inicio, fecha_fin, tipo, faltas_dias, salario_base_usd, horas_extras_usd, bono_complementario_usd, total_asignaciones_usd, total_deducciones_usd, neto_pagar_usd, neto_pagar_bs, tasa_bcv, fecha_calculo, sso_usd, rpe_usd, faov_usd, sso_bs, rpe_bs, faov_bs, descripcion, lote_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (emp[0], fecha_inicio, fecha_fin, tipo, calculo['faltas_dias'], calculo['salario_base_full_usd'], calculo['horas_extras_usd'], calculo['bono_complementario_usd'], calculo['total_asignaciones_base_usd'], calculo['total_deducciones_usd'], calculo['neto_pagar_usd'], calculo['neto_pagar_bs'], tasa_bcv, datetime.now().date(), calculo['sso_usd'], calculo['rpe_usd'], calculo['faov_usd'], calculo['sso_usd'] * tasa_bcv, calculo['rpe_usd'] * tasa_bcv, calculo['faov_usd'] * tasa_bcv, descripcion, lote_id))
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

@app.route('/api/lotes', methods=['GET'])
@login_required
def get_lotes():
    search = request.args.get('search', '')
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    query = '''
        SELECT l.*, COUNT(n.id_nomina) as total_empleados_detalle,
        STRING_AGG(DISTINCT s.nombre, ', ') as sucursales_involucradas
        FROM lotes_nomina l
        LEFT JOIN nominas n ON l.id_lote = n.lote_id
        LEFT JOIN empleados e ON n.id_empleado = e.id_empleado
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE 1=1
    '''
    params = []
    if search: query += " AND (l.descripcion ILIKE %s OR CAST(l.id_lote AS TEXT) ILIKE %s)"; sp = f"%{search}%"; params.extend([sp, sp])
    query += " GROUP BY l.id_lote ORDER BY l.fecha_calculo DESC, l.id_lote DESC"
    cur.execute(query, params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{
        'id_lote': r[0], 'descripcion': r[1], 'fecha_calculo': r[2].isoformat() if r[2] else None, 'total_usd': float(r[3]) if r[3] else 0,
        'total_bs': float(r[4]) if r[4] else 0, 'cantidad_empleados_lote': r[5],
        'sucursales_involucradas': r[7] or 'Mixto / Sin Sucursal'
    } for r in rows])

@app.route('/api/lotes/<int:id>', methods=['GET'])
@login_required
def get_lote_detalle(id):
    try:
        conn = get_db_connection()
        if not conn: return jsonify({'error': 'Error de conexión a la BD'}), 500
        cur = conn.cursor()
        cur.execute("SELECT * FROM lotes_nomina WHERE id_lote = %s", (id,))
        lote_row = cur.fetchone()
        if not lote_row: return jsonify({'error': 'Lote no encontrado'}), 404
        cur.execute('''
            SELECT 
                n.id_nomina, n.id_empleado, n.fecha_inicio, n.fecha_fin, n.tipo, n.faltas_dias, 
                n.salario_base_usd, n.horas_extras_usd, n.bono_complementario_usd, n.total_asignaciones_usd, 
                n.total_deducciones_usd, n.neto_pagar_usd, n.neto_pagar_bs, n.tasa_bcv, n.fecha_calculo, 
                n.sso_usd, n.rpe_usd, n.faov_usd, n.sso_bs, n.rpe_bs, n.faov_bs, 
                n.descripcion, n.lote_id,
                e.nombres, e.apellidos, e.cedula, s.id_sucursal, s.nombre as sucursal_nombre
            FROM nominas n
            JOIN empleados e ON n.id_empleado = e.id_empleado
            LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
            WHERE n.lote_id = %s
            ORDER BY e.nombres
        ''', (id,))
        nominas_rows = cur.fetchall()
        cur.close(); conn.close()
        nominas = []
        for n in nominas_rows:
            salario_base_usd = float(n[6]) if n[6] else 0
            nominas.append({
                'id_nomina': n[0], 'id_empleado': n[1], 
                'fecha_inicio': n[2].isoformat() if n[2] else None, 
                'fecha_fin': n[3].isoformat() if n[3] else None,
                'tipo': n[4], 'faltas_dias': n[5], 'salario_base_usd': salario_base_usd,
                'base_incidencia_60_usd': salario_base_usd * 0.60,
                'horas_extras_usd': float(n[7]) if n[7] else 0,
                'bono_complementario_usd': float(n[8]) if n[8] else 0,
                'total_asignaciones_usd': float(n[9]) if n[9] else 0,
                'total_deducciones_usd': float(n[10]) if n[10] else 0,
                'neto_pagar_usd': float(n[11]) if n[11] else 0,
                'neto_pagar_bs': float(n[12]) if n[12] else 0,
                'tasa_bcv': float(n[13]) if n[13] else 0,
                'fecha_calculo': n[14].isoformat() if n[14] else None,
                'sso_usd': float(n[15]) if n[15] else 0,
                'rpe_usd': float(n[16]) if n[16] else 0,
                'faov_usd': float(n[17]) if n[17] else 0,
                'sso_bs': float(n[18]) if n[18] else 0,
                'rpe_bs': float(n[19]) if n[19] else 0,
                'faov_bs': float(n[20]) if n[20] else 0,
                'descripcion': n[21] if n[21] else '',
                'lote_id': n[22] if n[22] else None,
                'nombres': n[23] if n[23] else '',
                'apellidos': n[24] if n[24] else '',
                'cedula': n[25] if n[25] else '',
                'sucursal_id': n[26] if n[26] else None,
                'sucursal_nombre': n[27] if n[27] else 'Sin sucursal'
            })
        return jsonify({
            'id_lote': lote_row[0], 'descripcion': lote_row[1], 'fecha_calculo': lote_row[2].isoformat() if lote_row[2] else None,
            'total_usd': float(lote_row[3]) if lote_row[3] else 0, 'total_bs': float(lote_row[4]) if lote_row[4] else 0,
            'cantidad_empleados_lote': lote_row[5],
            'nominas': nominas
        })
    except Exception as e:
        print(f"❌ Error crítico en get_lote_detalle: {e}")
        return jsonify({'error': f'Error interno del servidor: {str(e)}'}), 500

# ============================================
# 🏦 GENERADOR DE ARCHIVO TXT PARA EL BANCO (ACTUALIZADO CON 100%)
# ============================================
@app.route('/api/generar_archivo_pago/<int:lote_id>', methods=['GET'])
@login_required
def generar_archivo_pago(lote_id):
    tipo = request.args.get('tipo', '60')
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
        
        if tipo == '60':
            cur.execute('''
                SELECT 
                    e.cedula, 
                    e.cuenta_bancaria, 
                    e.nombres, 
                    e.apellidos,
                    ((n.neto_pagar_usd * 0.60) + n.bono_complementario_usd) * n.tasa_bcv as monto_pago_bs
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
                    (n.neto_pagar_usd * 0.40) * n.tasa_bcv as monto_pago_bs
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s 
                  AND e.cuenta_bancaria IS NOT NULL 
                  AND e.cuenta_bancaria != ''
            ''', (lote_id,))
        # 🆕 NUEVO TIPO: 100% DEL MONTO TOTAL
        elif tipo == '100':
            cur.execute('''
                SELECT 
                    e.cedula, 
                    e.cuenta_bancaria, 
                    e.nombres, 
                    e.apellidos,
                    n.neto_pagar_usd * n.tasa_bcv as monto_pago_bs
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s 
                  AND e.cuenta_bancaria IS NOT NULL 
                  AND e.cuenta_bancaria != ''
            ''', (lote_id,))
        else:
            return jsonify({'error': 'Tipo de archivo no válido'}), 400

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
            download_name=f"PROV_{tipo}_{datetime.now().strftime('%Y%m%d')}.txt",
            mimetype='text/plain'
        )
    except Exception as e:
        print(f"❌ Error generando archivo bancario: {e}")
        return jsonify({'error': f'Error interno generando el archivo de pago: {str(e)}'}), 500

# ============================================
# 🆕 GENERADOR DE PDF DEL LOTE COMPLETO (CON LOGO EN RAÍZ)
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
        
        # 🆕 LOGO (Ahora busca en la raíz)
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
# 📄 CORREGIDO Y ALINEADO: GENERADOR DE RECIBO PDF INDIVIDUAL (Logo en raíz)
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

        # Extraer datos con índices correctos
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

        # CONVERSIÓN A BOLÍVARES (Bs.)
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

        # 🆕 LOGO (Ahora busca en la raíz)
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
        
        # ENCABEZADO ALINEADO A 2 COLUMNAS
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

        # TABLA DE CONCEPTOS (Todos en Bs)
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

        # PIE DE PÁGINA Y PAGOS EN Bs.
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

@app.route('/api/lotes/<int:id>', methods=['DELETE'])
@login_required
def eliminar_lote(id):
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

with app.app_context(): init_db()
if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
