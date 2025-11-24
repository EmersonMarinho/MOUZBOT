# 🚀 Guia Rápido de Migração para Banco de Dados Cloud

## 📝 Passo a Passo - Supabase (Recomendado)

### 1. Criar Conta no Supabase
1. Acesse https://supabase.com
2. Clique em "Start your project"
3. Faça login com GitHub, Google ou email

### 2. Criar Projeto
1. Clique em "New Project"
2. Escolha uma organização
3. Preencha:
   - **Name:** BDO Gearscore Bot
   - **Database Password:** (anote essa senha!)
   - **Region:** Escolha a mais próxima (South America se disponível)
4. Clique em "Create new project"
5. Aguarde alguns minutos para o projeto ser criado

### 3. Obter Connection String
1. No projeto, vá em **Settings** (ícone de engrenagem)
2. Clique em **Database**
3. Role até encontrar **Connection string**
4. Selecione **URI** (não "Session mode")
5. Copie a string que aparece (algo como: `postgresql://postgres:[YOUR-PASSWORD]@db.xxxxx.supabase.co:5432/postgres`)

### 4. Configurar o Bot
1. Substitua `[YOUR-PASSWORD]` pela senha que você criou
2. Abra o arquivo `.env` no seu projeto
3. Adicione:
```
DATABASE_URL=postgresql://postgres:SUA_SENHA@db.xxxxx.supabase.co:5432/postgres
```

### 5. Instalar Dependências
```bash
pip install psycopg2-binary
```

### 6. Usar o Banco PostgreSQL
Renomeie os arquivos:
- Renomeie `database.py` para `database_sqlite.py` (backup)
- Renomeie `database_postgres.py` para `database.py`

OU altere o import no `main.py`:
```python
from database_postgres import Database
```

### 7. Testar
Execute o bot:
```bash
python main.py
```

O bot criará a tabela automaticamente no Supabase!

---

## 📝 Passo a Passo - MongoDB Atlas

### 1. Criar Conta
1. Acesse https://www.mongodb.com/cloud/atlas
2. Clique em "Try Free"
3. Crie uma conta

### 2. Criar Cluster
1. Escolha **M0 FREE** (gratuito)
2. Escolha a região mais próxima
3. Dê um nome ao cluster (ex: "BDO-Bot")
4. Clique em "Create"

### 3. Configurar Acesso
1. Vá em **Security** > **Network Access**
2. Clique em "Add IP Address"
3. Clique em "Allow Access from Anywhere" (0.0.0.0/0)
4. Confirme

### 4. Criar Usuário
1. Vá em **Security** > **Database Access**
2. Clique em "Add New Database User"
3. Escolha "Password" como método
4. Crie um usuário e senha (anote!)
5. Dê permissão "Atlas admin" ou "Read and write to any database"
6. Clique em "Add User"

### 5. Obter Connection String
1. Vá em **Deployment** > **Database**
2. Clique em "Connect" no seu cluster
3. Escolha "Connect your application"
4. Copie a connection string (algo como: `mongodb+srv://username:password@cluster.mongodb.net/`)
5. Substitua `<password>` pela senha do usuário que você criou
6. Substitua `<dbname>` por `bdo_gearscore` (ou deixe vazio)

### 6. Configurar o Bot
No arquivo `.env`:
```
MONGODB_URI=mongodb+srv://usuario:senha@cluster.mongodb.net/bdo_gearscore
MONGODB_DB_NAME=bdo_gearscore
```

### 7. Instalar Dependências
```bash
pip install pymongo
```

### 8. Usar o Banco MongoDB
Renomeie os arquivos:
- Renomeie `database.py` para `database_sqlite.py` (backup)
- Renomeie `database_mongodb.py` para `database.py`

OU altere o import no `main.py`:
```python
from database_mongodb import Database
```

### 9. Testar
Execute o bot:
```bash
python main.py
```

---

## ✅ Verificação

Após configurar, teste com o comando:
```
/atualizar_gearscore nome_familia:Teste nome_personagem:TesteChar classe_pvp:Warrior ap:300 aap:280 dp:400
```

Se funcionar, você verá a mensagem de sucesso e os dados estarão salvos no banco cloud! 🎉

---

## 🔄 Voltar para SQLite

Se quiser voltar a usar SQLite local:
1. Renomeie `database.py` para `database_cloud.py`
2. Crie um novo `database.py` copiando de `database_sqlite.py`
3. Remova as variáveis de banco do `.env`

---

## 💡 Dicas

- **Supabase:** Tem um dashboard visual onde você pode ver os dados em tempo real
- **MongoDB Atlas:** Permite visualizar documentos JSON diretamente
- Ambos têm limites generosos para projetos pequenos/médios
- Os dados ficam na nuvem, então você pode rodar o bot de qualquer lugar!

