import json, os, time, schedule
import gspread
from google.genai import types
from google import genai
from dotenv import load_dotenv
load_dotenv()

ARCHIVO_CREDENCIALES = "client_secrets.json"
NOMBRE_HOJA = "Respuestas de Quejas (Base de Datos)"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "TU_API_KEY_AQUI")
INTERVALO_MINUTOS = 15
COL_CLASIFICACION = 4   # D
COL_SENTIMIENTO   = 5   # E
COL_EJECUTADO     = 6   # F
cliente_gemini = None

def conectar_sheets():
    """
    Autentica el script con Google usando OAuth 2.0 (gspread)
    y devuelve la primera hoja de la base de datos.
    """
    try:
        # 1. Autenticación con OAuth 2.0 usando el archivo client_secrets.json
        #    gspread se encarga de abrir el navegador la primera vez
        cliente = gspread.oauth(credentials_filename=ARCHIVO_CREDENCIALES)

        # 2. Abrir la hoja de cálculo por su nombre exacto
        libro = cliente.open(NOMBRE_HOJA)

        # 3. Obtener y retornar la primera hoja (sheet1)
        hoja = libro.sheet1
        print(f"✅ Conectado exitosamente a la hoja: {NOMBRE_HOJA}")
        return hoja

    except FileNotFoundError:
        print(f"❌ Error: No se encontró el archivo '{ARCHIVO_CREDENCIALES}'.")
        print("   Asegúrate de que esté en la misma carpeta que el script.")
        return None
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"❌ Error: No se encontró la hoja '{NOMBRE_HOJA}'.")
        print("   Verifica mayúsculas, tildes y espacios del nombre.")
        return None
    except Exception as e:
        print(f"❌ Error inesperado al conectar con Google Sheets: {e}")
        return None

def analizar_con_gemini(comentario):
    """
    Envía un comentario a Gemini y devuelve un diccionario con
    la clasificación y el sentimiento detectados.
    """
    # 1. Instrucción del sistema: rol + formato de salida
    instruccion_sistema = (
        "Eres un analista de soporte al cliente. "
        "Tu única tarea es devolver una respuesta en formato JSON, "
        "sin texto adicional ni explicaciones. "
        "El JSON debe tener exactamente las claves "
        '"clasificación" y "sentimiento".'
    )

    # 2. Instrucción del usuario: el comentario + la tarea concreta
    instruccion_usuario = (
        f"Analiza el siguiente comentario de un cliente:\n\n"
        f'"{comentario}"\n\n'
        "Clasifícalo en una de estas tres categorías: "
        "Ventas, Soporte Técnico o Logística. "
        "Detecta el sentimiento: Positivo, Negativo o Neutro. "
        "Devuelve únicamente el JSON."
    )

    # 3. Llamada a la API de Gemini
    respuesta = cliente_gemini.models.generate_content(
        model = "gemini-3.8-flash",
        contents = instruccion_usuario,
        config = types.GenerateContentConfig(
            system_instruction=instruccion_sistema
        ),
    )

    # 4. Limpiar la respuesta: quitar bloques de código ```json ... ```
    texto = respuesta.text.strip()
    if texto.startswith("```"):
        texto = texto.replace("```json", "").replace("```", "").strip()

    # 5. Convertir el texto JSON en diccionario Python
    resultado = json.loads(texto)

    # 6. Devolver el diccionario
    return resultado

