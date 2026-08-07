import os
import psycopg2
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

def get_db_connection():
    database_url = 'postgresql://nomina_db_naiu_user:58sgnjVGnVRtLVbOVqYiA7d41VXwsHUH@dpg-d9prbrr9ik0c73ci4e0g-a.oregon-postgres.render.com/nomina_db_naiu'
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except Exception as e:
        print(f"❌ Error conectando a la BD: {e}")
        return None

def init_db():
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
        CREATE TABLE IF NOT EXISTS nominas (
            id_nomina SERIAL PRIMARY KEY, id_empleado INTEGER NOT NULL, fecha_inicio DATE NOT NULL, fecha_fin DATE NOT NULL,
            tipo TEXT CHECK(tipo IN ('Quincenal', 'Semanal')), faltas_dias INTEGER DEFAULT 0, salario_base_usd REAL,
            horas_extras_usd REAL DEFAULT 0, total_asignaciones_usd REAL, total_deducciones_usd REAL,
            neto_pagar_usd REAL, neto_pagar_bs REAL, tasa_bcv REAL, fecha_calculo DATE,
            sso_usd REAL DEFAULT 0, rpe_usd REAL DEFAULT 0, faov_usd REAL DEFAULT 0,
            sso_bs REAL DEFAULT 0, rpe_bs REAL DEFAULT 0, faov_bs REAL DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS parametros (
            id SERIAL PRIMARY KEY, clave TEXT UNIQUE NOT NULL, valor REAL NOT NULL, fecha_actualizacion DATE
        )
    ''')
    cur.execute("SELECT * FROM parametros WHERE clave = 'tasa_bcv'")
    if not cur.fetchone(): cur.execute("INSERT INTO parametros (clave, valor) VALUES ('tasa_bcv', 755.1552)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'cestaticket_usd'")
    if not cur.fetchone(): cur.execute("INSERT INTO parametros (clave, valor) VALUES ('cestaticket_usd', 40.0)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'porcentaje_ivss'")
    if not cur.fetchone(): cur.execute("INSERT INTO parametros (clave, valor) VALUES ('porcentaje_ivss', 0.04)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'porcentaje_rpe'")
    if not cur.fetchone(): cur.execute("INSERT INTO parametros (clave, valor) VALUES ('porcentaje_rpe', 0.005)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'porcentaje_faov'")
    if not cur.fetchone(): cur.execute("INSERT INTO parametros (clave, valor) VALUES ('porcentaje_faov', 0.01)")
    conn.commit(); cur.close(); conn.close()
    print("✅ Base de datos inicializada correctamente")

# ============================================
# MÓDULO EMPLEADOS (CRUD + FILTROS)
# ============================================
@app.route('/api/empleados', methods=['GET'])
def get_empleados():
    search = request.args.get('search', '')
    sucursal_id = request.args.get('sucursal_id', '')
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    query = "SELECT * FROM empleados WHERE activo = 1"
    params = []
    if sucursal_id:
        query += " AND sucursal_id = %s"; params.append(sucursal_id)
    if search:
        query += " AND (cedula ILIKE %s OR nombres ILIKE %s OR apellidos ILIKE %s)"
        sp = f"%{search}%"; params.extend([sp, sp, sp])
    query += " ORDER BY nombres"
    cur.execute(query, params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{
        'id_empleado': r[0], 'cedula': r[1], 'nombres': r[2], 'apellidos': r[3],
        'fecha_nacimiento': r[4].isoformat() if r[4] else None,
        'fecha_ingreso': r[5].isoformat() if r[5] else None,
        'cargo': r[6], 'departamento': r[7], 'sucursal_id': r[8],
        'salario_mensual_usd': float(r[9]) if r[9] else 0,
        'tipo_pago': r[10], 'activo': r[11], 'email': r[12], 'telefono': r[13], 'direccion': r[14],
        'cuenta_bancaria': r[15]
    } for r in rows])

@app.route('/api/empleados', methods=['POST'])
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
def actualizar_empleado(id):
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute('''
            UPDATE empleados SET cedula=%s, nombres=%s, apellidos=%s, fecha_nacimiento=%s, fecha_ingreso=%s, cargo=%s, departamento=%s, sucursal_id=%s, salario_mensual_usd=%s, tipo_pago=%s, email=%s, telefono=%s, direccion=%s, cuenta_bancaria=%s
            WHERE id_empleado=%s
        ''', (data['cedula'], data['nombres'], data['apellidos'], data['fecha_nacimiento'], data['fecha_ingreso'], data['cargo'], data['departamento'], data['sucursal_id'], data['salario_mensual_usd'], data['tipo_pago'], data.get('email'), data.get('telefono'), data.get('direccion'), data.get('cuenta_bancaria'), id))
        conn.commit(); return jsonify({'mensaje': 'Empleado actualizado exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/empleados/<int:id>', methods=['DELETE'])
def eliminar_empleado(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("UPDATE empleados SET activo = 0 WHERE id_empleado = %s", (id,))
        conn.commit(); return jsonify({'mensaje': 'Empleado eliminado exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

# ============================================
# MÓDULO SUCURSALES
# ============================================
@app.route('/api/sucursales', methods=['GET'])
def get_sucursales():
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    cur.execute('SELECT * FROM sucursales WHERE activo = 1 ORDER BY nombre')
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{'id_sucursal': r[0], 'nombre': r[1], 'activo': r[2]} for r in rows])

@app.route('/api/sucursales', methods=['POST'])
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
def eliminar_sucursal(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("UPDATE sucursales SET activo = 0 WHERE id_sucursal = %s", (id,))
        conn.commit(); return jsonify({'mensaje': 'Sucursal eliminada exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

# ============================================
# MÓDULO NÓMINA Y PARÁMETROS (CALCULAR)
# ============================================
@app.route('/api/parametros', methods=['GET'])
def get_parametros():
    conn = get_db_connection()
    if not conn: return jsonify({})
    cur = conn.cursor()
    cur.execute("SELECT clave, valor FROM parametros")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify({row[0]: float(row[1]) for row in rows})

@app.route('/api/calcular_nomina', methods=['POST'])
def calcular_nomina():
    data = request.json
    tipo, fecha_inicio, fecha_fin = data.get('tipo'), data.get('fecha_inicio'), data.get('fecha_fin')
    empleados_ids, faltas_dict, horas_extras_dict = data.get('empleados_ids', []), data.get('faltas', {}), data.get('horas_extras', {})
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
    for emp in empleados:
        cedula = emp[1]
        faltas = faltas_dict.get(cedula, 0)
        horas_data = horas_extras_dict.get(cedula, {})
        horas, valor_hora = horas_data.get('horas', 0), horas_data.get('valor_hora', 0)
        salario_mensual = float(emp[9]) if emp[9] else 0
        salario_diario = salario_mensual / 30
        total_horas_extras = horas * valor_hora
        if tipo == 'Quincenal':
            salario_base = salario_mensual / 2
            total_asignaciones = salario_base - (faltas * salario_diario) + total_horas_extras
        else:
            dias_trabajados = max(0, 5 - faltas)
            total_asignaciones = (salario_diario * dias_trabajados) + total_horas_extras
        ivss, rpe, faov = total_asignaciones * 0.04, total_asignaciones * 0.005, total_asignaciones * 0.01
        total_deducciones = ivss + rpe + faov
        neto_usd = total_asignaciones - total_deducciones
        calculo = {
            'salario_base_usd': salario_base if tipo == 'Quincenal' else salario_diario * dias_trabajados,
            'horas_extras_usd': total_horas_extras, 'total_asignaciones_usd': total_asignaciones,
            'total_deducciones_usd': total_deducciones, 'sso_usd': ivss, 'rpe_usd': rpe, 'faov_usd': faov,
            'neto_pagar_usd': neto_usd, 'neto_pagar_bs': neto_usd * tasa_bcv, 'faltas_dias': faltas,
            'empleado': {'id': emp[0], 'cedula': cedula, 'nombre_completo': f"{emp[2]} {emp[3]}"}
        }
        resultados.append(calculo)
        cur.execute('''
            INSERT INTO nominas (...) VALUES (...)
        ''', (emp[0], fecha_inicio, fecha_fin, tipo, calculo['faltas_dias'], calculo['salario_base_usd'], calculo['horas_extras_usd'], calculo['total_asignaciones_usd'], calculo['total_deducciones_usd'], calculo['neto_pagar_usd'], calculo['neto_pagar_bs'], tasa_bcv, datetime.now().date(), calculo['sso_usd'], calculo['rpe_usd'], calculo['faov_usd'], calculo['sso_usd'] * tasa_bcv, calculo['rpe_usd'] * tasa_bcv, calculo['faov_usd'] * tasa_bcv))
    conn.commit(); cur.close(); conn.close()
    return jsonify({'tasa_bcv': tasa_bcv, 'resultados': resultados})

# ============================================
# NUEVO: MÓDULO HISTORIAL DE NÓMINAS (CRUD)
# ============================================
@app.route('/api/nominas', methods=['GET'])
def get_historico_nominas():
    search = request.args.get('search', '')
    sucursal_id = request.args.get('sucursal_id', '')
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    query = '''
        SELECT n.*, e.nombres, e.apellidos, e.cedula, s.id_sucursal, s.nombre as sucursal_nombre
        FROM nominas n
        JOIN empleados e ON n.id_empleado = e.id_empleado
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE 1=1
    '''
    params = []
    if sucursal_id:
        query += " AND s.id_sucursal = %s"; params.append(sucursal_id)
    if search:
        query += " AND (e.cedula ILIKE %s OR e.nombres ILIKE %s OR e.apellidos ILIKE %s)"
        sp = f"%{search}%"; params.extend([sp, sp, sp])
    query += " ORDER BY n.fecha_calculo DESC, n.id_nomina DESC"
    cur.execute(query, params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{
        'id_nomina': r[0], 'id_empleado': r[1], 'fecha_inicio': r[2].isoformat(), 'fecha_fin': r[3].isoformat(),
        'tipo': r[4], 'faltas_dias': r[5], 'salario_base_usd': float(r[6]) if r[6] else 0,
        'horas_extras_usd': float(r[7]) if r[7] else 0, 'total_asignaciones_usd': float(r[8]) if r[8] else 0,
        'total_deducciones_usd': float(r[9]) if r[9] else 0, 'neto_pagar_usd': float(r[10]) if r[10] else 0,
        'neto_pagar_bs': float(r[11]) if r[11] else 0, 'tasa_bcv': float(r[12]) if r[12] else 0,
        'fecha_calculo': r[13].isoformat(),
        'sso_usd': float(r[14]) if r[14] else 0, 'rpe_usd': float(r[15]) if r[15] else 0, 'faov_usd': float(r[16]) if r[16] else 0,
        'sso_bs': float(r[17]) if r[17] else 0, 'rpe_bs': float(r[18]) if r[18] else 0, 'faov_bs': float(r[19]) if r[19] else 0,
        'nombres': r[20], 'apellidos': r[21], 'cedula': r[22], 'sucursal_id': r[23], 'sucursal_nombre': r[24]
    } for r in rows])

@app.route('/api/nominas/<int:id>', methods=['GET'])
def get_nomina_por_id(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    cur.execute('''
        SELECT n.*, e.nombres, e.apellidos, e.cedula, s.id_sucursal, s.nombre as sucursal_nombre
        FROM nominas n
        JOIN empleados e ON n.id_empleado = e.id_empleado
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE n.id_nomina = %s
    ''', (id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row: return jsonify({'error': 'Nómina no encontrada'}), 404
    return jsonify({
        'id_nomina': row[0], 'id_empleado': row[1], 'fecha_inicio': row[2].isoformat(), 'fecha_fin': row[3].isoformat(),
        'tipo': row[4], 'faltas_dias': row[5], 'salario_base_usd': float(row[6]) if row[6] else 0,
        'horas_extras_usd': float(row[7]) if row[7] else 0, 'total_asignaciones_usd': float(row[8]) if row[8] else 0,
        'total_deducciones_usd': float(row[9]) if row[9] else 0, 'neto_pagar_usd': float(row[10]) if row[10] else 0,
        'neto_pagar_bs': float(row[11]) if row[11] else 0, 'tasa_bcv': float(row[12]) if row[12] else 0,
        'fecha_calculo': row[13].isoformat(),
        'sso_usd': float(row[14]) if row[14] else 0, 'rpe_usd': float(row[15]) if row[15] else 0, 'faov_usd': float(row[16]) if row[16] else 0,
        'sso_bs': float(row[17]) if row[17] else 0, 'rpe_bs': float(row[18]) if row[18] else 0, 'faov_bs': float(row[19]) if row[19] else 0,
        'nombres': row[20], 'apellidos': row[21], 'cedula': row[22], 'sucursal_id': row[23], 'sucursal_nombre': row[24]
    })

@app.route('/api/nominas/<int:id>', methods=['PUT'])
def actualizar_nomina(id):
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        # Obtener el empleado y los datos actuales
        cur.execute("SELECT id_empleado, tipo, fecha_inicio, fecha_fin, salario_base_usd FROM nominas WHERE id_nomina = %s", (id,))
        nomina_original = cur.fetchone()
        if not nomina_original: return jsonify({'error': 'Nómina no encontrada'}), 404
        emp_id, tipo, fecha_inicio, fecha_fin, salario_base_usd = nomina_original

        # Obtener el salario real del empleado para recalcular
        cur.execute("SELECT salario_mensual_usd FROM empleados WHERE id_empleado = %s", (emp_id,))
        salario_mensual_emp = cur.fetchone()
        salario_mensual = float(salario_mensual_emp[0]) if salario_mensual_emp else 0

        # Recalcular con los nuevos datos de faltas y horas extras
        faltas = data.get('faltas_dias', 0)
        horas = data.get('horas_extras', 0)
        valor_hora = data.get('valor_hora', 0)

        salario_diario = salario_mensual / 30
        total_horas_extras = horas * valor_hora
        if tipo == 'Quincenal':
            salario_base = salario_mensual / 2
            total_asignaciones = salario_base - (faltas * salario_diario) + total_horas_extras
        else: # Semanal
            dias_trabajados = max(0, 5 - faltas)
            total_asignaciones = (salario_diario * dias_trabajados) + total_horas_extras

        ivss, rpe, faov = total_asignaciones * 0.04, total_asignaciones * 0.005, total_asignaciones * 0.01
        total_deducciones = ivss + rpe + faov
        neto_usd = total_asignaciones - total_deducciones

        cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
        tasa_row = cur.fetchone()
        tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552

        # Actualizar en BD
        cur.execute('''
            UPDATE nominas SET 
                faltas_dias=%s, horas_extras_usd=%s, salario_base_usd=%s, 
                total_asignaciones_usd=%s, total_deducciones_usd=%s,
                neto_pagar_usd=%s, neto_pagar_bs=%s, sso_usd=%s, rpe_usd=%s, faov_usd=%s,
                sso_bs=%s, rpe_bs=%s, faov_bs=%s, fecha_calculo=%s
            WHERE id_nomina=%s
        ''', (faltas, total_horas_extras, salario_base, total_asignaciones, total_deducciones, neto_usd, neto_usd * tasa_bcv, ivss, rpe, faov, ivss * tasa_bcv, rpe * tasa_bcv, faov * tasa_bcv, datetime.now().date(), id))
        conn.commit()
        return jsonify({'mensaje': 'Nómina actualizada exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

@app.route('/api/nominas/<int:id>', methods=['DELETE'])
def eliminar_nomina(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM nominas WHERE id_nomina = %s", (id,))
        conn.commit()
        return jsonify({'mensaje': 'Nómina eliminada exitosamente'})
    except Exception as e: return jsonify({'error': str(e)}), 400
    finally: cur.close(); conn.close()

# ============================================
# INICIALIZACIÓN
# ============================================
with app.app_context(): init_db()
if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
