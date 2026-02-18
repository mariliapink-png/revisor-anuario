# 🌐 Auditoria Anuário UnB - Aplicação Web

Aplicação web completa pronta para hospedar. Usuários apenas colam a URL do anuário e o sistema faz toda a auditoria.

## 📦 O Que Está Nesta Pasta

```
web-deploy/
├── index.html              # Aplicação web completa (HTML + CSS + JS)
├── docker-compose.yml      # Orquestração de containers
├── nginx.conf              # Configuração reverse proxy
├── DEPLOYMENT.md           # Guia completo de deployment
├── start-local.sh          # Script para rodar localmente
└── README.md               # Este arquivo
```

## 🚀 Rodar Localmente (2 minutos)

### Opção 1: Com Docker (Recomendado)

```bash
# Navegar até a pasta
cd web-deploy

# Executar
docker-compose up

# Acessar
http://localhost
```

### Opção 2: Script Automático

```bash
bash start-local.sh
```

### Opção 3: Manual

Terminal 1 (Backend):
```bash
cd backend
bash run.sh
```

Terminal 2 (Frontend):
```bash
cd web-deploy
python -m http.server 80
# Ou qualquer servidor HTTP que sirva o index.html
```

---

## 🌐 Rodar em Produção

Escolha uma das opções em [DEPLOYMENT.md](./DEPLOYMENT.md):

1. **Railway** (Mais fácil)
2. **Render** (Grátis)
3. **Heroku** (Tradicional)
4. **Docker no VPS** (Mais controle)

Cada opção tem instruções passo-a-passo.

---

## 📸 Como Funciona

1. **Usuário acessa** a página web
2. **Cola a URL** do anuário (ex: `https://anuariounb2025.netlify.app/`)
3. **Clica "Iniciar"**
4. **Sistema:**
   - Baixa a página
   - Extrai TOC automaticamente
   - Roda 6 regras de checagem
   - Exibe resultados
   - Permite exportar relatório

---

## 🎨 Recursos

- ✅ Design responsivo (mobile + desktop)
- ✅ 12 cores semânticas (paleta oficial)
- ✅ Resultados em tempo real
- ✅ Exportar HTML/PDF
- ✅ Stats e estatísticas
- ✅ Interface intuitiva

---

## 📊 Estrutura Técnica

```
Frontend (HTML + CSS + JS puro)
        ↓
    Nginx (Proxy)
        ↓
  Backend API (FastAPI/Python)
        ↓
   SQLite Database
```

---

## 🔧 Configuração

### API Backend

Por padrão, espera encontrar backend em:
- Local: `http://localhost:8000`
- Produção: mesmo domínio/porta (nginx redireciona)

Se mudar, editar em `index.html`:

```javascript
const API_URL = new URL(window.location.origin);
API_URL.hostname = API_URL.hostname === 'localhost' ? 'localhost:8000' : API_URL.hostname;
```

---

## 📈 Performance

- **Frontend:** <100KB (HTML puro)
- **Backend:** API rápida (FastAPI)
- **Database:** SQLite local (sem latência)
- **Nginx:** Compressão gzip + cache

---

## 🐛 Troubleshooting

### "Cannot reach API"

1. Verificar se backend está rodando
2. Verificar se nginx está escutando porta 80
3. Verificar logs: `docker-compose logs`

### "Porta 80 já em uso"

```bash
# Linux/macOS
sudo lsof -i :80
sudo kill -9 <PID>

# Ou mudar porta em docker-compose.yml
ports:
  - "8080:80"
# Depois acessar http://localhost:8080
```

### "JavaScript não funciona"

Limpar cache do navegador (Ctrl+Shift+Delete).

---

## 📚 Documentação

- [DEPLOYMENT.md](./DEPLOYMENT.md) - Guia de deployment
- [../backend/README.md](../backend/README.md) - Backend
- [../backend/EXAMPLES.txt](../backend/EXAMPLES.txt) - Exemplos API

---

## 🎯 Próximos Passos

1. **Testar localmente:** `docker-compose up`
2. **Fazer deploy:** Seguir [DEPLOYMENT.md](./DEPLOYMENT.md)
3. **Configurar domínio:** Apontar DNS para servidor
4. **SSL:** Certbot (Let's Encrypt)

---

## 📞 Suporte

Consulte a documentação na pasta raiz (`README_PRIMEIRO.txt`, `COMO_USAR.md`).

---

**Versão:** 1.0.0  
**Status:** ✅ Pronto para Produção  
**Última Atualização:** 2025-02-18