def ejecutar_agente():
    """
    Ciclo completo del agente:
    1. Conectar con Google Sheets
    2. Leer todas las filas
    3. Filtrar las que ya fueron procesadas (columna F = "SI")
    4. Analizar cada comentario nuevo con Gemini
    5. Escribir los resultados en las columnas D, E y F
    """
    print("\n" + "=" * 55)
    print("🤖 Ejecutando agente de soporte...")
    print("=" * 55)

    # ── 1. CONECTAR ─────────────────────────────────────────
    hoja = conectar_sheets()
    if hoja is None:
        print("⚠️ No se pudo conectar con Google Sheets. Se omite este ciclo.")
        return

    # ── 2. LEER ─────────────────────────────────────────────
    try:
        todas_las_filas = hoja.get_all_values()
    except Exception as e:
        print(f"❌ Error al leer la hoja: {e}")
        return

    # Omitir la primera fila (encabezados)
    filas_datos = todas_las_filas[1:]

    if not filas_datos:
        print("📭 No hay respuestas registradas todavía.")
        return

    print(f"📋 Filas encontradas: {len(filas_datos)}")

    # ── 3. FILTRAR + ANALIZAR + ESCRIBIR ────────────────────
    nuevas_procesadas = 0

    for indice, fila in enumerate(filas_datos, start=2):  # start=2 porque la fila 1 es encabezado
        # Protección por si la fila viene incompleta
        while len(fila) < 6:
            fila.append("")

        nombre_cliente = fila[1]     # columna B
        comentario     = fila[2]     # columna C
        ya_procesada   = fila[5]     # columna F

        print(f"   🔍 DEBUG fila {indice}: col F = '{ya_procesada}' → {'SALTAR' if ya_procesada.strip().upper() == 'SI' else 'PROCESAR'}")
        
        # ── 3.1 FILTRAR: si ya tiene "SI" en la columna F, se omite
        if ya_procesada.strip().upper() == "SI":
            continue

        # Si el comentario está vacío, también se omite
        if not comentario.strip():
            print(f"⚠️ Fila {indice}: comentario vacío, se omite.")
            continue

        print(f"\n🔎 Fila {indice} | Cliente: {nombre_cliente or '(sin nombre)'}")
        print(f"   Comentario: {comentario[:70]}...")

        # ── 3.2 ANALIZAR: enviar el comentario a Gemini
                # ── 3.2 ANALIZAR: enviar el comentario a Gemini (con reintentos)
        try:
            resultado = analizar_con_gemini(comentario)

        except json.JSONDecodeError as e:
            print(f"   ❌ Gemini no devolvió un JSON válido. Detalle: {e}")
            continue

        except Exception as e:
            error_str = str(e)

            # Reintento para 503 (servidor saturado)
            if "503" in error_str:
                print("   ⏳ Servidor saturado. Reintentando en 10 s...")
                time.sleep(10)
                try:
                    resultado = analizar_con_gemini(comentario)
                except Exception as e2:
                    print(f"   ❌ Falló el reintento: {e2}")
                    continue

            # Reintento para 429 (cuota excedida)
            elif "429" in error_str:
                print("   ⏳ Cuota excedida. Esperando 20 s...")
                time.sleep(20)
                try:
                    resultado = analizar_con_gemini(comentario)
                except Exception as e2:
                    print(f"   ❌ Falló el reintento: {e2}")
                    continue

            else:
                print(f"   ❌ Error al analizar con Gemini: {e}")
                continue

        # Extraer los valores (solo si el análisis funcionó)
        clasificacion = resultado.get("clasificación", "Desconocida")
        sentimiento   = resultado.get("sentimiento", "Desconocido")
        print(f"   ✅ Clasificación: {clasificacion}")
        print(f"   ✅ Sentimiento:   {sentimiento}")

        # ── 3.3 ESCRIBIR: guardar resultados en D, E y F
        try:
            hoja.update_cell(indice, 4, clasificacion)   # D
            hoja.update_cell(indice, 5, sentimiento)     # E
            hoja.update_cell(indice, 6, "SI")            # F
            print(f"   💾 Resultados escritos en la fila {indice}.")
            nuevas_procesadas += 1
        except Exception as e:
            print(f"   ❌ Error al escribir en la hoja: {e}")
            continue

        # Pausa para respetar la cuota de 5 peticiones/minuto
        time.sleep(13)

        # ── 3.3 ESCRIBIR: guardar resultados en D, E y F
        try:
            hoja.update_cell(indice, 4, clasificacion)   # D → Clasificación IA
            hoja.update_cell(indice, 5, sentimiento)     # E → Sentimiento IA
            hoja.update_cell(indice, 6, "SI")            # F → Agente Ejecutado
            print(f"   💾 Resultados escritos en la fila {indice}.")
            nuevas_procesadas += 1
            time.sleep(13)   # 13 s entre llamadas → máx ~4-5 por minuto

        except Exception as e:
            print(f"   ❌ Error al escribir en la hoja: {e}")
            continue
        except Exception as e:
            if "503" in str(e):
                print("   ⏳ Servidor saturado, reintentando en 10 s...")
                time.sleep(10)
                try:
                    resultado = analizar_con_gemini(comentario)
                except Exception:
                    print("   ❌ Falló de nuevo. Se omite esta fila.")
                    continue
            else:
                print(f"   ❌ Error: {e}")
                continue

    # ── 4. RESUMEN DEL CICLO ────────────────────────────────
    print("\n" + "-" * 55)
    if nuevas_procesadas == 0:
        print("📭 No había filas nuevas por procesar.")
    else:
        print(f"✅ Ciclo terminado. Filas procesadas: {nuevas_procesadas}")
    print("-" * 55)
    
# ═══════════════════════════════════════════════════════════
# PUNTO DE ENTRADA DEL SCRIPT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":

    print("🚀 Iniciando Agente de Soporte...")

    # ── 1. VALIDAR LA API KEY ────────────────────────────────
    if GEMINI_API_KEY == "TU_API_KEY_AQUI" or not GEMINI_API_KEY.strip():
        print("❌ Error: Debes reemplazar GEMINI_API_KEY con tu clave real.")
        print("   Obtén una en: https://aistudio.google.com/app/api-keys")
        exit(1)

    # ── 2. VALIDAR EL ARCHIVO DE CREDENCIALES ───────────────
    if not os.path.exists(ARCHIVO_CREDENCIALES):
        print(f"❌ Error: No se encontró '{ARCHIVO_CREDENCIALES}'.")
        print("   Descárgalo en la Fase 2 (Paso 2.5) y colócalo junto al script.")
        exit(1)

    # ── 3. INICIALIZAR EL CLIENTE DE GEMINI ─────────────────
    try:
        cliente_gemini = genai.Client(api_key=GEMINI_API_KEY)
        print("✅ Cliente de Gemini inicializado.")
    except Exception as e:
        print(f"❌ Error al inicializar Gemini: {e}")
        exit(1)

    # ── 4. EJECUTAR INMEDIATAMENTE (sin esperar el intervalo) ─
    print("▶️  Ejecutando primera revisión ahora...")
    ejecutar_agente()

    # ── 5. PROGRAMAR EJECUCIONES AUTOMÁTICAS ────────────────
    schedule.every(INTERVALO_MINUTOS).minutes.do(ejecutar_agente)
    print(f"\n⏰ Agente programado para ejecutarse cada {INTERVALO_MINUTOS} minutos.")
    print("   Presiona Ctrl+C para detenerlo.\n")

    # ── 6. MANTENER EL SCRIPT CORRIENDO ─────────────────────
    while True:
        try:
            schedule.run_pending()
            time.sleep(30)   # revisa cada 30 s si hay tareas pendientes
        except KeyboardInterrupt:
            print("\n🛑 Agente detenido por el usuario. ¡Hasta luego!")
            break
        except Exception as e:
            print(f"⚠️ Error inesperado en el bucle principal: {e}")
            time.sleep(30)