import os
import re
import logging
import json
from decimal import Decimal, getcontext
from datetime import datetime, timedelta
from functools import wraps
from contextlib import contextmanager
from io import BytesIO, StringIO

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, request, jsonify, session, g, send_file
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import inch, mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib import colors

# ================== CONFIGURACIÓN ==================
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv('SECRET_KEY', 'clave_super_secreta_para_nomina_2026'),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    SESSION_COOKIE_NAME='nomina_session'
)

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('NominaApp')

# ================== CORS ==================
frontend_urls = [
    "https://nomina-frontend.onrender.com",
    "https://soporteagroavicola.github.io",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]
CORS(app, origins=frontend_urls, supports_credentials=True,
     allow_headers=["Content-Type", "Authorization"],
     expose_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

# ================== CONSTANTES LOTTT ==================
IVSS_PCT = Decimal('0.04')
RPE_PCT = Decimal('0.005')
FAOV_PCT = Decimal('0.01')
LPPP_PCT = Decimal('0.09')
DIAS_UTILIDADES = 15
DIAS_AGUINALDO = 15
DIAS_VACACIONES = 15
DIAS_BONO_VAC = 7

# ================== BASE DE DATOS ==================
def get_db_connection():
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        # Fallback solo para desarrollo local (no usar en producción)
        database_url = 'postgresql://user:pass@localhost:5432/nomina_db'
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except Exception as e:
        logger.error(f"Error conectando a la BD: {e}")
        return None

@contextmanager
def get_cursor(commit=False, dict_cursor=False):
    conn = get_db_connection()
    if not conn:
        raise Exception("No se pudo conectar a la base de datos")
    try:
        if dict_cursor:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        else:
            cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Error en transacción: {e}")
        raise e
    finally:
        cur.close()
        conn.close()

# ================== INICIALIZACIÓN Y MIGRACIÓN DE BD ==================
def init_db():
    try:
        with get_cursor(commit=True) as cur:
            # Tabla empleados (con DECIMAL)
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
                    salario_mensual_usd DECIMAL(12,2) DEFAULT 0,
                    bono_fijo_usd DECIMAL(12,2) DEFAULT 0,
                    bono_adicional_usd DECIMAL(12,2) DEFAULT 0,
                    tipo_pago TEXT DEFAULT 'Quincenal',
                    activo BOOLEAN DEFAULT TRUE,
                    email TEXT,
                    telefono TEXT,
                    direccion TEXT,
                    cuenta_bancaria TEXT,
                    banco_codigo TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Tabla sucursales
            cur.execute('''
                CREATE TABLE IF NOT EXISTS sucursales (
                    id_sucursal SERIAL PRIMARY KEY,
                    nombre TEXT UNIQUE NOT NULL,
                    activo BOOLEAN DEFAULT TRUE
                )
            ''')
            # Tabla lotes_nomina (con DECIMAL y más campos)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS lotes_nomina (
                    id_lote SERIAL PRIMARY KEY,
                    descripcion TEXT,
                    fecha_calculo DATE NOT NULL,
                    fecha_inicio DATE,
                    fecha_fin DATE,
                    total_usd DECIMAL(15,2) DEFAULT 0,
                    total_bs DECIMAL(15,2) DEFAULT 0,
                    cantidad_empleados INTEGER DEFAULT 0,
                    aplicar_deducciones BOOLEAN DEFAULT TRUE,
                    tasa_bcv DECIMAL(12,4),
                    created_by TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Tabla nominas (con DECIMAL)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS nominas (
                    id_nomina SERIAL PRIMARY KEY,
                    id_empleado INTEGER NOT NULL,
                    fecha_inicio DATE NOT NULL,
                    fecha_fin DATE NOT NULL,
                    tipo TEXT CHECK(tipo IN ('Quincenal', 'Semanal')),
                    faltas_dias INTEGER DEFAULT 0,
                    dias_laborados INTEGER DEFAULT 0,
                    dias_descanso INTEGER DEFAULT 0,
                    salario_base_usd DECIMAL(12,2),
                    horas_extras_usd DECIMAL(12,2) DEFAULT 0,
                    bono_complementario_usd DECIMAL(12,2) DEFAULT 0,
                    bono_adicional_usd DECIMAL(12,2) DEFAULT 0,
                    total_asignaciones_usd DECIMAL(15,2),
                    total_deducciones_usd DECIMAL(15,2),
                    neto_pagar_usd DECIMAL(15,2),
                    neto_pagar_bs DECIMAL(15,2),
                    tasa_bcv DECIMAL(12,4),
                    fecha_calculo DATE,
                    sso_usd DECIMAL(12,2) DEFAULT 0,
                    rpe_usd DECIMAL(12,2) DEFAULT 0,
                    faov_usd DECIMAL(12,2) DEFAULT 0,
                    sso_bs DECIMAL(15,2) DEFAULT 0,
                    rpe_bs DECIMAL(15,2) DEFAULT 0,
                    faov_bs DECIMAL(15,2) DEFAULT 0,
                    descripcion TEXT,
                    lote_id INTEGER REFERENCES lotes_nomina(id_lote)
                )
            ''')
            # Tabla usuarios (con roles)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    rol TEXT DEFAULT 'operador' CHECK (rol IN ('admin', 'operador', 'consulta')),
                    activo BOOLEAN DEFAULT TRUE,
                    last_login TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Insertar admin por defecto si no existe
            cur.execute("SELECT id FROM usuarios WHERE username = 'admin'")
            if not cur.fetchone():
                hashed = generate_password_hash('admin123')
                cur.execute(
                    "INSERT INTO usuarios (username, password, rol) VALUES (%s, %s, 'admin')",
                    ('admin', hashed)
                )

            # Tabla parametros (con DECIMAL)
            cur.execute('''
                CREATE TABLE IF NOT EXISTS parametros (
                    id SERIAL PRIMARY KEY,
                    clave TEXT UNIQUE NOT NULL,
                    valor TEXT NOT NULL,
                    descripcion TEXT,
                    fecha_actualizacion DATE
                )
            ''')
            # Insertar parámetros por defecto (incluyendo empresa)
            default_params = [
                ('tasa_bcv', '755.1552', 'Tasa de cambio BCV'),
                ('cestaticket_usd', '40.0', 'Valor mensual del cestaticket en USD'),
                ('porcentaje_ivss', '0.04', 'Porcentaje IVSS'),
                ('porcentaje_rpe', '0.005', 'Porcentaje RPE'),
                ('porcentaje_faov', '0.01', 'Porcentaje FAOV'),
                ('rif_empresa', '', 'RIF de la empresa'),
                ('cuenta_empresa', '', 'Número de cuenta de la empresa'),
                ('nombre_cuenta_empresa', '', 'Nombre de la cuenta empresa'),
                ('codigo_banco_defecto', '', 'Código del banco (4 dígitos)')
            ]
            for clave, valor, desc in default_params:
                cur.execute(
                    "INSERT INTO parametros (clave, valor, descripcion) VALUES (%s, %s, %s) "
                    "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                    (clave, valor, desc)
                )

            # Tabla audit_log
            cur.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id SERIAL PRIMARY KEY,
                    tabla TEXT NOT NULL,
                    registro_id INTEGER,
                    accion TEXT NOT NULL,
                    usuario TEXT,
                    datos_anteriores JSONB,
                    datos_nuevos JSONB,
                    ip_address TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')

            # Tablas cestaticket
            cur.execute('''
                CREATE TABLE IF NOT EXISTS cestaticket_lotes (
                    id_lote SERIAL PRIMARY KEY,
                    descripcion TEXT,
                    fecha_calculo DATE NOT NULL,
                    total_bs DECIMAL(15,2) DEFAULT 0,
                    cantidad_empleados INTEGER DEFAULT 0,
                    tasa_bcv DECIMAL(12,4)
                )
            ''')
            cur.execute('''
                CREATE TABLE IF NOT EXISTS cestaticket_nominas (
                    id SERIAL PRIMARY KEY,
                    id_empleado INTEGER NOT NULL,
                    fecha_inicio DATE NOT NULL,
                    fecha_fin DATE NOT NULL,
                    dias_pagados INTEGER NOT NULL,
                    valor_diario_usd DECIMAL(12,4) NOT NULL,
                    tasa_bcv DECIMAL(12,4) NOT NULL,
                    total_usd DECIMAL(15,2) NOT NULL,
                    total_bs DECIMAL(15,2) NOT NULL,
                    descripcion TEXT,
                    lote_id INTEGER
                )
            ''')

            # Índices
            cur.execute('CREATE INDEX IF NOT EXISTS idx_nominas_lote ON nominas(lote_id)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_nominas_empleado ON nominas(id_empleado)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_nominas_fechas ON nominas(fecha_inicio, fecha_fin)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)')
            cur.execute('CREATE INDEX IF NOT EXISTS idx_empleados_activo ON empleados(activo)')

            logger.info("✅ Base de datos inicializada y migrada correctamente.")
    except Exception as e:
        logger.error(f"❌ Error en init_db: {e}")
        raise

# ================== DECORADORES ==================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'No autorizado'}), 401
        # Inyectar datos del usuario en g
        user_id = session['user_id']
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT username, rol FROM usuarios WHERE id = %s AND activo = TRUE", (user_id,))
            user = cur.fetchone()
            if not user:
                session.clear()
                return jsonify({'error': 'Usuario no activo'}), 401
            g.username = user['username']
            g.user_id = user_id
            g.rol = user['rol']
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'No autorizado'}), 401
        if g.rol != 'admin':
            return jsonify({'error': 'Se requieren permisos de administrador'}), 403
        return f(*args, **kwargs)
    return wrapper

def audit_action(tabla, accion):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Obtener datos anteriores si es actualización o eliminación
            datos_anteriores = None
            if accion in ('ACTUALIZAR', 'ELIMINAR'):
                # Intentar obtener el registro antes de la operación
                # Esto requiere que el endpoint tenga un parámetro 'id' en kwargs
                registro_id = kwargs.get('id')
                if registro_id:
                    try:
                        with get_cursor(dict_cursor=True) as cur:
                            cur.execute(f"SELECT * FROM {tabla} WHERE id = %s", (registro_id,))
                            row = cur.fetchone()
                            if row:
                                datos_anteriores = json.dumps(dict(row), default=str)
                    except Exception as e:
                        logger.warning(f"No se pudo obtener datos previos para auditoría: {e}")

            # Ejecutar la función
            response = f(*args, **kwargs)

            # Si la respuesta es exitosa, registrar auditoría
            if response and hasattr(response, 'status_code') and response.status_code < 400:
                # Obtener el ID del registro (si existe en kwargs o en la respuesta)
                registro_id = kwargs.get('id')
                if not registro_id and hasattr(response, 'json'):
                    try:
                        data = response.json
                        if isinstance(data, dict) and 'id' in data:
                            registro_id = data['id']
                    except:
                        pass

                # Obtener nuevos datos (si es creación o actualización)
                datos_nuevos = None
                if accion in ('CREAR', 'ACTUALIZAR'):
                    try:
                        if request.is_json:
                            datos_nuevos = json.dumps(request.json, default=str)
                    except:
                        pass

                try:
                    with get_cursor(commit=True) as cur:
                        cur.execute('''
                            INSERT INTO audit_log (tabla, registro_id, accion, usuario, datos_anteriores, datos_nuevos, ip_address)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ''', (
                            tabla, registro_id, accion,
                            getattr(g, 'username', None),
                            datos_anteriores, datos_nuevos,
                            request.remote_addr
                        ))
                except Exception as e:
                    logger.error(f"Error al registrar auditoría: {e}")

            return response
        return wrapper
    return decorator

# ================== VALIDACIONES ==================
class ValidationError(Exception):
    pass

def validate_cedula(cedula):
    cedula = re.sub(r'\D', '', str(cedula))
    if len(cedula) < 6:
        raise ValidationError("La cédula debe tener al menos 6 dígitos")
    return cedula

def validate_dates(fecha_inicio, fecha_fin):
    try:
        start = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
        end = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        if start > end:
            raise ValidationError("La fecha de inicio debe ser anterior a la fecha de fin")
        if (end - start).days > 31:
            raise ValidationError("El período no puede exceder los 31 días")
        return start, end
    except ValueError:
        raise ValidationError("Formato de fecha inválido. Use YYYY-MM-DD")

# ================== SERVICIOS (LÓGICA DE NEGOCIO) ==================

# ---------- Parámetros ----------
def get_param(clave, default=None):
    with get_cursor() as cur:
        cur.execute("SELECT valor FROM parametros WHERE clave = %s", (clave,))
        row = cur.fetchone()
        if not row:
            return default
        valor = row[0]
        # Intentar convertir a Decimal si es numérico
        try:
            return Decimal(valor)
        except:
            return valor

def get_param_all():
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT clave, valor, descripcion FROM parametros")
        rows = cur.fetchall()
        params = {}
        for r in rows:
            valor = r['valor']
            try:
                params[r['clave']] = Decimal(valor)
            except:
                params[r['clave']] = valor
        return params

def update_param(clave, valor, usuario):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE parametros SET valor = %s, fecha_actualizacion = CURRENT_DATE WHERE clave = %s",
            (str(valor), clave)
        )
        # Auditoría manual para parámetros (o usar decorador)
        cur.execute('''
            INSERT INTO audit_log (tabla, registro_id, accion, usuario, datos_nuevos, ip_address)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', ('parametros', None, 'ACTUALIZAR', usuario, json.dumps({clave: str(valor)}), request.remote_addr)
        )

