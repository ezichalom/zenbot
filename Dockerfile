# Imagem oficial do Playwright: já vem com Chromium e TODAS as libs de sistema.
# É a abordagem recomendada pela Railway (testada a cada release do Playwright).
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

WORKDIR /app

# Instala as dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Garante o browser instalado (a imagem já traz, mas reforça a versão certa)
RUN playwright install chromium

# Copia o código
COPY . .

# Roda o worker
CMD ["python", "main.py"]
