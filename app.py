import os
import psycopg2
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, timedelta

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
        CREATE TABLE IF NOT EXISTS lotes_nomina (
            id_lote SERIAL PRIMARY KEY, 
            descripcion TEXT, 
            fecha_calculo DATE NOT NULL, 
            total_usd REAL DEFAULT 0, 
            total_bs REAL DEFAULT 0,
            cantidad_empleados INTEGER DEFAULT 0
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS nominas (
            id_nomina SERIAL PRIMARY KEY, id_empleado INTEGER NOT NULL, fecha_inicio DATE NOT NULL, fecha_fin DATE NOT NULL,
            tipo TEXT CHECK(tipo IN ('Quincenal', 'Semanal')), faltas_dias INTEGER DEFAULT 0, salario_base_usd REAL,
            horas_extras_usd REAL DEFAULT 0, total_asignaciones_usd REAL, total_deducciones_usd REAL,
            neto_pagar_usd REAL, neto_pagar_bs REAL, tasa_bcv REAL, fecha_calculo DATE,
            sso_usd REAL DEFAULT 0, rpe_usd REAL DEFAULT 0, faov_usd REAL DEFAULT 0,
            sso_bs REAL DEFAULT 0, rpe_bs REAL DEFAULT 0, faov_bs REAL DEFAULT 0,
            descripcion TEXT,
            lote_id INTEGER
        )
    ''')
    cur.execute("ALTER TABLE nominas ADD COLUMN IF NOT EXISTS descripcion TEXT")
    cur.execute("ALTER TABLE nominas ADD COLUMN IF NOT EXISTS lote_id INTEGER")
    
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
# EMPLEADOS Y SUCURSALES
# ============================================
@app.route('/api/empleados', methods=['GET'])
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
def crear_empleado():
    data = request.json
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        cur.execute('''INSERT INTO empleados (cedula, nombres, apellidos, fecha_nacimiento, fecha_ingreso, cargo, departamento, sucursal_id, salario_mensual_usd, tipo_pago, email, telefono, direccion, cuenta_bancaria) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''', (data['cedula'], data['nombres'], data['apellidos'], data['fecha_nacimiento'], data['fecha_ingreso'], data['cargo'], data['departamento'], data['sucursal_id'], data['salario_mensual_usd'], data['tipo_pago'], data.get('email'), data.get('telefono'), data.get('direccion'), data.get('cuenta_bancaria')))
        conn.commit(); return jsonify({'mensaje': 'Empleado creado exitosamente'})
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
# CÁLCULO DE NÓMINA Y LOTES
# ============================================
@app.route('/api/calcular_nomina', methods=['POST'])
def calcular_nomina():
    data = request.json
    tipo, fecha_inicio, fecha_fin = data.get('tipo'), data.get('fecha_inicio'), data.get(' fecha_fin')
    descripcion = data.get('descripcion', '')
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
    total_usd_lote = 0.0
    total_bs_lote = 0.0
    
    start_date = datetime.strptime(fecha_inicio, "%Y-%m-%d").date()
    end_date = datetime.strptime(fecha_fin, "%Y-%m-%d").date()
    total_calendar_days = (end_date - start_date).days + 1
    rest_days = 0
    current_day = start_date
    while current_day <= end_date:
        if current_day.weekday() >= 5:
            rest_days += 1
        current_day += timedelta(days=1)
    working_days = total_calendar_days - rest_days
    
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
            dias_semana_trabajados = max(0, 5 - faltas)
            total_asignaciones = (salario_diario * dias_semana_trabajados) + total_horas_extras

        ivss, rpe, faov = total_asignaciones * 0.04, total_asignaciones * 0.005, total_asignaciones * 0.01
        total_deducciones = ivss + rpe + faov
        neto_usd = total_asignaciones - total_deducciones
        neto_bs = neto_usd * tasa_bcv
        
        total_usd_lote += neto_usd
        total_bs_lote += neto_bs
        
        calculo = {
            'salario_base_usd': salario_base if tipo == 'Quincenal' else salario_diario * dias_semana_trabajados,
            'horas_extras_usd': total_horas_extras, 'total_asignaciones_usd': total_asignaciones,
            'total_deducciones_usd': total_deducciones, 'sso_usd': ivss, 'rpe_usd': rpe, 'faov_usd': faov,
            'neto_pagar_usd': neto_usd, 'neto_pagar_bs': neto_bs, 'faltas_dias': faltas,
            'dias_totales_periodo': total_calendar_days, 'dias_descanso': rest_days, 'dias_reales_trabajados': working_days,
            'empleado': {'id': emp[0], 'cedula': cedula, 'nombre_completo': f"{emp[2]} {emp[3]}"}
        }
        resultados.append(calculo)

    cur.execute('''
        INSERT INTO lotes_nomina (descripcion, fecha_calculo, total_usd, total_bs, cantidad_empleados)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id_lote
    ''', (descripcion, datetime.now().date(), total_usd_lote, total_bs_lote, len(empleados)))
    lote_id = cur.fetchone()[0]

    for emp, calculo in zip(empleados, resultados):
        cur.execute('''
            INSERT INTO nominas (
                id_empleado, fecha_inicio, fecha_fin, tipo, faltas_dias, salario_base_usd, horas_extras_usd,
                total_asignaciones_usd, total_deducciones_usd, neto_pagar_usd, neto_pagar_bs, tasa_bcv, fecha_calculo,
                sso_usd, rpe_usd, faov_usd, sso_bs, rpe_bs, faov_bs, descripcion, lote_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (emp[0], fecha_inicio, fecha_fin, tipo, calculo['faltas_dias'], calculo['salario_base_usd'], calculo['horas_extras_usd'], calculo['total_asignaciones_usd'], calculo['total_deducciones_usd'], calculo['neto_pagar_usd'], calculo['neto_pagar_bs'], tasa_bcv, datetime.now().date(), calculo['sso_usd'], calculo['rpe_usd'], calculo['faov_usd'], calculo['sso_usd'] * tasa_bcv, calculo['rpe_usd'] * tasa_bcv, calculo['faov_usd'] * tasa_bcv, descripcion, lote_id))
        
    conn.commit(); cur.close(); conn.close()
    return jsonify({'tasa_bcv': tasa_bcv, 'resultados': resultados, 'lote_id': lote_id})

# ============================================
# CONSULTA Y BORRADO DE LOTES
# ============================================
@app.route('/api/lotes', methods=['GET'])
def get_lotes():
    search = request.args.get('search', '')
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    query = '''
        SELECT l.*, 
               COUNT(n.id_nomina) as total_empleados_detalle,
               STRING_AGG(DISTINCT s.nombre, ', ') as sucursales_involucradas
        FROM lotes_nomina l
        LEFT JOIN nominas n ON l.id_lote = n.lote_id
        LEFT JOIN empleados e ON n.id_empleado = e.id_empleado
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE 1=1
    '''
    params = []
    if search:
        query += " AND (l.descripcion ILIKE %s OR CAST(l.id_lote AS TEXT) ILIKE %s)"
        sp = f"%{search}%"
        params.extend([sp, sp])
    query += " GROUP BY l.id_lote ORDER BY l.fecha_calculo DESC, l.id_lote DESC"
    cur.execute(query, params)
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([{
        'id_lote': r[0], 'descripcion': r[1], 'fecha_calculo': r[2].isoformat(), 'total_usd': float(r[3]) if r[3] else 0,
        'total_bs': float(r[4]) if r[4] else 0, 'cantidad_empleados_lote': r[5],
        'total_empleados_detalle': r[6], 'sucursales_involucradas': r[7] or 'Mixto / Sin Sucursal'
    } for r in rows])

@app.route('/api/lotes/<int:id>', methods=['GET'])
def get_lote_detalle(id):
    conn = get_db_connection()
    if not conn: return jsonify([])
    cur = conn.cursor()
    cur.execute("SELECT * FROM lotes_nomina WHERE id_lote = %s", (id,))
    lote_row = cur.fetchone()
    if not lote_row: return jsonify({'error': 'Lote no encontrado'}), 404
    
    cur.execute('''
        SELECT n.*, e.nombres, e.apellidos, e.cedula, s.id_sucursal, s.nombre as sucursal_nombre
        FROM nominas n
        JOIN empleados e ON n.id_empleado = e.id_empleado
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE n.lote_id = %s
        ORDER BY e.nombres
    ''', (id,))
    nominas_rows = cur.fetchall(); cur.close(); conn.close()
    nominas = []
    for n in nominas_rows:
        nominas.append({
            'id_nomina': n[0], 'id_empleado': n[1], 'fecha_inicio': n[2].isoformat(), 'fecha_fin': n[3].isoformat(),
            'tipo': n[4], 'faltas_dias': n[5], 'salario_base_usd': float(n[6]) if n[6] else 0,
            'horas_extras_usd': float(n[7]) if n[7] else 0, 'total_asignaciones_usd': float(n[8]) if n[8] else 0,
            'total_deducciones_usd': float(n[9]) if n[9] else 0, 'neto_pagar_usd': float(n[10]) if n[10] else 0,
            'neto_pagar_bs': float(n[11]) if n[11] else 0, 'tasa_bcv': float(n[12]) if n[12] else 0,
            'fecha_calculo': n[13].isoformat(),
            'sso_usd': float(n[14]) if n[14] else 0, 'rpe_usd': float(n[15]) if n[15] else 0, 'faov_usd': float(n[16]) if n[16] else 0,
            'sso_bs': float(n[17]) if n[17] else 0, 'rpe_bs': float(n[18]) if n[18] else 0, 'faov_bs': float(n[19]) if n[19] else 0,
            'descripcion': n[20], 'lote_id': n[21],
            'nombres': n[22], 'apellidos': n[23], 'cedula': n[24], 'sucursal_id': n[25], 'sucursal_nombre': n[26] or 'Sin sucursal'
        })
    return jsonify({
        'id_lote': lote_row[0], 'descripcion': lote_row[1], 'fecha_calculo': lote_row[2].isoformat(),
        'total_usd': float(lote_row[3]) if lote_row[3] else 0, 'total_bs': float(lote_row[4]) if lote_row[4] else 0,
        'cantidad_empleados_lote': lote_row[5],
        'nominas': nominas
    })

# 🆕 NUEVO ENDPOINT: Borrar un Lote completo y sus nóminas asociadas
@app.route('/api/lotes/<int:id>', methods=['DELETE'])
def eliminar_lote(id):
    conn = get_db_connection()
    if not conn: return jsonify({'error': 'Error de conexión'}), 500
    cur = conn.cursor()
    try:
        # 1. Eliminar todas las nóminas vinculadas a este lote
        cur.execute("DELETE FROM nominas WHERE lote_id = %s", (id,))
        # 2. Eliminar el lote
        cur.execute("DELETE FROM lotes_nomina WHERE id_lote = %s", (id,))
        conn.commit()
        return jsonify({'mensaje': 'Lote de nómina y todos sus registros eliminados exitosamente'})
    except Exception as e:
        conn.rollback() # Si falla, deshacemos todo
        return jsonify({'error': str(e)}), 400
    finally:
        cur.close(); conn.close()

with app.app_context(): init_db()
if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