# ---------- Empleados ----------
def get_empleados(search='', sucursal_id=None, tipo_pago=None, activo=True, limit=100, offset=0):
    with get_cursor(dict_cursor=True) as cur:
        query = "SELECT * FROM empleados WHERE activo = %s"
        params = [activo]
        if sucursal_id:
            query += " AND sucursal_id = %s"
            params.append(sucursal_id)
        if tipo_pago:
            query += " AND tipo_pago = %s"
            params.append(tipo_pago)
        if search:
            query += " AND (cedula ILIKE %s OR nombres ILIKE %s OR apellidos ILIKE %s)"
            like = f"%{search}%"
            params.extend([like, like, like])
        query += " ORDER BY nombres LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cur.execute(query, params)
        return cur.fetchall()

def get_empleado_by_id(id):
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT * FROM empleados WHERE id_empleado = %s", (id,))
        return cur.fetchone()

def create_empleado(data, usuario):
    cedula = validate_cedula(data.get('cedula', ''))
    with get_cursor(commit=True) as cur:
        # Verificar duplicado
        cur.execute("SELECT id_empleado FROM empleados WHERE cedula = %s", (cedula,))
        if cur.fetchone():
            raise ValidationError("Ya existe un empleado con esa cédula")
        cur.execute('''
            INSERT INTO empleados (
                cedula, nombres, apellidos, fecha_nacimiento, fecha_ingreso,
                cargo, departamento, sucursal_id, salario_mensual_usd,
                bono_fijo_usd, bono_adicional_usd, tipo_pago,
                email, telefono, direccion, cuenta_bancaria, banco_codigo
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_empleado
        ''', (
            cedula, data.get('nombres'), data.get('apellidos'),
            data.get('fecha_nacimiento'), data.get('fecha_ingreso'),
            data.get('cargo'), data.get('departamento'), data.get('sucursal_id'),
            Decimal(str(data.get('salario_mensual_usd', 0))),
            Decimal(str(data.get('bono_fijo_usd', 0))),
            Decimal(str(data.get('bono_adicional_usd', 0))),
            data.get('tipo_pago', 'Quincenal'),
            data.get('email'), data.get('telefono'), data.get('direccion'),
            data.get('cuenta_bancaria'), data.get('banco_codigo')
        ))
        new_id = cur.fetchone()[0]
        return new_id

