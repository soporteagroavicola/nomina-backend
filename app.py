import os
import psycopg2
from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

# ============================================
# Crear la aplicación Flask
# ============================================
app = Flask(__name__)
CORS(app)  # Permitir peticiones desde cualquier origen (para desarrollo)

# ============================================
# Conexión a la base de datos PostgreSQL
# ============================================
def get_db_connection():
    # Aquí va la URL exacta que me diste. ¡Sin .env, directo al grano!
    database_url = 'postgresql://nomina_db_naiu_user:58sgnjVGnVRtLVbOVqYiA7d41VXwsHUH@dpg-d9prbrr9ik0c73ci4e0g-a.oregon-postgres.render.com/nomina_db_naiu'
    
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except Exception as e:
        print(f"❌ Error conectando a la base de datos: {e}")
        return None

# ============================================
# Crear las tablas si no existen
# ============================================
def init_db():
    conn = get_db_connection()
    
    # Seguridad extra: si no hay conexión, salir para evitar el error 'NoneType'
    if not conn:
        print("❌ No se pudo establecer conexión con la BD. Saliendo de init_db.")
        return

    cur = conn.cursor()
    
    # Creamos las tablas necesarias
    cur.execute('''
        CREATE TABLE IF NOT EXISTS empleados (
            id_empleado SERIAL PRIMARY KEY,
            cedula TEXT UNIQUE NOT NULL,
            nombres TEXT NOT NULL,
            apellidos TEXT NOT NULL,
            fecha_nacimiento DATE,
            fecha_ingreso DATE,
            cargo TEXT,
            departamento TEXT,
            sucursal_id INTEGER,
            salario_mensual_usd REAL DEFAULT 0,
            tipo_pago TEXT DEFAULT 'Quincenal',
            activo INTEGER DEFAULT 1,
            email TEXT,
            telefono TEXT,
            direccion TEXT,
            cuenta_bancaria TEXT
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS sucursales (
            id_sucursal SERIAL PRIMARY KEY,
            nombre TEXT UNIQUE NOT NULL,
            activo INTEGER DEFAULT 1
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS nominas (
            id_nomina SERIAL PRIMARY KEY,
            id_empleado INTEGER NOT NULL,
            fecha_inicio DATE NOT NULL,
            fecha_fin DATE NOT NULL,
            tipo TEXT CHECK(tipo IN ('Quincenal', 'Semanal')),
            faltas_dias INTEGER DEFAULT 0,
            salario_base_usd REAL,
            horas_extras_usd REAL DEFAULT 0,
            total_asignaciones_usd REAL,
            total_deducciones_usd REAL,
            neto_pagar_usd REAL,
            neto_pagar_bs REAL,
            tasa_bcv REAL,
            fecha_calculo DATE,
            sso_usd REAL DEFAULT 0,
            rpe_usd REAL DEFAULT 0,
            faov_usd REAL DEFAULT 0,
            sso_bs REAL DEFAULT 0,
            rpe_bs REAL DEFAULT 0,
            faov_bs REAL DEFAULT 0
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS cestaticket (
            id_cestaticket SERIAL PRIMARY KEY,
            id_empleado INTEGER NOT NULL,
            mes INTEGER NOT NULL,
            anio INTEGER NOT NULL,
            dias_mes INTEGER DEFAULT 30,
            faltas_injustificadas INTEGER DEFAULT 0,
            dias_a_pagar INTEGER,
            cestaticket_base_usd REAL,
            tasa_bcv REAL,
            cestaticket_mes_usd REAL,
            cestaticket_mes_bs REAL,
            total_a_pagar_usd REAL,
            total_a_pagar_bs REAL,
            fecha_calculo DATE
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS parametros (
            id SERIAL PRIMARY KEY,
            clave TEXT UNIQUE NOT NULL,
            valor REAL NOT NULL,
            fecha_actualizacion DATE
        )
    ''')
    
    # Insertar algunos parámetros por defecto si no existen
    cur.execute("SELECT * FROM parametros WHERE clave = 'tasa_bcv'")
    if not cur.fetchone():
        cur.execute("INSERT INTO parametros (clave, valor, fecha_actualizacion) VALUES ('tasa_bcv', 755.1552, CURRENT_DATE)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'cestaticket_usd'")
    if not cur.fetchone():
        cur.execute("INSERT INTO parametros (clave, valor, fecha_actualizacion) VALUES ('cestaticket_usd', 40.0, CURRENT_DATE)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'porcentaje_ivss'")
    if not cur.fetchone():
        cur.execute("INSERT INTO parametros (clave, valor, fecha_actualizacion) VALUES ('porcentaje_ivss', 0.04, CURRENT_DATE)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'porcentaje_rpe'")
    if not cur.fetchone():
        cur.execute("INSERT INTO parametros (clave, valor, fecha_actualizacion) VALUES ('porcentaje_rpe', 0.005, CURRENT_DATE)")
    cur.execute("SELECT * FROM parametros WHERE clave = 'porcentaje_faov'")
    if not cur.fetchone():
        cur.execute("INSERT INTO parametros (clave, valor, fecha_actualizacion) VALUES ('porcentaje_faov', 0.01, CURRENT_DATE)")
    
    conn.commit()
    cur.close()
    conn.close()
    print("✅ Base de datos inicializada correctamente")

