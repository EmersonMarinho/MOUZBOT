# 🗄️ Guia de Bancos de Dados Gratuitos para o Bot

## 🏆 Recomendações (Top 3)

### 1. **Supabase** ⭐ RECOMENDADO
- **Tipo:** PostgreSQL
- **Limite gratuito:** 500MB de banco, 2GB de banda
- **Por que escolher:** Interface muito fácil, dashboard completo, API automática
- **Link:** https://supabase.com
- **Arquivo:** Use `database_postgres.py`

**Como configurar:**
1. Crie uma conta em https://supabase.com
2. Crie um novo projeto
3. Vá em Settings > Database
4. Copie a "Connection string" (URI)
5. Adicione no `.env`: `DATABASE_URL=postgresql://user:password@host:port/database`

---

### 2. **MongoDB Atlas** ⭐ MUITO POPULAR
- **Tipo:** MongoDB (NoSQL)
- **Limite gratuito:** 512MB de armazenamento
- **Por que escolher:** Fácil de usar, muito popular, flexível
- **Link:** https://www.mongodb.com/cloud/atlas
- **Arquivo:** Use `database_mongodb.py`

**Como configurar:**
1. Crie uma conta em https://www.mongodb.com/cloud/atlas
2. Crie um cluster gratuito (M0)
3. Configure o acesso (IP 0.0.0.0/0 para permitir qualquer IP)
4. Crie um usuário de banco de dados
5. Copie a connection string
6. Adicione no `.env`: `MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/`

---

### 3. **Railway** ⭐ SIMPLES
- **Tipo:** PostgreSQL
- **Limite gratuito:** $5 de crédito grátis por mês
- **Por que escolher:** Muito simples, deploy fácil
- **Link:** https://railway.app
- **Arquivo:** Use `database_postgres.py`

**Como configurar:**
1. Crie uma conta em https://railway.app
2. Crie um novo projeto
3. Adicione um serviço PostgreSQL
4. Copie a connection string
5. Adicione no `.env`: `DATABASE_URL=postgresql://user:password@host:port/database`

---

## 📋 Outras Opções

### 4. **Neon** (PostgreSQL Serverless)
- **Link:** https://neon.tech
- **Limite:** 3GB de armazenamento gratuito
- **Arquivo:** Use `database_postgres.py`

### 5. **PlanetScale** (MySQL)
- **Link:** https://planetscale.com
- **Limite:** 5GB de armazenamento, 1 bilhão de reads/mês
- **Nota:** Requer adaptação do código para MySQL

### 6. **ElephantSQL** (PostgreSQL)
- **Link:** https://www.elephantsql.com
- **Limite:** 20MB de banco de dados
- **Arquivo:** Use `database_postgres.py`

---

## 🔧 Como Migrar

### Opção 1: PostgreSQL (Supabase, Railway, Neon)

1. **Instale a dependência:**
```bash
pip install psycopg2-binary
```

2. **Atualize o `config.py`:**
```python
DATABASE_URL = os.getenv('DATABASE_URL')
```

3. **Renomeie o arquivo:**
- Renomeie `database_postgres.py` para `database.py`
- OU altere o import no `main.py` para usar `database_postgres`

4. **Adicione no `.env`:**
```
DATABASE_URL=postgresql://user:password@host:port/database
```

---

### Opção 2: MongoDB Atlas

1. **Instale a dependência:**
```bash
pip install pymongo
```

2. **Atualize o `config.py`:**
```python
MONGODB_URI = os.getenv('MONGODB_URI')
DATABASE_NAME = 'bdo_gearscore'  # Nome do banco no MongoDB
```

3. **Renomeie o arquivo:**
- Renomeie `database_mongodb.py` para `database.py`
- OU altere o import no `main.py`

4. **Adicione no `.env`:**
```
MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/
```

---

## 📊 Comparação Rápida

| Banco | Tipo | Facilidade | Limite Grátis | Recomendação |
|-------|------|------------|---------------|--------------|
| **Supabase** | PostgreSQL | ⭐⭐⭐⭐⭐ | 500MB | ⭐⭐⭐⭐⭐ |
| **MongoDB Atlas** | MongoDB | ⭐⭐⭐⭐ | 512MB | ⭐⭐⭐⭐ |
| **Railway** | PostgreSQL | ⭐⭐⭐⭐⭐ | $5/mês | ⭐⭐⭐⭐ |
| **Neon** | PostgreSQL | ⭐⭐⭐⭐ | 3GB | ⭐⭐⭐⭐ |
| **PlanetScale** | MySQL | ⭐⭐⭐ | 5GB | ⭐⭐⭐ |

---

## 💡 Minha Recomendação

**Para começar:** Use **Supabase** - é o mais fácil e tem uma interface excelente.

**Se quiser NoSQL:** Use **MongoDB Atlas** - muito popular e flexível.

Ambos têm limites generosos para um bot de Discord e são totalmente gratuitos para começar!