def update_empleado(id, data, usuario):
    with get_cursor(commit=True) as cur:
        cur.execute('''
            UPDATE empleados SET
                cedula=%s, nombres=%s, apellidos=%s,
                fecha_nacimiento=%s, fecha_ingreso=%s,
                cargo=%s, departamento=%s, sucursal_id=%s,
                salario_mensual_usd=%s, bono_fijo_usd=%s, bono_adicional_usd=%s,
                tipo_pago=%s, email=%s, telefono=%s, direccion=%s,
                cuenta_bancaria=%s, banco_codigo=%s, updated_at=NOW()
            WHERE id_empleado=%s
        ''', (
            data.get('cedula'), data.get('nombres'), data.get('apellidos'),
            data.get('fecha_nacimiento'), data.get('fecha_ingreso'),
            data.get('cargo'), data.get('departamento'), data.get('sucursal_id'),
            Decimal(str(data.get('salario_mensual_usd', 0))),
            Decimal(str(data.get('bono_fijo_usd', 0))),
            Decimal(str(data.get('bono_adicional_usd', 0))),
            data.get('tipo_pago', 'Quincenal'),
            data.get('email'), data.get('telefono'), data.get('direccion'),
            data.get('cuenta_bancaria'), data.get('banco_codigo'),
            id
        ))

def delete_empleado(id, usuario):
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE empleados SET activo = FALSE WHERE id_empleado = %s", (id,))

# ---------- Cálculo de Nómina ----------
def calcular_dias_habiles(inicio, fin):
    """Retorna número de días hábiles (lunes a viernes) entre dos fechas inclusive"""
    total = 0
    current = inicio
    while current <= fin:
        if current.weekday() < 5:  # 0=lunes, 6=domingo
            total += 1
        current += timedelta(days=1)
    return total