# ============================================
# Funciones de cálculo
# ============================================
def calcular_nomina_empleado(empleado, tipo, faltas_dias, horas_extras_horas, valor_hora, tasa_bcv):
    salario_mensual = empleado['salario_mensual_usd'] or 0
    salario_diario = salario_mensual / 30
    
    total_horas_extras = horas_extras_horas * valor_hora if horas_extras_horas and valor_hora else 0
    
    if tipo == 'Quincenal':
        salario_base_periodo = salario_mensual / 2
        descuento_faltas = faltas_dias * salario_diario
        total_asignaciones = salario_base_periodo - descuento_faltas + total_horas_extras
    else:  # Semanal
        dias_trabajados = 5 - faltas_dias
        if dias_trabajados < 0: 
            dias_trabajados = 0
        total_asignaciones = (salario_diario * dias_trabajados) + total_horas_extras
    
    ivss = total_asignaciones * 0.04
    rpe = total_asignaciones * 0.005
    faov = total_asignaciones * 0.01
    total_deducciones = ivss + rpe + faov
    
    neto_usd = total_asignaciones - total_deducciones
    neto_bs = neto_usd * tasa_bcv if tasa_bcv else 0
    
    return {
        'salario_base_usd': salario_base_periodo if tipo == 'Quincenal' else salario_diario * dias_trabajados,
        'horas_extras_usd': total_horas_extras,
        'total_asignaciones_usd': total_asignaciones,
        'total_deducciones_usd': total_deducciones,
        'sso_usd': ivss,
        'rpe_usd': rpe,
        'faov_usd': faov,
        'neto_pagar_usd': neto_usd,
        'neto_pagar_bs': neto_bs,
        'faltas_dias': faltas_dias
    }

# ============================================
# Endpoints de la API
# ============================================
@app.route('/')
def home():
    return jsonify({'mensaje': 'API de Nómina funcionando 🚀'})

@app.route('/api/empleados', methods=['GET'])
def get_empleados():
    conn = get_db_connection()
    if not conn: 
        return jsonify({'error': 'Error de conexión a la BD'}), 500
    
    cur = conn.cursor()
    cur.execute('''
        SELECT e.*, s.nombre as sucursal_nombre
        FROM empleados e
        LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
        WHERE e.activo = 1
        ORDER BY e.nombres
    ''')
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    empleados = []
    for row in rows:
        empleados.append({
            'id_empleado': row[0],
            'cedula': row[1],
            'nombres': row[2],
            'apellidos': row[3],
            'fecha_nacimiento': row[4].isoformat() if row[4] else None,
            'fecha_ingreso': row[5].isoformat() if row[5] else None,
            'cargo': row[6],
            'departamento': row[7],
            'sucursal_id': row[8],
            'sucursal_nombre': row[16] if len(row) > 16 else None,
            'salario_mensual_usd': float(row[9]) if row[9] else 0,
            'tipo_pago': row[10],
            'activo': row[11],
            'email': row[12],
            'telefono': row[13],
            'direccion': row[14],
            'cuenta_bancaria': row[15]
        })
    return jsonify(empleados)

