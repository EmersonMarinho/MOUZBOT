# 🚀 Guia de Deploy - Bot Discord

Este guia mostra como fazer deploy do bot em diferentes plataformas de hospedagem.

## 📋 Pré-requisitos

1. Conta no GitHub (com o código do bot)
2. Token do Discord Bot
3. URL do banco de dados PostgreSQL (Neon Tech)

## 🚂 Railway (Recomendado)

### Passo 1: Criar Conta
1. Acesse https://railway.app
2. Faça login com GitHub
3. Clique em "New Project"

### Passo 2: Deploy
1. Selecione "Deploy from GitHub repo"
2. Escolha o repositório do bot
3. Railway detectará automaticamente o Python

### Passo 3: Configurar Variáveis de Ambiente
No Railway, vá em **Variables** e adicione:

```
DISCORD_TOKEN=seu_token_do_bot_aqui
DATABASE_URL=postgresql://usuario:senha@host/database
ALLOWED_DM_ROLES=1413227376095264980,1412255754328473830,1413237056204832881
```

### Passo 4: Deploy
1. Railway fará o deploy automaticamente
2. Verifique os logs em **Deployments**
3. O bot estará online!

---

## 🎨 Render

### Passo 1: Criar Conta
1. Acesse https://render.com
2. Faça login com GitHub

### Passo 2: Criar Web Service
1. Clique em "New +" → "Web Service"
2. Conecte seu repositório GitHub
3. Configure:
   - **Name:** bdo-bot (ou o nome que preferir)
   - **Environment:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`

### Passo 3: Variáveis de Ambiente
Em **Environment**, adicione as mesmas variáveis do Railway.

### Passo 4: Deploy
1. Clique em "Create Web Service"
2. Aguarde o build e deploy
3. O bot estará online!

**⚠️ Nota:** No plano gratuito, o serviço pode suspender após 15 minutos de inatividade.

---

## ☁️ Discloud (Especializado em Bots Discord)

### Passo 1: Criar Conta
1. Acesse https://discloud.com
2. Crie uma conta

### Passo 2: Upload
1. Crie um arquivo ZIP com todos os arquivos do bot
2. Faça upload no Discloud
3. Configure as variáveis de ambiente na interface

### Passo 3: Deploy
1. Clique em "Deploy"
2. O bot estará online!

---

## 🔧 Variáveis de Ambiente Necessárias

| Variável | Descrição | Obrigatória |
|----------|-----------|-------------|
| `DISCORD_TOKEN` | Token do bot Discord | ✅ Sim |
| `DATABASE_URL` | URL de conexão PostgreSQL | ✅ Sim |
| `ALLOWED_DM_ROLES` | IDs dos cargos (separados por vírgula) | ❌ Não |

---

## 📝 Arquivos de Configuração

O projeto já inclui:
- ✅ `Procfile` - Para Railway/Heroku
- ✅ `railway.json` - Configuração específica Railway
- ✅ `runtime.txt` - Versão do Python
- ✅ `requirements.txt` - Dependências Python

---

## 🐛 Troubleshooting

### Bot não inicia
- Verifique se todas as variáveis de ambiente estão configuradas
- Verifique os logs do deploy
- Confirme que o `DISCORD_TOKEN` está correto

### Erro de conexão com banco
- Verifique se a `DATABASE_URL` está correta
- Confirme que o banco PostgreSQL está acessível
- Verifique se o banco permite conexões externas

### Bot não responde
- Verifique se o bot está online no Discord
- Confirme que o bot tem as permissões necessárias no servidor
- Verifique os logs para erros

---

## 🔄 Atualizações

Para atualizar o bot:
1. Faça commit das mudanças no GitHub
2. O deploy automático fará o resto (Railway/Render)
3. Ou faça upload manual (Discloud)

---

## 💡 Dicas

- Use **Railway** para começar (mais fácil)
- Use **Discloud** se quiser interface em português
- Monitore os logs regularmente
- Configure alertas de erro se possível

---

**Boa sorte com o deploy! 🎉**

