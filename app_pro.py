# ─── CONTINÚA: RUTAS DE NÓMINA ───────────────────────────────────────────────

@app.route('/api/calcular_nomina', methods=['POST'])
@login_required
@audit_action('nomina', 'CALCULAR')
def calcular_nomina():
    try:
        data = request.json or {}
        result = NominaService.calcular_y_guardar(data, g.username)
        return jsonify(result)
    except ValidationError as e:
        return jsonify({'error': str(e), 'code': 'VALIDATION_ERROR'}), 400
    except Exception as e:
        logger.error(f"Error calculando nómina: {e}")
        return jsonify({'error': 'Error interno calculando nómina'}), 500

@app.route('/api/calcular_cestaticket', methods=['POST'])
@login_required
@audit_action('cestaticket', 'CALCULAR')
def calcular_cestaticket():
    try:
        data = request.json or {}
        result = CestaticketService.calcular(data)
        return jsonify(result)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error calculando cestaticket: {e}")
        return jsonify({'error': 'Error interno'}), 500

@app.route('/api/calcular_pasivos', methods=['POST'])
@login_required
def calcular_pasivos():
    try:
        data = request.json or {}
        salario = Decimal(str(data.get('salario_mensual', 0)))
        dias = int(data.get('dias', 30))
        usar_base_60 = data.get('usar_base_60', True)
        
        tasa_bcv = ParametroService.get('tasa_bcv', Config.DEFAULT_TASA_BCV)
        salario_diario = salario / Decimal('30')
        if usar_base_60:
            salario_diario = salario_diario * Decimal('0.60')
        total_usd = salario_diario * Decimal(str(dias))
        total_bs = total_usd * tasa_bcv
        
        return jsonify({
            'dias': dias,
            'tasa_bcv': float(tasa_bcv),
            'base_usada': 'Incidencia 60%' if usar_base_60 else '100% (Full)',
            'total_usd': float(total_usd),
            'total_bs': float(total_bs)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

# ─── HISTORIAL ───────────────────────────────────────────────────────────────

@app.route('/api/lotes', methods=['GET'])
@login_required
def get_lotes():
    search = request.args.get('search', '')
    with Database.get_cursor(dict_cursor=True) as cur:
        query = """
            SELECT l.*, COUNT(DISTINCT n.id_empleado) as cantidad_empleados_lote,
                   STRING_AGG(DISTINCT s.nombre, ', ') as sucursales_involucradas
            FROM lotes_nomina l
            LEFT JOIN nominas n ON l.id_lote = n.lote_id
            LEFT JOIN empleados e ON n.id_empleado = e.id_empleado
            LEFT JOIN sucursales s ON e.sucursal_id = s.id_sucursal
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (l.descripcion ILIKE %s OR CAST(l.id_lote AS TEXT) ILIKE %s)"
            sp = f"%{search}%"
            params.extend([sp, sp])
        query += " GROUP BY l.id_lote ORDER BY l.fecha_calculo DESC, l.id_lote DESC"
        cur.execute(query, params)
        return jsonify(cur.fetchall())

@app.route('/api/lotes/<int:id>', methods=['GET', 'DELETE'])
@login_required
def manejar_lote(id):
    if request.method == 'GET':
        with Database.get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM lotes_nomina WHERE id_lote = %s", (id,))
            lote = cur.fetchone()
            if not lote:
                return jsonify({'error': 'Lote no encontrado'}), 404
            
            cur.execute("""
                SELECT n.*, e.nombres, e.apellidos, e.cedula
                FROM nominas n
                JOIN empleados e ON n.id_empleado = e.id_empleado
                WHERE n.lote_id = %s
            """, (id,))
            nominas = cur.fetchall()
            
            return jsonify({
                'lote': lote,
                'nominas': nominas
            })
    else:
        with Database.get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM nominas WHERE lote_id = %s", (id,))
            cur.execute("DELETE FROM lotes_nomina WHERE id_lote = %s", (id,))
            return jsonify({'mensaje': 'Lote eliminado'})

@app.route('/api/lotes_cestaticket', methods=['GET'])
@login_required
def get_lotes_cestaticket():
    search = request.args.get('search', '')
    with Database.get_cursor(dict_cursor=True) as cur:
        query = """
            SELECT l.*, COUNT(c.id) as total_empleados_detalle
            FROM cestaticket_lotes l
            LEFT JOIN cestaticket_nominas c ON l.id_lote = c.lote_id
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (l.descripcion ILIKE %s OR CAST(l.id_lote AS TEXT) ILIKE %s)"
            sp = f"%{search}%"
            params.extend([sp, sp])
        query += " GROUP BY l.id_lote ORDER BY l.fecha_calculo DESC, l.id_lote DESC"
        cur.execute(query, params)
        return jsonify(cur.fetchall())

@app.route('/api/lotes_cestaticket/<int:id>', methods=['GET', 'DELETE'])
@login_required
def manejar_lote_cestaticket(id):
    if request.method == 'GET':
        with Database.get_cursor(dict_cursor=True) as cur:
            cur.execute("SELECT * FROM cestaticket_lotes WHERE id_lote = %s", (id,))
            lote = cur.fetchone()
            if not lote:
                return jsonify({'error': 'Lote no encontrado'}), 404
            
            cur.execute("""
                SELECT c.*, e.nombres, e.apellidos, e.cedula
                FROM cestaticket_nominas c
                JOIN empleados e ON c.id_empleado = e.id_empleado
                WHERE c.lote_id = %s
            """, (id,))
            return jsonify({'lote': lote, 'nominas': cur.fetchall()})
    else:
        with Database.get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM cestaticket_nominas WHERE lote_id = %s", (id,))
            cur.execute("DELETE FROM cestaticket_lotes WHERE id_lote = %s", (id,))
            return jsonify({'mensaje': 'Lote eliminado'})

# ─── GENERACIÓN DE ARCHIVOS ──────────────────────────────────────────────────

@app.route('/api/generar_archivo_pago/<int:lote_id>', methods=['GET'])
@login_required
def generar_archivo_pago(lote_id):
    try:
        tipo = request.args.get('tipo', '100')
        mem = TXTGenerator.generar_provision_nomina(lote_id, tipo)
        return send_file(
            mem,
            as_attachment=True,
            download_name=f"PROV_{tipo}_{datetime.now().strftime('%Y%m%d')}.txt",
            mimetype='text/plain'
        )
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.error(f"Error generando archivo: {e}")
        return jsonify({'error': 'Error generando archivo'}), 500

# ─── USUARIOS ────────────────────────────────────────────────────────────────

@app.route('/api/usuarios', methods=['GET'])
@login_required
def get_usuarios():
    with Database.get_cursor(dict_cursor=True) as cur:
        cur.execute("SELECT id, username, rol, activo, last_login FROM usuarios ORDER BY id")
        return jsonify(cur.fetchall())

@app.route('/api/usuarios', methods=['POST'])
@login_required
@admin_required
@audit_action('usuarios', 'CREAR')
def create_usuario():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    rol = data.get('rol', 'operador')
    
    if not username or not password:
        return jsonify({'error': 'Usuario y contraseña requeridos'}), 400
    
    with Database.get_cursor(commit=True) as cur:
        try:
            hashed = generate_password_hash(password)
            cur.execute("INSERT INTO usuarios (username, password, rol) VALUES (%s, %s, %s) RETURNING id",
                        (username, hashed, rol))
            return jsonify({'mensaje': 'Usuario creado', 'id': cur.fetchone()['id']}), 201
        except psycopg2.IntegrityError:
            return jsonify({'error': 'El usuario ya existe'}), 400

@app.route('/api/usuarios/<int:id>', methods=['PUT', 'DELETE'])
@login_required
@admin_required
def manage_usuario(id):
    if request.method == 'PUT':
        data = request.json or {}
        with Database.get_cursor(commit=True) as cur:
            if data.get('password'):
                hashed = generate_password_hash(data['password'])
                cur.execute("UPDATE usuarios SET username=%s, password=%s, rol=%s WHERE id=%s",
                            (data['username'], hashed, data.get('rol'), id))
            else:
                cur.execute("UPDATE usuarios SET username=%s, rol=%s WHERE id=%s",
                            (data['username'], data.get('rol'), id))
            return jsonify({'mensaje': 'Usuario actualizado'})
    else:
        user_id = session.get('user_id')
        if user_id == id:
            return jsonify({'error': 'No puedes eliminarte a ti mismo'}), 400
        with Database.get_cursor(commit=True) as cur:
            cur.execute("UPDATE usuarios SET activo = FALSE WHERE id = %s", (id,))
            return jsonify({'mensaje': 'Usuario eliminado'})

@app.route('/api/usuarios/password', methods=['PUT'])
@login_required
def cambiar_password():
    data = request.json or {}
    old = data.get('old_password', '')
    new = data.get('new_password', '')
    
    if not old or not new:
        return jsonify({'error': 'Contraseña actual y nueva requeridas'}), 400
    
    with Database.get_cursor() as cur:
        cur.execute("SELECT password FROM usuarios WHERE id = %s", (g.user_id,))
        user = cur.fetchone()
        if not user or not check_password_hash(user[0], old):
            return jsonify({'error': 'Contraseña actual incorrecta'}), 401
        
        with Database.get_cursor(commit=True) as cur2:
            cur2.execute("UPDATE usuarios SET password = %s WHERE id = %s",
                        (generate_password_hash(new), g.user_id))
            return jsonify({'mensaje': 'Contraseña actualizada'})

# ─── REPORTES ────────────────────────────────────────────────────────────────

@app.route('/api/reporte_pasivos', methods=['POST'])
@login_required
def reporte_pasivos():
    data = request.json or {}
    return jsonify(ReporteService.pasivos_laborales(data.get('empleado_id')))

@app.route('/api/reporte_parafiscales', methods=['POST'])
@login_required
def reporte_parafiscales():
    data = request.json or {}
    fecha_inicio = data.get('fecha_inicio')
    fecha_fin = data.get('fecha_fin')
    empleado_id = data.get('empleado_id')
    
    if not fecha_inicio or not fecha_fin:
        return jsonify({'error': 'Fechas requeridas'}), 400
    
    # Implementación simplificada - puedes expandirla
    return jsonify({'mensaje': 'Reporte generado', 'periodo': {'inicio': fecha_inicio, 'fin': fecha_fin}})

@app.route('/api/resumen_dolares', methods=['POST'])
@login_required
def resumen_dolares():
    data = request.json or {}
    return jsonify({
        'mes': data.get('mes'),
        'anio': data.get('anio'),
        'mensaje': 'Resumen generado'
    })

# ─── MANEJO DE ERRORES ───────────────────────────────────────────────────────

@app.errorhandler(ValidationError)
def handle_validation(e):
    return jsonify({'error': str(e), 'code': 'VALIDATION_ERROR'}), 400

@app.errorhandler(DatabaseError)
def handle_db_error(e):
    logger.error(f"DB Error: {e}")
    return jsonify({'error': 'Error de base de datos', 'code': 'DB_ERROR'}), 500

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Recurso no encontrado', 'code': 'NOT_FOUND'}), 404

@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal error: {e}")
    return jsonify({'error': 'Error interno del servidor', 'code': 'INTERNAL_ERROR'}), 500

# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    Database.init_schema()
    app.run(debug=True, host='0.0.0.0', port=5000)