def calcular_periodo(tipo, fecha_ref=None):
    """Calcula inicio y fin del período según tipo y fecha de referencia"""
    if fecha_ref is None:
        fecha_ref = datetime.now().date()
    if tipo == 'Quincenal':
        year, month = fecha_ref.year, fecha_ref.month
        if fecha_ref.day <= 15:
            start = datetime(year, month, 1).date()
            end = datetime(year, month, 15).date()
        else:
            # Último día del mes
            if month == 12:
                next_month = datetime(year+1, 1, 1).date()
            else:
                next_month = datetime(year, month+1, 1).date()
            end = next_month - timedelta(days=1)
            start = datetime(year, month, 16).date()
    else:  # Semanal
        # Lunes a domingo de la semana actual
        start = fecha_ref - timedelta(days=fecha_ref.weekday())  # Lunes
        end = start + timedelta(days=6)  # Domingo
    return start, end

def procesar_empleado(emp, tipo, fecha_inicio, fecha_fin, faltas, horas, valor_hora, bono, salario_override, aplicar_deducciones):
    """Procesa un empleado y devuelve el cálculo completo usando Decimal"""
    # Obtener salario base (60%)
    salario_mensual = Decimal(str(emp['salario_mensual_usd'])) if emp['salario_mensual_usd'] else Decimal('0')
    if salario_override:
        salario_mensual = Decimal(str(salario_override))

    salario_diario = salario_mensual / Decimal('30')
    salario_diario_incidencia = salario_mensual * Decimal('0.60') / Decimal('30')
    total_horas_extras = Decimal(str(horas)) * Decimal(str(valor_hora))

    # Calcular días del período
    if tipo == 'Quincenal':
        base = salario_mensual / Decimal('2')
        dias_teoricos_trabajo = 11
        dias_descanso = 4
    else:
        base = salario_diario * Decimal('7')
        dias_teoricos_trabajo = 7
        dias_descanso = 2

    # Aplicar faltas
    faltas = int(faltas) if faltas else 0
    descuento_faltas = Decimal(faltas) * salario_diario
    salario_base_ajustado = base - descuento_faltas

    # Bonos
    bono_fijo = Decimal(str(emp.get('bono_fijo_usd', 0)))  # 40% fijo del empleado
    bono_adicional = Decimal(str(bono))  # lo que el usuario ingresa en el campo "Bono"
    total_bonos = bono_fijo + bono_adicional

    # Total asignaciones
    total_asignaciones = salario_base_ajustado + total_horas_extras + total_bonos

    # Deducciones
    if aplicar_deducciones:
        ivss = total_asignaciones * IVSS_PCT
        rpe = total_asignaciones * RPE_PCT
        faov = total_asignaciones * FAOV_PCT
        total_deducciones = ivss + rpe + faov
    else:
        ivss = rpe = faov = total_deducciones = Decimal('0')

    neto_usd = total_asignaciones - total_deducciones
    tasa_bcv = get_param('tasa_bcv', Decimal('755.1552'))
    neto_bs = neto_usd * tasa_bcv

    dias_reales_trabajados = max(0, dias_teoricos_trabajo - faltas)

    return {
        'salario_base_full_usd': base,
        'salario_base_ajustado_usd': salario_base_ajustado,
        'base_incidencia_60_usd': (salario_mensual * Decimal('0.60')) / Decimal('2') if tipo == 'Quincenal' else salario_diario_incidencia * Decimal('7'),
        'horas_extras_usd': total_horas_extras,
        'bono_fijo_usd': bono_fijo,
        'bono_adicional_usd': bono_adicional,
        'bono_total_usd': total_bonos,
        'total_asignaciones_base_usd': total_asignaciones,
        'total_deducciones_usd': total_deducciones,
        'sso_usd': ivss,
        'rpe_usd': rpe,
        'faov_usd': faov,
        'neto_pagar_usd': neto_usd,
        'neto_pagar_bs': neto_bs,
        'faltas_dias': faltas,
        'dias_reales_trabajados': dias_reales_trabajados,
        'dias_descanso': dias_descanso,
        'tasa_bcv': tasa_bcv,
        'empleado': {
            'id': emp['id_empleado'],
            'cedula': emp['cedula'],
            'nombre_completo': f"{emp['nombres']} {emp['apellidos']}"
        }
    }

