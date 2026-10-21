# 1. Usar una imagen base de Linux con Python 3.12 instalado
FROM python:3.12-slim

# 2. Evitar que Python guarde los prints en memoria (para ver los logs en vivo)
ENV PYTHONUNBUFFERED=1

# 3. Crear y establecer la carpeta de trabajo dentro del contenedor
WORKDIR /app

# 4. Copiar SOLO el requirements.txt primero (para aprovechar la caché de Docker)
COPY requirements.txt .

# 5. Instalar las librerías de Python
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copiar el script del agente
COPY agente_soporte.py .

# 7. Comando que se ejecuta al arrancar el contenedor
ENTRYPOINT ["python", "agente_soporte.py"]