@app.route('/api/parametros', methods=['GET'])
def get_parametros():
    conn = get_db_connection()
    if not conn: 
        return jsonify({'error': 'Error de conexión a la BD'}), 500

    cur = conn.cursor()
    cur.execute("SELECT clave, valor FROM parametros")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    params = {row[0]: float(row[1]) for row in rows}
    return jsonify(params)

@app.route('/api/calcular_nomina', methods=['POST'])
def calcular_nomina():
    data = request.json
    tipo = data.get('tipo', 'Quincenal')
    fecha_inicio = data.get('fecha_inicio')
    fecha_fin = data.get('fecha_fin')
    empleados_ids = data.get('empleados_ids', [])
    faltas_dict = data.get('faltas', {})
    horas_extras_dict = data.get('horas_extras', {})
    
    if not fecha_inicio or not fecha_fin or not empleados_ids:
        return jsonify({'error': 'Faltan datos'}), 400
    
    conn = get_db_connection()
    if not conn: 
        return jsonify({'error': 'Error de conexión a la BD'}), 500

    cur = conn.cursor()
    cur.execute("SELECT valor FROM parametros WHERE clave = 'tasa_bcv'")
    tasa_row = cur.fetchone()
    tasa_bcv = float(tasa_row[0]) if tasa_row else 755.1552
    
    placeholders = ','.join(['%s'] * len(empleados_ids))
    cur.execute(f'''
        SELECT * FROM empleados WHERE id_empleado IN ({placeholders})
    ''', empleados_ids)
    empleados = cur.fetchall()
    
    resultados = []
    for emp in empleados:
        cedula = emp[1]
        faltas = faltas_dict.get(cedula, 0)
        horas_data = horas_extras_dict.get(cedula, {})
        horas = horas_data.get('horas', 0)
        valor_hora = horas_data.get('valor_hora', 0)
        
        emp_dict = {
            'id_empleado': emp[0],
            'cedula': emp[1],
            'nombres': emp[2],
            'apellidos': emp[3],
            'salario_mensual_usd': float(emp[9]) if emp[9] else 0,
            'tipo_pago': emp[10]
        }
        
        calculo = calcular_nomina_empleado(emp_dict, tipo, faltas, horas, valor_hora, tasa_bcv)
        calculo['empleado'] = {
            'id': emp[0],
            'cedula': cedula,
            'nombre_completo': f"{emp[2]} {emp[3]}"
        }
        resultados.append(calculo)
        
        cur.execute('''
            INSERT INTO nominas (
                id_empleado, fecha_inicio, fecha_fin, tipo,
                faltas_dias, salario_base_usd, horas_extras_usd,
                total_asignaciones_usd, total_deducciones_usd,
                neto_pagar_usd, neto_pagar_bs, tasa_bcv, fecha_calculo,
                sso_usd, rpe_usd, faov_usd, sso_bs, rpe_bs, faov_bs
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            emp[0], fecha_inicio, fecha_fin, tipo,
            calculo['faltas_dias'],
            calculo['salario_base_usd'],
            calculo['horas_extras_usd'],
            calculo['total_asignaciones_usd'],
            calculo['total_deducciones_usd'],
            calculo['neto_pagar_usd'],
            calculo['neto_pagar_bs'],
            tasa_bcv,
            datetime.now().date(),
            calculo['sso_usd'],
            calculo['rpe_usd'],
            calculo['faov_usd'],
            calculo['sso_usd'] * tasa_bcv,
            calculo['rpe_usd'] * tasa_bcv,
            calculo['faov_usd'] * tasa_bcv
        ))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({
        'tasa_bcv': tasa_bcv,
        'resultados': resultados
    })

# ============================================
# Inicializar la base de datos al arrancar
# ============================================
with app.app_context():
    init_db()

# ============================================
# Ejecutar la aplicación (Listo para la nube)
# ============================================
if __name__ == '__main__':
    # Render asigna un puerto mediante la variable de entorno PORT
    port = int(os.getenv("PORT", 5000))
    # Desactivamos el modo debug para producción
    app.run(host='0.0.0.0', port=port, debug=False)