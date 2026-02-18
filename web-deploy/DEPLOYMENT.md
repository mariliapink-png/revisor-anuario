# 🚀 GUIA DE DEPLOYMENT - Auditoria Anuário UnB

Sistema pronto para rodar em produção. Escolha sua plataforma preferida.

---

## 📋 Opções de Deployment

- [1. Docker Local](#1-docker-local)
- [2. Railway (Recomendado)](#2-railway-recomendado)
- [3. Render](#3-render)
- [4. Heroku](#4-heroku)
- [5. VPS/Servidor Próprio](#5-vpsservidor-próprio)

---

## 1. Docker Local

### Pré-requisitos
- Docker e Docker Compose instalados
- 4GB RAM mínimo

### Passos

```bash
# Clone ou copie os arquivos
mkdir auditoria && cd auditoria
cp -r backend/ .
cp docker-compose.yml nginx.conf web-deploy/ .

# Iniciar
docker-compose up -d

# Verificar logs
docker-compose logs -f api
docker-compose logs -f web

# Acessar
http://localhost
```

### Parar
```bash
docker-compose down
```

---

## 2. Railway (Recomendado)

Railway é a opção mais simples. Backend + Frontend hospedados.

### Passo 1: Preparar Repositório

```bash
# Estrutura esperada:
.
├── backend/
│   ├── app/
│   ├── requirements.txt
│   └── Dockerfile
├── web-deploy/
│   └── index.html
├── nginx.conf
└── docker-compose.yml
```

### Passo 2: Criar Railway Project

1. Ir em: https://railway.app/
2. Criar conta (com GitHub é mais fácil)
3. Criar novo projeto: "Deploy from GitHub"
4. Conectar repositório

### Passo 3: Configurar Backend Service

No dashboard Railway:

1. **Plugins** → Add Plugin → PostgreSQL (opcional, backend usa SQLite)
2. **Variables** → Adicionar:
   ```
   PYTHON_VERSION=3.11
   PYTHONUNBUFFERED=1
   ```
3. **Settings** → Root Directory: `backend`

### Passo 4: Configurar Frontend Service

1. **New Service** → Empty Service
2. **Editor** → Adicionar arquivo `Dockerfile`:
   ```dockerfile
   FROM node:18-alpine as build
   WORKDIR /app
   COPY web-deploy/ .
   RUN npm init -y && npm install
   EXPOSE 3000
   CMD ["npx", "http-server", "-p", "3000"]
   ```
3. **Settings** → Root Directory: `.`

### Passo 5: Deploy

Apenas fazer push para o branch main:
```bash
git push origin main
```

Railway fará deploy automaticamente.

**URL da Aplicação:** Será gerada automaticamente (ex: `https://app-xxx.railway.app`)

---

## 3. Render

### Passo 1: Criar Backend Service

1. Ir em: https://render.com/
2. "New +" → "Web Service"
3. Conectar repositório GitHub
4. Configurar:
   - **Name:** auditoria-backend
   - **Root Directory:** backend
   - **Runtime:** Python 3.11
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Environment:** Adicionar `PYTHONUNBUFFERED=true`

5. Deploy

### Passo 2: Criar Frontend Service (Estático)

1. "New +" → "Static Site"
2. Conectar repositório
3. Configurar:
   - **Name:** auditoria-frontend
   - **Root Directory:** web-deploy
   - **Build Command:** `echo "No build needed"`
   - **Publish Directory:** `.`

4. Deploy

### Passo 3: Configurar CORS

No `backend/app/main.py`, adicione no app:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## 4. Heroku

### Passo 1: Instalar Heroku CLI

```bash
# macOS
brew install heroku

# Linux
curl https://cli-assets.heroku.com/install.sh | sh

# Verificar
heroku --version
```

### Passo 2: Criar Apps

```bash
# Login
heroku login

# Criar app backend
heroku create auditoria-api
heroku create auditoria-web
```

### Passo 3: Deploy Backend

```bash
cd backend

# Adicionar remote
heroku git:remote -a auditoria-api

# Deploy
git push heroku main
```

### Passo 4: Deploy Frontend

```bash
cd ../web-deploy

# Criar arquivo `Procfile`:
echo "web: npm start" > Procfile

# Criar `package.json`:
cat > package.json << EOF
{
  "name": "auditoria-web",
  "version": "1.0.0",
  "scripts": {
    "start": "npx http-server -p $PORT"
  },
  "dependencies": {
    "http-server": "^14.1.1"
  }
}
EOF

git add .
git commit -m "Add deployment files"

# Deploy
heroku git:remote -a auditoria-web
git push heroku main
```

---

## 5. VPS/Servidor Próprio

### Pré-requisitos
- VPS com Ubuntu 20.04+ (DigitalOcean, Linode, AWS EC2)
- Domínio configurado (ex: auditoria.unb.br)
- SSH acesso

### Passo 1: Instalar Docker

```bash
# SSH no servidor
ssh root@seu_servidor

# Instalar Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Instalar Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### Passo 2: Clone Repositório

```bash
sudo mkdir -p /opt/auditoria
cd /opt/auditoria

# Clone (ou copie arquivos)
sudo git clone seu-repo .
```

### Passo 3: Configurar SSL (Let's Encrypt)

```bash
# Instalar Certbot
sudo apt-get install certbot python3-certbot-nginx

# Gerar certificado
sudo certbot certonly --standalone -d seu-dominio.com.br

# Os certificados estarão em:
# /etc/letsencrypt/live/seu-dominio.com.br/
```

### Passo 4: Atualizar nginx.conf

Descomente e configure a seção HTTPS no arquivo `nginx.conf`:

```nginx
ssl_certificate /etc/letsencrypt/live/seu-dominio.com.br/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/seu-dominio.com.br/privkey.pem;
server_name seu-dominio.com.br;
```

### Passo 5: Iniciar Serviço

```bash
# Iniciar
sudo docker-compose up -d

# Verificar logs
sudo docker-compose logs -f

# Acessar
https://seu-dominio.com.br
```

### Passo 6: Auto-renovação SSL

```bash
# Editar crontab
sudo crontab -e

# Adicionar:
0 2 * * * /usr/bin/certbot renew --quiet && docker-compose -f /opt/auditoria/docker-compose.yml restart web
```

---

## 🔧 Configurações Importantes

### CORS para Diferentes Domínios

Se backend e frontend em domínios diferentes, adicione em `backend/app/main.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://seu-dominio.com.br",
        "https://app.seu-dominio.com.br",
        "http://localhost:3000",  # dev
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### Variáveis de Ambiente

Criar arquivo `.env` (não commitar):

```bash
# Backend
PYTHONUNBUFFERED=1
DATABASE_URL=sqlite:///./anuario_audit.db

# Frontend
VITE_API_BASE=https://api.seu-dominio.com.br
```

### Aumentar Limite de Upload

No `nginx.conf`:

```nginx
client_max_body_size 500M;
```

---

## 📊 Monitoramento

### Health Checks

```bash
# Verificar backend
curl https://seu-dominio.com.br/api/health

# Verificar frontend
curl https://seu-dominio.com.br/health
```

### Logs

```bash
# Docker
docker-compose logs -f api
docker-compose logs -f web

# VPS (systemd)
sudo journalctl -u docker -f
```

### Uso de Disco

```bash
# Limpar dados antigos
sqlite3 backend/anuario_audit.db "DELETE FROM check_results WHERE checkrun_id NOT IN (SELECT MAX(id) FROM check_runs GROUP BY review_id)"
```

---

## 🔐 Segurança

### Firewall

```bash
# UFW (Ubuntu)
sudo ufw enable
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
```

### Backup Automático

```bash
# Script backup.sh
#!/bin/bash
BACKUP_DIR="/backups"
DB_FILE="/opt/auditoria/backend/anuario_audit.db"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR
cp $DB_FILE $BACKUP_DIR/anuario_$DATE.db
tar czf $BACKUP_DIR/auditoria_$DATE.tar.gz /opt/auditoria

# Manter últimos 30 dias
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete

echo "Backup concluído: $BACKUP_DIR/auditoria_$DATE.tar.gz"
```

Agendar:
```bash
sudo crontab -e
0 2 * * * /backup.sh
```

---

## 🆘 Troubleshooting

### Backend não responde

```bash
# Verificar logs
docker-compose logs api

# Reiniciar
docker-compose restart api

# Verificar porta
sudo netstat -tlnp | grep 8000
```

### Frontend não carrega

```bash
# Verificar nginx
sudo docker-compose logs web

# Verificar config
sudo nginx -t

# Recarregar nginx
docker-compose exec web nginx -s reload
```

### CORS Error

Adicione header no nginx:

```nginx
add_header 'Access-Control-Allow-Origin' '*';
```

### Certificado SSL expirou

```bash
sudo certbot renew --force-renewal
docker-compose restart web
```

---

## 📈 Performance

### Cache Frontend

No `nginx.conf`:

```nginx
location ~* \.(js|css|png|jpg|jpeg|gif|svg)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### Compressão

Já configurada em `nginx.conf` (gzip enabled).

### Database Optimization

```bash
# Executar periodicamente
sqlite3 backend/anuario_audit.db "VACUUM;"
```

---

## 📞 Suporte

- Documentação backend: `backend/README.md`
- Documentação frontend: `frontend/README.md`
- API docs: `https://seu-dominio.com.br/api/docs`

---

**Versão:** 1.0.0  
**Última Atualização:** 2025-02-18