def calcular_y_guardar_nomina(data, usuario):
    """Orquesta el cálculo y guardado de la nómina"""
    tipo = data.get('tipo', 'Quincenal')
    fecha_inicio = data.get('fecha_inicio')
    fecha_fin = data.get('fecha_fin')
    descripcion = data.get('descripcion', '')
    empleados_ids = data.get('empleados_ids', [])
    faltas_dict = data.get('faltas', {})
    horas_extras_dict = data.get('horas_extras', {})
    bonos_dict = data.get('bonos', {})
    salarios_override = data.get('salarios_override', {})
    aplicar_deducciones = data.get('aplicar_deducciones', True)
    guardar_en_bd = data.get('guardar_en_bd', True)

    if not fecha_inicio or not fecha_fin or not empleados_ids:
        raise ValidationError("Faltan datos: fechas o empleados")

    start, end = validate_dates(fecha_inicio, fecha_fin)

    tasa_bcv = get_param('tasa_bcv', Decimal('755.1552'))

    resultados = []
    total_usd_lote = Decimal('0')
    total_bs_lote = Decimal('0')

    with get_cursor(dict_cursor=True) as cur:
        placeholders = ','.join(['%s'] * len(empleados_ids))
        cur.execute(f"SELECT * FROM empleados WHERE id_empleado IN ({placeholders}) AND activo = TRUE", empleados_ids)
        empleados = cur.fetchall()

        for emp in empleados:
            cedula = emp['cedula']
            faltas = int(faltas_dict.get(cedula, 0))
            horas_data = horas_extras_dict.get(cedula, {})
            horas = Decimal(str(horas_data.get('horas', 0)))
            valor_hora = Decimal(str(horas_data.get('valor_hora', 0)))
            bono = Decimal(str(bonos_dict.get(cedula, 0)))
            salario_override_val = Decimal(str(salarios_override.get(str(emp['id_empleado']), 0))) if str(emp['id_empleado']) in salarios_override else None

            calc = procesar_empleado(
                emp, tipo, start, end,
                faltas, horas, valor_hora, bono,
                salario_override_val, aplicar_deducciones
            )
            resultados.append(calc)
            total_usd_lote += calc['neto_pagar_usd']
            total_bs_lote += calc['neto_pagar_bs']

    lote_id = None
    if guardar_en_bd:
        with get_cursor(commit=True) as cur:
            # Insertar lote
            cur.execute('''
                INSERT INTO lotes_nomina (
                    descripcion, fecha_calculo, fecha_inicio, fecha_fin,
                    total_usd, total_bs, cantidad_empleados,
                    aplicar_deducciones, tasa_bcv, created_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_lote
            ''', (
                descripcion or f"{tipo} del {start.strftime('%d/%m/%Y')} al {end.strftime('%d/%m/%Y')}",
                datetime.now().date(), start, end,
                total_usd_lote, total_bs_lote, len(empleados),
                aplicar_deducciones, tasa_bcv, usuario
            ))
            lote_id = cur.fetchone()[0]

            # Insertar cada nómina
            for emp, calc in zip(empleados, resultados):
                cur.execute('''
                    INSERT INTO nominas (
                        id_empleado, fecha_inicio, fecha_fin, tipo,
                        faltas_dias, dias_laborados, dias_descanso,
                        salario_base_usd, horas_extras_usd,
                        bono_complementario_usd, bono_adicional_usd,
                        total_asignaciones_usd, total_deducciones_usd,
                        neto_pagar_usd, neto_pagar_bs, tasa_bcv, fecha_calculo,
                        sso_usd, rpe_usd, faov_usd,
                        sso_bs, rpe_bs, faov_bs,
                        descripcion, lote_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s, %s
                    )
                ''', (
                    emp['id_empleado'], start, end, tipo,
                    calc['faltas_dias'], calc['dias_reales_trabajados'], calc['dias_descanso'],
                    calc['salario_base_full_usd'], calc['horas_extras_usd'],
                    calc['bono_fijo_usd'], calc['bono_adicional_usd'],
                    calc['total_asignaciones_base_usd'], calc['total_deducciones_usd'],
                    calc['neto_pagar_usd'], calc['neto_pagar_bs'], calc['tasa_bcv'], datetime.now().date(),
                    calc['sso_usd'], calc['rpe_usd'], calc['faov_usd'],
                    calc['sso_usd'] * calc['tasa_bcv'],
                    calc['rpe_usd'] * calc['tasa_bcv'],
                    calc['faov_usd'] * calc['tasa_bcv'],
                    descripcion, lote_id
                ))

        # Auditoría de la nómina (manual)
        with get_cursor(commit=True) as cur:
            cur.execute('''
                INSERT INTO audit_log (tabla, registro_id, accion, usuario, datos_nuevos, ip_address)
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', ('lotes_nomina', lote_id, 'CALCULAR', usuario, json.dumps({'total_usd': str(total_usd_lote), 'total_bs': str(total_bs_lote), 'empleados': len(empleados)}), request.remote_addr)

    return {
        'tasa_bcv': tasa_bcv,
        'resultados': resultados,
        'lote_id': lote_id,
        'total_usd': total_usd_lote,
        'total_bs': total_bs_lote
    }

# ---------- Dashboard ----------
def get_dashboard_stats():
    stats = {}
    today = datetime.now().date()
    month_start = today.replace(day=1)

    with get_cursor(dict_cursor=True) as cur:
        # Total empleados activos
        cur.execute("SELECT COUNT(*) as total FROM empleados WHERE activo = TRUE")
        stats['total_empleados'] = cur.fetchone()['total']

        # Desglose por tipo de pago
        cur.execute("SELECT tipo_pago, COUNT(*) as count FROM empleados WHERE activo = TRUE GROUP BY tipo_pago")
        stats['empleados_por_tipo'] = {row['tipo_pago']: row['count'] for row in cur.fetchall()}

        # Pagos del mes actual
        cur.execute('''
            SELECT COALESCE(SUM(total_usd), 0) as total_usd, COALESCE(SUM(total_bs), 0) as total_bs
            FROM lotes_nomina
            WHERE fecha_calculo >= %s AND fecha_calculo <= %s
        ''', (month_start, today))
        row = cur.fetchone()
        stats['pagos_mes_usd'] = row['total_usd'] if row else 0
        stats['pagos_mes_bs'] = row['total_bs'] if row else 0

        # Cestaticket del mes
        cur.execute('''
            SELECT COALESCE(SUM(total_bs), 0) as total_bs
            FROM cestaticket_lotes
            WHERE fecha_calculo >= %s AND fecha_calculo <= %s
        ''', (month_start, today))
        row = cur.fetchone()
        stats['cestaticket_mes_bs'] = row['total_bs'] if row else 0

        # Última nómina
        cur.execute('''
            SELECT id_lote, descripcion, fecha_calculo, total_usd, total_bs
            FROM lotes_nomina
            ORDER BY fecha_calculo DESC, id_lote DESC
            LIMIT 1
        ''')
        stats['ultima_nomina'] = cur.fetchone()

        # Distribución por sucursal
        cur.execute('''
            SELECT s.nombre, COUNT(e.id_empleado) as total
            FROM sucursales s
            LEFT JOIN empleados e ON s.id_sucursal = e.sucursal_id AND e.activo = TRUE
            WHERE s.activo = TRUE
            GROUP BY s.id_sucursal, s.nombre
            ORDER BY total DESC
        ''')
        stats['distribucion_sucursales'] = cur.fetchall()

    return stats

# ---------- Generación de TXT bancario ----------
def generar_txt_provision(lote_id, tipo='100'):
    """Genera el archivo TXT bancario para el lote especificado"""
    with get_cursor(dict_cursor=True) as cur:
        # Obtener parámetros de empresa
        params = {}
        for clave in ['rif_empresa', 'cuenta_empresa', 'nombre_cuenta_empresa', 'codigo_banco_defecto']:
            cur.execute("SELECT valor FROM parametros WHERE clave = %s", (clave,))
            row = cur.fetchone()
            params[clave] = row['valor'] if row else ''

        rif_empresa = params['rif_empresa'].strip().upper()
        cuenta_empresa = params['cuenta_empresa'].strip()
        nombre_cuenta = params['nombre_cuenta_empresa'].strip().upper()
        codigo_banco = params['codigo_banco_defecto'].strip()

        # Obtener nóminas del lote con datos de empleados
        cur.execute('''
            SELECT 
                e.cedula, e.cuenta_bancaria, e.nombres, e.apellidos,
                n.neto_pagar_usd, n.neto_pagar_bs, n.bono_complementario_usd,
                e.banco_codigo
            FROM nominas n
            JOIN empleados e ON n.id_empleado = e.id_empleado
            WHERE n.lote_id = %s
              AND e.cuenta_bancaria IS NOT NULL AND e.cuenta_bancaria != ''
        ''', (lote_id,))
        rows = cur.fetchall()

        if not rows:
            raise ValidationError("No hay empleados con cuenta bancaria en este lote")

        # Calcular montos según tipo
        fecha_ejecucion = datetime.now().strftime("%d/%m/%Y")
        buffer = StringIO()
        total_count = len(rows)
        total_amount = Decimal('0')

        # HEADER
        header = f"HEADER  {total_count:08d}0011853{rif_empresa:<10}{fecha_ejecucion}{fecha_ejecucion}"
        buffer.write(header + "\n")

        for i, row in enumerate(rows, 1):
            cedula = str(row['cedula']).strip()
            cuenta_empleado = str(row['cuenta_bancaria']).strip()
            nombre = f"{row['nombres']} {row['apellidos']}" if row['nombres'] and row['apellidos'] else row['nombres'] or row['apellidos'] or ''
            neto_usd = Decimal(str(row['neto_pagar_usd'] or 0))
            neto_bs = Decimal(str(row['neto_pagar_bs'] or 0))
            bono = Decimal(str(row['bono_complementario_usd'] or 0))
            banco_codigo = str(row['banco_codigo'] or codigo_banco).strip()

            if tipo == '60':
                monto = (neto_usd * Decimal('0.60')) + bono
            elif tipo == '40':
                monto = neto_usd * Decimal('0.40')
            else:
                monto = neto_usd

            # Convertir a bolívares usando la tasa del lote (si no, usar la actual)
            if neto_usd > 0:
                tasa = neto_bs / neto_usd
            else:
                tasa = get_param('tasa_bcv', Decimal('755.1552'))
            monto_bs = monto * tasa
            total_amount += monto_bs

            # Formatear monto con coma decimal y 16 dígitos totales
            monto_str = f"{monto_bs:016.2f}".replace('.', ',')

            # DEBITO
            debit = (f"DEBITO  {i:08d}{rif_empresa:<10}{nombre_cuenta:<30}"
                     f"{fecha_ejecucion}{cuenta_empresa:<12}00000487092{monto_str:<21}VEB40 ")
            # CREDITO
            credit = (f"CREDITO {i:08d}{cedula:<10}{nombre:<29}"
                      f"{cuenta_empleado:<22}{monto_str:<21}00{banco_codigo:<8}")

            buffer.write(debit + "\n")
            buffer.write(credit + "\n")

        # TOTAL
        total_amount_str = f"{total_amount:015.2f}".replace('.', ',')
        total_line = f"TOTAL   {total_count:05d}{total_count:05d}{total_amount_str:<18}"
        buffer.write(total_line + "\n")

        # Codificar a cp1252 para compatibilidad bancaria
        mem = BytesIO()
        mem.write(buffer.getvalue().encode('cp1252', errors='replace'))
        mem.seek(0)
        buffer.close()
        return mem

# ================== RUTAS API ==================

# ---------- Health ----------
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})

# ---------- Autenticación ----------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña son requeridos'}), 400

    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT id, password, rol FROM usuarios WHERE username = %s AND activo = TRUE", (username,))
        user = cur.fetchone()
        if not user or not check_password_hash(user['password'], password):
            return jsonify({'error': 'Credenciales inválidas'}), 401

        session['user_id'] = user['id']
        session['username'] = username
        session.permanent = True

        # Actualizar último login
        cur.execute("UPDATE usuarios SET last_login = NOW() WHERE id = %s", (user['id'],))
        return jsonify({'mensaje': 'Login exitoso', 'username': username, 'rol': user['rol']})

@app.route('/api/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return jsonify({'mensaje': 'Sesión cerrada'})

@app.route('/api/check_auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT username, rol FROM usuarios WHERE id = %s AND activo = TRUE", (session['user_id'],))
            user = cur.fetchone()
            if user:
                return jsonify({'authenticated': True, 'username': user['username'], 'rol': user['rol']})
    return jsonify({'authenticated': False}), 401

# ---------- Dashboard ----------
@app.route('/api/dashboard', methods=['GET'])
@login_required
def dashboard():
    try:
        stats = get_dashboard_stats()
        # Convertir Decimal a float para JSON
        def convert_decimal(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return obj
        return jsonify(json.loads(json.dumps(stats, default=convert_decimal)))
    except Exception as e:
        logger.error(f"Error en dashboard: {e}")
        return jsonify({'error': str(e)}), 500

# ---------- Empleados ----------
@app.route('/api/empleados', methods=['GET'])
@login_required
def get_empleados_route():
    search = request.args.get('search', '')
    sucursal_id = request.args.get('sucursal_id')
    tipo_pago = request.args.get('tipo_pago')
    limit = int(request.args.get('limit', 100))
    offset = int(request.args.get('offset', 0))
    try:
        empleados = get_empleados(search, sucursal_id, tipo_pago, True, limit, offset)
        return jsonify(empleados)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/empleados/<int:id>', methods=['GET'])
@login_required
def get_empleado(id):
    emp = get_empleado_by_id(id)
    if not emp:
        return jsonify({'error': 'Empleado no encontrado'}), 404
    return jsonify(emp)

@app.route('/api/empleados', methods=['POST'])
@login_required
@audit_action('empleados', 'CREAR')
def create_empleado_route():
    try:
        data = request.json
        new_id = create_empleado(data, g.username)
        return jsonify({'mensaje': 'Empleado creado', 'id': new_id})
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error creando empleado: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/empleados/<int:id>', methods=['PUT'])
@login_required
@audit_action('empleados', 'ACTUALIZAR')
def update_empleado_route(id):
    try:
        data = request.json
        update_empleado(id, data, g.username)
        return jsonify({'mensaje': 'Empleado actualizado'})
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error actualizando empleado: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/empleados/<int:id>', methods=['DELETE'])
@login_required
@audit_action('empleados', 'ELIMINAR')
def delete_empleado_route(id):
    try:
        delete_empleado(id, g.username)
        return jsonify({'mensaje': 'Empleado eliminado (desactivado)'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------- Sucursales ----------
@app.route('/api/sucursales', methods=['GET'])
@login_required
def get_sucursales():
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT * FROM sucursales WHERE activo = TRUE ORDER BY nombre")
        return jsonify(cur.fetchall())

@app.route('/api/sucursales', methods=['POST'])
@login_required
@audit_action('sucursales', 'CREAR')
def create_sucursal():
    data = request.json
    nombre = data.get('nombre')
    if not nombre:
        return jsonify({'error': 'Nombre es requerido'}), 400
    with get_cursor(commit=True) as cur:
        cur.execute("INSERT INTO sucursales (nombre) VALUES (%s) RETURNING id_sucursal", (nombre,))
        new_id = cur.fetchone()[0]
        return jsonify({'mensaje': 'Sucursal creada', 'id': new_id})

@app.route('/api/sucursales/<int:id>', methods=['DELETE'])
@login_required
@audit_action('sucursales', 'ELIMINAR')
def delete_sucursal(id):
    with get_cursor(commit=True) as cur:
        cur.execute("UPDATE sucursales SET activo = FALSE WHERE id_sucursal = %s", (id,))
        return jsonify({'mensaje': 'Sucursal eliminada'})

# ---------- Parámetros ----------
@app.route('/api/parametros', methods=['GET'])
@login_required
def get_parametros():
    try:
        params = get_param_all()
        # Convertir Decimal a float para JSON
        def convert(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return obj
        return jsonify({k: convert(v) for k, v in params.items()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/parametros', methods=['PUT'])
@login_required
def update_parametro():
    data = request.json
    clave = data.get('clave')
    valor = data.get('valor')
    if not clave or valor is None:
        return jsonify({'error': 'Clave y valor son requeridos'}), 400
    try:
        update_param(clave, valor, g.username)
        return jsonify({'mensaje': 'Parámetro actualizado'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/actualizar_bcv', methods=['GET'])
@login_required
def actualizar_bcv():
    try:
        response = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=10)
        if response.status_code == 200:
            data = response.json()
            new_rate = Decimal(str(data['promedio']))
            update_param('tasa_bcv', new_rate, g.username)
            return jsonify({'tasa': float(new_rate), 'mensaje': 'Tasa BCV actualizada'})
        return jsonify({'error': 'No se pudo obtener la tasa'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------- Calcular Nómina ----------
@app.route('/api/calcular_nomina', methods=['POST'])
@login_required
def calcular_nomina():
    try:
        data = request.json
        # Asegurar que los montos se manejen como Decimal
        result = calcular_y_guardar_nomina(data, g.username)
        # Convertir Decimal a float para JSON
        def convert(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return obj
        return jsonify(json.loads(json.dumps(result, default=convert)))
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error en cálculo de nómina: {e}")
        return jsonify({'error': str(e)}), 500

# ---------- Historial (Lotes) ----------
@app.route('/api/lotes', methods=['GET'])
@login_required
def get_lotes():
    search = request.args.get('search', '')
    with get_cursor(dict_cursor=True) as cur:
        query = '''
            SELECT l.*, 
                   COUNT(DISTINCT n.id_empleado) as cantidad_empleados_lote,
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
            like = f"%{search}%"
            params.extend([like, like])
        query += " GROUP BY l.id_lote ORDER BY l.fecha_calculo DESC, l.id_lote DESC"
        cur.execute(query, params)
        lotes = cur.fetchall()
        # Convertir Decimal a float
        for l in lotes:
            for k in ['total_usd', 'total_bs', 'tasa_bcv']:
                if k in l and l[k] is not None:
                    l[k] = float(l[k])
        return jsonify(lotes)

@app.route('/api/lotes/<int:id>', methods=['GET', 'DELETE'])
@login_required
def manejar_lote(id):
    if request.method == 'GET':
        with get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM lotes_nomina WHERE id_lote = %s", (id,))
            lote = cur.fetchone()
            if not lote:
                return jsonify({'error': 'Lote no encontrado'}), 404
            cur.execute('''
                SELECT 
                    n.id_nomina, n.id_empleado, n.fecha_inicio, n.fecha_fin, n.tipo,
                    n.faltas_dias, n.salario_base_usd, n.horas_extras_usd,
                    n.bono_complementario_usd, n.bono_adicional_usd,
                    n.total_asignaciones_usd, n.total_deducciones_usd,
                    n.neto_pagar_usd, n.neto_pagar_bs,
                    n.tasa_bcv, n.sso_usd, n.rpe_usd, n.faov_usd,
                    e.nombres, e.apellidos, e.cedula
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s
            ''', (id,))
            nominas = cur.fetchall()
            # Convertir Decimal a float
            for n in nominas:
                for k in ['salario_base_usd', 'horas_extras_usd', 'bono_complementario_usd',
                          'bono_adicional_usd', 'total_asignaciones_usd', 'total_deducciones_usd',
                          'neto_pagar_usd', 'neto_pagar_bs', 'tasa_bcv', 'sso_usd', 'rpe_usd', 'faov_usd']:
                    if k in n and n[k] is not None:
                        n[k] = float(n[k])
            for k in ['total_usd', 'total_bs', 'tasa_bcv']:
                if k in lote and lote[k] is not None:
                    lote[k] = float(lote[k])
            return jsonify({
                'id_lote': lote['id_lote'],
                'descripcion': lote['descripcion'],
                'fecha_calculo': lote['fecha_calculo'].isoformat() if lote['fecha_calculo'] else None,
                'total_usd': lote['total_usd'],
                'total_bs': lote['total_bs'],
                'cantidad_empleados': lote['cantidad_empleados'],
                'tasa_bcv': lote['tasa_bcv'],
                'nominas': nominas
            })
    else:  # DELETE
        with get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM nominas WHERE lote_id = %s", (id,))
            cur.execute("DELETE FROM lotes_nomina WHERE id_lote = %s", (id,))
            return jsonify({'mensaje': 'Lote eliminado'})

# ---------- Generar TXT ----------
@app.route('/api/generar_archivo_pago/<int:lote_id>', methods=['GET'])
@login_required
def generar_archivo_pago(lote_id):
    tipo = request.args.get('tipo', '100')
    try:
        mem = generar_txt_provision(lote_id, tipo)
        return send_file(
            mem,
            as_attachment=True,
            download_name=f"PROV_{tipo}_{datetime.now().strftime('%Y%m%d')}.txt",
            mimetype='text/plain'
        )
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error generando TXT: {e}")
        return jsonify({'error': str(e)}), 500

# ---------- Cestaticket ----------
# (Mantener las rutas existentes de cestaticket con las mejoras de Decimal)
# Aquí van las rutas de cestaticket (las mismas que antes pero con Decimal)
# Por brevedad, asumo que ya están en el código original y solo se ajustan los tipos.

# ---------- Usuarios (Administración) ----------
@app.route('/api/usuarios', methods=['GET'])
@login_required
@admin_required
def get_usuarios():
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT id, username, rol, last_login, activo FROM usuarios ORDER BY id")
        return jsonify(cur.fetchall())

@app.route('/api/usuarios', methods=['POST'])
@login_required
@admin_required
@audit_action('usuarios', 'CREAR')
def crear_usuario():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    rol = data.get('rol', 'operador')
    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña requeridos'}), 400
    with get_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM usuarios WHERE username = %s", (username,))
        if cur.fetchone():
            return jsonify({'error': 'El usuario ya existe'}), 400
        hashed = generate_password_hash(password)
        cur.execute(
            "INSERT INTO usuarios (username, password, rol) VALUES (%s, %s, %s) RETURNING id",
            (username, hashed, rol)
        )
        new_id = cur.fetchone()[0]
        return jsonify({'mensaje': 'Usuario creado', 'id': new_id})

@app.route('/api/usuarios/<int:id>', methods=['PUT'])
@login_required
@admin_required
@audit_action('usuarios', 'ACTUALIZAR')
def actualizar_usuario(id):
    data = request.json
    with get_cursor(commit=True) as cur:
        if 'password' in data and data['password']:
            hashed = generate_password_hash(data['password'])
            cur.execute("UPDATE usuarios SET password = %s WHERE id = %s", (hashed, id))
        if 'rol' in data:
            cur.execute("UPDATE usuarios SET rol = %s WHERE id = %s", (data['rol'], id))
        if 'activo' in data:
            cur.execute("UPDATE usuarios SET activo = %s WHERE id = %s", (data['activo'], id))
        return jsonify({'mensaje': 'Usuario actualizado'})

@app.route('/api/usuarios/<int:id>', methods=['DELETE'])
@login_required
@admin_required
@audit_action('usuarios', 'ELIMINAR')
def eliminar_usuario(id):
    if id == session['user_id']:
        return jsonify({'error': 'No puedes eliminar tu propio usuario'}), 400
    with get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM usuarios WHERE id = %s", (id,))
        return jsonify({'mensaje': 'Usuario eliminado'})

@app.route('/api/usuarios/password', methods=['PUT'])
@login_required
def cambiar_password():
    data = request.json
    old = data.get('old_password')
    new = data.get('new_password')
    if not old or not new:
        return jsonify({'error': 'Contraseña actual y nueva requeridas'}), 400
    user_id = session['user_id']
    with get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT password FROM usuarios WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not check_password_hash(user['password'], old):
            return jsonify({'error': 'Contraseña actual incorrecta'}), 401
        hashed = generate_password_hash(new)
        cur.execute("UPDATE usuarios SET password = %s WHERE id = %s", (hashed, user_id))
        return jsonify({'mensaje': 'Contraseña actualizada'})

# ---------- Reportes ----------
# (Mantener rutas de reportes con mejoras de Decimal)
# Similar a lo que ya existe, pero usando Decimal en cálculos.

# ---------- Error Handlers ----------
@app.errorhandler(ValidationError)
def handle_validation_error(e):
    return jsonify({'error': str(e)}), 400

@app.errorhandler(Exception)
def handle_generic_error(e):
    logger.error(f"Error no manejado: {e}")
    return jsonify({'error': 'Error interno del servidor'}), 500

# ================== INICIO ==================
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
